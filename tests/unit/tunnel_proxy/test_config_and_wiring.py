"""Config field, ConnectionManager wiring and required arguments (tests 6, 16, 17)."""
from __future__ import annotations

import inspect
import json
import logging
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_mcp_server import connection_manager as cm_module
from anki_mcp_server.config import Config
from anki_mcp_server.tunnel.auth import DeviceCodeResponse, DeviceFlowAuth
from anki_mcp_server.tunnel.client import TunnelClient
from anki_mcp_server.tunnel.protocol import PROXY_UNUSABLE_STOP_CODE
from anki_mcp_server.tunnel.reconnect import TunnelReconnectManager

from .helpers import load_proxy

from ..test_login_worker import login_dialog  # noqa: F401  (fixture)
from ..fakes import FakeTunnelLog

_ADDON_DIR = Path(__file__).resolve().parents[3] / "anki_mcp_server"


class TestConfigField:

    def test_default_is_auto(self) -> None:
        assert Config().tunnel_proxy == "auto"

    def test_shipped_config_json_declares_the_default(self) -> None:
        shipped = json.loads((_ADDON_DIR / "config.json").read_text())

        assert shipped["tunnel_proxy"] == "auto"

    def test_value_survives_a_dict_round_trip(self) -> None:
        config = Config.from_dict({"tunnel_proxy": "none"})

        assert config.tunnel_proxy == "none"
        assert config.to_dict()["tunnel_proxy"] == "none"

    def test_documented_in_the_readme(self) -> None:
        readme = (_ADDON_DIR.parent / "README.md").read_text()

        assert "tunnel_proxy" in readme


class TestRequiredKeywordArguments:
    """A forgotten pass must fail loudly instead of silently defaulting."""

    @pytest.mark.parametrize(
        ("target", "name"),
        [
            (TunnelClient.__init__, "route"),
            (TunnelReconnectManager.__init__, "proxy"),
            (DeviceFlowAuth.__init__, "proxy"),
        ],
        ids=["TunnelClient.route", "TunnelReconnectManager.proxy", "DeviceFlowAuth.proxy"],
    )
    def test_is_keyword_only_without_default(self, target, name: str) -> None:
        parameter = inspect.signature(target).parameters.get(name)

        assert parameter is not None, f"{target.__qualname__} has no {name!r} argument"
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty


@pytest.fixture()
def build_manager(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cm_module, "TunnelLog", FakeTunnelLog)
    monkeypatch.setattr(cm_module, "CredentialsManager", MagicMock)
    auth_cls = MagicMock()
    monkeypatch.setattr(cm_module, "DeviceFlowAuth", auth_cls)

    def build(**config_kwargs: object) -> cm_module.ConnectionManager:
        return cm_module.ConnectionManager(Config(**config_kwargs))

    build.auth_cls = auth_cls  # type: ignore[attr-defined]
    return build


