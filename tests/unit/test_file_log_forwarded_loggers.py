"""Unit tests for ``file_log``'s forwarding of third-party logger records --
``file_log._FORWARDED_LOGGER_NAMES``, namely ``mcp.server.transport_security``
(Host/Origin rejection WARNINGs, observability gap noted in #78) and
``uvicorn.error`` (bind failures, ASGI exception tracebacks). Neither
propagates into the addon's own logger, so without forwarding their records
would never reach ``ankimcp.log``.

All tests reset file logging to a known "disabled" state before and after
they run, so they never leak a handler into other test modules that import
``anki_mcp_server`` in the same process.
"""
from __future__ import annotations

import logging

import pytest

import anki_mcp_server  # noqa: F401 -- triggers vendor path setup

from anki_mcp_server import file_log


# Hard-coded on purpose, not file_log._FORWARDED_LOGGER_NAMES: removing a name
# from the production tuple must fail these tests, not silently skip them.
_EXPECTED_FORWARDED_LOGGER_NAMES = ("mcp.server.transport_security", "uvicorn.error")


def _reset():
    file_log.init_file_logging(enabled=False, user_files_dir=None)
    file_log.get_logger().setLevel(logging.NOTSET)


@pytest.fixture(autouse=True)
def _clean_file_logging():
    # Third-party loggers are process-global: restore their level/propagate
    # even if a regression in init_file_logging changed them.
    saved = {
        name: (logging.getLogger(name).level, logging.getLogger(name).propagate)
        for name in _EXPECTED_FORWARDED_LOGGER_NAMES
    }
    _reset()
    try:
        yield
    finally:
        _reset()
        for name, (level, propagate) in saved.items():
            fw_logger = logging.getLogger(name)
            fw_logger.setLevel(level)
            fw_logger.propagate = propagate

forwarded_logger_name = pytest.mark.parametrize(
    "logger_name", _EXPECTED_FORWARDED_LOGGER_NAMES
)


def test_forwarded_logger_names_match_expected():
    # An added name must be added here too, so every guarantee below is
    # exercised for it.
    assert file_log._FORWARDED_LOGGER_NAMES == _EXPECTED_FORWARDED_LOGGER_NAMES


@forwarded_logger_name
def test_forwarded_logger_record_reaches_the_file(tmp_path, logger_name):
    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)

    fw_logger = logging.getLogger(logger_name)
    fw_logger.warning(f"forwarded record from {logger_name}")

    log_path = tmp_path / file_log._LOG_FILENAME
    content = log_path.read_text(encoding="utf-8")
    assert f"forwarded record from {logger_name}" in content
    assert f"[{logger_name}]" in content


@forwarded_logger_name
@pytest.mark.parametrize(
    "preset_level",
    [logging.NOTSET, 13, logging.CRITICAL],
    ids=["level-NOTSET", "level-below-floor", "level-CRITICAL"],
)
@pytest.mark.parametrize(
    "preset_propagate", [True, False], ids=["propagate", "no-propagate"]
)
def test_init_leaves_forwarded_logger_level_and_propagate_alone(
    tmp_path, logger_name, preset_level, preset_propagate
):
    # Preset explicitly -- NOTSET, below any plausible floor, above it; both
    # propagate values -- so neither an unconditional nor a conditional floor
    # (the addon logger's own NOTSET-or-too-high idiom), nor forcing propagate
    # either way, can coincide with whatever state an earlier test left behind.
    fw_logger = logging.getLogger(logger_name)
    fw_logger.setLevel(preset_level)
    fw_logger.propagate = preset_propagate

    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)

    assert fw_logger.level == preset_level
    assert fw_logger.propagate is preset_propagate


@forwarded_logger_name
def test_forwarded_logger_redacts_registered_secret(tmp_path, logger_name):
    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)
    file_log.register_secret("s3cr3t-api-key-value")

    logging.getLogger(logger_name).warning(
        "rejected request with key s3cr3t-api-key-value"
    )

    log_path = tmp_path / file_log._LOG_FILENAME
    content = log_path.read_text(encoding="utf-8")
    assert "s3cr3t-api-key-value" not in content
    assert "***REDACTED***" in content


@forwarded_logger_name
def test_forwarded_logger_redacts_bearer_token(tmp_path, logger_name):
    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)

    logging.getLogger(logger_name).warning(
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789"
    )

    log_path = tmp_path / file_log._LOG_FILENAME
    content = log_path.read_text(encoding="utf-8")
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in content
    assert "***REDACTED***" in content


@forwarded_logger_name
def test_teardown_detaches_forwarded_logger(tmp_path, logger_name):
    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)
    fw_logger = logging.getLogger(logger_name)
    handler = file_log._existing_handler(file_log.get_logger())
    assert handler is not None
    assert handler in fw_logger.handlers

    file_log.init_file_logging(enabled=False, user_files_dir=None)

    assert handler not in fw_logger.handlers


@forwarded_logger_name
def test_init_twice_does_not_double_attach_forwarded_logger(tmp_path, logger_name):
    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)
    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)

    fw_logger = logging.getLogger(logger_name)
    tagged = [h for h in fw_logger.handlers if getattr(h, file_log._HANDLER_TAG, False)]
    assert len(tagged) == 1


def test_root_logger_never_gets_our_handler(tmp_path):
    file_log.init_file_logging(enabled=True, user_files_dir=tmp_path)

    root = logging.getLogger()
    assert not any(getattr(h, file_log._HANDLER_TAG, False) for h in root.handlers)

    file_log.init_file_logging(enabled=False, user_files_dir=None)
    assert not any(getattr(h, file_log._HANDLER_TAG, False) for h in root.handlers)
