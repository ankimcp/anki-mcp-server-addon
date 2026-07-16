"""Thread-safe communication bridge between MCP server and Anki main thread.

This module provides the core threading infrastructure for the AnkiMCP Server addon.
It enables safe communication between the background asyncio thread (running the
MCP server) and the Qt main thread (where Anki collection access is safe).

Architecture:
    - Background thread: MCP server handles protocol, puts requests in queue,
      fires the registered waker (event-driven wake-up of the main thread),
      then blocks waiting for responses
    - Main thread: a drain callback (scheduled by the waker via
      mw.taskman.run_on_main) pulls requests off the queue, executes Anki
      operations, and sends responses back. No polling — the main thread is
      only woken when there is work to do.

Thread Safety:
    - Uses Python's built-in `queue.Queue` which is thread-safe by design
    - Per-request response queues via _pending dict (protected by _pending_lock)
    - Main thread never blocks - uses get_nowait() in the drain callback

Response Routing:
    Each call to send_request() creates a private one-shot queue keyed by
    request_id. The main thread's send_response() looks up this queue and
    delivers the response to the correct waiting thread. This supports
    multiple concurrent MCP sessions without cross-talk.
"""

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class BridgeError(Exception):
    """Failure surfaced by the queue bridge.

    Raised when a ``ToolResponse`` comes back with ``success=False`` (handler
    raised on the main thread, or shutdown unblocked a pending request), or
    when ``send_request`` is called after shutdown. Distinct from
    ``HandlerError`` — by the time a response crosses the bridge the original
    exception type is gone and only the error string survives, so callers
    can't recover the structured handler info anyway. Use this when you need
    to distinguish bridge/transport failures from genuine bugs in the
    background thread.
    """


@dataclass
class ToolRequest:
    """Request from MCP server to execute an Anki operation.

    Created by the background thread (MCP server) when a tool call is received
    from an AI client. Contains all information needed to execute the operation
    on the main thread.

    Attributes:
        request_id: Unique identifier to match requests with responses. Typically
            a UUID string.
        tool_name: Name of the tool to execute (e.g., "list_decks", "create_note").
            Must match a registered handler in the handler registry.
        arguments: Tool-specific arguments as a dictionary. These will be passed
            to the corresponding handler function via the handler registry.

    Example:
        >>> request = ToolRequest(
        ...     request_id="123e4567-e89b-12d3-a456-426614174000",
        ...     tool_name="search_notes",
        ...     arguments={"query": "deck:Default", "limit": 10}
        ... )
    """

    request_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass
class ToolResponse:
    """Response from main thread after executing an Anki operation.

    Created by the main thread (RequestProcessor) after executing a tool request.
    Contains either a successful result or an error message.

    Attributes:
        request_id: Matches the request_id from the corresponding ToolRequest.
        success: True if the operation completed successfully, False if an error
            occurred.
        result: The return value from the tool execution. Can be any JSON-serializable
            type (dict, list, str, int, etc.). Only set when success=True.
        error: Human-readable error message. Only set when success=False. Typically
            includes the exception type and message.

    Example (success):
        >>> response = ToolResponse(
        ...     request_id="123e4567-e89b-12d3-a456-426614174000",
        ...     success=True,
        ...     result={"decks": [{"id": 1, "name": "Default"}]}
        ... )

    Example (error):
        >>> response = ToolResponse(
        ...     request_id="123e4567-e89b-12d3-a456-426614174000",
        ...     success=False,
        ...     error="ValueError: Invalid deck name"
        ... )
    """

    request_id: str
    success: bool
    result: Any = None
    error: Optional[str] = None


