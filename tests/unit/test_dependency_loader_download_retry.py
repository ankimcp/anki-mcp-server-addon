"""Unit tests for the download timeout + bounded retry policy in
``dependency_loader._download_and_extract_wheel`` (GitHub issue #73, first
bullet).

Before this fix, the PyPI metadata request had a 30s timeout but a single
attempt, and the wheel fetch used ``urllib.request.urlretrieve``, which has NO
timeout at all -- a stalled connection could hang Anki's startup forever.

Both requests now go through ``_retry_network_call``, a small bounded retry
(``_DOWNLOAD_MAX_ATTEMPTS`` total attempts, ``_DOWNLOAD_RETRY_BACKOFF_SECONDS``
backoff) that retries ONLY network-class failures (timeouts, connection
reset/aborted, HTTP 5xx) -- never a user cancel, an HTTP 4xx, or a
non-network exception. The wheel fetch itself is now a chunked
``urlopen``-based read (``_fetch_wheel_bytes``) instead of ``urlretrieve``, so
it can carry the same timeout and remain cancellable between chunks.

These tests drive ``_download_and_extract_wheel`` directly against a scripted
fake ``urllib.request.urlopen``, with no real network access. Backoff sleeps
are monkeypatched to no-ops so the tests run instantly.
"""
from __future__ import annotations

import http.client
import importlib.util
import io
import json
import ssl
import sys
import urllib.error
import zipfile
from email.message import Message
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load dependency_loader.py as a standalone module (same technique the other
# dependency_loader unit tests use) so this file doesn't trigger
# anki_mcp_server/__init__.py, which requires a running Anki/Qt environment.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_loader_path = _REPO_ROOT / "anki_mcp_server" / "dependency_loader.py"
_spec = importlib.util.spec_from_file_location(
    "_dep_loader_download_retry_under_test", _loader_path
)
_dep_loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dep_loader)


_VERSION = "9.9.9"
_WHEEL_NAME = f"dummy_pkg-{_VERSION}-py3-none-any.whl"
_WHEEL_URL = f"https://files.example.com/{_WHEEL_NAME}"
_PYPI_URL = "https://pypi.org/pypi/dummy/9.9.9/json"


def _make_http_error(url: str, code: int, msg: str) -> urllib.error.HTTPError:
    """Build an ``HTTPError`` with a real, closable body.

    A bare ``HTTPError(..., fp=None)`` wraps ``None`` on Python 3.14, whose
    ``addinfourl``-style body is temp-file-like and triggers ``ResourceWarning:
    Implicitly cleaning up <HTTPError ...>`` when garbage-collected without an
    explicit ``close()``. Passing a real ``fp``/``hdrs`` here gives every
    caller something to close (see ``http_errors``).
    """
    return urllib.error.HTTPError(url=url, code=code, msg=msg, hdrs=Message(), fp=io.BytesIO(b""))


@pytest.fixture
def http_errors():
    """Factory fixture for ``HTTPError`` instances that closes every one it
    built, at teardown, so no test leaks a ``ResourceWarning``."""
    created: list[urllib.error.HTTPError] = []

    def factory(code: int, msg: str = "", url: str = _PYPI_URL) -> urllib.error.HTTPError:
        err = _make_http_error(url, code, msg)
        created.append(err)
        return err

    yield factory

    for err in created:
        try:
            err.close()
        except Exception:
            pass


def _wheel_bytes() -> bytes:
    return _wheel_bytes_with_content("x = 1\n" * 20)


def _wheel_bytes_with_content(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("dummy_pkg/__init__.py", text)
    return buf.getvalue()


def _metadata_bytes(version: str = _VERSION) -> bytes:
    return json.dumps(
        {
            "info": {"version": version},
            "urls": [{"filename": _WHEEL_NAME, "url": _WHEEL_URL}],
        }
    ).encode()


class _FakeResponse:
    """Minimal stand-in for the context-managed object ``urlopen`` returns."""

    # Explicit default matching a real non-chunked `http.client.HTTPResponse`
    # -- production code only skips the Content-Length mismatch check when
    # this is truthy (see `_ChunkedFakeResponse`), so an explicit default here
    # keeps every existing test on the "chunked=False" behaviour it exercises.
    chunked = False

    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}
        self._pos = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            chunk = self._body[self._pos :]
            self._pos = len(self._body)
            return chunk
        chunk = self._body[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    # Production reads via `read1()` (see dependency_loader.py) -- a plain
    # ``http.client.HTTPResponse.read1`` does at most one underlying read and
    # returns early with whatever is available. This fake has no underlying
    # socket to partially drain, so `read1` behaves identically to `read`.
    read1 = read


class _ChunkedFakeResponse(_FakeResponse):
    """Like ``_FakeResponse``, but reports ``chunked = True`` -- mirroring a
    real ``http.client.HTTPResponse`` whose ``Transfer-Encoding: chunked``
    made it ignore ``Content-Length`` for its own framing. A non-conformant
    server can still send both headers, so this lets tests supply a
    ``Content-Length`` that deliberately does NOT match the delivered body
    and assert the mismatch check is skipped rather than rejecting a good
    body."""

    chunked = True


class _CountingResponse(_FakeResponse):
    """Like ``_FakeResponse``, but increments a shared counter on every
    non-empty ``read()`` -- used to trigger cancellation mid-download, tied to
    actual bytes flowing rather than a brittle guess at call ordering."""

    def __init__(self, body: bytes, headers: dict, counter: dict):
        super().__init__(body, headers)
        self._counter = counter

    def read(self, n: int = -1) -> bytes:
        chunk = super().read(n)
        if chunk:
            self._counter["n"] += 1
        return chunk

    # See `_FakeResponse.read1` -- production reads via `read1()`, so this
    # override needs its own alias (a base-class `read1 = read` would bind to
    # `_FakeResponse.read`, not this override, and the counter would never
    # increment).
    read1 = read


class _PartialThenRaise:
    """Delivers one short chunk of ``body``, then raises ``exc`` on the next
    ``read()`` call -- simulating a connection that stalls mid-body after
    starting to deliver bytes. Used to prove a partial file is discarded and
    never concatenated with a later, successful attempt's bytes."""

    def __init__(self, body: bytes, headers: dict, exc: BaseException, first_chunk_size: int = 16):
        self._body = body
        self.headers = headers
        self._exc = exc
        self._first_chunk_size = first_chunk_size
        self._calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n: int = -1) -> bytes:
        self._calls += 1
        if self._calls == 1:
            return self._body[: self._first_chunk_size]
        raise self._exc

    # Production reads via `read1()` -- see `_FakeResponse.read1`.
    read1 = read


