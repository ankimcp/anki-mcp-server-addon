"""Which way the tunnel WebSocket goes (tests 1-5b, 8b, 9, 10 and 15 of the plan).

Real ``TunnelReconnectManager`` + ``TunnelClient`` + vendored ``websockets``
over loopback sockets. The local relay counts raw TCP accepts, the CONNECT
proxy records what it was asked for, and the SOCKS sink counts anything that
reaches it. The relay closes with 1000 right after ``tunnel_established``, so
a healthy run ends in exactly one clean terminal stop.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from anki_mcp_server.tunnel.protocol import PROXY_UNUSABLE_STOP_CODE

from .helpers import (
    PUBLIC_TUNNEL_URL,
    RELAY_TARGET,
    RELAY_WS_URL,
    ADDON_LOGGER as _ADDON_LOGGER,
    SECRET,
    WINDOWS_SHAPE_IDS,
    AuthServer,
    ConnectProxy,
    Observed,
    RelayServer,
    SocksSink,
    basic_auth,
    clash_shape,
    load_proxy,
    run_tunnel,
    set_system_proxies,
    windows_socks_only_shapes,
)


def _assert_established_and_stopped_cleanly(observed: Observed) -> None:
    assert [url for url, _ in observed.established] == [PUBLIC_TUNNEL_URL]
    assert len(observed.stopped) == 1
    assert observed.stopped[0][0] == 1000
    assert observed.reconnecting == []
    assert observed.errors == []


def _assert_terminal_proxy_failure(observed: Observed) -> None:
    assert len(observed.stopped) == 1
    assert observed.reconnecting == []
    assert observed.established == []
    assert observed.errors == []
    code, message = observed.stopped[0]
    assert code == PROXY_UNUSABLE_STOP_CODE
    assert "tunnel_proxy" in message
    assert "socks" in message.lower()


class TestAutoWithClashShape:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("socks_goes_to", ["proxy", "sink"])
    async def test_goes_through_the_http_proxy_and_never_touches_socks(
        self,
        monkeypatch: pytest.MonkeyPatch,
        relay: RelayServer,
        connect_proxy: ConnectProxy,
        socks_sink: SocksSink,
        socks_goes_to: str,
    ) -> None:
        px = load_proxy()
        socks_port = socks_sink.port if socks_goes_to == "sink" else None
        set_system_proxies(
            monkeypatch, clash_shape(connect_proxy.port, socks_port=socks_port)
        )

        observed = await run_tunnel(px.AUTO_PROXY, RELAY_WS_URL)

        _assert_established_and_stopped_cleanly(observed)
        assert connect_proxy.connect_targets == [RELAY_TARGET]
        assert relay.accepts == 1
        assert socks_sink.accepts == 0

    @pytest.mark.asyncio
    async def test_windows_mixed_shape_goes_through_the_http_proxy(
        self,
        monkeypatch: pytest.MonkeyPatch,
        relay: RelayServer,
        connect_proxy: ConnectProxy,
        socks_sink: SocksSink,
    ) -> None:
        px = load_proxy()
        set_system_proxies(monkeypatch, {
            "http": f"http://127.0.0.1:{connect_proxy.port}",
            "https": f"http://127.0.0.1:{connect_proxy.port}",
            "socks": f"socks://127.0.0.1:{socks_sink.port}",
        })

        observed = await run_tunnel(px.AUTO_PROXY, RELAY_WS_URL)

        _assert_established_and_stopped_cleanly(observed)
        assert connect_proxy.connect_targets == [RELAY_TARGET]
        assert socks_sink.accepts == 0


class TestSchemeLessEntry:

    @pytest.mark.asyncio
    async def test_scheme_less_https_entry_is_used_as_an_http_proxy(
        self,
        monkeypatch: pytest.MonkeyPatch,
        relay: RelayServer,
        connect_proxy: ConnectProxy,
    ) -> None:
        px = load_proxy()
        set_system_proxies(monkeypatch, {"https": f"127.0.0.1:{connect_proxy.port}"})

        observed = await run_tunnel(px.AUTO_PROXY, RELAY_WS_URL)

        _assert_established_and_stopped_cleanly(observed)
        assert connect_proxy.connect_targets == [RELAY_TARGET]
        assert relay.accepts == 1


class TestRoutesAreResolvedPerAttempt:

    @pytest.mark.asyncio
    async def test_a_shape_change_between_attempts_applies_to_the_next_one(
        self,
        monkeypatch: pytest.MonkeyPatch,
        connect_proxy: ConnectProxy,
        socks_sink: SocksSink,
    ) -> None:
        px = load_proxy()
        set_system_proxies(monkeypatch, clash_shape(connect_proxy.port))

        def vpn_switches_to_socks_only() -> None:
            set_system_proxies(
                monkeypatch, {"socks": f"http://127.0.0.1:{socks_sink.port}"}
            )

        observed = await run_tunnel(
            px.AUTO_PROXY,
            RELAY_WS_URL,
            on_reconnecting_hook=vpn_switches_to_socks_only,
        )

        assert len(observed.reconnecting) == 1
        assert connect_proxy.connect_targets == [RELAY_TARGET]
        assert observed.errors == []
        assert [code for code, _ in observed.stopped] == [PROXY_UNUSABLE_STOP_CODE]
        assert socks_sink.accepts == 0
        assert len(observed.routes) == 1

    @pytest.fixture(autouse=True)
    def _relay_that_asks_for_a_reconnect(self, relay: RelayServer) -> None:
        relay.close_codes = [4008, 1000]


class TestAutoWithSocksOnlySystem:

    @pytest.mark.asyncio
    async def test_stops_terminally_with_an_actionable_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        relay: RelayServer,
        socks_sink: SocksSink,
    ) -> None:
        px = load_proxy()
        set_system_proxies(
            monkeypatch, {"socks": f"http://127.0.0.1:{socks_sink.port}"}
        )

        observed = await run_tunnel(px.AUTO_PROXY, relay.url)

        _assert_terminal_proxy_failure(observed)
        assert relay.accepts == 0
        assert socks_sink.accepts == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("index", [0, 1, 2], ids=[*WINDOWS_SHAPE_IDS, "all-socks5"])
    async def test_windows_and_all_key_shapes_are_terminal_too(
        self,
        monkeypatch: pytest.MonkeyPatch,
        relay: RelayServer,
        socks_sink: SocksSink,
        index: int,
    ) -> None:
        px = load_proxy()
        shapes = [
            *windows_socks_only_shapes(socks_sink.port),
            {"all": f"socks5://127.0.0.1:{socks_sink.port}"},
        ]
        set_system_proxies(monkeypatch, shapes[index])

        observed = await run_tunnel(px.AUTO_PROXY, relay.url)

        _assert_terminal_proxy_failure(observed)
        assert relay.accepts == 0
        assert socks_sink.accepts == 0

    @pytest.mark.asyncio
    async def test_a_bypassed_target_connects_directly_instead(
        self,
        monkeypatch: pytest.MonkeyPatch,
        relay: RelayServer,
        socks_sink: SocksSink,
    ) -> None:
        px = load_proxy()
        set_system_proxies(
            monkeypatch,
            {"socks": f"http://127.0.0.1:{socks_sink.port}"},
            bypass=lambda host: host == "127.0.0.1",
        )

        observed = await run_tunnel(px.AUTO_PROXY, relay.url)

        _assert_established_and_stopped_cleanly(observed)
        assert relay.accepts == 1
        assert socks_sink.accepts == 0


class TestDirectConnections:

    @pytest.mark.asyncio
    async def test_no_proxy_configured_connects_directly(
        self, relay: RelayServer, connect_proxy: ConnectProxy
    ) -> None:
        px = load_proxy()

        observed = await run_tunnel(px.AUTO_PROXY, relay.url)

        _assert_established_and_stopped_cleanly(observed)
        assert relay.accepts == 1
        assert connect_proxy.accepts == 0

    @pytest.mark.asyncio
    async def test_none_ignores_the_system_proxy(
        self,
        monkeypatch: pytest.MonkeyPatch,
        relay: RelayServer,
        connect_proxy: ConnectProxy,
        socks_sink: SocksSink,
    ) -> None:
        px = load_proxy()
        set_system_proxies(
            monkeypatch, clash_shape(connect_proxy.port, socks_port=socks_sink.port)
        )

        observed = await run_tunnel(px.parse_tunnel_proxy("none"), relay.url)

        _assert_established_and_stopped_cleanly(observed)
        assert relay.accepts == 1
        assert connect_proxy.accepts == 0
        assert socks_sink.accepts == 0

    @pytest.mark.asyncio
    async def test_none_connects_directly_on_a_socks_only_system(
        self,
        monkeypatch: pytest.MonkeyPatch,
        relay: RelayServer,
        socks_sink: SocksSink,
    ) -> None:
        px = load_proxy()
        set_system_proxies(
            monkeypatch, {"socks": f"http://127.0.0.1:{socks_sink.port}"}
        )

        observed = await run_tunnel(px.parse_tunnel_proxy("none"), relay.url)

        _assert_established_and_stopped_cleanly(observed)
        assert relay.accepts == 1
        assert socks_sink.accepts == 0


class TestExplicitProxy:

    @pytest.mark.asyncio
    async def test_credentials_in_the_url_become_basic_proxy_auth(
        self, relay: RelayServer, connect_proxy: ConnectProxy
    ) -> None:
        px = load_proxy()
        setting = px.parse_tunnel_proxy(f"http://u:p@127.0.0.1:{connect_proxy.port}")

        observed = await run_tunnel(setting, RELAY_WS_URL)

        _assert_established_and_stopped_cleanly(observed)
        assert connect_proxy.connect_targets == [RELAY_TARGET]
        headers = connect_proxy.requests[0].headers
        assert headers["proxy-authorization"] == basic_auth("u", "p")
        assert relay.accepts == 1
        assert relay.handshakes[0]["authorization"] == "Bearer access-token"

    @pytest.mark.asyncio
    async def test_https_proxy_works_with_the_trusted_tls_context(
        self, relay: RelayServer, tls_connect_proxy: ConnectProxy
    ) -> None:
        px = load_proxy()
        setting = px.parse_tunnel_proxy(f"https://127.0.0.1:{tls_connect_proxy.port}")

        observed = await run_tunnel(setting, RELAY_WS_URL)

        _assert_established_and_stopped_cleanly(observed)
        assert tls_connect_proxy.connect_targets == [RELAY_TARGET]
        assert relay.accepts == 1

    @pytest.mark.asyncio
    async def test_explicit_beats_a_socks_only_system(
        self,
        monkeypatch: pytest.MonkeyPatch,
        relay: RelayServer,
        connect_proxy: ConnectProxy,
        socks_sink: SocksSink,
    ) -> None:
        px = load_proxy()
        set_system_proxies(
            monkeypatch, {"socks": f"http://127.0.0.1:{socks_sink.port}"}
        )
        setting = px.parse_tunnel_proxy(f"http://127.0.0.1:{connect_proxy.port}")

        observed = await run_tunnel(setting, RELAY_WS_URL)

        _assert_established_and_stopped_cleanly(observed)
        assert connect_proxy.connect_targets == [RELAY_TARGET]
        assert socks_sink.accepts == 0


class TestExpiredCredentials:

    @pytest.mark.asyncio
    async def test_socks_only_system_stops_before_any_network_contact(
        self,
        monkeypatch: pytest.MonkeyPatch,
        relay: RelayServer,
        auth_server: AuthServer,
        socks_sink: SocksSink,
    ) -> None:
        from anki_mcp_server.tunnel.auth import DeviceFlowAuth

        px = load_proxy()
        set_system_proxies(
            monkeypatch, {"socks": f"http://127.0.0.1:{socks_sink.port}"}
        )
        auth = DeviceFlowAuth(
            f"wss://127.0.0.1:{auth_server.port}", "client", proxy=px.AUTO_PROXY
        )

        observed = await run_tunnel(px.AUTO_PROXY, relay.url, expired=True, auth=auth)

        _assert_terminal_proxy_failure(observed)
        assert relay.accepts == 0
        assert auth_server.accepts == 0
        assert socks_sink.accepts == 0

    @pytest.mark.asyncio
    async def test_a_proxy_error_from_the_refresh_after_an_auth_close_is_terminal(
        self, relay: RelayServer
    ) -> None:
        from anki_mcp_server.tunnel.auth import ProxyUnusableError

        px = load_proxy()
        relay.close_codes = [4001]
        auth = MagicMock()
        auth.refresh_token = AsyncMock(
            side_effect=ProxyUnusableError("tunnel_proxy cannot use SOCKS")
        )

        observed = await run_tunnel(px.AUTO_PROXY, relay.url, auth=auth)

        auth.refresh_token.assert_awaited_once()
        assert len(observed.stopped) == 1
        code, message = observed.stopped[0]
        assert code == PROXY_UNUSABLE_STOP_CODE
        assert "tunnel_proxy" in message
        assert observed.errors == []
        assert observed.reconnecting == []
        assert relay.accepts == 1

    @pytest.mark.asyncio
    async def test_a_proxy_error_from_the_refresh_is_terminal_too(
        self, relay: RelayServer
    ) -> None:
        from anki_mcp_server.tunnel.auth import ProxyUnusableError

        px = load_proxy()
        auth = MagicMock()
        auth.refresh_token = AsyncMock(
            side_effect=ProxyUnusableError("tunnel_proxy cannot use SOCKS")
        )

        observed = await run_tunnel(px.AUTO_PROXY, relay.url, expired=True, auth=auth)

        _assert_terminal_proxy_failure(observed)
        assert relay.accepts == 0

    @pytest.mark.asyncio
    async def test_a_server_sent_proxy_unsupported_code_is_not_the_local_condition(
        self, relay: RelayServer
    ) -> None:
        from anki_mcp_server.tunnel.auth import AuthError

        px = load_proxy()
        auth = MagicMock()
        auth.refresh_token = AsyncMock(
            side_effect=AuthError("from the relay", error_code="proxy_unsupported")
        )

        observed = await run_tunnel(px.AUTO_PROXY, relay.url, expired=True, auth=auth)

        assert [code for code, _ in observed.errors] == ["token_refresh_failed"]
        assert len(observed.established) == 1
        assert relay.accepts == 1


class TestRouteIsReported:

    @pytest.mark.asyncio
    async def test_one_log_record_per_route(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        connect_proxy: ConnectProxy,
        panel_manager,
    ) -> None:
        px = load_proxy()
        caplog.set_level(logging.INFO, logger=_ADDON_LOGGER)
        set_system_proxies(monkeypatch, clash_shape(connect_proxy.port))
        address = f"127.0.0.1:{connect_proxy.port}"

        observed = await run_tunnel(
            px.AUTO_PROXY, RELAY_WS_URL, connection_manager=panel_manager
        )
        panel_manager._on_tunnel_route(observed.routes[0])

        records = [r for r in caplog.records if address in r.getMessage()]
        assert len(records) == 1, [r.getMessage() for r in records]
        panel = [t for _, t in panel_manager.tunnel_log.entries if address in t]
        assert panel == [f"Route: {observed.routes[0]}"]

    @pytest.mark.asyncio
    async def test_the_proxy_stop_is_one_warning_record(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        socks_sink: SocksSink,
        panel_manager,
    ) -> None:
        px = load_proxy()
        caplog.set_level(logging.DEBUG, logger=_ADDON_LOGGER)
        set_system_proxies(
            monkeypatch, {"socks": f"http://127.0.0.1:{socks_sink.port}"}
        )

        await run_tunnel(px.AUTO_PROXY, RELAY_WS_URL, connection_manager=panel_manager)

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1, [r.getMessage() for r in warnings]

    @pytest.mark.asyncio
    async def test_proxy_route_is_logged_and_sent_to_the_panel_callback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        connect_proxy: ConnectProxy,
    ) -> None:
        px = load_proxy()
        caplog.set_level(logging.INFO, logger=_ADDON_LOGGER)
        set_system_proxies(monkeypatch, clash_shape(connect_proxy.port))
        address = f"127.0.0.1:{connect_proxy.port}"

        observed = await run_tunnel(px.AUTO_PROXY, RELAY_WS_URL)

        assert len(observed.routes) == 1
        assert address in observed.routes[0]
        assert "socks" in observed.routes[0].lower()
        info_lines = [
            r.getMessage() for r in caplog.records if r.levelno == logging.INFO
        ]
        assert any(address in line for line in info_lines), info_lines

    @pytest.mark.asyncio
    async def test_direct_route_says_direct(
        self,
        caplog: pytest.LogCaptureFixture,
        relay: RelayServer,
    ) -> None:
        px = load_proxy()
        caplog.set_level(logging.INFO, logger=_ADDON_LOGGER)

        observed = await run_tunnel(px.AUTO_PROXY, relay.url)

        assert len(observed.routes) == 1
        assert "direct" in observed.routes[0].lower()
        assert any(
            "direct" in r.getMessage().lower()
            for r in caplog.records
            if r.levelno == logging.INFO
        )

    @pytest.mark.asyncio
    async def test_explicit_route_is_masked(
        self,
        caplog: pytest.LogCaptureFixture,
        connect_proxy: ConnectProxy,
    ) -> None:
        px = load_proxy()
        caplog.set_level(logging.DEBUG, logger=_ADDON_LOGGER)
        setting = px.parse_tunnel_proxy(
            f"http://user:{SECRET}@127.0.0.1:{connect_proxy.port}"
        )

        observed = await run_tunnel(setting, RELAY_WS_URL)

        assert f"127.0.0.1:{connect_proxy.port}" in observed.routes[0]
        assert SECRET not in "\n".join(observed.routes)
        assert SECRET not in caplog.text
