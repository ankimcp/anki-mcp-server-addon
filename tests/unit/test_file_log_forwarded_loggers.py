"""Unit tests for ``file_log``'s forwarding of third-party logger records --
specifically ``mcp.server.transport_security``, whose Host/Origin rejection
WARNINGs don't propagate into the addon's own logger and would otherwise
never reach ``ankimcp.log`` (observability gap noted in #78).

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


def test_forwarded_logger_record_reaches_the_file(tmp_path):
    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)

    fw_logger = logging.getLogger("mcp.server.transport_security")
    # We must not have altered the third-party logger's own behaviour --
    # only attached our handler to it.
    assert fw_logger.propagate is True
    assert fw_logger.level == logging.NOTSET

    fw_logger.warning("Invalid Host header: evil.example:3141")

    log_path = tmp_path / file_log._LOG_FILENAME
    content = log_path.read_text(encoding="utf-8")
    assert "Invalid Host header: evil.example:3141" in content


def test_forwarded_logger_redacts_registered_secret(tmp_path):
    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)
    file_log.register_secret("s3cr3t-api-key-value")

    logging.getLogger("mcp.server.transport_security").warning(
        "rejected request with key s3cr3t-api-key-value"
    )

    log_path = tmp_path / file_log._LOG_FILENAME
    content = log_path.read_text(encoding="utf-8")
    assert "s3cr3t-api-key-value" not in content
    assert "***REDACTED***" in content


def test_forwarded_logger_redacts_bearer_token(tmp_path):
    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)

    logging.getLogger("mcp.server.transport_security").warning(
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789"
    )

    log_path = tmp_path / file_log._LOG_FILENAME
    content = log_path.read_text(encoding="utf-8")
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in content
    assert "***REDACTED***" in content


def test_teardown_detaches_forwarded_logger(tmp_path):
    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)
    fw_logger = logging.getLogger("mcp.server.transport_security")
    handler = file_log._existing_handler(file_log.get_logger())
    assert handler is not None
    assert handler in fw_logger.handlers

    file_log.init_file_logging(enabled=False, user_files_dir=None)

    assert handler not in fw_logger.handlers


def test_init_twice_does_not_double_attach_forwarded_logger(tmp_path):
    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)
    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)

    fw_logger = logging.getLogger("mcp.server.transport_security")
    tagged = [h for h in fw_logger.handlers if getattr(h, file_log._HANDLER_TAG, False)]
    assert len(tagged) == 1


def test_root_logger_never_gets_our_handler(tmp_path):
    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)

    root = logging.getLogger()
    assert not any(getattr(h, file_log._HANDLER_TAG, False) for h in root.handlers)

    file_log.init_file_logging(enabled=False, user_files_dir=None)
    assert not any(getattr(h, file_log._HANDLER_TAG, False) for h in root.handlers)
