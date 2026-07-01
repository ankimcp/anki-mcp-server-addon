"""sync_status tool -- read the snapshot of a sync job started by ``sync``.

Read-only and ``require_col=False``: it only reads the in-memory job registry
and never touches ``mw.col`` (so it stays usable even while a full sync has the
collection closed and the gate is up).
"""
from typing import Any

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError

_DESCRIPTION = (
    "Check the status of a sync job started by the sync tool. Returns the job "
    "snapshot: status ('running'|'conflict'|'success'|'error'|'cancelled'), "
    "phase ('checking'|'full_transfer'|'media'|'done'), legal_directions "
    "(resolve options when status is 'conflict'), result, and error. Poll this "
    "after calling sync. On status 'conflict', call sync again with the job_id "
    "and a legal resolve direction (the wrong direction discards data), or "
    "resolve='cancel' to abandon the conflict. On status 'error', inspect "
    "error.code / error.category for the cause: error.code=='auth_failed' is a "
    "STABLE signal that AnkiWeb authentication failed or expired and the user "
    "must re-authenticate; error.hint (when present) is a human-readable next "
    "step. On status 'cancelled', the conflict was abandoned and nothing was "
    "transferred."
)


@Tool(
    "sync_status",
    _DESCRIPTION,
    write=False,
    require_col=False,  # reads the job registry only; safe while the collection is closed
)
def sync_status(job_id: str) -> dict[str, Any]:
    from ....sync_state import registry

    job = registry.get(job_id)
    if job is None:
        raise HandlerError(
            "Sync job not found",
            hint="job_ids expire after completion; start a new sync with the sync tool",
            code="not_found",
            job_id=job_id,
        )
    return job.to_dict()
