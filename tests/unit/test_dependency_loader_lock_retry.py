"""Unit tests for ``dependency_loader._import_with_lock_retry`` and its
supporting classifier, ``_classify_native_load_error``.

``_import_with_lock_retry`` had ZERO tests before this file, despite being a
production seam this change set touched (its ``time.sleep`` calls were
switched to the ``_sleep`` module seam so tests never really sleep). Its
retry loop is driven entirely by the classification ``_preflight_native_
extension`` returns for the cached native extension file
(``"ok"``/``"locked"``/``"access-denied"``/``"missing"``/``"unknown"``); that
classification in turn comes from ``_classify_native_load_error``, which reads
``winerror``/``errno`` off a real ``OSError`` raised by pre-flight-opening the
file.

Coverage split (see the module-level comment above each test class):

* ``_classify_native_load_error`` is exercised directly, against
  hand-constructed ``OSError`` instances with the relevant ``winerror``/
  ``errno`` set -- no real native extension file or platform-specific I/O
  needed.
* ``_import_with_lock_retry``'s retry LOOP (attempt counting, backoff
  schedule, which classifications retry vs stop) is exercised by
  monkeypatching ``_preflight_native_extension`` itself to return a scripted
  sequence of classifications. This is the smallest seam available without
  driving a real locked/access-denied/missing ``.pyd``/``.so`` file, which
  would require actual OS-level file locking (Windows-only for the "locked"
  case) or root/permission tricks (for "access-denied") -- neither of which
  this suite attempts. What IS covered: the retry loop's attempt count,
  backoff durations passed to ``_sleep``, and the return value / import_fn
  call pattern for each classification, exactly as
  ``_import_with_lock_retry``'s docstring documents them. What is NOT
  covered: that ``_preflight_native_extension`` itself correctly classifies a
  REAL locked/access-denied/missing file on a REAL filesystem -- that would
  need platform-specific native-file-locking infrastructure this suite does
  not set up.

No production code was changed to make this file possible.
"""
from __future__ import annotations

import errno as errno_module
import importlib.util
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load dependency_loader.py as a standalone module (same technique the sibling
# dependency_loader unit tests use) so this file doesn't trigger
# anki_mcp_server/__init__.py, which requires a running Anki/Qt environment.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_loader_path = _REPO_ROOT / "anki_mcp_server" / "dependency_loader.py"
_spec = importlib.util.spec_from_file_location(
    "_dep_loader_lock_retry_under_test", _loader_path
)
_dep_loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dep_loader)


# ---------------------------------------------------------------------------
# _classify_native_load_error
# ---------------------------------------------------------------------------

def _make_oserror(*, winerror: int | None = None, errno: int | None = None) -> OSError:
    """Build an OSError carrying exactly the attributes under test, without
    relying on any real OS call to raise one for us."""
    exc = OSError("synthetic")
    if errno is not None:
        exc.errno = errno
    else:
        exc.errno = None
    if winerror is not None:
        exc.winerror = winerror
    return exc


# Note: these tests patch `_dep_loader.sys.platform`, i.e. the real, shared
# `sys` module (dependency_loader.py does a plain `import sys`) -- monkeypatch
# restores it after each test, but this is module-global state, so this file
# is serial-only, not xdist-safe, matching the convention already used by
# test_native_cache_cleanup.py for the same reason.


def test_classify_windows_sharing_violation_is_locked(monkeypatch):
    monkeypatch.setattr(_dep_loader.sys, "platform", "win32")
    exc = _make_oserror(winerror=_dep_loader._WINERROR_SHARING_VIOLATION)
    assert _dep_loader._classify_native_load_error(exc) == "locked"


def test_classify_windows_access_denied(monkeypatch):
    monkeypatch.setattr(_dep_loader.sys, "platform", "win32")
    exc = _make_oserror(winerror=_dep_loader._WINERROR_ACCESS_DENIED)
    assert _dep_loader._classify_native_load_error(exc) == "access-denied"


def test_classify_windows_missing_falls_through_to_errno(monkeypatch):
    """On Windows, a winerror that isn't 32/5 falls through to the errno
    checks below -- e.g. a file-not-found OSError still carries ENOENT."""
    monkeypatch.setattr(_dep_loader.sys, "platform", "win32")
    exc = _make_oserror(winerror=2, errno=errno_module.ENOENT)
    assert _dep_loader._classify_native_load_error(exc) == "missing"


def test_classify_posix_missing(monkeypatch):
    monkeypatch.setattr(_dep_loader.sys, "platform", "linux")
    exc = _make_oserror(errno=errno_module.ENOENT)
    assert _dep_loader._classify_native_load_error(exc) == "missing"


