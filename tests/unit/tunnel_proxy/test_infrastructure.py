"""The proxy test infrastructure itself.

These pass today. They prove the vendored libraries are the ones under test,
the ambient proxy is neutralised, and every local server works when driven
directly with explicit ``proxy=`` arguments -- so a red routing test later
means the fix is missing, not that a fixture is broken.
"""
from __future__ import annotations

import asyncio
import inspect
import importlib.util
import json
import os
import urllib.request
from pathlib import Path

import httpx
import httpx._utils as httpx_utils
from packaging.version import Version
import pytest
import websockets
from websockets.asyncio.client import connect
from websockets.exceptions import ProxyError

from .helpers import (
    DEVICE_CODE_BODY,
    PUBLIC_TUNNEL_URL,
    RELAY_TARGET,
    RELAY_WS_URL,
    SCENARIO_TIMEOUT,
    AuthServer,
    ConnectProxy,
    RelayServer,
    SocksSink,
    basic_auth,
    client_tls_context,
)

_VENDOR_SHARED = (
    Path(__file__).resolve().parents[3] / "anki_mcp_server" / "vendor" / "shared"
)


class TestVendoredLibraries:

    def test_libraries_come_from_the_vendor_dir(self) -> None:
        for module in (websockets, httpx):
            location = Path(module.__file__).resolve()
            assert _VENDOR_SHARED in location.parents, (
                f"{module.__name__} is imported from {location}, not from the "
                "vendored copy the add-on ships; the tests would exercise "
                "different code than production"
            )

    def test_vendored_api_has_what_the_fix_relies_on(self) -> None:
        assert websockets.version.version.startswith("17.")
        assert "proxy" in inspect.signature(connect.__init__).parameters
        # proxy_* keywords travel through **kwargs; the API doc names proxy_ssl.
        assert "proxy_ssl" in (connect.__doc__ or "")
        # httpx 1.x is a rewrite; pre-releases such as 1.0.dev3 count as 1.x.
        assert Version(httpx.__version__).major < 1, (
            f"vendored httpx is {httpx.__version__}, 1.x or later; its API "
            "differs from the 0.x one the add-on is written against"
        )
        assert "ssl_context" in inspect.signature(httpx.Proxy.__init__).parameters
        assert "trust_env" in inspect.signature(httpx.AsyncClient.__init__).parameters

    @pytest.mark.parametrize("name", ["python_socks", "socksio"])
    def test_no_socks_library_is_available(self, name: str) -> None:
        assert importlib.util.find_spec(name) is None, (
            f"{name} is importable; the add-on does not ship it, so a test "
            "environment that has it would hide the production failure"
        )


class TestAmbientProxyIsolation:

    def test_no_proxy_variables_and_no_system_proxy(self) -> None:
        leftovers = [n for n in os.environ if n.lower().endswith("_proxy")]
        assert leftovers == []
        assert urllib.request.getproxies() == {}
        assert httpx_utils.getproxies() == {}
        assert urllib.request.proxy_bypass("anything.example") is False


async def _tunnel_message(url: str, **connect_kwargs: object) -> dict:
    async with asyncio.timeout(SCENARIO_TIMEOUT):
        async with connect(
            url, ssl=client_tls_context(), open_timeout=SCENARIO_TIMEOUT,
            **connect_kwargs,
        ) as ws:
            return json.loads(await ws.recv())


