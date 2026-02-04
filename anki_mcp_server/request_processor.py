"""Request processor that handles MCP tool requests on Qt main thread.

This module provides the RequestProcessor class which polls the request queue
using a QTimer and executes Anki operations safely on the main thread.

Thread Safety:
    - Runs exclusively on Qt main thread (via QTimer callback)
    - Safe to access mw.col here since Qt main thread owns it
    - Never blocks - uses non-blocking queue.get_nowait() internally

Architecture:
    - QTimer fires every 25ms (same interval as AnkiConnect)
    - Each tick drains ALL pending requests from the queue
    - Executes each request via handler registry
    - Sends response back via QueueBridge to unblock waiting background thread

Performance:
    - 25ms polling interval adds negligible latency (~imperceptible)
    - Batch processing prevents queue buildup under load
    - Non-blocking operation keeps UI responsive
"""

from aqt import mw
from aqt.qt import QTimer

from anki_mcp_server.handler_registry import execute
from anki_mcp_server.queue_bridge import QueueBridge, ToolRequest, ToolResponse


class RequestProcessor:
    """Processes MCP requests on Qt main thread using QTimer.

    This class is the bridge between the async MCP server (running in a
    background thread) and Anki's collection API (which must be accessed
    from the Qt main thread). It uses a QTimer to periodically poll the
    request queue and execute pending operations.

    The processor is designed to:
    1. Never block the Qt event loop (keeps UI responsive)
    2. Process all pending requests in each tick (drains queue)
    3. Safely access mw.col (we're on main thread)
    4. Handle errors gracefully (returns error response instead of crashing)

    Usage:
        >>> bridge = QueueBridge()
        >>> processor = RequestProcessor(bridge)
        >>> processor.start()  # Start processing requests
        >>> # Later, during shutdown:
        >>> processor.stop()

    Attributes:
        _bridge: Thread-safe communication bridge to background thread.
        _timer: Qt timer that triggers request processing.

    Note:
        This class MUST be instantiated on the Qt main thread (where mw is
        available). The timer will run on the same thread where it's created.
    """

    def __init__(self, bridge: QueueBridge) -> None:
        """Initialize request processor.

        Args:
            bridge: Queue bridge for thread-safe communication with the
                background MCP server thread.

        Note:
            The QTimer is created with mw as parent to ensure proper Qt
            lifecycle management. This means this class cannot be instantiated
            outside of Anki (mw must exist).
        """
        self._bridge = bridge
        self._timer = QTimer(mw)
        self._timer.timeout.connect(self._process_pending)

    def start(self, interval_ms: int = 25) -> None:
        """Start processing requests.

        Starts the QTimer which will call _process_pending() every interval_ms
        milliseconds. This begins the request processing loop.

        Args:
            interval_ms: Polling interval in milliseconds. Default is 25ms,
                which is the same interval used by AnkiConnect and provides
                imperceptible latency while keeping the UI responsive.

        Note:
            Calling start() when already started is safe - QTimer.start()
            will restart the timer with the new interval.

        Performance:
            - 25ms = 40 Hz polling rate
            - Typical Anki operations complete in < 5ms
            - Total added latency: ~0-25ms (one timer interval)
            - UI remains responsive (no blocking)
        """
        self._timer.start(interval_ms)

    def stop(self) -> None:
        """Stop processing requests.

        Stops the QTimer, which stops calling _process_pending(). Any requests
        currently in the queue will remain there until start() is called again.

        This should be called during addon shutdown to clean up resources.

        Note:
            Calling stop() when already stopped is safe - QTimer.stop() is
            idempotent.
        """
        self._timer.stop()

    def _process_pending(self) -> None:
        """Process all pending requests from the queue.

        Called automatically by QTimer every interval_ms milliseconds. This
        method:
        1. Checks for pending requests (non-blocking)
        2. If found, executes the tool via handler registry
        3. Sends response back via bridge
        4. Repeats until queue is empty

        The loop structure ensures we drain the entire queue in one timer tick,
        preventing request buildup under load.

        Thread Safety:
            - Runs on Qt main thread (guaranteed by QTimer)
            - Safe to access mw.col here
            - Non-blocking queue operations keep UI responsive

        Error Handling:
            - Exceptions during tool execution are caught and returned as
              error responses
            - Processing continues even if one request fails
            - Never crashes the timer or Anki

        Performance:
            - Processes requests until queue is empty
            - Typical queue depth: 0-2 requests
            - Each iteration takes ~1-10ms depending on operation
            - Total time per tick rarely exceeds the 25ms interval
        """
        while True:
            # Non-blocking check for pending request
            # Returns None immediately if queue is empty
            request = self._bridge.get_pending_request()
            if request is None:
                break  # Queue empty, exit until next timer tick

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
            - This prevents exceptions from propagating to the timer
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
