"""Orchestration tests for the async sync runner (_sync_runner.py).

These drive ``start_sync`` / ``resolve_sync`` end-to-end with a synchronous fake
taskman (see conftest ``sync_fakes`` / ``install_mw`` / ``fresh_registry``), so a
single call runs the worker, its on_done, any media monitor, and finalization
in-line. The focus is the gate / single-flight lifecycle and the main-thread
sequencing -- NOT the pure helpers (covered by test_sync_state.py et al).
"""
from __future__ import annotations

import pytest

from anki_mcp_server.handler_wrappers import HandlerError
from anki_mcp_server.sync_state import legal_directions_for


@pytest.fixture(autouse=True)
def _fast_media_poll(sync_runner, monkeypatch):
    """Zero the media poll interval so [True, False] sequences don't sleep."""
    monkeypatch.setattr(sync_runner, "_MEDIA_POLL_INTERVAL", 0)


def _make_conflict(reg, required_name="FULL_SYNC", server_media_usn=7):
    """Seed the registry with a job already awaiting conflict resolution."""
    job = reg.try_begin()
    reg.update(
        job.job_id,
        status="conflict",
        required=required_name,
        legal_directions=legal_directions_for(required_name),
        server_media_usn=server_media_usn,
    )
    reg.release_gate()  # conflict: collection usable, single-flight still held
    return job.job_id


# ===========================================================================
# NORMAL (incremental) path
# ===========================================================================
class TestNormalSync:
    def test_success_no_media_releases_everything(
        self, sync_runner, fresh_registry, install_mw, sync_fakes
    ):
        col = sync_fakes.Col(required=0)  # NO_CHANGES
        mw = sync_fakes.Mw(col, sync_fakes.Taskman(), sync_fakes.Pm(media=False))
        install_mw(mw)

        out = sync_runner.start_sync()
        assert out["status"] == "running"
        job = fresh_registry.get(out["job_id"])
        assert job.status == "success"
        assert fresh_registry.is_sync_active() is False
        assert fresh_registry.active_job() is None
        assert ("sync_media",) not in col.calls

    def test_success_with_media_monitors_without_restarting(
        self, sync_runner, fresh_registry, install_mw, sync_fakes
    ):
        gate_states: list[bool] = []
        col = sync_fakes.Col(
            required=1,  # NORMAL_SYNC
            media_active_seq=[True, False],
            gate_probe=lambda: gate_states.append(fresh_registry.is_sync_active()),
        )
        mw = sync_fakes.Mw(col, sync_fakes.Taskman(), sync_fakes.Pm(media=True))
        install_mw(mw)

        out = sync_runner.start_sync()
        job = fresh_registry.get(out["job_id"])
        assert job.status == "success"
        assert job.result["media_error"] is None
        # Fix 5: media is monitored, NEVER restarted via sync_media().
        assert ("sync_media",) not in col.calls
        assert ("media_sync_status",) in col.calls
        # Fix 6: the gate was already released before media polling started.
        assert gate_states and all(state is False for state in gate_states)
        assert fresh_registry.active_job() is None

    def test_full_sync_surfaces_conflict(
        self, sync_runner, fresh_registry, install_mw, sync_fakes
    ):
        col = sync_fakes.Col(required=2)  # FULL_SYNC
        mw = sync_fakes.Mw(col, sync_fakes.Taskman(), sync_fakes.Pm(media=True))
        install_mw(mw)

        out = sync_runner.start_sync()
        job = fresh_registry.get(out["job_id"])
        assert job.status == "conflict"
        assert set(job.legal_directions) == {"upload", "download"}
        assert fresh_registry.is_sync_active() is False       # gate off
        assert fresh_registry.active_job() is not None         # single-flight held
        assert not any(c[0] == "full_upload_or_download" for c in col.calls)

    def test_unknown_required_becomes_error_not_conflict(
        self, sync_runner, fresh_registry, install_mw, sync_fakes
    ):
        col = sync_fakes.Col(required=99)  # outside the ChangesRequired enum
        mw = sync_fakes.Mw(col, sync_fakes.Taskman(), sync_fakes.Pm(media=True))
        install_mw(mw)

        out = sync_runner.start_sync()
        job = fresh_registry.get(out["job_id"])
        assert job.status == "error"          # NOT a conflict wedge
        assert job.error["code"] == "unknown"
        assert fresh_registry.is_sync_active() is False
        assert fresh_registry.active_job() is None

    def test_backend_error_releases_everything(
        self, sync_runner, fresh_registry, install_mw, sync_fakes
    ):
        col = sync_fakes.Col(sync_exc=RuntimeError("network down"))
        mw = sync_fakes.Mw(col, sync_fakes.Taskman(), sync_fakes.Pm(media=False))
        install_mw(mw)

        out = sync_runner.start_sync()
        job = fresh_registry.get(out["job_id"])
        assert job.status == "error"
        assert fresh_registry.is_sync_active() is False
        assert fresh_registry.active_job() is None

    def test_not_configured_raises_without_touching_registry(
        self, sync_runner, fresh_registry, install_mw, sync_fakes
    ):
        col = sync_fakes.Col()
        mw = sync_fakes.Mw(col, sync_fakes.Taskman(), sync_fakes.Pm(auth=None))
        install_mw(mw)

        with pytest.raises(HandlerError):
            sync_runner.start_sync()
        assert fresh_registry.active_job() is None
        assert fresh_registry.is_sync_active() is False

    def test_second_sync_blocked_while_active(
        self, sync_runner, fresh_registry, install_mw, sync_fakes
    ):
        # Seed an unresolved conflict (single-flight held).
        _make_conflict(fresh_registry)
        col = sync_fakes.Col(required=0)
        mw = sync_fakes.Mw(col, sync_fakes.Taskman(), sync_fakes.Pm(media=False))
        install_mw(mw)

        with pytest.raises(HandlerError):
            sync_runner.start_sync()


