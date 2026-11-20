"""``_LoginWorker`` in ``tunnel/ui/login_dialog.py``: failure reporting.

``login_dialog`` subclasses ``aqt.qt.QThread`` / ``QDialog`` and declares
``pyqtSignal`` attributes. The conftest resolves those names to MagicMock
instances, so subclassing them yields a mock rather than the real class and
``_LoginWorker.run`` would not be the real function. The fixture below swaps
plain Python stand-ins in for the import only and restores the stub
attributes, the cached module and the package attribute afterwards, so no other
test module sees them. ``DeviceFlowAuth`` is replaced where ``login_dialog``
looks it up, so nothing touches the network. ``run()`` is driven on a real
thread with a bounded join so no test can hang.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import threading
from types import ModuleType
from typing import Any, Callable, Iterator
from unittest.mock import MagicMock

import pytest

# The package __init__ must run before the MCP SDK is imported.
import anki_mcp_server  # noqa: F401

from anki_mcp_server.tunnel.auth import AuthError, DeviceCodeResponse

_MODULE = "anki_mcp_server.tunnel.ui.login_dialog"
_UI_PACKAGE = "anki_mcp_server.tunnel.ui"
_QT_NAMES = ("QThread", "QDialog", "pyqtSignal")
_JOIN_TIMEOUT = 5.0

_MISSING = object()

_DEVICE = DeviceCodeResponse(
    device_code="dev-code",
    user_code="USER-CODE",
    verification_uri="https://example.invalid/device",
    verification_uri_complete=None,
    expires_in=60,
    interval=1,
)


class _FakeBoundSignal:
    def __init__(self) -> None:
        self.emitted: list[tuple[Any, ...]] = []

    def emit(self, *args: Any) -> None:
        self.emitted.append(args)

    def connect(self, slot: Callable[..., Any]) -> None:
        pass


class _FakeSignal:
    def __init__(self, *types: type) -> None:
        self._name = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = f"_signal_{name}"

    def __get__(self, obj: Any, owner: type | None = None) -> Any:
        if obj is None:
            return self
        return obj.__dict__.setdefault(self._name, _FakeBoundSignal())


class _FakeQThread:
    def __init__(self, parent: Any = None) -> None:
        self._parent = parent

    def start(self) -> None:
        self.run()

    def run(self) -> None:
        raise NotImplementedError

    def wait(self, msecs: int = 0) -> bool:
        return True


@pytest.fixture()
def login_dialog() -> Iterator[ModuleType]:
    # Modules login_dialog imports that themselves use aqt.qt names must be
    # cached against the stubs, not the stand-ins below.
    importlib.import_module("anki_mcp_server.credentials")
    importlib.import_module("anki_mcp_server.tunnel.auth")
    importlib.import_module("anki_mcp_server.tunnel.log")
    importlib.import_module("anki_mcp_server.tunnel.login_failure")
    package = importlib.import_module(_UI_PACKAGE)

    qt = sys.modules["aqt.qt"]
    saved_qt = {name: vars(qt).get(name, _MISSING) for name in _QT_NAMES}
    saved_module = sys.modules.pop(_MODULE, _MISSING)
    saved_package_attr = vars(package).get("login_dialog", _MISSING)

    qt.QThread = _FakeQThread
    qt.QDialog = object
    qt.pyqtSignal = _FakeSignal
    try:
        yield importlib.import_module(_MODULE)
    finally:
        for name, saved in saved_qt.items():
            if saved is _MISSING:
                vars(qt).pop(name, None)
            else:
                setattr(qt, name, saved)

        if saved_module is _MISSING:
            sys.modules.pop(_MODULE, None)
        else:
            sys.modules[_MODULE] = saved_module

        if saved_package_attr is _MISSING:
            vars(package).pop("login_dialog", None)
        else:
            package.login_dialog = saved_package_attr


@pytest.fixture()
def loops(monkeypatch) -> list[asyncio.AbstractEventLoop]:
    created: list[asyncio.AbstractEventLoop] = []
    real = asyncio.new_event_loop

    def spy() -> asyncio.AbstractEventLoop:
        loop = real()
        created.append(loop)
        return loop

    monkeypatch.setattr(asyncio, "new_event_loop", spy)
    return created


@pytest.fixture()
def install_auth(login_dialog, monkeypatch) -> Callable[..., None]:
    """Replace ``DeviceFlowAuth`` as ``login_dialog`` looks it up."""

    def install(*, init_exc=None, request=None, poll=None) -> None:
        async def default_request():
            return _DEVICE

        async def default_poll():
            raise AssertionError("poll_for_token was not expected")

        request_fn = request or default_request
        poll_fn = poll or default_poll

        class FakeAuth:
            def __init__(self, server_url: str, client_id: str) -> None:
                if init_exc is not None:
                    raise init_exc

            async def request_device_code(self) -> DeviceCodeResponse:
                return await request_fn()

            async def poll_for_token(self, device_code, interval, expires_in):
                return await poll_fn()

        monkeypatch.setattr(login_dialog, "DeviceFlowAuth", FakeAuth)

    return install


def _worker(login_dialog):
    return login_dialog._LoginWorker("wss://example.invalid", "client-id")


def _run(worker) -> None:
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    thread.join(_JOIN_TIMEOUT)
    assert not thread.is_alive(), "worker thread did not exit"


def _failed(worker) -> list[str]:
    return [args[0] for args in worker.login_failed.emitted]


def _serious(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


def _raiser(exc: BaseException):
    async def raise_it():
        raise exc

    return raise_it


class TestEscapedException:

    def test_error_in_request_step_is_reported_once(
        self, login_dialog, install_auth, loops, caplog,
    ) -> None:
        install_auth(request=_raiser(RuntimeError("boom")))
        worker = _worker(login_dialog)

        with caplog.at_level(logging.DEBUG):
            _run(worker)

        assert _failed(worker) == ["Unexpected error: RuntimeError: boom"]
        (record,) = _serious(caplog)
        assert record.levelno == logging.ERROR
        assert record.exc_info is not None and isinstance(record.exc_info[1], RuntimeError)
        assert worker._loop is None
        assert loops[0].is_closed()

    def test_error_before_the_flow_handlers_is_reported_once(
        self, login_dialog, install_auth, loops, caplog,
    ) -> None:
        install_auth(init_exc=ValueError("bad server url"))
        worker = _worker(login_dialog)

        with caplog.at_level(logging.DEBUG):
            _run(worker)

        assert _failed(worker) == ["Unexpected error: ValueError: bad server url"]
        (record,) = _serious(caplog)
        assert record.levelno == logging.ERROR
        assert record.exc_info is not None and isinstance(record.exc_info[1], ValueError)
        assert worker._loop is None
        assert loops[0].is_closed()

    def test_error_escaping_flow_itself_is_reported_once(
        self, login_dialog, loops, caplog, monkeypatch,
    ) -> None:
        worker = _worker(login_dialog)

        async def broken_flow() -> None:
            raise OSError("flow broke")

        monkeypatch.setattr(worker, "_flow", broken_flow)

        with caplog.at_level(logging.DEBUG):
            _run(worker)

        assert _failed(worker) == ["Unexpected error: OSError: flow broke"]
        (record,) = _serious(caplog)
        assert record.levelno == logging.ERROR
        assert record.exc_info is not None and isinstance(record.exc_info[1], OSError)
        assert worker._loop is None
        assert loops[0].is_closed()

    def test_second_report_is_suppressed(self, login_dialog, loops, monkeypatch) -> None:
        worker = _worker(login_dialog)

        async def report_then_raise() -> None:
            worker._report_failure("first")
            raise RuntimeError("second")

        monkeypatch.setattr(worker, "_flow", report_then_raise)
        _run(worker)

        assert _failed(worker) == ["first"]

    def test_loop_creation_failure_is_reported_without_masking(
        self, login_dialog, monkeypatch, caplog,
    ) -> None:
        def no_loop() -> asyncio.AbstractEventLoop:
            raise RuntimeError("cannot create loop")

        monkeypatch.setattr(asyncio, "new_event_loop", no_loop)
        worker = _worker(login_dialog)

        with caplog.at_level(logging.DEBUG):
            _run(worker)

        assert _failed(worker) == ["Unexpected error: RuntimeError: cannot create loop"]
        (record,) = _serious(caplog)
        assert record.levelno == logging.ERROR
        assert worker._loop is None


class TestAuthError:

    def test_device_code_failure_logs_warning_with_cause_chain(
        self, login_dialog, install_auth, loops, caplog,
    ) -> None:
        cause = OSError("connection refused")
        exc = AuthError("HTTP request failed: connection refused")
        exc.__cause__ = cause
        install_auth(request=_raiser(exc))
        worker = _worker(login_dialog)

        with caplog.at_level(logging.DEBUG):
            _run(worker)

        assert _failed(worker) == ["Failed to start login: HTTP request failed: connection refused"]
        (record,) = _serious(caplog)
        assert record.levelno == logging.WARNING
        assert record.exc_info is not None
        assert record.exc_info[1] is exc
        assert record.exc_info[1].__cause__ is cause
        assert worker.device_code_received.emitted == []
        assert loops[0].is_closed()


def _cancelling_raiser(worker, exc: BaseException):
    """A poll step that flips ``_cancelled`` mid-flight and then fails.

    Sets the flag directly rather than via ``cancel()`` so the loop is not
    stopped and only the guards in ``_flow``'s handlers are exercised.
    """

    async def raise_it():
        worker._cancelled = True
        raise exc

    return raise_it


class TestPollFailures:

    def test_transport_failure_is_shown_and_logged_with_cause_chain(
        self, login_dialog, install_auth, loops, caplog,
    ) -> None:
        cause = OSError("connection reset")
        exc = AuthError("HTTP request failed: connection reset")
        exc.__cause__ = cause
        install_auth(poll=_raiser(exc))
        worker = _worker(login_dialog)

        with caplog.at_level(logging.DEBUG):
            _run(worker)

        assert _failed(worker) == [str(exc)]
        (record,) = _serious(caplog)
        assert record.levelno == logging.WARNING
        assert record.exc_info is not None
        assert record.exc_info[1] is exc
        assert record.exc_info[1].__cause__ is cause
        assert loops[0].is_closed()

    def test_unexpected_error_is_shown_with_type_and_logged_as_error(
        self, login_dialog, install_auth, loops, caplog,
    ) -> None:
        install_auth(poll=_raiser(ValueError("bad payload")))
        worker = _worker(login_dialog)

        with caplog.at_level(logging.DEBUG):
            _run(worker)

        assert _failed(worker) == ["Unexpected error: ValueError: bad payload"]
        (record,) = _serious(caplog)
        assert record.levelno == logging.ERROR
        assert record.exc_info is not None and isinstance(record.exc_info[1], ValueError)
        assert loops[0].is_closed()

    @pytest.mark.parametrize(
        "code",
        [{"nested": "x"}, ["expired_token"]],
        ids=["dict", "list"],
    )
    def test_non_string_error_code_shows_the_servers_message(
        self, login_dialog, install_auth, loops, caplog, code,
    ) -> None:
        exc = AuthError("the server's own message", error_code=code)
        install_auth(poll=_raiser(exc))
        worker = _worker(login_dialog)

        with caplog.at_level(logging.DEBUG):
            _run(worker)

        assert _failed(worker) == [str(exc)]
        assert not any("TypeError" in message for message in _failed(worker))
        (record,) = _serious(caplog)
        assert record.levelno == logging.WARNING
        assert loops[0].is_closed()

    @pytest.mark.parametrize(
        "exc",
        [AuthError("HTTP request failed: x"), RuntimeError("boom")],
        ids=["auth-error", "runtime-error"],
    )
    def test_failure_after_cancel_is_silent(
        self, login_dialog, install_auth, loops, caplog, exc,
    ) -> None:
        worker = _worker(login_dialog)
        install_auth(poll=_cancelling_raiser(worker, exc))

        with caplog.at_level(logging.DEBUG):
            _run(worker)

        assert worker._cancelled is True
        assert len(worker.device_code_received.emitted) == 1
        assert worker.login_failed.emitted == []
        assert worker.login_succeeded.emitted == []
        assert _serious(caplog) == []
        assert loops[0].is_closed()


class _RecordingTunnelLog:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)


def _bare_dialog(login_dialog, tunnel_log):
    """A ``LoginDialog`` without ``__init__``.

    Under the stubs ``QDialog`` is ``object``, so ``LoginDialog.__init__``
    (``super().__init__(parent)``) cannot run. Only what ``_on_failure`` reads
    is set by hand.
    """
    dialog = login_dialog.LoginDialog.__new__(login_dialog.LoginDialog)
    dialog._tunnel_log = tunnel_log
    dialog._status_label = MagicMock()
    dialog._retry_button = MagicMock()
    dialog._browser_button = MagicMock()
    return dialog


class TestDialogFailureSlot:

    def test_failure_is_written_once_to_the_tunnel_log(self, login_dialog) -> None:
        tunnel_log = _RecordingTunnelLog()
        dialog = _bare_dialog(login_dialog, tunnel_log)

        dialog._on_failure("HTTP request failed: boom")

        assert tunnel_log.errors == ["Login failed: HTTP request failed: boom"]
        dialog._status_label.setText.assert_called_with("Login failed: HTTP request failed: boom")
        dialog._retry_button.setVisible.assert_called_once_with(True)
        dialog._browser_button.setEnabled.assert_called_once_with(False)

    def test_failure_without_a_tunnel_log_still_updates_the_dialog(self, login_dialog) -> None:
        dialog = _bare_dialog(login_dialog, None)

        dialog._on_failure("nope")

        dialog._status_label.setText.assert_called_with("Login failed: nope")
        dialog._retry_button.setVisible.assert_called_once_with(True)


class TestCancel:

    def test_cancel_mid_poll_is_silent(
        self, login_dialog, install_auth, loops, caplog,
    ) -> None:
        polling = threading.Event()

        async def never_completes():
            polling.set()
            await asyncio.Event().wait()

        install_auth(poll=never_completes)
        worker = _worker(login_dialog)

        with caplog.at_level(logging.DEBUG):
            thread = threading.Thread(target=worker.run, daemon=True)
            thread.start()
            try:
                assert polling.wait(_JOIN_TIMEOUT), "flow never reached the poll step"
                worker.cancel()
            finally:
                thread.join(_JOIN_TIMEOUT)

        assert not thread.is_alive(), "worker thread did not exit after cancel()"
        assert worker.login_failed.emitted == []
        assert worker.login_succeeded.emitted == []
        assert len(worker.device_code_received.emitted) == 1
        assert _serious(caplog) == []
        assert worker._loop is None
        assert loops[0].is_closed()

    @pytest.mark.parametrize(
        "exc",
        [AuthError("HTTP request failed: x"), RuntimeError("boom")],
        ids=["auth-error", "other-error"],
    )
    def test_cancelled_before_request_fails_is_silent(
        self, login_dialog, install_auth, loops, caplog, exc,
    ) -> None:
        install_auth(request=_raiser(exc))
        worker = _worker(login_dialog)
        worker.cancel()

        with caplog.at_level(logging.DEBUG):
            _run(worker)

        assert worker.login_failed.emitted == []
        assert worker.device_code_received.emitted == []
        assert _serious(caplog) == []
        assert loops[0].is_closed()


class TestExpectedEndings:

    @pytest.mark.parametrize("code", ["expired_token", "access_denied"])
    def test_poll_ending_is_shown_and_logged_at_info(
        self, login_dialog, install_auth, loops, caplog, code,
    ) -> None:
        install_auth(poll=_raiser(AuthError(f"ended: {code}", error_code=code)))
        worker = _worker(login_dialog)

        with caplog.at_level(logging.DEBUG):
            _run(worker)

        assert _failed(worker) == [f"ended: {code}"]
        assert _serious(caplog) == []
        (record,) = [
            r for r in caplog.records
            if r.name == "anki_mcp_server.tunnel.login_failure"
        ]
        assert record.levelno == logging.INFO
        assert record.exc_info is None
        assert loops[0].is_closed()
