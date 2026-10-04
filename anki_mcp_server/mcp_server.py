"""MCP server running in background thread with HTTP transport.

This module implements the MCP server component that runs in a separate background
thread with its own asyncio event loop. It uses the official MCP SDK (FastMCP) to
handle the protocol and exposes Anki operations as MCP tools.

Architecture:
    - Background thread: Runs asyncio event loop with MCP server and optional tunnel
    - HTTP transport: Uses FastMCP's built-in streamable HTTP (starlette + uvicorn)
    - Tunnel transport: TunnelReconnectManager runs as an asyncio task alongside HTTP
    - Queue bridge: Tool handlers bridge calls to main thread via QueueBridge
    - Async I/O: All tool handlers use asyncio.to_thread to bridge blocking queue ops

Thread Safety:
    - This module runs entirely in a background thread
    - Never accesses mw.col directly - all Anki operations go through QueueBridge
    - Uses asyncio.to_thread to safely call blocking queue.Queue operations
    - Qt thread signals tunnel start/stop via asyncio.run_coroutine_threadsafe()
"""

import asyncio
import errno
import logging
import os
import socket
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import Icon

from . import __version__
from .config import Config
from .http_auth import ApiKeyAuthMiddleware
from .transport_security_config import build_transport_security
from .queue_bridge import BridgeError, QueueBridge, ToolRequest
from .primitives import register_all_tools, register_all_resources, register_all_prompts

logger = logging.getLogger(__name__)

# asyncio's create_server default for reuse_address.
_REUSE_ADDRESS = os.name == "posix" and sys.platform != "cygwin"

# Server-level instructions sent in the initialize result. The Anthropic
# Software Directory Policy asks tool descriptions to state what each tool does
# and when it applies; cross-tool workflow guidance lives here instead.
SERVER_INSTRUCTIONS = (
    "This server works on the user's local Anki collection. When the user syncs "
    "Anki across devices, offer to sync with AnkiWeb at the start and end of a "
    "session; no tool call syncs implicitly; only the sync tool contacts AnkiWeb. "
    "AI-driven review loop: get_due_cards -> present_card (question first, then "
    "show_answer=true once the user has answered) -> rate_card, with the user "
    "confirming the rating after seeing the answer. Review inside Anki's own "
    "window (when the GUI review tools are enabled): gui_deck_review -> "
    "gui_current_card -> gui_show_answer -> gui_answer_card; by default the user "
    "presses the answer buttons themselves, and gui_answer_card applies only when "
    "the user asked for hands-free rating and confirmed the rating; rate_card does not "
    "apply to a card shown in Anki's reviewer. Create, edit or delete only the "
    "notes, decks, note types and media the user asked for."
)


class _FirstErrorCapture(logging.Handler):
    """Remembers the first record at ERROR or above, reduced to one line.

    The record can be a whole traceback (Starlette passes
    ``traceback.format_exc()`` on lifespan failure and uvicorn logs it
    verbatim); its last non-empty line names the exception, which is what
    fits a single-line status label.
    """

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.message: Optional[str] = None

    def emit(self, record: logging.LogRecord) -> None:
        if self.message is None:
            try:
                lines = record.getMessage().strip().splitlines()
                if lines:
                    self.message = lines[-1]
            except Exception:  # noqa: BLE001 - a bad record must not break startup
                pass


def build_fastmcp(streamable_path: str, transport_security: TransportSecuritySettings) -> FastMCP:
    """Construct the shared FastMCP instance used by both HTTP and tunnel transports.

    FastMCP exposes no `version` kwarg, so the lowlevel Server defaults to
    version=None. Without an explicit value, create_initialization_options()
    falls back to importlib.metadata.version("mcp"), which returns None when
    a stale/empty mcp-*.dist-info is found first on sys.path -- pydantic then
    rejects server_version=None and crashes every initialize handshake.
    Setting the addon's own version short-circuits that fallback.
    """
    mcp = FastMCP(
        "anki-mcp",
        instructions=SERVER_INSTRUCTIONS,
        website_url="https://ankimcp.ai",
        icons=[Icon(
            src="https://ankimcp.ai/favicon.svg",
            mimeType="image/svg+xml",
            sizes=["any"],
        )],
        streamable_http_path=streamable_path,
        stateless_http=True,
        transport_security=transport_security,
    )
    mcp._mcp_server.version = __version__
    return mcp


