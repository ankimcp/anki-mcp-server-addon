"""MCP server running in background thread with HTTP transport.

This module implements the MCP server component that runs in a separate background
thread with its own asyncio event loop. It uses the official MCP SDK (FastMCP) to
handle the protocol and exposes Anki operations as MCP tools.

Architecture:
    - Background thread: Runs asyncio event loop with MCP server
    - HTTP transport: Uses FastMCP's built-in streamable HTTP (starlette + uvicorn)
    - Queue bridge: Tool handlers bridge calls to main thread via QueueBridge
    - Async I/O: All tool handlers use asyncio.to_thread to bridge blocking queue ops

Thread Safety:
    - This module runs entirely in a background thread
    - Never accesses mw.col directly - all Anki operations go through QueueBridge
    - Uses asyncio.to_thread to safely call blocking queue.Queue operations
"""

import asyncio
import threading
import uuid
from typing import Any, Optional

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import Icon

from .config import Config
from .queue_bridge import BridgeError, QueueBridge, ToolRequest
from .primitives import register_all_tools, register_all_resources, register_all_prompts


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

    Attributes:
        _bridge: Queue bridge for thread-safe communication with main thread
        _config: Server configuration (HTTP host/port, mode, etc.)
        _thread: Background thread running the asyncio event loop
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
        if loop is not None and server is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(lambda: setattr(server, "should_exit", True))
            except RuntimeError:
                # Loop already stopped/closing — nothing to signal.
                pass

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None
        self._loop = None
        self._uvicorn_server = None

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

        # Use asyncio.to_thread for blocking queue operation
        # This allows the async event loop to continue while waiting for response
        response = await asyncio.to_thread(self._bridge.send_request, request)

        if not response.success:
            raise BridgeError(response.error or "Unknown bridge error")
        return response.result

    async def _async_main(self) -> None:
        """Main async function for MCP server.

        Sets up the MCP server with FastMCP, defines tool handlers, and starts
        the appropriate transport (HTTP only for now).

        The tool handlers are async functions that bridge to the main thread via
        _call_main_thread(). This keeps the background thread async-friendly while
        ensuring Anki operations happen on the main thread where mw.col is safe.

        Thread Safety:
            Runs in background thread. Never accesses Qt or Anki APIs directly.
        """
        # Capture the loop so stop() (Qt thread) can schedule cross-thread
        # callbacks via call_soon_threadsafe.
        self._loop = asyncio.get_running_loop()

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

        # Register all MCP primitives (apply tool filtering from config)
        register_all_tools(
            mcp, self._call_main_thread,
            disabled_tools=self._config.disabled_tools,
        )
        register_all_resources(mcp, self._call_main_thread)
        register_all_prompts(mcp)

        # HTTP mode only for now
        await self._run_http_mode(mcp)

    async def _run_http_mode(self, mcp: FastMCP) -> None:
        """Run with SDK's built-in HTTP transport.

        Uses FastMCP's streamable_http() which returns a Starlette ASGI app
        configured with the MCP protocol handlers. The app is served via uvicorn.

        Shutdown is driven by stop() flipping ``server.should_exit`` via
        ``call_soon_threadsafe``; uvicorn's serve loop polls that flag and
        unwinds on its next tick, releasing the listening socket so the next
        profile open can rebind the port.

        Args:
            mcp: Configured FastMCP server instance with tools defined

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
        # Publish before serving so stop() can reach it via call_soon_threadsafe.
        self._uvicorn_server = server

        await server.serve()