class TestConnectionManager:

    @pytest.mark.parametrize(
        "bad",
        ["socks5://127.0.0.1:1080", "ftp://x", "", 7, "http://x/path"],
    )
    def test_invalid_proxy_is_reported_and_connect_is_refused(
        self, build_manager, bad: object
    ) -> None:
        px = load_proxy()
        manager = build_manager(tunnel_proxy=bad)
        manager._server = MagicMock()

        manager.connect_tunnel()

        reason = manager.tunnel_config_error
        assert reason == px.parse_tunnel_proxy(bad)
        assert "tunnel_proxy" in reason
        assert manager.auth is None
        build_manager.auth_cls.assert_not_called()
        assert manager.tunnel_log.entries == [("error", f"Cannot connect: {reason}")]
        manager._server.start_tunnel.assert_not_called()

    def test_socks_url_message_offers_the_alternatives(self, build_manager) -> None:
        manager = build_manager(tunnel_proxy="socks5://127.0.0.1:1080")

        reason = manager.tunnel_config_error

        assert reason is not None
        assert "socks" in reason.lower()
        assert "none" in reason.lower()
        assert "http" in reason.lower()

    @pytest.mark.parametrize("raw", ["auto", " NONE ", "http://u:p@127.0.0.1:3128"])
    def test_valid_proxy_has_no_reason_and_reaches_the_auth_client(
        self, build_manager, raw: str
    ) -> None:
        px = load_proxy()
        manager = build_manager(tunnel_proxy=raw)

        assert manager.tunnel_config_error is None
        build_manager.auth_cls.assert_called_once()
        assert build_manager.auth_cls.call_args.kwargs["proxy"] == (
            px.parse_tunnel_proxy(raw)
        )

    def test_invalid_url_and_invalid_proxy_both_block_connect(
        self, build_manager
    ) -> None:
        manager = build_manager(
            tunnel_server_url="https://tunnel.ankimcp.ai",
            tunnel_proxy="socks5://127.0.0.1:1080",
        )

        assert manager.tunnel_config_error is not None
        assert manager.auth is None

    def test_local_proxy_stop_is_one_proxy_error_line_and_no_log_record(
        self, build_manager, caplog: pytest.LogCaptureFixture
    ) -> None:
        manager = build_manager()
        caplog.set_level(logging.DEBUG)

        manager._on_tunnel_stopped(PROXY_UNUSABLE_STOP_CODE, "tunnel_proxy: no SOCKS")

        assert manager.tunnel_log.entries == [
            ("error", "Proxy error: tunnel_proxy: no SOCKS")
        ]
        assert caplog.records == []

    def test_server_sent_proxy_unsupported_is_a_server_error_and_is_logged(
        self, build_manager, caplog: pytest.LogCaptureFixture
    ) -> None:
        manager = build_manager()
        caplog.set_level(logging.DEBUG)

        manager._on_tunnel_error("proxy_unsupported", "sent by the relay")

        assert manager.tunnel_log.entries == [
            ("error", "Server error [proxy_unsupported]: sent by the relay")
        ]
        (record,) = caplog.records
        assert record.levelno == logging.WARNING
        assert "proxy_unsupported" in record.getMessage()

    def test_other_error_labels_are_unchanged(self, build_manager) -> None:
        manager = build_manager()

        manager._on_tunnel_error("connection_failed", "boom")

        assert manager.tunnel_log.entries[-1] == (
            "error", "Server error [connection_failed]: boom"
        )


class TestLoginDialogUsesTheManagersSetting:
    """The login path gets the ConnectionManager's parsed setting, never a re-read.

    ``TunnelSettingsSection`` is a Qt widget that cannot be built here without
    replacing most of ``aqt.qt``, so its hand-off is checked at source level:
    it must pass ``tunnel_proxy_setting`` and must not parse the config itself.
    ``LoginDialog``'s and the worker's signatures are checked for real, using
    the plain-Python Qt stand-ins from ``test_login_worker``.
    """

    @pytest.mark.parametrize("cls_name", ["LoginDialog", "_LoginWorker"])
    def test_proxy_is_a_required_keyword_only_argument(
        self, login_dialog, cls_name: str  # noqa: F811
    ) -> None:
        cls = getattr(login_dialog, cls_name)

        parameter = inspect.signature(cls.__init__).parameters.get("proxy")

        assert parameter is not None, f"{cls_name} has no 'proxy' argument"
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty

    def test_settings_section_passes_the_managers_setting_and_never_parses(
        self,
    ) -> None:
        source = (_ADDON_DIR / "tunnel" / "ui" / "settings_section.py").read_text()

        assert "tunnel_proxy_setting" in source
        assert "parse_tunnel_proxy" not in source
        assert "_config.tunnel_proxy" not in source
        call = source.split("LoginDialog(", 1)[1][:400]
        assert "proxy=" in call and "tunnel_proxy_setting" in call

    def test_worker_builds_its_auth_client_with_the_given_setting(
        self, login_dialog, monkeypatch: pytest.MonkeyPatch  # noqa: F811
    ) -> None:
        px = load_proxy()
        setting = px.parse_tunnel_proxy("none")
        seen: list[object] = []

        class RecordingAuth:
            def __init__(self, server_url: str, client_id: str, *, proxy) -> None:
                seen.append(proxy)

            async def request_device_code(self) -> DeviceCodeResponse:
                raise RuntimeError("stop after construction")

        monkeypatch.setattr(login_dialog, "DeviceFlowAuth", RecordingAuth)
        worker = login_dialog._LoginWorker(
            "wss://example.invalid", "client-id", proxy=setting
        )

        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        thread.join(5.0)

        assert not thread.is_alive()
        assert seen == [setting]
        assert seen[0] is setting
