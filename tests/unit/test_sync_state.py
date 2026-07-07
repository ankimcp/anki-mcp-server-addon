"""Unit tests for anki_mcp_server.sync_state -- the registry.

Covers the SyncJobRegistry: creation, single-flight acquisition, the
gate/single-flight independence, immutable updates, terminal release, and
TTL/size eviction of finished jobs. Pure logic -- no Anki required.

Tests instantiate their own ``SyncJobRegistry`` rather than touching the
module-level singleton, so there is no cross-test global state.
"""
from __future__ import annotations

import time

import pytest

from anki_mcp_server import sync_state
from anki_mcp_server.sync_state import SyncJob, SyncJobRegistry


@pytest.fixture()
def reg() -> SyncJobRegistry:
    return SyncJobRegistry()


# ===========================================================================
# create / basic state
# ===========================================================================
class TestCreate:
    def test_create_returns_running_checking_job(self, reg: SyncJobRegistry):
        job = reg.create()
        assert isinstance(job, SyncJob)
        assert job.status == "running"
        assert job.phase == "checking"
        assert job.started_at > 0
        assert job.finished_at is None

    def test_create_is_retrievable(self, reg: SyncJobRegistry):
        job = reg.create()
        assert reg.get(job.job_id) is job

    def test_job_ids_are_unique(self, reg: SyncJobRegistry):
        ids = {reg.create().job_id for _ in range(20)}
        assert len(ids) == 20

    def test_get_unknown_returns_none(self, reg: SyncJobRegistry):
        assert reg.get("does-not-exist") is None

    def test_plain_create_does_not_hold_single_flight_or_gate(self, reg: SyncJobRegistry):
        reg.create()
        assert reg.active_job() is None
        assert reg.is_sync_active() is False


# ===========================================================================
# try_begin / single-flight
# ===========================================================================
class TestTryBegin:
    def test_try_begin_raises_gate_and_holds_single_flight(self, reg: SyncJobRegistry):
        job = reg.try_begin()
        assert job is not None
        assert reg.is_sync_active() is True
        assert reg.active_job() is job

    def test_second_try_begin_is_blocked(self, reg: SyncJobRegistry):
        first = reg.try_begin()
        assert first is not None
        assert reg.try_begin() is None
        # First job still the active one.
        assert reg.active_job().job_id == first.job_id

    def test_try_begin_after_end_succeeds(self, reg: SyncJobRegistry):
        first = reg.try_begin()
        reg.end(first.job_id)
        second = reg.try_begin()
        assert second is not None
        assert second.job_id != first.job_id


# ===========================================================================
# gate vs single-flight independence
# ===========================================================================
class TestGateSingleFlightIndependence:
    def test_release_gate_keeps_single_flight(self, reg: SyncJobRegistry):
        """Conflict case: gate drops (collection usable) but no new sync."""
        job = reg.try_begin()
        reg.release_gate()
        assert reg.is_sync_active() is False        # gate off
        assert reg.active_job().job_id == job.job_id  # single-flight held
        assert reg.try_begin() is None                # still blocked

    def test_raise_gate_sets_flag_without_new_job(self, reg: SyncJobRegistry):
        job = reg.try_begin()
        reg.release_gate()
        reg.raise_gate()  # resolve path re-raises the gate
        assert reg.is_sync_active() is True
        assert reg.active_job().job_id == job.job_id

    def test_end_clears_both(self, reg: SyncJobRegistry):
        job = reg.try_begin()
        reg.end(job.job_id)
        assert reg.is_sync_active() is False
        assert reg.active_job() is None


