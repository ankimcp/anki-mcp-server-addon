"""Unit tests for RequestProcessor event-driven dispatch.

Tests the waker/drain mechanics directly, without Anki or Docker: a real
QueueBridge plus an injected fake ``schedule_on_main`` replace the
``mw.taskman.run_on_main`` path. The handler registry's ``execute`` is
monkeypatched (real handlers touch mw.col).

Two fake schedulers drive the tests:
- ImmediateScheduler runs the drain closure synchronously inside the waker,
  i.e. inline within send_request()'s calling thread BEFORE it blocks — so
  the response is already queued when send_request() gets to its get().
- DeferredScheduler collects closures for manual invocation, which lets
  tests observe coalescing (one drain handles a burst) and the
  stop-then-late-callback shutdown race.

One test exercises the DEFAULT scheduler path instead of injecting: a fake
``mw`` installed on the stubbed ``aqt`` module (conftest's ``install_mw``)
proves the lazy ``mw.taskman.run_on_main`` resolution actually works.
"""

import threading
import time
import types

import pytest

from anki_mcp_server.queue_bridge import QueueBridge, ToolRequest, ToolResponse
from anki_mcp_server.request_processor import RequestProcessor


class ImmediateScheduler:
    """Fake schedule_on_main that runs the closure synchronously."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, closure) -> None:
        self.calls += 1
        closure()


class DeferredScheduler:
    """Fake schedule_on_main that collects closures for manual invocation."""

    def __init__(self) -> None:
        self.closures: list = []
        self._lock = threading.Lock()

    def __call__(self, closure) -> None:
        with self._lock:
            self.closures.append(closure)

    def __len__(self) -> int:
        with self._lock:
            return len(self.closures)


def _make_request(request_id: str, tool_name: str = "test") -> ToolRequest:
    return ToolRequest(request_id=request_id, tool_name=tool_name, arguments={})


@pytest.fixture()
def fake_execute(monkeypatch):
    """Stub the handler registry: echo the tool name, record calls."""
    calls: list[str] = []

    def _execute(tool_name, arguments):
        calls.append(tool_name)
        return f"result:{tool_name}"

    monkeypatch.setattr("anki_mcp_server.request_processor.execute", _execute)
    return calls


class TestActiveDispatch:
    """Requests enqueued while active are dispatched immediately."""

    def test_request_dispatched_via_waker(self, fake_execute):
        """put -> wake -> drain -> response, all inline with a sync scheduler.

        With ImmediateScheduler the waker runs the drain synchronously inside
        send_request()'s calling thread, before it blocks — so the response
        is already in the per-request queue when get() runs and send_request
        returns without ever waiting.
        """
        bridge = QueueBridge()
        scheduler = ImmediateScheduler()
        processor = RequestProcessor(bridge, schedule_on_main=scheduler)
        processor.start()

        response = bridge.send_request(_make_request("r1", "list_decks"))

        assert response.success is True
        assert response.request_id == "r1"
        assert response.result == "result:list_decks"
        assert fake_execute == ["list_decks"]

    def test_multiple_sequential_requests(self, fake_execute):
        bridge = QueueBridge()
        processor = RequestProcessor(bridge, schedule_on_main=ImmediateScheduler())
        processor.start()

        for i in range(3):
            response = bridge.send_request(_make_request(f"r{i}", f"tool_{i}"))
            assert response.success is True
            assert response.result == f"result:tool_{i}"

        assert fake_execute == ["tool_0", "tool_1", "tool_2"]

    def test_handler_exception_returns_error_response(self, monkeypatch):
        def _boom(tool_name, arguments):
            raise ValueError("handler exploded")

        monkeypatch.setattr("anki_mcp_server.request_processor.execute", _boom)

        bridge = QueueBridge()
        processor = RequestProcessor(bridge, schedule_on_main=ImmediateScheduler())
        processor.start()

        response = bridge.send_request(_make_request("r1"))

        assert response.success is False
        assert "handler exploded" in response.error


class TestBurstCoalescing:
    """N wakes before any drain runs: one drain does all the work."""

    def test_one_drain_handles_burst_and_rest_are_noops(self, fake_execute):
        bridge = QueueBridge()
        scheduler = DeferredScheduler()
        processor = RequestProcessor(bridge, schedule_on_main=scheduler)
        processor.start()
        assert len(scheduler) == 1  # initial drain scheduled by start()

        num_requests = 5
        results: dict[str, ToolResponse] = {}
        lock = threading.Lock()

        def sender(rid: str, tool: str):
            resp = bridge.send_request(_make_request(rid, tool))
            with lock:
                results[rid] = resp

        threads = [
            threading.Thread(target=sender, args=(f"r{i}", f"tool_{i}"))
            for i in range(num_requests)
        ]
        for t in threads:
            t.start()

        # Wait until every sender has enqueued + fired its wake.
        for _ in range(1000):
            if len(scheduler) == 1 + num_requests:
                break
            time.sleep(0.005)
        assert len(scheduler) == 1 + num_requests

        # ONE collected closure drains all N requests.
        scheduler.closures[1]()
        for t in threads:
            t.join(timeout=10)
        assert all(not t.is_alive() for t in threads)

        assert len(results) == num_requests
        for i in range(num_requests):
            assert results[f"r{i}"].success is True
            assert results[f"r{i}"].result == f"result:tool_{i}"
        assert sorted(fake_execute) == sorted(f"tool_{i}" for i in range(num_requests))

        # The remaining collected closures find an empty queue: cheap no-ops.
        for closure in scheduler.closures:
            closure()
        assert len(fake_execute) == num_requests  # nothing executed twice


class TestShutdownRace:
    """Late drain callbacks after stop() must be harmless no-ops."""

    def test_late_callback_after_stop_does_not_touch_queue(self, fake_execute):
        bridge = QueueBridge()
        scheduler = DeferredScheduler()
        processor = RequestProcessor(bridge, schedule_on_main=scheduler)
        processor.start()
        late_drain = scheduler.closures[0]

        processor.stop()

        # A request sitting in the queue (enqueued directly to avoid blocking
        # on send_request — no consumer is active).
        bridge.request_queue.put(_make_request("r1", "orphan"))

        # Qt delivering an already-queued callback after stop(): no crash,
        # no execution, request stays queued.
        late_drain()

        assert fake_execute == []
        pending = bridge.get_pending_request()
        assert pending is not None
        assert pending.request_id == "r1"


class TestStartStopIdempotence:
    """start()/stop() are safe to call twice, in any order."""

    def test_double_start_registers_waker_once(self, fake_execute):
        bridge = QueueBridge()
        scheduler = DeferredScheduler()
        processor = RequestProcessor(bridge, schedule_on_main=scheduler)

        processor.start()
        processor.start()

        # Second start is a no-op: exactly one initial drain, one waker.
        assert len(scheduler) == 1
        assert bridge._waker is not None

    def test_double_stop_is_safe(self):
        bridge = QueueBridge()
        processor = RequestProcessor(bridge, schedule_on_main=DeferredScheduler())
        processor.start()

        processor.stop()
        processor.stop()

        assert bridge._waker is None

    def test_stop_before_start_is_safe(self):
        bridge = QueueBridge()
        processor = RequestProcessor(bridge, schedule_on_main=DeferredScheduler())

        processor.stop()  # never started — must not crash

        assert bridge._waker is None

    def test_initial_drain_picks_up_requests_enqueued_while_stopped(
        self, fake_execute
    ):
        """Requests enqueued with no waker registered are drained by start()."""
        bridge = QueueBridge()
        result_holder: list[ToolResponse] = []

        def sender():
            result_holder.append(bridge.send_request(_make_request("r1", "early")))

        # No processor running: send_request enqueues, wakes nobody, blocks.
        t = threading.Thread(target=sender)
        t.start()
        for _ in range(1000):
            if not bridge.request_queue.empty():
                break
            time.sleep(0.005)
        assert not bridge.request_queue.empty()

        # start() with a synchronous scheduler runs the initial drain inline.
        processor = RequestProcessor(bridge, schedule_on_main=ImmediateScheduler())
        processor.start()

        t.join(timeout=10)
        assert not t.is_alive()
        assert len(result_holder) == 1
        assert result_holder[0].success is True
        assert result_holder[0].result == "result:early"


class TestRestart:
    """stop() -> start() on the SAME instance must fully re-arm dispatch."""

    def test_stop_then_start_reregisters_waker_and_drains(self, fake_execute):
        """Restart re-registers the waker, schedules a fresh initial drain,
        and picks up a request enqueued while stopped.

        Guards against a future refactor making start() one-shot (e.g. an
        "already initialized" early-return that skips waker registration or
        the initial drain on the second start).
        """
        bridge = QueueBridge()
        scheduler = DeferredScheduler()
        processor = RequestProcessor(bridge, schedule_on_main=scheduler)
        processor.start()
        assert len(scheduler) == 1  # initial drain from the first start()

        processor.stop()

        # Enqueue while stopped: send_request blocks (no consumer), so run
        # it on a thread. Its wake fires into the void — no waker registered.
        results: list[ToolResponse] = []

        def sender(rid: str, tool: str):
            results.append(bridge.send_request(_make_request(rid, tool)))

        t1 = threading.Thread(target=sender, args=("r1", "while_stopped"))
        t1.start()
        for _ in range(1000):
            if not bridge.request_queue.empty():
                break
            time.sleep(0.005)
        assert not bridge.request_queue.empty()
        assert len(scheduler) == 1  # nothing was scheduled while stopped

        processor.start()  # restart on the SAME instance

        # Restart re-registered the waker and scheduled a NEW initial drain.
        assert bridge._waker is not None
        assert len(scheduler) == 2
        scheduler.closures[1]()  # run the fresh initial drain

        t1.join(timeout=10)
        assert not t1.is_alive()
        assert results[0].success is True
        assert results[0].result == "result:while_stopped"

        # Post-restart dispatch: a new request fires the re-registered
        # waker (observable as a third scheduled closure).
        t2 = threading.Thread(target=sender, args=("r2", "after_restart"))
        t2.start()
        for _ in range(1000):
            if len(scheduler) == 3:
                break
            time.sleep(0.005)
        assert len(scheduler) == 3
        scheduler.closures[2]()

        t2.join(timeout=10)
        assert not t2.is_alive()
        assert results[1].success is True
        assert results[1].result == "result:after_restart"


class TestDefaultLazyScheduler:
    """Default scheduler path: lazily resolved ``mw.taskman.run_on_main``."""

    def test_dispatch_via_lazily_resolved_mw_taskman(
        self, fake_execute, install_mw
    ):
        """No injected scheduler: _schedule resolves mw.taskman.run_on_main.

        A fake ``mw`` (installed on the stubbed aqt module via conftest's
        ``install_mw``) exposes a synchronous ``taskman.run_on_main`` that
        counts calls — proving both the attribute path (a typo in the
        attribute name would AttributeError here instead of only surfacing
        inside Anki) and that start() + dispatch actually flow through it.
        """
        run_on_main = ImmediateScheduler()
        install_mw(
            types.SimpleNamespace(
                taskman=types.SimpleNamespace(run_on_main=run_on_main)
            )
        )

        bridge = QueueBridge()
        processor = RequestProcessor(bridge)  # default: lazy mw resolution
        processor.start()
        assert run_on_main.calls == 1  # initial drain went through taskman

        response = bridge.send_request(_make_request("r1", "list_decks"))

        assert response.success is True
        assert response.result == "result:list_decks"
        assert run_on_main.calls == 2  # the wake also went through taskman
        assert fake_execute == ["list_decks"]


class TestWakerExceptionSafety:
    """A raising waker must never break send_request delivery."""

    def test_send_request_survives_raising_waker(self):
        bridge = QueueBridge()
        bridge.set_waker(lambda: (_ for _ in ()).throw(RuntimeError("waker broke")))

        result_holder: list[ToolResponse] = []
        error_holder: list[Exception] = []

        def sender():
            try:
                result_holder.append(bridge.send_request(_make_request("r1", "tool")))
            except Exception as e:  # noqa: BLE001
                error_holder.append(e)

        t = threading.Thread(target=sender)
        t.start()

        # The waker raised, but the request must still be queued; drain it
        # manually and respond.
        for _ in range(1000):
            request = bridge.get_pending_request()
            if request is not None:
                break
            time.sleep(0.005)
        assert request is not None
        bridge.send_response(
            ToolResponse(request_id=request.request_id, success=True, result="ok")
        )

        t.join(timeout=10)
        assert not t.is_alive()
        assert error_holder == []  # waker exception never propagated
        assert len(result_holder) == 1
        assert result_holder[0].success is True
        assert result_holder[0].result == "ok"

    def test_raising_waker_with_processor_drain(self, fake_execute, monkeypatch):
        """End-to-end: waker raises, a manual drain still delivers."""
        bridge = QueueBridge()
        scheduler = DeferredScheduler()
        processor = RequestProcessor(bridge, schedule_on_main=scheduler)
        processor.start()

        # Sabotage the registered waker AFTER start() wired it up.
        bridge.set_waker(lambda: (_ for _ in ()).throw(RuntimeError("boom")))

        result_holder: list[ToolResponse] = []

        def sender():
            result_holder.append(bridge.send_request(_make_request("r1", "tool_x")))

        t = threading.Thread(target=sender)
        t.start()
        for _ in range(1000):
            if not bridge.request_queue.empty():
                break
            time.sleep(0.005)

        # No new drain was scheduled (the waker raised) — the initial drain
        # from start() stands in for "a subsequent wake picks it up".
        assert len(scheduler) == 1
        scheduler.closures[0]()

        t.join(timeout=10)
        assert not t.is_alive()
        assert len(result_holder) == 1
        assert result_holder[0].success is True
        assert result_holder[0].result == "result:tool_x"
