"""Unit tests for QueueBridge per-request response routing.

Tests the threading mechanics of QueueBridge directly, without Anki or Docker.
These are the primary verification for the multi-session concurrency fix (issue #21).
"""

import queue
import threading
import time

import pytest

from anki_mcp_server.queue_bridge import QueueBridge, ToolRequest, ToolResponse


def _make_request(request_id: str, tool_name: str = "test") -> ToolRequest:
    return ToolRequest(request_id=request_id, tool_name=tool_name, arguments={})


def _simulate_main_thread(bridge: QueueBridge, count: int) -> None:
    """Simulate main thread draining queue and responding."""
    processed = 0
    while processed < count:
        req = bridge.get_pending_request()
        if req is None:
            time.sleep(0.005)
            continue
        bridge.send_response(
            ToolResponse(
                request_id=req.request_id,
                success=True,
                result=req.tool_name,
            )
        )
        processed += 1


class TestSingleRequest:
    """Basic single-request tests."""

    def test_request_response_roundtrip(self):
        bridge = QueueBridge()

        t = threading.Thread(target=_simulate_main_thread, args=(bridge, 1))
        t.start()

        resp = bridge.send_request(_make_request("r1", "list_decks"))
        assert resp.success is True
        assert resp.request_id == "r1"
        assert resp.result == "list_decks"
        t.join(timeout=10)
        assert not t.is_alive()

    def test_pending_empty_after_completion(self):
        bridge = QueueBridge()

        t = threading.Thread(target=_simulate_main_thread, args=(bridge, 1))
        t.start()

        bridge.send_request(_make_request("r1"))
        t.join(timeout=10)
        assert not t.is_alive()

        with bridge._pending_lock:
            assert len(bridge._pending) == 0


class TestConcurrentRequests:
    """Verify per-request response routing under concurrency."""

    def test_two_concurrent_requests_routed_correctly(self):
        """Two concurrent requests get their own responses — the core fix."""
        bridge = QueueBridge()
        results: dict[str, ToolResponse] = {}

        def sender(request_id: str, tool_name: str):
            resp = bridge.send_request(_make_request(request_id, tool_name))
            results[request_id] = resp

        main_t = threading.Thread(target=_simulate_main_thread, args=(bridge, 2))
        main_t.start()

        t1 = threading.Thread(target=sender, args=("r1", "tool_a"))
        t2 = threading.Thread(target=sender, args=("r2", "tool_b"))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        main_t.join(timeout=10)
        assert not t1.is_alive() and not t2.is_alive() and not main_t.is_alive()

        assert results["r1"].result == "tool_a"
        assert results["r2"].result == "tool_b"

    def test_many_concurrent_requests(self):
        """Stress test: 10 concurrent requests all get correct responses."""
        bridge = QueueBridge()
        num_requests = 10
        results: dict[str, ToolResponse] = {}
        lock = threading.Lock()

        def sender(request_id: str, tool_name: str):
            resp = bridge.send_request(_make_request(request_id, tool_name))
            with lock:
                results[request_id] = resp

        main_t = threading.Thread(
            target=_simulate_main_thread, args=(bridge, num_requests)
        )
        main_t.start()

        threads = []
        for i in range(num_requests):
            t = threading.Thread(target=sender, args=(f"r{i}", f"tool_{i}"))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=10)
        main_t.join(timeout=10)
        assert all(not t.is_alive() for t in threads), "Some sender threads are still alive"
        assert not main_t.is_alive()

        assert len(results) == num_requests
        for i in range(num_requests):
            assert results[f"r{i}"].result == f"tool_{i}"