# ===========================================================================
# Spawn-failure resilience
# ===========================================================================
class TestSpawnFailure:
    def test_start_sync_spawn_failure_never_wedges(
        self, sync_runner, fresh_registry, install_mw, sync_fakes
    ):
        tm = sync_fakes.Taskman()
        tm.fail_rib_on = {1}  # first spawn (the sync worker) fails
        col = sync_fakes.Col(required=0)
        mw = sync_fakes.Mw(col, tm, sync_fakes.Pm(media=False))
        install_mw(mw)

        with pytest.raises(RuntimeError):
            sync_runner.start_sync()
        assert fresh_registry.is_sync_active() is False
        assert fresh_registry.active_job() is None

        # A fresh sync works after the failure.
        tm.fail_rib_on = set()
        out = sync_runner.start_sync()
        assert out["status"] == "running"

    def test_media_monitor_spawn_failure_still_finalizes(
        self, sync_runner, fresh_registry, install_mw, sync_fakes
    ):
        tm = sync_fakes.Taskman()
        tm.fail_rib_on = {2}  # 1 = sync worker, 2 = media monitor spawn
        col = sync_fakes.Col(required=1)  # NORMAL_SYNC + media
        mw = sync_fakes.Mw(col, tm, sync_fakes.Pm(media=True))
        install_mw(mw)

        out = sync_runner.start_sync()
        job = fresh_registry.get(out["job_id"])
        assert job.status == "success"                 # collection sync succeeded
        assert job.result["media_error"] is not None   # media reported best-effort
        assert fresh_registry.active_job() is None      # single-flight cleared
        assert fresh_registry.is_sync_active() is False


