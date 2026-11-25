"""Which way the login / token-refresh HTTP calls go (tests 7a-d, 9, 10).

The real ``DeviceFlowAuth`` and the real vendored httpx talk to a local TLS
auth server. ``tunnel_proxy`` governs these calls exactly as it governs the
tunnel WebSocket, including the SOCKS-only terminal error.
"""
from __future__ import annotations

import asyncio

import pytest

from .helpers import (
    DEVICE_CODE_BODY,
    RELAY_TARGET,
    RELAY_WS_URL,
    SCENARIO_TIMEOUT,
    WINDOWS_SHAPE_IDS,
    AuthServer,
    ConnectProxy,
    SocksSink,
    basic_auth,
    clash_shape,
    load_proxy,
    set_system_proxies,
    windows_socks_only_shapes,
)


def _auth(setting, url: str):
    from anki_mcp_server.tunnel.auth import DeviceFlowAuth

    return DeviceFlowAuth(url, "client", proxy=setting)


async def _request_code(auth):
    return await asyncio.wait_for(auth.request_device_code(), SCENARIO_TIMEOUT)


def _assert_proxy_unsupported(excinfo, auth_server: AuthServer) -> None:
    error = excinfo.value
    assert error.error_code == "proxy_unsupported"
    assert "tunnel_proxy" in str(error)
    assert "socks" in str(error).lower()
    assert auth_server.accepts == 0


class TestAutoMode:

    @pytest.mark.asyncio
    async def test_clash_shape_goes_through_the_http_proxy(
        self,
        monkeypatch: pytest.MonkeyPatch,
        auth_server: AuthServer,
        connect_proxy: ConnectProxy,
        socks_sink: SocksSink,
    ) -> None:
        px = load_proxy()
        connect_proxy.upstreams[RELAY_TARGET] = auth_server.port
        set_system_proxies(
            monkeypatch, clash_shape(connect_proxy.port, socks_port=socks_sink.port)
        )

        device = await _request_code(_auth(px.AUTO_PROXY, RELAY_WS_URL))

        assert device.user_code == DEVICE_CODE_BODY["user_code"]
        assert connect_proxy.connect_targets == [RELAY_TARGET]
        assert auth_server.accepts == 1
        assert socks_sink.accepts == 0

    @pytest.mark.asyncio
    async def test_socks_only_system_is_a_terminal_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        auth_server: AuthServer,
        socks_sink: SocksSink,
    ) -> None:
        from anki_mcp_server.tunnel.auth import AuthError

        px = load_proxy()
        set_system_proxies(
            monkeypatch, {"socks": f"http://127.0.0.1:{socks_sink.port}"}
        )
        auth = _auth(px.AUTO_PROXY, f"wss://127.0.0.1:{auth_server.port}")

        with pytest.raises(AuthError) as excinfo:
            await _request_code(auth)

        _assert_proxy_unsupported(excinfo, auth_server)
        assert socks_sink.accepts == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("index", [0, 1], ids=WINDOWS_SHAPE_IDS)
    async def test_windows_socks_only_shapes_are_terminal_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        auth_server: AuthServer,
        socks_sink: SocksSink,
        index: int,
    ) -> None:
        from anki_mcp_server.tunnel.auth import AuthError

        px = load_proxy()
        set_system_proxies(
            monkeypatch, windows_socks_only_shapes(socks_sink.port)[index]
        )
        auth = _auth(px.AUTO_PROXY, f"wss://127.0.0.1:{auth_server.port}")

        with pytest.raises(AuthError) as excinfo:
            await _request_code(auth)

        _assert_proxy_unsupported(excinfo, auth_server)
        assert socks_sink.accepts == 0

    @pytest.mark.asyncio
    async def test_windows_mixed_shape_goes_through_the_http_proxy(
        self,
        monkeypatch: pytest.MonkeyPatch,
        auth_server: AuthServer,
        connect_proxy: ConnectProxy,
        socks_sink: SocksSink,
    ) -> None:
        px = load_proxy()
        connect_proxy.upstreams[RELAY_TARGET] = auth_server.port
        set_system_proxies(monkeypatch, {
            "http": f"http://127.0.0.1:{connect_proxy.port}",
            "https": f"http://127.0.0.1:{connect_proxy.port}",
            "socks": f"socks://127.0.0.1:{socks_sink.port}",
        })

        await _request_code(_auth(px.AUTO_PROXY, RELAY_WS_URL))

        assert connect_proxy.connect_targets == [RELAY_TARGET]
        assert socks_sink.accepts == 0

    @pytest.mark.asyncio
    async def test_scheme_less_entry_goes_through_the_http_proxy(
        self,
        monkeypatch: pytest.MonkeyPatch,
        auth_server: AuthServer,
        connect_proxy: ConnectProxy,
    ) -> None:
        px = load_proxy()
        connect_proxy.upstreams[RELAY_TARGET] = auth_server.port
        set_system_proxies(monkeypatch, {"https": f"127.0.0.1:{connect_proxy.port}"})

        await _request_code(_auth(px.AUTO_PROXY, RELAY_WS_URL))

        assert connect_proxy.connect_targets == [RELAY_TARGET]
        assert auth_server.accepts == 1

    @pytest.mark.asyncio
    async def test_bypassed_auth_host_is_reached_directly(
        self,
        monkeypatch: pytest.MonkeyPatch,
        auth_server: AuthServer,
        connect_proxy: ConnectProxy,
    ) -> None:
        px = load_proxy()
        set_system_proxies(
            monkeypatch,
            clash_shape(connect_proxy.port),
            bypass=lambda host: host == "127.0.0.1",
        )

        await _request_code(
            _auth(px.AUTO_PROXY, f"wss://127.0.0.1:{auth_server.port}")
        )

        assert auth_server.accepts == 1
        assert connect_proxy.accepts == 0

    @pytest.mark.asyncio
    async def test_the_shape_is_read_on_every_request_not_at_construction(
        self,
        monkeypatch: pytest.MonkeyPatch,
        auth_server: AuthServer,
        connect_proxy: ConnectProxy,
    ) -> None:
        px = load_proxy()
        target = f"127.0.0.1:{auth_server.port}"
        connect_proxy.upstreams[target] = auth_server.port
        auth = _auth(px.AUTO_PROXY, f"wss://{target}")

        await _request_code(auth)
        assert connect_proxy.accepts == 0

        set_system_proxies(monkeypatch, clash_shape(connect_proxy.port))
        await _request_code(auth)

        assert connect_proxy.connect_targets == [target]
        assert auth_server.accepts == 2

    @pytest.mark.asyncio
    async def test_refresh_hits_the_same_terminal_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        auth_server: AuthServer,
        socks_sink: SocksSink,
    ) -> None:
        from anki_mcp_server.tunnel.auth import AuthError

        px = load_proxy()
        set_system_proxies(
            monkeypatch, {"socks": f"http://127.0.0.1:{socks_sink.port}"}
        )
        auth = _auth(px.AUTO_PROXY, f"wss://127.0.0.1:{auth_server.port}")

        with pytest.raises(AuthError) as excinfo:
            await asyncio.wait_for(auth.refresh_token("refresh"), SCENARIO_TIMEOUT)

        _assert_proxy_unsupported(excinfo, auth_server)


