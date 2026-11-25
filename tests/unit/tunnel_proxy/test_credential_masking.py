"""A proxy URL's credentials never reach any output (test 13 of the plan).

Covers the masking helpers, the file-log redacting filter's structural
pattern, and two real end-to-end leaks: a bad system/env proxy entry and an
explicit proxy that answers 407. For each, the secret must be absent from the
captured logs, every callback argument, the tunnel log panel entries and the
raised exception text.
"""
from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_mcp_server import file_log
from anki_mcp_server.tunnel.protocol import PROXY_UNUSABLE_STOP_CODE

from .helpers import (
    ADDON_LOGGER as _ADDON_LOGGER,
    RELAY_WS_URL,
    SCENARIO_TIMEOUT,
    SECRET,
    AuthServer,
    ConnectProxy,
    Observed,
    load_proxy,
    run_tunnel,
    set_system_proxies,
)

from ..fakes import FakeTunnelLog

_USER_AND_SECRET = f"user:{SECRET}"


def _assert_clean(label: str, text: str) -> None:
    assert SECRET not in text, f"{label} leaked the proxy password: {text!r}"
    assert _USER_AND_SECRET not in text, f"{label} leaked the credentials: {text!r}"


def _assert_nothing_leaked(
    observed: Observed,
    caplog: pytest.LogCaptureFixture,
    panel: FakeTunnelLog,
) -> None:
    # An empty panel would make the checks below vacuous (e.g. a callback that
    # raised and was swallowed), so require that something was written.
    assert panel.entries, "the tunnel log panel received nothing"
    assert caplog.records, "nothing was logged"
    _assert_clean("caplog", caplog.text)
    for record in caplog.records:
        _assert_clean("log record", record.getMessage())
        if record.exc_text:
            _assert_clean("log record traceback", record.exc_text)
    for text in observed.all_text():
        _assert_clean("callback argument", text)
    for kind, text in panel.entries:
        _assert_clean(f"tunnel log entry ({kind})", text)


class TestMaskingHelpers:

    @pytest.mark.parametrize(
        ("url", "masked"),
        [
            ("http://user:pw@h:1", "http://***@h:1"),
            ("https://user@h:1", "https://***@h:1"),
            ("http://h:1", "http://h:1"),
            ("http://user:p@ss@h:1", "http://***@h:1"),
        ],
    )
    def test_mask_proxy_url(self, url: str, masked: str) -> None:
        px = load_proxy()

        assert px.mask_proxy_url(url) == masked

    def test_mask_url_credentials_masks_every_occurrence_in_free_text(self) -> None:
        px = load_proxy()
        text = (
            f"failed via http://user:{SECRET}@127.0.0.1:7890 then "
            "https://other@proxy.example:3128/x, finally http://plain:80"
        )

        masked = px.mask_url_credentials(text)

        _assert_clean("masked text", masked)
        assert "other@" not in masked
        assert "127.0.0.1:7890" in masked
        assert "proxy.example:3128/x" in masked
        assert "http://plain:80" in masked

    def test_mask_url_credentials_leaves_ordinary_text_alone(self) -> None:
        px = load_proxy()
        text = "mail me at someone@example.com or see https://example.com/@me"

        assert px.mask_url_credentials(text) == text


class TestRedactingFilterMasksUrlCredentials:
    """``file_log`` stays stdlib-only, so it carries its own structural pattern."""

    @staticmethod
    def _filtered(message: str) -> str:
        record = logging.LogRecord(
            "anki_mcp_server.test", logging.INFO, __file__, 1, message, None, None
        )
        assert file_log._RedactingFilter().filter(record) is True
        return record.getMessage()

    def test_password_and_short_password_are_masked(self) -> None:
        # register_secret ignores values under 4 chars, so these only work
        # through the structural pattern.
        assert "pw" not in self._filtered("via http://user:pw@127.0.0.1:1 failed")
        assert "u:p" not in self._filtered("via http://u:p@127.0.0.1:1 failed")

    def test_user_only_userinfo_is_masked(self) -> None:
        assert "alice" not in self._filtered("via http://alice@127.0.0.1:1 failed")

    def test_keeps_the_rest_of_the_line(self) -> None:
        filtered = self._filtered(f"via http://user:{SECRET}@127.0.0.1:7890 failed")

        _assert_clean("filtered record", filtered)
        assert filtered.startswith("via ")
        assert "127.0.0.1:7890 failed" in filtered

    def test_urls_without_userinfo_are_untouched(self) -> None:
        for message in (
            "plain http://127.0.0.1:7890 ok",
            "path https://example.com/users/@me ok",
        ):
            assert self._filtered(message) == message

    def test_bearer_masking_still_works(self) -> None:
        token = "abcdefghijklmnopqrstuvwxyz0123456789"

        assert token not in self._filtered(f"Authorization: Bearer {token}")

    def test_file_log_stays_stdlib_only_and_independent_of_the_tunnel(self) -> None:
        tree = ast.parse(Path(file_log.__file__).read_text())
        imported = [
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ] + [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]

        assert not [name for name in imported if "tunnel" in name]