class QueueBridge:
    """Thread-safe bridge between MCP server and Anki main thread.

    This class provides the core infrastructure for safe cross-thread communication
    in the AnkiMCP addon. It uses Python's built-in Queue which is thread-safe and
    designed for producer-consumer patterns.

    Usage Pattern:
        1. Background thread (MCP server):
           - Creates ToolRequest
           - Calls send_request() - puts the request, fires the waker (which
             schedules a main-thread drain), then BLOCKS until the response
             is ready
           - Returns result to MCP client

        2. Main thread (drain callback, scheduled by the waker):
           - Calls get_pending_request() - non-blocking, loops until empty
           - If request found, executes on Anki collection
           - Calls send_response() - unblocks waiting background thread

    Thread Safety:
        - request_queue: Background thread writes (put), main thread reads (get_nowait)
        - Per-request response queues via _pending dict (protected by _pending_lock)
        - Both queue.Queue and threading.Lock are thread-safe by design

    Response Routing:
        Each call to send_request() creates a private one-shot queue keyed by
        request_id. The main thread's send_response() looks up this queue and
        delivers the response to the correct waiting thread. This supports
        multiple concurrent MCP sessions without cross-talk.

    Waker (event-driven dispatch):
        The consumer (RequestProcessor) registers a waker via set_waker().
        send_request() fires the waker right after enqueueing, waking the
        main thread exactly when there is work — no polling. The bridge
        stays Qt-free: the waker is an opaque zero-argument callable, and
        any Qt scheduling (mw.taskman.run_on_main) lives in the caller.
        With no waker registered, puts still succeed but nothing drains
        the queue until a consumer registers and performs a drain.

    Shutdown Handling:
        - Setting _shutdown=True prevents new requests
        - Calling shutdown() sends error responses to ALL pending per-request queues
        - This ensures graceful addon shutdown without deadlocks

    Attributes:
        request_queue: Requests from background thread to main thread.

    Example:
        >>> bridge = QueueBridge()
        >>>
        >>> # In background thread (MCP server):
        >>> request = ToolRequest(
        ...     request_id="abc-123",
        ...     tool_name="list_decks",
        ...     arguments={}
        ... )
        >>> response = bridge.send_request(request)  # Wakes main thread, blocks for response
        >>>
        >>> # In main thread (drain callback scheduled by the waker):
        >>> request = bridge.get_pending_request()  # Non-blocking
        >>> if request:
        ...     result = execute_on_anki(request)
        ...     response = ToolResponse(
        ...         request_id=request.request_id,
        ...         success=True,
        ...         result=result
        ...     )
        ...     bridge.send_response(response)  # Unblocks correct background thread
    """

    def __init__(self) -> None:
        """Initialize the queue bridge.

        Creates:
        - request_queue: shared FIFO for incoming tool requests
        - _pending: dict mapping request_id -> per-request response queue
        - _pending_lock: protects _pending and _shutdown for thread safety
        - _waker: optional callable fired after each enqueue (see set_waker)
        """
        self.request_queue: queue.Queue[ToolRequest] = queue.Queue()
        self._pending: dict[str, queue.Queue[ToolResponse]] = {}
        self._pending_lock = threading.Lock()
        self._shutdown = False
        self._waker: Optional[Callable[[], None]] = None

    def set_waker(self, waker: Callable[[], None]) -> None:
        """Register the waker fired after each request is enqueued.

        The waker is how event-driven dispatch stays Qt-free at this layer:
        the consumer (RequestProcessor) passes a callable that schedules a
        drain onto the Qt main thread, and this module never imports aqt.

        Args:
            waker: Zero-argument callable invoked from send_request() right
                after the request is put on the queue. Called from the
                background thread — it must be thread-safe (run_on_main is).

        Thread Safety:
            Assigning a single attribute is atomic under the GIL. Typically
            called from the Qt main thread during processor start().
        """
        self._waker = waker

    def clear_waker(self) -> None:
        """Deregister the waker.

        After this, send_request() still enqueues requests but no longer
        wakes anyone — same semantics as a stopped consumer. Called during
        processor stop(); requests enqueued afterwards stay queued until a
        consumer registers again and performs a drain.

        Thread Safety:
            Assigning a single attribute is atomic under the GIL.
        """
        self._waker = None

    def send_request(self, request: ToolRequest) -> ToolResponse:
        """Send request to main thread and wait for response.

        Called from background thread (MCP server) when a tool call is received.
        After enqueueing, the registered waker (if any) is fired so the main
        thread schedules a drain immediately — this is the event-driven
        replacement for the old polling timer. This method then BLOCKS until
        the main thread processes the request and sends a response back. This
        is safe because it's not the Qt main thread that blocks.

        Each call creates a private one-shot queue keyed by request_id. This
        ensures that with multiple concurrent sessions, each thread receives
        only its own response.

        Args:
            request: The tool request to execute on the main thread.

        Returns:
            The response from the main thread after executing the tool.

        Raises:
            BridgeError: If the bridge is shutting down and new requests are
                not accepted. This prevents deadlocks during addon shutdown.
            queue.Empty: If no response is received within 30 seconds. This
                timeout prevents indefinite blocking if the main thread crashes
                or becomes unresponsive.

        Thread Safety:
            Safe to call from any thread. Typically called from multiple
            background threads (one per MCP session) via asyncio.to_thread.
        """
        response_q: queue.Queue[ToolResponse] = queue.Queue()

        with self._pending_lock:
            if self._shutdown:
                raise BridgeError("Bridge is shutting down")
            self._pending[request.request_id] = response_q

        self.request_queue.put(request)

        # Wake the main-thread consumer (event-driven dispatch). Ordering is
        # critical: put FIRST, then wake, then block — so the drain scheduled
        # by the wake is guaranteed to see the request. Snapshot the attribute
        # so a concurrent clear_waker() can't turn the None-check into a race.
        waker = self._waker
        if waker is not None:
            try:
                waker()
            except Exception:
                # A broken waker must never break request delivery. The
                # request is already queued; a subsequent wake (or the
                # processor's initial drain on restart) will pick it up,
                # and the 30s timeout below bounds the worst case. Logged
                # (not printed) so the diagnostic file log captures it —
                # the symptom is "requests hang 30s", prime diagnostics.
                logger.warning(
                    "Waker raised — request %s stays queued",
                    request.request_id,
                    exc_info=True,
                )

        try:
            # Block until main thread responds (with timeout to prevent indefinite hang)
            # 30 second timeout is generous - typical operations complete in milliseconds
            return response_q.get(timeout=30)
        finally:
            with self._pending_lock:
                self._pending.pop(request.request_id, None)

    def get_pending_request(self) -> Optional[ToolRequest]:
        """Non-blocking check for pending requests.

        Called from main thread (the drain callback scheduled by the waker)
        to check if there are any pending tool requests. This method NEVER
        blocks - it returns immediately with either a request or None.

        Returns:
            The next pending request if one exists, otherwise None.

        Thread Safety:
            Safe to call from any thread, but designed to be called from the
            Qt main thread in the drain callback.

        Performance:
            Called in a drain-until-empty loop each time the waker schedules
            a drain — never on a timer, so an idle server costs nothing. The
            non-blocking behavior ensures the Qt event loop is never blocked,
            keeping the UI responsive.
        """
        try:
            return self.request_queue.get_nowait()
        except queue.Empty:
            return None

    def send_response(self, response: ToolResponse) -> None:
        """Send response back to the correct waiting MCP handler.

        Called from main thread (RequestProcessor) after executing a tool request.
        Looks up the per-request queue by response.request_id and delivers the
        response to the thread that is waiting for it.

        If no pending queue is found (request already timed out or shutdown
        race), the response is silently dropped with a log message.

        Args:
            response: The response containing the result or error from executing
                the tool.

        Thread Safety:
            Safe to call from any thread, but designed to be called from the
            Qt main thread after executing Anki operations. Uses put_nowait()
            to guarantee it never blocks the main thread.
        """
        with self._pending_lock:
            response_q = self._pending.get(response.request_id)

        if response_q is None:
            # Request already cleaned up: timeout fired, or shutdown sent an
            # error response before the main thread finished processing.
            # Note: this also fires if the recipient timed out but we found
            # the queue before the finally block popped it — in that case
            # put_nowait succeeds but nobody reads it (harmless, GC'd).
            print(
                f"AnkiMCP Server: Response for unknown request_id "
                f"{response.request_id!r} (likely timed out or shutdown)"
            )
            return

        response_q.put_nowait(response)

    def shutdown(self) -> None:
        """Unblock all waiting requests on shutdown.

        Called when the addon is shutting down. This prevents deadlocks by:
        1. Setting the _shutdown flag to reject new requests
        2. Sending error responses to ALL per-request queues to unblock
           any threads waiting in send_request()

        After calling this method, any threads blocked in send_request() will
        receive an error response, and new calls to send_request() will raise
        an exception.

        Thread Safety:
            Safe to call from any thread, typically called from the main thread
            during addon shutdown. The lock ensures atomicity between setting
            _shutdown and iterating _pending.
        """
        with self._pending_lock:
            self._shutdown = True
            for request_id, response_q in self._pending.items():
                response_q.put_nowait(
                    ToolResponse(
                        request_id=request_id,
                        success=False,
                        error="Server shutting down",
                    )
                )
            # Don't clear _pending here. The finally blocks in send_request()
            # will pop their own entries.