class TestNoneAndExplicit:

    @pytest.mark.asyncio
    async def test_none_goes_direct_even_with_a_system_proxy(
        self,
        monkeypatch: pytest.MonkeyPatch,
        auth_server: AuthServer,
        connect_proxy: ConnectProxy,
    ) -> None:
        px = load_proxy()
        set_system_proxies(monkeypatch, clash_shape(connect_proxy.port))
        auth = _auth(
            px.parse_tunnel_proxy("none"), f"wss://127.0.0.1:{auth_server.port}"
        )

        device = await _request_code(auth)

        assert device.user_code == DEVICE_CODE_BODY["user_code"]
        assert auth_server.accepts == 1
        assert connect_proxy.accepts == 0

    @pytest.mark.asyncio
    async def test_none_goes_direct_on_a_socks_only_system(
        self,
        monkeypatch: pytest.MonkeyPatch,
        auth_server: AuthServer,
        socks_sink: SocksSink,
    ) -> None:
        px = load_proxy()
        set_system_proxies(
            monkeypatch, {"socks": f"http://127.0.0.1:{socks_sink.port}"}
        )
        auth = _auth(
            px.parse_tunnel_proxy("none"), f"wss://127.0.0.1:{auth_server.port}"
        )

        await _request_code(auth)

        assert auth_server.accepts == 1
        assert socks_sink.accepts == 0

    @pytest.mark.asyncio
    async def test_explicit_proxy_with_credentials(
        self, auth_server: AuthServer, connect_proxy: ConnectProxy
    ) -> None:
        px = load_proxy()
        connect_proxy.upstreams[RELAY_TARGET] = auth_server.port
        setting = px.parse_tunnel_proxy(f"http://u:p@127.0.0.1:{connect_proxy.port}")

        await _request_code(_auth(setting, RELAY_WS_URL))

        assert connect_proxy.connect_targets == [RELAY_TARGET]
        headers = connect_proxy.requests[0].headers
        assert headers["proxy-authorization"] == basic_auth("u", "p")
        assert auth_server.accepts == 1

    @pytest.mark.asyncio
    async def test_explicit_https_proxy(
        self, auth_server: AuthServer, tls_connect_proxy: ConnectProxy
    ) -> None:
        px = load_proxy()
        tls_connect_proxy.upstreams[RELAY_TARGET] = auth_server.port
        setting = px.parse_tunnel_proxy(f"https://127.0.0.1:{tls_connect_proxy.port}")

        await _request_code(_auth(setting, RELAY_WS_URL))

        assert tls_connect_proxy.connect_targets == [RELAY_TARGET]
        assert auth_server.accepts == 1

    @pytest.mark.asyncio
    async def test_explicit_beats_a_socks_only_system(
        self,
        monkeypatch: pytest.MonkeyPatch,
        auth_server: AuthServer,
        connect_proxy: ConnectProxy,
        socks_sink: SocksSink,
    ) -> None:
        px = load_proxy()
        connect_proxy.upstreams[RELAY_TARGET] = auth_server.port
        set_system_proxies(
            monkeypatch, {"socks": f"http://127.0.0.1:{socks_sink.port}"}
        )
        setting = px.parse_tunnel_proxy(f"http://127.0.0.1:{connect_proxy.port}")

        await _request_code(_auth(setting, RELAY_WS_URL))

        assert connect_proxy.connect_targets == [RELAY_TARGET]
        assert socks_sink.accepts == 0