class _ClockAdvancingResponse(_FakeResponse):
    """Like ``_FakeResponse``, but advances a ``fake_clock``-style object by
    ``advance`` seconds on every non-empty ``read()`` -- used to trip the
    overall download budget deterministically, without any real sleeping."""

    def __init__(self, body: bytes, headers: dict, clock, advance: float):
        super().__init__(body, headers)
        self._clock = clock
        self._advance = advance

    def read(self, n: int = -1) -> bytes:
        chunk = super().read(n)
        if chunk:
            self._clock.t += self._advance
        return chunk

    # See `_CountingResponse.read1` -- needs its own alias for the same
    # reason (a base-class alias would bind to `_FakeResponse.read`).
    read1 = read


class _Read1OnlyResponse:
    """A response whose `read()` blows up and whose `read1()` serves the
    body -- proves production reads via `read1()`, not `read()`. See the
    `read1` vs `read` distinction documented at `_DOWNLOAD_TOTAL_BUDGET_SECONDS`
    in dependency_loader.py: `read(n)` loops on the socket until it has `n`
    bytes or EOF, which would let a slow-trickle server block a single call
    for the whole download regardless of how often the budget is checked
    between calls; `read1(n)` performs at most one underlying read."""

    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}
        self._pos = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n: int = -1) -> bytes:
        raise AssertionError("production must use read1")

    def read1(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            chunk = self._body[self._pos :]
            self._pos = len(self._body)
            return chunk
        chunk = self._body[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk


_FAKE_CLOCK_MAX_POLLS = 100_000


class _FakeClockSpinError(BaseException):
    """Raised by ``_FakeMonotonicClock.now()`` when the clock is polled
    without advancing past ``_FAKE_CLOCK_MAX_POLLS`` -- i.e. a spinning
    backoff loop under test.

    Deliberately a ``BaseException`` subclass, NOT ``Exception``: production
    code on the paths this clock drives (``_retry_network_call``,
    ``_download_and_extract_wheel``) only ever catches ``except Exception``
    (plus a couple of named exceptions like ``InterruptedError`` and
    ``_DownloadBudgetExceeded``), so a plain ``Exception``-based failure here
    would be swallowed by that broad handler and surface only indirectly, as
    ``result=False`` plus an ``on_error`` message -- not as a hard test
    failure pointing at this docstring. A ``BaseException`` subclass bypasses
    every ``except Exception`` on those paths and propagates straight out of
    the call under test, failing it directly with this exception's own
    message instead of a misleading `result is False` assertion elsewhere in
    the test.
    """


class _FakeMonotonicClock:
    """Deterministic stand-in for ``time.monotonic`` + ``_dep_loader._sleep``,
    so backoff/budget tests never actually sleep: ``sleep(s)`` just advances
    the clock by ``s`` instead of blocking.

    Defensive: ``now()`` counts its own calls and raises ``_FakeClockSpinError``
    past ``_FAKE_CLOCK_MAX_POLLS`` if the clock is never advanced in between. A
    backoff loop (e.g. ``_wait_with_ui_pump``) only terminates once
    ``_now() >= end``, and the clock only advances via the patched ``sleep``
    -- if a future regression removes the sleep call, the loop would spin
    forever under test and hang pytest. This turns that hang into a clear,
    directly-propagating test failure instead (see ``_FakeClockSpinError``'s
    docstring for why it's a ``BaseException``, not a plain ``Exception``).
    """

    def __init__(self, start: float = 0.0):
        self.t = start
        self._poll_count = 0
        self._last_seen = start

    def now(self) -> float:
        if self.t == self._last_seen:
            self._poll_count += 1
            if self._poll_count > _FAKE_CLOCK_MAX_POLLS:
                raise _FakeClockSpinError(
                    "fake clock polled without advancing — backoff loop is spinning"
                )
        else:
            self._last_seen = self.t
            self._poll_count = 0
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def fake_clock(monkeypatch):
    clock = _FakeMonotonicClock()
    monkeypatch.setattr(_dep_loader, "_now", clock.now)
    # Patches the `_dep_loader._sleep` seam (not the process-global
    # `time.sleep`) so this stays scoped to this module under test.
    monkeypatch.setattr(_dep_loader, "_sleep", clock.sleep)
    return clock


class _UrlopenScript:
    """Scripts a sequence of urlopen behaviours: each entry is either an
    exception instance (raised) or a response object (returned)."""

    def __init__(self, steps):
        self._steps = list(steps)
        self.calls: list[str] = []

    def __call__(self, url, timeout=None):
        self.calls.append(url)
        if not self._steps:
            raise AssertionError("urlopen called more times than scripted")
        step = self._steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def _run(monkeypatch, tmp_path, steps, clock, *, is_cancelled=lambda: False, chunk_size=None):
    """Shared driver for ``_download_and_extract_wheel``. Takes the fake clock
    as a parameter (callers pass the ``fake_clock`` fixture) rather than
    building its own, so a test can never end up with two independent clocks
    -- one from this helper, one from the fixture -- where the helper's would
    silently win."""
    script = _UrlopenScript(steps)
    # Patches the real stdlib urllib.request module (module-global state, not
    # scoped to _dep_loader) -- these tests are therefore serial-only, not
    # xdist-safe.
    monkeypatch.setattr(_dep_loader.urllib.request, "urlopen", script)
    monkeypatch.setattr(_dep_loader, "_find_wheel_url", lambda data: _WHEEL_URL)
    monkeypatch.setattr(_dep_loader, "_fix_windows_pyd", lambda *a, **k: None)
    # The sliced backoff wait (`_wait_with_ui_pump`) loops on `_now()` between
    # `_sleep()` calls. Patching only `_sleep` to a no-op would leave that
    # loop spinning on REAL wall-clock time until a whole backoff (1-2s)
    # elapses -- a fake clock makes `_sleep()` advance time instead of
    # blocking, so backoff-triggering tests finish instantly. Patches the
    # `_dep_loader._sleep` seam, not the process-global `time.sleep`.
    monkeypatch.setattr(_dep_loader, "_now", clock.now)
    monkeypatch.setattr(_dep_loader, "_sleep", clock.sleep)
    if chunk_size is not None:
        monkeypatch.setattr(_dep_loader, "_DOWNLOAD_CHUNK_SIZE", chunk_size)

    statuses: list[str] = []
    progresses: list[int] = []
    errors: list[str] = []
    cache_dir = tmp_path / "pkg_cache"

    result = _dep_loader._download_and_extract_wheel(
        display_name="dummy",
        pypi_url=_PYPI_URL,
        expected_version=_VERSION,
        cache_dir=cache_dir,
        package_subdir="dummy_pkg",
        on_status=statuses.append,
        on_progress=progresses.append,
        is_cancelled=is_cancelled,
        on_error=errors.append,
        yield_ui=lambda: None,
    )
    return result, script, statuses, progresses, errors, cache_dir


@pytest.fixture(autouse=True)
def _restore_sys_path():
    """A successful download inserts ``cache_dir`` into ``sys.path``. These
    tests use tmp_path cache dirs, so that entry must never survive the test."""
    original = list(sys.path)
    try:
        yield
    finally:
        sys.path[:] = original


def _stale_siblings(cache_dir: Path) -> list[Path]:
    if not cache_dir.parent.exists():
        return []
    return [
        p
        for p in cache_dir.parent.iterdir()
        if p.name.startswith(cache_dir.name + ".tmp-")
        or p.name.startswith(cache_dir.name + ".old-")
    ]


# ---------------------------------------------------------------------------
# (a) timeout once then succeed -> overall success, expected attempt count
# ---------------------------------------------------------------------------

def test_timeout_once_then_succeeds(monkeypatch, tmp_path, fake_clock):
    wheel = _wheel_bytes()
    steps = [
        TimeoutError("stalled"),
        _FakeResponse(_metadata_bytes()),
        _FakeResponse(wheel, headers={"Content-Length": str(len(wheel))}),
    ]
    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch, tmp_path, steps, fake_clock
    )

    assert result is True
    assert len(script.calls) == 3  # failed metadata attempt + retry + wheel
    assert (cache_dir / ".complete").exists()
    assert (cache_dir / ".version").read_text() == _VERSION
    assert not errors
    assert not _stale_siblings(cache_dir)


# ---------------------------------------------------------------------------
# (b) always times out -> failure after bounded attempts, no partial file,
# previous/legacy cache left untouched
# ---------------------------------------------------------------------------

def test_always_times_out_fails_after_bounded_attempts_leaves_cache_untouched(
    monkeypatch, tmp_path, fake_clock
):
    # A pre-existing "good" cache -- must survive a failed re-download attempt.
    cache_dir = tmp_path / "pkg_cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / ".complete").touch()
    (cache_dir / ".version").write_text("1.0.0")
    (cache_dir / "marker.txt").write_text("previously good cache")

    steps = [TimeoutError("stalled")] * _dep_loader._DOWNLOAD_MAX_ATTEMPTS
    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch, tmp_path, steps, fake_clock
    )

    assert result is False
    assert len(script.calls) == _dep_loader._DOWNLOAD_MAX_ATTEMPTS
    assert errors  # on_error was called
    # Existing cache is completely untouched.
    assert (cache_dir / ".version").read_text() == "1.0.0"
    assert (cache_dir / "marker.txt").read_text() == "previously good cache"
    # No orphaned temp/old siblings left behind.
    assert not _stale_siblings(cache_dir)


