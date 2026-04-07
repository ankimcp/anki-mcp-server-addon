"""Thread-safe ring buffer for tunnel events with Qt signal integration.

This module provides a simple event log for the tunnel subsystem. Tunnel code
runs on an asyncio background thread, while the UI lives on the Qt main thread.
The TunnelLog bridges the two: background code writes entries, the Qt UI reacts
to the ``entry_added`` signal which is automatically queued across threads.

Architecture:
    - Background thread (asyncio): calls info(), error(), request(), auth()
    - Qt main thread: connects to entry_added signal, calls get_entries()
    - Thread safety: all shared state protected by threading.Lock

No tunnel-specific dependencies — this is a standalone utility module.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from aqt.qt import QObject, pyqtSignal


@dataclass
class LogEntry:
    """Single tunnel event log entry.

    Attributes:
        timestamp: When the event occurred (local time).
        level: Category of the event — one of "info", "error", "request",
            or "auth".
        message: Human-readable description of the event.
    """

    timestamp: datetime
    level: str  # "info", "error", "request", "auth"
    message: str


def format_entry(entry: LogEntry) -> str:
    """Format a log entry for display in the UI.

    Returns a string like ``"14:32:01  Tunnel connected"``.

    Args:
        entry: The log entry to format.

    Returns:
        Formatted string with HH:MM:SS timestamp and message.
    """
    return f"{entry.timestamp.strftime('%H:%M:%S')}  {entry.message}"


class TunnelLog(QObject):
    """Thread-safe ring buffer for tunnel events.

    Emits a Qt signal when new entries are added, allowing the UI to
    update reactively. The signal uses ``Qt.QueuedConnection`` for
    cross-thread safety (tunnel code runs on asyncio thread, UI runs
    on Qt main thread).

    The buffer is backed by a ``collections.deque`` with a fixed max
    length — oldest entries are automatically evicted when the buffer
    is full.

    Usage:
        >>> log = TunnelLog(max_entries=50)
        >>> log.entry_added.connect(on_new_entry)  # Qt slot
        >>> log.info("Tunnel connected")
        >>> log.request("POST /mcp", 200, 45.2)
        >>> entries = log.get_entries()  # thread-safe snapshot

    Thread Safety:
        All public methods are safe to call from any thread. Internal
        state is protected by a threading.Lock. The ``entry_added``
        signal can be emitted from any thread — Qt automatically queues
        the delivery when the receiver lives on a different thread.
    """

    entry_added = pyqtSignal(object)  # emits LogEntry

    def __init__(self, max_entries: int = 100) -> None:
        """Initialize the tunnel log.

        Args:
            max_entries: Maximum number of entries to retain. When the
                buffer is full, the oldest entry is evicted on each new
                append. Defaults to 100.
        """
        super().__init__()
        self._entries: deque[LogEntry] = deque(maxlen=max_entries)
        self._lock = threading.Lock()

    def _add(self, level: str, message: str) -> None:
        """Create and store a new log entry, then emit the Qt signal.

        This is the shared implementation for all public logging methods.
        The lock is held only for the deque append — the signal emission
        happens outside the lock to avoid potential deadlocks with Qt
        internals.

        Args:
            level: Event category (e.g. "info", "error").
            message: Human-readable event description.
        """
        entry = LogEntry(
            timestamp=datetime.now(),
            level=level,
            message=message,
        )
        with self._lock:
            self._entries.append(entry)
        self.entry_added.emit(entry)

    def info(self, message: str) -> None:
        """Add an info-level entry.

        Use for general status updates like connection state changes.

        Args:
            message: Description of the event.
        """
        self._add("info", message)

    def error(self, message: str) -> None:
        """Add an error-level entry.

        Use for failures that the user should be aware of.

        Args:
            message: Description of the error.
        """
        self._add("error", message)

    def request(self, method_path: str, status: int, duration_ms: float) -> None:
        """Add a request-level entry for an HTTP request through the tunnel.

        Formats the entry as ``"-> POST /mcp (200, 45ms)"``.

        Args:
            method_path: HTTP method and path, e.g. ``"POST /mcp"``.
            status: HTTP response status code.
            duration_ms: Request duration in milliseconds.
        """
        self._add("request", f"\u2192 {method_path} ({status}, {duration_ms:.0f}ms)")

    def auth(self, message: str) -> None:
        """Add an auth-level entry.

        Use for authentication and authorization events (token refresh,
        login, permission changes).

        Args:
            message: Description of the auth event.
        """
        self._add("auth", message)

    def get_entries(self) -> list[LogEntry]:
        """Return a snapshot of all current entries.

        Returns a new list (copy), so the caller can iterate without
        holding the lock.

        Returns:
            List of log entries in chronological order (oldest first).
        """
        with self._lock:
            return list(self._entries)

    def clear(self) -> None:
        """Remove all entries from the buffer."""
        with self._lock:
            self._entries.clear()
