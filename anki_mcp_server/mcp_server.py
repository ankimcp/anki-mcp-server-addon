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
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import Icon

from .config import Config
from .queue_bridge import QueueBridge, ToolRequest
from .primitives import register_all_tools, register_all_resources, register_all_prompts

logger = logging.getLogger(__name__)


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
        _shutdown_event: Event to signal server shutdown
        _loop: Reference to the background asyncio event loop (set once running)
        _tunnel_task: The asyncio task running TunnelReconnectManager.run()
        _tunnel_manager: The active TunnelReconnectManager instance
        _tunnel_running: Thread-safe flag indicating tunnel status
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
        self._shutdown_event = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mcp-bridge")

        # Asyncio loop reference — set in _async_main(), used by
        # start_tunnel()/stop_tunnel() for cross-thread scheduling.
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # FastMCP instance — set in _async_main(), used by tunnel to get
        # the lowlevel Server for in-memory transport.
        self._mcp_instance: Optional[FastMCP] = None

        # Async shutdown event — created on the event loop in _async_main(),
        # used to keep the loop alive when HTTP is disabled (tunnel-only mode).
        # Cannot use the threading.Event _shutdown_event because it can't be
        # awaited in asyncio.
        self._async_shutdown: Optional[asyncio.Event] = None

        # Tunnel state — all access is thread-safe via GIL for simple
        # attribute reads/writes, plus asyncio.run_coroutine_threadsafe()
        # for operations that touch the event loop.
        self._tunnel_task: Optional[asyncio.Task] = None
        self._tunnel_manager: Optional[Any] = None  # TunnelReconnectManager
        self._tunnel_running: bool = False

    def start(self) -> None:
        """Start MCP server in background thread.

        Creates and starts a daemon thread that runs the asyncio event loop.
        The daemon flag ensures the thread won't prevent Anki from closing.

        Thread Safety:
            Safe to call from main thread (Qt event loop).
        """
        self._shutdown_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal shutdown.

        Sets the shutdown event. The daemon thread will be terminated
        automatically when Anki's process exits.

        Note:
            We don't wait for the thread - uvicorn doesn't respond to
            shutdown events, so waiting would just add unnecessary delay.
            The daemon=True flag ensures clean process exit.

        Thread Safety:
            Safe to call from main thread (Qt event loop).
        """
        self._shutdown_event.set()
        # Signal the async shutdown event so _async_main() can exit
        # when running in tunnel-only mode (no uvicorn to block on).
        if self._loop and not self._loop.is_closed() and self._async_shutdown is not None:
            self._loop.call_soon_threadsafe(self._async_shutdown.set)
        # Stop the tunnel if running — best effort, don't wait
        if self._tunnel_running:
            self.stop_tunnel()
        self._executor.shutdown(wait=False)

    # ------------------------------------------------------------------
    # Tunnel control — called from Qt main thread
    # ------------------------------------------------------------------

    def start_tunnel(
        self,
        credentials_manager: Any,
        auth: Any,
        on_tunnel_established: Callable[[str, str | None, dict | None], None] | None = None,
        on_disconnected: Callable[[int, str], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
        on_request_completed: Callable[[str, int, float], None] | None = None,
        on_reconnecting: Callable[[int, float], None] | None = None,
        on_gave_up: Callable[[int, str], None] | None = None,
    ) -> None:
        """Start the tunnel alongside the HTTP server.

        Called from the Qt main thread. Schedules tunnel startup on the
        background asyncio loop via asyncio.run_coroutine_threadsafe().

        Args:
            credentials_manager: CredentialsManager instance for token I/O.
            auth: DeviceFlowAuth instance for token refresh.
            on_tunnel_established: Called when tunnel is ready (url, expires_at, user).
            on_disconnected: Called when a connection ends (code, reason).
            on_error: Called on server error (error_code, message).
            on_request_completed: Called after each proxied request
                (method_path, status_code, duration_ms).
            on_reconnecting: Called before each reconnection delay
                (attempt, delay_seconds).
            on_gave_up: Called when reconnection is permanently abandoned
                (close_code, reason).

        Thread Safety:
            Safe to call from any thread. The actual work runs on the
            background asyncio loop.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            logger.warning("Cannot start tunnel: asyncio loop not running")
            return

        asyncio.run_coroutine_threadsafe(
            self._start_tunnel_async(
                credentials_manager=credentials_manager,
                auth=auth,
                on_tunnel_established=on_tunnel_established,
                on_disconnected=on_disconnected,
                on_error=on_error,
                on_request_completed=on_request_completed,
                on_reconnecting=on_reconnecting,
                on_gave_up=on_gave_up,
            ),
            loop,
        )

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
    # Tunnel async internals — run on the background asyncio loop
    # ------------------------------------------------------------------

    async def _start_tunnel_async(
        self,
        credentials_manager: Any,
        auth: Any,
        on_tunnel_established: Callable[[str, str | None, dict | None], None] | None = None,
        on_disconnected: Callable[[int, str], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
        on_request_completed: Callable[[str, int, float], None] | None = None,
        on_reconnecting: Callable[[int, float], None] | None = None,
        on_gave_up: Callable[[int, str], None] | None = None,
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

        def _on_established_wrapper(url: str, expires_at: str | None, user: dict | None = None) -> None:
            self._tunnel_running = True
            if original_on_established is not None:
                original_on_established(url, expires_at, user)

        # Wrap on_disconnected to update _tunnel_running
        original_on_disconnected = on_disconnected

        def _on_disconnected_wrapper(code: int, reason: str) -> None:
            # Don't clear _tunnel_running here — the reconnect manager may
            # reconnect. Only gave_up and explicit stop clear it.
            if original_on_disconnected is not None:
                original_on_disconnected(code, reason)

        # Wrap on_gave_up to clear _tunnel_running
        original_on_gave_up = on_gave_up

        def _on_gave_up_wrapper(code: int, reason: str) -> None:
            self._tunnel_running = False
            self._tunnel_manager = None
            if original_on_gave_up is not None:
                original_on_gave_up(code, reason)

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
            on_gave_up=_on_gave_up_wrapper,
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

        if task is not None and not task.done():
            task.cancel()
            try:
                await task
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

        Thread Safety:
            Runs in background thread. Never accesses Qt or Anki APIs directly.
        """
        asyncio.run(self._async_main())

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
            Exception: If the main thread returns an error response, with the
                error message from the response

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
            raise Exception(response.error)
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
        # Capture the running loop so Qt thread can schedule tunnel tasks
        self._loop = asyncio.get_running_loop()

        # Create async shutdown event on the event loop — used to keep the
        # loop alive in tunnel-only mode (when HTTP is disabled).
        self._async_shutdown = asyncio.Event()

        # Disable DNS rebinding protection to allow tunnels/proxies (ngrok, Cloudflare, etc.)
        # The addon runs locally and users explicitly configure tunnel access
        security_settings = TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        )
        # Use http_path if configured, otherwise default to root "/"
        streamable_path = f"/{self._config.http_path.strip('/')}/" if self._config.http_path else "/"
        mcp = FastMCP(
            "anki-mcp",
            website_url="https://ankimcp.ai",
            icons=[Icon(
                src="https://ankimcp.ai/favicon.svg",
                mimeType="image/svg+xml",
                sizes=["any"],
            )],
            streamable_http_path=streamable_path,
            transport_security=security_settings,
        )

        # Store the FastMCP instance so the tunnel can access the lowlevel
        # Server via mcp._mcp_server for in-memory transport.
        self._mcp_instance = mcp

        # Register all MCP primitives (apply tool filtering from config)
        register_all_tools(
            mcp, self._call_main_thread,
            disabled_tools=self._config.disabled_tools,
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

        Args:
            mcp: Configured FastMCP server instance with tools defined

        Note:
            Shutdown handling is best-effort for v1. Uvicorn's serve() blocks
            and we use a daemon thread, so the server will be forcibly terminated
            when Anki closes. This is acceptable for v1 since:
            - Daemon thread won't block Anki shutdown
            - MCP is stateless - no data loss from abrupt termination
            - Future versions can implement proper shutdown via server.shutdown()

        Thread Safety:
            Runs in background thread. Never accesses Qt or Anki APIs directly.
        """
        app = mcp.streamable_http_app()

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

        config = uvicorn.Config(
            app,
            host=self._config.http_host,
            port=self._config.http_port,
            log_level="warning",
        )
        server = uvicorn.Server(config)

        # Note: server.serve() blocks until shutdown
        # For v1, daemon=True on thread handles cleanup
        # TODO(future): Implement graceful shutdown via server.shutdown()
        await server.serve()