# ---------------------------------------------------------------------------
# (c) HTTP 404 -> no retry
# ---------------------------------------------------------------------------

def test_http_404_is_not_retried(monkeypatch, tmp_path, fake_clock, http_errors):
    steps = [http_errors(404, msg="Not Found")]
    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch, tmp_path, steps, fake_clock
    )

    assert result is False
    assert len(script.calls) == 1
    assert errors
    assert not cache_dir.exists()


# ---------------------------------------------------------------------------
# (d) HTTP 503 -> retried
# ---------------------------------------------------------------------------

def test_http_503_is_retried(monkeypatch, tmp_path, fake_clock, http_errors):
    wheel = _wheel_bytes()
    steps = [
        http_errors(503, msg="Service Unavailable"),
        _FakeResponse(_metadata_bytes()),
        _FakeResponse(wheel, headers={"Content-Length": str(len(wheel))}),
    ]
    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch, tmp_path, steps, fake_clock
    )

    assert result is True
    assert len(script.calls) == 3


def test_http_503_exhausts_retries_and_fails(monkeypatch, tmp_path, fake_clock, http_errors):
    steps = [
        http_errors(503, msg="Service Unavailable")
    ] * _dep_loader._DOWNLOAD_MAX_ATTEMPTS
    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch, tmp_path, steps, fake_clock
    )

    assert result is False
    assert len(script.calls) == _dep_loader._DOWNLOAD_MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# (e) cancel mid-download -> no retry, cleanup done