def test_classify_posix_access_denied_eacces(monkeypatch):
    monkeypatch.setattr(_dep_loader.sys, "platform", "linux")
    exc = _make_oserror(errno=errno_module.EACCES)
    assert _dep_loader._classify_native_load_error(exc) == "access-denied"


def test_classify_posix_access_denied_eperm(monkeypatch):
    monkeypatch.setattr(_dep_loader.sys, "platform", "linux")
    exc = _make_oserror(errno=errno_module.EPERM)
    assert _dep_loader._classify_native_load_error(exc) == "access-denied"


def test_classify_posix_locked_ebusy(monkeypatch):
    monkeypatch.setattr(_dep_loader.sys, "platform", "linux")
    exc = _make_oserror(errno=errno_module.EBUSY)
    assert _dep_loader._classify_native_load_error(exc) == "locked"


def test_classify_unknown_errno(monkeypatch):
    monkeypatch.setattr(_dep_loader.sys, "platform", "linux")
    exc = _make_oserror(errno=errno_module.EIO)
    assert _dep_loader._classify_native_load_error(exc) == "unknown"


def test_classify_windows_prefers_winerror_over_errno(monkeypatch):
    """A winerror match wins even when errno would classify differently --
    the Windows branch returns before the errno checks ever run."""
    monkeypatch.setattr(_dep_loader.sys, "platform", "win32")
    exc = _make_oserror(
        winerror=_dep_loader._WINERROR_SHARING_VIOLATION, errno=errno_module.ENOENT
    )
    assert _dep_loader._classify_native_load_error(exc) == "locked"


# ---------------------------------------------------------------------------
# _import_with_lock_retry -- retry loop, driven via a scripted
# _preflight_native_extension
# ---------------------------------------------------------------------------

_CACHE_DIR = Path("/nonexistent/cache_dir")  # never touched -- preflight is stubbed
_PACKAGE_SUBDIR = "dummy_pkg"
_DISPLAY_NAME = "dummy"


class _ScriptedPreflight:
    """Returns each classification in ``classifications`` in order, one per
    call; raises if called more times than scripted."""

    def __init__(self, classifications: list[str]):
        self._remaining = list(classifications)
        self.calls = 0

    def __call__(self, cache_dir, package_subdir, display_name):
        self.calls += 1
        if not self._remaining:
            raise AssertionError("_preflight_native_extension called more times than scripted")
        return self._remaining.pop(0)


def _install_preflight(monkeypatch, classifications: list[str]) -> _ScriptedPreflight:
    script = _ScriptedPreflight(classifications)
    monkeypatch.setattr(_dep_loader, "_preflight_native_extension", script)
    return script


def _install_sleep_recorder(monkeypatch) -> list[float]:
    durations: list[float] = []
    monkeypatch.setattr(_dep_loader, "_sleep", durations.append)
    return durations


def test_success_on_first_attempt_never_sleeps(monkeypatch):
    _install_preflight(monkeypatch, ["ok"])
    durations = _install_sleep_recorder(monkeypatch)

    calls = {"n": 0}

    def import_fn() -> bool:
        calls["n"] += 1
        return True

    result = _dep_loader._import_with_lock_retry(
        import_fn,
        cache_dir=_CACHE_DIR,
        package_subdir=_PACKAGE_SUBDIR,
        display_name=_DISPLAY_NAME,
    )

    assert result is True
    assert calls["n"] == 1
    assert durations == []


def test_locked_once_then_ok_retries_with_documented_backoff(monkeypatch):
    """A single transient lock: preflight reports "locked" once, then "ok" --
    exactly one backoff sleep, using the FIRST documented delay."""
    script = _install_preflight(monkeypatch, ["locked", "ok"])
    durations = _install_sleep_recorder(monkeypatch)

    calls = {"n": 0}

    def import_fn() -> bool:
        calls["n"] += 1
        return True

    result = _dep_loader._import_with_lock_retry(
        import_fn,
        cache_dir=_CACHE_DIR,
        package_subdir=_PACKAGE_SUBDIR,
        display_name=_DISPLAY_NAME,
    )

    assert result is True
    assert script.calls == 2
    assert calls["n"] == 1
    assert durations == [_dep_loader._LOCK_RETRY_DELAYS[0]]


