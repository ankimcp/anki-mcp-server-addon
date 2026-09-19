"""Unit tests for ``file_log.release_log_handle`` -- the helper that logs an
abort/release reason and then closes the file-log handler.

Shared by two call sites: ``__init__.py``'s dependency-gate abort branches
(the add-on gives up importing) and ``native_cache_cleanup``'s
``on_addon_manager_will_install_addon`` handler (releasing the handle before
Anki's own install/update path backs up ``user_files``, an ``os.rename`` a
held-open ``ankimcp.log`` blocks on Windows).

All tests reset file logging to a known "disabled" state before and after
they run, so they never leak a handler into other test modules that import
``anki_mcp_server`` in the same process.
"""
from __future__ import annotations

import logging

import pytest

import anki_mcp_server  # noqa: F401 -- triggers vendor path setup

from anki_mcp_server import file_log


def _reset():
    file_log.init_file_logging(enabled=False, user_files_dir=None)
    file_log.get_logger().setLevel(logging.NOTSET)


@pytest.fixture(autouse=True)
def _clean_file_logging():
    _reset()
    try:
        yield
    finally:
        _reset()


def test_release_log_handle_removes_and_closes_handler(tmp_path):
    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)
    handler = file_log._existing_handler(file_log.get_logger())
    assert handler is not None
    stream = handler.stream

    file_log.release_log_handle("test abort reason")

    assert file_log._existing_handler(file_log.get_logger()) is None
    assert file_log.is_enabled() is False
    assert stream.closed is True
    assert handler.stream is None


def test_release_log_handle_logs_reason_before_closing(tmp_path):
    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)

    file_log.release_log_handle("distinctive abort reason 12345")

    log_path = tmp_path / file_log._LOG_FILENAME
    content = log_path.read_text(encoding="utf-8")
    assert "distinctive abort reason 12345" in content


def test_release_log_handle_idempotent(tmp_path):
    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)

    file_log.release_log_handle("first release")
    # Second call must be a safe no-op -- no handler left to remove/close.
    file_log.release_log_handle("second release")

    assert file_log._existing_handler(file_log.get_logger()) is None


def test_release_log_handle_noop_when_logging_never_enabled():
    assert file_log._existing_handler(file_log.get_logger()) is None

    file_log.release_log_handle("never enabled")  # must not raise

    assert file_log._existing_handler(file_log.get_logger()) is None


def test_release_log_handle_never_raises_when_logging_the_reason_fails(
    monkeypatch, tmp_path
):
    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)

    real_logger = file_log.get_logger()

    class _ErrorRaisingLoggerProxy:
        """Delegates everything to the real logger except ``.error()``, which
        raises. Narrower than patching ``logging.Logger.error`` at the class
        level -- that would break ``.error()`` for every logger in the
        process, not just the one ``release_log_handle`` calls."""

        def error(self, *args, **kwargs):
            raise RuntimeError("logging blew up")

        def __getattr__(self, name):
            return getattr(real_logger, name)

    monkeypatch.setattr(file_log, "get_logger", lambda: _ErrorRaisingLoggerProxy())

    file_log.release_log_handle("reason")  # must not raise

    # Teardown still ran despite the logging failure.
    assert file_log._existing_handler(real_logger) is None


def test_release_log_handle_stale_handler_never_reopens_the_file(tmp_path):
    """A logger elsewhere in the process may still hold a reference to the
    now-detached handler after ``release_log_handle`` runs. ``FileHandler``
    re-opens its file on ``emit()`` whenever ``self.stream`` is ``None``
    (mode is ``'a'``, not ``'w'``) -- so a record delivered through that
    stale reference must never reach ``emit()`` in the first place. This is
    exactly what ``callHandlers`` guarantees by comparing
    ``record.levelno >= hdlr.level`` before calling ``handle()``."""
    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)
    handler = file_log._existing_handler(file_log.get_logger())
    assert handler is not None
    log_path = tmp_path / file_log._LOG_FILENAME

    file_log.release_log_handle("release before staleness check")
    size_after_release = log_path.stat().st_size

    # The handler was made inert before removal -- its level must now reject
    # everything callHandlers could throw at it, including CRITICAL.
    assert handler.level > logging.CRITICAL

    # Simulate a logger elsewhere in the process that still references the
    # stale handler (e.g. captured before release_log_handle ran).
    stale_logger = logging.getLogger("ankimcp_stale_handler_probe")
    stale_logger.addHandler(handler)
    stale_logger.setLevel(logging.DEBUG)
    try:
        stale_logger.error("this must never reach the closed file")
    finally:
        stale_logger.removeHandler(handler)

    assert handler.stream is None
    assert log_path.stat().st_size == size_after_release