# ---------------------------------------------------------------------------

def test_cancel_mid_download_no_retry_and_cleans_up(monkeypatch, tmp_path, fake_clock):
    wheel = _wheel_bytes()
    counter = {"n": 0}
    steps = [
        _FakeResponse(_metadata_bytes()),
        _CountingResponse(
            wheel, headers={"Content-Length": str(len(wheel))}, counter=counter
        ),
    ]

    # Cancel as soon as the first chunk of wheel data has actually been read
    # -- tied to real download progress, not a guess at unrelated call counts
    # (e.g. is_cancelled() calls made during the metadata phase).
    def is_cancelled():
        return counter["n"] >= 1

    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch,
        tmp_path,
        steps,
        fake_clock,
        is_cancelled=is_cancelled,
        chunk_size=16,  # force multiple reads so cancellation lands mid-download
    )

    assert result is False
    assert len(script.calls) == 2  # metadata + exactly one (cancelled) wheel attempt
    assert not errors  # a cancel is not a reported error
    assert not cache_dir.exists()
    assert not _stale_siblings(cache_dir)


# ---------------------------------------------------------------------------
# (f) progress callback: increasing percentages + final 100 when
# Content-Length is known; no crash when it's absent
# ---------------------------------------------------------------------------

def test_progress_increases_and_reaches_100_with_content_length(
    monkeypatch, tmp_path, fake_clock
):
    wheel = _wheel_bytes()
    steps = [
        _FakeResponse(_metadata_bytes()),
        _FakeResponse(wheel, headers={"Content-Length": str(len(wheel))}),
    ]
    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch, tmp_path, steps, fake_clock, chunk_size=16
    )

    assert result is True
    assert progresses == sorted(progresses)  # never decreases
    assert progresses[-1] == 100
    # At least one intermediate download-phase percentage was reported.
    assert any(10 <= p <= 80 for p in progresses)


def test_progress_does_not_crash_without_content_length(monkeypatch, tmp_path, fake_clock):
    wheel = _wheel_bytes()
    steps = [
        _FakeResponse(_metadata_bytes()),
        _FakeResponse(wheel, headers={}),  # no Content-Length at all
    ]
    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch, tmp_path, steps, fake_clock
    )

    assert result is True
    assert not errors
    assert progresses[-1] == 100


# ---------------------------------------------------------------------------
# (g) wheel-phase retry: the retry policy applies to the wheel fetch too, not
# just the metadata fetch.
# ---------------------------------------------------------------------------

def test_wheel_phase_retry_after_timeout_then_succeeds(monkeypatch, tmp_path, fake_clock):
    wheel = _wheel_bytes()
    steps = [
        _FakeResponse(_metadata_bytes()),
        TimeoutError("stalled"),
        _FakeResponse(wheel, headers={"Content-Length": str(len(wheel))}),
    ]
    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch, tmp_path, steps, fake_clock
    )

    assert result is True
    assert len(script.calls) == 3  # metadata + failed wheel attempt + retry wheel
    assert not errors


def test_wheel_phase_exhaustion_leaves_no_tmp_sibling_or_partial_whl(
    monkeypatch, tmp_path, fake_clock
):
    steps = [_FakeResponse(_metadata_bytes())] + [
        TimeoutError("stalled")
    ] * _dep_loader._DOWNLOAD_MAX_ATTEMPTS
    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch, tmp_path, steps, fake_clock
    )

    assert result is False
    assert len(script.calls) == 1 + _dep_loader._DOWNLOAD_MAX_ATTEMPTS
    assert errors
    # This exercises the temp-dir cleanup path the metadata-phase exhaustion
    # test never reaches (that one fails before a temp dir is even created).
    assert not _stale_siblings(cache_dir)
    assert not list(cache_dir.parent.glob("**/*.whl"))