# ===========================================================================
# reset (profile teardown)
# ===========================================================================
class TestReset:
    def test_reset_clears_gate_single_flight_and_jobs(self, reg: SyncJobRegistry):
        job = reg.try_begin()
        reg.update(job.job_id, status="conflict")
        reg.release_gate()  # conflict: gate off, single-flight still held
        reg.reset()
        assert reg.is_sync_active() is False
        assert reg.active_job() is None
        assert reg.get(job.job_id) is None  # stale jobs dropped

    def test_fresh_sync_works_after_reset(self, reg: SyncJobRegistry):
        first = reg.try_begin()
        assert first is not None
        # A stuck gate/single-flight would block a new profile's first sync.
        reg.reset()
        second = reg.try_begin()
        assert second is not None
        assert second.job_id != first.job_id

    def test_reset_on_empty_registry_is_noop(self, reg: SyncJobRegistry):
        reg.reset()  # must not raise
        assert reg.is_sync_active() is False
        assert reg.active_job() is None


# ===========================================================================
# update / immutability
# ===========================================================================
class TestUpdate:
    def test_update_replaces_and_returns_new_snapshot(self, reg: SyncJobRegistry):
        job = reg.create()
        updated = reg.update(job.job_id, status="success", phase="done")
        assert updated is not None
        assert updated.status == "success"
        assert updated.phase == "done"
        assert reg.get(job.job_id) is updated

    def test_update_does_not_mutate_prior_snapshot(self, reg: SyncJobRegistry):
        job = reg.create()
        reg.update(job.job_id, status="error")
        # The original reference still reads its old value.
        assert job.status == "running"

    def test_update_unknown_job_returns_none(self, reg: SyncJobRegistry):
        assert reg.update("nope", status="success") is None

    def test_frozen_dataclass_cannot_be_mutated(self):
        job = SyncJob(job_id="x", status="running", phase="checking")
        with pytest.raises(Exception):
            job.status = "success"  # type: ignore[misc]


# ===========================================================================
# eviction
# ===========================================================================
class TestEviction:
    def test_ttl_evicts_old_finished_jobs(self, reg: SyncJobRegistry):
        old = reg.create()
        reg.update(old.job_id, status="success", finished_at=time.time() - (sync_state._JOB_TTL_SECONDS + 100))
        # A fresh create() triggers eviction.
        reg.create()
        assert reg.get(old.job_id) is None

    def test_ttl_keeps_recent_finished_jobs(self, reg: SyncJobRegistry):
        recent = reg.create()
        reg.update(recent.job_id, status="success", finished_at=time.time())
        reg.create()
        assert reg.get(recent.job_id) is not None

    def test_running_jobs_are_never_evicted(self, reg: SyncJobRegistry):
        running = reg.create()  # finished_at is None
        reg.create()
        assert reg.get(running.job_id) is not None

    def test_active_single_flight_job_is_never_evicted(self, reg: SyncJobRegistry):
        active = reg.try_begin()
        # Even if it somehow has an old finished_at, single-flight protects it.
        reg.update(active.job_id, status="success", finished_at=time.time() - 10 * sync_state._JOB_TTL_SECONDS)
        reg.create()
        assert reg.get(active.job_id) is not None

    def test_size_cap_evicts_oldest_finished(self, reg: SyncJobRegistry, monkeypatch):
        monkeypatch.setattr(sync_state, "_MAX_FINISHED_JOBS", 3)
        created = []
        now = time.time()
        for i in range(6):
            job = reg.create()
            # Distinct, increasing finished_at so ordering is deterministic.
            reg.update(job.job_id, status="success", finished_at=now + i)
            created.append(job.job_id)
        # Trigger one more eviction pass.
        reg.create()
        # Oldest three finished jobs evicted; newest three retained.
        for jid in created[:3]:
            assert reg.get(jid) is None
        for jid in created[3:]:
            assert reg.get(jid) is not None


# ===========================================================================
# to_dict serialization
# ===========================================================================
class TestToDict:
    def test_to_dict_shape_and_excludes_server_media_usn(self):
        job = SyncJob(
            job_id="j1",
            status="conflict",
            phase="checking",
            legal_directions=["upload", "download"],
            required="FULL_SYNC",
            server_media_usn=42,
        )
        d = job.to_dict()
        assert d["job_id"] == "j1"
        assert d["status"] == "conflict"
        assert d["legal_directions"] == ["upload", "download"]
        assert d["required"] == "FULL_SYNC"
        # Internal detail must not leak to clients.
        assert "server_media_usn" not in d