def _bind_http_sockets(host: str, port: int) -> list[socket.socket]:
    """Bind the HTTP server sockets the way ``loop.create_server`` would.

    This is what uvicorn itself does with ``host``/``port`` (it calls
    ``loop.create_server``), done up front so a failure is an ``OSError`` we
    can handle. The sockets are bound but not listening, exactly as
    ``create_server`` leaves its own: uvicorn passes them to
    ``loop.create_server(sock=...)``, whose ``Server._start_serving`` calls
    ``listen(backlog)`` with uvicorn's configured backlog. Mirrors CPython
    3.13's ``BaseEventLoop.create_server``:

    - one socket per distinct address ``host`` resolves to (so ``localhost``
      still gets both ``::1`` and ``127.0.0.1``);
    - an address whose ``socket()`` call fails for any reason is skipped
      (e.g. ``EAFNOSUPPORT`` with IPv6 disabled in the kernel);
    - an address whose ``bind()`` fails with ``EADDRNOTAVAIL`` is skipped
      (bpo-30945: e.g. ``localhost`` -> ``::1`` with IPv6 off on every
      interface); any other bind error fails the whole call;
    - ``SO_REUSEADDR`` on POSIX only, and ``IPV6_V6ONLY`` on IPv6 sockets.

    ``uvicorn.Config.bind_socket()`` is deliberately not used: it sets
    ``SO_REUSEADDR`` on Windows too, where that lets a second socket bind a
    port another process is already listening on instead of failing, and it
    reports a bind failure via ``sys.exit`` rather than ``OSError``.

    Raises:
        OSError: name resolution failed, a bind failed with anything but
            ``EADDRNOTAVAIL``, or no address could be bound at all. Sockets
            created before the failure are closed first.
        UnicodeError: ``host`` cannot be IDNA-encoded (e.g. a label longer
            than 63 characters); ``getaddrinfo`` raises it before any socket
            exists.
    """
    infos = socket.getaddrinfo(
        host or None, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE,
    )
    sockets: list[socket.socket] = []
    seen: set[tuple[int, Any]] = set()
    try:
        for family, socktype, proto, _canonname, sockaddr in infos:
            if (family, sockaddr) in seen:
                continue
            seen.add((family, sockaddr))
            try:
                sock = socket.socket(family, socktype, proto)
            except OSError:
                continue
            sockets.append(sock)
            if _REUSE_ADDRESS:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
            if family == socket.AF_INET6 and hasattr(socket, "IPPROTO_IPV6"):
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, True)
            try:
                sock.bind(sockaddr)
            except OSError as exc:
                if exc.errno == errno.EADDRNOTAVAIL:
                    sockets.pop()
                    sock.close()
                    continue
                raise
        if not sockets:
            raise OSError(
                f"could not bind on any address out of {[info[4] for info in infos]!r}"
            )
    except BaseException:
        for sock in sockets:
            sock.close()
        raise
    return sockets