def test_partial_wheel_file_never_reused_on_retry(monkeypatch, tmp_path, fake_clock):
    """The headline guarantee: a partial file from a failed/cancelled attempt
    is discarded, never concatenated with a later successful attempt's bytes.
    Verified by giving the two attempts DIFFERENT wheel contents and checking
    the final extracted file matches ONLY the second attempt's bytes.

    Also pins the partial-file `unlink` itself: `_fetch_wheel_bytes` opens
    the destination with `"wb"` (truncating), so the end-to-end byte
    assertion alone would still pass even if the `unlink()` in the failure
    path were deleted. A probe during backoff inspects the temp dir directly
    and records whether any partial `.whl` is present there -- catching a
    regression that removes the unlink but happens to still overwrite the
    file content on the next attempt. The probe call count is asserted to be
    at least 1 so a future change that skips the backoff entirely (e.g. the
    backoff schedule collapsing to 0s) can't leave `saw_whl_during_backoff`
    vacuously `False` because the probe never ran at all.
    """
    first_wheel = _wheel_bytes_with_content("FIRST\n" * 500)
    second_wheel = _wheel_bytes_with_content("SECOND\n" * 500)

    cache_dir = tmp_path / "pkg_cache"
    saw_whl_during_backoff = {"flag": False}
    probe_calls = {"n": 0}

    def probing_sleep(seconds: float) -> None:
        probe_calls["n"] += 1
        if any(cache_dir.parent.glob("**/*.whl")):
            saw_whl_during_backoff["flag"] = True
        fake_clock.t += seconds

    monkeypatch.setattr(_dep_loader, "_find_wheel_url", lambda data: _WHEEL_URL)
    monkeypatch.setattr(_dep_loader, "_fix_windows_pyd", lambda *a, **k: None)
    monkeypatch.setattr(_dep_loader, "_now", fake_clock.now)
    monkeypatch.setattr(_dep_loader, "_sleep", probing_sleep)

    steps = [
        _FakeResponse(_metadata_bytes()),
        _PartialThenRaise(
            first_wheel,
            headers={"Content-Length": str(len(first_wheel))},
            exc=TimeoutError("stalled mid-body"),
        ),
        _FakeResponse(second_wheel, headers={"Content-Length": str(len(second_wheel))}),
    ]
    script = _UrlopenScript(steps)
    monkeypatch.setattr(_dep_loader.urllib.request, "urlopen", script)

    result = _dep_loader._download_and_extract_wheel(
        display_name="dummy",
        pypi_url=_PYPI_URL,
        expected_version=_VERSION,
        cache_dir=cache_dir,
        package_subdir="dummy_pkg",
        on_status=lambda *_: None,
        on_progress=lambda *_: None,
        is_cancelled=lambda: False,
        on_error=lambda *_: None,
        yield_ui=lambda: None,
    )

    assert result is True
    extracted = (cache_dir / "dummy_pkg" / "__init__.py").read_text()
    assert extracted == "SECOND\n" * 500
    assert "FIRST" not in extracted
    assert probe_calls["n"] >= 1  # the probe must actually have run
    assert saw_whl_during_backoff["flag"] is False


# ---------------------------------------------------------------------------
# (h) no-Content-Length download: never garbage progress, yield_ui fires on
# every chunk (not just when the percentage is known), and cancel is honoured
# between chunks even without a known total size.
# ---------------------------------------------------------------------------

def test_no_content_length_download_completes_with_clean_progress_and_yields(
    monkeypatch, tmp_path
):
    wheel = _wheel_bytes()
    yield_calls = {"n": 0}

    def yield_ui():
        yield_calls["n"] += 1

    monkeypatch.setattr(_dep_loader, "_find_wheel_url", lambda data: _WHEEL_URL)
    monkeypatch.setattr(_dep_loader, "_fix_windows_pyd", lambda *a, **k: None)
    monkeypatch.setattr(_dep_loader, "_sleep", lambda *_: None)
    monkeypatch.setattr(_dep_loader, "_DOWNLOAD_CHUNK_SIZE", 16)

    steps = [
        _FakeResponse(_metadata_bytes()),
        _FakeResponse(wheel, headers={}),  # no Content-Length at all
    ]
    script = _UrlopenScript(steps)
    monkeypatch.setattr(_dep_loader.urllib.request, "urlopen", script)

    cache_dir = tmp_path / "pkg_cache"
    progresses: list[int] = []
    result = _dep_loader._download_and_extract_wheel(
        display_name="dummy",
        pypi_url=_PYPI_URL,
        expected_version=_VERSION,
        cache_dir=cache_dir,
        package_subdir="dummy_pkg",
        on_status=lambda *_: None,
        on_progress=progresses.append,
        is_cancelled=lambda: False,
        on_error=lambda *_: None,
        yield_ui=yield_ui,
    )

    assert result is True
    # No garbage intermediate percentages -- Content-Length was never known, so
    # only the fixed lifecycle percentages (0/10/85/100) are ever reported.
    assert progresses == [0, 10, 85, 100]
    expected_min_chunks = (len(wheel) + 15) // 16
    assert yield_calls["n"] >= expected_min_chunks


def test_no_content_length_honours_cancel_between_chunks(monkeypatch, tmp_path, fake_clock):
    wheel = _wheel_bytes()
    counter = {"n": 0}
    steps = [
        _FakeResponse(_metadata_bytes()),
        _CountingResponse(wheel, headers={}, counter=counter),
    ]

    def is_cancelled():
        return counter["n"] >= 1

    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch, tmp_path, steps, fake_clock, is_cancelled=is_cancelled, chunk_size=16
    )

    assert result is False
    assert not errors
    assert not cache_dir.exists()
    assert not _stale_siblings(cache_dir)


# ---------------------------------------------------------------------------
# (i) sliced, UI-pumping, cancellable backoff (`_wait_with_ui_pump` via
# `_retry_network_call`)
# ---------------------------------------------------------------------------

def test_retry_network_call_yields_ui_during_backoff(fake_clock):
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("stalled")
        return "ok"

    yield_calls = {"n": 0}

    def yield_ui():
        yield_calls["n"] += 1

    result = _dep_loader._retry_network_call(
        fn,
        description="test",
        on_status=lambda *_: None,
        is_cancelled=lambda: False,
        yield_ui=yield_ui,
        deadline=None,
    )

    assert result == "ok"
    assert calls["n"] == 2
    assert yield_calls["n"] > 0  # the backoff wait pumped the UI


