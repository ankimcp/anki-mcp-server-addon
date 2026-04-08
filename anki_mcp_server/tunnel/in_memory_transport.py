"""In-memory transport bridging tunnel requests into a running MCP Server.run() loop.

Creates anyio memory stream pairs and runs Server.run(stateless=True) as a
background task.  Incoming JSON-RPC strings are parsed, wrapped in
SessionMessage, and pushed into the server's read stream.  Responses are
collected from the server's write stream by a background reader and matched
back to callers via asyncio.Future keyed by JSON-RPC request ``id``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from mcp.server.lowlevel.server import Server
from mcp.shared.message import SessionMessage
from mcp.types import (
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
)

logger = logging.getLogger(__name__)

_RESPONSE_TIMEOUT = 30  # seconds
_STOP_GRACE_PERIOD = 5  # seconds to wait for Server.run() to exit


class InMemoryTransport:
    """Feed raw JSON-RPC strings into an MCP ``Server`` via memory streams.

    Lifecycle::

        transport = InMemoryTransport(server)
        await transport.start()
        ...
        response_json = await transport.handle_request(body_json)
        ...
        await transport.stop()
    """

    def __init__(self, server: Server[Any]) -> None:
        self._server = server

        # Stream endpoints – set in start()
        self._server_read_send: MemoryObjectSendStream[SessionMessage | Exception] | None = None
        self._server_write_recv: MemoryObjectReceiveStream[SessionMessage] | None = None

        # Background tasks
        self._server_task: asyncio.Task[None] | None = None
        self._reader_task: asyncio.Task[None] | None = None

        # request-id -> Future awaiting the JSON-RPC response
        self._pending: dict[str | int, asyncio.Future[SessionMessage]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Create memory streams and start ``Server.run()`` + reader tasks."""

        # Direction: we send INTO server_read, server sends INTO server_write.
        #   our_send  -> server reads from our_recv  (the "server read" pair)
        #   server writes to sw_send -> we read from sw_recv  (the "server write" pair)
        our_send, our_recv = anyio.create_memory_object_stream[SessionMessage | Exception](1)
        sw_send, sw_recv = anyio.create_memory_object_stream[SessionMessage](1)

        self._server_read_send = our_send
        self._server_write_recv = sw_recv

        init_options = self._server.create_initialization_options()

        self._server_task = asyncio.create_task(
            self._run_server(our_recv, sw_send, init_options),
            name="in-memory-mcp-server",
        )
        self._reader_task = asyncio.create_task(
            self._read_responses(),
            name="in-memory-response-reader",
        )
        logger.info("InMemoryTransport started")

    async def stop(self) -> None:
        """Gracefully stop Server.run(), cancel reader, reject pending futures.

        Shutdown order:
        1. Close _server_read_send (our send end) -> Server.run() sees
           EndOfStream and exits gracefully.
        2. Await _server_task with a timeout; cancel if it doesn't finish.
        3. Cancel _reader_task.
        4. Close _server_write_recv (our receive end of the write stream).
        5. Reject any pending futures.

        We do NOT close _server_read_recv or _server_write_send — those are
        owned by Server.run() via ServerSession and will be closed when
        Server.run() exits.
        """

        # 1. Signal Server.run() to exit by closing our send end
        if self._server_read_send is not None:
            await self._server_read_send.aclose()
            self._server_read_send = None

        # 2. Wait for Server.run() to finish; cancel if it takes too long
        try:
            if self._server_task is not None and not self._server_task.done():
                try:
                    await asyncio.wait_for(self._server_task, timeout=_STOP_GRACE_PERIOD)
                except asyncio.TimeoutError:
                    logger.warning("Server.run() did not exit within %ds, cancelling", _STOP_GRACE_PERIOD)
                    self._server_task.cancel()
                    try:
                        await self._server_task
                    except (asyncio.CancelledError, Exception):
                        pass
                except (asyncio.CancelledError, Exception):
                    pass
        finally:
            # 3-5 MUST run even if step 2 raises CancelledError

            # 3. Cancel the response reader
            if self._reader_task is not None and not self._reader_task.done():
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except (asyncio.CancelledError, Exception):
                    pass

            # 4. Close our receive end of the write stream
            if self._server_write_recv is not None:
                await self._server_write_recv.aclose()
                self._server_write_recv = None

            # 5. Reject any pending futures still waiting
            for request_id, fut in self._pending.items():
                if not fut.done():
                    fut.set_exception(
                        RuntimeError(f"Transport stopped while awaiting response for request {request_id}")
                    )
            self._pending.clear()

            self._server_task = None
            self._reader_task = None
            logger.info("InMemoryTransport stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def handle_request(self, body: str) -> str:
        """Parse a JSON-RPC ``body``, feed it to the server, return the response.

        For notifications (no ``id``), the message is sent fire-and-forget and
        an empty string is returned immediately.

        Raises ``RuntimeError`` if the transport is not started.
        """

        if self._server_read_send is None:
            raise RuntimeError("Transport is not started")

        raw: dict[str, Any] = json.loads(body)
        message = JSONRPCMessage.model_validate(raw)
        session_message = SessionMessage(message=message)

        # Notifications have no id – fire and forget
        if isinstance(message.root, JSONRPCNotification):
            await self._server_read_send.send(session_message)
            return ""

        # Requests must have an id
        request_id = message.root.id  # type: ignore[union-attr]
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[SessionMessage] = loop.create_future()
        self._pending[request_id] = fut

        try:
            await self._server_read_send.send(session_message)
            response_msg = await asyncio.wait_for(fut, timeout=_RESPONSE_TIMEOUT)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            error_body = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": "Request timed out"},
            }
            return json.dumps(error_body)
        except Exception:
            self._pending.pop(request_id, None)
            raise

        return response_msg.message.model_dump_json(by_alias=True, exclude_none=True)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_server(
        self,
        read_stream: MemoryObjectReceiveStream[SessionMessage | Exception],
        write_stream: MemoryObjectSendStream[SessionMessage],
        init_options: Any,
    ) -> None:
        """Wrapper around ``Server.run()`` that logs exceptions."""
        try:
            await self._server.run(
                read_stream,
                write_stream,
                init_options,
                stateless=True,
            )
        except asyncio.CancelledError:
            logger.debug("Server.run() cancelled")
        except Exception:
            logger.exception("Server.run() failed unexpectedly")

    async def _read_responses(self) -> None:
        """Read ``SessionMessage`` items from the server's write stream.

        Responses and errors (which carry an ``id``) are matched to pending
        futures.  Notifications emitted by the server (no ``id``) are logged
        and discarded — there is no connected client to forward them to.
        """
        if self._server_write_recv is None:
            return

        try:
            async for session_message in self._server_write_recv:
                root = session_message.message.root

                if isinstance(root, (JSONRPCResponse, JSONRPCError)):
                    request_id = root.id
                    fut = self._pending.pop(request_id, None)
                    if fut is not None and not fut.done():
                        fut.set_result(session_message)
                    else:
                        logger.warning(
                            "Received response for unknown/completed request id=%s",
                            request_id,
                        )
                elif isinstance(root, JSONRPCRequest):
                    # Server-initiated request (e.g. sampling) – not supported
                    # in tunnel mode. Log and discard.
                    logger.debug(
                        "Ignoring server-initiated request: method=%s",
                        root.method,
                    )
                elif isinstance(root, JSONRPCNotification):
                    logger.debug(
                        "Ignoring server notification: method=%s",
                        root.method,
                    )
                else:
                    logger.warning("Unknown message type on write stream: %s", type(root))
        except (anyio.ClosedResourceError, anyio.EndOfStream):
            logger.debug("Server write stream closed")
        except asyncio.CancelledError:
            logger.debug("Response reader cancelled")
        except Exception:
            logger.exception("Unexpected error in response reader")
