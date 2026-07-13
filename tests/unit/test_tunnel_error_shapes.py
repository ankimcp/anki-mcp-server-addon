"""Unit tests locking the tunnel client's error/timeout response contract.

These tests pin the JSON-RPC error envelopes the ``TunnelClient`` sends back
through the relay when request processing fails, plus the transport's own
timeout behaviour. They close a known coverage gap around:

* **504** — ``asyncio.TimeoutError`` from the transport maps to ``statusCode``
  504 with a JSON-RPC ``-32004`` error envelope that *echoes* the inner
  request id.
* **500** — any other ``Exception`` maps to ``statusCode`` 500 with a
  ``-32603`` envelope. NOTE: the 500 path hardcodes ``id: None`` (it does not
  recover the inner id the way the 504 path does — an asymmetry flagged in
  review and asserted explicitly below).
* ``_extract_request_id`` — the helper used by the 504 path.
* ``InMemoryTransport.handle_request`` — a slow handler raises
  ``asyncio.TimeoutError`` rather than returning an error body or hanging.

For A/B/C we drive ``TunnelClient._handle_request(ws, msg)`` directly with a
stub transport and an ``AsyncMock`` WebSocket — no network, no real server.
For D we run a real ``FastMCP`` through the transport with a tiny patched
``_RESPONSE_TIMEOUT`` so the test stays fast and deterministic.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Module aliasing – must happen before InMemoryTransport is imported.
#
# The transport uses *relative* imports (``from ..vendor.shared.mcp.types …``)
# while the MCP Server internally uses *absolute* imports (``from mcp.types …``).
# Python treats these as two distinct module objects, which breaks ``isinstance``
# checks inside the transport's ``_read_responses`` reader.
#
# The fix: after ``anki_mcp_server.__init__`` adds ``vendor/shared`` to
# ``sys.path`` (giving us ``mcp.*``), we create aliases so that
# ``anki_mcp_server.vendor.shared.mcp.*`` points to the **same** module
# objects.  This must run before the transport module is imported.
# ---------------------------------------------------------------------------
import sys

import anki_mcp_server  # noqa: F401 – triggers vendor path setup

for _key, _mod in list(sys.modules.items()):
    if _key.startswith("mcp.") or _key == "mcp":
        _alias = f"anki_mcp_server.vendor.shared.{_key}"
        if _alias not in sys.modules:
            sys.modules[_alias] = _mod

# ---------------------------------------------------------------------------
# Now safe to import the transport and the rest of the MCP SDK.
# ---------------------------------------------------------------------------
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from mcp.server.fastmcp import FastMCP

from anki_mcp_server.tunnel import in_memory_transport
from anki_mcp_server.tunnel.client import TunnelClient, _extract_request_id
from anki_mcp_server.tunnel.in_memory_transport import InMemoryTransport


# ---------------------------------------------------------------------------
# Helpers / fixtures for A & B
# ---------------------------------------------------------------------------

def _make_tunnel_request(*, request_id: str = "req-1", inner_id: int = 42) -> dict:
    """Build a ``request`` message dict shaped the way ``_handle_request`` reads it.

    ``_handle_request`` indexes ``requestId``, ``method``, ``path`` directly and
    reads ``body`` via ``.get``. ``body`` must be truthy or the transport is
    never invoked, so we always supply a JSON-RPC body carrying ``inner_id`` —
    that's the id the 504 path is expected to echo back.
    """
    return {
        "type": "request",
        "requestId": request_id,
        "method": "POST",
        "path": "/mcp",
        "headers": {"content-type": "application/json"},
        "body": json.dumps(
            {"jsonrpc": "2.0", "id": inner_id, "method": "tools/list"}
        ),
    }


def _make_client(transport: object) -> TunnelClient:
    """Construct a TunnelClient with a stub transport and dummy credentials.

    We never call ``run()``, so the server URL and credentials are inert — a
    MagicMock credentials object is enough.
    """
    return TunnelClient(
        server_url="wss://example.invalid",
        credentials=MagicMock(),
        transport=transport,  # type: ignore[arg-type]
    )


def _sent_payload(ws: AsyncMock) -> dict:
    """Decode the single JSON payload the client sent over ``ws.send``."""
    assert ws.send.await_count == 1, f"expected exactly one send, got {ws.send.await_count}"
    (raw,) = ws.send.await_args.args
    return json.loads(raw)


# ===========================================================================
# A. Client 504 timeout mapping
# ===========================================================================

class TestClient504Timeout:
    """Transport ``asyncio.TimeoutError`` -> 504 + JSON-RPC -32004, echoed id."""

    @pytest.mark.asyncio
    async def test_timeout_maps_to_504_with_echoed_id(self) -> None:
        transport = MagicMock()
        # Inject the timeout: handle_request awaits, then raises TimeoutError.
        transport.handle_request = AsyncMock(side_effect=asyncio.TimeoutError())

        ws = MagicMock()
        ws.send = AsyncMock()

        client = _make_client(transport)
        msg = _make_tunnel_request(request_id="req-504", inner_id=42)

        await client._handle_request(ws, msg)

        payload = _sent_payload(ws)
        # Outer tunnel-response envelope
        assert payload["type"] == "response"
        assert payload["requestId"] == "req-504"
        assert payload["statusCode"] == 504

        # Inner JSON-RPC error envelope
        body = json.loads(payload["body"])
        assert body["jsonrpc"] == "2.0"
        assert body["error"]["code"] == -32004
        assert "message" in body["error"]
        # The 504 path recovers and echoes the inner request id.
        assert body["id"] == 42


# ===========================================================================
# B. Client 500 generic-error mapping
# ===========================================================================

class TestClient500GenericError:
    """Generic ``Exception`` -> 500 + JSON-RPC -32603, hardcoded id: None."""

    @pytest.mark.asyncio
    async def test_generic_error_maps_to_500_with_none_id(self) -> None:
        transport = MagicMock()
        # Inject a NON-timeout exception. In Python 3.11+ ``asyncio.TimeoutError``
        # IS ``TimeoutError``, so a timeout-flavoured error would be caught by the
        # earlier ``except asyncio.TimeoutError`` branch (504) and this test would
        # pass for the wrong reason. RuntimeError guarantees the generic branch.
        transport.handle_request = AsyncMock(side_effect=RuntimeError("boom"))

        ws = MagicMock()
        ws.send = AsyncMock()

        client = _make_client(transport)
        # Inner id 99 is deliberately present and recoverable from the body. The
        # 504 path WOULD echo it; we assert the 500 path ignores it (id: None).
        msg = _make_tunnel_request(request_id="req-500", inner_id=99)

        await client._handle_request(ws, msg)

        payload = _sent_payload(ws)
        assert payload["type"] == "response"
        assert payload["requestId"] == "req-500"
        # Must be 500, NOT 504 — proves we hit the generic branch, not timeout.
        assert payload["statusCode"] == 500

        body = json.loads(payload["body"])
        assert body["jsonrpc"] == "2.0"
        assert body["error"]["code"] == -32603
        assert "boom" in body["error"]["message"]
        # ASYMMETRY: the 500 path hardcodes ``id: None`` and does NOT echo the
        # recoverable inner id (99). The 504 path (test A) does echo it. This is
        # the review-flagged inconsistency; pinned here so a change is noticed.
        assert body["id"] is None
        assert body["id"] != 99


# ===========================================================================
# C. _extract_request_id helper
# ===========================================================================

class TestExtractRequestId:
    """Best-effort recovery of the inner JSON-RPC id from a body string."""

    def test_dict_with_id_returns_id(self) -> None:
        body = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
        assert _extract_request_id(body) == 7

    def test_dict_with_string_id_returns_id(self) -> None:
        body = json.dumps({"jsonrpc": "2.0", "id": "abc", "method": "tools/list"})
        assert _extract_request_id(body) == "abc"

    def test_array_batch_body_returns_none(self) -> None:
        body = json.dumps([{"jsonrpc": "2.0", "id": 1}, {"jsonrpc": "2.0", "id": 2}])
        assert _extract_request_id(body) is None

    def test_malformed_json_returns_none(self) -> None:
        assert _extract_request_id("not valid json {{{") is None

    def test_none_body_returns_none(self) -> None:
        assert _extract_request_id(None) is None

    def test_empty_body_returns_none(self) -> None:
        assert _extract_request_id("") is None

    def test_dict_without_id_returns_none(self) -> None:
        body = json.dumps({"jsonrpc": "2.0", "method": "tools/list"})
        assert _extract_request_id(body) is None


# ===========================================================================
# D. Transport layer — slow handler actually times out
# ===========================================================================

def _build_slow_mcp(delay: float) -> FastMCP:
    """FastMCP with a single tool whose handler sleeps for ``delay`` seconds."""
    mcp = FastMCP("slow-server")

    @mcp.tool()
    async def slow() -> str:
        """A deliberately slow tool used to trigger the response timeout."""
        await asyncio.sleep(delay)
        return "done"

    return mcp


@pytest_asyncio.fixture
async def slow_transport(monkeypatch):
    """Started transport whose response timeout is patched to a tiny value.

    ``_RESPONSE_TIMEOUT`` is read inline inside ``handle_request`` at call time,
    so monkeypatching the module constant takes effect for the next request.

    Teardown note: the slow tool sleeps only slightly longer than the patched
    timeout, so the in-flight handler finishes naturally well within
    ``stop()``'s grace period — keeping teardown fast. ``asyncio.wait_for``
    still raises at the patched timeout regardless of the sleep.
    """
    monkeypatch.setattr(in_memory_transport, "_RESPONSE_TIMEOUT", 0.05)

    # Sleep above the 0.05s timeout but small enough that teardown is quick.
    mcp = _build_slow_mcp(delay=0.25)
    t = InMemoryTransport(mcp._mcp_server)
    await t.start()
    yield t
    await t.stop()


class TestTransportTimeout:
    """``InMemoryTransport.handle_request`` raises on a slow handler."""

    @pytest.mark.asyncio
    async def test_slow_handler_raises_timeout(
        self, slow_transport: InMemoryTransport
    ) -> None:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "slow", "arguments": {}},
            }
        )
        # Must raise — not return an error body, not hang.
        with pytest.raises(asyncio.TimeoutError):
            await slow_transport.handle_request(body)