def test_cancel_during_backoff_stops_further_attempts_and_cleans_up(
    monkeypatch, tmp_path, fake_clock
):
    """A cancel flip DURING the backoff wait (not just before an attempt)
    must be honoured immediately: no further network attempt, and the same
    cleanup an ordinary pre-attempt cancel gets."""
    monkeypatch.setattr(_dep_loader, "_find_wheel_url", lambda data: _WHEEL_URL)
    monkeypatch.setattr(_dep_loader, "_fix_windows_pyd", lambda *a, **k: None)

    cancelled = {"flag": False}
    yield_calls = {"n": 0}

    def yield_ui():
        yield_calls["n"] += 1
        if yield_calls["n"] >= 3:
            cancelled["flag"] = True

    steps = [TimeoutError("stalled")]  # the only metadata attempt -- triggers backoff
    script = _UrlopenScript(steps)
    monkeypatch.setattr(_dep_loader.urllib.request, "urlopen", script)

    cache_dir = tmp_path / "pkg_cache"
    errors: list[str] = []
    result = _dep_loader._download_and_extract_wheel(
        display_name="dummy",
        pypi_url=_PYPI_URL,
        expected_version=_VERSION,
        cache_dir=cache_dir,
        package_subdir="dummy_pkg",
        on_status=lambda *_: None,
        on_progress=lambda *_: None,
        is_cancelled=lambda: cancelled["flag"],
        on_error=errors.append,
        yield_ui=yield_ui,
    )

    assert result is False
    assert len(script.calls) == 1  # cancelled during backoff -- no retry attempt made
    assert not errors  # a cancel is not a reported error
    assert not cache_dir.exists()
    assert not _stale_siblings(cache_dir)
    assert yield_calls["n"] >= 3


def test_bumped_max_attempts_does_not_index_error_on_backoff(monkeypatch, fake_clock):
    """`_DOWNLOAD_RETRY_BACKOFF_SECONDS` has 2 elements; bumping
    `_DOWNLOAD_MAX_ATTEMPTS` past that must never IndexError when indexing
    the backoff schedule for the later attempts."""
    monkeypatch.setattr(_dep_loader, "_DOWNLOAD_MAX_ATTEMPTS", 5)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise TimeoutError("stalled")

    with pytest.raises(TimeoutError):
        _dep_loader._retry_network_call(
            fn,
            description="test",
            on_status=lambda *_: None,
            is_cancelled=lambda: False,
            yield_ui=lambda: None,
            deadline=None,
        )

    assert calls["n"] == 5


# ---------------------------------------------------------------------------
# (j) overall download budget (`_DOWNLOAD_TOTAL_BUDGET_SECONDS`) -- terminal,
# never retried, bounds the whole network phase regardless of per-socket
# timeouts.
# ---------------------------------------------------------------------------

def test_budget_exceeded_mid_chunk_is_terminal_and_not_retried(
    monkeypatch, tmp_path, fake_clock
):
    wheel = _wheel_bytes()
    steps = [
        _FakeResponse(_metadata_bytes()),
        _ClockAdvancingResponse(
            wheel,
            {"Content-Length": str(len(wheel))},
            fake_clock,
            _dep_loader._DOWNLOAD_TOTAL_BUDGET_SECONDS + 1,
        ),
    ]
    monkeypatch.setattr(_dep_loader, "_find_wheel_url", lambda data: _WHEEL_URL)
    monkeypatch.setattr(_dep_loader, "_fix_windows_pyd", lambda *a, **k: None)
    monkeypatch.setattr(_dep_loader, "_DOWNLOAD_CHUNK_SIZE", 16)
    script = _UrlopenScript(steps)
    monkeypatch.setattr(_dep_loader.urllib.request, "urlopen", script)

    cache_dir = tmp_path / "pkg_cache"
    errors: list[str] = []
    result = _dep_loader._download_and_extract_wheel(
        display_name="dummy",
        pypi_url=_PYPI_URL,
        expected_version=_VERSION,
        cache_dir=cache_dir,
        package_subdir="dummy_pkg",
        on_status=lambda *_: None,
        on_progress=lambda *_: None,
        is_cancelled=lambda: False,
        on_error=errors.append,
        yield_ui=lambda: None,
    )

    assert result is False
    assert len(script.calls) == 2  # metadata + exactly one (budget-blown) wheel attempt
    assert errors  # unlike a cancel, budget exhaustion IS reported to the user
    assert not cache_dir.exists()
    assert not _stale_siblings(cache_dir)


def test_budget_exceeded_during_backoff_is_terminal(monkeypatch, tmp_path, fake_clock):
    monkeypatch.setattr(_dep_loader, "_find_wheel_url", lambda data: _WHEEL_URL)
    monkeypatch.setattr(_dep_loader, "_fix_windows_pyd", lambda *a, **k: None)

    steps = [TimeoutError("stalled")]  # the only metadata attempt -- triggers backoff
    script = _UrlopenScript(steps)
    monkeypatch.setattr(_dep_loader.urllib.request, "urlopen", script)

    def blow_budget_yield_ui():
        # `_download_and_extract_wheel` calls `yield_ui()` once BEFORE the
        # first network attempt too. Advancing the clock unconditionally
        # would trip the pre-attempt budget check and `urlopen` would never
        # be called at all -- this test is specifically about the budget
        # tripping DURING BACKOFF, i.e. only once an attempt has actually
        # happened.
        if script.calls:
            fake_clock.t += _dep_loader._DOWNLOAD_TOTAL_BUDGET_SECONDS + 1

    cache_dir = tmp_path / "pkg_cache"
    errors: list[str] = []
    result = _dep_loader._download_and_extract_wheel(
        display_name="dummy",
        pypi_url=_PYPI_URL,
        expected_version=_VERSION,
        cache_dir=cache_dir,
        package_subdir="dummy_pkg",
        on_status=lambda *_: None,
        on_progress=lambda *_: None,
        is_cancelled=lambda: False,
        on_error=errors.append,
        yield_ui=blow_budget_yield_ui,
    )

    assert result is False
    assert len(script.calls) == 1  # never retried once the budget trips during backoff
    assert errors
    assert any(
        f"{_dep_loader._DOWNLOAD_TOTAL_BUDGET_SECONDS} seconds" in msg for msg in errors
    )
    assert not cache_dir.exists()
    assert not _stale_siblings(cache_dir)


