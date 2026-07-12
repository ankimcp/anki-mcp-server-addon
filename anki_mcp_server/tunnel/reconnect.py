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
from typing import Any, Callable

import anyio

from ..credentials import CredentialsManager
from .auth import AuthError, DeviceFlowAuth
from .client import TunnelClient, TunnelConnectionError
from .hosted_credentials import HostedCredentialsLoader
from .in_memory_transport import InMemoryTransport
from .protocol import (
    RECONNECT_INITIAL_DELAY,
    RECONNECT_JITTER_FACTOR,
    RECONNECT_MAX_ATTEMPTS,
    RECONNECT_MAX_DELAY,
    CloseCodes,
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
        mcp_server: Any,
        credentials_manager: CredentialsManager,
        auth: DeviceFlowAuth,
        on_tunnel_established: Callable[[str, dict | None], None] | None = None,
        on_disconnected: Callable[[int, str], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
        on_request_completed: Callable[[str, int, float], None] | None = None,
        on_reconnecting: Callable[[int, float], None] | None = None,
        on_stopped: Callable[[int, str], None] | None = None,
        hosted_mode: bool = False,
        hosted_credentials_path: str = "",
    ) -> None:
        """Initialize the reconnection manager.

        Args:
            server_url: WebSocket URL of the tunnel relay server
                (e.g. ``wss://tunnel.ankimcp.ai``).
            mcp_server: The lowlevel ``mcp.server.lowlevel.server.Server``
                instance from ``FastMCP._mcp_server``. Used to create a
                fresh ``InMemoryTransport`` per connection attempt.
            credentials_manager: Reads/writes credentials from disk. Only used
                in regular (non-hosted) mode.
            auth: Device flow auth client for token refresh. Only used in
                regular (non-hosted) mode.
            on_tunnel_established: Called when the tunnel is ready.
                Receives ``(public_url, user_dict)``.
            on_disconnected: Called when a single connection ends.
                Receives ``(close_code, reason)``.
            on_error: Called when the server sends an error message.
                Receives ``(error_code, error_message)``.
            on_request_completed: Called after each proxied request.
                Receives ``(method_path, status_code, duration_ms)``.
            on_reconnecting: Called before each reconnection delay.
                Receives ``(attempt_number, delay_seconds)``.
            on_stopped: Called once when the tunnel stops for good (no
                further reconnection) — whether a clean disconnect or a
                permanent failure. Receives ``(close_code, reason)``.
                Inspect ``close_code`` (``NORMAL`` == clean) to distinguish.
            hosted_mode: When ``True``, the tunnel runs unattended in a hosted
                environment: credentials come from a provisioned file (via
                ``hosted_credentials_path``) instead of OAuth, no token refresh
                or save occurs, and every disconnect is terminal (no backoff /
                reconnect). Defaults to ``False`` (regular OAuth behavior).
            hosted_credentials_path: Filesystem path to the hosted credentials
                file. Only consulted when ``hosted_mode`` is ``True``.
        """
        self._server_url = server_url
        self._mcp_server = mcp_server
        self._credentials_manager = credentials_manager
        self._auth = auth

        # Hosted mode: read-only credentials from a provisioned file. The loader
        # does no caching, so the file is re-read on every connection attempt.
        self._hosted_mode = hosted_mode
        self._hosted_credentials_path = hosted_credentials_path
        self._hosted_loader = (
            HostedCredentialsLoader(hosted_credentials_path)
            if hosted_mode and hosted_credentials_path
            else None
        )

        # Callbacks forwarded to TunnelClient
        self._on_tunnel_established = on_tunnel_established
        self._on_disconnected = on_disconnected
        self._on_error = on_error
        self._on_request_completed = on_request_completed

        # Reconnection-specific callbacks
        self._on_reconnecting = on_reconnecting
        self._on_stopped = on_stopped

        # State
        self._shutdown = False
        self._active_client: TunnelClient | None = None
        self._sleep_scope: anyio.CancelScope | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Run the tunnel with automatic reconnection until it stops for good."""
        self._shutdown = False
        try:
            close_code, reason = await self._run_loop()
        except Exception as exc:
            # Any unexpected error is still a terminal stop: fire on_stopped so
            # the UI/log reflect it. CancelledError (BaseException) must NOT be
            # caught here — the timeout-cancel path in _stop_tunnel_async relies
            # on it propagating up to _run_tunnel.
            logger.error(
                "Tunnel loop failed unexpectedly: %s", exc, exc_info=True
            )
            close_code, reason = (0, f"Tunnel error: {exc}")
        self._fire_callback(self._on_stopped, close_code, reason)

    async def _run_loop(self) -> tuple[int, str]:
        """The reconnect loop. Returns the terminal ``(close_code, reason)``
        describing why the tunnel stopped. Does NOT fire on_stopped — run() does.

        It keeps reconnecting until:
        - A permanent close code is received (TOKEN_REVOKED, ACCOUNT_DELETED,
          SESSION_REPLACED, NORMAL)
        - Max reconnection attempts exhausted
        - Cancelled via disconnect()

        Uses anyio.sleep for cancellation safety.
        """
        attempt = 0

        while not self._shutdown:
            # --- Steps 1 & 2: Acquire the target URL, bearer token, and the
            # user dict to report on connection. Hosted mode reads a
            # provisioned file (no expiry check, no refresh, no save); regular
            # mode loads and, if needed, refreshes OAuth credentials. ---
            if self._hosted_mode:
                acquired = self._load_hosted_connection()
                if acquired is None:
                    # Missing/malformed hosted credentials — stop cleanly, the
                    # same terminal outcome as the regular "no credentials" case.
                    return (0, "No hosted credentials available")
                server_url, bearer_token, established_user = acquired
            else:
                # --- Step 1: Load credentials ---
                credentials = self._credentials_manager.load()
                if credentials is None:
                    logger.warning("No credentials found, cannot connect tunnel")
                    return (0, "No credentials available")

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
                            return (0, "Refresh token revoked — re-login required")
                        logger.warning("Token refresh failed: %s", exc)
                        self._fire_callback(
                            self._on_error,
                            "token_refresh_failed",
                            f"Token refresh failed (will retry): {exc}",
                        )
                        # Non-fatal refresh failure — try connecting anyway,
                        # the server will tell us if the token is truly dead.

                server_url = self._server_url
                bearer_token = credentials.access_token
                established_user = credentials.user

            if self._shutdown:
                return (CloseCodes.NORMAL, "Disconnected by user")

            # --- Step 3: Create a fresh transport and client, then run ---
            # Wrap the on_tunnel_established callback to reset the attempt
            # counter on successful connection. This way transient failures
            # during an otherwise healthy session don't accumulate.
            def _on_established_wrapper(url: str) -> None:
                nonlocal attempt
                attempt = 0
                self._fire_callback(
                    self._on_tunnel_established, url, established_user,
                )

            # Fresh in-memory transport per connection — gives each
            # reconnect a clean Server.run() session with no stale state.
            transport = InMemoryTransport(self._mcp_server)
            try:
                await transport.start()
            except Exception as exc:
                logger.error("Failed to start in-memory transport: %s", exc)
                self._fire_callback(
                    self._on_error,
                    "transport_start_failed",
                    f"Internal transport failed to start: {exc}",
                )
                await transport.stop()
                if self._hosted_mode:
                    # Hosted mode never retries — a transport failure is
                    # terminal. The platform recovers by restarting the pod.
                    return (0, f"Internal transport failed to start: {exc}")
                attempt += 1
                # Fall through to attempt-limit check and backoff below
                if self._shutdown:
                    return (CloseCodes.NORMAL, "Disconnected by user")
                if attempt >= RECONNECT_MAX_ATTEMPTS:
                    logger.warning(
                        "Max reconnection attempts (%d) reached, giving up",
                        RECONNECT_MAX_ATTEMPTS,
                    )
                    return (
                        0,
                        f"Max reconnection attempts ({RECONNECT_MAX_ATTEMPTS}) exhausted",
                    )
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
                continue

            client = TunnelClient(
                server_url=server_url,
                bearer_token=bearer_token,
                transport=transport,
                on_tunnel_established=_on_established_wrapper,
                on_disconnected=self._on_disconnected,
                on_error=self._on_error,
                on_request_completed=self._on_request_completed,
            )
            self._active_client = client

            try:
                close_code, close_reason = await client.run()
            except TunnelConnectionError as exc:
                logger.warning("Tunnel connection failed: %s", exc)
                self._fire_callback(
                    self._on_error, "connection_failed", str(exc)
                )
                self._active_client = None
                if self._hosted_mode:
                    # Hosted mode never retries — a failed connect is terminal.
                    return (0, str(exc))
                attempt += 1
            else:
                self._active_client = None

                if self._hosted_mode:
                    # Every disconnect is terminal in hosted mode — no refresh,
                    # no backoff, no reconnect. The platform reconnects hosted
                    # pods on demand by restarting them, so a background retry
                    # here could fight a local client that reconnected meanwhile.
                    return (close_code, close_reason)

                # --- Decide what to do based on close code ---
                if not should_reconnect(close_code):
                    logger.info(
                        "Permanent close code %d (%s), not reconnecting",
                        close_code,
                        close_reason,
                    )
                    return (close_code, close_reason)

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
                            return (
                                close_code,
                                "Refresh token revoked — re-login required",
                            )
                        logger.warning(
                            "Token refresh failed after close code %d: %s",
                            close_code,
                            exc,
                        )
                        self._fire_callback(
                            self._on_error,
                            "token_refresh_failed",
                            f"Token refresh failed: {exc}",
                        )
                        attempt += 1
                else:
                    attempt += 1
            finally:
                await transport.stop()

            if self._shutdown:
                return (CloseCodes.NORMAL, "Disconnected by user")

            # --- Check attempt limit ---
            if attempt >= RECONNECT_MAX_ATTEMPTS:
                logger.warning(
                    "Max reconnection attempts (%d) reached, giving up",
                    RECONNECT_MAX_ATTEMPTS,
                )
                return (
                    0,
                    f"Max reconnection attempts ({RECONNECT_MAX_ATTEMPTS}) exhausted",
                )

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

        # The ``while not self._shutdown`` guard fell through — a clean,
        # user-initiated disconnect.
        return (CloseCodes.NORMAL, "Disconnected by user")

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

    def _load_hosted_connection(
        self,
    ) -> tuple[str, str, dict | None] | None:
        """Load hosted-mode connection parameters for one attempt.

        Reads the provisioned hosted credentials file FRESH (the loader does no
        caching). No expiry check, refresh, or save ever happens in hosted mode.

        Returns:
            ``(server_url, bearer_token, established_user)`` where ``server_url``
            is the file's tunnel URL if present, else the configured default;
            or ``None`` if no hosted credentials are available (missing,
            malformed, or no path configured). ``None`` signals the caller to
            stop the tunnel cleanly. Logged once, quietly — never raises.
        """
        if self._hosted_loader is None:
            logger.warning(
                "Hosted mode enabled but no credentials path configured"
            )
            return None
        hosted = self._hosted_loader.load()
        if hosted is None:
            logger.warning("No hosted credentials available, cannot connect tunnel")
            return None
        # Register the bearer token with the file-log redactor so diagnostics /
        # file logging never write it to disk (mirrors CredentialsManager for
        # regular OAuth tokens). Never logs the token itself.
        from ..file_log import register_secret

        register_secret(hosted.token)
        server_url = hosted.tunnel_url or self._server_url
        established_user = {"id": hosted.user_id} if hosted.user_id else None
        return server_url, hosted.token, established_user

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
        reconnection manager.  We catch ``Exception`` so that callback
        failures are swallowed, but let ``BaseException`` (``SystemExit``,
        ``KeyboardInterrupt``) propagate normally.
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
