"""Auth-failure signal test for the sync runner.

Verifies the product requirement: an AnkiWeb auth failure surfaces as a STABLE,
machine-readable ``code == "auth_failed"`` derived STRUCTURALLY from
``SyncError.kind == SyncErrorKind.AUTH`` (not from fragile message text), with a
GENERIC hint (this is a general open-source addon -- no service/dashboard URL or
service-specific text may appear).
"""
from __future__ import annotations

import sys
import types

import pytest


@pytest.fixture()
def fake_anki_errors():
    """Inject a stand-in ``anki.errors`` module (real Anki isn't importable here).

    _classify_exception imports ``anki.errors`` lazily, so seeding sys.modules
    before the call lets us exercise the structural AUTH branch.
    """
    created_anki = "anki" not in sys.modules
    if created_anki:
        anki_pkg = types.ModuleType("anki")
        anki_pkg.__path__ = []  # mark as a package
        sys.modules["anki"] = anki_pkg

    errors = types.ModuleType("anki.errors")

    class SyncErrorKind:
        AUTH = object()
        OTHER = object()

    class SyncError(Exception):
        def __init__(self, message, kind=None):
            super().__init__(message)
            self.kind = kind

    errors.SyncError = SyncError
    errors.SyncErrorKind = SyncErrorKind

    prev = sys.modules.get("anki.errors")
    sys.modules["anki.errors"] = errors
    try:
        yield errors
    finally:
        if prev is not None:
            sys.modules["anki.errors"] = prev
        else:
            sys.modules.pop("anki.errors", None)
        if created_anki:
            sys.modules.pop("anki", None)


def test_auth_failure_has_stable_code_and_generic_hint(
    sync_runner, fresh_registry, install_mw, sync_fakes, fake_anki_errors
):
    sync_error = fake_anki_errors.SyncError(
        "AnkiWeb login expired", kind=fake_anki_errors.SyncErrorKind.AUTH
    )
    col = sync_fakes.Col(sync_exc=sync_error)
    mw = sync_fakes.Mw(col, sync_fakes.Taskman(), sync_fakes.Pm(media=False))
    install_mw(mw)

    out = sync_runner.start_sync()
    job = fresh_registry.get(out["job_id"])

    assert job.status == "error"
    assert job.error["code"] == "auth_failed"       # stable, machine-readable
    assert job.error["category"] == "auth_failed"

    hint = job.error.get("hint", "")
    assert "ankiweb" in hint.lower()                 # generic, user-actionable
    # No service/dashboard specifics leak into a general open-source addon.
    assert "http" not in hint.lower()
    assert "://" not in hint

    # Terminal + fully released.
    assert fresh_registry.active_job() is None
    assert fresh_registry.is_sync_active() is False


def test_non_auth_syncerror_is_not_auth_failed(
    sync_runner, fresh_registry, install_mw, sync_fakes, fake_anki_errors
):
    sync_error = fake_anki_errors.SyncError(
        "The server encountered an error.", kind=fake_anki_errors.SyncErrorKind.OTHER
    )
    col = sync_fakes.Col(sync_exc=sync_error)
    mw = sync_fakes.Mw(col, sync_fakes.Taskman(), sync_fakes.Pm(media=False))
    install_mw(mw)

    out = sync_runner.start_sync()
    job = fresh_registry.get(out["job_id"])
    assert job.status == "error"
    assert job.error["code"] != "auth_failed"        # falls back to text classify
    assert job.error["code"] == "server_error"
