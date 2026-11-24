"""ConnectionManager's side of the proxy setting (items: wiring, panel, startup).

``ConnectionManager`` parses ``tunnel_proxy`` once, hands that setting to every
consumer (``McpServer.start_tunnel``, the auth client, the login dialog),
writes the chosen route and the terminal proxy stop to the tunnel log panel,
and the add-on's profile-open path reports an invalid value as a warning.
"""
from __future__ import annotations

import logging
from typing import Iterator
from unittest.mock import MagicMock

import pytest

import anki_mcp_server as addon
from anki_mcp_server import connection_manager as cm_module
from anki_mcp_server.config import Config

from ..fakes import FakeTunnelLog
from .helpers import (
    ADDON_LOGGER,
    RELAY_WS_URL,
    SocksSink,
    load_proxy,
    run_tunnel,
    set_system_proxies,
)

_ROUTE_LINE = "via HTTP proxy 127.0.0.1:7890 (system HTTPS proxy)"


@pytest.fixture()
def build_manager(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cm_module, "TunnelLog", FakeTunnelLog)
    monkeypatch.setattr(cm_module, "CredentialsManager", MagicMock)
    monkeypatch.setattr(cm_module, "DeviceFlowAuth", MagicMock())

    def build(**config_kwargs: object) -> cm_module.ConnectionManager:
        return cm_module.ConnectionManager(Config(**config_kwargs))

    return build


def _ready_for_connect(manager: cm_module.ConnectionManager) -> MagicMock:
    """The seam existing tests use: a mock server that is up and not connected."""
    manager._processor = MagicMock()
    manager._server = MagicMock()
    manager._server.loop_alive = True
    manager._server.tunnel_running = False
    return manager._server


class TestParsedSettingIsHeldOnce:

    @pytest.mark.parametrize("raw", ["auto", "none", "http://u:p@127.0.0.1:3128"])
    def test_valid_value_is_exposed_as_the_parsed_setting(
        self, build_manager, raw: str
    ) -> None:
        px = load_proxy()

        manager = build_manager(tunnel_proxy=raw)

        assert manager.tunnel_proxy_setting == px.parse_tunnel_proxy(raw)

    @pytest.mark.parametrize("raw", ["socks5://127.0.0.1:1080", "", 7])
    def test_invalid_value_has_no_setting(self, build_manager, raw: object) -> None:
        load_proxy()

        assert build_manager(tunnel_proxy=raw).tunnel_proxy_setting is None

    def test_update_config_replaces_it(self, build_manager) -> None:
        px = load_proxy()
        manager = build_manager(tunnel_proxy="auto")

        manager.update_config(Config(tunnel_proxy="none"))
        assert manager.tunnel_proxy_setting == px.parse_tunnel_proxy("none")

        manager.update_config(Config(tunnel_proxy="ftp://x"))
        assert manager.tunnel_proxy_setting is None
        assert manager.tunnel_config_error is not None


class TestConnectTunnelWiring:

    def test_start_tunnel_receives_the_setting_and_a_route_callback(
        self, build_manager
    ) -> None:
        px = load_proxy()
        manager = build_manager(tunnel_proxy="http://127.0.0.1:3128")
        server = _ready_for_connect(manager)

        manager.connect_tunnel()

        server.start_tunnel.assert_called_once()
        kwargs = server.start_tunnel.call_args.kwargs
        assert kwargs["proxy"] == px.parse_tunnel_proxy("http://127.0.0.1:3128")
        assert callable(kwargs["on_route"])

    def test_route_callback_writes_the_panel_line_and_leaves_logging_to_reconnect(
        self, build_manager, caplog: pytest.LogCaptureFixture
    ) -> None:
        load_proxy()
        caplog.set_level(logging.INFO, logger=ADDON_LOGGER)
        manager = build_manager()
        server = _ready_for_connect(manager)
        manager.connect_tunnel()
        on_route = server.start_tunnel.call_args.kwargs["on_route"]

        on_route(_ROUTE_LINE)

        assert ("info", f"Route: {_ROUTE_LINE}") in manager.tunnel_log.entries
        assert not any(_ROUTE_LINE in r.getMessage() for r in caplog.records)


class TestTerminalProxyStopReadsCleanlyInThePanel:

    @pytest.mark.asyncio
    async def test_socks_only_system(
        self,
        monkeypatch: pytest.MonkeyPatch,
        socks_sink: SocksSink,
        panel_manager,
    ) -> None:
        px = load_proxy()
        set_system_proxies(
            monkeypatch, {"socks": f"http://127.0.0.1:{socks_sink.port}"}
        )

        observed = await run_tunnel(
            px.AUTO_PROXY, RELAY_WS_URL, connection_manager=panel_manager
        )

        message = observed.stopped[0][1]
        lines = [text for _, text in panel_manager.tunnel_log.entries]
        assert any("tunnel_proxy" in line for line in lines)
        assert not any("Server error" in line for line in lines)
        assert not any("Gave up reconnecting" in line for line in lines)
        assert sum(line.count(message) for line in lines) == 1, lines


class TestProfileOpenedWithInvalidTunnelProxy:

    @pytest.fixture()
    def profile_open(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
        seen: dict = {"started": 0, "dialog": [], "timers": []}
        monkeypatch.setattr(addon, "_config_manager", addon._config_manager)
        monkeypatch.setattr(addon, "_connection_manager", addon._connection_manager)

        config_manager = MagicMock()
        config_manager.load.return_value = Config.from_dict(
            {"tunnel_proxy": "socks5://user:s3cret@127.0.0.1:1080"}
        )
        monkeypatch.setattr(addon, "ConfigManager", lambda _pkg: config_manager)

        connection_manager = MagicMock()
        connection_manager.start.side_effect = lambda: seen.__setitem__(
            "started", seen["started"] + 1
        )
        monkeypatch.setattr(addon, "ConnectionManager", lambda _cfg: connection_manager)
        monkeypatch.setattr(
            addon, "_show_startup_warnings", lambda w: seen["dialog"].append(w)
        )

        mw = MagicMock()
        mw.progress.single_shot.side_effect = (
            lambda ms, func, requires_collection=True: seen["timers"].append(func)
        )
        monkeypatch.setattr(addon, "mw", mw)
        yield seen

    def test_server_starts_and_warning_reaches_console_log_and_dialog(
        self, profile_open, capsys, caplog,
    ) -> None:
        px = load_proxy()
        reason = px.parse_tunnel_proxy("socks5://user:s3cret@127.0.0.1:1080")
        assert isinstance(reason, str)

        with caplog.at_level("WARNING"):
            addon._on_profile_opened()

        assert profile_open["started"] == 1
        assert reason in capsys.readouterr().out
        assert reason in caplog.text
        (show,) = profile_open["timers"]
        show()
        assert profile_open["dialog"] == [[reason]]
