"""The "Copy diagnostics" HTTP / tunnel lines must match the real server state.

``DiagnosticsSection`` subclasses ``aqt.qt.QWidget``, which the conftest stub
resolves to a MagicMock instance; subclassing that yields another mock, not
the real class, so ``_transport_lines`` would not be the real function. The
fixture below swaps in ``object`` for the import only and restores the stub
attribute, the cached module and the package attribute afterwards, so no
other test module sees it.
``_transport_lines`` is then called unbound on a namespace carrying the two
attributes it reads.
"""
from __future__ import annotations

import importlib
import sys
from types import ModuleType, SimpleNamespace
from typing import Iterator

import pytest

from anki_mcp_server.config import Config

from .test_mcp_server_http_failure import (  # noqa: F401  (fixtures)
    _attach_server,
    closed_loop,
    dead_thread,
    live_thread,
    manager,
    open_loop,
)

_MODULE = "anki_mcp_server.diagnostics_section"
_PORT_CLASH = "cannot listen on 127.0.0.1:3141: Address already in use"
_FATAL = "RuntimeError: boom"

_MISSING = object()


@pytest.fixture()
def diagnostics_section() -> Iterator[ModuleType]:
    qt = sys.modules["aqt.qt"]
    package = sys.modules["anki_mcp_server"]
    saved_qwidget = vars(qt).get("QWidget", _MISSING)
    saved_module = sys.modules.pop(_MODULE, _MISSING)
    saved_package_attr = vars(package).get("diagnostics_section", _MISSING)

    qt.QWidget = object
    try:
        yield importlib.import_module(_MODULE)
    finally:
        if saved_qwidget is _MISSING:
            vars(qt).pop("QWidget", None)
        else:
            qt.QWidget = saved_qwidget

        if saved_module is _MISSING:
            sys.modules.pop(_MODULE, None)
        else:
            sys.modules[_MODULE] = saved_module

        if saved_package_attr is _MISSING:
            vars(package).pop("diagnostics_section", None)
        else:
            package.diagnostics_section = saved_package_attr


def _lines(module: ModuleType, manager, *, http_enabled: bool) -> list[str]:
    section = SimpleNamespace(_cm=manager, _config=Config(http_enabled=http_enabled))
    return module.DiagnosticsSection._transport_lines(section)


class TestTransportLines:

    def test_healthy(self, diagnostics_section, manager, open_loop, live_thread) -> None:
        _attach_server(manager, loop=open_loop, thread=live_thread, started=True)
        assert _lines(diagnostics_section, manager, http_enabled=True) == [
            "HTTP   : enabled (running)",
            "Tunnel : not connected",
        ]

    def test_still_starting(
        self, diagnostics_section, manager, open_loop, live_thread,
    ) -> None:
        _attach_server(manager, loop=open_loop, thread=live_thread, started=False)
        assert _lines(diagnostics_section, manager, http_enabled=True) == [
            "HTTP   : enabled (not running)",
            "Tunnel : not connected",
        ]

    def test_port_clash_loop_alive(
        self, diagnostics_section, manager, open_loop, live_thread,
    ) -> None:
        _attach_server(
            manager, loop=open_loop, thread=live_thread, started=None,
            http_error=_PORT_CLASH,
        )
        assert _lines(diagnostics_section, manager, http_enabled=True) == [
            f"HTTP   : enabled (not running: {_PORT_CLASH})",
            "Tunnel : not connected",
        ]

    def test_port_clash_loop_alive_http_disabled_in_dialog_config(
        self, diagnostics_section, manager, open_loop, live_thread,
    ) -> None:
        # The dialog's Config can differ from the one the server started with;
        # the loop is alive, so the tunnel is still usable.
        _attach_server(
            manager, loop=open_loop, thread=live_thread, started=None,
            http_error=_PORT_CLASH,
        )
        assert _lines(diagnostics_section, manager, http_enabled=False) == [
            "HTTP   : disabled",
            "Tunnel : not connected",
        ]

    def test_loop_dead_http_enabled(
        self, diagnostics_section, manager, closed_loop, dead_thread,
    ) -> None:
        _attach_server(
            manager, loop=closed_loop, thread=dead_thread, started=True,
            fatal_error=_FATAL,
        )
        assert _lines(diagnostics_section, manager, http_enabled=True) == [
            f"HTTP   : enabled (not running: {_FATAL})",
            f"Tunnel : unavailable ({_FATAL})",
        ]

    def test_loop_dead_http_disabled(
        self, diagnostics_section, manager, closed_loop, dead_thread,
    ) -> None:
        _attach_server(
            manager, loop=closed_loop, thread=dead_thread, started=None,
            fatal_error=_FATAL,
        )
        assert _lines(diagnostics_section, manager, http_enabled=False) == [
            "HTTP   : disabled",
            f"Tunnel : unavailable ({_FATAL})",
        ]