class TestBadSystemProxyEntry:
    """An env/system ``https`` entry with credentials but an unusable scheme."""

    def test_route_error_text_has_no_credentials(self) -> None:
        px = load_proxy()

        with pytest.raises(px.TunnelProxyError) as excinfo:
            px.decide_route(
                px.AUTO_PROXY,
                host="relay.test",
                port=443,
                secure=True,
                proxies={"https": f"ftp://user:{SECRET}@127.0.0.1:1"},
                bypass=lambda host: False,
            )

        _assert_clean("TunnelProxyError", str(excinfo.value))
        assert "tunnel_proxy" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_tunnel_run_leaks_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        panel_manager,
    ) -> None:
        px = load_proxy()
        caplog.set_level(logging.DEBUG, logger=_ADDON_LOGGER)
        set_system_proxies(
            monkeypatch, {"https": f"ftp://user:{SECRET}@127.0.0.1:1"}
        )

        observed = await run_tunnel(
            px.AUTO_PROXY, RELAY_WS_URL, connection_manager=panel_manager
        )

        assert len(observed.stopped) == 1
        assert observed.reconnecting == []
        assert observed.errors == []
        assert observed.stopped[0][0] == PROXY_UNUSABLE_STOP_CODE
        _assert_nothing_leaked(observed, caplog, panel_manager.tunnel_log)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "entry",
        [
            f"http://user:{SECRET}@127.0.0.1:1/path",
            f"http://user:{SECRET}@127.0.0.1:99999",
            f"http://user:{SECRET}@127.0.0.1:1?x=1",
            "http://user@127.0.0.1:1",
        ],
    )
    async def test_malformed_entry_stops_terminally_and_leaks_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        panel_manager,
        entry: str,
    ) -> None:
        px = load_proxy()
        caplog.set_level(logging.DEBUG, logger=_ADDON_LOGGER)
        set_system_proxies(monkeypatch, {"https": entry})

        observed = await run_tunnel(
            px.AUTO_PROXY, RELAY_WS_URL, connection_manager=panel_manager
        )

        assert len(observed.stopped) == 1
        assert observed.reconnecting == []
        assert observed.errors == []
        assert observed.stopped[0][0] == PROXY_UNUSABLE_STOP_CODE
        assert "tunnel_proxy" in observed.stopped[0][1]
        _assert_nothing_leaked(observed, caplog, panel_manager.tunnel_log)
        assert all(
            "user@" not in text for _, text in panel_manager.tunnel_log.entries
        )


