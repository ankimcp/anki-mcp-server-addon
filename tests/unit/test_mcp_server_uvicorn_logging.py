"""Regression tests: starting the HTTP transport must not reconfigure the
process-wide logging system.

``uvicorn.Config``'s default ``log_config`` runs ``logging.config.dictConfig()``,
which calls ``logging.shutdown()`` over EVERY handler in the process -- inside
Anki that is Anki's own handlers, other add-ons' handlers and our file log.
``logging.shutdown()`` (with the default ``logging.raiseExceptions = True``)
only swallows ``OSError``/``ValueError``, so a foreign
handler whose ``close()`` raises anything else killed the MCP background
thread (seen in the wild as ``RuntimeError: cannot release un-acquired lock``).
The same dictConfig also strips any handler pre-attached to ``uvicorn.error``,
which would silently undo ``file_log``'s forwarding of that logger.

These tests run the real ``McpServer._run_http_mode`` with a REAL
``uvicorn.Config`` (that is where ``configure_logging()`` runs) and only
``uvicorn.Server`` and the socket binder mocked out, so nothing binds a port.

They mutate process-global logging state, so every fixture restores what it
touched in a finalizer that runs even when the test fails.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# conftest.py installs aqt + primitives stubs before this module is collected,
# so the addon import below is safe even without a running Anki.
from anki_mcp_server import mcp_server as mcp_server_module
from anki_mcp_server.config import Config
from anki_mcp_server.mcp_server import McpServer
from anki_mcp_server.queue_bridge import QueueBridge


_UVICORN_LOGGER_NAMES = ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi")


class _ExplodingCloseHandler(logging.Handler):
    """A foreign add-on's handler whose ``close()`` raises while armed."""

    def __init__(self) -> None:
        super().__init__()
        self.armed = True
        self.close_attempts = 0

    def emit(self, record: logging.LogRecord) -> None:
        pass

    def close(self) -> None:
        self.close_attempts += 1
        if self.armed:
            raise RuntimeError("cannot release un-acquired lock")
        super().close()


async def _trivial_asgi_app(scope, receive, send) -> None:  # pragma: no cover
    pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_uvicorn_loggers() -> Iterator[None]:
    saved = {}
    for name in _UVICORN_LOGGER_NAMES:
        lg = logging.getLogger(name)
        saved[name] = (lg.level, list(lg.handlers), lg.propagate, lg.disabled)
    try:
        yield
    finally:
        for name, (level, handlers, propagate, disabled) in saved.items():
            lg = logging.getLogger(name)
            lg.setLevel(level)
            lg.handlers = handlers
            lg.propagate = propagate
            lg.disabled = disabled


@pytest.fixture()
def foreign_handler() -> Iterator[_ExplodingCloseHandler]:
    handler = _ExplodingCloseHandler()
    foreign_logger = logging.getLogger("tests.some_other_addon")
    foreign_logger.addHandler(handler)
    try:
        yield handler
    finally:
        # Disarm first: an armed handler left in logging's handler list would
        # also blow up the interpreter's atexit logging.shutdown().
        handler.armed = False
        foreign_logger.removeHandler(handler)
        handler.close()


@pytest.fixture()
def uvicorn_error_sentinel() -> Iterator[logging.Handler]:
    sentinel = logging.NullHandler()
    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_error.addHandler(sentinel)
    try:
        yield sentinel
    finally:
        uvicorn_error.removeHandler(sentinel)
        sentinel.close()


@pytest.fixture()
def server() -> McpServer:
    return McpServer(MagicMock(spec=QueueBridge), Config(http_port=0, http_host="127.0.0.1"))


@pytest.fixture()
def mock_fastmcp() -> MagicMock:
    mcp = MagicMock()
    mcp.streamable_http_app.return_value = _trivial_asgi_app
    return mcp


@pytest.fixture()
def mock_uvicorn_server() -> Iterator[MagicMock]:
    # Patch the attribute mcp_server.py actually resolves (``uvicorn.Server``
    # via its ``import uvicorn``); uvicorn.Config stays real. The socket
    # binder is stubbed too so nothing binds the configured port.
    with patch.object(mcp_server_module.uvicorn, "Server") as server_cls, \
            patch.object(mcp_server_module, "_bind_http_sockets", return_value=[MagicMock()]):
        server_cls.return_value.serve = AsyncMock()
        yield server_cls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_foreign_handler_raising_on_close_does_not_kill_http_startup(
    server: McpServer,
    mock_fastmcp: MagicMock,
    mock_uvicorn_server: MagicMock,
    foreign_handler: _ExplodingCloseHandler,
) -> None:
    asyncio.run(server._run_http_mode(mock_fastmcp))  # must not raise

    assert foreign_handler.close_attempts == 0
    mock_uvicorn_server.return_value.serve.assert_awaited_once()


def test_uvicorn_error_handler_survives_http_restart(
    server: McpServer,
    mock_fastmcp: MagicMock,
    mock_uvicorn_server: MagicMock,
    uvicorn_error_sentinel: logging.Handler,
) -> None:
    asyncio.run(server._run_http_mode(mock_fastmcp))
    asyncio.run(server._run_http_mode(mock_fastmcp))

    assert uvicorn_error_sentinel in logging.getLogger("uvicorn.error").handlers
    assert mock_uvicorn_server.return_value.serve.await_count == 2


def test_http_startup_sets_uvicorn_logger_levels_to_warning(
    server: McpServer,
    mock_fastmcp: MagicMock,
    mock_uvicorn_server: MagicMock,
) -> None:
    # Without log_level, uvicorn.access would inherit the root logger's level
    # and print one line per request to Anki's console. Preset NOTSET so a
    # level left behind by an earlier test cannot satisfy the asserts.
    for name in ("uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).setLevel(logging.NOTSET)

    asyncio.run(server._run_http_mode(mock_fastmcp))

    assert logging.getLogger("uvicorn.access").level == logging.WARNING
    assert logging.getLogger("uvicorn.error").level == logging.WARNING