class TestRelayAndProxyWithWebsockets:

    @pytest.mark.asyncio
    async def test_relay_direct(self, relay: RelayServer) -> None:
        message = await _tunnel_message(relay.url, proxy=None)

        assert message == {"type": "tunnel_established", "url": PUBLIC_TUNNEL_URL}
        assert relay.accepts == 1

    @pytest.mark.asyncio
    async def test_connect_proxy_forwards_to_relay(
        self, relay: RelayServer, connect_proxy: ConnectProxy
    ) -> None:
        message = await _tunnel_message(
            RELAY_WS_URL, proxy=f"http://127.0.0.1:{connect_proxy.port}"
        )

        assert message["type"] == "tunnel_established"
        assert connect_proxy.connect_targets == [RELAY_TARGET]
        assert relay.accepts == 1

    @pytest.mark.asyncio
    async def test_connect_proxy_records_basic_credentials(
        self, connect_proxy: ConnectProxy
    ) -> None:
        await _tunnel_message(
            RELAY_WS_URL, proxy=f"http://u:p@127.0.0.1:{connect_proxy.port}"
        )

        headers = connect_proxy.requests[0].headers
        assert headers["proxy-authorization"] == basic_auth("u", "p")

    @pytest.mark.asyncio
    async def test_tls_proxy_forwards_to_relay(
        self, relay: RelayServer, tls_connect_proxy: ConnectProxy
    ) -> None:
        message = await _tunnel_message(
            RELAY_WS_URL,
            proxy=f"https://127.0.0.1:{tls_connect_proxy.port}",
            proxy_ssl=client_tls_context(),
        )

        assert message["type"] == "tunnel_established"
        assert tls_connect_proxy.connect_targets == [RELAY_TARGET]
        assert relay.accepts == 1

    @pytest.mark.asyncio
    async def test_proxy_can_answer_407(
        self, relay: RelayServer, connect_proxy: ConnectProxy
    ) -> None:
        connect_proxy.reply_407 = True

        with pytest.raises(ProxyError):
            await _tunnel_message(
                RELAY_WS_URL, proxy=f"http://127.0.0.1:{connect_proxy.port}"
            )

        assert relay.accepts == 0


class TestAuthServerWithHttpx:

    @pytest.mark.asyncio
    async def test_direct(self, auth_server: AuthServer) -> None:
        async with httpx.AsyncClient(
            verify=client_tls_context(), trust_env=False
        ) as client:
            response = await asyncio.wait_for(
                client.post(
                    f"https://127.0.0.1:{auth_server.port}/auth/device",
                    data={"client_id": "c"},
                ),
                SCENARIO_TIMEOUT,
            )

        assert response.json() == DEVICE_CODE_BODY
        assert auth_server.accepts == 1
        assert auth_server.requests == [("/auth/device", "client_id=c")]

    @pytest.mark.asyncio
    async def test_through_connect_proxy(
        self, auth_server: AuthServer, connect_proxy: ConnectProxy
    ) -> None:
        connect_proxy.upstreams[RELAY_TARGET] = auth_server.port

        async with httpx.AsyncClient(
            verify=client_tls_context(),
            proxy=f"http://u:p@127.0.0.1:{connect_proxy.port}",
        ) as client:
            response = await asyncio.wait_for(
                client.post("https://relay.test/auth/token", data={"a": "b"}),
                SCENARIO_TIMEOUT,
            )

        assert response.json()["access_token"] == "access-1"
        assert connect_proxy.connect_targets == [RELAY_TARGET]
        headers = connect_proxy.requests[0].headers
        assert headers["proxy-authorization"] == basic_auth("u", "p")

    @pytest.mark.asyncio
    async def test_through_tls_proxy(
        self, auth_server: AuthServer, tls_connect_proxy: ConnectProxy
    ) -> None:
        tls_connect_proxy.upstreams[RELAY_TARGET] = auth_server.port
        ctx = client_tls_context()

        async with httpx.AsyncClient(
            verify=ctx,
            proxy=httpx.Proxy(
                f"https://127.0.0.1:{tls_connect_proxy.port}", ssl_context=ctx
            ),
        ) as client:
            response = await asyncio.wait_for(
                client.post("https://relay.test/auth/device", data={"a": "b"}),
                SCENARIO_TIMEOUT,
            )

        assert response.json()["user_code"] == "ABCD-EFGH"
        assert tls_connect_proxy.connect_targets == [RELAY_TARGET]


class TestSocksSink:

    @pytest.mark.asyncio
    async def test_counts_accepts(self, socks_sink: SocksSink) -> None:
        for expected in (1, 2):
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", socks_sink.port),
                SCENARIO_TIMEOUT,
            )
            assert await asyncio.wait_for(reader.read(), SCENARIO_TIMEOUT) == b""
            writer.close()
            assert socks_sink.accepts == expected

    @pytest.mark.asyncio
    async def test_untouched_servers_report_zero(
        self,
        relay: RelayServer,
        auth_server: AuthServer,
        connect_proxy: ConnectProxy,
        socks_sink: SocksSink,
    ) -> None:
        assert (
            relay.accepts, auth_server.accepts,
            connect_proxy.accepts, socks_sink.accepts,
        ) == (0, 0, 0, 0)
