"""Fixtures for the tunnel proxy-routing tests (servers, TLS trust, no backoff)."""
from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from anki_mcp_server import connection_manager as cm_module
from anki_mcp_server.config import Config
from anki_mcp_server.tunnel import auth as auth_module
from anki_mcp_server.tunnel import client as client_module
from anki_mcp_server.tunnel import reconnect as reconnect_module

from ..fakes import FakeTunnelLog
from .helpers import (
    RELAY_TARGET,
    AuthServer,
    ConnectProxy,
    RelayServer,
    SocksSink,
    client_tls_context,
    server_tls_context,
)


@pytest.fixture(autouse=True)
def _tunnel_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trust only the test CA, skip backoff sleeps, and fake the MCP transport."""
    monkeypatch.setattr(client_module, "create_tls_context", client_tls_context)
    monkeypatch.setattr(auth_module, "create_tls_context", client_tls_context)
    monkeypatch.setattr(
        reconnect_module.TunnelReconnectManager,
        "_calculate_delay",
        lambda self, attempt: 0.0,
    )
    transport = MagicMock()
    transport.start = AsyncMock()
    transport.stop = AsyncMock()
    monkeypatch.setattr(
        reconnect_module, "InMemoryTransport", MagicMock(return_value=transport)
    )


@pytest.fixture()
def panel_manager(monkeypatch: pytest.MonkeyPatch) -> cm_module.ConnectionManager:
    """A real ConnectionManager whose tunnel log panel is the in-memory fake."""
    monkeypatch.setattr(cm_module, "TunnelLog", FakeTunnelLog)
    monkeypatch.setattr(cm_module, "CredentialsManager", MagicMock)
    monkeypatch.setattr(cm_module, "DeviceFlowAuth", MagicMock)
    return cm_module.ConnectionManager(Config())


@pytest_asyncio.fixture
async def relay() -> AsyncIterator[RelayServer]:
    server = await RelayServer().start()
    yield server
    await server.stop()


@pytest_asyncio.fixture
async def auth_server() -> AsyncIterator[AuthServer]:
    server = await AuthServer().start()
    yield server
    await server.stop()


@pytest_asyncio.fixture
async def socks_sink() -> AsyncIterator[SocksSink]:
    server = await SocksSink().start()
    yield server
    await server.stop()


@pytest_asyncio.fixture
async def connect_proxy(relay: RelayServer) -> AsyncIterator[ConnectProxy]:
    """Plain HTTP CONNECT proxy; ``relay.test:443`` goes to the relay.

    Anything else answers 502. Tests that proxy the login flow re-point
    ``upstreams`` at the auth server.
    """
    server = await ConnectProxy({RELAY_TARGET: relay.port}).start()
    yield server
    await server.stop()


@pytest_asyncio.fixture
async def tls_connect_proxy(relay: RelayServer) -> AsyncIterator[ConnectProxy]:
    """HTTP CONNECT proxy that is itself reached over TLS (``https://`` proxy)."""
    server = await ConnectProxy(
        {RELAY_TARGET: relay.port}, ssl_context=server_tls_context()
    ).start()
    yield server
    await server.stop()
