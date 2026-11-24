"""An invalid ``tunnel_server_url`` must never break startup or HTTP.

Covers the single validator (``parse_tunnel_server_url``), the advisory startup
check, ``ConnectionManager`` (constructs, reports the reason, refuses to
connect) and ``DeviceFlowAuth`` agreeing with the validator. The
``ConnectionManager`` is built the way ``test_mcp_server_http_failure.py``
builds it: Qt/credentials/auth collaborators stubbed, no server started.
"""
from __future__ import annotations

from typing import Iterator
from unittest.mock import MagicMock

import pytest

import anki_mcp_server as addon
from anki_mcp_server import connection_manager as cm_module
from anki_mcp_server.config import Config
from anki_mcp_server.tunnel.auth import DeviceFlowAuth
from anki_mcp_server.tunnel.proxy import AUTO_PROXY
from anki_mcp_server.tunnel.protocol import (
    TunnelServerUrl,
    parse_tunnel_server_url,
    validate_tunnel_server_url,
)

from .fakes import FakeTunnelLog

_EXPECTED = "expected a ws:// or wss:// URL (e.g. wss://tunnel.ankimcp.ai)"


class TestParseTunnelServerUrl:

    @pytest.mark.parametrize(
        ("raw", "http_url"),
        [
            ("wss://tunnel.ankimcp.ai", "https://tunnel.ankimcp.ai"),
            ("ws://localhost:3004", "http://localhost:3004"),
            ("wss://relay.example:8443", "https://relay.example:8443"),
            ("wss://relay.example/prefix", "https://relay.example/prefix"),
            ("wss://relay.example/", "https://relay.example"),
            ("WSS://Relay.Example", "https://Relay.Example"),
            ("ws://[::1]:3004", "http://[::1]:3004"),
        ],
    )
    def test_valid(self, raw: str, http_url: str) -> None:
        assert parse_tunnel_server_url(raw) == TunnelServerUrl(
            ws_url=raw, http_url=http_url
        )

    @pytest.mark.parametrize(
        ("raw", "reason"),
        [
            (
                "https://tunnel.ankimcp.ai",
                "Invalid tunnel_server_url 'https://tunnel.ankimcp.ai': expected a "
                "ws:// or wss:// URL, not https:// (use wss://tunnel.ankimcp.ai)",
            ),
            (
                "http://localhost:3004",
                "Invalid tunnel_server_url 'http://localhost:3004': expected a "
                "ws:// or wss:// URL, not http:// (use ws://localhost:3004)",
            ),
            (
                "tunnel.ankimcp.ai",
                f"Invalid tunnel_server_url 'tunnel.ankimcp.ai': {_EXPECTED}",
            ),
            ("", f"Invalid tunnel_server_url '': {_EXPECTED}"),
            ("   ", f"Invalid tunnel_server_url '   ': {_EXPECTED}"),
            (
                " wss://h",
                "Invalid tunnel_server_url ' wss://h': must not contain "
                f"whitespace, {_EXPECTED}",
            ),
            (
                "wss://h:abc",
                f"Invalid tunnel_server_url 'wss://h:abc': invalid port, {_EXPECTED}",
            ),
            (
                "wss://",
                f"Invalid tunnel_server_url 'wss://': missing host, {_EXPECTED}",
            ),
            (
                "wss://h?x=1",
                "Invalid tunnel_server_url 'wss://h?x=1': must not contain a "
                f"query or fragment, {_EXPECTED}",
            ),
            (
                "wss://h#frag",
                "Invalid tunnel_server_url 'wss://h#frag': must not contain a "
                f"query or fragment, {_EXPECTED}",
            ),
            (
                "wss://[::1",
                f"Invalid tunnel_server_url 'wss://[::1': not a valid URL, {_EXPECTED}",
            ),
            (None, f"Invalid tunnel_server_url None: {_EXPECTED}"),
            (443, f"Invalid tunnel_server_url 443: {_EXPECTED}"),
            (["wss://h"], f"Invalid tunnel_server_url ['wss://h']: {_EXPECTED}"),
        ],
    )
    def test_invalid_reason(self, raw: object, reason: str) -> None:
        assert parse_tunnel_server_url(raw) == reason

    def test_credentials_are_rejected_without_echoing_them(self) -> None:
        reason = parse_tunnel_server_url("wss://user:hunter2@relay.example")

        assert isinstance(reason, str)
        assert "credentials" in reason
        assert "hunter2" not in reason

    @pytest.mark.parametrize(
        "raw",
        [
            "wss://user:secret@host x",
            "wss://u:secret@[::1",
            ["wss://u:p@h"],
            "wss://u:secret@h/a b",
            "https://user:secret@h",
            "https://h/p@secret",
        ],
    )
    def test_userinfo_is_never_echoed(self, raw: object) -> None:
        reason = parse_tunnel_server_url(raw)

        assert isinstance(reason, str)
        assert reason.startswith("Invalid tunnel_server_url:")
        for leaked in ("secret", "u:p", "user:"):
            assert leaked not in reason

    @pytest.mark.parametrize("raw", ["https://u:PW＠h", "wss://u:PW﹫h"])
    def test_lookalike_at_sign_is_never_echoed(self, raw: str) -> None:
        reason = parse_tunnel_server_url(raw)

        assert isinstance(reason, str)
        assert "PW" not in reason

    def test_huge_value_is_truncated(self) -> None:
        reason = parse_tunnel_server_url("x" * 10_000)

        assert isinstance(reason, str)
        assert len(reason) < 500
        assert "..." in reason

    def test_huge_https_value_is_truncated_without_suggesting_it(self) -> None:
        reason = parse_tunnel_server_url("https://" + "h" * 10_000)

        assert isinstance(reason, str)
        assert len(reason) < 500
        assert reason.endswith("(use wss://)")

    @pytest.mark.parametrize(
        "raw",
        ["wss://ho\x01st", "\x01wss://host", "wss://h​x", "wss://h\x00x",
         "wss://h/\ud800", "\x01https://host"],
    )
    def test_control_and_invalid_characters_are_rejected(self, raw: str) -> None:
        reason = parse_tunnel_server_url(raw)

        assert isinstance(reason, str)
        assert "contains control or invalid characters" in reason
        assert "(use" not in reason

    @pytest.mark.parametrize(
        "raw",
        ["wss://tunnel.ankimcp.ai", "ws://localhost:3004", "ws://[::1]:3004",
         "wss://bücher.example", "wss://relay.example/tunnel"],
    )
    def test_legitimate_values_still_pass(self, raw: str) -> None:
        assert isinstance(parse_tunnel_server_url(raw), TunnelServerUrl)

    def test_out_of_range_port_is_invalid(self) -> None:
        reason = parse_tunnel_server_url("wss://h:99999")

        assert isinstance(reason, str)
        assert "invalid port" in reason


