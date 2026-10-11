"""Config warnings at profile open must never hold the MCP server back.

``_show_startup_warnings`` opens a modal dialog. ``_on_profile_opened`` has to
start the server first and only queue the dialog for a later event-loop turn,
so an outdated config entry (e.g. a deprecated ``card_management:bury`` alias)
can't keep the server from listening until someone clicks OK -- on a headless
Anki nobody ever would.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import anki_mcp_server as addon
from anki_mcp_server.config import Config


@pytest.fixture()
def profile_open(monkeypatch):
    calls: list[str] = []
    timers: list[tuple[int, object, bool]] = []
    # _on_profile_opened assigns these globals; restore them afterwards.
    monkeypatch.setattr(addon, "_config_manager", addon._config_manager)
    monkeypatch.setattr(addon, "_connection_manager", addon._connection_manager)

    config_manager = MagicMock()
    config_manager.load.return_value = Config()
    monkeypatch.setattr(addon, "ConfigManager", lambda _pkg: config_manager)

    connection_manager = MagicMock()
    connection_manager.start.side_effect = lambda: calls.append("start")
    monkeypatch.setattr(addon, "ConnectionManager", lambda _cfg: connection_manager)

    monkeypatch.setattr(
        addon, "validate_disabled_tools",
        lambda _entries: ["disabled_tools: 'card_management:bury' is deprecated"],
    )
    monkeypatch.setattr(
        addon, "_show_startup_warnings",
        lambda warnings: calls.append(f"dialog:{len(warnings)}"),
    )

    mw = MagicMock()
    mw.progress.single_shot.side_effect = (
        lambda ms, func, requires_collection=True: timers.append(
            (ms, func, requires_collection)
        )
    )
    monkeypatch.setattr(addon, "mw", mw)

    return calls, timers, monkeypatch


def test_server_starts_before_warning_dialog(profile_open):
    calls, timers, _ = profile_open

    addon._on_profile_opened()

    assert calls == ["start"], "start() must run and the dialog must not be shown inline"
    assert len(timers) == 1
    ms, func, requires_collection = timers[0]
    assert ms == 0
    assert requires_collection is False

    func()
    assert calls == ["start", "dialog:1"]


def test_no_dialog_queued_without_warnings(profile_open):
    calls, timers, monkeypatch = profile_open
    monkeypatch.setattr(addon, "validate_disabled_tools", lambda _entries: [])

    addon._on_profile_opened()

    assert calls == ["start"]
    assert timers == []
