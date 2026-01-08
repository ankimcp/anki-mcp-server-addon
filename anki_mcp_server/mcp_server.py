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

from .config import Config
from .queue_bridge import QueueBridge, ToolRequest
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
        _shutdown_event: Event to signal server shutdown
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
        from .queue_bridge import ToolRequest

        request = ToolRequest(
            request_id=str(uuid.uuid4()),
            tool_name=tool_name,
            arguments=arguments,
        )

        # Use asyncio.to_thread for blocking queue operation
        # This allows the async event loop to continue while waiting for response
        response = await asyncio.to_thread(self._bridge.send_request, request)

        if not response.success:
            raise Exception(response.error)
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
        # Disable DNS rebinding protection to allow tunnels/proxies (ngrok, Cloudflare, etc.)
        # The addon runs locally and users explicitly configure tunnel access
        security_settings = TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        )
        mcp = FastMCP("anki-mcp", streamable_http_path="/", transport_security=security_settings)

        # Register all MCP primitives
        register_all_tools(mcp, self._call_main_thread)
        register_all_resources(mcp, self._call_main_thread)
        register_all_prompts(mcp)

        # HTTP mode only for now
        await self._run_http_mode(mcp)

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