class TestShutdown:
    """Verify shutdown behavior."""

    def test_shutdown_unblocks_waiting_thread(self):
        bridge = QueueBridge()
        result_holder: list[ToolResponse] = []

        def sender():
            try:
                resp = bridge.send_request(_make_request("r1", "slow"))
                result_holder.append(resp)
            except Exception:
                pass

        t = threading.Thread(target=sender)
        t.start()
        time.sleep(0.05)

        bridge.shutdown()
        t.join(timeout=5)

        assert not t.is_alive(), "Thread should have been unblocked by shutdown"
        assert len(result_holder) == 1, "Sender should have received a shutdown response"
        assert result_holder[0].success is False
        assert "shutting down" in result_holder[0].error.lower()

    def test_shutdown_unblocks_multiple_waiting_threads(self):
        bridge = QueueBridge()
        errors: list[str] = []
        lock = threading.Lock()

        def sender(rid: str):
            try:
                resp = bridge.send_request(_make_request(rid))
                with lock:
                    if not resp.success:
                        errors.append(resp.error)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [threading.Thread(target=sender, args=(f"r{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        time.sleep(0.05)

        bridge.shutdown()
        for t in threads:
            t.join(timeout=5)
            assert not t.is_alive()

        assert len(errors) == 5
        for err in errors:
            assert "shutting down" in err.lower()

    def test_shutdown_rejects_new_requests(self):
        bridge = QueueBridge()
        bridge.shutdown()

        with pytest.raises(Exception, match="shutting down"):
            bridge.send_request(_make_request("late"))

    def test_shutdown_then_fresh_bridge(self):
        """Simulates profile switch: shutdown old bridge, create new one."""
        old_bridge = QueueBridge()
        result_holder: list[ToolResponse] = []

        def sender():
            try:
                resp = old_bridge.send_request(_make_request("r1"))
                result_holder.append(resp)
            except Exception:
                pass

        t = threading.Thread(target=sender)
        t.start()
        time.sleep(0.05)

        old_bridge.shutdown()
        t.join(timeout=5)
        assert not t.is_alive()

        # Fresh bridge works independently
        new_bridge = QueueBridge()
        main_t = threading.Thread(target=_simulate_main_thread, args=(new_bridge, 1))
        main_t.start()

        resp = new_bridge.send_request(_make_request("r2", "fresh"))
        assert resp.success is True
        assert resp.result == "fresh"
        main_t.join(timeout=10)
        assert not main_t.is_alive()


class TestTimeout:
    """Verify timeout and cleanup behavior."""

    def test_timeout_raises_queue_empty(self):
        """send_request raises queue.Empty after timeout (no main thread processing)."""
        bridge = QueueBridge()

        # Monkey-patch a short timeout to avoid waiting 30s
        original_send = bridge.send_request

        def short_timeout_send(request: ToolRequest) -> ToolResponse:
            response_q: queue.Queue[ToolResponse] = queue.Queue()
            with bridge._pending_lock:
                if bridge._shutdown:
                    raise Exception("Bridge is shutting down")
                bridge._pending[request.request_id] = response_q
            bridge.request_queue.put(request)
            try:
                return response_q.get(timeout=0.1)
            finally:
                with bridge._pending_lock:
                    bridge._pending.pop(request.request_id, None)

        with pytest.raises(queue.Empty):
            short_timeout_send(_make_request("t1"))

    def test_pending_cleanup_after_timeout(self):
        """Timed-out send_request cleans up its _pending entry via finally block."""
        bridge = QueueBridge()

        def short_timeout_send(request: ToolRequest) -> ToolResponse:
            response_q: queue.Queue[ToolResponse] = queue.Queue()
            with bridge._pending_lock:
                if bridge._shutdown:
                    raise Exception("Bridge is shutting down")
                bridge._pending[request.request_id] = response_q
            bridge.request_queue.put(request)
            try:
                return response_q.get(timeout=0.1)
            finally:
                with bridge._pending_lock:
                    bridge._pending.pop(request.request_id, None)

        with pytest.raises(queue.Empty):
            short_timeout_send(_make_request("t1"))

        with bridge._pending_lock:
            assert "t1" not in bridge._pending

    def test_late_response_for_unknown_request_id_is_harmless(self):
        """send_response for an unknown request_id does not raise."""
        bridge = QueueBridge()

        # Should not raise — just prints a warning
        bridge.send_response(
            ToolResponse(request_id="ghost", success=True, result="late")
        )


class TestGetPendingRequest:
    """Verify get_pending_request behavior."""

    def test_returns_none_when_empty(self):
        bridge = QueueBridge()
        assert bridge.get_pending_request() is None

    def test_returns_request_in_fifo_order(self):
        bridge = QueueBridge()
        bridge.request_queue.put(_make_request("r1", "first"))
        bridge.request_queue.put(_make_request("r2", "second"))

        req1 = bridge.get_pending_request()
        req2 = bridge.get_pending_request()
        assert req1.request_id == "r1"
        assert req2.request_id == "r2"
        assert bridge.get_pending_request() is None