class TestRawLibraryErrorsAreMasked:
    """Library exception text that carries the RAW proxy URL, password included.

    websockets' ``InvalidProxy`` quotes the URL it rejected. ``TunnelClient``
    is handed the route directly, so this holds whatever the route decision
    does upstream.
    """

    @pytest.mark.asyncio
    async def test_invalid_proxy_url_text_never_reaches_the_client_exception(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from anki_mcp_server.tunnel.client import TunnelClient, TunnelConnectionError

        px = load_proxy()
        caplog.set_level(logging.DEBUG, logger=_ADDON_LOGGER)
        route = px.ProxyRoute(f"http://user:{SECRET}@127.0.0.1:1/path", "explicit")
        creds = MagicMock()
        creds.access_token = "access-token"
        client = TunnelClient(RELAY_WS_URL, creds, MagicMock(), route=route)

        with pytest.raises(TunnelConnectionError) as excinfo:
            await asyncio.wait_for(client.run(), SCENARIO_TIMEOUT)

        # Proves the scenario bites: the library's own text names the URL.
        assert "127.0.0.1:1/path" in str(excinfo.value)
        _assert_clean("TunnelConnectionError", str(excinfo.value))
        _assert_clean("caplog", caplog.text)

    @pytest.mark.asyncio
    async def test_login_leaks_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from anki_mcp_server.tunnel.auth import AuthError, DeviceFlowAuth

        px = load_proxy()
        caplog.set_level(logging.DEBUG, logger=_ADDON_LOGGER)
        set_system_proxies(
            monkeypatch, {"https": f"ftp://user:{SECRET}@127.0.0.1:1"}
        )
        auth = DeviceFlowAuth(RELAY_WS_URL, "client", proxy=px.AUTO_PROXY)

        with pytest.raises(AuthError) as excinfo:
            await asyncio.wait_for(auth.request_device_code(), SCENARIO_TIMEOUT)

        _assert_clean("AuthError", str(excinfo.value))
        _assert_clean("caplog", caplog.text)


class TestExplicitProxyAnswering407:
    """What a 407 proves: the proxy was contacted and nothing secret escaped.

    websockets' 407 text names no URL, so these cannot catch a raw-URL leak;
    ``TestRawLibraryErrorsAreMasked`` covers that.
    """

    @pytest.mark.asyncio
    async def test_tunnel_run_leaks_nothing(
        self,
        caplog: pytest.LogCaptureFixture,
        connect_proxy: ConnectProxy,
        panel_manager,
    ) -> None:
        px = load_proxy()
        caplog.set_level(logging.DEBUG, logger=_ADDON_LOGGER)
        connect_proxy.reply_407 = True
        setting = px.parse_tunnel_proxy(
            f"http://user:{SECRET}@127.0.0.1:{connect_proxy.port}"
        )

        observed = await run_tunnel(
            setting, RELAY_WS_URL, connection_manager=panel_manager
        )

        assert observed.established == []
        assert observed.errors, "the 407 should have been reported"
        assert connect_proxy.requests, "the proxy was never contacted"
        _assert_nothing_leaked(observed, caplog, panel_manager.tunnel_log)

    @pytest.mark.asyncio
    async def test_client_exception_text_has_no_credentials(
        self,
        caplog: pytest.LogCaptureFixture,
        connect_proxy: ConnectProxy,
    ) -> None:
        from anki_mcp_server.tunnel.client import TunnelClient, TunnelConnectionError

        px = load_proxy()
        caplog.set_level(logging.DEBUG, logger=_ADDON_LOGGER)
        connect_proxy.reply_407 = True
        route = px.ProxyRoute(
            f"http://user:{SECRET}@127.0.0.1:{connect_proxy.port}", "explicit"
        )
        creds = MagicMock()
        creds.access_token = "access-token"
        client = TunnelClient(
            RELAY_WS_URL, creds, MagicMock(), route=route
        )

        with pytest.raises(TunnelConnectionError) as excinfo:
            await asyncio.wait_for(client.run(), SCENARIO_TIMEOUT)

        assert connect_proxy.requests
        _assert_clean("TunnelConnectionError", str(excinfo.value))
        _assert_clean("caplog", caplog.text)
        # The chained cause is dropped for a proxied route, so the type name
        # has to carry what the cause would have told.
        assert "InvalidProxyStatus" in str(excinfo.value)
        assert excinfo.value.__cause__ is None

    @pytest.mark.asyncio
    async def test_login_exception_text_has_no_credentials(
        self,
        caplog: pytest.LogCaptureFixture,
        auth_server: AuthServer,
        connect_proxy: ConnectProxy,
    ) -> None:
        from anki_mcp_server.tunnel.auth import AuthError, DeviceFlowAuth

        px = load_proxy()
        caplog.set_level(logging.DEBUG, logger=_ADDON_LOGGER)
        connect_proxy.reply_407 = True
        setting = px.parse_tunnel_proxy(
            f"http://user:{SECRET}@127.0.0.1:{connect_proxy.port}"
        )
        auth = DeviceFlowAuth(RELAY_WS_URL, "client", proxy=setting)

        with pytest.raises(AuthError) as excinfo:
            await asyncio.wait_for(auth.request_device_code(), SCENARIO_TIMEOUT)

        assert connect_proxy.requests
        assert auth_server.accepts == 0
        _assert_clean("AuthError", str(excinfo.value))
        _assert_clean("caplog", caplog.text)


class TestInvalidSettingMessagesDoNotEchoCredentials:

    @pytest.mark.parametrize(
        "raw",
        [
            f"socks5://user:{SECRET}@127.0.0.1:1080",
            f"ftp://user:{SECRET}@127.0.0.1:21",
            f"http://user:{SECRET}@127.0.0.1:1/path",
            f"http://user:{SECRET}@127.0.0.1:1?x=1",
        ],
    )
    def test_parse_message(self, raw: str) -> None:
        px = load_proxy()

        message = px.parse_tunnel_proxy(raw)

        assert isinstance(message, str)
        _assert_clean("parse_tunnel_proxy message", message)
