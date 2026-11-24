"""Edge cases and wiring found in review.

Masking performance, the McpServer start path, the settings section's guard,
masking of httpx-layer errors, and the malformed-input corners of the route
decision and the setting parser.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
from typing import Any, Iterator
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from anki_mcp_server import connection_manager as cm_module
from anki_mcp_server import file_log
from anki_mcp_server import mcp_server as mcp_server_module
from anki_mcp_server.config import Config
from anki_mcp_server.mcp_server import McpServer
from anki_mcp_server.queue_bridge import QueueBridge

from .helpers import (
    ADDON_LOGGER,
    RELAY_WS_URL,
    SCENARIO_TIMEOUT,
    SECRET,
    load_proxy,
    run_tunnel,
)

# Linear-time inputs finish in milliseconds; the quadratic pattern took
# seconds at 50k characters. The bound is far above the former and far below
# the latter, so a slow CI machine cannot trip it.
_SIZE = 200_000
_PERF_BOUND_SECONDS = 5.0


# ---------------------------------------------------------------------------
# URL-credential masking: output and run time
# ---------------------------------------------------------------------------

_PATHOLOGICAL = {
    "alphanumeric-run": "a" * _SIZE,
    "scheme-then-run": "a://" + "b" * _SIZE,
    "run-then-at": "x" * _SIZE + "@",
    "repeated-scheme": "a://" * _SIZE,
    "colon-run": "a:" * _SIZE,
    "dot-run": "a." * _SIZE,
    "at-less-urls": ("h://" + "b" * 10 + " ") * (_SIZE // 10),
    "slashes-after-scheme": "a://b" + "/" * _SIZE,
}


def _file_log_mask(text: str) -> str:
    record = logging.LogRecord("anki_mcp_server.t", logging.INFO, __file__, 1, text, None, None)
    file_log._RedactingFilter().filter(record)
    return record.getMessage()


class TestMaskingOutput:

    @pytest.mark.parametrize(
        ("text", "masked"),
        [
            ("xhttps://a:b@c", "xhttps://***@c"),
            ("(http://q:w@e)", "(http://***@e)"),
            ("http://user:pw@h:1", "http://***@h:1"),
            ("https://other@proxy.example:3128/x", "https://***@proxy.example:3128/x"),
            ("via http://u:p@h:1 then https://v@h:2 end", "via http://***@h:1 then https://***@h:2 end"),
            ("http://plain:80", "http://plain:80"),
        ],
    )
    def test_mask_url_credentials(self, text: str, masked: str) -> None:
        assert load_proxy().mask_url_credentials(text) == masked

    @pytest.mark.parametrize(
        ("text", "secret"),
        [
            ("xhttps://a:s3cr3t@c", "s3cr3t"),
            ("(http://q:s3cr3t@e)", "s3cr3t"),
            ("http://user:s3cr3t@h:1", "s3cr3t"),
        ],
    )
    def test_file_log_filter_masks_the_same_cases(self, text: str, secret: str) -> None:
        assert secret not in _file_log_mask(text)


class TestMaskingPatternsStayTogether:

    def test_file_log_and_proxy_patterns_are_identical(self) -> None:
        from anki_mcp_server.tunnel import proxy

        assert file_log._RedactingFilter._URL_USERINFO_RE.pattern == (
            proxy._USERINFO_RE.pattern
        )


class TestMaskingRunsInLinearTime:

    @pytest.mark.parametrize("name", list(_PATHOLOGICAL))
    def test_mask_url_credentials(self, name: str) -> None:
        mask = load_proxy().mask_url_credentials
        text = _PATHOLOGICAL[name]

        started = time.perf_counter()
        mask(text)

        assert time.perf_counter() - started < _PERF_BOUND_SECONDS

    @pytest.mark.parametrize("name", list(_PATHOLOGICAL))
    def test_file_log_filter(self, name: str) -> None:
        text = _PATHOLOGICAL[name]

        started = time.perf_counter()
        _file_log_mask(text.replace("%", "%%"))

        assert time.perf_counter() - started < _PERF_BOUND_SECONDS


# ---------------------------------------------------------------------------
# McpServer.start_tunnel -> _start_tunnel_async
# ---------------------------------------------------------------------------

class TestMcpServerTunnelStart:

    def test_the_setting_and_the_route_callback_reach_the_reconnect_manager(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        px = load_proxy()
        from anki_mcp_server.tunnel import reconnect as reconnect_module

        setting = px.parse_tunnel_proxy("none")
        assert setting != px.AUTO_PROXY
        on_route = MagicMock(name="on_route")
        created = threading.Event()
        seen: dict[str, Any] = {}

        def fake_manager(**kwargs: Any) -> MagicMock:
            seen.update(kwargs)
            created.set()
            manager = MagicMock()
            manager.run = AsyncMock()
            return manager

        monkeypatch.setattr(reconnect_module, "TunnelReconnectManager", fake_manager)
        server = McpServer(
            MagicMock(spec=QueueBridge), Config(http_port=0, http_host="127.0.0.1")
        )
        server._mcp_instance = MagicMock()
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        try:
            server._loop = loop
            scheduled = server.start_tunnel(
                MagicMock(), MagicMock(), on_route=on_route, proxy=setting
            )
            assert scheduled is True
            assert created.wait(SCENARIO_TIMEOUT)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2.0)
            loop.close()
            server._executor.shutdown(wait=False)

        assert seen["proxy"] is setting
        assert seen["on_route"] is on_route


# ---------------------------------------------------------------------------
# Settings section: an invalid setting never reaches the login dialog
# ---------------------------------------------------------------------------

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


class TestSettingsSectionGuard:

    @staticmethod
    def _click_connect(
        settings_section: ModuleType,
        manager: cm_module.ConnectionManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> list[dict[str, Any]]:
        """Click Connect with no stored credentials; return the dialogs opened."""
        opened: list[dict[str, Any]] = []

        class FakeLoginDialog:
            DialogCode = SimpleNamespace(Accepted="accepted")

            def __init__(self, **kwargs: Any) -> None:
                opened.append(kwargs)
                self.credentials = None

            def exec(self) -> str:
                return "rejected"

        manager._credentials_manager.load.return_value = None
        monkeypatch.setattr(settings_section, "LoginDialog", FakeLoginDialog)
        section = SimpleNamespace(
            _cm=manager,
            _config=manager._config,
            _connect_button=MagicMock(),
            window=lambda: None,
        )

        settings_section.TunnelSettingsSection._on_connect(section)
        return opened

    @pytest.mark.parametrize(
        "config_kwargs",
        [
            {"tunnel_proxy": "socks5://127.0.0.1:1"},
            {"tunnel_server_url": "https://tunnel.ankimcp.ai"},
        ],
        ids=["invalid-proxy", "invalid-server-url"],
    )
    def test_invalid_config_reports_instead_of_opening_the_login_dialog(
        self,
        settings_section: ModuleType,
        panel_manager,
        monkeypatch: pytest.MonkeyPatch,
        config_kwargs: dict[str, str],
    ) -> None:
        manager = cm_module.ConnectionManager(Config(**config_kwargs))
        assert manager.tunnel_config_error is not None

        opened = self._click_connect(settings_section, manager, monkeypatch)

        assert opened == []
        assert manager.tunnel_log.entries == [
            ("error", f"Cannot connect: {manager.tunnel_config_error}")
        ]

    def test_valid_config_without_credentials_opens_the_login_dialog(
        self,
        settings_section: ModuleType,
        panel_manager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        assert panel_manager.tunnel_config_error is None

        opened = self._click_connect(settings_section, panel_manager, monkeypatch)

        assert len(opened) == 1
        assert opened[0]["proxy"] is panel_manager.tunnel_proxy_setting


# ---------------------------------------------------------------------------
# Masking of httpx-layer errors
# ---------------------------------------------------------------------------

class TestAuthErrorTextIsMasked:

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            httpx.ProxyError(f"tunnel via http://user:{SECRET}@127.0.0.1:9 failed"),
            OSError(f"cannot reach http://user:{SECRET}@127.0.0.1:9"),
        ],
        ids=["httpx-error", "os-error"],
    )
    async def test_proxy_url_in_the_library_text_is_masked(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, error: Exception
    ) -> None:
        from anki_mcp_server.tunnel.auth import AuthError, DeviceFlowAuth

        px = load_proxy()
        caplog.set_level(logging.DEBUG, logger=ADDON_LOGGER)

        async def post(self: Any, *args: Any, **kwargs: Any) -> Any:
            raise error

        monkeypatch.setattr(httpx.AsyncClient, "post", post)
        setting = px.parse_tunnel_proxy(f"http://user:{SECRET}@127.0.0.1:9")
        auth = DeviceFlowAuth(RELAY_WS_URL, "client", proxy=setting)

        with pytest.raises(AuthError) as excinfo:
            await asyncio.wait_for(auth.request_device_code(), SCENARIO_TIMEOUT)

        assert SECRET not in str(excinfo.value)
        assert "http://***@127.0.0.1:9" in str(excinfo.value)
        assert SECRET not in caplog.text


# ---------------------------------------------------------------------------
# Setting parser and route decision corners
# ---------------------------------------------------------------------------

class TestMalformedSystemEntries:

    @staticmethod
    def _decide(px: Any, shape: dict[str, str]) -> Any:
        return px.decide_route(
            px.AUTO_PROXY, host="relay.test", port=443, secure=True,
            proxies=shape, bypass=lambda host: False,
        )

    def test_unparseable_address_is_an_invalid_address_not_a_wrong_type(self) -> None:
        px = load_proxy()

        with pytest.raises(px.TunnelProxyError) as excinfo:
            self._decide(px, {"https": "http://[::1"})

        assert "invalid proxy address" in str(excinfo.value)
        assert "not HTTP or HTTPS" not in str(excinfo.value)

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_blank_entry_is_absent(self, blank: str) -> None:
        px = load_proxy()

        assert self._decide(px, {"https": blank}).proxy_url is None

    def test_blank_socks_entry_is_not_reported_as_ignored(self) -> None:
        px = load_proxy()

        route = self._decide(
            px, {"https": "http://127.0.0.1:3128", "socks": "   "}
        )

        assert "socks" not in route.display().lower()

    def test_blank_socks_entry_alone_means_direct(self) -> None:
        px = load_proxy()

        assert self._decide(px, {"socks": "  "}).proxy_url is None

    def test_blank_entry_does_not_hide_a_later_candidate(self) -> None:
        px = load_proxy()

        route = self._decide(px, {"https": "  ", "all": "http://127.0.0.1:3128"})

        assert route.proxy_url == "http://127.0.0.1:3128"

    def test_port_zero_is_invalid(self) -> None:
        px = load_proxy()

        with pytest.raises(px.TunnelProxyError) as excinfo:
            self._decide(px, {"https": f"http://user:{SECRET}@127.0.0.1:0"})

        assert "tunnel_proxy" in str(excinfo.value)
        assert SECRET not in str(excinfo.value)


class TestParserCorners:

    @pytest.mark.parametrize(
        "raw",
        ["http://h:0", f"http://u:{SECRET}@h:0", f"http://u:{SECRET}@[v1.x]:1", "http://[::1"],
    )
    def test_rejected_without_echoing_credentials(self, raw: str) -> None:
        px = load_proxy()

        message = px.parse_tunnel_proxy(raw)

        assert isinstance(message, str)
        assert "tunnel_proxy" in message
        assert SECRET not in message

    @pytest.mark.parametrize(
        "raw",
        [
            f"http://u:{SECRET}@hé.example:1",
            f"http://u:{SECRET}@[::1%é]:1",
            "http://[::1%ａ]:1",
        ],
        ids=["non-ascii-host", "non-ascii-zone-id", "fullwidth-zone-id"],
    )
    def test_non_ascii_hosts_are_rejected(self, raw: str) -> None:
        px = load_proxy()

        message = px.parse_tunnel_proxy(raw)

        assert isinstance(message, str)
        assert "tunnel_proxy" in message
        assert SECRET not in message

    @pytest.mark.asyncio
    async def test_auth_reports_a_non_ascii_zone_id_as_a_proxy_problem(self) -> None:
        from anki_mcp_server.tunnel.auth import DeviceFlowAuth, ProxyUnusableError

        px = load_proxy()
        setting = px.ProxySetting(px.ProxyMode.EXPLICIT, "http://[::1%é]:1")
        auth = DeviceFlowAuth(RELAY_WS_URL, "client", proxy=setting)

        with pytest.raises(ProxyUnusableError) as excinfo:
            await asyncio.wait_for(auth.request_device_code(), SCENARIO_TIMEOUT)

        assert "Invalid auth URL" not in str(excinfo.value)

    @pytest.mark.parametrize(
        "raw",
        ["http://256.256.256.256:1", "http://1.2.3:1", "http://1.2.3.4.5:1", "http://1:1"],
    )
    def test_malformed_ipv4_hosts_are_rejected(self, raw: str) -> None:
        px = load_proxy()

        message = px.parse_tunnel_proxy(raw)

        assert isinstance(message, str)
        assert "IPv4" in message

    @pytest.mark.parametrize("host", ["256.256.256.256", "1.2.3", "1.2.3.4.5"])
    def test_malformed_ipv4_system_entry_is_a_terminal_error(self, host: str) -> None:
        px = load_proxy()

        with pytest.raises(px.TunnelProxyError) as excinfo:
            TestMalformedSystemEntries._decide(px, {"https": f"http://{host}:1"})

        assert "tunnel_proxy" in str(excinfo.value)

    def test_non_ascii_message_tells_the_user_to_use_the_xn_form(self) -> None:
        px = load_proxy()

        assert "xn--" in px.parse_tunnel_proxy("http://hé.example:1")

    @pytest.mark.parametrize(
        "raw", ["http://127.0.0.1:1", "http://10.0.0.1:3128", "http://proxy1.corp:80"]
    )
    def test_valid_hosts_with_digits_are_accepted(self, raw: str) -> None:
        px = load_proxy()

        assert px.parse_tunnel_proxy(raw).url == raw

    @pytest.mark.parametrize("raw", ["http://[::1]:3128", "http://h:65535"])
    def test_valid_addresses_are_still_accepted(self, raw: str) -> None:
        px = load_proxy()

        assert px.parse_tunnel_proxy(raw).url == raw


class TestAuthRejectsWhatHttpxRejects:

    @pytest.mark.asyncio
    async def test_an_address_httpx_refuses_blames_the_proxy_not_the_auth_url(
        self,
    ) -> None:
        from anki_mcp_server.tunnel.auth import DeviceFlowAuth, ProxyUnusableError

        px = load_proxy()
        # Built directly: the parser rejects this address, so only a caller
        # that skipped it can reach the httpx-side handling.
        setting = px.ProxySetting(px.ProxyMode.EXPLICIT, f"http://u:{SECRET}@[v1.x]:1")
        auth = DeviceFlowAuth(RELAY_WS_URL, "client", proxy=setting)

        with pytest.raises(ProxyUnusableError) as excinfo:
            await asyncio.wait_for(auth.request_device_code(), SCENARIO_TIMEOUT)

        assert "tunnel_proxy" in str(excinfo.value)
        assert "Invalid auth URL" not in str(excinfo.value)
        assert SECRET not in str(excinfo.value)


class TestServerSentCodeIsNotTheLocalCondition:

    @pytest.mark.asyncio
    async def test_only_the_local_error_is_a_proxy_unusable_error(self) -> None:
        from anki_mcp_server.tunnel.auth import AuthError, ProxyUnusableError

        local = ProxyUnusableError("x")

        assert isinstance(local, AuthError)
        assert local.error_code == "proxy_unsupported"
        assert not isinstance(
            AuthError("x", error_code="proxy_unsupported"), ProxyUnusableError
        )


class TestLoginFailureLogging:

    def test_unusable_proxy_is_a_warning_without_traceback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from anki_mcp_server.tunnel.auth import ProxyUnusableError
        from anki_mcp_server.tunnel.login_failure import log_login_failure

        caplog.set_level(logging.DEBUG, logger=ADDON_LOGGER)

        log_login_failure("request a device code", ProxyUnusableError("tunnel_proxy: no"))

        (record,) = caplog.records
        assert record.levelno == logging.WARNING
        assert "tunnel_proxy: no" in record.getMessage()
        assert record.exc_info is None

    def test_other_auth_errors_keep_their_traceback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from anki_mcp_server.tunnel.auth import AuthError
        from anki_mcp_server.tunnel.login_failure import log_login_failure

        caplog.set_level(logging.DEBUG, logger=ADDON_LOGGER)

        log_login_failure("request a device code", AuthError("boom"))

        (record,) = caplog.records
        assert record.exc_info is not None


class TestMalformedTarget:

    def test_resolve_route_never_raises_for_a_malformed_target(self) -> None:
        px = load_proxy()

        for target in ("wss://[bad", "wss://host:notaport", "https://[::1"):
            assert px.resolve_route(px.AUTO_PROXY, target).proxy_url is None

    @pytest.mark.asyncio
    async def test_a_malformed_relay_url_still_ends_in_the_connection_error(self) -> None:
        px = load_proxy()

        observed = await run_tunnel(px.AUTO_PROXY, "wss://[bad")

        assert observed.errors, "no error was reported"
        assert {code for code, _ in observed.errors} == {"connection_failed"}
        assert all("Failed to connect to wss://[bad" in m for _, m in observed.errors)
        assert len(observed.stopped) == 1
