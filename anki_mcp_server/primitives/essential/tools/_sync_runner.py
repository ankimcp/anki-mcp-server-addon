"""Worker + main-thread sequencing for the async ``sync`` tool.

All ``aqt``/``anki`` interaction for syncing lives here so the tool modules
(``sync_tool.py``, ``sync_status_tool.py``) stay thin and the collection logic
is isolated (SRP). The ``_`` prefix keeps this module out of the "is a tool"
mental model -- it registers no ``@Tool`` (auto-discovery importing it is
harmless; it only defines functions).

Threading contract
------------------
Three entry points run on the Qt MAIN THREAD (invoked via the queue bridge) and
return FAST -- they only START or READ background work, never wait for a
transfer, so they stay well under the 30s queue-bridge timeout:

* :func:`start_sync`   -- begin a normal sync (returns ``{job_id, status}``).
* :func:`resolve_sync` -- resolve OR cancel a surfaced full-sync conflict.

Everything that blocks (the collection check, the full-sync transfer, backups,
media polling) runs on a BACKGROUND thread; the ``on_done`` callbacks (also main
thread, marshaled by taskman) finalize the job's registry state. Crucially,
**every mutation of the registry gate / single-flight happens on the main
thread** (in an entry point or an ``on_done``) -- the background workers only do
I/O and return a plain result. That is what makes the cheap unlocked
:meth:`SyncJobRegistry.is_sync_active` read on the tool hot path safe.

Two transports for the actual transfer:

* NORMAL (incremental) sync -> ``run_in_background`` (async, NON-modal). The
  collection stays open, so the UI stays usable.
* RESOLVE (full sync) -> ``mw.taskman.with_progress`` (async but shows Anki's
  own APPLICATION-MODAL progress dialog). During a full sync the collection is
  CLOSED (``mw.col.db is None``) while the rest of Anki is live; the modal
  blocks the user from clicking Browse/Stats and crashing on the closed
  collection. ``with_progress`` still returns immediately (it shows a modeless-
  to-code dialog via ``.show()``, not ``.exec()``), so the handler returns fast
  and QTimers -- including the queue-bridge poll and ``sync_status`` -- keep
  firing. The registry gate is kept as belt-and-suspenders defense.

Concurrency safety comes from the registry's gate + single-flight, mirroring
Anki's own ``qt/aqt/sync.py`` sequencing for the collection-closing full sync.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import Future
from typing import Any, Optional

from ....handler_wrappers import HandlerError, get_mw
from ....sync_state import (
    classify_sync_error,
    is_legal_resolution,
    legal_directions_for,
    registry,
)

logger = logging.getLogger(__name__)

# ChangesRequired enum values (SyncCollectionResponse.ChangesRequired) are a
# stable protobuf contract (confirmed against anki 25.9.2). We map the int the
# backend returns to a name once, here, so the rest of the code speaks names.
# An int OUTSIDE this table is treated as an ERROR (see _normal_sync_worker) --
# never as a conflict, so we never create a job that can't be resolved.
_REQUIRED_NAMES: dict[int, str] = {
    0: "NO_CHANGES",
    1: "NORMAL_SYNC",
    2: "FULL_SYNC",
    3: "FULL_DOWNLOAD",
    4: "FULL_UPLOAD",
}

_FULL_SYNC_NAMES = frozenset({"FULL_SYNC", "FULL_UPLOAD", "FULL_DOWNLOAD"})
_IN_SYNC_NAMES = frozenset({"NO_CHANGES", "NORMAL_SYNC"})

# How often (seconds) to poll media_sync_status().active while media syncs.
_MEDIA_POLL_INTERVAL = 0.2

# Generic, service-agnostic hints attached to a terminal error payload. Keyed by
# the stable error ``code``. ``auth_failed`` is the machine-readable signal a
# client keys off -- its hint MUST stay generic (this is a general open-source
# addon; no service/dashboard URL or service-specific text belongs here).
_ERROR_HINTS: dict[str, str] = {
    "auth_failed": (
        "AnkiWeb authentication failed or expired -- re-authenticate "
        "(Tools > Preferences, or the Sync button), then start a new sync"
    ),
    "another_sync_running": (
        "Another client is already syncing to your AnkiWeb account; wait a "
        "minute, then start a new sync"
    ),
    "clock_incorrect": "Fix your computer's clock, then start a new sync",
    "client_too_old": "Update Anki to the latest version, then start a new sync",
    "sanity_check_failed": (
        "Run Tools > Check Database in Anki, then start a new sync"
    ),
    "upload_too_large": (
        "Your collection is too large to upload in one go; see AnkiWeb's size "
        "limits"
    ),
}


# ===========================================================================
# GUI feedback (non-modal tooltips)
#
# MCP-triggered syncs run silently (the async job model has no GUI of its own),
# so we surface a brief, non-modal ``aqt.utils.tooltip`` at the meaningful
# lifecycle moments to give the LOCAL user feedback. Every call is BEST-EFFORT:
# a tooltip failure (config read, missing GUI, offscreen Qt, any Qt error) must
# NEVER break or interrupt a sync. All callers below already run on the Qt MAIN
# thread (entry points via the queue bridge; on_done finalizers marshaled by
# taskman), which is required for GUI calls.
# ===========================================================================
def _notify(msg: str) -> None:
    """Show a non-modal tooltip, gated on config and fully defensive.

    Skips silently when ``show_sync_tooltip`` is disabled and swallows ANY
    exception (logged at debug) so it can never propagate into the sync flow.
    Must be called on the Qt main thread.
    """
    try:
        from ....config import get_show_sync_tooltip

        if not get_show_sync_tooltip():
            return
        from aqt.utils import tooltip

        tooltip(msg)
    except Exception:  # noqa: BLE001 -- a tooltip must never break a sync
        logger.debug("sync tooltip failed (non-fatal)", exc_info=True)


def _notify_sync_error(code: str) -> None:
    """Tooltip for a terminal sync error, worded by the stable error ``code``."""
    if code == "auth_failed":
        _notify("AnkiMCP: AnkiWeb login required")
    else:
        _notify("AnkiMCP: sync failed")


# ===========================================================================
# START path (normal / incremental sync -- collection stays OPEN)
# ===========================================================================
def start_sync() -> dict[str, Any]:
    """Begin a normal sync. Runs on the main thread; returns immediately.

    Raises HandlerError when sync is not configured or a sync is already active
    (single-flight). On success, spawns the background worker and returns
    ``{"job_id": ..., "status": "running"}``.
    """
    mw = get_mw()

    auth = mw.pm.sync_auth()
    if auth is None:
        raise HandlerError(
            "Sync not configured",
            hint="Log in to AnkiWeb first (Tools > Preferences, or the Sync button)",
            code="not_found",
        )

    # Atomically acquire single-flight + raise the gate.
    job = registry.try_begin()
    if job is None:
        active = registry.active_job()
        raise HandlerError(
            "Sync already in progress",
            hint="Poll sync_status, or resolve/cancel the active conflict",
            code="conflict",
            job_id=active.job_id if active else None,
        )

    job_id = job.job_id
    media_enabled = mw.pm.media_syncing_enabled()

    # uses_collection=False is intentional. Anki's own sync uses the single
    # collection executor (uses_collection=True) to serialize against its own
    # concurrent collection ops; we instead serialize MCP sync access via the
    # registry gate and rely on the Rust backend mutex to prevent corruption.
    # The non-collection executor avoids monopolizing Anki's single collection
    # worker for the whole duration of a sync.
    try:
        mw.taskman.run_in_background(
            task=lambda: _normal_sync_worker(mw, auth, media_enabled),
            on_done=lambda fut: _on_normal_done(fut, job_id, mw, media_enabled),
            uses_collection=False,
        )
    except Exception:  # noqa: BLE001 -- never leave the gate stuck ON
        registry.end(job_id)
        raise

    _notify("AnkiMCP: syncing…")
    return {"job_id": job_id, "status": "running"}


def _normal_sync_worker(
    mw: Any, auth: Any, media_enabled: bool
) -> dict[str, Any]:
    """Background thread: run the collection sync (NO GUI, NO registry writes).

    Returns a small result payload consumed by :func:`_on_normal_done`. Lets
    exceptions propagate -- taskman delivers them to on_done via the Future,
    where classification happens in one place.

    ``sync_collection(sync_media=media_enabled)`` already starts media in the
    backend on a successful incremental sync, so we do NOT call ``sync_media``
    again (that would start a second, full re-check pass). Media is only
    MONITORED, later, from :func:`_spawn_media_monitor`.
    """
    out = mw.col.sync_collection(auth, media_enabled)
    required_int = int(out.required)
    required_name = _REQUIRED_NAMES.get(required_int)

    if required_name is None:
        # Unknown/newer enum value: surface as an ERROR, never a conflict --
        # a conflict we can't map has no legal directions and would wedge.
        return {
            "outcome": "unknown_required",
            "required_int": required_int,
        }

    if required_name in _IN_SYNC_NAMES:
        return {"outcome": "success", "required": required_name}

    # FULL_SYNC / FULL_UPLOAD / FULL_DOWNLOAD: do NOT auto-transfer. Surface as
    # a conflict for the caller to resolve (with a direction) or cancel.
    return {
        "outcome": "conflict",
        "required": required_name,
        "server_media_usn": int(out.server_media_usn),
    }


def _on_normal_done(
    fut: "Future[dict[str, Any]]", job_id: str, mw: Any, media_enabled: bool
) -> None:
    """Main thread: finalize (or advance) a normal-sync job from its worker.

    Terminal states (error, success-without-media) fully release the job.
    A conflict releases only the gate -- the collection was never closed, so it
    is usable, but single-flight stays held until the conflict is resolved or
    cancelled. On success WITH media, the gate is released and single-flight is
    held while a background media monitor drains the media sync.
    """
    _reload_scheduler_quietly()

    try:
        result = fut.result()
    except Exception as exc:  # noqa: BLE001 -- classify any backend failure
        code = _record_error(job_id, exc)
        registry.end(job_id)
        _notify_sync_error(code)
        return

    outcome = result["outcome"]

    if outcome == "unknown_required":
        required_int = result["required_int"]
        _store_error(
            job_id,
            code="unknown",
            category="unknown",
            message=(
                f"Server reported an unknown sync requirement ({required_int}); "
                "this Anki/addon build may be out of date"
            ),
        )
        registry.end(job_id)
        _notify_sync_error("unknown")
        return

    if outcome == "conflict":
        required = result["required"]
        registry.update(
            job_id,
            status="conflict",
            required=required,
            legal_directions=legal_directions_for(required),
            server_media_usn=result.get("server_media_usn"),
        )
        # Collection is open and usable; keep single-flight, drop the gate.
        registry.release_gate()
        _notify("AnkiMCP: sync needs your decision (conflict)")
        return

    # Success: the collection is synced and OPEN. Release the gate so other
    # collection tools work again (Fix 6); keep single-flight until media (if
    # any) finishes and the job reaches a terminal state.
    registry.release_gate()
    extra = {"required": result.get("required")}
    if media_enabled:
        _spawn_media_monitor(mw, job_id, extra)
        return
    _finalize_success(job_id, media_error=None, extra=extra)


# ===========================================================================
# RESOLVE / CANCEL path (full sync -- CLOSES the collection)
# ===========================================================================
def resolve_sync(job_id: Optional[str], resolve: Optional[str]) -> dict[str, Any]:
    """Resolve OR cancel a surfaced full-sync conflict. Main thread; fast.

    ``resolve`` is one of ``"upload"``, ``"download"`` or ``"cancel"``.

    * ``cancel`` abandons the conflict: the collection is left EXACTLY as-is (it
      was never closed for a mere conflict), the job is marked ``"cancelled"``
      and single-flight is released so a fresh ``sync()`` works immediately.
    * ``upload``/``download`` must be one of the job's ``legal_directions``.
      Mirrors Anki's full-sync sequencing: fire
      ``collection_will_temporarily_close`` on the main thread, then spawn a
      background transfer under a modal progress dialog (backup + close +
      transfer happen on the worker; reopen happens back on the main thread).
    """
    mw = get_mw()

    if not job_id:
        raise HandlerError(
            "job_id is required to resolve a conflict",
            hint="Pass both job_id and resolve; to start a new sync, pass neither",
            code="validation_error",
        )

    job = registry.get(job_id)
    if job is None:
        raise HandlerError(
            "Sync job not found",
            hint="job_ids expire after completion; start a new sync with no arguments",
            code="not_found",
            job_id=job_id,
        )
    if job.status != "conflict":
        raise HandlerError(
            f"Job is not awaiting conflict resolution (status={job.status})",
            hint="Only a job with status 'conflict' can be resolved or cancelled",
            code="validation_error",
            job_id=job_id,
        )

    # cancel is always legal for a conflicted job -- handle it before the
    # direction legality check.
    if resolve == "cancel":
        return _cancel_conflict(job_id)

    if not is_legal_resolution(job.legal_directions, resolve):
        legal = ", ".join(job.legal_directions) or "(none)"
        raise HandlerError(
            "Invalid resolve direction",
            hint=f"Legal directions: {legal}, or 'cancel' to abandon the conflict",
            code="validation_error",
            job_id=job_id,
            legal_directions=job.legal_directions,
        )

    auth = mw.pm.sync_auth()
    if auth is None:
        raise HandlerError(
            "Sync not configured",
            hint="Log in to AnkiWeb first",
            code="not_found",
        )

    media_enabled = mw.pm.media_syncing_enabled()
    server_usn = job.server_media_usn if media_enabled else None
    upload = resolve == "upload"

    # The collection is about to close -- raise the gate before we touch it.
    registry.raise_gate()
    registry.update(job_id, status="running", phase="full_transfer")

    # Fire the hook on the MAIN thread so the rest of Anki releases the
    # collection early (mirrors qt/aqt/sync.py full_download/full_upload). The
    # backup + close_for_full_sync + transfer run on the worker (backup MUST
    # precede close and MUST be off the main thread, so both move to the
    # worker, exactly as Anki's full_download does).
    from aqt import gui_hooks

    gui_hooks.collection_will_temporarily_close(mw.col)

    try:
        # uses_collection=False: MCP sync access is serialized by the registry
        # gate + Rust backend mutex, so we use the non-collection executor to
        # avoid monopolizing Anki's single collection worker during full sync.
        mw.taskman.with_progress(
            task=lambda: _full_transfer_worker(mw, auth, server_usn, upload),
            on_done=lambda fut: _on_resolve_done(fut, job_id, mw, media_enabled),
            label="Syncing with AnkiWeb...",
            title="Sync",
            immediate=True,
            uses_collection=False,
        )
    except Exception:  # noqa: BLE001 -- spawn failed; nothing was closed yet
        # The worker never ran, so the collection is still open. Tear down the
        # progress dialog (with_progress opened it before spawning) and release
        # so nothing is left wedged.
        try:
            mw.progress.finish()
        except Exception:  # noqa: BLE001
            logger.debug("progress.finish after failed spawn failed", exc_info=True)
        registry.end(job_id)
        raise

    return {"job_id": job_id, "status": "running"}


def _cancel_conflict(job_id: str) -> dict[str, Any]:
    """Abandon a conflicted job without transferring anything.

    The collection was never closed for a conflict, so there is nothing to
    reopen or roll back. Mark the job terminal and release single-flight (and
    the gate, defensively) so a fresh sync can start.
    """
    registry.update(
        job_id,
        status="cancelled",
        phase="done",
        finished_at=time.time(),
    )
    registry.end(job_id)
    _notify("AnkiMCP: sync cancelled")
    return {"job_id": job_id, "status": "cancelled"}


def _full_transfer_worker(
    mw: Any, auth: Any, server_usn: Optional[int], upload: bool
) -> None:
    """Background thread: back up (download only), close, and transfer.

    Mirrors Anki's ``full_download`` closure order: back up the OPEN collection
    first (download is destructive locally), then close for full sync, then run
    the one-way transfer. ``full_upload_or_download`` also drives media using
    ``server_usn``; media completion is MONITORED later, after reopen.
    """
    if not upload:
        # Destructive local overwrite incoming -- snapshot first (blocking, so
        # it belongs off the main thread; requires the collection still open).
        mw.create_backup_now()
    mw.col.close_for_full_sync()
    mw.col.full_upload_or_download(auth=auth, server_usn=server_usn, upload=upload)


def _on_resolve_done(
    fut: "Future[None]", job_id: str, mw: Any, media_enabled: bool
) -> None:
    """Main thread: reopen the collection (ALWAYS) then finalize / advance.

    ``mw.reopen`` + ``mw.reset`` run unconditionally so a failed transfer never
    leaves Anki with a closed collection. The gate is released after the reopen
    attempt: even if reopen failed, ``_check_col_available`` still blocks tools
    via its ``mw.col.db is None`` check, so dropping the gate is safe.
    """
    # ALWAYS reopen, even if the transfer raised.
    try:
        mw.reopen(after_full_sync=True)
        mw.reset()
    except Exception:  # noqa: BLE001 -- reopen must not mask the transfer result
        logger.exception("Failed to reopen collection after full sync")

    # Closed-collection window is over; drop the gate (single-flight stays held
    # through any media monitoring below).
    registry.release_gate()

    try:
        fut.result()
    except Exception as exc:  # noqa: BLE001 -- classify any backend failure
        code = _record_error(job_id, exc)
        registry.end(job_id)
        _notify_sync_error(code)
        return

    # Transfer succeeded. full_upload_or_download already kicked off media via
    # server_usn; monitor it to completion on a background thread (best-effort).
    extra = {"resolved": True}
    if media_enabled:
        _spawn_media_monitor(mw, job_id, extra)
        return
    _finalize_success(job_id, media_error=None, extra=extra)


# ===========================================================================
# Media monitoring (shared by both paths -- collection is OPEN, gate is OFF)
# ===========================================================================
def _spawn_media_monitor(
    mw: Any, job_id: str, extra: Optional[dict[str, Any]] = None
) -> None:
    """Spawn a background media-completion monitor, then return.

    On success this leaves single-flight held; :func:`_on_media_done` finalizes
    the job. If the spawn itself fails, the collection sync already SUCCEEDED,
    so we record media as a best-effort error and finalize now -- never leaving
    single-flight stuck.
    """
    registry.update(job_id, phase="media")
    try:
        # uses_collection=False: MCP sync access is serialized by the registry
        # gate + Rust backend mutex, so we use the non-collection executor to
        # avoid monopolizing Anki's single collection worker.
        mw.taskman.run_in_background(
            task=lambda: _monitor_media_worker(mw),
            on_done=lambda fut: _on_media_done(fut, job_id, extra),
            uses_collection=False,
        )
    except Exception as exc:  # noqa: BLE001 -- never leave single-flight stuck
        logger.exception("Failed to spawn media monitor for job %s", job_id)
        media_error = _error_payload(
            "unknown", "unknown", f"media monitor failed to start: {exc}"
        )
        _finalize_success(job_id, media_error, extra)


def _monitor_media_worker(mw: Any) -> Optional[dict[str, Any]]:
    """Background thread: poll until media sync finishes. Returns error or None.

    ``media_sync_status()`` RAISES if the media sync failed; we catch that and
    report it without failing the whole job (media is best-effort). It does NOT
    start media -- that already happened in the collection/full-sync call.
    """
    try:
        while mw.col.media_sync_status().active:
            time.sleep(_MEDIA_POLL_INTERVAL)
        return None
    except Exception as exc:  # noqa: BLE001 -- media failure never fails the job
        category = classify_sync_error(str(exc))
        logger.warning("Media sync failed (%s): %s", category, exc)
        return _error_payload(category, category, str(exc))


def _on_media_done(
    fut: "Future[Optional[dict[str, Any]]]",
    job_id: str,
    extra: Optional[dict[str, Any]],
) -> None:
    """Main thread: finalize a job whose collection sync already succeeded."""
    try:
        media_error = fut.result()
    except Exception as exc:  # noqa: BLE001 -- monitor crash is best-effort too
        logger.warning("Media monitor crashed: %s", exc)
        media_error = _error_payload("unknown", "unknown", str(exc))
    _finalize_success(job_id, media_error, extra)


# ===========================================================================
# Shared finalization helpers
# ===========================================================================
def _finalize_success(
    job_id: str,
    media_error: Optional[dict[str, Any]],
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """Mark a job successful and fully release it (gate OFF, single-flight cleared)."""
    result: dict[str, Any] = {"media_error": media_error}
    if extra:
        result.update(extra)
    registry.update(
        job_id,
        status="success",
        phase="done",
        result=result,
        finished_at=time.time(),
    )
    registry.end(job_id)
    _notify("AnkiMCP: sync complete")


def _record_error(job_id: str, exc: Exception) -> str:
    """Store an error payload on the job and return its stable error ``code``.

    Does not touch gate/single-flight. The returned code lets callers pick the
    matching user-facing tooltip without re-classifying the exception.
    """
    code, category, message = _classify_exception(exc)
    _store_error(job_id, code, category, message)
    return code


def _store_error(job_id: str, code: str, category: str, message: str) -> None:
    """Write a terminal error payload onto the job (with a generic hint)."""
    registry.update(
        job_id,
        status="error",
        phase="done",
        error=_error_payload(code, category, message),
        finished_at=time.time(),
    )


def _error_payload(code: str, category: str, message: str) -> dict[str, Any]:
    """Build the client-facing error dict, attaching a generic hint if known."""
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "category": category,
    }
    hint = _ERROR_HINTS.get(code)
    if hint:
        payload["hint"] = hint
    return payload


def _classify_exception(exc: Exception) -> tuple[str, str, str]:
    """Return ``(code, category, message)`` for a sync exception.

    ``SyncError`` with ``kind == AUTH`` maps to the STABLE ``auth_failed`` code
    (the machine-readable signal clients key off); everything else is
    classified from the message text. Written defensively so a missing
    ``anki.errors`` (e.g. under unit-test stubs) degrades gracefully.
    """
    message = str(exc)
    try:
        from anki.errors import SyncError, SyncErrorKind
    except Exception:  # noqa: BLE001 -- anki not importable (test stub etc.)
        category = classify_sync_error(message)
        return category, category, message

    if isinstance(exc, SyncError) and getattr(exc, "kind", None) is SyncErrorKind.AUTH:
        return "auth_failed", "auth_failed", message

    category = classify_sync_error(message)
    return category, category, message


def _reload_scheduler_quietly() -> None:
    """Reload the scheduler after a normal sync (its version may have changed).

    Anki's own ``sync_collection`` on_done does this. It uses a private method,
    so we guard it -- a failure here must never break job finalization. (The
    full-sync/resolve path reloads the scheduler inside ``reopen``, so it does
    not call this.)
    """
    try:
        mw = get_mw()
        if mw is not None and mw.col is not None:
            mw.col._load_scheduler()
    except Exception:  # noqa: BLE001 -- private API, best-effort only
        logger.debug("Scheduler reload after sync failed (non-fatal)", exc_info=True)
