"""The tunnel log panel wraps long lines instead of scrolling them off-screen.

No Qt is available in the unit suite (``aqt.qt`` is a stub of MagicMocks), so
this checks the configuration the section applies to its log widget, not the
rendering; the rendering was checked by hand in a real Anki.
"""
from __future__ import annotations

# The package __init__ must run before the MCP SDK is imported.
import anki_mcp_server  # noqa: F401

import importlib
import sys
from types import ModuleType
from typing import Iterator
from unittest.mock import MagicMock

import pytest

_SECTION_MODULE = "anki_mcp_server.tunnel.ui.settings_section"
_UI_PACKAGE = "anki_mcp_server.tunnel.ui"
_MISSING = object()


@pytest.fixture()
def settings_section() -> Iterator[ModuleType]:
    """``settings_section`` imported with a plain base class instead of the QWidget stub."""
    importlib.import_module("anki_mcp_server.tunnel.ui.login_dialog")
    package = importlib.import_module(_UI_PACKAGE)
    qt = sys.modules["aqt.qt"]
    saved_widget = vars(qt).get("QWidget", _MISSING)
    saved_module = sys.modules.pop(_SECTION_MODULE, _MISSING)
    saved_attr = vars(package).get("settings_section", _MISSING)
    qt.QWidget = object
    try:
        yield importlib.import_module(_SECTION_MODULE)
    finally:
        if saved_widget is _MISSING:
            vars(qt).pop("QWidget", None)
        else:
            qt.QWidget = saved_widget
        if saved_module is _MISSING:
            sys.modules.pop(_SECTION_MODULE, None)
        else:
            sys.modules[_SECTION_MODULE] = saved_module
        if saved_attr is _MISSING:
            vars(package).pop("settings_section", None)
        else:
            package.settings_section = saved_attr


def test_log_display_wraps_at_the_widget_width_and_anywhere_for_long_tokens(
    settings_section: ModuleType,
) -> None:
    section = MagicMock()

    settings_section.TunnelSettingsSection._setup_ui(section)

    log = section._log_display
    log.setLineWrapMode.assert_called_once_with(
        settings_section.QPlainTextEdit.LineWrapMode.WidgetWidth
    )
    log.setWordWrapMode.assert_called_once_with(
        settings_section.QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
    )
    log.setReadOnly.assert_called_once_with(True)


@pytest.mark.parametrize(("empty", "blocks_inserted"), [(True, 0), (False, 1)])
def test_a_new_line_starts_before_every_entry_except_the_first(
    settings_section: ModuleType, empty: bool, blocks_inserted: int
) -> None:
    section = MagicMock()
    section._log_display.document.return_value.isEmpty.return_value = empty
    cursor = section._log_display.textCursor.return_value

    settings_section.TunnelSettingsSection._append_log_entry(
        section, MagicMock(level="info")
    )

    assert cursor.insertBlock.call_count == blocks_inserted
    (inserted,) = cursor.insertText.call_args.args[:1]
    assert not inserted.endswith("\n")


@pytest.mark.parametrize(
    ("entries_since_error", "anchored"),
    [(None, False), (0, True), (1, True), (2, True), (3, False), (10, False)],
)
def test_only_a_recent_error_entry_is_anchored(
    settings_section: ModuleType, entries_since_error, anchored: bool
) -> None:
    assert settings_section._should_anchor_on_error(entries_since_error) is anchored


def test_error_entries_are_tracked_by_their_colour_level_not_their_text(
    settings_section: ModuleType,
) -> None:
    append = settings_section.TunnelSettingsSection._append_log_entry
    section = MagicMock()
    section._entries_since_error = None
    cursor = section._log_display.textCursor.return_value
    cursor.blockNumber.return_value = 4

    append(section, MagicMock(level="info"))
    assert section._entries_since_error is None

    append(section, MagicMock(level="error"))
    assert section._entries_since_error == 0
    assert section._error_block == 4

    append(section, MagicMock(level="info"))
    append(section, MagicMock(level="info"))
    assert section._entries_since_error == 2

    cursor.blockNumber.return_value = 9
    append(section, MagicMock(level="error"))
    assert (section._error_block, section._entries_since_error) == (9, 0)


def test_the_scroll_decision_is_deferred_and_coalesced(
    settings_section: ModuleType,
) -> None:
    append = settings_section.TunnelSettingsSection._append_log_entry
    section = MagicMock()
    section._entries_since_error = None

    section._scroll_timer.isActive.return_value = False
    append(section, MagicMock(level="info"))
    section._scroll_timer.start.assert_called_once_with(0)

    section._scroll_timer.isActive.return_value = True
    append(section, MagicMock(level="info"))
    section._scroll_timer.start.assert_called_once_with(0)
    section._scroll_to_latest_entry.assert_not_called()


def test_clear_forgets_the_error_entry(settings_section: ModuleType) -> None:
    section = MagicMock()
    section._entries_since_error = 1

    settings_section.TunnelSettingsSection._on_clear_log(section)

    assert section._entries_since_error is None


def test_copy_log_keeps_the_trailing_newline_it_always_had(
    settings_section: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    clipboard = MagicMock()
    monkeypatch.setattr(
        settings_section, "QApplication", MagicMock(clipboard=lambda: clipboard)
    )
    section = MagicMock()
    section._log_display.toPlainText.return_value = "first\nsecond"

    settings_section.TunnelSettingsSection._on_copy_log(section)

    clipboard.setText.assert_called_once_with("first\nsecond\n")
