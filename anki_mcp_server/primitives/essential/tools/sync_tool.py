"""Sync tool -- asynchronous, job-based synchronization with AnkiWeb.

Start / poll / resolve model:
  * ``sync()`` (no args)                -> START; returns {job_id, status:"running"}
  * ``sync_status(job_id)``             -> POLL  (separate tool)
  * ``sync(job_id=..., resolve=...)``   -> RESOLVE a surfaced full-sync conflict

The handler only STARTS or RESOLVES work and returns immediately -- it never
waits for the transfer, keeping it well under the queue-bridge 30s timeout.
All ``aqt``/``anki`` logic lives in ``_sync_runner.py``.
"""
from typing import Any, Literal, Optional

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError

_DESCRIPTION = (
    "Synchronize the local Anki collection with AnkiWeb using an asynchronous "
    "job model. Call with NO arguments to START a sync: it returns immediately "
    "with {job_id, status:'running'} and does NOT wait for the transfer. Then "
    "poll sync_status(job_id) until status is 'success', 'error', 'conflict', "
    "or 'cancelled'. If status becomes 'conflict', a one-way full sync is "
    "required: call sync AGAIN with the same job_id plus resolve='upload' (push "
    "local -> server) or resolve='download' (pull server -> local). ONLY pass a "
    "direction listed in the job's legal_directions -- the WRONG direction "
    "PERMANENTLY DISCARDS data on one side. If unsure, call sync with the "
    "job_id and resolve='cancel' to abandon the conflict safely (nothing is "
    "transferred; the collection is left untouched and a fresh sync can start). "
    "On status 'error', inspect error.code: 'auth_failed' means AnkiWeb "
    "authentication failed or expired and the user must re-authenticate. "
    "IMPORTANT: sync at the START of a review session (before getting cards) "
    "and at the END when the user is done, to keep devices consistent."
)


@Tool(
    "sync",
    _DESCRIPTION,
    write=False,          # sync manages the collection via its own gate, not the undo system
    require_col=False,    # must bypass the collection gate (sync runs while col may be closed)
)
def sync(
    job_id: Optional[str] = None,
    resolve: Optional[Literal["upload", "download", "cancel"]] = None,
) -> dict[str, Any]:
    # Import inside the handler (runs on the main thread) to keep aqt out of
    # module import time and avoid import cycles during auto-discovery.
    from ._sync_runner import resolve_sync, start_sync

    if resolve is not None:
        return resolve_sync(job_id, resolve)

    if job_id is not None:
        raise HandlerError(
            "job_id provided without resolve",
            hint="To resolve a conflict pass both job_id and resolve; "
            "to start a new sync pass neither",
            code="validation_error",
            job_id=job_id,
        )

    return start_sync()
