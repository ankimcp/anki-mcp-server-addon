"""Unit tests locking the tunnel client's success-reply shapes.

MCP Streamable HTTP ("Sending Messages to the Server"): a POST carrying a
JSON-RPC notification or response MUST get ``202 Accepted`` with no body; a
request gets ``200`` with a JSON body. The transport signals the former by
returning ``None`` from ``handle_request``.

We drive ``TunnelClient._handle_request(ws, msg)`` directly with a stub
transport and an ``AsyncMock`` WebSocket, like ``test_tunnel_error_shapes.py``.
"""

from __future__ import annotations

# The package __init__ must run before the MCP SDK is imported.
import anki_mcp_server  # noqa: F401

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from anki_mcp_server.tunnel.client import TunnelClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tunnel_request(body: str | None, *, request_id: str = "req-1") -> dict:
    """Build a ``request`` message dict shaped the way ``_handle_request`` reads it."""
    msg: dict = {
        "type": "request",
        "requestId": request_id,
        "method": "POST",
        "path": "/mcp",
        "headers": {"content-type": "application/json"},
    }
    if body is not None:
        msg["body"] = body
    return msg


def _make_client(transport: object, on_request_completed: MagicMock) -> TunnelClient:
    """Construct a TunnelClient with a stub transport and a completion spy."""
    return TunnelClient(
        server_url="wss://example.invalid",
        credentials=MagicMock(),
        transport=transport,  # type: ignore[arg-type]
        on_request_completed=on_request_completed,
    )


def _sent_payload(ws: MagicMock) -> dict:
    """Decode the single JSON payload the client sent over ``ws.send``."""
    assert ws.send.await_count == 1, f"expected exactly one send, got {ws.send.await_count}"
    (raw,) = ws.send.await_args.args
    return json.loads(raw)


async def _run(handle_request_result: str | None, body: str | None) -> tuple[dict, MagicMock, MagicMock]:
    """Run ``_handle_request`` once; return (sent payload, completion spy, transport)."""
    transport = MagicMock()
    transport.handle_request = AsyncMock(return_value=handle_request_result)
    ws = MagicMock()
    ws.send = AsyncMock()
    completed = MagicMock()

    client = _make_client(transport, completed)
    await client._handle_request(ws, _make_tunnel_request(body, request_id="req-shape"))

    return _sent_payload(ws), completed, transport


# ===========================================================================
# 202 Accepted, no body
# ===========================================================================

class TestAcceptedNoBody:
    """Transport returns ``None`` -> 202, empty headers, no ``body`` key."""

    @pytest.mark.asyncio
    async def test_notification_yields_202_without_body(self) -> None:
        body = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        payload, completed, transport = await _run(None, body)

        transport.handle_request.assert_awaited_once_with(body)
        assert payload["type"] == "response"
        assert payload["requestId"] == "req-shape"
        assert payload["statusCode"] == 202
        assert payload["headers"] == {}
        assert "body" not in payload

        completed.assert_called_once()
        method_path, status_code, _duration = completed.call_args.args
        assert method_path == "POST /mcp"
        assert status_code == 202

    @pytest.mark.asyncio
    async def test_jsonrpc_response_yields_202_without_body(self) -> None:
        body = json.dumps({"jsonrpc": "2.0", "id": 3, "result": {}})
        payload, completed, _transport = await _run(None, body)

        assert payload["statusCode"] == 202
        assert payload["headers"] == {}
        assert "body" not in payload
        assert completed.call_args.args[1] == 202

    @pytest.mark.asyncio
    async def test_empty_inbound_body_yields_202_without_body(self) -> None:
        payload, completed, transport = await _run("unused", None)

        transport.handle_request.assert_not_awaited()
        assert payload["statusCode"] == 202
        assert payload["headers"] == {}
        assert "body" not in payload
        assert completed.call_args.args[1] == 202


# ===========================================================================
# 200 with JSON body
# ===========================================================================

class TestRequestReply:
    """Transport returns a string -> 200, JSON content-type, body present."""

    @pytest.mark.asyncio
    async def test_request_yields_200_with_json_body(self) -> None:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        response_json = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})
        payload, completed, _transport = await _run(response_json, body)

        assert payload["type"] == "response"
        assert payload["requestId"] == "req-shape"
        assert payload["statusCode"] == 200
        assert payload["headers"] == {"content-type": "application/json"}
        assert payload["body"] == response_json

        completed.assert_called_once()
        assert completed.call_args.args[1] == 200
