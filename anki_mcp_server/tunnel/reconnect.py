"""Reconnection wrapper for the tunnel client.

Adds retry/backoff logic around TunnelClient. Each reconnection attempt
creates a fresh TunnelClient instance. This is the main entry point for
running a tunnel — callers interact with this, not TunnelClient directly.

Responsibilities:
- Exponential backoff with jitter between reconnection attempts
- Token refresh on auth-related close codes
- Clean shutdown via cancellation

Does NOT handle WebSocket internals — that belongs in client.py.
"""

from __future__ import annotations

import logging
import random
from typing import Callable

import anyio

from ..credentials import CredentialsManager
from .auth import AuthError, DeviceFlowAuth
from .client import TunnelClient, TunnelConnectionError
from .protocol import (
    RECONNECT_INITIAL_DELAY,
    RECONNECT_JITTER_FACTOR,
    RECONNECT_MAX_ATTEMPTS,
    RECONNECT_MAX_DELAY,
    should_reconnect,
    should_refresh_token,
)

logger = logging.getLogger(__name__)


class TunnelReconnectManager:
    """Manages tunnel connection with automatic reconnection.

    Wraps TunnelClient to add retry logic. Each reconnection attempt
    creates a fresh TunnelClient instance. The manager is the main
    entry point for running a tunnel — callers interact with this,
    not TunnelClient directly.
    """

    def __init__(
        self,
        server_url: str,
        local_mcp_url: str,
        credentials_manager: CredentialsManager,
        auth: DeviceFlowAuth,
        on_tunnel_established: Callable[[str, str | None], None] | None = None,
        on_disconnected: Callable[[int, str], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
        on_url_changed: Callable[[str, str], None] | None = None,
        on_request_completed: Callable[[str, int, float], None] | None = None,
        on_reconnecting: Callable[[int, float], None] | None = None,
        on_gave_up: Callable[[int, str], None] | None = None,
    ) -> None:
        """Initialize the reconnection manager.

        Args:
            server_url: WebSocket URL of the tunnel relay server
                (e.g. ``wss://tunnel.ankimcp.ai``).
            local_mcp_url: Base URL of the local MCP HTTP server
                (e.g. ``http://127.0.0.1:3141``).
            credentials_manager: Reads/writes credentials from disk.
            auth: Device flow auth client for token refresh.
            on_tunnel_established: Called when the tunnel is ready.
                Receives ``(public_url, expires_at)``.
            on_disconnected: Called when a single connection ends.
                Receives ``(close_code, reason)``.
            on_error: Called when the server sends an error message.
                Receives ``(error_code, error_message)``.
            on_url_changed: Called when the tunnel URL changes.
                Receives ``(old_url, new_url)``.
            on_request_completed: Called after each proxied request.
                Receives ``(method_path, status_code, duration_ms)``.
            on_reconnecting: Called before each reconnection delay.
                Receives ``(attempt_number, delay_seconds)``.
            on_gave_up: Called when reconnection is permanently abandoned.
                Receives ``(close_code, reason)``.
        """
        self._server_url = server_url
        self._local_mcp_url = local_mcp_url
        self._credentials_manager = credentials_manager
        self._auth = auth

        # Callbacks forwarded to TunnelClient
        self._on_tunnel_established = on_tunnel_established
        self._on_disconnected = on_disconnected
        self._on_error = on_error
        self._on_url_changed = on_url_changed
        self._on_request_completed = on_request_completed

        # Reconnection-specific callbacks
        self._on_reconnecting = on_reconnecting
        self._on_gave_up = on_gave_up

        # State
        self._shutdown = False
        self._active_client: TunnelClient | None = None
        self._sleep_scope: anyio.CancelScope | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Run the tunnel with automatic reconnection.

        This is a long-running coroutine. It keeps reconnecting until:
        - A permanent close code is received (TOKEN_REVOKED, ACCOUNT_DELETED,
          SESSION_REPLACED, NORMAL)
        - Max reconnection attempts exhausted
        - Cancelled via disconnect()

        Uses anyio.sleep for cancellation safety.
        """
        self._shutdown = False
        attempt = 0

        while not self._shutdown:
            # --- Step 1: Load credentials ---
            credentials = self._credentials_manager.load()
            if credentials is None:
                logger.warning("No credentials found, cannot connect tunnel")
                self._fire_callback(
                    self._on_gave_up, 0, "No credentials available"
                )
                return

            # --- Step 2: Refresh token if expired ---
            if self._credentials_manager.is_token_expired(credentials):
                logger.info("Access token expired, attempting refresh")
                try:
                    credentials = await self._auth.refresh_token(
                        credentials.refresh_token
                    )
                    self._credentials_manager.save(credentials)
                    logger.info("Token refreshed successfully")
                except AuthError as exc:
                    if exc.error_code == "invalid_grant":
                        logger.warning(
                            "Refresh token revoked, re-login required: %s", exc
                        )
                        self._fire_callback(
                            self._on_gave_up,
                            0,
                            "Refresh token revoked — re-login required",
                        )
                        return
                    logger.warning("Token refresh failed: %s", exc)
                    # Non-fatal refresh failure — try connecting anyway,
                    # the server will tell us if the token is truly dead.

            if self._shutdown:
                return

            # --- Step 3: Create a fresh client and run ---
            # Wrap the on_tunnel_established callback to reset the attempt
            # counter on successful connection. This way transient failures
            # during an otherwise healthy session don't accumulate.
            def _on_established_wrapper(url: str, expires_at: str | None) -> None:
                nonlocal attempt
                attempt = 0
                self._fire_callback(
                    self._on_tunnel_established, url, expires_at
                )

            client = TunnelClient(
                server_url=self._server_url,
                credentials=credentials,
                local_mcp_url=self._local_mcp_url,
                on_tunnel_established=_on_established_wrapper,
                on_disconnected=self._on_disconnected,
                on_error=self._on_error,
                on_url_changed=self._on_url_changed,
                on_request_completed=self._on_request_completed,
            )
            self._active_client = client

            try:
                close_code, close_reason = await client.run()
            except TunnelConnectionError as exc:
                logger.warning("Tunnel connection failed: %s", exc)
                self._active_client = None
                attempt += 1
            else:
                self._active_client = None

                # --- Decide what to do based on close code ---
                if not should_reconnect(close_code):
                    logger.info(
                        "Permanent close code %d (%s), not reconnecting",
                        close_code,
                        close_reason,
                    )
                    self._fire_callback(
                        self._on_gave_up, close_code, close_reason
                    )
                    return

                if should_refresh_token(close_code):
                    logger.info(
                        "Auth-related close code %d, refreshing token",
                        close_code,
                    )
                    try:
                        refreshed = await self._auth.refresh_token(
                            credentials.refresh_token
                        )
                        self._credentials_manager.save(refreshed)
                        logger.info(
                            "Token refreshed after close code %d", close_code
                        )
                        # Reset attempts — we have a fresh token, give it
                        # a full set of retries.
                        attempt = 0
                        continue
                    except AuthError as exc:
                        if exc.error_code == "invalid_grant":
                            logger.warning(
                                "Refresh token revoked after close code %d: %s",
                                close_code,
                                exc,
                            )
                            self._fire_callback(
                                self._on_gave_up,
                                close_code,
                                "Refresh token revoked — re-login required",
                            )
                            return
                        logger.warning(
                            "Token refresh failed after close code %d: %s",
                            close_code,
                            exc,
                        )
                        attempt += 1
                else:
                    attempt += 1

            if self._shutdown:
                return

            # --- Check attempt limit ---
            if attempt >= RECONNECT_MAX_ATTEMPTS:
                logger.warning(
                    "Max reconnection attempts (%d) reached, giving up",
                    RECONNECT_MAX_ATTEMPTS,
                )
                self._fire_callback(
                    self._on_gave_up,
                    0,
                    f"Max reconnection attempts ({RECONNECT_MAX_ATTEMPTS}) exhausted",
                )
                return

            # --- Backoff delay ---
            delay = self._calculate_delay(attempt)
            logger.info(
                "Reconnecting in %.1fs (attempt %d/%d)",
                delay,
                attempt,
                RECONNECT_MAX_ATTEMPTS,
            )
            self._fire_callback(self._on_reconnecting, attempt, delay)

            with anyio.CancelScope() as scope:
                self._sleep_scope = scope
                await anyio.sleep(delay)
            self._sleep_scope = None

    async def disconnect(self) -> None:
        """Stop the reconnection loop and disconnect the active tunnel.

        Sets the shutdown flag so the reconnection loop exits after the
        current sleep or client.run() returns. Also cancels any in-progress
        backoff sleep and disconnects the active TunnelClient if one is
        running.
        """
        self._shutdown = True
        if self._sleep_scope is not None:
            self._sleep_scope.cancel()
        client = self._active_client
        if client is not None:
            await client.disconnect()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _calculate_delay(self, attempt: int) -> float:
        """Calculate reconnection delay with exponential backoff and jitter.

        Formula: min(initial * 2^attempt + jitter, max_delay)
        where jitter = random(0, jitter_factor * base_delay).

        Args:
            attempt: Zero-based attempt number (0 = first retry).

        Returns:
            Delay in seconds.
        """
        base_delay = RECONNECT_INITIAL_DELAY * (2 ** attempt)
        jitter = random.uniform(0, RECONNECT_JITTER_FACTOR * base_delay)
        return min(base_delay + jitter, RECONNECT_MAX_DELAY)

    def _fire_callback(self, callback: Callable | None, *args: object) -> None:
        """Invoke a callback, catching and logging any exceptions.

        Callbacks are fire-and-forget — errors must never crash the
        reconnection manager.  We catch ``BaseException`` (not just
        ``Exception``) because callbacks may emit Qt signals from the
        asyncio thread, and any failure must be swallowed.
        """
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as exc:
            logger.error(
                "Callback %s raised: %s",
                getattr(callback, "__name__", repr(callback)),
                exc,
                exc_info=True,
            )
