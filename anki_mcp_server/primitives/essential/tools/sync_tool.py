"""Sync tool -- one polymorphic, job-based synchronization tool for AnkiWeb.

A single ``sync`` tool covers the whole flow; the argument shape selects intent:

  * ``sync()``                          -> START a sync; returns {job_id, status:"running"}
  * ``sync(job_id)``                    -> POLL   that job; returns its status snapshot
  * ``sync(job_id, resolve=...)``       -> RESOLVE a surfaced full-sync conflict

The handler only STARTS, READS or RESOLVES work and returns immediately -- it
never waits for the transfer, keeping it well under the queue-bridge 30s
timeout. All ``aqt``/``anki`` logic lives in ``_sync_runner.py``.
"""
from typing import Any, Literal, Optional

from ....tool_decorator import Tool

_DESCRIPTION = (
    "Synchronize the local Anki collection with AnkiWeb. This is an ASYNCHRONOUS "
    "job: one tool, three call shapes, driven by which arguments you pass.\n"
    "\n"
    "THE THREE CALL SHAPES:\n"
    "  1. sync()                     -> start a sync\n"
    "  2. sync(job_id=\"...\")         -> check that sync's progress/result\n"
    "  3. sync(job_id=\"...\", resolve=\"upload\"|\"download\"|\"cancel\") "
    "-> resolve a conflict\n"
    "\n"
    "STEP 1 -- START. Call sync with NO arguments. You get back "
    "{job_id, status:'running'}. The sync is NOT finished yet -- 'running' only "
    "means it has begun. Keep the job_id.\n"
    "\n"
    "STEP 2 -- POLL until done. Call sync AGAIN passing ONLY that job_id "
    "(sync(job_id=\"...\")). This returns the job's status snapshot. Do NOT assume "
    "the sync is done after one poll -- poll a few times until status becomes one "
    "of the FOUR terminal values: 'success', 'error', 'conflict', or 'cancelled'. "
    "While status is still 'running', wait briefly and poll again.\n"
    "\n"
    "STEP 3 -- HANDLE the terminal status:\n"
    "  * 'success'   -> done, the collection is in sync. Nothing more to do.\n"
    "  * 'conflict'  -> a one-way full sync is required. Look at the snapshot's "
    "legal_directions and call sync with the job_id AND resolve set to one of "
    "them: resolve='upload' pushes LOCAL -> AnkiWeb (your computer wins), "
    "resolve='download' pulls AnkiWeb -> LOCAL (the server wins). WARNING: the "
    "wrong direction PERMANENTLY DISCARDS the data on the losing side. Only pass "
    "a direction that appears in legal_directions. If you are unsure which side "
    "is correct, call sync with the job_id and resolve='cancel' to abandon "
    "safely -- nothing is transferred, the collection is left untouched, and a "
    "fresh sync() can be started later. After resolve='upload'/'download' you get "
    "a new {status:'running'} -- go back to STEP 2 and poll again.\n"
    "  * 'error'     -> inspect error.code. error.code=='auth_failed' means the "
    "AnkiWeb login expired or failed and the USER must re-authenticate (in Anki: "
    "Tools > Preferences, or the Sync button) before a new sync will work. "
    "error.hint (when present) is a human-readable next step.\n"
    "  * 'cancelled' -> a conflict was abandoned; nothing was transferred.\n"
    "\n"
    "GUARDRAILS: resolve REQUIRES a job_id (calling sync with resolve but no "
    "job_id is an error -- resolve needs a job to act on). An unknown or expired "
    "job_id returns a not_found error.\n"
    "\n"
    "SNAPSHOT FIELDS returned by sync(job_id): status (running|conflict|success|"
    "error|cancelled), phase (checking|full_transfer|media|done), "
    "legal_directions (the resolve options, only when status=='conflict'), "
    "result (on success), error ({code, message, category, hint} on error), "
    "job_id, started_at, finished_at.\n"
    "\n"
    "TIP: sync at the START of a review session (before fetching cards) and at "
    "the END when the user is done, so all devices stay consistent."
)


@Tool(
    "sync",
    _DESCRIPTION,
    write=False,          # sync manages the collection via its own gate, not the undo system
    require_col=False,    # must bypass the collection gate (polling must work while col is closed)
)
def sync(
    job_id: Optional[str] = None,
    resolve: Optional[Literal["upload", "download", "cancel"]] = None,
) -> dict[str, Any]:
    # Import inside the handler (runs on the main thread) to keep aqt out of
    # module import time and avoid import cycles during auto-discovery.
    from ._sync_runner import resolve_sync, start_sync, status_snapshot

    # Shape 3: resolve a conflict. resolve_sync enforces the "resolve needs a
    # job_id" guardrail (validation_error) itself, so we route both the valid
    # (job_id + resolve) and the invalid (resolve alone) cases through it.
    if resolve is not None:
        return resolve_sync(job_id, resolve)

    # Shape 2: poll an existing job's status snapshot.
    if job_id is not None:
        return status_snapshot(job_id)

    # Shape 1: start a new sync.
    return start_sync()
