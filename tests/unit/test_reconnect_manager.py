"""Regression tests for ``TunnelReconnectManager`` terminal-callback behavior.

These tests pin the central invariant of the reconnect refactor: the
``on_stopped`` callback fires **exactly once** on EVERY terminal exit of
``run()`` — clean disconnect, permanent close, exhausted retries, or an
unexpected error escaping the loop. The settings UI/log rely on this single
terminal signal to leave the transitional "Connecting…/Stop" state.

What's covered:
- Clean (NORMAL) close → on_stopped once, no reconnect.
- ``disconnect()`` during an active connection → terminal on_stopped (the
  cancel-race the refactor was built to fix).
- Permanent close code (in ``_NO_RECONNECT``) → on_stopped once, no reconnect.
- Max attempts exhausted → on_stopped once.
- Reconnect-then-clean-stop → on_stopped fires exactly once (only at the end).
- Unexpected exception in the loop → on_stopped STILL fires once (pins the
  ``run()`` try/except robustness fix — fails without it).

``TunnelClient`` and ``InMemoryTransport`` are hard-instantiated inside
``_run_loop``, so the only seams are module-level monkeypatch (intercept the
classes where ``reconnect.py`` references them) and the ``on_stopped`` callback
(observe terminal state). This mirrors the sibling ``test_tunnel_disconnect.py``
file's ``monkeypatch.setattr(client_module, "connect", ...)`` idiom.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Module aliasing – must happen before reconnect.py (and the in-memory
# transport it imports at module top) is imported. Copied verbatim from
# test_tunnel_disconnect.py: the transport uses *relative* imports
# (``from ..vendor.shared.mcp.types …``) while the MCP Server uses *absolute*
# imports (``from mcp.types …``). Python treats these as distinct module
# objects, which breaks ``isinstance`` checks inside the transport. After
# ``anki_mcp_server.__init__`` adds ``vendor/shared`` to ``sys.path`` we alias
# ``anki_mcp_server.vendor.shared.mcp.*`` onto the same objects.
# ---------------------------------------------------------------------------
import sys

import anki_mcp_server  # noqa: F401 – triggers vendor path setup

for _key, _mod in list(sys.modules.items()):
    if _key.startswith("mcp.") or _key == "mcp":
        _alias = f"anki_mcp_server.vendor.shared.{_key}"
        if _alias not in sys.modules:
            sys.modules[_alias] = _mod

# ---------------------------------------------------------------------------
# Now safe to import the manager and the rest of the MCP SDK.
# ---------------------------------------------------------------------------
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from anki_mcp_server.tunnel import reconnect as reconnect_module
from anki_mcp_server.tunnel.reconnect import TunnelReconnectManager
from anki_mcp_server.tunnel.protocol import (
    CloseCodes,
    should_reconnect,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_credentials_manager() -> MagicMock:
    """A credentials manager whose token is present and NOT expired.

    ``load()`` returns a non-None fake (with a ``user`` attr the established
    callback reads) and ``is_token_expired()`` returns ``False`` so the loop
    skips the refresh branch entirely — otherwise it would ``await`` a plain
    MagicMock and crash for the wrong reason.
    """
    creds = MagicMock()
    creds.user = {"email": "test@example.com", "tier": "free"}
    creds.refresh_token = "refresh-token"

    cm = MagicMock()
    cm.load.return_value = creds
    cm.is_token_expired.return_value = False
    return cm


def _install_fake_transport(monkeypatch) -> MagicMock:
    """Patch ``InMemoryTransport`` so ``start()``/``stop()`` are awaitable no-ops.

    Both are awaited (``start`` in the loop body, ``stop`` in ``finally``), so
    they must be AsyncMocks. Returns the instance the factory hands back.
    """
    transport = MagicMock()
    transport.start = AsyncMock()
    transport.stop = AsyncMock()
    monkeypatch.setattr(
        reconnect_module, "InMemoryTransport", MagicMock(return_value=transport)
    )
    return transport


def _install_fake_client(monkeypatch, *, run_side_effect) -> MagicMock:
    """Patch ``TunnelClient`` so every construction yields the SAME mock client.

    ``_run_loop`` constructs ``TunnelClient(...)`` fresh each iteration, but a
    factory with a fixed ``return_value`` hands back one shared instance, so we
    can drive multi-iteration behavior via ``run``'s ``side_effect`` and assert
    on ``run.call_count``.

    Args:
        run_side_effect: A list of ``(code, reason)`` tuples (one per
            iteration) or an exception instance/class to raise.
    """
    client = MagicMock()
    client.run = AsyncMock(side_effect=run_side_effect)
    client.disconnect = AsyncMock()
    monkeypatch.setattr(
        reconnect_module, "TunnelClient", MagicMock(return_value=client)
    )
    return client


def _make_manager(**overrides) -> TunnelReconnectManager:
    """Construct a manager with inert deps and the given callback overrides.

    ``auth`` is a MagicMock with an AsyncMock ``refresh_token`` (unused by tests
    that keep the token non-expired and use non-auth close codes, but harmless).
    """
    auth = MagicMock()
    auth.refresh_token = AsyncMock()

    kwargs: dict = {
        "server_url": "wss://tunnel.example",
        "mcp_server": MagicMock(),
        "credentials_manager": _make_credentials_manager(),
        "auth": auth,
    }
    kwargs.update(overrides)
    return TunnelReconnectManager(**kwargs)


# ===========================================================================
# 1. Clean disconnect → on_stopped once with NORMAL, no reconnect
# ===========================================================================

class TestCleanDisconnect:
    @pytest.mark.asyncio
    async def test_clean_close_fires_on_stopped_once_no_reconnect(
        self, monkeypatch
    ) -> None:
        _install_fake_transport(monkeypatch)
        client = _install_fake_client(
            monkeypatch,
            run_side_effect=[(CloseCodes.NORMAL, "Disconnected by user")],
        )

        on_stopped = MagicMock()
        on_reconnecting = MagicMock()
        manager = _make_manager(
            on_stopped=on_stopped, on_reconnecting=on_reconnecting
        )

        await asyncio.wait_for(manager.run(), timeout=2)

        # Terminal callback fires exactly once, carrying the NORMAL code.
        on_stopped.assert_called_once()
        (code, _reason), _kwargs = on_stopped.call_args
        assert code == CloseCodes.NORMAL

        # NORMAL is in _NO_RECONNECT → exactly one connection attempt, no
        # backoff/reconnect.
        assert client.run.call_count == 1
        on_reconnecting.assert_not_called()


# ===========================================================================
# 2. disconnect() during an active connection → terminal on_stopped
#    (the cancel-race regression)
# ===========================================================================

class TestDisconnectDuringActiveConnection:
    @pytest.mark.asyncio
    async def test_disconnect_unblocks_and_fires_terminal_on_stopped(
        self, monkeypatch
    ) -> None:
        _install_fake_transport(monkeypatch)

        started = asyncio.Event()
        unblock = asyncio.Event()

        # A small real fake (not a bare MagicMock): run() parks until
        # disconnect() unblocks it, then returns NORMAL — mirroring how
        # manager.disconnect() → client.disconnect() unblocks a live
        # client.run() in production.
        class _BlockingClient:
            async def run(self) -> tuple[int, str]:
                started.set()
                await unblock.wait()
                return (CloseCodes.NORMAL, "Disconnected by user")

            async def disconnect(self) -> None:
                unblock.set()

        client = _BlockingClient()
        monkeypatch.setattr(
            reconnect_module, "TunnelClient", MagicMock(return_value=client)
        )

        on_stopped = MagicMock()
        manager = _make_manager(on_stopped=on_stopped)

        task = asyncio.create_task(manager.run())
        await asyncio.wait_for(started.wait(), timeout=2)

        # disconnect() sets _shutdown and unblocks the parked client.run().
        await manager.disconnect()

        # The 2s budget is the regression guard: if the loop fails to reach
        # its terminal branch (the original hang), wait_for trips here.
        await asyncio.wait_for(task, timeout=2)

        on_stopped.assert_called_once()
        (code, _reason), _kwargs = on_stopped.call_args
        assert code == CloseCodes.NORMAL


# ===========================================================================
# 3. Permanent (non-NORMAL) close → on_stopped once, no reconnect
# ===========================================================================

class TestPermanentClose:
    @pytest.mark.asyncio
    async def test_permanent_close_fires_on_stopped_once_no_reconnect(
        self, monkeypatch
    ) -> None:
        _install_fake_transport(monkeypatch)

        # TOKEN_REVOKED is in _NO_RECONNECT → permanent, must not reconnect.
        assert should_reconnect(CloseCodes.TOKEN_REVOKED) is False

        client = _install_fake_client(
            monkeypatch,
            run_side_effect=[(CloseCodes.TOKEN_REVOKED, "Token revoked")],
        )

        on_stopped = MagicMock()
        on_reconnecting = MagicMock()
        manager = _make_manager(
            on_stopped=on_stopped, on_reconnecting=on_reconnecting
        )

        await asyncio.wait_for(manager.run(), timeout=2)

        on_stopped.assert_called_once()
        (code, _reason), _kwargs = on_stopped.call_args
        assert code == CloseCodes.TOKEN_REVOKED

        assert client.run.call_count == 1
        on_reconnecting.assert_not_called()


# ===========================================================================
# 4. Max attempts exhausted → on_stopped once, loop stops
# ===========================================================================

class TestMaxAttemptsExhausted:
    @pytest.mark.asyncio
    async def test_transient_drops_exhaust_attempts_and_stop(
        self, monkeypatch
    ) -> None:
        _install_fake_transport(monkeypatch)

        # Shrink the attempt budget and zero the backoff so the test is fast
        # and deterministic. Patch reconnect_module.* — reconnect.py binds the
        # constant via `from .protocol import RECONNECT_MAX_ATTEMPTS`, so
        # patching protocol.* would have zero effect (same lesson the sibling
        # file documents for HEARTBEAT_INTERVAL).
        monkeypatch.setattr(reconnect_module, "RECONNECT_MAX_ATTEMPTS", 3)

        # SERVICE_UNAVAILABLE (4008) is reconnect-eligible (not in
        # _NO_RECONNECT, not an auth-refresh code) → pure transient retry.
        assert should_reconnect(CloseCodes.SERVICE_UNAVAILABLE) is True

        client = _install_fake_client(
            monkeypatch,
            # Always transient: the loop must stop on the attempt limit, not a
            # close code. More entries than the budget so we never run dry.
            run_side_effect=[
                (CloseCodes.SERVICE_UNAVAILABLE, "Service unavailable")
                for _ in range(10)
            ],
        )

        # Zero the backoff delay so anyio.sleep returns instantly.
        on_stopped = MagicMock()
        manager = _make_manager(on_stopped=on_stopped)
        monkeypatch.setattr(manager, "_calculate_delay", lambda attempt: 0.0)

        await asyncio.wait_for(manager.run(), timeout=2)

        # Terminal callback fires exactly once when the budget is exhausted.
        on_stopped.assert_called_once()
        (code, _reason), _kwargs = on_stopped.call_args
        # Exhaustion path returns sentinel code 0 (see _run_loop).
        assert code == 0

        # The loop connected exactly RECONNECT_MAX_ATTEMPTS times, then gave up.
        assert client.run.call_count == 3


# ===========================================================================
# 5. Reconnect then clean stop → on_stopped fires EXACTLY once (only terminal)
# ===========================================================================

class TestReconnectThenCleanStop:
    @pytest.mark.asyncio
    async def test_transient_drop_then_normal_fires_on_stopped_once(
        self, monkeypatch
    ) -> None:
        _install_fake_transport(monkeypatch)

        client = _install_fake_client(
            monkeypatch,
            run_side_effect=[
                # First connection drops with a reconnect-eligible code...
                (CloseCodes.SERVICE_UNAVAILABLE, "Service unavailable"),
                # ...reconnect, then a clean user-initiated close.
                (CloseCodes.NORMAL, "Disconnected by user"),
            ],
        )

        on_stopped = MagicMock()
        on_reconnecting = MagicMock()
        manager = _make_manager(
            on_stopped=on_stopped, on_reconnecting=on_reconnecting
        )
        monkeypatch.setattr(manager, "_calculate_delay", lambda attempt: 0.0)

        await asyncio.wait_for(manager.run(), timeout=2)

        # Two connection attempts: the drop and the clean close.
        assert client.run.call_count == 2

        # on_stopped fires ONCE total — only at the terminal NORMAL close, NOT
        # on the intermediate transient drop. This is the no-double-fire pin.
        on_stopped.assert_called_once()
        (code, _reason), _kwargs = on_stopped.call_args
        assert code == CloseCodes.NORMAL

        # The manager DOES control on_reconnecting: it fires once, before the
        # single backoff between the drop and the reconnect. (on_disconnected
        # is forwarded into TunnelClient and never fires at the manager level
        # with the client mocked — so we do not assert it here.)
        on_reconnecting.assert_called_once()


# ===========================================================================
# 6. Robustness: unexpected exception in the loop STILL fires on_stopped
#    (pins the run() try/except — fails without it)
# ===========================================================================

class TestUnexpectedErrorStillFiresOnStopped:
    @pytest.mark.asyncio
    async def test_runtime_error_in_loop_still_fires_on_stopped_once(
        self, monkeypatch
    ) -> None:
        _install_fake_transport(monkeypatch)

        # An unexpected error that _run_loop does NOT catch (it only handles
        # TunnelConnectionError / AuthError). client.run() raising RuntimeError
        # would escape _run_loop entirely — without run()'s try/except, the
        # _fire_callback(on_stopped, ...) line is skipped and this test fails.
        _install_fake_client(
            monkeypatch,
            run_side_effect=RuntimeError("boom"),
        )

        on_stopped = MagicMock()
        manager = _make_manager(on_stopped=on_stopped)

        # run() must NOT propagate the RuntimeError — it converts it to a
        # terminal stop. (If it raised, wait_for would surface the RuntimeError
        # and the assert_called_once below would never run.)
        await asyncio.wait_for(manager.run(), timeout=2)

        on_stopped.assert_called_once()
        (code, _reason), _kwargs = on_stopped.call_args
        # Robustness path uses sentinel code 0 (non-NORMAL).
        assert code == 0
        assert code != CloseCodes.NORMAL

    @pytest.mark.asyncio
    async def test_credentials_load_raising_still_fires_on_stopped(
        self, monkeypatch
    ) -> None:
        # The error need not come from the client — anything escaping the loop
        # must still reach on_stopped. Here credentials_manager.load() (called
        # at the very top of each loop iteration) raises before any client is
        # ever constructed.
        _install_fake_transport(monkeypatch)
        _install_fake_client(monkeypatch, run_side_effect=[(CloseCodes.NORMAL, "")])

        cm = _make_credentials_manager()
        cm.load.side_effect = RuntimeError("disk on fire")

        on_stopped = MagicMock()
        manager = _make_manager(credentials_manager=cm, on_stopped=on_stopped)

        await asyncio.wait_for(manager.run(), timeout=2)

        on_stopped.assert_called_once()
        (code, _reason), _kwargs = on_stopped.call_args
        assert code == 0