class McpServer:
    """MCP server running in background thread.

    This class manages the lifecycle of the MCP server which runs in a separate
    background thread with its own asyncio event loop. It uses FastMCP's built-in
    HTTP transport (starlette + uvicorn) for communication.

    The server acts as a bridge between AI clients and Anki's main thread:
    1. AI client sends MCP request via HTTP
    2. Tool handler receives request in background thread
    3. Handler puts request in queue and waits for response
    4. Main thread processes request and returns result via queue
    5. Handler returns result to AI client

    Tunnel support:
    The tunnel runs as an asyncio task alongside the HTTP server on the same
    event loop. The Qt main thread can start/stop the tunnel dynamically via
    start_tunnel()/stop_tunnel(), which use asyncio.run_coroutine_threadsafe()
    to schedule operations on the background loop.

    Attributes:
        _bridge: Queue bridge for thread-safe communication with main thread
        _config: Server configuration (HTTP host/port, mode, etc.)
        _thread: Background thread running the asyncio event loop
        _loop: Reference to the background asyncio event loop (set once running)
        _uvicorn_server: The uvicorn.Server instance; stop() flips should_exit on it
        _tunnel_task: The asyncio task running TunnelReconnectManager.run()
        _tunnel_manager: The active TunnelReconnectManager instance
        _tunnel_running: Thread-safe flag indicating tunnel status
        _http_error: Why HTTP failed to start (loop kept alive), or None
        _fatal_error: Why the background thread died, or None
    """

    def __init__(self, bridge: QueueBridge, config: Config) -> None:
        """Initialize MCP server.

        Args:
            bridge: Queue bridge for communication with main thread
            config: Server configuration
        """
        self._bridge = bridge
        self._config = config
        self._thread: Optional[threading.Thread] = None
        # Set in _async_main() / _run_http_mode(); used by stop() to signal
        # uvicorn shutdown across the asyncio/Qt thread boundary.
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._uvicorn_server: Optional[uvicorn.Server] = None

        # Bounded executor for bridging blocking queue ops into asyncio.
        # Used by _call_main_thread() via loop.run_in_executor().
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mcp-bridge")

        # FastMCP instance — set in _async_main(), used by tunnel to get
        # the lowlevel Server for in-memory transport.
        self._mcp_instance: Optional[FastMCP] = None

        # Async keepalive event — created on the event loop in _async_main(),
        # used to keep the loop alive when HTTP is disabled (tunnel-only mode)
        # or failed to start. Otherwise uvicorn.Server.serve() blocks the loop.
        self._async_shutdown: Optional[asyncio.Event] = None

        # Tunnel state — all access is thread-safe via GIL for simple
        # attribute reads/writes, plus asyncio.run_coroutine_threadsafe()
        # for operations that touch the event loop.
        self._tunnel_task: Optional[asyncio.Task] = None
        self._tunnel_manager: Optional[Any] = None  # TunnelReconnectManager
        self._tunnel_running: bool = False

        # Failure reasons for the UI. _http_error: HTTP could not start but
        # the loop stayed up for the tunnel. _fatal_error: the background
        # thread itself died (loop gone, tunnel impossible).
        self._http_error: Optional[str] = None
        self._fatal_error: Optional[str] = None

    def start(self) -> None:
        """Start MCP server in background thread.

        Creates and starts a daemon thread that runs the asyncio event loop.
        The daemon flag ensures the thread won't prevent Anki from closing.

        Thread Safety:
            Safe to call from main thread (Qt event loop).
        """
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal shutdown and wait for the background thread to exit.

        Sets uvicorn's ``should_exit`` flag via ``call_soon_threadsafe`` so the
        serve loop wakes on its next tick (~100ms) and releases the listening
        socket. Then joins the background thread with a short timeout so a
        subsequent ``start()`` (e.g. profile switch) doesn't race the port
        rebind.

        Thread Safety:
            Safe to call from main thread (Qt event loop).
        """
        loop = self._loop
        server = self._uvicorn_server
        async_shutdown = self._async_shutdown

        if loop is not None and not loop.is_closed():
            # Best-effort tunnel teardown — schedules _stop_tunnel_async() on
            # the background loop. Safe to call even when no tunnel is active.
            if self._tunnel_running or self._tunnel_task is not None:
                try:
                    self.stop_tunnel()
                except RuntimeError:
                    pass

            # Main's shutdown signal: flip uvicorn.should_exit so the serve
            # loop unwinds on its next tick (~100ms) and releases the socket.
            if server is not None:
                try:
                    loop.call_soon_threadsafe(lambda: setattr(server, "should_exit", True))
                except RuntimeError:
                    # Loop already stopped/closing — nothing to signal.
                    pass

            # Tunnel-only mode keepalive: when HTTP is disabled there is no
            # uvicorn serve() blocking the loop, so wake _async_main() via
            # the asyncio.Event it's waiting on.
            if async_shutdown is not None:
                try:
                    loop.call_soon_threadsafe(async_shutdown.set)
                except RuntimeError:
                    pass

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None
        self._loop = None
        self._uvicorn_server = None
        self._executor.shutdown(wait=False)

    # ------------------------------------------------------------------
    # Tunnel control — called from Qt main thread
    # ------------------------------------------------------------------

    def start_tunnel(
        self,
        credentials_manager: Any,
        auth: Any,
        on_tunnel_established: Callable[[str, dict | None], None] | None = None,
        on_disconnected: Callable[[int, str], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
        on_request_completed: Callable[[str, int, float], None] | None = None,
        on_reconnecting: Callable[[int, float], None] | None = None,
        on_stopped: Callable[[int, str], None] | None = None,
    ) -> bool:
        """Start the tunnel alongside the HTTP server.

        Called from the Qt main thread. Schedules tunnel startup on the
        background asyncio loop via asyncio.run_coroutine_threadsafe().

        Returns:
            True once startup is scheduled on the loop; False (after logging
            a warning) when the loop is not running, so nothing was scheduled
            and none of the callbacks will fire.

        Args:
            credentials_manager: CredentialsManager instance for token I/O.
            auth: DeviceFlowAuth instance for token refresh.
            on_tunnel_established: Called when tunnel is ready (url, user).
            on_disconnected: Called when a connection ends (code, reason).
            on_error: Called on server error (error_code, message).
            on_request_completed: Called after each proxied request
                (method_path, status_code, duration_ms).
            on_reconnecting: Called before each reconnection delay
                (attempt, delay_seconds).
            on_stopped: Called once when the tunnel stops for good (no
                further reconnection) — whether a clean disconnect or a
                permanent failure. Receives (close_code, reason); inspect
                close_code (NORMAL == clean) to distinguish.

        Thread Safety:
            Safe to call from any thread. The actual work runs on the
            background asyncio loop.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            logger.warning("Cannot start tunnel: asyncio loop not running")
            return False

        asyncio.run_coroutine_threadsafe(
            self._start_tunnel_async(
                credentials_manager=credentials_manager,
                auth=auth,
                on_tunnel_established=on_tunnel_established,
                on_disconnected=on_disconnected,
                on_error=on_error,
                on_request_completed=on_request_completed,
                on_reconnecting=on_reconnecting,
                on_stopped=on_stopped,
            ),
            loop,
        )
        return True

    def stop_tunnel(self) -> None:
        """Stop the tunnel if running.

        Called from the Qt main thread. Schedules tunnel shutdown on the
        background asyncio loop via asyncio.run_coroutine_threadsafe().

        Thread Safety:
            Safe to call from any thread.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        asyncio.run_coroutine_threadsafe(self._stop_tunnel_async(), loop)

    @property
    def tunnel_running(self) -> bool:
        """Whether the tunnel is currently connected.

        Thread Safety:
            Safe to read from any thread (Python GIL makes simple
            attribute reads atomic).
        """
        return self._tunnel_running

    @property
    def tunnel_active(self) -> bool:
        """Whether the tunnel task is alive (connecting, connected, or reconnecting).

        Unlike ``tunnel_running`` which is only True when connected,
        this is True whenever the tunnel task exists and hasn't finished.

        Thread Safety:
            Safe to read from any thread.
        """
        task = self._tunnel_task
        return task is not None and not task.done()

    # ------------------------------------------------------------------
    # Server health — read from the Qt main thread
    # ------------------------------------------------------------------

    @property
    def loop_alive(self) -> bool:
        """Whether the background thread and its asyncio loop are both alive.

        False before ``_async_main`` has captured the loop, after
        ``asyncio.run`` has closed it, and once the thread has exited.

        Thread Safety:
            Safe to read from any thread.
        """
        loop = self._loop
        thread = self._thread
        return (
            loop is not None
            and not loop.is_closed()
            and thread is not None
            and thread.is_alive()
        )

    @property
    def http_started(self) -> bool:
        """Whether uvicorn finished startup and is accepting connections.

        Derived from ``uvicorn.Server.started``, which uvicorn sets at the
        end of ``startup()`` once every listener is serving. It is never
        reset, so pair it with ``loop_alive`` to know HTTP is still up.

        Thread Safety:
            Safe to read from any thread.
        """
        server = self._uvicorn_server
        return server is not None and bool(server.started)

    @property
    def http_error(self) -> Optional[str]:
        """Why HTTP failed to start while the loop stayed alive, or None."""
        return self._http_error

    @property
    def fatal_error(self) -> Optional[str]:
        """Why the background thread died (``"<ExcType>: <msg>"``), or None."""
        return self._fatal_error

    # ------------------------------------------------------------------
    # Tunnel async internals — run on the background asyncio loop
    # ------------------------------------------------------------------

    async def _start_tunnel_async(
        self,
        credentials_manager: Any,
        auth: Any,
        on_tunnel_established: Callable[[str, dict | None], None] | None = None,
        on_disconnected: Callable[[int, str], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
        on_request_completed: Callable[[str, int, float], None] | None = None,
        on_reconnecting: Callable[[int, float], None] | None = None,
        on_stopped: Callable[[int, str], None] | None = None,
    ) -> None:
        """Internal: start the tunnel on the asyncio loop.

        Creates a TunnelReconnectManager and runs it as an asyncio task.
        If a tunnel is already running, stops it first.
        """
        # Stop existing tunnel if running
        if self._tunnel_task is not None and not self._tunnel_task.done():
            await self._stop_tunnel_async()

        from .tunnel.reconnect import TunnelReconnectManager

        # Wrap the on_tunnel_established callback to also set _tunnel_running
        original_on_established = on_tunnel_established

        def _on_established_wrapper(url: str, user: dict | None = None) -> None:
            self._tunnel_running = True
            if original_on_established is not None:
                original_on_established(url, user)

        # Wrap on_disconnected to update _tunnel_running
        original_on_disconnected = on_disconnected

        def _on_disconnected_wrapper(code: int, reason: str) -> None:
            # Don't clear _tunnel_running here — the reconnect manager may
            # reconnect. Only on_stopped and explicit stop clear it.
            if original_on_disconnected is not None:
                original_on_disconnected(code, reason)

        # Wrap on_stopped to clear _tunnel_running. Fires once when the tunnel
        # stops for good — clean disconnect or permanent failure alike.
        original_on_stopped = on_stopped

        def _on_stopped_wrapper(code: int, reason: str) -> None:
            self._tunnel_running = False
            self._tunnel_manager = None
            if original_on_stopped is not None:
                original_on_stopped(code, reason)

        manager = TunnelReconnectManager(
            server_url=self._config.tunnel_server_url,
            mcp_server=self._mcp_instance._mcp_server,  # type: ignore[union-attr]
            credentials_manager=credentials_manager,
            auth=auth,
            on_tunnel_established=_on_established_wrapper,
            on_disconnected=_on_disconnected_wrapper,
            on_error=on_error,
            on_request_completed=on_request_completed,
            on_reconnecting=on_reconnecting,
            on_stopped=_on_stopped_wrapper,
        )

        self._tunnel_manager = manager

        # Run as a fire-and-forget task alongside the HTTP server
        self._tunnel_task = asyncio.create_task(
            self._run_tunnel(manager),
            name="tunnel-reconnect",
        )

        logger.info("Tunnel task started (server=%s)", self._config.tunnel_server_url)

    async def _run_tunnel(self, manager: Any) -> None:
        """Wrapper that runs the tunnel manager and cleans up on exit.

        Catches ``BaseException`` (not just ``Exception``) because anyio's
        task group can raise ``BaseExceptionGroup`` if a child task raises
        a ``BaseException`` subclass (e.g. ``KeyboardInterrupt``).  We must
        never let an exception escape this wrapper — it would become an
        unhandled exception on the asyncio event loop.
        """
        try:
            await manager.run()
        except asyncio.CancelledError:
            logger.info("Tunnel task cancelled")
        except BaseException as exc:
            logger.error("Tunnel task failed unexpectedly: %s", exc, exc_info=True)
        finally:
            self._tunnel_running = False
            self._tunnel_manager = None
            self._tunnel_task = None

    async def _stop_tunnel_async(self) -> None:
        """Internal: stop the tunnel on the asyncio loop."""
        manager = self._tunnel_manager
        task = self._tunnel_task

        if manager is not None:
            await manager.disconnect()

        # Let the task wind down naturally: disconnect() already set the
        # manager's shutdown flag and closed the active client, so the
        # reconnect loop will exit and fire on_stopped on its own. Cancelling
        # here would inject CancelledError mid-unwind and drop that terminal
        # callback. Only force-cancel as a timeout fallback.
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Tunnel task did not stop within timeout; cancelling")
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass

        self._tunnel_running = False
        self._tunnel_manager = None
        self._tunnel_task = None
        logger.info("Tunnel stopped")

    # ------------------------------------------------------------------
    # Core server methods
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Thread entry point - runs asyncio event loop.

        Creates a new asyncio event loop for this thread and runs the main
        async function. This is required because Qt owns the main thread's
        event loop.

        Any exception escaping ``_async_main`` — whether from the setup phase
        (building FastMCP, registering tools) or the serve phase (uvicorn) —
        is caught and logged here with a full traceback. Without this guard the
        background daemon thread would die silently (surfacing only via
        ``threading.excepthook``), leaving a connected client to hang until it
        times out. This mirrors how ``_run_tunnel`` already guards the tunnel
        task. We catch ``BaseException`` for the same reason it does: anyio /
        asyncio can surface ``BaseExceptionGroup`` here. The failure is also
        recorded as ``fatal_error`` so the settings dialog can say why.

        Thread Safety:
            Runs in background thread. Never accesses Qt or Anki APIs directly.
        """
        try:
            asyncio.run(self._async_main())
        except BaseException as exc:  # noqa: BLE001 - must not let the thread die silently
            self._fatal_error = f"{type(exc).__name__}: {exc}"
            logger.error(
                "MCP server background thread failed unexpectedly: %s",
                exc,
                exc_info=True,
            )

    async def _call_main_thread(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Bridge tool call to main thread via queue.

        Sends a tool request to the main thread and waits for the response.
        Uses asyncio.to_thread to make the blocking queue operation async-friendly.

        Args:
            tool_name: Name of the tool to execute (e.g., "sync")
            arguments: Tool arguments as a dictionary

        Returns:
            The result from executing the tool on the main thread

        Raises:
            BridgeError: If the main thread returns an error response, with
                the error message from the response.

        Thread Safety:
            Safe to call from background thread (asyncio event loop). Uses
            asyncio.to_thread to safely call blocking queue.Queue methods.

        Example:
            >>> result = await self._call_main_thread("sync", {})
            >>> # Main thread executes sync, returns result via queue
            >>> print(result)  # {"status": "success", ...}
        """
        request = ToolRequest(
            request_id=str(uuid.uuid4()),
            tool_name=tool_name,
            arguments=arguments,
        )

        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(self._executor, self._bridge.send_request, request)

        if not response.success:
            raise BridgeError(response.error or "Unknown bridge error")
        return response.result

    async def _async_main(self) -> None:
        """Main async function for MCP server.

        Sets up the MCP server with FastMCP, defines tool handlers, and starts
        the HTTP transport. The tunnel can be started/stopped dynamically as an
        asyncio task alongside the HTTP server.

        The tool handlers are async functions that bridge to the main thread via
        _call_main_thread(). This keeps the background thread async-friendly while
        ensuring Anki operations happen on the main thread where mw.col is safe.

        Thread Safety:
            Runs in background thread. Never accesses Qt or Anki APIs directly.
        """
        # Capture the loop so stop() (Qt thread) can schedule cross-thread
        # callbacks via call_soon_threadsafe.
        self._loop = asyncio.get_running_loop()

        # Async keepalive event — needed in tunnel-only mode (HTTP disabled)
        # where there's no uvicorn serve() blocking the loop. Must be created
        # on the event loop, not in __init__.
        self._async_shutdown = asyncio.Event()

        # Build the Host/Origin transport-security policy from config.
        # See transport_security_config.build_transport_security.
        security_settings = build_transport_security(self._config)
        # Use http_path if configured, otherwise default to root "/"
        streamable_path = f"/{self._config.http_path.strip('/')}/" if self._config.http_path else "/"
        mcp = build_fastmcp(streamable_path, security_settings)

        # Store the FastMCP instance so the tunnel can access the lowlevel
        # Server via mcp._mcp_server for in-memory transport.
        self._mcp_instance = mcp

        # Register all MCP primitives (apply tool filtering from config)
        register_all_tools(
            mcp, self._call_main_thread,
            disabled_tools=self._config.disabled_tools,
            enabled_destructive_tools=self._config.enabled_destructive_tools,
            enabled_opt_in_tools=self._config.enabled_opt_in_tools,
        )
        register_all_resources(mcp, self._call_main_thread)
        register_all_prompts(mcp)

        # Run HTTP server or wait for async shutdown (tunnel-only mode).
        # The tunnel runs as a separate asyncio task on the same loop, started
        # dynamically via start_tunnel() from the Qt thread.
        if self._config.http_enabled:
            await self._run_http_mode(mcp)
        else:
            # No HTTP server — keep the event loop alive for tunnel-only mode.
            # stop() will signal this event via loop.call_soon_threadsafe().
            await self._async_shutdown.wait()

    async def _run_http_mode(self, mcp: FastMCP) -> None:
        """Run with SDK's built-in HTTP transport.

        Uses FastMCP's streamable_http() which returns a Starlette ASGI app
        configured with the MCP protocol handlers. The app is served via uvicorn.

        Shutdown is driven by stop() flipping ``server.should_exit`` via
        ``call_soon_threadsafe``; uvicorn's serve loop polls that flag and
        unwinds on its next tick, releasing the listening socket so the next
        profile open can rebind the port.

        The listening sockets are bound here (``_bind_http_sockets``) and
        handed to ``serve()``, and closed in this method's ``finally`` on
        every path. uvicorn's own ``shutdown()`` also closes them, but only
        when startup completed; ``socket.close()`` is idempotent.

        HTTP failing to start must not end the loop, because the tunnel runs
        on it too. A bind ``OSError``, or a ``SystemExit``/``Exception``
        escaping ``serve()`` before uvicorn reports ``started`` (uvicorn turns
        startup failures into ``sys.exit``), is recorded as ``http_error``,
        logged, and followed by the same ``_async_shutdown`` wait that keeps
        tunnel-only mode alive. Exceptions after startup still propagate.

        Args:
            mcp: Configured FastMCP server instance with tools defined

        Thread Safety:
            Runs in background thread. Never accesses Qt or Anki APIs directly.
        """
        app = mcp.streamable_http_app()

        # Apply optional shared-API-key auth (AnkiConnect-style) when a key is
        # configured. Applied to the MCP app FIRST so that CORS (below) ends up
        # outermost: CORS handles OPTIONS preflight before auth, and actual
        # requests flow CORS -> Auth -> MCP. Empty key = layer not applied.
        if self._config.http_api_key:
            app = ApiKeyAuthMiddleware(app, self._config.http_api_key)

        # Apply CORS middleware if configured
        if self._config.cors_origins:
            from starlette.middleware.cors import CORSMiddleware

            app = CORSMiddleware(
                app,
                allow_origins=self._config.cors_origins,
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["*"],
                expose_headers=self._config.cors_expose_headers,
                allow_credentials=True,
            )

        # log_config=None is load-bearing: uvicorn's default log_config runs
        # logging.config.dictConfig(), which shuts down EVERY handler in Anki's
        # process (Anki's, other add-ons', our file log) and kills this thread
        # if any of them raises anything but OSError/ValueError on the way
        # (with the default logging.raiseExceptions = True). None skips that;
        # log_level still sets the uvicorn.error/.access/.asgi logger levels.
        config = uvicorn.Config(
            app,
            host=self._config.http_host,
            port=self._config.http_port,
            log_level="warning",
            log_config=None,
        )
        server = uvicorn.Server(config)

        host, port = self._config.http_host, self._config.http_port
        try:
            sockets = _bind_http_sockets(host, port)
        except (OSError, UnicodeError) as exc:
            reason = getattr(exc, "strerror", None) or exc
            self._http_error = f"cannot listen on {host}:{port}: {reason}"
            logger.error("HTTP server failed to start: %s", self._http_error)
            await self._async_shutdown.wait()
            return

        # Publish before serving so stop() can reach it via call_soon_threadsafe.
        self._uvicorn_server = server

        # uvicorn logs the real startup failure on uvicorn.error and then
        # exits with a bare code, so keep its first ERROR for the dialog.
        uvicorn_errors = _FirstErrorCapture()
        uvicorn_error_logger = logging.getLogger("uvicorn.error")
        uvicorn_error_logger.addHandler(uvicorn_errors)
        failure: Optional[BaseException] = None
        try:
            await server.serve(sockets=sockets)
        except (SystemExit, Exception) as exc:
            if server.started:
                raise
            failure = exc
        finally:
            uvicorn_error_logger.removeHandler(uvicorn_errors)
            for sock in sockets:
                sock.close()

        if failure is not None:
            reason = uvicorn_errors.message or f"{type(failure).__name__}: {failure}"
            self._http_error = f"HTTP server failed to start: {reason}"
            logger.error(
                "%s", self._http_error,
                exc_info=failure if isinstance(failure, Exception) else None,
            )
            await self._async_shutdown.wait()
