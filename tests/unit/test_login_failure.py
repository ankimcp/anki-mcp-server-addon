"""Reporting helpers for login failures (``tunnel/login_failure.py``).

The decisions the login worker delegates here -- what the user sees and what
gets logged at which level -- are covered in isolation; the worker itself is in
``test_login_worker.py``.
"""
from __future__ import annotations

# The package __init__ must run before the MCP SDK is imported.
import anki_mcp_server  # noqa: F401

import logging

import pytest

from anki_mcp_server.tunnel.auth import AuthError
from anki_mcp_server.tunnel.login_failure import (
    log_login_failure,
    unexpected_error_message,
)

_LOGGER = "anki_mcp_server.tunnel.login_failure"


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == _LOGGER]


def test_unexpected_error_message_includes_type() -> None:
    message = unexpected_error_message(PermissionError(13, "Permission denied"))
    assert message == "Unexpected error: PermissionError: [Errno 13] Permission denied"


def test_transport_auth_error_is_warning_with_chained_cause(caplog) -> None:
    cause = OSError("boom")
    exc = AuthError("HTTP request failed: boom")
    exc.__cause__ = cause

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        log_login_failure("request a device code", exc)

    (record,) = _records(caplog)
    assert record.levelno == logging.WARNING
    assert record.exc_info is not None and record.exc_info[1].__cause__ is cause
    assert "request a device code" in record.getMessage()


def test_formatted_record_traceback_names_the_cause_type(caplog) -> None:
    cause = ConnectionResetError("reset by peer")
    exc = AuthError("HTTP request failed: reset")
    exc.__cause__ = cause

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        log_login_failure("request a device code", exc)

    (record,) = _records(caplog)
    text = logging.Formatter().format(record)
    assert "ConnectionResetError: reset by peer" in text
    assert "AuthError: HTTP request failed: reset" in text
    assert "NoneType: None" not in text


@pytest.mark.parametrize("code", ["expired_token", "access_denied"])
def test_expected_oauth_outcome_is_info_without_traceback(caplog, code) -> None:
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        log_login_failure("poll for the login token", AuthError("nope", error_code=code))

    (record,) = _records(caplog)
    assert record.levelno == logging.INFO
    assert record.exc_info is None


def test_other_oauth_error_code_is_still_a_warning(caplog) -> None:
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        log_login_failure("poll for the login token", AuthError("bad", error_code="invalid_client"))

    (record,) = _records(caplog)
    assert record.levelno == logging.WARNING
    assert record.exc_info is not None


@pytest.mark.parametrize(
    "code",
    [{"nested": "x"}, ["expired_token"]],
    ids=["dict", "list"],
)
def test_non_string_error_code_is_a_warning_not_a_crash(caplog, code) -> None:
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        log_login_failure("poll for the login token", AuthError("odd", error_code=code))

    (record,) = _records(caplog)
    assert record.levelno == logging.WARNING
    assert record.exc_info is not None


def test_unexpected_exception_is_error_with_type_and_traceback(caplog) -> None:
    try:
        raise PermissionError(13, "Permission denied")
    except PermissionError as exc:
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            log_login_failure("request a device code", exc)

    (record,) = _records(caplog)
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert "PermissionError" in record.getMessage()
