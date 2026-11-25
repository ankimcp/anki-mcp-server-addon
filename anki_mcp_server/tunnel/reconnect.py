"""Reconnection wrapper for the tunnel client.

Adds retry/backoff logic around TunnelClient. Each reconnection attempt
creates a fresh TunnelClient instance. This is the main entry point for
running a tunnel — callers interact with this, not TunnelClient directly.

Responsibilities:
- Exponential backoff with jitter between reconnection attempts
- Token refresh on auth-related close codes
- Resolving the proxy route afresh for every attempt; an unusable proxy
  setting is a terminal stop, not something to retry
- Clean shutdown via cancellation

Does NOT handle WebSocket internals — that belongs in client.py.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Callable

import anyio

from ..credentials import CredentialsManager
from .auth import (
    AuthError,
    CertificateVerificationError,
    DeviceFlowAuth,
    ProxyUnusableError,
)
from .client import TunnelCertificateError, TunnelClient, TunnelConnectionError
from .in_memory_transport import InMemoryTransport
from .protocol import (
    CERTIFICATE_GAVE_UP_NOTE,
    RECONNECT_INITIAL_DELAY,
    RECONNECT_JITTER_FACTOR,
    RECONNECT_MAX_ATTEMPTS,
    RECONNECT_MAX_DELAY,
    PROXY_UNUSABLE_STOP_CODE,
    CloseCodes,
    should_reconnect,
    should_refresh_token,
)
from .proxy import ProxySetting, TunnelProxyError, resolve_route

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
        on_route: Callable[[str], None] | None = None,
        on_certificate_error: Callable[[str], None] | None = None,
        *,
        proxy: ProxySetting,
    ) -> None:
        """Initialize the reconnection manager.

        Args:
            server_url: WebSocket URL of the tunnel relay server
                (e.g. ``wss://tunnel.ankimcp.ai``).
            mcp_server: The lowlevel ``mcp.server.lowlevel.server.Server``
                instance from ``FastMCP._mcp_server``. Used to create a
                fresh ``InMemoryTransport`` per connection attempt.
            credentials_manager: Reads/writes credentials from disk.
            auth: Device flow auth client for token refresh.
            on_tunnel_established: Called when the tunnel is ready.
                Receives ``(public_url, user_dict)``.
            on_disconnected: Called when a single connection ends.
                Receives ``(close_code, reason)``.
            on_error: Called when the server sends an error message, or when
                a connection attempt fails or the tunnel cannot start
                (``connection_failed``, ``token_refresh_failed``, ...).
                Receives ``(error_code, error_message)``. Certificate
                failures do not use it (see ``on_certificate_error``).
            on_request_completed: Called after each proxied request.
                Receives ``(method_path, status_code, duration_ms)``.
            on_reconnecting: Called before each reconnection delay.
                Receives ``(attempt_number, delay_seconds)``.
            on_stopped: Called once when the tunnel stops for good (no
                further reconnection) — whether a clean disconnect or a
                permanent failure. Receives ``(close_code, reason)``.
                Inspect ``close_code`` (``NORMAL`` == clean) to distinguish.
                An unusable proxy setting stops with
                ``(PROXY_UNUSABLE_STOP_CODE, <full actionable message>)``
                and fires no ``on_error``.
            on_route: Called once per attempt with the route taken, as one
                masked line (``ProxyRoute.display()``).
            on_certificate_error: Called with the full user-facing message
                when a connection attempt or token refresh fails because a
                certificate could not be verified. A callback of its own, so
                nothing a relay sends through ``on_error`` can look like it.
                This module writes the log record; the callback only shows
                the line.
            proxy: The ``tunnel_proxy`` setting; the route is resolved from
                it at the start of every attempt.
        """
        self._server_url = server_url
        self._mcp_server = mcp_server
        self._credentials_manager = credentials_manager
        self._auth = auth
        self._proxy = proxy

        # Callbacks forwarded to TunnelClient
        self._on_tunnel_established = on_tunnel_established
        self._on_disconnected = on_disconnected
        self._on_error = on_error
        self._on_request_completed = on_request_completed

        # Reconnection-specific callbacks
        self._on_reconnecting = on_reconnecting
        self._on_stopped = on_stopped
        self._on_route = on_route
        self._on_certificate_error = on_certificate_error

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
        - The proxy setting cannot be used (PROXY_UNUSABLE_STOP_CODE)
        - Max reconnection attempts exhausted
        - Cancelled via disconnect()

        Uses anyio.sleep for cancellation safety.
        """
        attempt = 0

        while not self._shutdown:
            certificate_failed = False

            # --- Step 1: Load credentials ---
            credentials = self._credentials_manager.load()
            if credentials is None:
                logger.warning("No credentials found, cannot connect tunnel")
                return (0, "No credentials available")

            # --- Step 1b: Resolve the route for this attempt ---
            try:
                route = resolve_route(self._proxy, self._server_url)
            except TunnelProxyError as exc:
                return self._stop_for_unusable_proxy(str(exc))
            logger.info("Tunnel route: %s", route.display())
            self._fire_callback(self._on_route, route.display())

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
                    if isinstance(exc, ProxyUnusableError):
                        return self._stop_for_unusable_proxy(str(exc))
                    if isinstance(exc, CertificateVerificationError):
                        logger.warning(
                            "Token refresh failed: %s (ssl error: %r)",
                            exc,
                            exc.__cause__,
                        )
                        self._fire_callback(
                            self._on_certificate_error,
                            f"Token refresh failed (will retry): {exc}",
                        )
                    else:
                        logger.warning("Token refresh failed: %s", exc)
                        self._fire_callback(
                            self._on_error,
                            "token_refresh_failed",
                            f"Token refresh failed (will retry): {exc}",
                        )
                    # Non-fatal refresh failure — try connecting anyway,
                    # the server will tell us if the token is truly dead.

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
                    self._on_tunnel_established, url, credentials.user,
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
                attempt += 1
                # Fall through to attempt-limit check and backoff below
                if self._shutdown:
                    return (CloseCodes.NORMAL, "Disconnected by user")
                if attempt >= RECONNECT_MAX_ATTEMPTS:
                    logger.warning(
                        "Max reconnection attempts (%d) reached, giving up",
                        RECONNECT_MAX_ATTEMPTS,
                    )
                    return (0, self._exhausted_reason(certificate_failed))
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
                server_url=self._server_url,
                credentials=credentials,
                transport=transport,
                on_tunnel_established=_on_established_wrapper,
                on_disconnected=self._on_disconnected,
                on_error=self._on_error,
                on_request_completed=self._on_request_completed,
                route=route,
            )
            self._active_client = client

            try:
                close_code, close_reason = await client.run()
            except TunnelCertificateError as exc:
                logger.warning(
                    "Tunnel certificate verification failed: %s (ssl error: %r)",
                    exc,
                    exc.__cause__,
                )
                self._fire_callback(self._on_certificate_error, str(exc))
                self._active_client = None
                attempt += 1
                certificate_failed = True
            except TunnelConnectionError as exc:
                logger.warning("Tunnel connection failed: %s", exc)
                self._fire_callback(
                    self._on_error, "connection_failed", str(exc)
                )
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
                        if isinstance(exc, ProxyUnusableError):
                            return self._stop_for_unusable_proxy(str(exc))
                        if isinstance(exc, CertificateVerificationError):
                            logger.warning(
                                "Token refresh failed after close code %d: %s "
                                "(ssl error: %r)",
                                close_code,
                                exc,
                                exc.__cause__,
                            )
                            self._fire_callback(
                                self._on_certificate_error,
                                f"Token refresh failed: {exc}",
                            )
                            certificate_failed = True
                        else:
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
                return (0, self._exhausted_reason(certificate_failed))

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

    def _stop_for_unusable_proxy(self, message: str) -> tuple[int, str]:
        """Log an unusable proxy setting once and give the terminal stop result.

        The full message travels as the stop reason, a channel a relay cannot
        produce, so ``on_error`` (which also carries server codes) is not used.
        """
        logger.warning("Tunnel proxy cannot be used: %s", message)
        return (PROXY_UNUSABLE_STOP_CODE, message)

    @staticmethod
    def _exhausted_reason(certificate_failed: bool) -> str:
        """The give-up reason, naming a certificate failure as the last error."""
        reason = f"Max reconnection attempts ({RECONNECT_MAX_ATTEMPTS}) exhausted"
        if certificate_failed:
            reason += f" ({CERTIFICATE_GAVE_UP_NOTE})"
        return reason

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
