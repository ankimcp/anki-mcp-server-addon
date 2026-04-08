"""Connection lifecycle orchestration for MCP server and request processor.

This module provides the ConnectionManager class which orchestrates the startup
and shutdown of both the MCP server (background thread) and request processor
(main thread). It ensures proper initialization order and clean shutdown to
prevent deadlocks.

Additionally manages the tunnel lifecycle — creating shared instances of
TunnelLog, CredentialsManager, and DeviceFlowAuth, and exposing tunnel
control methods that the UI layer calls.

Shutdown Order (Critical):
    1. Bridge shutdown - unblocks any waiting requests
    2. MCP server stop - terminates background thread (also stops tunnel)
    3. Request processor stop - stops main thread timer

This order ensures that:
- Background thread's blocking queue operations are unblocked first
- MCP server can cleanly shut down its asyncio event loop
- Request processor timer stops after all async operations complete

Thread Safety:
    - All methods designed to be called from Qt main thread
    - Coordinates between main thread and background thread components
    - Uses QueueBridge for thread-safe shutdown signaling
    - Tunnel state properties use simple attribute reads (GIL-safe)
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from .config import Config
from .credentials import CredentialsManager
from .mcp_server import McpServer
from .queue_bridge import QueueBridge
from .request_processor import RequestProcessor
from .tunnel.auth import DeviceFlowAuth
from .tunnel.log import TunnelLog

logger = logging.getLogger(__name__)


@dataclass
class _TunnelState:
    """Bundled tunnel state for atomic replacement.

    A single attribute swap is GIL-safe, so the UI thread always sees a
    consistent snapshot even though callbacks fire from the background thread.
    """

    url: str | None = None
    expires_at: str | None = None
    user: dict | None = field(default=None)


class ConnectionManager:
    """Manages MCP server, request processor, and tunnel lifecycle.

    This class orchestrates the startup and shutdown of the two-thread architecture:
    - Main thread: RequestProcessor polls queue via QTimer
    - Background thread: McpServer runs asyncio event loop with MCP SDK + tunnel

    It also owns the shared tunnel subsystem instances:
    - TunnelLog: ring buffer for tunnel events, emits Qt signals for UI
    - CredentialsManager: reads/writes OAuth credentials from disk
    - DeviceFlowAuth: handles OAuth device flow (used by login UI)

    The manager ensures components start and stop in the correct order to prevent
    deadlocks and ensure clean shutdown.

    Usage:
        >>> config = Config(http_port=3141)
        >>> manager = ConnectionManager(config)
        >>> manager.start()  # Start both components
        >>> # Tunnel control:
        >>> manager.connect_tunnel()   # Start tunnel (checks credentials)
        >>> manager.disconnect_tunnel() # Stop tunnel
        >>> manager.logout_tunnel()     # Disconnect + delete credentials
        >>> # Later, during shutdown:
        >>> manager.stop()   # Clean shutdown

    Attributes:
        _config: Current configuration (host/port, CORS, tunnel settings, etc.)
        _bridge: Thread-safe queue bridge for cross-thread communication
        _processor: Request processor running on main thread (or None if stopped)
        _server: MCP server running in background thread (or None if stopped)
        _tunnel_log: Shared TunnelLog instance (created once, persists across restarts)
        _credentials_manager: Shared CredentialsManager instance
        _auth: DeviceFlowAuth instance (created from config)
        _tunnel_state: Bundled tunnel URL, expiry, and user info (atomic swap)

    Note:
        This class must be instantiated on the Qt main thread since it creates
        Qt components (RequestProcessor uses QTimer, TunnelLog is a QObject).
    """

    def __init__(self, config: Config) -> None:
        """Initialize connection manager.

        Creates the queue bridge and tunnel subsystem instances, but does not
        start any components. Call start() to begin processing requests.

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

        # Tunnel subsystem — created once, shared with UI.
        # These persist across start()/stop() cycles.
        self._tunnel_log = TunnelLog()
        self._credentials_manager = CredentialsManager()
        self._auth = DeviceFlowAuth(
            server_url=config.tunnel_server_url,
            client_id=config.tunnel_client_id,
        )

        # Tunnel state — bundled into a single object for atomic replacement.
        # Background thread creates a new _TunnelState; UI thread reads the
        # current reference.  Single-attribute swap is GIL-safe.
        self._tunnel_state = _TunnelState()

    # ------------------------------------------------------------------
    # Core lifecycle — HTTP server + request processor
    # ------------------------------------------------------------------

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
            >>> config = Config(http_port=3141)
            >>> manager = ConnectionManager(config)
            >>> manager.start()
            >>> # MCP server now accepting connections on localhost:3141
        """
        if self.is_running:
            return

        # Create a fresh bridge for this lifecycle.
        # A previous stop() marks the old bridge as shut down, so reusing it
        # would reject all new requests.
        self._bridge = QueueBridge()

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
           (also stops the tunnel if running)
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

        # Clear tunnel state — the server stop will handle actual cleanup
        self._tunnel_state = _TunnelState()

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
            >>> manager = ConnectionManager(Config(http_port=3141))
            >>> manager.start()
            >>> # User changes port in settings
            >>> manager.update_config(Config(http_port=5555))
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

    @property
    def http_running(self) -> bool:
        """Whether the HTTP server is running.

        Returns True only when the background thread is running AND HTTP
        is enabled in the config.  When ``http_enabled=False`` the
        background thread still runs (for the tunnel) but uvicorn is not
        serving, so this returns False.

        Thread Safety:
            Safe to read from any thread -- reads simple attributes.
        """
        if not self.is_running:
            return False
        return self._config.http_enabled

    def update_config(self, config: Config) -> None:
        """Update configuration and restart if running.

        Updates the stored configuration and restarts the connection if it's
        currently active. If not running, just updates the config for the next
        start() call.

        Also updates the DeviceFlowAuth instance if tunnel settings changed.

        Args:
            config: New configuration to apply. Should be validated before
                calling this method (use Config.is_valid()).

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
        old_config = self._config
        self._config = config

        # Recreate auth client if tunnel settings changed
        if (config.tunnel_server_url != old_config.tunnel_server_url
                or config.tunnel_client_id != old_config.tunnel_client_id):
            self._auth = DeviceFlowAuth(
                server_url=config.tunnel_server_url,
                client_id=config.tunnel_client_id,
            )

        if was_running:
            self.restart()

    # ------------------------------------------------------------------
    # Tunnel control — called from Qt main thread (UI layer)
    # ------------------------------------------------------------------

    def connect_tunnel(self) -> None:
        """Start the tunnel connection.

        Checks for stored credentials, then tells the MCP server to start
        the tunnel. If credentials are missing, logs an error and returns.

        Thread Safety:
            Must be called from Qt main thread.
        """
        if not self.is_running or self._server is None:
            logger.warning("Cannot connect tunnel: MCP server not running")
            self._tunnel_log.error("Cannot connect: server not running")
            return

        if self.tunnel_connected:
            logger.info("Tunnel already connected, ignoring connect_tunnel()")
            return

        # Check for stored credentials
        credentials = self._credentials_manager.load()
        if credentials is None:
            logger.warning("No credentials found, cannot connect tunnel")
            self._tunnel_log.error("No credentials — please log in first")
            return

        self._tunnel_log.info("Connecting to tunnel...")

        self._server.start_tunnel(
            credentials_manager=self._credentials_manager,
            auth=self._auth,
            on_tunnel_established=self._on_tunnel_established,
            on_disconnected=self._on_tunnel_disconnected,
            on_error=self._on_tunnel_error,
            on_request_completed=self._on_tunnel_request_completed,
            on_reconnecting=self._on_tunnel_reconnecting,
            on_gave_up=self._on_tunnel_gave_up,
        )

    def disconnect_tunnel(self) -> None:
        """Stop the tunnel connection.

        Thread Safety:
            Must be called from Qt main thread.
        """
        if self._server is None:
            return

        self._tunnel_log.info("Disconnecting tunnel...")
        self._server.stop_tunnel()
        self._tunnel_state = _TunnelState()

    def logout_tunnel(self) -> None:
        """Disconnect the tunnel and delete stored credentials.

        Thread Safety:
            Must be called from Qt main thread.
        """
        self.disconnect_tunnel()
        self._credentials_manager.delete()
        self._tunnel_log.auth("Logged out — credentials deleted")
        logger.info("Tunnel credentials deleted")

    # ------------------------------------------------------------------
    # Tunnel state properties — thread-safe reads (GIL)
    # ------------------------------------------------------------------

    @property
    def tunnel_connected(self) -> bool:
        """Whether the tunnel is currently connected.

        Thread Safety:
            Safe to read from any thread.
        """
        if self._server is None:
            return False
        return self._server.tunnel_running

    @property
    def tunnel_active(self) -> bool:
        """Whether the tunnel task is alive (connecting, connected, or reconnecting).

        Thread Safety:
            Safe to read from any thread.
        """
        if self._server is None:
            return False
        return self._server.tunnel_active

    @property
    def tunnel_url(self) -> Optional[str]:
        """The current public tunnel URL, or None if not connected.

        Thread Safety:
            Safe to read from any thread — reads a single object reference.
        """
        return self._tunnel_state.url

    @property
    def tunnel_user(self) -> Optional[dict]:
        """User info (email, tier), or None.

        Returns the cached ``user`` dict when available (set on tunnel
        connect). Falls back to disk only when the cache is empty — e.g.
        credentials saved by CLI before any tunnel connection.

        Thread Safety:
            Safe to read from any thread.
        """
        if self._tunnel_state.user is not None:
            return self._tunnel_state.user
        # Fallback: check disk for credentials saved by CLI
        # (only relevant when no tunnel has connected yet this session)
        credentials = self._credentials_manager.load()
        return credentials.user if credentials else None

    @property
    def tunnel_expires_at(self) -> Optional[str]:
        """ISO 8601 expiry of the current tunnel session, or None.

        Thread Safety:
            Safe to read from any thread — reads a single object reference.
        """
        return self._tunnel_state.expires_at

    @property
    def tunnel_log(self) -> TunnelLog:
        """The shared TunnelLog instance for UI binding.

        The TunnelLog is a QObject that emits ``entry_added`` signals when
        new log entries arrive. The UI connects to this signal to update
        the tunnel status display reactively.

        Thread Safety:
            The TunnelLog itself is thread-safe. The returned reference is
            stable (created once in __init__).
        """
        return self._tunnel_log

    @property
    def credentials_manager(self) -> CredentialsManager:
        """The shared CredentialsManager instance.

        Exposed so the login UI can save credentials after device flow
        authentication completes.
        """
        return self._credentials_manager

    @property
    def auth(self) -> DeviceFlowAuth:
        """The shared DeviceFlowAuth instance.

        Exposed so the login UI can drive the device flow (request code,
        poll for token).
        """
        return self._auth

    # ------------------------------------------------------------------
    # Tunnel callbacks — fired from the background asyncio thread
    # ------------------------------------------------------------------

    def _on_tunnel_established(self, url: str, expires_at: str | None) -> None:
        """Called when the tunnel is ready and has a public URL."""
        creds = self._credentials_manager.load()
        self._tunnel_state = _TunnelState(
            url=url,
            expires_at=expires_at,
            user=creds.user if creds else None,
        )
        self._tunnel_log.info(f"Tunnel connected: {url}")
        logger.info("Tunnel established: %s (expires: %s)", url, expires_at or "never")

    def _on_tunnel_disconnected(self, code: int, reason: str) -> None:
        """Called when a single tunnel connection ends (may reconnect)."""
        self._tunnel_log.info(f"Disconnected (code {code}): {reason}")
        logger.info("Tunnel disconnected: code=%d, reason=%s", code, reason)

    def _on_tunnel_error(self, error_code: str, message: str) -> None:
        """Called when the server sends an error message."""
        self._tunnel_log.error(f"Server error [{error_code}]: {message}")
        logger.warning("Tunnel server error: code=%s, message=%s", error_code, message)

    def _on_tunnel_request_completed(
        self, method_path: str, status_code: int, duration_ms: float
    ) -> None:
        """Called after each HTTP request proxied through the tunnel."""
        self._tunnel_log.request(method_path, status_code, duration_ms)

    def _on_tunnel_reconnecting(self, attempt: int, delay: float) -> None:
        """Called before each reconnection delay."""
        self._tunnel_log.info(
            f"Reconnecting (attempt {attempt}, delay {delay:.1f}s)..."
        )
        logger.info("Tunnel reconnecting: attempt=%d, delay=%.1fs", attempt, delay)

    def _on_tunnel_gave_up(self, code: int, reason: str) -> None:
        """Called when reconnection is permanently abandoned."""
        self._tunnel_state = _TunnelState()
        self._tunnel_log.error(f"Gave up reconnecting: {reason}")
        logger.warning("Tunnel gave up: code=%d, reason=%s", code, reason)