# ===========================================================================
# RESOLVE / CANCEL (full-sync) path
# ===========================================================================
class TestResolve:
    def test_upload_uses_modal_progress_and_succeeds(
        self, sync_runner, fresh_registry, install_mw, sync_fakes
    ):
        jid = _make_conflict(fresh_registry)
        col = sync_fakes.Col()
        tm = sync_fakes.Taskman()
        mw = sync_fakes.Mw(col, tm, sync_fakes.Pm(media=True))
        install_mw(mw)

        out = sync_runner.resolve_sync(jid, "upload")
        assert out["status"] == "running"
        # Fix 3: full-transfer window uses the modal with_progress; media uses
        # the non-modal run_in_background.
        assert tm.wp_calls == 1
        assert tm.rib_calls == 1

        job = fresh_registry.get(jid)
        assert job.status == "success"
        assert job.result.get("resolved") is True
        assert any(
            c[0] == "full_upload_or_download" and c[2] is True for c in col.calls
        )
        assert mw.backups == 0          # no backup on upload
        assert mw.reopened == 1
        assert fresh_registry.active_job() is None
        assert fresh_registry.is_sync_active() is False

    def test_download_backs_up_before_close_and_transfer(
        self, sync_runner, fresh_registry, install_mw, sync_fakes
    ):
        jid = _make_conflict(fresh_registry, "FULL_DOWNLOAD")
        col = sync_fakes.Col()
        mw = sync_fakes.Mw(col, sync_fakes.Taskman(), sync_fakes.Pm(media=False))
        install_mw(mw)

        sync_runner.resolve_sync(jid, "download")

        assert mw.backups == 1
        names = [c[0] for c in col.calls]
        # Ordering: backup the OPEN collection, then close, then transfer.
        assert (
            names.index("create_backup_now")
            < names.index("close_for_full_sync")
            < names.index("full_upload_or_download")
        )
        assert any(
            c[0] == "full_upload_or_download" and c[2] is False for c in col.calls
        )
        assert fresh_registry.get(jid).status == "success"

    def test_reopens_even_when_transfer_raises(
        self, sync_runner, fresh_registry, install_mw, sync_fakes
    ):
        jid = _make_conflict(fresh_registry)
        col = sync_fakes.Col(transfer_exc=RuntimeError("mid-transfer failure"))
        mw = sync_fakes.Mw(col, sync_fakes.Taskman(), sync_fakes.Pm(media=False))
        install_mw(mw)

        sync_runner.resolve_sync(jid, "download")

        assert mw.reopened == 1          # reopened despite the raising transfer
        job = fresh_registry.get(jid)
        assert job.status == "error"
        assert fresh_registry.active_job() is None
        assert fresh_registry.is_sync_active() is False

    def test_cancel_abandons_and_frees_single_flight(
        self, sync_runner, fresh_registry, install_mw, sync_fakes
    ):
        jid = _make_conflict(fresh_registry)
        col = sync_fakes.Col()
        mw = sync_fakes.Mw(col, sync_fakes.Taskman(), sync_fakes.Pm())
        install_mw(mw)

        out = sync_runner.resolve_sync(jid, "cancel")
        assert out["status"] == "cancelled"
        job = fresh_registry.get(jid)
        assert job.status == "cancelled"
        # Nothing transferred; collection left untouched.
        assert col.calls == []
        assert col.db is not None
        assert mw.reopened == 0
        assert fresh_registry.active_job() is None
        assert fresh_registry.is_sync_active() is False

        # A fresh sync works immediately after cancel.
        col2 = sync_fakes.Col(required=0)
        mw2 = sync_fakes.Mw(col2, sync_fakes.Taskman(), sync_fakes.Pm(media=False))
        install_mw(mw2)
        assert sync_runner.start_sync()["status"] == "running"

    def test_illegal_direction_keeps_conflict(
        self, sync_runner, fresh_registry, install_mw, sync_fakes
    ):
        jid = _make_conflict(fresh_registry, "FULL_UPLOAD")  # legal: ["upload"]
        col = sync_fakes.Col()
        mw = sync_fakes.Mw(col, sync_fakes.Taskman(), sync_fakes.Pm())
        install_mw(mw)

        with pytest.raises(HandlerError):
            sync_runner.resolve_sync(jid, "download")

        job = fresh_registry.get(jid)
        assert job.status == "conflict"                    # unchanged
        assert fresh_registry.active_job() is not None      # still held
        assert not any(c[0] == "full_upload_or_download" for c in col.calls)

    def test_unknown_job_id_raises(
        self, sync_runner, fresh_registry, install_mw, sync_fakes
    ):
        mw = sync_fakes.Mw(sync_fakes.Col(), sync_fakes.Taskman(), sync_fakes.Pm())
        install_mw(mw)
        with pytest.raises(HandlerError):
            sync_runner.resolve_sync("does-not-exist", "upload")

    def test_resolve_without_job_id_is_validation_error(
        self, sync_runner, fresh_registry, install_mw, sync_fakes
    ):
        # resolve needs a job to act on -- resolving with no job_id is a
        # validation error, not a not_found (there is nothing to look up).
        mw = sync_fakes.Mw(sync_fakes.Col(), sync_fakes.Taskman(), sync_fakes.Pm())
        install_mw(mw)
        with pytest.raises(HandlerError) as exc:
            sync_runner.resolve_sync(None, "upload")
        assert exc.value.code == "validation_error"

    def test_resolve_spawn_failure_never_wedges(
        self, sync_runner, fresh_registry, install_mw, sync_fakes
    ):
        jid = _make_conflict(fresh_registry)
        tm = sync_fakes.Taskman()
        tm.fail_wp_on = {1}  # with_progress spawn fails
        col = sync_fakes.Col()
        mw = sync_fakes.Mw(col, tm, sync_fakes.Pm())
        install_mw(mw)

        with pytest.raises(RuntimeError):
            sync_runner.resolve_sync(jid, "upload")

        assert mw.progress.finished >= 1     # progress dialog torn down
        assert fresh_registry.active_job() is None
        assert fresh_registry.is_sync_active() is False
        # Worker never ran, so the collection was never closed.
        assert not any(c[0] == "close_for_full_sync" for c in col.calls)


# ===========================================================================
# STATUS path (read-only snapshot; the poll leg of the merged sync tool)
# ===========================================================================
class TestStatusSnapshot:
    def test_returns_snapshot_for_existing_job(self, sync_runner, fresh_registry):
        jid = _make_conflict(fresh_registry, "FULL_SYNC")

        snap = sync_runner.status_snapshot(jid)

        # Same payload SyncJob.to_dict() produces (what the old sync_status
        # tool returned), including the conflict's resolve options.
        assert snap == fresh_registry.get(jid).to_dict()
        assert snap["job_id"] == jid
        assert snap["status"] == "conflict"
        assert set(snap["legal_directions"]) == {"upload", "download"}

    def test_unknown_job_id_raises_not_found(self, sync_runner, fresh_registry):
        with pytest.raises(HandlerError) as exc:
            sync_runner.status_snapshot("does-not-exist")
        assert exc.value.code == "not_found"

    def test_never_touches_the_collection(self, sync_runner, fresh_registry):
        # Reading a snapshot must work with no ``mw`` installed at all: it reads
        # the registry only, so it stays usable while a full sync has the
        # collection closed and the gate raised.
        out = fresh_registry.try_begin()  # status 'running', gate raised
        assert fresh_registry.is_sync_active() is True

        snap = sync_runner.status_snapshot(out.job_id)
        assert snap["status"] == "running"
