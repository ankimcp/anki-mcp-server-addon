"""Tests for the sync GUI-feedback tooltip (config gating + defensive no-op).

Covers the two pure-ish pieces that don't need a real Qt:

* ``config.get_show_sync_tooltip`` -- reads the flag defensively, falling back
  to the dataclass default whenever the config read raises.
* ``_sync_runner._notify`` -- gates on the flag and swallows ANY tooltip error
  so a GUI failure can never propagate into the sync flow.

``aqt`` and ``aqt.utils`` are stubbed by conftest (``_StubModule``); we swap in
recording callables where we need to observe or force behavior.
"""
from __future__ import annotations

import sys
import types


# ---------------------------------------------------------------------------
# config.get_show_sync_tooltip
# ---------------------------------------------------------------------------
class TestGetShowSyncTooltip:
    def test_defaults_to_true_when_no_mw(self, monkeypatch):
        import anki_mcp_server.config as config

        monkeypatch.setattr(sys.modules["aqt"], "mw", None, raising=False)
        assert config.get_show_sync_tooltip() is True

    def test_defaults_to_true_when_getconfig_raises(self, monkeypatch):
        import anki_mcp_server.config as config

        def boom(_name):
            raise RuntimeError("config unavailable")

        fake_mw = types.SimpleNamespace(
            addonManager=types.SimpleNamespace(getConfig=boom)
        )
        monkeypatch.setattr(sys.modules["aqt"], "mw", fake_mw, raising=False)
        assert config.get_show_sync_tooltip() is True

    def test_reads_false_from_config(self, monkeypatch):
        import anki_mcp_server.config as config

        fake_mw = types.SimpleNamespace(
            addonManager=types.SimpleNamespace(
                getConfig=lambda _name: {"show_sync_tooltip": False}
            )
        )
        monkeypatch.setattr(sys.modules["aqt"], "mw", fake_mw, raising=False)
        assert config.get_show_sync_tooltip() is False


# ---------------------------------------------------------------------------
# _sync_runner._notify
# ---------------------------------------------------------------------------
class TestNotify:
    def test_shows_tooltip_when_enabled(self, sync_runner, monkeypatch):
        import anki_mcp_server.config as config

        monkeypatch.setattr(config, "get_show_sync_tooltip", lambda: True)
        seen: list[str] = []
        monkeypatch.setattr(
            sys.modules["aqt.utils"],
            "tooltip",
            lambda msg, *a, **k: seen.append(msg),
        )

        sync_runner._notify("hello")
        assert seen == ["hello"]

    def test_skips_tooltip_when_disabled(self, sync_runner, monkeypatch):
        import anki_mcp_server.config as config

        monkeypatch.setattr(config, "get_show_sync_tooltip", lambda: False)
        seen: list[str] = []
        monkeypatch.setattr(
            sys.modules["aqt.utils"],
            "tooltip",
            lambda msg, *a, **k: seen.append(msg),
        )

        sync_runner._notify("hello")
        assert seen == []

    def test_swallows_tooltip_errors(self, sync_runner, monkeypatch):
        import anki_mcp_server.config as config

        monkeypatch.setattr(config, "get_show_sync_tooltip", lambda: True)

        def boom(*_a, **_k):
            raise RuntimeError("no gui / offscreen")

        monkeypatch.setattr(sys.modules["aqt.utils"], "tooltip", boom)

        # Must not raise -- a tooltip failure can never break a sync.
        sync_runner._notify("hello")

    def test_swallows_config_errors(self, sync_runner, monkeypatch):
        import anki_mcp_server.config as config

        def boom():
            raise RuntimeError("config blew up")

        monkeypatch.setattr(config, "get_show_sync_tooltip", boom)
        sync_runner._notify("hello")  # must not raise


class TestNotifySyncError:
    def test_auth_failed_wording(self, sync_runner, monkeypatch):
        seen: list[str] = []
        monkeypatch.setattr(sync_runner, "_notify", lambda msg: seen.append(msg))

        sync_runner._notify_sync_error("auth_failed")
        assert seen == ["AnkiMCP: AnkiWeb login required"]

    def test_generic_error_wording(self, sync_runner, monkeypatch):
        seen: list[str] = []
        monkeypatch.setattr(sync_runner, "_notify", lambda msg: seen.append(msg))

        sync_runner._notify_sync_error("unknown")
        assert seen == ["AnkiMCP: sync failed"]
