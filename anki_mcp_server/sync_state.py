"""Sync job registry, gate flag, and pure sync helpers.

This is a TOP-LEVEL module deliberately kept free of ``aqt``/``anki`` imports at
load time so that ``handler_wrappers.py`` can import it (for the collection
gate) without creating an import cycle and without dragging Anki into the
pure-logic layer.

It owns three things:

1. :class:`SyncJob` -- an immutable snapshot of a single sync operation.
2. :class:`SyncJobRegistry` -- thread-safe storage for jobs plus the two
   independent concurrency controls used by the async sync tool:
       * the *gate* flag  -- when set, every collection-touching tool is
         rejected (the collection is closed / mid-transfer and unsafe).
       * *single-flight*  -- at most one sync job may be active at a time,
         even while the gate is released (e.g. a conflict awaiting resolution
         leaves the collection usable but must block a second sync).
   A module-level singleton :data:`registry` is provided.
3. Pure, unit-testable helpers (:func:`legal_directions_for`,
   :func:`is_legal_resolution`, :func:`classify_sync_error`).

Threading model: every mutation of the registry happens under a single lock,
and :class:`SyncJob` is treated as immutable -- updates *replace* the stored
object via :func:`dataclasses.replace` rather than mutating it in place. This
means a caller holding a reference to an old snapshot never sees it change
underneath them.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Optional
from uuid import uuid4

# ---------------------------------------------------------------------------
# Eviction tuning: finished jobs are cleaned up so the registry never grows
# without bound. Neither value is safety-critical -- they only bound memory.
# ---------------------------------------------------------------------------
_JOB_TTL_SECONDS = 3600  # keep a finished job's snapshot readable for one hour
_MAX_FINISHED_JOBS = 50  # hard cap on retained finished jobs (oldest evicted)


# ---------------------------------------------------------------------------
# SyncJob -- immutable snapshot of one sync operation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SyncJob:
    """Immutable snapshot of a single sync job.

    Instances are never mutated. The registry replaces the stored object
    whenever any field changes (see :meth:`SyncJobRegistry.update`).

    Attributes:
        job_id: Opaque unique identifier handed back to the client.
        status: One of ``"running"``, ``"conflict"``, ``"success"``,
            ``"error"``, ``"cancelled"`` (a conflict abandoned via
            ``resolve="cancel"``).
        phase: Coarse progress marker -- ``"checking"``, ``"full_transfer"``,
            ``"media"`` or ``"done"``.
        legal_directions: For a conflict, the resolve directions the client is
            allowed to pass (subset of ``["upload", "download"]``).
        required: Raw ``ChangesRequired`` enum *name* from the backend
            (e.g. ``"FULL_SYNC"``), or ``None`` before the check completes.
        server_media_usn: Carried from the collection check to the full-sync
            resolve step; internal, not surfaced to clients.
        result: Success payload, or ``None``.
        error: Error payload ``{"code", "message", "category"}``, or ``None``.
        started_at: Epoch seconds when the job was created.
        finished_at: Epoch seconds when the job reached a terminal state
            (success/error), or ``None`` while still active.
    """

    job_id: str
    status: str
    phase: str
    legal_directions: list[str] = field(default_factory=list)
    required: Optional[str] = None
    server_media_usn: Optional[int] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None
    started_at: float = 0.0
    finished_at: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the client-facing view of this job.

        ``server_media_usn`` is intentionally omitted -- it is an internal
        detail carried between the check and resolve steps.
        """
        return {
            "job_id": self.job_id,
            "status": self.status,
            "phase": self.phase,
            "legal_directions": self.legal_directions,
            "required": self.required,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# ---------------------------------------------------------------------------
# SyncJobRegistry -- thread-safe job storage + gate + single-flight
# ---------------------------------------------------------------------------
class SyncJobRegistry:
    """Thread-safe registry of sync jobs with a gate and single-flight guard.

    The *gate* (``is_sync_active``) and *single-flight* (``active_job``) are two
    SEPARATE controls on purpose:

    * While a full-sync transfer runs, the collection is closed -- the gate is
      ON so every other tool is rejected.
    * While a conflict merely awaits resolution, the collection is open and
      usable -- the gate is OFF, but single-flight stays held so a *second*
      sync cannot start until the conflict is resolved (or the job evicted).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, SyncJob] = {}
        # Gate: collection unavailable to other tools while True.
        self._gate_active = False
        # Single-flight: job_id currently holding the "one sync at a time" slot.
        self._single_flight_id: Optional[str] = None

    # -- creation ----------------------------------------------------------

    def create(self) -> SyncJob:
        """Create and store a fresh ``running``/``checking`` job.

        Does NOT touch the gate or single-flight slot. Prefer
        :meth:`try_begin` to start a sync; this is exposed mainly for tests.
        """
        with self._lock:
            return self._create_locked()

    def _create_locked(self) -> SyncJob:
        self._evict_locked()
        job = SyncJob(
            job_id=uuid4().hex,
            status="running",
            phase="checking",
            started_at=time.time(),
        )
        self._jobs[job.job_id] = job
        return job

    def try_begin(self) -> Optional[SyncJob]:
        """Atomically acquire single-flight + raise the gate and create a job.

        Returns the new :class:`SyncJob` on success, or ``None`` if a sync is
        already active (single-flight held). This is the only correct way to
        start a sync -- the check-and-set is atomic under the lock, so two
        concurrent callers can never both begin.
        """
        with self._lock:
            if self._single_flight_id is not None:
                return None
            job = self._create_locked()
            self._single_flight_id = job.job_id
            self._gate_active = True
            return job

    # -- reads -------------------------------------------------------------

    def get(self, job_id: str) -> Optional[SyncJob]:
        """Return the current snapshot for ``job_id`` (or ``None``)."""
        with self._lock:
            return self._jobs.get(job_id)

    def active_job(self) -> Optional[SyncJob]:
        """Return the job currently holding the single-flight slot (or ``None``)."""
        with self._lock:
            if self._single_flight_id is None:
                return None
            return self._jobs.get(self._single_flight_id)

    def is_sync_active(self) -> bool:
        """Cheap read of the gate flag. NEVER touches ``mw``/``mw.col``.

        Used by the collection gate on the hot path of every tool call, so it
        must stay trivial -- a single unlocked attribute read.

        Correctness invariant (NOT "atomic under the GIL"): every gate mutation
        (``try_begin``/``raise_gate``/``release_gate``/``end``/``reset``) is
        performed on the Qt MAIN THREAD -- from a tool entry point or a taskman
        ``on_done`` callback -- and every read on the tool hot path also runs on
        the main thread (tools execute on the main thread via the queue bridge).
        Background sync workers do I/O only; they never mutate the gate. So the
        writer and the hot-path reader are the same thread and cannot interleave
        here. The lock still guards the registry's internal consistency for the
        rare cross-thread ``get``/``update``.
        """
        return self._gate_active

    # -- mutation ----------------------------------------------------------

    def update(self, job_id: str, **changes: Any) -> Optional[SyncJob]:
        """Replace the stored job with an updated copy (immutable update).

        Returns the new snapshot, or ``None`` if the job no longer exists.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            new_job = replace(job, **changes)
            self._jobs[job_id] = new_job
            return new_job

    # -- gate / single-flight control -------------------------------------

    def raise_gate(self) -> None:
        """Turn the gate ON (collection becomes unavailable to other tools)."""
        with self._lock:
            self._gate_active = True

    def release_gate(self) -> None:
        """Turn the gate OFF, keeping single-flight held.

        Used when a conflict is surfaced: the collection was never closed, so
        it is usable again, but no new sync may start until the conflict is
        resolved.
        """
        with self._lock:
            self._gate_active = False

    def end(self, job_id: Optional[str] = None) -> None:
        """Fully release the job: gate OFF and single-flight cleared.

        Called when a job reaches a terminal state (success/error). ``job_id``
        is accepted for symmetry/clarity but the registry only tracks one
        single-flight holder, so both controls are cleared unconditionally.
        """
        with self._lock:
            self._gate_active = False
            self._single_flight_id = None

    def reset(self) -> None:
        """Hard reset for profile teardown: gate OFF, single-flight cleared,
        stored jobs dropped.

        Called from ``profile_will_close`` so a freshly opened profile never
        inherits a stuck gate/single-flight or stale ``job_id``s from the
        previous profile. Any in-flight background transfer is aborted
        separately by the caller (it needs ``mw.col``); this method is pure and
        never touches Anki.
        """
        with self._lock:
            self._gate_active = False
            self._single_flight_id = None
            self._jobs.clear()

    # -- eviction ----------------------------------------------------------

    def _evict_locked(self) -> None:
        """Drop finished jobs past the TTL, then enforce the size cap.

        The single-flight holder is never evicted. Must be called with the
        lock held.
        """
        now = time.time()

        def finished_items() -> list[tuple[str, SyncJob]]:
            return [
                (jid, j)
                for jid, j in self._jobs.items()
                if j.finished_at is not None and jid != self._single_flight_id
            ]

        # TTL sweep.
        for jid, j in finished_items():
            if j.finished_at is not None and now - j.finished_at > _JOB_TTL_SECONDS:
                self._jobs.pop(jid, None)

        # Size cap: evict oldest finished jobs beyond the cap.
        finished = finished_items()
        if len(finished) > _MAX_FINISHED_JOBS:
            finished.sort(key=lambda kv: kv[1].finished_at or 0.0)
            for jid, _ in finished[: len(finished) - _MAX_FINISHED_JOBS]:
                self._jobs.pop(jid, None)


# Module-level singleton used by the tools and the collection gate.
registry = SyncJobRegistry()


# ---------------------------------------------------------------------------
# Pure helpers (no aqt/anki, fully unit-testable)
# ---------------------------------------------------------------------------

# Map ChangesRequired enum names -> the resolve directions a client may pick.
# FULL_SYNC is ambiguous (server can't tell which side is authoritative), so
# both directions are legal and the client MUST choose. FULL_UPLOAD /
# FULL_DOWNLOAD are forced by the server -- exactly one legal direction.
_LEGAL_DIRECTIONS: dict[str, list[str]] = {
    "NO_CHANGES": [],
    "NORMAL_SYNC": [],
    "FULL_SYNC": ["upload", "download"],
    "FULL_UPLOAD": ["upload"],
    "FULL_DOWNLOAD": ["download"],
}


def legal_directions_for(required_name: str) -> list[str]:
    """Return the legal resolve directions for a ``ChangesRequired`` name.

    Unknown names yield ``[]`` (nothing to resolve). Returns a fresh list so
    callers can store it on an immutable :class:`SyncJob` without aliasing.
    """
    return list(_LEGAL_DIRECTIONS.get(required_name, []))


def is_legal_resolution(legal_directions: list[str], resolve: Optional[str]) -> bool:
    """True iff ``resolve`` is one of ``legal_directions``. Pure predicate."""
    return resolve in legal_directions


# Ordered (category, substrings) table for classifying backend sync error text.
# Substrings are drawn from Anki's ftl sync strings; matching is case-folded.
# Order matters: the most distinctive phrases come first so a generic word
# (e.g. "server") never shadows a specific cause (e.g. "only one copy").
_ERROR_CATEGORY_SUBSTRINGS: list[tuple[str, tuple[str, ...]]] = [
    # sync-conflict: "Only one copy of Anki can sync to your account at once."
    ("another_sync_running", ("only one copy", "one copy of anki")),
    # sync-sanity-check-failed: "... use the Check Database function ..."
    ("sanity_check_failed", ("check database", "sanity check", "sanity")),
    # sync-clock-off: "Your computer's clock is not set to the correct time."
    ("clock_incorrect", ("clock", "not set to the correct time")),
    # sync-upload-too-large: "Your collection file is too large to send."
    ("upload_too_large", ("too large",)),
    # sync-client-too-old: "Your Anki version is too old / out of date."
    ("client_too_old", ("out of date", "too old", "please upgrade", "update anki")),
    # auth failures (also detected structurally via SyncErrorKind.AUTH upstream).
    # "denied" was intentionally dropped -- too broad (matches "access denied"
    # server errors); the structural SyncErrorKind.AUTH check is the real signal.
    ("auth_failed", ("authentication", "invalid credentials", "please log in")),
    # sync-server-error: "The server encountered an error. Please try again ..."
    # MUST precede resync_required: a "server error ... sync again" message is a
    # server error first, not a resync request (specific beats generic).
    ("server_error", ("server error", "server encountered", "internal server", "the server ")),
    # sync-resync-required: "Please sync again, and post on the support site ..."
    ("resync_required", ("resync", "sync again")),
]


def classify_sync_error(message: str) -> str:
    """Classify a backend sync error *message* into a stable category string.

    This is best-effort text matching against Anki's ftl error strings. It is
    intended as a hint for the AI client, not a contract. Returns
    ``"unknown"`` when nothing matches (including empty input).
    """
    if not message:
        return "unknown"
    text = message.lower()
    for category, needles in _ERROR_CATEGORY_SUBSTRINGS:
        if any(needle in text for needle in needles):
            return category
    return "unknown"
