"""Connection lifecycle orchestration for MCP server and request processor.

This module provides the ConnectionManager class which orchestrates the startup
and shutdown of both the MCP server (background thread) and request processor
(main thread). It ensures proper initialization order and clean shutdown to
prevent deadlocks.

Shutdown Order (Critical):
    1. Bridge shutdown - unblocks any waiting requests
    2. MCP server stop - terminates background thread
    3. Request processor stop - stops main thread timer

This order ensures that:
- Background thread's blocking queue operations are unblocked first
- MCP server can cleanly shut down its asyncio event loop
- Request processor timer stops after all async operations complete

Thread Safety:
    - All methods designed to be called from Qt main thread
    - Coordinates between main thread and background thread components
    - Uses QueueBridge for thread-safe shutdown signaling
"""

from typing import Optional

from .config import Config
from .mcp_server import McpServer
from .queue_bridge import QueueBridge
from .request_processor import RequestProcessor


class ConnectionManager:
    """Manages MCP server and request processor lifecycle.

    This class orchestrates the startup and shutdown of the two-thread architecture:
    - Main thread: RequestProcessor polls queue via QTimer
    - Background thread: McpServer runs asyncio event loop with MCP SDK

    The manager ensures components start and stop in the correct order to prevent
    deadlocks and ensure clean shutdown.

    Usage:
        >>> config = Config(mode="http", http_port=3141)
        >>> manager = ConnectionManager(config)
        >>> manager.start()  # Start both components
        >>> # Later, during shutdown:
        >>> manager.stop()   # Clean shutdown

    Attributes:
        _config: Current configuration (mode, host/port, token, etc.)
        _bridge: Thread-safe queue bridge for cross-thread communication
        _processor: Request processor running on main thread (or None if stopped)
        _server: MCP server running in background thread (or None if stopped)

    Note:
        This class must be instantiated on the Qt main thread since it creates
        Qt components (RequestProcessor uses QTimer).
    """

    def __init__(self, config: Config) -> None:
        """Initialize connection manager.

        Creates the queue bridge, but does not start any components.
        Call start() to begin processing requests.

        Args:
            config: Configuration specifying HTTP host/port and other settings.

        Note:
            Components are not created until start() is called. This allows
            updating the config before starting the connection.
        """
        self._config = config
        self._bridge = QueueBridge()
        self._processor: Optional[RequestProcessor] = None
        self._server: Optional[McpServer] = None

    def start(self) -> None:
        """Start MCP server (background) and request processor (main thread).

        Starts both components in the correct order:
        1. Request processor on main thread (QTimer starts polling)
        2. MCP server in background thread (asyncio event loop starts)

        The request processor must start first so it's ready to handle requests
        when the MCP server begins accepting connections.

        Thread Safety:
            Must be called from Qt main thread. The request processor uses
            QTimer which requires the Qt event loop.

        Idempotency:
            If already running, this method is a no-op. Use restart() to
            apply configuration changes.

        Example:
            >>> config = Config(mode="http", http_port=3141)
            >>> manager = ConnectionManager(config)
            >>> manager.start()
            >>> # MCP server now accepting connections on localhost:3141
        """
        if self.is_running:
            return

        # Start request processor on main thread
        # This begins polling the request_queue every 25ms
        self._processor = RequestProcessor(self._bridge)
        self._processor.start(interval_ms=25)

        # Start MCP server in background thread
        # This creates a daemon thread running asyncio event loop
        self._server = McpServer(self._bridge, self._config)
        self._server.start()

    def stop(self) -> None:
        """Stop both components gracefully.

        Stops components in the correct order to prevent deadlocks:
        1. Bridge shutdown - signals shutdown and unblocks waiting requests
        2. MCP server stop - terminates background thread gracefully
        3. Request processor stop - stops main thread timer

        This order is critical:
        - Bridge shutdown must happen FIRST to unblock any threads waiting
          in send_request()
        - MCP server must stop BEFORE processor to ensure no new requests
          arrive after processor stops
        - Processor stops LAST after all async operations complete

        Thread Safety:
            Must be called from Qt main thread. Coordinates shutdown between
            main thread and background thread components.

        Idempotency:
            Safe to call multiple times. If already stopped, this is a no-op.

        Example:
            >>> manager.stop()
            >>> # All components cleanly shut down, no pending requests
        """
        if not self.is_running:
            return

        # 1. Signal bridge shutdown (unblocks waiting requests)
        # This prevents deadlocks by ensuring no threads are stuck waiting
        # for responses that will never arrive
        self._bridge.shutdown()

        # 2. Stop MCP server (background thread)
        # This signals the asyncio event loop to shut down and waits up to
        # 5 seconds for the thread to terminate
        if self._server:
            self._server.stop()
            self._server = None

        # 3. Stop request processor (main thread timer)
        # This stops the QTimer so it no longer polls the request queue
        if self._processor:
            self._processor.stop()
            self._processor = None

    def restart(self) -> None:
        """Restart with potentially new config.

        Performs a clean stop followed by a start. This is used when
        configuration changes (e.g., user changes port or switches modes).

        Thread Safety:
            Must be called from Qt main thread.

        Example:
            >>> manager = ConnectionManager(Config(mode="http", http_port=3141))
            >>> manager.start()
            >>> # User changes port in settings
            >>> manager.update_config(Config(mode="http", http_port=5555))
            >>> # Connection automatically restarted with new port
        """
        self.stop()
        self.start()

    @property
    def is_running(self) -> bool:
        """Check if connection is currently active.

        Returns:
            True if both the MCP server and request processor are running,
            False otherwise.

        Note:
            A connection is considered running only if BOTH components are
            active. If either is None, the connection is stopped.

        Example:
            >>> manager = ConnectionManager(config)
            >>> manager.is_running
            False
            >>> manager.start()
            >>> manager.is_running
            True
            >>> manager.stop()
            >>> manager.is_running
            False
        """
        return self._server is not None and self._processor is not None

    def update_config(self, config: Config) -> None:
        """Update configuration and restart if running.

        Updates the stored configuration and restarts the connection if it's
        currently active. If not running, just updates the config for the next
        start() call.

        Args:
            config: New configuration to apply. Should be validated before
                calling this method (use Config.is_valid_for_mode()).

        Thread Safety:
            Must be called from Qt main thread.

        Example:
            >>> manager = ConnectionManager(Config())
            >>> manager.start()
            >>> # User changes port
            >>> new_config = Config(http_port=8080)
            >>> manager.update_config(new_config)
            >>> # Connection restarted with new port
        """
        was_running = self.is_running
        self._config = config

        if was_running:
            self.restart()