class TestValidateTunnelServerUrl:

    def test_default_config_has_no_warning(self) -> None:
        assert validate_tunnel_server_url(Config()) == []

    def test_valid_custom_url_has_no_warning(self) -> None:
        assert validate_tunnel_server_url(Config(tunnel_server_url="ws://localhost:3004")) == []

    def test_invalid_url_warns_once(self) -> None:
        config = Config(tunnel_server_url="https://tunnel.ankimcp.ai")

        assert validate_tunnel_server_url(config) == [
            parse_tunnel_server_url("https://tunnel.ankimcp.ai")
        ]

    def test_non_string_value_from_json_config_warns(self) -> None:
        assert len(validate_tunnel_server_url(Config(tunnel_server_url=None))) == 1


class TestDeviceFlowAuthAgreesWithValidator:

    def test_invalid_url_raises_value_error_with_the_reason(self) -> None:
        reason = parse_tunnel_server_url("https://tunnel.ankimcp.ai")

        with pytest.raises(ValueError) as excinfo:
            DeviceFlowAuth("https://tunnel.ankimcp.ai", "client", proxy=AUTO_PROXY)

        assert str(excinfo.value) == reason

    def test_endpoints_derive_from_the_parsed_http_url(self) -> None:
        auth = DeviceFlowAuth(
            "wss://relay.example/prefix/", "client", proxy=AUTO_PROXY
        )

        assert auth._device_endpoint == "https://relay.example/prefix/auth/device"
        assert auth._token_endpoint == "https://relay.example/prefix/auth/token"


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


