"""Request processor that handles MCP tool requests on Qt main thread.

This module provides the RequestProcessor class which drains the request
queue and executes Anki operations safely on the main thread. Dispatch is
event-driven: the background MCP server thread enqueues a request and
immediately wakes the main thread via ``mw.taskman.run_on_main``, which
schedules a drain callback onto the Qt event loop. There is no polling
timer, so an idle server causes zero main-thread wakeups.

Thread Safety:
    - The drain callback runs exclusively on the Qt main thread (guaranteed
      by ``mw.taskman.run_on_main``)
    - Safe to access mw.col there since the Qt main thread owns it
    - Never blocks - uses non-blocking queue.get_nowait() internally

Architecture:
    - start() registers a waker on the QueueBridge and schedules one initial
      drain (to pick up requests enqueued while stopped)
    - QueueBridge.send_request() puts the request, then fires the waker,
      which schedules _process_pending() onto the main thread
    - Each drain consumes ALL pending requests, so N concurrent wakes
      coalesce safely: the first drain does the work, the rest are cheap
      no-ops on an empty queue
    - stop() clears the waker and flips the _active flag so late callbacks
      (already queued in Qt's event loop) return without touching the queue

Performance:
    - Zero idle churn (no wakeups while no client is talking)
    - Dispatch latency is one Qt event-loop turn (effectively immediate,
      vs. up to a full polling interval with the old timer approach)
    - Batch draining prevents queue buildup under load
"""

from typing import Callable, Optional

from .handler_registry import execute
from .queue_bridge import QueueBridge, ToolRequest, ToolResponse