def test_budget_already_exceeded_before_first_attempt_is_terminal_with_zero_calls(
    monkeypatch, tmp_path, fake_clock
):
    """If the budget is already blown before the very first network attempt
    (e.g. a caller that reuses a deadline across calls), the download must
    fail terminally without ever calling `urlopen` -- not attempt once and
    then fail during backoff."""
    monkeypatch.setattr(_dep_loader, "_find_wheel_url", lambda data: _WHEEL_URL)
    monkeypatch.setattr(_dep_loader, "_fix_windows_pyd", lambda *a, **k: None)

    steps: list = []  # urlopen must never be called
    script = _UrlopenScript(steps)
    monkeypatch.setattr(_dep_loader.urllib.request, "urlopen", script)

    def blow_budget_yield_ui():
        fake_clock.t += _dep_loader._DOWNLOAD_TOTAL_BUDGET_SECONDS + 1

    cache_dir = tmp_path / "pkg_cache"
    errors: list[str] = []
    result = _dep_loader._download_and_extract_wheel(
        display_name="dummy",
        pypi_url=_PYPI_URL,
        expected_version=_VERSION,
        cache_dir=cache_dir,
        package_subdir="dummy_pkg",
        on_status=lambda *_: None,
        on_progress=lambda *_: None,
        is_cancelled=lambda: False,
        on_error=errors.append,
        yield_ui=blow_budget_yield_ui,
    )

    assert result is False
    assert len(script.calls) == 0  # budget already blown before the first attempt
    assert errors
    assert any(
        f"{_dep_loader._DOWNLOAD_TOTAL_BUDGET_SECONDS} seconds" in msg for msg in errors
    )
    assert not cache_dir.exists()
    assert not _stale_siblings(cache_dir)


# ---------------------------------------------------------------------------
# (j2) production reads via `read1()`, not `read()` -- a `read()` loops on
# the socket until it has the full chunk or EOF, which would let a
# slow-trickle server block a single call for the whole download and defeat
# the budget check between calls (see `_DOWNLOAD_TOTAL_BUDGET_SECONDS`).
# ---------------------------------------------------------------------------

def test_metadata_fetch_uses_read1_not_read(monkeypatch, tmp_path, fake_clock):
    steps = [
        _Read1OnlyResponse(_metadata_bytes()),
        _FakeResponse(_wheel_bytes(), headers={"Content-Length": str(len(_wheel_bytes()))}),
    ]
    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch, tmp_path, steps, fake_clock
    )

    assert result is True
    assert not errors


def test_wheel_fetch_uses_read1_not_read(monkeypatch, tmp_path, fake_clock):
    steps = [
        _FakeResponse(_metadata_bytes()),
        _Read1OnlyResponse(
            _wheel_bytes(), headers={"Content-Length": str(len(_wheel_bytes()))}
        ),
    ]
    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch, tmp_path, steps, fake_clock
    )

    assert result is True
    assert not errors


# ---------------------------------------------------------------------------
# (j3) truncated-body detection, restoring the guarantee `urlretrieve` used to
# provide (`if size >= 0 and read < size: raise ContentTooShortError`), lost
# when it was replaced by the chunked `read1()`-based fetch. A server that
# closes mid-body yields `b""` with no exception, which -- without an
# explicit length check -- looks identical to a clean EOF.
# ---------------------------------------------------------------------------

def test_wheel_content_length_mismatch_short_is_retried_and_succeeds(
    monkeypatch, tmp_path, fake_clock
):
    full_wheel = _wheel_bytes()
    short_body = full_wheel[: len(full_wheel) // 2]
    steps = [
        _FakeResponse(_metadata_bytes()),
        _FakeResponse(short_body, headers={"Content-Length": str(len(full_wheel))}),
        _FakeResponse(full_wheel, headers={"Content-Length": str(len(full_wheel))}),
    ]
    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch, tmp_path, steps, fake_clock
    )

    assert result is True
    assert len(script.calls) == 3  # metadata + short (failed) wheel attempt + retry
    assert not errors
    assert not _stale_siblings(cache_dir)
    extracted = (cache_dir / "dummy_pkg" / "__init__.py").read_text()
    assert extracted == "x = 1\n" * 20  # the SECOND (complete) response's content


def test_wheel_content_length_mismatch_always_short_fails_after_bounded_attempts(
    monkeypatch, tmp_path, fake_clock
):
    full_wheel = _wheel_bytes()
    short_body = full_wheel[: len(full_wheel) // 2]
    steps = [_FakeResponse(_metadata_bytes())] + [
        _FakeResponse(short_body, headers={"Content-Length": str(len(full_wheel))})
        for _ in range(_dep_loader._DOWNLOAD_MAX_ATTEMPTS)
    ]
    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch, tmp_path, steps, fake_clock
    )

    assert result is False
    assert len(script.calls) == 1 + _dep_loader._DOWNLOAD_MAX_ATTEMPTS
    assert errors
    assert not cache_dir.exists()
    assert not _stale_siblings(cache_dir)
    assert not list(cache_dir.parent.glob("**/*.whl"))


