"""Thread-safe communication bridge between MCP server and Anki main thread.

This module provides the core threading infrastructure for the AnkiMCP Server addon.
It enables safe communication between the background asyncio thread (running the
MCP server) and the Qt main thread (where Anki collection access is safe).

Architecture:
    - Background thread: MCP server handles protocol, puts requests in queue,
      blocks waiting for responses
    - Main thread: QTimer polls request queue, executes Anki operations, sends
      responses back

Thread Safety:
    - Uses Python's built-in `queue.Queue` which is thread-safe by design
    - Background thread is allowed to block on response_queue.get()
    - Main thread never blocks - uses get_nowait() in QTimer callback
"""

import queue
from dataclasses import dataclass
from typing import Any


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
    error: str | None = None


class QueueBridge:
    """Thread-safe bridge between MCP server and Anki main thread.

    This class provides the core infrastructure for safe cross-thread communication
    in the AnkiMCP addon. It uses Python's built-in Queue which is thread-safe and
    designed for producer-consumer patterns.

    Usage Pattern:
        1. Background thread (MCP server):
           - Creates ToolRequest
           - Calls send_request() - this BLOCKS until response is ready
           - Returns result to MCP client

        2. Main thread (QTimer callback every 25ms):
           - Calls get_pending_request() - non-blocking
           - If request found, executes on Anki collection
           - Calls send_response() - unblocks waiting background thread

    Thread Safety:
        - request_queue: Background thread writes (put), main thread reads (get_nowait)
        - response_queue: Main thread writes (put), background thread reads (get with timeout)
        - Both queues are thread-safe by design (queue.Queue uses locks internally)

    Shutdown Handling:
        - Setting _shutdown=True prevents new requests
        - Calling shutdown() puts a "poison pill" response to unblock any waiting threads
        - This ensures graceful addon shutdown without deadlocks

    Attributes:
        request_queue: Requests from background thread to main thread.
        response_queue: Responses from main thread back to background thread.

    Example:
        >>> bridge = QueueBridge()
        >>>
        >>> # In background thread (MCP server):
        >>> request = ToolRequest(
        ...     request_id="abc-123",
        ...     tool_name="list_decks",
        ...     arguments={}
        ... )
        >>> response = bridge.send_request(request)  # Blocks until main thread responds
        >>>
        >>> # In main thread (QTimer callback):
        >>> request = bridge.get_pending_request()  # Non-blocking
        >>> if request:
        ...     result = execute_on_anki(request)
        ...     response = ToolResponse(
        ...         request_id=request.request_id,
        ...         success=True,
        ...         result=result
        ...     )
        ...     bridge.send_response(response)  # Unblocks background thread
    """

    def __init__(self) -> None:
        """Initialize the queue bridge with empty request and response queues.

        Creates two thread-safe queues:
        - request_queue: For MCP server → main thread communication
        - response_queue: For main thread → MCP server communication
        """
        self.request_queue: queue.Queue[ToolRequest] = queue.Queue()
        self.response_queue: queue.Queue[ToolResponse] = queue.Queue()
        self._shutdown = False

    def send_request(self, request: ToolRequest) -> ToolResponse:
        """Send request to main thread and wait for response.

        Called from background thread (MCP server) when a tool call is received.
        This method BLOCKS until the main thread processes the request and sends
        a response back. This is safe because it's not the Qt main thread that
        blocks.

        Args:
            request: The tool request to execute on the main thread.

        Returns:
            The response from the main thread after executing the tool.

        Raises:
            Exception: If the bridge is shutting down and new requests are not
                accepted. This prevents deadlocks during addon shutdown.
            queue.Empty: If no response is received within 30 seconds. This
                timeout prevents indefinite blocking if the main thread crashes
                or becomes unresponsive.

        Thread Safety:
            Safe to call from any thread. Typically called from the background
            asyncio thread running the MCP server.

        Example:
            >>> bridge = QueueBridge()
            >>> request = ToolRequest(
            ...     request_id="req-001",
            ...     tool_name="get_note",
            ...     arguments={"note_id": 12345}
            ... )
            >>> try:
            ...     response = bridge.send_request(request)
            ...     if response.success:
            ...         print(f"Note: {response.result}")
            ...     else:
            ...         print(f"Error: {response.error}")
            ... except queue.Empty:
            ...     print("Request timed out after 30 seconds")
            ... except Exception as e:
            ...     print(f"Bridge shutting down: {e}")
        """
        if self._shutdown:
            raise Exception("Bridge is shutting down")

        self.request_queue.put(request)

        # Block until main thread responds (with timeout to prevent indefinite hang)
        # 30 second timeout is generous - typical operations complete in milliseconds
        # If this times out, something is seriously wrong (main thread crashed, etc.)
        return self.response_queue.get(timeout=30)

    def get_pending_request(self) -> ToolRequest | None:
        """Non-blocking check for pending requests.

        Called from main thread (QTimer callback) to check if there are any
        pending tool requests. This method NEVER blocks - it returns immediately
        with either a request or None.

        Returns:
            The next pending request if one exists, otherwise None.

        Thread Safety:
            Safe to call from any thread, but designed to be called from the
            Qt main thread in a QTimer callback.

        Performance:
            This method is called every 25ms by the QTimer. The non-blocking
            behavior ensures the Qt event loop is never blocked, keeping the
            UI responsive.

        Example:
            >>> bridge = QueueBridge()
            >>>
            >>> # In QTimer callback (called every 25ms):
            >>> def process_pending():
            ...     while True:
            ...         request = bridge.get_pending_request()
            ...         if request is None:
            ...             break  # No more requests, exit loop
            ...
            ...         # Process request on main thread (safe to access mw.col)
            ...         response = execute_tool(request)
            ...         bridge.send_response(response)
        """
        try:
            return self.request_queue.get_nowait()
        except queue.Empty:
            return None

    def send_response(self, response: ToolResponse) -> None:
        """Send response back to waiting MCP handler.

        Called from main thread (RequestProcessor) after executing a tool request.
        This unblocks the background thread that is waiting in send_request().

        Args:
            response: The response containing the result or error from executing
                the tool.

        Thread Safety:
            Safe to call from any thread, but designed to be called from the
            Qt main thread after executing Anki operations.

        Example:
            >>> bridge = QueueBridge()
            >>> response = ToolResponse(
            ...     request_id="req-001",
            ...     success=True,
            ...     result={"id": 12345, "fields": {"Front": "Hello"}}
            ... )
            >>> bridge.send_response(response)  # Unblocks background thread
        """
        self.response_queue.put(response)

    def shutdown(self) -> None:
        """Unblock any waiting requests on shutdown.

        Called when the addon is shutting down. This prevents deadlocks by:
        1. Setting the _shutdown flag to reject new requests
        2. Putting a "poison pill" response to unblock any threads waiting in
           send_request()

        After calling this method, any threads blocked in send_request() will
        receive an error response, and new calls to send_request() will raise
        an exception.

        Thread Safety:
            Safe to call from any thread, typically called from the main thread
            during addon shutdown.

        Example:
            >>> bridge = QueueBridge()
            >>>
            >>> # During addon shutdown:
            >>> bridge.shutdown()
            >>>
            >>> # Any waiting threads will receive:
            >>> # ToolResponse(request_id="shutdown", success=False,
            >>> #              error="Server shutting down")
            >>>
            >>> # New requests will raise:
            >>> try:
            ...     bridge.send_request(some_request)
            ... except Exception as e:
            ...     print(e)  # "Bridge is shutting down"
        """
        self._shutdown = True

        # Put poison pill to unblock any waiting threads
        # This ensures graceful shutdown without deadlocks
        self.response_queue.put(
            ToolResponse(
                request_id="shutdown",
                success=False,
                error="Server shutting down",
            )
        )
