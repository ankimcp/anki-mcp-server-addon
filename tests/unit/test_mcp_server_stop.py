"""Unit tests for McpServer.stop() shutdown ordering and invariants.

Approach: Option A (mock event loop).

We replace ``self._loop`` with a MagicMock before calling ``stop()`` so that
``call_soon_threadsafe`` and ``asyncio.run_coroutine_threadsafe`` never touch a
real event loop.  This keeps the tests fully synchronous, deterministic, and
independent of OS thread scheduling.

Option B (real loop in a thread) would require us to actually join a thread
and await coroutines, which adds ~100ms of sleep/join overhead and makes
ordering assertions racy.  Option A is cleaner here because the invariants we
care about are *scheduling* invariants (was the coroutine enqueued before the
flag flip?) not *execution* invariants (did the tunnel actually disconnect?).

The key ordering fact from the source:
  1. ``stop_tunnel()``     → ``asyncio.run_coroutine_threadsafe(_stop_tunnel_async(), loop)``
  2. uvicorn shutdown      → ``loop.call_soon_threadsafe(lambda: setattr(server, "should_exit", True))``
  3. async_shutdown signal → ``loop.call_soon_threadsafe(async_shutdown.set)``

These use two *different* scheduling primitives, so we patch both and track
their call order via a shared ``call_order`` list.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# conftest.py installs aqt + primitives stubs before this module is collected,
# so the addon import below is safe even without a running Anki.
from anki_mcp_server.config import Config
from anki_mcp_server.mcp_server import McpServer
from anki_mcp_server.queue_bridge import QueueBridge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _consume_coro(coro: Any, _loop: Any = None) -> MagicMock:
    """Side-effect for ``asyncio.run_coroutine_threadsafe``.

    Closes the coroutine so Python's GC doesn't emit ``RuntimeWarning:
    coroutine '...' was never awaited`` when the test finishes.
    """
    if asyncio.iscoroutine(coro):
        coro.close()
    return MagicMock()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def config() -> Config:
    return Config(http_port=3141, http_host="127.0.0.1")


@pytest.fixture()
def config_no_http() -> Config:
    return Config(http_enabled=False)


@pytest.fixture()
def bridge() -> MagicMock:
    return MagicMock(spec=QueueBridge)


@pytest.fixture()
def mock_loop() -> MagicMock:
    # is_closed=False so stop() enters the shutdown branch.
    loop = MagicMock(spec=asyncio.AbstractEventLoop)
    loop.is_closed.return_value = False
    return loop


@pytest.fixture()
def mock_uvicorn_server() -> MagicMock:
    srv = MagicMock()
    srv.should_exit = False
    return srv


@pytest.fixture()
def mock_thread() -> MagicMock:
    t = MagicMock(spec=threading.Thread)
    t.is_alive.return_value = True
    return t


@pytest.fixture()
def server_http(bridge: MagicMock, config: Config) -> McpServer:
    s = McpServer(bridge, config)
    # Replace the real ThreadPoolExecutor so .shutdown() is observable
    # without actually allocating worker threads.
    s._executor = MagicMock(spec=ThreadPoolExecutor)
    return s


@pytest.fixture()
def server_no_http(bridge: MagicMock, config_no_http: Config) -> McpServer:
    s = McpServer(bridge, config_no_http)
    s._executor = MagicMock(spec=ThreadPoolExecutor)
    return s


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _inject_running_state(
    server: McpServer,
    loop: MagicMock,
    uvicorn_server: MagicMock | None,
    thread: MagicMock | None,
    *,
    tunnel_running: bool = False,
    tunnel_task: Any = None,
    tunnel_manager: Any = None,
    async_shutdown: Any = None,
) -> None:
    """Simulate a fully started McpServer without spinning a real thread."""
    server._loop = loop
    server._uvicorn_server = uvicorn_server
    server._thread = thread
    server._tunnel_running = tunnel_running
    server._tunnel_task = tunnel_task
    server._tunnel_manager = tunnel_manager
    server._async_shutdown = async_shutdown


# ---------------------------------------------------------------------------
# 1. Ordering invariant — tunnel scheduled before uvicorn shutdown signal
# ---------------------------------------------------------------------------

class TestStopOrdering:

    def test_tunnel_teardown_scheduled_before_uvicorn_signal(
        self,
        server_http: McpServer,
        mock_loop: MagicMock,
        mock_uvicorn_server: MagicMock,
        mock_thread: MagicMock,
    ) -> None:
        """stop_tunnel() is called before loop.call_soon_threadsafe(should_exit)."""
        call_order: list[str] = []

        fake_task = MagicMock(spec=asyncio.Task)
        fake_task.done.return_value = False

        _inject_running_state(
            server_http,
            mock_loop,
            mock_uvicorn_server,
            mock_thread,
            tunnel_running=True,
            tunnel_task=fake_task,
        )

        # Patch run_coroutine_threadsafe so stop_tunnel() doesn't need a real loop,
        # and record the order of the two scheduling primitives.
        with patch("anki_mcp_server.mcp_server.asyncio.run_coroutine_threadsafe") as mock_rcf:
            def _record_rcf(coro: Any, _loop: Any) -> MagicMock:
                _consume_coro(coro)
                call_order.append("tunnel_teardown")
                return MagicMock()

            mock_rcf.side_effect = _record_rcf
            mock_loop.call_soon_threadsafe.side_effect = (
                lambda *args, **kwargs: call_order.append("call_soon_threadsafe")
            )

            server_http.stop()

        assert call_order[0] == "tunnel_teardown", f"Expected tunnel first, got: {call_order}"
        assert "call_soon_threadsafe" in call_order
        tunnel_idx = call_order.index("tunnel_teardown")
        uvicorn_idx = call_order.index("call_soon_threadsafe")
        assert tunnel_idx < uvicorn_idx, (
            f"Tunnel teardown ({tunnel_idx}) must precede uvicorn signal ({uvicorn_idx})"
        )

    def test_uvicorn_signal_scheduled_before_thread_join(
        self,
        server_http: McpServer,
        mock_loop: MagicMock,
        mock_uvicorn_server: MagicMock,
        mock_thread: MagicMock,
    ) -> None:
        """loop.call_soon_threadsafe is called before thread.join()."""
        call_order: list[str] = []

        _inject_running_state(server_http, mock_loop, mock_uvicorn_server, mock_thread)
        mock_loop.call_soon_threadsafe.side_effect = (
            lambda *args, **kwargs: call_order.append("call_soon_threadsafe")
        )
        mock_thread.join.side_effect = lambda *args, **kwargs: call_order.append("thread_join")

        server_http.stop()

        assert "call_soon_threadsafe" in call_order
        assert "thread_join" in call_order
        assert call_order.index("call_soon_threadsafe") < call_order.index("thread_join")


# ---------------------------------------------------------------------------
# 2. Side-effect invariant — executor.shutdown called exactly once
# ---------------------------------------------------------------------------

class TestExecutorShutdown:

    def test_executor_shutdown_called_once(
        self,
        server_http: McpServer,
        mock_loop: MagicMock,
        mock_uvicorn_server: MagicMock,
        mock_thread: MagicMock,
    ) -> None:
        _inject_running_state(server_http, mock_loop, mock_uvicorn_server, mock_thread)

        server_http.stop()

        server_http._executor.shutdown.assert_called_once_with(wait=False)

    def test_executor_shutdown_called_even_when_loop_is_none(
        self,
        server_http: McpServer,
        mock_thread: MagicMock,
    ) -> None:
        """_executor.shutdown is unconditional, even when stop() fires before start() completed."""
        # _loop stays None — simulates stop() called before the bg thread set it.
        server_http._thread = mock_thread
        mock_thread.is_alive.return_value = False  # no join needed

        server_http.stop()

        server_http._executor.shutdown.assert_called_once_with(wait=False)


# ---------------------------------------------------------------------------
# 3. Thread join invariant
# ---------------------------------------------------------------------------

class TestThreadJoin:

    def test_thread_joined_with_timeout(
        self,
        server_http: McpServer,
        mock_loop: MagicMock,
        mock_uvicorn_server: MagicMock,
        mock_thread: MagicMock,
    ) -> None:
        _inject_running_state(server_http, mock_loop, mock_uvicorn_server, mock_thread)

        server_http.stop()

        mock_thread.join.assert_called_once_with(timeout=3.0)

    def test_thread_join_skipped_when_not_alive(
        self,
        server_http: McpServer,
        mock_loop: MagicMock,
        mock_uvicorn_server: MagicMock,
    ) -> None:
        dead_thread = MagicMock(spec=threading.Thread)
        dead_thread.is_alive.return_value = False

        _inject_running_state(server_http, mock_loop, mock_uvicorn_server, dead_thread)

        server_http.stop()

        dead_thread.join.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Tunnel teardown guard — exercises both branches of the OR
# ---------------------------------------------------------------------------

class TestTunnelTeardownGuard:
    """Cover ``if self._tunnel_running or self._tunnel_task is not None``."""

    def test_no_tunnel_state_skips_stop_tunnel(
        self,
        server_http: McpServer,
        mock_loop: MagicMock,
        mock_uvicorn_server: MagicMock,
        mock_thread: MagicMock,
    ) -> None:
        """Both flags at __init__ defaults → guard is False → stop_tunnel() not called."""
        _inject_running_state(server_http, mock_loop, mock_uvicorn_server, mock_thread)

        with patch.object(server_http, "stop_tunnel") as mock_stop_tunnel:
            server_http.stop()

        mock_stop_tunnel.assert_not_called()

    def test_running_flag_alone_triggers_stop_tunnel(
        self,
        server_http: McpServer,
        mock_loop: MagicMock,
        mock_uvicorn_server: MagicMock,
        mock_thread: MagicMock,
    ) -> None:
        """_tunnel_running=True with _tunnel_task=None still triggers teardown."""
        _inject_running_state(
            server_http,
            mock_loop,
            mock_uvicorn_server,
            mock_thread,
            tunnel_running=True,
            tunnel_task=None,
        )

        with patch.object(server_http, "stop_tunnel") as mock_stop_tunnel:
            server_http.stop()

        mock_stop_tunnel.assert_called_once()

    def test_task_alone_triggers_stop_tunnel(
        self,
        server_http: McpServer,
        mock_loop: MagicMock,
        mock_uvicorn_server: MagicMock,
        mock_thread: MagicMock,
    ) -> None:
        """_tunnel_task set with _tunnel_running=False still triggers teardown.

        Covers the rhs of ``self._tunnel_running or self._tunnel_task is not None``,
        which is the race window between task creation and the
        on_tunnel_established callback flipping _tunnel_running.
        """
        fake_task = MagicMock(spec=asyncio.Task)
        fake_task.done.return_value = False

        _inject_running_state(
            server_http,
            mock_loop,
            mock_uvicorn_server,
            mock_thread,
            tunnel_running=False,
            tunnel_task=fake_task,
        )

        with patch.object(server_http, "stop_tunnel") as mock_stop_tunnel:
            server_http.stop()

        mock_stop_tunnel.assert_called_once()

    def test_stop_tunnel_runtime_error_does_not_propagate(
        self,
        server_http: McpServer,
        mock_loop: MagicMock,
        mock_uvicorn_server: MagicMock,
        mock_thread: MagicMock,
    ) -> None:
        """Source wraps stop_tunnel() in try/except RuntimeError — verify the catch."""
        _inject_running_state(
            server_http,
            mock_loop,
            mock_uvicorn_server,
            mock_thread,
            tunnel_running=True,
        )

        with patch.object(server_http, "stop_tunnel", side_effect=RuntimeError("loop dead")):
            server_http.stop()  # must not raise

        # Downstream cleanup must still happen.
        server_http._executor.shutdown.assert_called_once_with(wait=False)


# ---------------------------------------------------------------------------
# 5. No-HTTP path — stop() skips uvicorn signal when _uvicorn_server is None
# ---------------------------------------------------------------------------

class TestNoHttpPath:

    def test_stop_without_uvicorn_skips_should_exit_scheduling(
        self,
        server_no_http: McpServer,
        mock_loop: MagicMock,
        mock_thread: MagicMock,
    ) -> None:
        """When _uvicorn_server is None, only the async_shutdown.set call is scheduled.

        The first try/except block in stop() is guarded by ``if server is not None``;
        verify it is skipped entirely (exactly one call_soon_threadsafe, for
        async_shutdown.set), not just that no AttributeError leaks.
        """
        fake_async_shutdown = MagicMock(spec=asyncio.Event)

        _inject_running_state(
            server_no_http,
            mock_loop,
            uvicorn_server=None,
            thread=mock_thread,
            async_shutdown=fake_async_shutdown,
        )

        server_no_http.stop()

        # Exactly one scheduled callback (the async_shutdown.set), proving the
        # uvicorn-specific block was skipped.
        assert mock_loop.call_soon_threadsafe.call_count == 1, (
            f"Expected exactly 1 call_soon_threadsafe, got "
            f"{mock_loop.call_soon_threadsafe.call_count}"
        )

        # And the scheduled callback must be async_shutdown.set itself.
        scheduled_fn = mock_loop.call_soon_threadsafe.call_args.args[0]
        assert scheduled_fn is fake_async_shutdown.set

    def test_stop_without_uvicorn_signals_async_shutdown(
        self,
        server_no_http: McpServer,
        mock_loop: MagicMock,
        mock_thread: MagicMock,
    ) -> None:
        """async_shutdown.set is scheduled so _async_main() wakes up."""
        fake_async_shutdown = MagicMock(spec=asyncio.Event)

        _inject_running_state(
            server_no_http,
            mock_loop,
            uvicorn_server=None,
            thread=mock_thread,
            async_shutdown=fake_async_shutdown,
        )

        server_no_http.stop()

        mock_loop.call_soon_threadsafe.assert_called()
        # Invoke every scheduled callback to assert the .set() side effect.
        for c in mock_loop.call_soon_threadsafe.call_args_list:
            c.args[0]()
        fake_async_shutdown.set.assert_called()

    def test_stop_without_uvicorn_no_attribute_error(
        self,
        server_no_http: McpServer,
        mock_loop: MagicMock,
        mock_thread: MagicMock,
    ) -> None:
        _inject_running_state(server_no_http, mock_loop, uvicorn_server=None, thread=mock_thread)

        server_no_http.stop()  # must not raise

    def test_stop_without_uvicorn_still_shuts_down_executor(
        self,
        server_no_http: McpServer,
        mock_loop: MagicMock,
        mock_thread: MagicMock,
    ) -> None:
        """executor.shutdown is unconditional — runs even on the no-HTTP path."""
        _inject_running_state(server_no_http, mock_loop, uvicorn_server=None, thread=mock_thread)

        server_no_http.stop()

        server_no_http._executor.shutdown.assert_called_once_with(wait=False)


# ---------------------------------------------------------------------------
# 6. State reset — _loop, _uvicorn_server, _thread cleared after stop()
# ---------------------------------------------------------------------------

class TestStateReset:

    def test_loop_cleared_after_stop(
        self,
        server_http: McpServer,
        mock_loop: MagicMock,
        mock_uvicorn_server: MagicMock,
        mock_thread: MagicMock,
    ) -> None:
        """A subsequent start() must see a clean _loop slot."""
        _inject_running_state(server_http, mock_loop, mock_uvicorn_server, mock_thread)

        server_http.stop()

        assert server_http._loop is None

    def test_uvicorn_server_cleared_after_stop(
        self,
        server_http: McpServer,
        mock_loop: MagicMock,
        mock_uvicorn_server: MagicMock,
        mock_thread: MagicMock,
    ) -> None:
        """Prevents the stale handle from leaking into the next profile switch."""
        _inject_running_state(server_http, mock_loop, mock_uvicorn_server, mock_thread)

        server_http.stop()

        assert server_http._uvicorn_server is None

    def test_thread_ref_cleared_after_stop(
        self,
        server_http: McpServer,
        mock_loop: MagicMock,
        mock_uvicorn_server: MagicMock,
        mock_thread: MagicMock,
    ) -> None:
        """Next start() must be able to assign a fresh thread without races."""
        _inject_running_state(server_http, mock_loop, mock_uvicorn_server, mock_thread)

        server_http.stop()

        assert server_http._thread is None


# ---------------------------------------------------------------------------
# 7. Loop already closed / RuntimeError — swallowed by both try/except blocks
# ---------------------------------------------------------------------------

class TestRuntimeErrorSwallowed:
    """Cover both ``try: loop.call_soon_threadsafe(...) except RuntimeError`` blocks.

    The source has *two* such guards — one around ``setattr(server, "should_exit", True)``
    and one around ``async_shutdown.set``. Each must catch independently.
    """

    def test_closed_loop_does_not_propagate(
        self,
        server_http: McpServer,
        mock_uvicorn_server: MagicMock,
        mock_thread: MagicMock,
    ) -> None:
        """is_closed=True → stop() skips the entire shutdown branch, no error leaks."""
        closed_loop = MagicMock(spec=asyncio.AbstractEventLoop)
        closed_loop.is_closed.return_value = True

        _inject_running_state(server_http, closed_loop, mock_uvicorn_server, mock_thread)

        server_http.stop()  # must not raise

    def test_uvicorn_signal_runtime_error_swallowed(
        self,
        server_http: McpServer,
        mock_loop: MagicMock,
        mock_uvicorn_server: MagicMock,
        mock_thread: MagicMock,
    ) -> None:
        """First try/except: RuntimeError from the should_exit lambda is caught."""
        mock_loop.call_soon_threadsafe.side_effect = RuntimeError("Event loop is closed")

        _inject_running_state(server_http, mock_loop, mock_uvicorn_server, mock_thread)

        server_http.stop()  # must not raise

        # executor.shutdown must still run despite the error.
        server_http._executor.shutdown.assert_called_once_with(wait=False)

    def test_async_shutdown_signal_runtime_error_swallowed(
        self,
        server_no_http: McpServer,
        mock_loop: MagicMock,
        mock_thread: MagicMock,
    ) -> None:
        """Second try/except: RuntimeError from async_shutdown.set scheduling is caught.

        ``server`` is None so the first block is skipped entirely, isolating
        the second block.
        """
        fake_async_shutdown = MagicMock(spec=asyncio.Event)
        mock_loop.call_soon_threadsafe.side_effect = RuntimeError("Event loop is closed")

        _inject_running_state(
            server_no_http,
            mock_loop,
            uvicorn_server=None,
            thread=mock_thread,
            async_shutdown=fake_async_shutdown,
        )

        server_no_http.stop()  # must not raise

        # Verify the second block was actually reached.
        mock_loop.call_soon_threadsafe.assert_called_once()
        server_no_http._executor.shutdown.assert_called_once_with(wait=False)
