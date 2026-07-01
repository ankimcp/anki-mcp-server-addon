"""Concurrency test for SyncJobRegistry.try_begin single-flight.

Uses REAL threads (not mocks) to prove the check-and-set inside ``try_begin``
is atomic: many threads racing to start a sync must yield exactly one winner.
Pure logic -- no Anki required.
"""
from __future__ import annotations

import threading

from anki_mcp_server.sync_state import SyncJobRegistry


def test_concurrent_try_begin_has_exactly_one_winner():
    reg = SyncJobRegistry()
    n_threads = 64
    barrier = threading.Barrier(n_threads)  # release all threads at once
    results_lock = threading.Lock()
    results: list[object] = []

    def worker() -> None:
        barrier.wait()  # maximize contention on the check-and-set
        job = reg.try_begin()
        with results_lock:
            results.append(job)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [r for r in results if r is not None]
    losers = [r for r in results if r is None]

    assert len(winners) == 1, f"expected exactly one winner, got {len(winners)}"
    assert len(losers) == n_threads - 1
    # The registry agrees on who holds the slot.
    assert reg.active_job() is not None
    assert reg.active_job().job_id == winners[0].job_id
    assert reg.is_sync_active() is True


def test_try_begin_race_after_end_still_single_winner():
    """After a full begin/end cycle, a second racing round has one winner too."""
    reg = SyncJobRegistry()
    first = reg.try_begin()
    assert first is not None
    reg.end(first.job_id)

    n_threads = 32
    barrier = threading.Barrier(n_threads)
    lock = threading.Lock()
    results: list[object] = []

    def worker() -> None:
        barrier.wait()
        with lock:
            results.append(reg.try_begin())

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len([r for r in results if r is not None]) == 1
