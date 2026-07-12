"""WebSocket tunnel client — manages a single connection lifecycle.

Connects to the tunnel relay server, receives forwarded MCP requests from
AI clients, processes them via an in-memory transport directly into the
FastMCP server, and sends responses back through the WebSocket.

This module handles ONE connection. It does NOT handle reconnection — that
responsibility belongs to a separate reconnection manager. When the
connection drops, ``run()`` returns the close code and reason so the caller
can decide what to do next.

Uses:
- ``websockets.asyncio.client`` (vendored) for the WebSocket connection
- ``InMemoryTransport`` for direct JSON-RPC processing (no HTTP round-trip)
- ``anyio`` (vendored) for task groups and cancellation-safe sleep
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

import anyio
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK

from .in_memory_transport import InMemoryTransport
from .protocol import (
    CLIENT_TYPE,
    CLIENT_TYPE_HEADER,
    CLIENT_VERSION_HEADER,
    CONNECTION_TIMEOUT,
    HEALTH_CHECK_TIMEOUT,
    HEARTBEAT_INTERVAL,
    CloseCodes,
    TunnelPing,
    TunnelRequest,
    TunnelResponse,
    normalize_client_version,
    parse_server_message,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------

class TunnelConnectionError(Exception):
    """Raised when the initial connection or handshake fails.

    This covers WebSocket connection errors, authentication rejections,
    and timeouts waiting for the ``tunnel_established`` message.
    """


class _HealthCheckTimeout(Exception):
    """Internal signal that the server stopped sending pings."""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _extract_request_id(body: str | None) -> Any:
    """Best-effort parse of the inner JSON-RPC request ``id`` from a body string.

    Returns the ``id`` so error envelopes can be correlated by the end client,
    or ``None`` if the body is absent, not a JSON object, or has no ``id``
    (e.g. a batch request, which is a list, or a malformed body).
    """
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return None
    if isinstance(parsed, dict):
        return parsed.get("id")
    return None


# --------------------------------------------------------------------------
# Tunnel Client
# --------------------------------------------------------------------------

class TunnelClient:
    """Manages a single WebSocket connection to the tunnel relay server.

    The lifecycle is simple:

    1. ``run()`` connects, waits for the handshake, then enters the
       message loop.
    2. Incoming ``request`` messages are proxied to the local MCP server.
    3. ``ping`` messages get an immediate ``pong`` reply.
    4. A health check task closes the connection if the server stops
       sending pings.
    5. When the connection ends (server close, network error, or
       ``disconnect()``), ``run()`` returns ``(close_code, reason)``.

    All callbacks are optional and fire-and-forget — exceptions in
    callbacks are logged but never propagate into the message loop.
    """

    def __init__(
        self,
        server_url: str,
        bearer_token: str,
        transport: InMemoryTransport,
        on_tunnel_established: Callable[[str], None] | None = None,
        on_disconnected: Callable[[int, str], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
        on_request_completed: Callable[[str, int, float], None] | None = None,
    ) -> None:
        """Initialize the tunnel client.

        Args:
            server_url: WebSocket URL of the tunnel relay server
                (e.g. ``wss://tunnel.ankimcp.ai``).
            bearer_token: Bearer token string sent as the ``Authorization``
                header. In regular mode this is an OAuth access token; in
                hosted mode it is the opaque token from the hosted credentials
                file. The client only needs the token string, not the full
                credentials envelope.
            transport: In-memory transport for direct JSON-RPC processing
                into the FastMCP server (no HTTP round-trip).
            on_tunnel_established: Called when the tunnel is ready.
                Receives ``(public_url,)``.
            on_disconnected: Called when the connection ends.
                Receives ``(close_code, reason)``.
            on_error: Called when the server sends an error message.
                Receives ``(error_code, error_message)``.
            on_request_completed: Called after each proxied request.
                Receives ``(method_path, status_code, duration_ms)``.
        """
        self._server_url = server_url
        self._bearer_token = bearer_token
        self._transport = transport

        # Callbacks
        self._on_tunnel_established = on_tunnel_established
        self._on_disconnected = on_disconnected
        self._on_error = on_error
        self._on_request_completed = on_request_completed

        # Connection state
        self._ws: Any = None  # websockets ClientConnection, set during run()
        self._last_server_ping: float = 0.0
        self._tunnel_url: str | None = None
        self._pending_requests: set[asyncio.Task] = set()
        # Cancel scope of the message-loop task group, captured during run()
        # so disconnect() can proactively wind down the health-check loop.
        self._cancel_scope: anyio.CancelScope | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> tuple[int, str]:
        """Connect and run the message loop until disconnected.

        Returns:
            ``(close_code, close_reason)`` when the connection ends.

        Raises:
            TunnelConnectionError: If the initial WebSocket connection
                or the tunnel handshake fails.
        """
        # --- Step 1: Connect ---
        try:
            ws = await connect(
                self._server_url,
                additional_headers=self._build_connect_headers(),
                open_timeout=CONNECTION_TIMEOUT,
                # Disable websockets' built-in keepalive — we handle pings at
                # the application protocol level.
                ping_interval=None,
                ping_timeout=None,
            )
        except Exception as exc:
            raise TunnelConnectionError(
                f"Failed to connect to {self._server_url}: {exc}"
            ) from exc

        self._ws = ws
        self._last_server_ping = time.monotonic()

        # --- Step 2: Wait for tunnel_established handshake ---
        try:
            await self._wait_for_handshake(ws)
        except Exception as exc:
            await self._close_ws_quietly(ws)
            self._ws = None
            raise TunnelConnectionError(
                f"Tunnel handshake failed: {exc}"
            ) from exc

        # --- Step 3: Run receive loop + health check concurrently ---
        close_code = CloseCodes.NORMAL
        close_reason = "Connection closed"

        try:
            async with anyio.create_task_group() as tg:
                self._cancel_scope = tg.cancel_scope
                # Run the health check as a sibling, but drive the receive
                # loop INLINE so we know precisely when the connection ends.
                #
                # A clean WebSocket close (code 1000 — what a user-initiated
                # disconnect produces) makes the receive iterator stop
                # *silently*: websockets does not raise on normal closure.
                # anyio task groups only auto-cancel siblings when a child
                # RAISES; on normal completion they WAIT for all children. So
                # if both ran via start_soon(), the infinite health-check loop
                # would block the group forever and run() would never return.
                #
                # By awaiting the receive loop inline and cancelling the group
                # scope once it returns, the health-check loop is cancelled and
                # run() returns normally with the default NORMAL close code.
                # The unclean path is unaffected: a dropped connection raises
                # ConnectionClosed out of the receive loop, the cancel below is
                # skipped, and the exception tears the group down as before
                # (so reconnect.py still re-attempts).
                tg.start_soon(self._health_check_loop, ws)
                await self._receive_loop(ws)
                tg.cancel_scope.cancel()
        except* ConnectionClosedOK:
            close_code = CloseCodes.NORMAL
            close_reason = "Normal closure"
        except* (_HealthCheckTimeout, ConnectionClosed) as eg:
            # Extract the first exception from the group
            exc = eg.exceptions[0]
            if isinstance(exc, ConnectionClosed) and exc.rcvd is not None:
                # Use exc.rcvd (Close frame) instead of the deprecated
                # exc.code / exc.reason properties (deprecated in websockets 13.1).
                close_code = exc.rcvd.code
                close_reason = exc.rcvd.reason or "Connection lost"
            else:
                close_code = CloseCodes.GOING_AWAY
                close_reason = str(exc)
        except* Exception as eg:
            exc = eg.exceptions[0]
            logger.error("Unexpected error in tunnel message loop: %s", exc)
            close_code = CloseCodes.GOING_AWAY
            close_reason = str(exc)
        finally:
            # Cancel and await any in-flight request tasks
            for task in self._pending_requests:
                task.cancel()
            if self._pending_requests:
                await asyncio.gather(*self._pending_requests, return_exceptions=True)
            self._pending_requests.clear()

            await self._close_ws_quietly(ws)
            self._ws = None
            self._cancel_scope = None

        # --- Step 4: Fire disconnected callback ---
        self._fire_callback(
            self._on_disconnected, close_code, close_reason
        )

        return close_code, close_reason

    def _build_connect_headers(self) -> dict[str, str]:
        """Build the WebSocket upgrade headers for the tunnel connection.

        Always includes ``Authorization`` (Bearer token) and the
        client-type header identifying this as the Anki addon. The client
        version header is added only when the addon version normalizes to a
        valid ``MAJOR.MINOR.PATCH`` — omitting it rather than sending a
        known-invalid value. Both identity headers are optional server-side.

        The addon ``__version__`` is imported lazily here (not at module top
        level) to avoid any import-cycle risk during package init; by the time
        a tunnel connects, the package ``__init__`` is fully loaded. This
        matches the codebase's lazy-import idiom (e.g. ``from aqt import mw``
        inside functions).
        """
        from .. import __version__

        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._bearer_token}",
            CLIENT_TYPE_HEADER: CLIENT_TYPE,
        }
        client_version = normalize_client_version(__version__)
        if client_version is not None:
            headers[CLIENT_VERSION_HEADER] = client_version
        return headers

    async def disconnect(self) -> None:
        """Close the WebSocket gracefully if connected.

        Proactively cancels the message-loop task group so the health-check
        loop winds down immediately — even before the WebSocket close
        round-trips with the server. Code after the task group's
        ``async with`` (the ``on_disconnected`` fire and the ``return``)
        still runs: the ``CancelledError`` is absorbed at the group boundary.
        """
        if self._cancel_scope is not None:
            self._cancel_scope.cancel()
        ws = self._ws
        if ws is not None:
            await self._close_ws_quietly(ws)

    # ------------------------------------------------------------------
    # Handshake
    # ------------------------------------------------------------------

    async def _wait_for_handshake(self, ws: Any) -> None:
        """Wait for the ``tunnel_established`` message from the server.

        Must be called immediately after connecting. Times out after
        ``CONNECTION_TIMEOUT`` seconds.

        Raises:
            TunnelConnectionError: On timeout or unexpected first message.
        """
        try:
            with anyio.fail_after(CONNECTION_TIMEOUT):
                raw = await ws.recv()
        except TimeoutError as exc:
            raise TunnelConnectionError(
                f"Timed out waiting for tunnel_established "
                f"(>{CONNECTION_TIMEOUT}s)"
            ) from exc

        try:
            msg = parse_server_message(raw)
        except ValueError as exc:
            raise TunnelConnectionError(
                f"Invalid handshake message: {exc}"
            ) from exc

        if msg.get("type") != "tunnel_established":
            raise TunnelConnectionError(
                f"Expected tunnel_established, got {msg.get('type')!r}"
            )

        self._tunnel_url = msg["url"]

        logger.info("Tunnel established: url=%s", self._tunnel_url)

        self._fire_callback(self._on_tunnel_established, self._tunnel_url)

    # ------------------------------------------------------------------
    # Receive loop
    # ------------------------------------------------------------------

    async def _receive_loop(self, ws: Any) -> None:
        """Iterate over incoming WebSocket messages and dispatch them.

        Exits when the connection is closed. ``ConnectionClosed``
        exceptions propagate to the task group so ``run()`` can capture
        the close code.
        """
        async for raw in ws:
            try:
                msg = parse_server_message(raw)
            except ValueError as exc:
                logger.warning("Skipping malformed message: %s", exc)
                continue

            msg_type = msg.get("type")

            match msg_type:
                case "request":
                    # Fire-and-forget: spawn each request as a separate
                    # asyncio task so the receive loop stays responsive
                    # to pings and new requests while slow proxied
                    # requests are in flight.
                    task = asyncio.create_task(
                        self._handle_request(ws, msg)  # type: ignore[arg-type]
                    )
                    self._pending_requests.add(task)
                    task.add_done_callback(self._pending_requests.discard)
                case "ping":
                    await self._handle_ping(ws, msg)  # type: ignore[arg-type]
                case "error":
                    self._handle_error(msg)
                case _:
                    logger.warning("Unknown message type: %s", msg_type)

    # ------------------------------------------------------------------
    # Request handling
    # ------------------------------------------------------------------

    async def _handle_request(self, ws: Any, msg: TunnelRequest) -> None:
        """Process a tunneled MCP request via in-memory transport and relay the response.

        Feeds the JSON-RPC body directly into the FastMCP server via
        ``InMemoryTransport.handle_request()`` — no HTTP round-trip.

        On processing errors, sends a 500 response with a JSON-RPC error
        body back through the tunnel.
        """
        request_id = msg["requestId"]
        method = msg["method"]
        path = msg["path"]

        # Body is always a string per protocol contract (z.string().optional())
        body: str | None = msg.get("body")

        start = time.monotonic()

        try:
            if body:
                response_body = await self._transport.handle_request(body)
            else:
                response_body = ""

            tunnel_response: TunnelResponse = {
                "type": "response",
                "requestId": request_id,
                "statusCode": 200,
                "headers": {"content-type": "application/json"},
            }
            if response_body:
                tunnel_response["body"] = response_body

            await ws.send(json.dumps(tunnel_response))

            duration_ms = (time.monotonic() - start) * 1000
            logger.debug(
                "Processed %s %s -> 200 (%.1fms)",
                method, path, duration_ms,
            )
            self._fire_callback(
                self._on_request_completed,
                f"{method} {path}",
                200,
                duration_ms,
            )

        except asyncio.TimeoutError:
            duration_ms = (time.monotonic() - start) * 1000
            logger.warning(
                "Timed out processing %s %s after %.1fms", method, path, duration_ms
            )

            # Mirror the relay's request-timeout convention: 504 + JSON-RPC
            # error code -32004. Echo the inner request id when we can recover
            # it from the body so the end client can correlate the error.
            timeout_response: TunnelResponse = {
                "type": "response",
                "requestId": request_id,
                "statusCode": 504,
                "headers": {"content-type": "application/json"},
                "body": json.dumps({
                    "jsonrpc": "2.0",
                    "id": _extract_request_id(body),
                    "error": {
                        "code": -32004,
                        "message": "Request timed out",
                    },
                }),
            }
            try:
                await ws.send(json.dumps(timeout_response))
            except Exception as send_exc:
                logger.error(
                    "Failed to send timeout response for request %s: %s",
                    request_id,
                    send_exc,
                )

            self._fire_callback(
                self._on_request_completed,
                f"{method} {path}",
                504,
                duration_ms,
            )

        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error(
                "Failed to process %s %s: %s", method, path, exc
            )

            # Send a 500 error response through the tunnel so the
            # AI client gets a meaningful error instead of a timeout.
            error_response: TunnelResponse = {
                "type": "response",
                "requestId": request_id,
                "statusCode": 500,
                "headers": {"content-type": "application/json"},
                "body": json.dumps({
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32603,
                        "message": f"Internal error: {exc}",
                    },
                    "id": None,
                }),
            }
            try:
                await ws.send(json.dumps(error_response))
            except Exception as send_exc:
                logger.error(
                    "Failed to send error response for request %s: %s",
                    request_id,
                    send_exc,
                )

            self._fire_callback(
                self._on_request_completed,
                f"{method} {path}",
                500,
                duration_ms,
            )

    # ------------------------------------------------------------------
    # Ping handling
    # ------------------------------------------------------------------

    async def _handle_ping(self, ws: Any, msg: TunnelPing) -> None:
        """Respond to a server ping with a pong and update the health timestamp."""
        self._last_server_ping = time.monotonic()
        pong = {"type": "pong", "timestamp": msg["timestamp"]}
        await ws.send(json.dumps(pong))
        logger.debug("Pong sent (timestamp=%d)", msg["timestamp"])

    # ------------------------------------------------------------------
    # Error handler
    # ------------------------------------------------------------------

    def _handle_error(self, msg: dict[str, Any]) -> None:
        """Handle a server-side error notification."""
        code = msg.get("code", "unknown")
        message = msg.get("message", "Unknown error")
        details = msg.get("details")

        logger.warning(
            "Server error: code=%s, message=%s, details=%s",
            code, message, details,
        )
        self._fire_callback(self._on_error, code, message)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def _health_check_loop(self, ws: Any) -> None:
        """Periodically check that the server is still sending pings.

        If more than ``HEALTH_CHECK_TIMEOUT`` seconds pass without a
        server ping, the connection is presumed dead and closed.
        """
        while True:
            await anyio.sleep(HEARTBEAT_INTERVAL)

            elapsed = time.monotonic() - self._last_server_ping
            if elapsed > HEALTH_CHECK_TIMEOUT:
                logger.warning(
                    "No server ping in %.1fs (timeout=%.1fs), closing connection",
                    elapsed,
                    HEALTH_CHECK_TIMEOUT,
                )
                await self._close_ws_quietly(ws)
                raise _HealthCheckTimeout(f"No server ping in {elapsed:.1f}s")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fire_callback(self, callback: Callable | None, *args: Any) -> None:
        """Invoke a callback, catching and logging any exceptions.

        Callbacks are fire-and-forget — errors must never crash the
        tunnel client.  We catch ``Exception`` so that callback failures
        are swallowed, but let ``BaseException`` (``SystemExit``,
        ``KeyboardInterrupt``) propagate normally.
        """
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as exc:
            logger.error(
                "Callback %s raised: %s", callback.__name__, exc,
                exc_info=True,
            )

    @staticmethod
    async def _close_ws_quietly(ws: Any) -> None:
        """Close a WebSocket connection, suppressing any errors."""
        try:
            await ws.close()
        except Exception as exc:
            logger.debug("Error closing WebSocket (ignored): %s", exc)
