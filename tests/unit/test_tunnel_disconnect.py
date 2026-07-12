"""Regression tests for tunnel-client disconnect / clean-close handling.

These tests pin the structured-concurrency contract of ``TunnelClient.run()``:
a **clean** WebSocket close (code 1000 — what a user-initiated disconnect
produces) must make ``run()`` RETURN, not hang.

The original bug: ``run()`` started ``_receive_loop`` and ``_health_check_loop``
as siblings in one anyio task group. On a clean close the receive loop exits
*silently* (websockets does not raise on normal closure), and anyio task groups
only auto-cancel siblings on a child RAISE — on normal completion they WAIT for
all children. The infinite ``_health_check_loop`` therefore blocked the group
forever and ``run()`` never returned, leaving the settings UI stuck in the
transitional "Connecting…/Stop" state.

Each test drives the REAL ``TunnelClient.run()`` (only the ``connect`` symbol is
patched to hand back a fake WebSocket) and is wrapped in ``asyncio.wait_for``
with a 2s budget. The health-check loop sleeps on the real ``HEARTBEAT_INTERVAL``
(30s); we deliberately do NOT patch ``anyio.sleep``. So if the bug regresses,
the un-cancelled health-check loop blocks for 30s and ``wait_for`` raises
``TimeoutError`` at 2s — the test FAILS fast instead of wedging the suite.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Module aliasing – must happen before TunnelClient (and the transport it
# transitively touches) is imported.
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
# Now safe to import the client and the rest of the MCP SDK.
# ---------------------------------------------------------------------------
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from websockets.exceptions import ConnectionClosed
from websockets.frames import Close

from anki_mcp_server.tunnel import client as client_module
from anki_mcp_server.tunnel.client import TunnelClient
from anki_mcp_server.tunnel.protocol import CloseCodes, should_reconnect


# ---------------------------------------------------------------------------
# Fake WebSocket
# ---------------------------------------------------------------------------

class _FakeCleanCloseWebSocket:
    """A fake websockets ClientConnection that closes cleanly with zero messages.

    ``run()`` first calls ``recv()`` once for the handshake (we hand back a
    ``tunnel_established`` frame), then iterates the connection via ``async for``.
    Here the iterator stops immediately with ``StopAsyncIteration`` — the exact
    shape of a normal (code 1000) close, where websockets does NOT raise.

    ``close_code`` is 1000 so any code that inspects it sees a normal closure.
    ``send`` / ``close`` are awaitable no-ops.
    """

    def __init__(self, url: str = "https://tunnel.example/abc123") -> None:
        self._handshake = json.dumps({"type": "tunnel_established", "url": url})
        self._handshake_consumed = False
        self.close_code = CloseCodes.NORMAL
        self.send = AsyncMock()
        self.close = AsyncMock()

    async def recv(self) -> str:
        # Only the handshake is delivered via recv(); after that the message
        # loop switches to async-iteration (which ends immediately below).
        if not self._handshake_consumed:
            self._handshake_consumed = True
            return self._handshake
        raise StopAsyncIteration

    def __aiter__(self) -> "_FakeCleanCloseWebSocket":
        return self

    async def __anext__(self) -> str:
        # Clean close: no messages, iterator stops silently (no exception).
        raise StopAsyncIteration


class _FakeUncleanCloseWebSocket:
    """A fake that drops the connection mid-stream with an UNCLEAN close.

    After the handshake, async-iteration RAISES ``ConnectionClosed`` carrying
    a *received* Close frame (``rcvd``) with a real, non-NORMAL code. This is
    the shape ``run()``'s ``except* (_HealthCheckTimeout, ConnectionClosed)``
    handler reads via ``exc.rcvd.code`` — so the test pins that the close code
    is derived from the frame, NOT the GOING_AWAY default.

    We deliberately raise the base ``ConnectionClosed`` (not
    ``ConnectionClosedOK``, a subclass): the OK branch is matched first in
    ``run()``, and we want to land in the generic-ConnectionClosed handler.
    """

    def __init__(
        self,
        code: int = CloseCodes.SERVICE_UNAVAILABLE,
        reason: str = "Service unavailable",
        url: str = "https://tunnel.example/abc123",
    ) -> None:
        self._handshake = json.dumps({"type": "tunnel_established", "url": url})
        self._handshake_consumed = False
        self._code = code
        self._reason = reason
        self.close_code = code
        self.send = AsyncMock()
        self.close = AsyncMock()

    async def recv(self) -> str:
        if not self._handshake_consumed:
            self._handshake_consumed = True
            return self._handshake
        raise StopAsyncIteration

    def __aiter__(self) -> "_FakeUncleanCloseWebSocket":
        return self

    async def __anext__(self) -> str:
        # Unclean close: the server sent a Close frame, so websockets raises
        # ConnectionClosed with `rcvd` populated and `sent=None`. The Close
        # dataclass requires (code, reason); rcvd_then_sent defaults to None,
        # which is valid when sent is None (asserted in ConnectionClosed).
        raise ConnectionClosed(rcvd=Close(self._code, self._reason), sent=None)


class _FakeSilentWebSocket:
    """A fake that completes the handshake then PARKS forever on iteration.

    No messages, no close — the receive loop awaits indefinitely. This lets
    the health-check loop win the race and raise ``_HealthCheckTimeout`` once
    the (monkeypatched, sub-second) heartbeat/timeout constants elapse.
    """

    def __init__(self, url: str = "https://tunnel.example/abc123") -> None:
        self._handshake = json.dumps({"type": "tunnel_established", "url": url})
        self._handshake_consumed = False
        self.close_code = None
        self.send = AsyncMock()
        self.close = AsyncMock()

    async def recv(self) -> str:
        if not self._handshake_consumed:
            self._handshake_consumed = True
            return self._handshake
        raise StopAsyncIteration

    def __aiter__(self) -> "_FakeSilentWebSocket":
        return self

    async def __anext__(self) -> str:
        # Park forever: never yields a message and never closes, so the
        # receive loop stays alive and the health-check loop is what ends run().
        await asyncio.Event().wait()
        raise AssertionError("unreachable")  # pragma: no cover


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(**callbacks) -> TunnelClient:
    """Construct a TunnelClient with an inert bearer token / transport.

    No requests flow (the fake closes with zero messages), so the transport is
    never touched — a MagicMock is sufficient.
    """
    return TunnelClient(
        server_url="wss://tunnel.example",
        bearer_token="test-token",
        transport=MagicMock(),
        **callbacks,
    )


# ===========================================================================
# Clean-close must terminate run()
# ===========================================================================

class TestCleanCloseTerminatesRun:
    """A clean WebSocket close (code 1000) must make ``run()`` return."""

    @pytest.mark.asyncio
    async def test_clean_close_returns_normal(self, monkeypatch) -> None:
        fake_ws = _FakeCleanCloseWebSocket()
        monkeypatch.setattr(
            client_module, "connect", AsyncMock(return_value=fake_ws)
        )

        client = _make_client()

        # 2s budget: the health-check loop sleeps on the real 30s heartbeat
        # interval, so a regression (un-cancelled health loop blocking the
        # group) trips this guard and fails fast instead of hanging the suite.
        close_code, close_reason = await asyncio.wait_for(client.run(), timeout=2)

        # This test pins "run() RETURNS / does not hang" on a clean close.
        # NORMAL here is the *hardcoded default* close_code: a silent
        # StopAsyncIteration does NOT raise ConnectionClosedOK, so neither
        # except* branch fires and the initial `close_code = NORMAL` stands.
        # The actual close-code *derivation* (reading exc.rcvd.code) is what
        # TestUncleanCloseReconnects covers below — don't infer derivation
        # coverage from this assertion.
        assert close_code == CloseCodes.NORMAL
        assert isinstance(close_reason, str)

    @pytest.mark.asyncio
    async def test_clean_close_fires_on_disconnected(self, monkeypatch) -> None:
        fake_ws = _FakeCleanCloseWebSocket()
        monkeypatch.setattr(
            client_module, "connect", AsyncMock(return_value=fake_ws)
        )

        on_disconnected = MagicMock()
        client = _make_client(on_disconnected=on_disconnected)

        await asyncio.wait_for(client.run(), timeout=2)

        # The terminal callback must fire exactly once with the normal code.
        on_disconnected.assert_called_once()
        (code, _reason), _kwargs = on_disconnected.call_args
        assert code == CloseCodes.NORMAL


# ===========================================================================
# Unclean close must still propagate the real code and stay reconnect-eligible
# ===========================================================================

class TestUncleanCloseReconnects:
    """An UNCLEAN close must surface the server's close code and reconnect.

    The disconnect-hang fix awaits ``_receive_loop`` inline and cancels the
    group scope *after* it returns. The risk this guards: that the inline-await
    rework didn't accidentally swallow the unclean path. Here the receive
    iterator RAISES ``ConnectionClosed``, so the inline ``await`` raises, the
    ``tg.cancel_scope.cancel()`` is skipped, and the exception tears the group
    down — exactly the pre-fix behaviour the reconnect manager depends on.
    """

    @pytest.mark.asyncio
    async def test_unclean_close_returns_real_code(self, monkeypatch) -> None:
        # 4008 SERVICE_UNAVAILABLE is the discriminator: the handler's else
        # fallback would yield GOING_AWAY (1001). Asserting 4008 proves the
        # code was read from exc.rcvd.code, not defaulted. 4008 is also
        # reconnect-eligible (not in _NO_RECONNECT).
        fake_ws = _FakeUncleanCloseWebSocket(
            code=CloseCodes.SERVICE_UNAVAILABLE,
            reason="Service unavailable",
        )
        monkeypatch.setattr(
            client_module, "connect", AsyncMock(return_value=fake_ws)
        )

        client = _make_client()

        close_code, close_reason = await asyncio.wait_for(client.run(), timeout=2)

        # Derived from the received Close frame, NOT the GOING_AWAY default.
        assert close_code == CloseCodes.SERVICE_UNAVAILABLE
        assert close_reason == "Service unavailable"
        # And the reconnection manager must treat it as retryable.
        assert should_reconnect(close_code) is True

    @pytest.mark.asyncio
    async def test_unclean_close_fires_on_disconnected(self, monkeypatch) -> None:
        fake_ws = _FakeUncleanCloseWebSocket(
            code=CloseCodes.SERVICE_UNAVAILABLE,
            reason="Service unavailable",
        )
        monkeypatch.setattr(
            client_module, "connect", AsyncMock(return_value=fake_ws)
        )

        on_disconnected = MagicMock()
        client = _make_client(on_disconnected=on_disconnected)

        await asyncio.wait_for(client.run(), timeout=2)

        on_disconnected.assert_called_once()
        (code, _reason), _kwargs = on_disconnected.call_args
        assert code == CloseCodes.SERVICE_UNAVAILABLE


# ===========================================================================
# Health-check timeout must end run() and stay reconnect-eligible
# ===========================================================================

class TestHealthCheckTimeoutReconnects:
    """A stalled server (no pings) must make the health loop end ``run()``.

    This exercises the sibling-cancellation / exception-group filtering the
    fix relies on: the receive loop is parked forever, so the health-check
    *sibling* is the one that raises ``_HealthCheckTimeout``. anyio cancels the
    parked receive loop, filters the resulting ``CancelledError`` at the group
    boundary, and leaves the single real exception for ``except*`` to map.
    """

    @pytest.mark.asyncio
    async def test_health_timeout_returns_reconnect_eligible(
        self, monkeypatch
    ) -> None:
        # CRITICAL: client.py does `from .protocol import HEARTBEAT_INTERVAL,
        # HEALTH_CHECK_TIMEOUT`, binding these names into the CLIENT module
        # namespace at import. Patch client_module.*, NOT protocol.* — patching
        # protocol would have zero effect and the loop would sleep the real
        # 30s, tripping the 2s guard with a misleading TimeoutError.
        monkeypatch.setattr(client_module, "HEARTBEAT_INTERVAL", 0.01)
        monkeypatch.setattr(client_module, "HEALTH_CHECK_TIMEOUT", 0.0)

        fake_ws = _FakeSilentWebSocket()
        monkeypatch.setattr(
            client_module, "connect", AsyncMock(return_value=fake_ws)
        )

        client = _make_client()

        # After one ~0.01s heartbeat sleep, elapsed (>0) exceeds the 0.0
        # timeout on the first iteration → _HealthCheckTimeout. Deterministic
        # and well under the 2s guard.
        close_code, close_reason = await asyncio.wait_for(client.run(), timeout=2)

        # _HealthCheckTimeout is not a ConnectionClosed, so the handler's else
        # branch maps it to GOING_AWAY — which is reconnect-eligible.
        assert close_code == CloseCodes.GOING_AWAY
        assert should_reconnect(close_code) is True
        assert isinstance(close_reason, str)
