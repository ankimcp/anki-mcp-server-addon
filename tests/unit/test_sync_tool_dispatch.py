"""Dispatch tests for the single, polymorphic ``sync`` tool (sync_tool.py).

The ``sync`` tool routes purely by argument shape onto the three ``_sync_runner``
entry points:

  * ``sync()``                     -> start_sync()
  * ``sync(job_id)``               -> status_snapshot(job_id)   (the merged poll leg)
  * ``sync(job_id, resolve=...)``  -> resolve_sync(job_id, resolve)

These tests exercise the real @Tool-decorated ``sync`` function (unwrapped, since
``@Tool`` returns the original callable) with the same synchronous Anki fakes the
runner tests use, so a single call drives a shape end-to-end. They lock in the
routing and the guardrails; the per-leg behavior is covered by test_sync_runner.py.
"""
from __future__ import annotations

import pytest

from anki_mcp_server.handler_wrappers import HandlerError
from anki_mcp_server.sync_state import legal_directions_for


def _seed_conflict(reg, required_name="FULL_SYNC", server_media_usn=7):
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


class TestSyncDispatch:
    def test_no_args_starts_a_sync(
        self, sync_tool, fresh_registry, install_mw, sync_fakes
    ):
        col = sync_fakes.Col(required=0)  # NO_CHANGES -> success
        mw = sync_fakes.Mw(col, sync_fakes.Taskman(), sync_fakes.Pm(media=False))
        install_mw(mw)

        out = sync_tool.sync()

        assert out["status"] == "running"
        assert "job_id" in out
        # It really started a job (the fake taskman ran it to success in-line).
        assert fresh_registry.get(out["job_id"]).status == "success"

    def test_job_id_only_returns_status_snapshot(
        self, sync_tool, fresh_registry
    ):
        jid = _seed_conflict(fresh_registry, "FULL_SYNC")

        out = sync_tool.sync(job_id=jid)

        # Routed to status_snapshot -> the job's to_dict() payload, NOT a new sync.
        assert out == fresh_registry.get(jid).to_dict()
        assert out["job_id"] == jid
        assert out["status"] == "conflict"
        assert set(out["legal_directions"]) == {"upload", "download"}
        # The single-flight job is unchanged -- polling never starts a second sync.
        assert fresh_registry.active_job().job_id == jid

    def test_job_id_plus_resolve_routes_to_resolve(
        self, sync_tool, fresh_registry, install_mw, sync_fakes
    ):
        jid = _seed_conflict(fresh_registry)
        mw = sync_fakes.Mw(sync_fakes.Col(), sync_fakes.Taskman(), sync_fakes.Pm())
        install_mw(mw)

        out = sync_tool.sync(job_id=jid, resolve="cancel")

        # Routed to resolve_sync -> the conflict was resolved (here: cancelled).
        assert out["status"] == "cancelled"
        assert fresh_registry.get(jid).status == "cancelled"

    def test_resolve_without_job_id_is_validation_error(
        self, sync_tool, fresh_registry, install_mw, sync_fakes
    ):
        # resolve needs a job to act on -- resolving with no job_id is a
        # validation_error, never a silent start or a not_found lookup.
        mw = sync_fakes.Mw(sync_fakes.Col(), sync_fakes.Taskman(), sync_fakes.Pm())
        install_mw(mw)

        with pytest.raises(HandlerError) as exc:
            sync_tool.sync(resolve="upload")
        assert exc.value.code == "validation_error"

    def test_unknown_job_id_status_is_not_found(
        self, sync_tool, fresh_registry
    ):
        with pytest.raises(HandlerError) as exc:
            sync_tool.sync(job_id="does-not-exist")
        assert exc.value.code == "not_found"