class RequestProcessor:
    """Processes MCP requests on Qt main thread via event-driven dispatch.

    This class is the bridge between the async MCP server (running in a
    background thread) and Anki's collection API (which must be accessed
    from the Qt main thread). It registers a waker on the QueueBridge:
    whenever the background thread enqueues a request, the waker schedules
    a drain callback onto the main thread via ``mw.taskman.run_on_main``.

    The processor is designed to:
    1. Never block the Qt event loop (keeps UI responsive)
    2. Process all pending requests in each drain (empties the queue)
    3. Safely access mw.col (we're on main thread)
    4. Handle errors gracefully (returns error response instead of crashing)
    5. Cause zero main-thread wakeups while idle (no polling timer)

    Usage:
        >>> bridge = QueueBridge()
        >>> processor = RequestProcessor(bridge)
        >>> processor.start()  # Register waker, drain anything already queued
        >>> # Later, during shutdown:
        >>> processor.stop()

    Attributes:
        _bridge: Thread-safe communication bridge to background thread.
        _schedule_on_main: Injectable "run this closure on the main thread"
            function. None means "resolve mw.taskman.run_on_main lazily" —
            the injection seam exists so unit tests can substitute a
            synchronous fake without a running Anki instance.
        _active: True between start() and stop(). Late drain callbacks
            (delivered by Qt after stop()) check this flag and return
            immediately, closing the shutdown race.

    Note:
        With the default scheduler this class requires a running Anki
        instance (mw must exist by the time the first wake fires), but the
        aqt import is lazy so the module itself imports without Anki.
    """

    def __init__(
        self,
        bridge: QueueBridge,
        schedule_on_main: Optional[Callable[[Callable[[], None]], None]] = None,
    ) -> None:
        """Initialize request processor.

        Args:
            bridge: Queue bridge for thread-safe communication with the
                background MCP server thread.
            schedule_on_main: Function that schedules a zero-argument
                closure onto the Qt main thread. Defaults to
                ``mw.taskman.run_on_main`` (resolved lazily on each call).
                Unit tests inject a synchronous fake here.

        Note:
            No Qt objects are created — the processor only needs a way to
            schedule closures onto the main thread, which taskman provides.
        """
        self._bridge = bridge
        self._schedule_on_main = schedule_on_main
        self._active = False

    def start(self) -> None:
        """Start processing requests.

        Registers the waker on the bridge so every subsequent
        ``send_request()`` schedules a drain onto the main thread, then
        schedules one initial drain to pick up any requests that were
        enqueued while the processor was stopped (their wakes fired into
        the void because no waker was registered).

        Note:
            Idempotent — calling start() when already started is a no-op,
            so the waker is never double-registered and no extra initial
            drain is scheduled.
        """
        if self._active:
            return
        self._active = True
        self._bridge.set_waker(self._wake)
        # Initial drain: requests enqueued while stopped had no waker to
        # fire, so they are sitting in the queue with nobody scheduled to
        # consume them. One explicit drain covers that window.
        self._schedule(self._process_pending)

    def stop(self) -> None:
        """Stop processing requests.

        Clears the waker on the bridge (new puts no longer schedule drains)
        and flips the _active flag so any drain callback already queued in
        Qt's event loop becomes a no-op when it eventually runs. Any
        requests currently in the queue remain there until start() is
        called again (whose initial drain picks them up).

        This should be called during addon shutdown to clean up resources.

        Note:
            Idempotent — calling stop() when already stopped (or never
            started) is a no-op.
        """
        if not self._active:
            return
        self._active = False
        self._bridge.clear_waker()

    def _wake(self) -> None:
        """Waker registered on the bridge.

        Called from the background thread immediately after a request is
        enqueued (see QueueBridge.send_request). Schedules a drain onto the
        Qt main thread — ``run_on_main`` is thread-safe, so this is the
        only cross-thread hop needed.
        """
        self._schedule(self._process_pending)

    def _schedule(self, callback: Callable[[], None]) -> None:
        """Schedule a closure onto the Qt main thread.

        Uses the injected scheduler if one was provided, otherwise resolves
        ``mw.taskman.run_on_main`` lazily. The lazy aqt import keeps this
        module importable in environments without Anki (unit tests inject
        their own scheduler and never hit this path).
        """
        schedule = self._schedule_on_main
        if schedule is None:
            from aqt import mw

            schedule = mw.taskman.run_on_main
        schedule(callback)

    def _process_pending(self) -> None:
        """Process all pending requests from the queue.

        Runs on the Qt main thread, scheduled by the waker (or by start()'s
        initial drain). This method:
        1. Returns immediately if the processor has been stopped
        2. Checks for pending requests (non-blocking)
        3. If found, executes the tool via handler registry
        4. Sends response back via bridge
        5. Repeats until queue is empty

        The drain-until-empty loop is what makes concurrent wakes coalesce:
        if N requests are enqueued before the first drain runs, that drain
        consumes all N, and the remaining N-1 scheduled callbacks find an
        empty queue and exit immediately.

        Thread Safety:
            - Runs on Qt main thread (guaranteed by run_on_main)
            - Safe to access mw.col here
            - Non-blocking queue operations keep UI responsive

        Shutdown Race:
            Qt may deliver an already-queued callback after stop() — the
            _active check at the top makes such late callbacks harmless
            no-ops that never touch the queue.

        Error Handling:
            - Exceptions during tool execution are caught and returned as
              error responses
            - Processing continues even if one request fails
            - Never crashes the Qt event loop or Anki
        """
        if not self._active:
            # Late callback delivered after stop() — teardown may already be
            # underway, so don't touch the queue or execute anything.
            return

        while True:
            # Non-blocking check for pending request
            # Returns None immediately if queue is empty
            request = self._bridge.get_pending_request()
            if request is None:
                break  # Queue empty, exit until the next wake

            # Execute the tool and get response (success or error)
            response = self._execute_tool(request)

            # Send response back to background thread
            # This unblocks the MCP server's send_request() call
            self._bridge.send_response(response)

    def _execute_tool(self, request: ToolRequest) -> ToolResponse:
        """Execute a single tool request and return response.

        This method is where Anki operations actually happen. We're on the
        Qt main thread here, so accessing mw.col is safe.

        Args:
            request: Tool request containing the tool name and arguments.

        Returns:
            ToolResponse with either a successful result or an error message.
            The response always has the same request_id as the input request
            to match up request/response pairs.

        Error Handling:
            - All exceptions are caught and converted to error responses
            - This prevents exceptions from propagating to the Qt event loop
            - Exception message is returned to the MCP client

        Example:
            >>> request = ToolRequest(
            ...     request_id="req-123",
            ...     tool_name="list_decks",
            ...     arguments={}
            ... )
            >>> response = processor._execute_tool(request)
            >>> if response.success:
            ...     print(f"Result: {response.result}")
            ... else:
            ...     print(f"Error: {response.error}")
        """
        try:
            # Dispatch to handler registry which routes to the appropriate handler
            # This will raise if:
            # - tool_name is unknown
            # - arguments are invalid
            # - Anki operation fails
            # - Collection is not loaded
            result = execute(request.tool_name, request.arguments)

            # Success - return result to MCP client
            return ToolResponse(
                request_id=request.request_id,
                success=True,
                result=result,
            )
        except Exception as e:
            # Error - return error message to MCP client
            # str(e) captures the exception message
            # Exception type is lost but message usually includes it
            return ToolResponse(
                request_id=request.request_id,
                success=False,
                error=str(e),
            )
