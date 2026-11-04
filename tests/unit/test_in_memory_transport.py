"""Unit tests for InMemoryTransport.

Tests exercise the transport against a real FastMCP server instance (no mocking
of the MCP SDK).  They verify JSON-RPC request routing, notification handling,
error responses, concurrency, and graceful shutdown.
"""

from __future__ import annotations

# The package __init__ must run before the MCP SDK is imported.
import anki_mcp_server  # noqa: F401

import asyncio
import json

import pytest
import pytest_asyncio

from mcp.server.fastmcp import FastMCP

from anki_mcp_server.tunnel.in_memory_transport import InMemoryTransport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(method: str, params: dict | None = None, *, request_id: int = 1) -> str:
    """Build a JSON-RPC 2.0 request string."""
    msg: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def _make_notification(method: str, params: dict | None = None) -> str:
    """Build a JSON-RPC 2.0 notification string (no ``id``)."""
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def _build_mcp() -> FastMCP:
    """Create a FastMCP instance with a simple ``add`` tool."""
    mcp = FastMCP("test-server")

    @mcp.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    return mcp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def transport():
    """Yield a started InMemoryTransport backed by a real FastMCP server."""
    mcp = _build_mcp()
    t = InMemoryTransport(mcp._mcp_server)
    await t.start()
    yield t
    await t.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestInMemoryTransport:
    """Tests for the InMemoryTransport class."""

    # -- 1. start / stop lifecycle -----------------------------------------

    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        """Transport starts and stops without errors."""
        mcp = _build_mcp()
        t = InMemoryTransport(mcp._mcp_server)

        await t.start()
        # Internal tasks should be running
        assert t._server_task is not None
        assert not t._server_task.done()
        assert t._reader_task is not None
        assert not t._reader_task.done()

        await t.stop()
        # After stop, tasks and streams should be cleared
        assert t._server_task is None
        assert t._reader_task is None
        assert t._server_read_send is None
        assert t._server_write_recv is None

    # -- 2. tool call ------------------------------------------------------

    @pytest.mark.asyncio
    async def test_handle_tool_call(self, transport: InMemoryTransport) -> None:
        """Calling a registered tool returns the correct result."""
        body = _make_request(
            "tools/call",
            {"name": "add", "arguments": {"a": 2, "b": 3}},
            request_id=10,
        )
        raw = await transport.handle_request(body)
        resp = json.loads(raw)

        assert resp["id"] == 10
        assert "result" in resp
        result = resp["result"]
        # FastMCP wraps tool return values in content blocks
        assert result.get("isError") is not True
        # The result should contain the value 5 somewhere in the content
        content = result.get("content", [])
        assert any("5" in str(c.get("text", "")) for c in content), f"Expected 5 in {content}"

    # -- 3. list tools -----------------------------------------------------

    @pytest.mark.asyncio
    async def test_handle_list_tools(self, transport: InMemoryTransport) -> None:
        """``tools/list`` returns the registered tool."""
        raw = await transport.handle_request(_make_request("tools/list", request_id=20))
        resp = json.loads(raw)

        assert resp["id"] == 20
        assert "result" in resp
        tools = resp["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert "add" in tool_names

    # -- 4. notification (fire-and-forget) ---------------------------------

    @pytest.mark.asyncio
    async def test_handle_notification(self, transport: InMemoryTransport) -> None:
        """Notifications return ``None`` (fire-and-forget, accepted with no body)."""
        result = await transport.handle_request(
            _make_notification("notifications/cancelled", {"requestId": "999", "reason": "test"})
        )
        assert result is None
        assert transport._pending == {}

    @pytest.mark.asyncio
    async def test_handle_initialized_notification(self, transport: InMemoryTransport) -> None:
        """``notifications/initialized`` -- the message Codex CLI tripped on -- returns ``None``."""
        result = await transport.handle_request(_make_notification("notifications/initialized"))
        assert result is None

    # -- 4b. client-sent JSON-RPC response / error (fire-and-forget) -------

    @pytest.mark.asyncio
    async def test_handle_jsonrpc_response_returns_none_immediately(
        self, transport: InMemoryTransport
    ) -> None:
        """A client-sent response returns ``None`` without waiting for the 25s timeout."""
        body = json.dumps({"jsonrpc": "2.0", "id": 77, "result": {}})
        result = await asyncio.wait_for(transport.handle_request(body), timeout=5)

        assert result is None
        assert transport._pending == {}

    @pytest.mark.asyncio
    async def test_handle_jsonrpc_error_returns_none_immediately(
        self, transport: InMemoryTransport
    ) -> None:
        """A client-sent error response returns ``None`` without waiting for the timeout."""
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 78, "error": {"code": -32601, "message": "nope"}}
        )
        result = await asyncio.wait_for(transport.handle_request(body), timeout=5)

        assert result is None
        assert transport._pending == {}

    @pytest.mark.asyncio
    async def test_jsonrpc_response_does_not_clobber_inflight_request(
        self, transport: InMemoryTransport
    ) -> None:
        """A client response sharing an in-flight request's id leaves that request intact."""
        sentinel: asyncio.Future = asyncio.get_running_loop().create_future()
        transport._pending[5] = sentinel

        result = await transport.handle_request(
            json.dumps({"jsonrpc": "2.0", "id": 5, "result": {}})
        )

        assert result is None
        assert transport._pending.get(5) is sentinel
        assert not sentinel.done()
        transport._pending.pop(5)

    @pytest.mark.asyncio
    async def test_request_still_returns_response_string(
        self, transport: InMemoryTransport
    ) -> None:
        """Requests keep returning the serialized JSON-RPC response."""
        raw = await transport.handle_request(_make_request("tools/list", request_id=21))
        assert isinstance(raw, str)
        assert json.loads(raw)["id"] == 21

    # -- 5. invalid JSON ---------------------------------------------------

    @pytest.mark.asyncio
    async def test_handle_invalid_json(self, transport: InMemoryTransport) -> None:
        """Invalid JSON raises a json.JSONDecodeError."""
        with pytest.raises(json.JSONDecodeError):
            await transport.handle_request("not valid json {{{")

    # -- 6. unknown method -------------------------------------------------

    @pytest.mark.asyncio
    async def test_handle_unknown_method(self, transport: InMemoryTransport) -> None:
        """An unknown method returns a JSON-RPC error response."""
        raw = await transport.handle_request(
            _make_request("nonexistent/method", request_id=30)
        )
        resp = json.loads(raw)

        assert resp["id"] == 30
        assert "error" in resp

    # -- 7. concurrent requests --------------------------------------------

    @pytest.mark.asyncio
    async def test_concurrent_requests(self, transport: InMemoryTransport) -> None:
        """Multiple concurrent tool calls all return correct results."""

        async def call_add(a: int, b: int, req_id: int) -> dict:
            body = _make_request(
                "tools/call",
                {"name": "add", "arguments": {"a": a, "b": b}},
                request_id=req_id,
            )
            raw = await transport.handle_request(body)
            return json.loads(raw)

        # Fire 10 requests concurrently
        tasks = [call_add(i, i * 10, req_id=100 + i) for i in range(10)]
        results = await asyncio.gather(*tasks)

        for i, resp in enumerate(results):
            assert resp["id"] == 100 + i
            assert "result" in resp
            expected = i + i * 10
            content = resp["result"].get("content", [])
            assert any(
                str(expected) in str(c.get("text", "")) for c in content
            ), f"Expected {expected} for i={i}, got {content}"

    # -- 8. stop rejects pending futures -----------------------------------

    @pytest.mark.asyncio
    async def test_stop_rejects_pending(self) -> None:
        """Stopping the transport rejects any pending futures with RuntimeError."""
        mcp = _build_mcp()
        t = InMemoryTransport(mcp._mcp_server)
        await t.start()

        # Inject a fake pending future to simulate a request still in flight
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        t._pending["test-id"] = fut

        await t.stop()

        assert fut.done()
        with pytest.raises(RuntimeError, match="Transport stopped"):
            fut.result()