def test_wheel_content_length_mismatch_long_is_also_treated_as_invalid(
    monkeypatch, tmp_path, fake_clock
):
    """A body LONGER than the advertised Content-Length is treated the same
    as a short body: invalid framing, retried like any other network-class
    failure. This is a deliberate choice (documented on `_fetch_wheel_bytes`
    and `_fetch_metadata_json`) -- either direction of mismatch means the
    server's byte count cannot be trusted, so neither is accepted as success
    on its own.
    """
    full_wheel = _wheel_bytes()
    short_declared_length = len(full_wheel) - 5  # body will be LONGER than this
    steps = [
        _FakeResponse(_metadata_bytes()),
        _FakeResponse(full_wheel, headers={"Content-Length": str(short_declared_length)}),
        _FakeResponse(full_wheel, headers={"Content-Length": str(len(full_wheel))}),
    ]
    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch, tmp_path, steps, fake_clock
    )

    assert result is True
    assert len(script.calls) == 3  # metadata + over-length (failed) wheel attempt + retry
    assert not errors


def test_metadata_content_length_mismatch_short_is_retried_and_succeeds(
    monkeypatch, tmp_path, fake_clock
):
    full_metadata = _metadata_bytes()
    short_metadata = full_metadata[: len(full_metadata) // 2]
    wheel = _wheel_bytes()
    steps = [
        _FakeResponse(short_metadata, headers={"Content-Length": str(len(full_metadata))}),
        _FakeResponse(full_metadata, headers={"Content-Length": str(len(full_metadata))}),
        _FakeResponse(wheel, headers={"Content-Length": str(len(wheel))}),
    ]
    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch, tmp_path, steps, fake_clock
    )

    assert result is True
    assert len(script.calls) == 3  # short (failed) metadata attempt + retry + wheel
    assert not errors


def test_no_content_length_header_skips_truncation_check_entirely(
    monkeypatch, tmp_path, fake_clock
):
    """No `Content-Length` header at all -- the length check never runs, so
    the download succeeds regardless of the actual byte count (already
    covered less directly by `test_progress_does_not_crash_without_content_
    length`; this test pins the truncation-check-specific contract)."""
    wheel = _wheel_bytes()
    steps = [
        _FakeResponse(_metadata_bytes()),
        _FakeResponse(wheel, headers={}),
    ]
    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch, tmp_path, steps, fake_clock
    )

    assert result is True
    assert not errors


def test_wheel_chunked_response_skips_content_length_mismatch_check(
    monkeypatch, tmp_path, fake_clock
):
    """A chunked response (`chunked = True`) carrying a `Content-Length` that
    does NOT match the delivered body must still succeed on the first
    attempt: `http.client` ignores `Content-Length` for a chunked response's
    own framing, so a non-conformant server sending both must not have the
    (irrelevant) header used to reject a perfectly good body."""
    wheel = _wheel_bytes()
    steps = [
        _FakeResponse(_metadata_bytes()),
        _ChunkedFakeResponse(wheel, headers={"Content-Length": str(len(wheel) * 2)}),
    ]
    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch, tmp_path, steps, fake_clock
    )

    assert result is True
    assert len(script.calls) == 2  # metadata + wheel -- no retry
    assert not errors


def test_metadata_chunked_response_skips_content_length_mismatch_check(
    monkeypatch, tmp_path, fake_clock
):
    """Same as the wheel-fetch case above, but for `_fetch_metadata_json`."""
    full_metadata = _metadata_bytes()
    wheel = _wheel_bytes()
    steps = [
        _ChunkedFakeResponse(
            full_metadata, headers={"Content-Length": str(len(full_metadata) * 2)}
        ),
        _FakeResponse(wheel, headers={"Content-Length": str(len(wheel))}),
    ]
    result, script, statuses, progresses, errors, cache_dir = _run(
        monkeypatch, tmp_path, steps, fake_clock
    )

    assert result is True
    assert len(script.calls) == 2  # metadata + wheel -- no retry
    assert not errors


# ---------------------------------------------------------------------------
# (k) `_is_retryable_network_error` classification table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "exc, expected",
    [
        (urllib.error.URLError("x"), True),
        (urllib.error.URLError(TimeoutError()), True),
        (TimeoutError(), True),
        (ConnectionResetError(), True),
        (http.client.RemoteDisconnected("x"), True),
        (ssl.SSLError("x"), True),
        (http.client.IncompleteRead(b""), True),
        (urllib.error.ContentTooShortError("length mismatch", None), True),
        (OSError(28, "No space left"), False),
        (InterruptedError(), False),
        (ValueError(), False),
        (json.JSONDecodeError("x", "y", 0), False),
    ],
)
def test_is_retryable_network_error_table(exc, expected):
    assert _dep_loader._is_retryable_network_error(exc) is expected


# HTTPError cases are split into their own parametrized test (rather than
# living in the table above) so each instance is built via the `http_errors`
# fixture -- which closes them at teardown -- instead of at collection time,
# where nothing would ever close them.
@pytest.mark.parametrize(
    "code, msg, expected",
    [
        (404, "Not Found", False),
        (403, "Forbidden", False),
        (500, "Server Error", True),
        (503, "Unavailable", True),
    ],
)
def test_is_retryable_network_error_table_http_error(http_errors, code, msg, expected):
    exc = http_errors(code, msg=msg, url="x")
    assert _dep_loader._is_retryable_network_error(exc) is expected


def test_is_retryable_network_error_budget_exceeded_is_never_retried():
    exc = _dep_loader._DownloadBudgetExceeded("budget blown")
    assert _dep_loader._is_retryable_network_error(exc) is False