class TestConnectionManagerInvalidTunnelUrl:

    @pytest.mark.parametrize(
        "bad", ["https://tunnel.ankimcp.ai", "", "wss://h:abc", None, 7],
    )
    def test_construction_does_not_raise_and_reports_the_reason(
        self, build_manager, bad: object,
    ) -> None:
        manager = build_manager(tunnel_server_url=bad)

        assert manager.tunnel_config_error == parse_tunnel_server_url(bad)
        assert manager.auth is None
        build_manager.auth_cls.assert_not_called()

    def test_valid_url_has_no_reason_and_builds_auth(self, build_manager) -> None:
        manager = build_manager(tunnel_server_url="ws://localhost:3004")

        assert manager.tunnel_config_error is None
        build_manager.auth_cls.assert_called_once_with(
            server_url="ws://localhost:3004", client_id="ankimcp-cli",
            proxy=AUTO_PROXY,
        )
        assert manager.auth is build_manager.auth_cls.return_value

    def test_connect_is_refused_with_one_error_line(self, build_manager) -> None:
        manager = build_manager(tunnel_server_url="https://tunnel.ankimcp.ai")
        manager._server = MagicMock()

        manager.connect_tunnel()

        reason = manager.tunnel_config_error
        assert manager.tunnel_log.entries == [("error", f"Cannot connect: {reason}")]
        manager._server.start_tunnel.assert_not_called()

    def test_connect_is_refused_when_server_not_running(self, build_manager) -> None:
        manager = build_manager(tunnel_server_url="nonsense")

        manager.connect_tunnel()

        assert [level for level, _ in manager.tunnel_log.entries] == ["error"]
        assert "tunnel_server_url" in manager.tunnel_log.entries[0][1]

    def test_http_state_is_unaffected(self, build_manager) -> None:
        manager = build_manager(tunnel_server_url="nonsense", http_enabled=True)

        assert manager.http_running is False
        assert manager.http_error is None
        assert manager.loop_error is None
        assert manager.tunnel_connected is False
        assert manager.tunnel_active is False

    def test_update_config_recomputes_the_reason(self, build_manager) -> None:
        manager = build_manager(tunnel_server_url="nonsense")

        manager.update_config(Config(tunnel_server_url="wss://relay.example"))
        assert manager.tunnel_config_error is None
        assert manager.auth is not None

        manager.update_config(Config(tunnel_server_url="http://relay.example"))
        assert manager.tunnel_config_error is not None
        assert manager.auth is None


class TestProfileOpenedWithInvalidTunnelUrl:

    @pytest.fixture()
    def profile_open(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
        seen: dict = {"started": 0, "dialog": [], "timers": []}
        monkeypatch.setattr(addon, "_config_manager", addon._config_manager)
        monkeypatch.setattr(addon, "_connection_manager", addon._connection_manager)

        config_manager = MagicMock()
        config_manager.load.return_value = Config(
            tunnel_server_url="https://tunnel.ankimcp.ai"
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
        reason = parse_tunnel_server_url("https://tunnel.ankimcp.ai")

        with caplog.at_level("WARNING"):
            addon._on_profile_opened()

        assert profile_open["started"] == 1
        assert reason in capsys.readouterr().out
        assert reason in caplog.text
        (show,) = profile_open["timers"]
        show()
        assert profile_open["dialog"] == [[reason]]