def test_locked_until_last_retry_then_ok_uses_full_backoff_schedule(monkeypatch):
    """Locked for every retry slot except the very last preflight, which
    reports "ok" -- backoff durations must match the FULL documented
    schedule, in order, with no attempt skipped or reordered."""
    n_retries = len(_dep_loader._LOCK_RETRY_DELAYS)
    classifications = ["locked"] * n_retries + ["ok"]
    script = _install_preflight(monkeypatch, classifications)
    durations = _install_sleep_recorder(monkeypatch)

    calls = {"n": 0}

    def import_fn() -> bool:
        calls["n"] += 1
        return True

    result = _dep_loader._import_with_lock_retry(
        import_fn,
        cache_dir=_CACHE_DIR,
        package_subdir=_PACKAGE_SUBDIR,
        display_name=_DISPLAY_NAME,
    )

    assert result is True
    assert script.calls == n_retries + 1
    assert calls["n"] == 1
    assert durations == list(_dep_loader._LOCK_RETRY_DELAYS)


def test_locked_through_every_retry_gives_up_without_importing(monkeypatch):
    """Locked on every single preflight, including all retry slots -- gives
    up after exactly the documented number of attempts (1 initial + len
    (_LOCK_RETRY_DELAYS) retries) and never calls import_fn at all."""
    n_retries = len(_dep_loader._LOCK_RETRY_DELAYS)
    classifications = ["locked"] * (n_retries + 1)
    script = _install_preflight(monkeypatch, classifications)
    durations = _install_sleep_recorder(monkeypatch)

    calls = {"n": 0}

    def import_fn() -> bool:
        calls["n"] += 1
        return True

    result = _dep_loader._import_with_lock_retry(
        import_fn,
        cache_dir=_CACHE_DIR,
        package_subdir=_PACKAGE_SUBDIR,
        display_name=_DISPLAY_NAME,
    )

    assert result is False
    assert script.calls == n_retries + 1
    assert calls["n"] == 0
    assert durations == list(_dep_loader._LOCK_RETRY_DELAYS)


def test_access_denied_is_not_retried(monkeypatch):
    """access-denied stops the retry loop immediately -- one preflight call,
    no sleep, and the import probe runs exactly once (its result is
    whatever it returns, not forced by the classification)."""
    script = _install_preflight(monkeypatch, ["access-denied"])
    durations = _install_sleep_recorder(monkeypatch)

    calls = {"n": 0}

    def import_fn() -> bool:
        calls["n"] += 1
        return False

    result = _dep_loader._import_with_lock_retry(
        import_fn,
        cache_dir=_CACHE_DIR,
        package_subdir=_PACKAGE_SUBDIR,
        display_name=_DISPLAY_NAME,
    )

    assert result is False
    assert script.calls == 1
    assert calls["n"] == 1
    assert durations == []


def test_missing_is_not_retried(monkeypatch):
    script = _install_preflight(monkeypatch, ["missing"])
    durations = _install_sleep_recorder(monkeypatch)

    calls = {"n": 0}

    def import_fn() -> bool:
        calls["n"] += 1
        return False

    result = _dep_loader._import_with_lock_retry(
        import_fn,
        cache_dir=_CACHE_DIR,
        package_subdir=_PACKAGE_SUBDIR,
        display_name=_DISPLAY_NAME,
    )

    assert result is False
    assert script.calls == 1
    assert calls["n"] == 1
    assert durations == []


def test_unknown_is_not_retried(monkeypatch):
    """"unknown" -- like access-denied/missing -- stops the retry loop and
    runs the import probe once."""
    script = _install_preflight(monkeypatch, ["unknown"])
    durations = _install_sleep_recorder(monkeypatch)

    calls = {"n": 0}

    def import_fn() -> bool:
        calls["n"] += 1
        return True

    result = _dep_loader._import_with_lock_retry(
        import_fn,
        cache_dir=_CACHE_DIR,
        package_subdir=_PACKAGE_SUBDIR,
        display_name=_DISPLAY_NAME,
    )

    assert result is True
    assert script.calls == 1
    assert calls["n"] == 1
    assert durations == []


def test_non_locked_classification_with_import_fn_raising_import_error(monkeypatch):
    """A wrong-version/corrupt cache: preflight says "ok" but the import
    probe itself raises ImportError -- treated as failure, not retried."""
    script = _install_preflight(monkeypatch, ["ok"])
    durations = _install_sleep_recorder(monkeypatch)

    def import_fn() -> bool:
        raise ImportError("wrong version")

    result = _dep_loader._import_with_lock_retry(
        import_fn,
        cache_dir=_CACHE_DIR,
        package_subdir=_PACKAGE_SUBDIR,
        display_name=_DISPLAY_NAME,
    )

    assert result is False
    assert script.calls == 1
    assert durations == []
