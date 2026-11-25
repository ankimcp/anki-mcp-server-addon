"""Shared infrastructure for the tunnel proxy-routing tests.

Real local servers (a TLS WebSocket relay, an HTTP CONNECT proxy, a SOCKS
sink, a TLS auth server) plus the helpers that point ``TunnelReconnectManager``,
``TunnelClient`` and ``DeviceFlowAuth`` at them. Nothing here mocks the
vendored ``websockets`` or ``httpx``: the tests drive the real libraries over
real loopback sockets and assert on what the servers recorded.

Targets that go through a proxy use ``relay.test`` (the test proxy maps it to a
local port, so no client-side DNS happens); direct targets use the literal
``127.0.0.1:<port>`` -- never ``localhost``, which may resolve to ``::1``.

``anki_mcp_server.tunnel.proxy`` does not exist until the fix lands, so it is
only ever reached through :func:`load_proxy`, which turns a missing module
into a test FAILURE with a message naming the missing behaviour.
"""
from __future__ import annotations

# The package __init__ must run before the MCP SDK is imported.
import anki_mcp_server  # noqa: F401

import asyncio
import base64
import importlib
import json
import ssl
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock

import httpx._utils as httpx_utils
import pytest
from websockets.asyncio.server import ServerConnection, serve

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
CA_PEM = FIXTURES_DIR / "test-ca.pem"
LEAF_PEM = FIXTURES_DIR / "test-leaf.pem"
LEAF_KEY = FIXTURES_DIR / "test-leaf.key"

RELAY_HOST = "relay.test"
RELAY_TARGET = f"{RELAY_HOST}:443"
RELAY_WS_URL = f"wss://{RELAY_HOST}"
PUBLIC_TUNNEL_URL = "https://relay.test/t/abc"

# Keyword under which TunnelReconnectManager reports the chosen route as a
# human-readable line for the tunnel log panel. The spec asks for such a
# callback but does not name it; this is the one assumption about its name.
ROUTE_CALLBACK_KWARG = "on_route"

# Bounds every awaited scenario; the 10s CONNECTION_TIMEOUT and the 30s
# health-check sleep must never be what ends a test.
SCENARIO_TIMEOUT = 5.0

SECRET = "s3cret"
ADDON_LOGGER = "anki_mcp_server"


def load_proxy() -> Any:
    """Import ``anki_mcp_server.tunnel.proxy`` or FAIL the calling test."""
    try:
        return importlib.import_module("anki_mcp_server.tunnel.proxy")
    except ImportError as exc:
        pytest.fail(
            "anki_mcp_server.tunnel.proxy is missing: the tunnel proxy routing "
            f"fix (spec section 3) is not implemented yet ({exc!r})",
            pytrace=False,
        )


# ---------------------------------------------------------------------------
# TLS
# ---------------------------------------------------------------------------

def server_tls_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(LEAF_PEM), str(LEAF_KEY))
    return ctx


def client_tls_context() -> ssl.SSLContext:
    """Stands in for ``tunnel.tls.create_tls_context``: trusts only the test CA."""
    return ssl.create_default_context(cafile=str(CA_PEM))


def untrusted_tls_context() -> ssl.SSLContext:
    """Verifies like the real one but trusts nothing, so every server certificate fails."""
    return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


# ---------------------------------------------------------------------------
# System proxy shapes
# ---------------------------------------------------------------------------

def set_system_proxies(
    monkeypatch: pytest.MonkeyPatch,
    shape: dict[str, str],
    bypass: Callable[[str], bool] = lambda host: False,
) -> None:
    """Make ``urllib.request`` (and httpx's bound copy) report ``shape``."""
    monkeypatch.setattr(urllib.request, "getproxies", lambda: dict(shape))
    monkeypatch.setattr(httpx_utils, "getproxies", lambda: dict(shape))
    monkeypatch.setattr(urllib.request, "proxy_bypass", bypass)


def clash_shape(port: int, *, socks_port: int | None = None) -> dict[str, str]:
    """What a Clash-style VPN sets: http, https and socks, macOS spelling."""
    return {
        "http": f"http://127.0.0.1:{port}",
        "https": f"http://127.0.0.1:{port}",
        "socks": f"http://127.0.0.1:{socks_port or port}",
    }


def windows_socks_only_shapes(port: int) -> list[dict[str, str]]:
    """The two registry shapes Windows produces for a SOCKS-only setup."""
    return [
        {
            "socks": f"socks://127.0.0.1:{port}",
            "http": f"socks4://127.0.0.1:{port}",
            "https": f"socks4://127.0.0.1:{port}",
        },
        {
            "http": f"socks://127.0.0.1:{port}",
            "https": f"socks://127.0.0.1:{port}",
            "ftp": f"socks://127.0.0.1:{port}",
        },
    ]


WINDOWS_SHAPE_IDS = ["windows-socks-key", "windows-socks-scheme"]


# ---------------------------------------------------------------------------
# Local servers
# ---------------------------------------------------------------------------

class StreamServer:
    """Loopback TCP server that counts RAW accepts (before any TLS handshake)."""

    def __init__(self, ssl_context: ssl.SSLContext | None = None) -> None:
        self.accepts = 0
        self.port = 0
        self._ssl = ssl_context
        self._server: asyncio.base_events.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> "StreamServer":
        loop = asyncio.get_running_loop()

        def factory() -> asyncio.StreamReaderProtocol:
            self.accepts += 1
            reader = asyncio.StreamReader(loop=loop)
            return asyncio.StreamReaderProtocol(
                reader, self._on_connection, loop=loop
            )

        kwargs: dict[str, Any] = {}
        if self._ssl is not None:
            kwargs.update(ssl=self._ssl, ssl_shutdown_timeout=1)
        self._server = await loop.create_server(
            factory, "127.0.0.1", 0, **kwargs
        )
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        for writer in list(self._writers):
            writer.transport.abort()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._server is not None:
            await asyncio.wait_for(self._server.wait_closed(), 2)

    async def _on_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._writers.add(writer)
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        try:
            await self.handle(reader, writer)
        except (ConnectionError, ssl.SSLError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            self._writers.discard(writer)
            self._tasks.discard(task)

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        raise NotImplementedError


async def _read_head(reader: asyncio.StreamReader) -> tuple[str, dict[str, str]]:
    raw = await reader.readuntil(b"\r\n\r\n")
    lines = raw.decode("latin-1").split("\r\n")
    headers = {
        key.strip().lower(): value.strip()
        for key, _, value in (line.partition(":") for line in lines[1:] if ":" in line)
    }
    return lines[0], headers


class SocksSink(StreamServer):
    """Only counts accepts. Anything that connects is dropped immediately."""

    async def handle(self, reader, writer) -> None:
        return


@dataclass
class ProxyRequest:
    request_line: str
    headers: dict[str, str]

    @property
    def target(self) -> str:
        return self.request_line.split(" ")[1]


class ConnectProxy(StreamServer):
    """HTTP CONNECT proxy mapping every known target to a local upstream port."""

    def __init__(
        self,
        upstreams: dict[str, int] | None = None,
        *,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        super().__init__(ssl_context)
        self.upstreams: dict[str, int] = dict(upstreams or {})
        self.reply_407 = False
        self.requests: list[ProxyRequest] = []

    @property
    def connect_targets(self) -> list[str]:
        return [r.target for r in self.requests if r.request_line.startswith("CONNECT ")]

    async def handle(self, reader, writer) -> None:
        request_line, headers = await _read_head(reader)
        request = ProxyRequest(request_line, headers)
        self.requests.append(request)

        if self.reply_407:
            writer.write(
                b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                b'Proxy-Authenticate: Basic realm="test"\r\n'
                b"Content-Length: 0\r\nConnection: close\r\n\r\n"
            )
            await writer.drain()
            return

        upstream = (
            self.upstreams.get(request.target)
            if request_line.startswith("CONNECT ")
            else None
        )
        if upstream is None:
            writer.write(
                b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n"
                b"Connection: close\r\n\r\n"
            )
            await writer.drain()
            return

        up_reader, up_writer = await asyncio.open_connection("127.0.0.1", upstream)
        try:
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await asyncio.gather(
                _pipe(reader, up_writer),
                _pipe(up_reader, writer),
                return_exceptions=True,
            )
        finally:
            up_writer.close()


def basic_auth(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()


DEVICE_CODE_BODY = {
    "device_code": "dev-123",
    "user_code": "ABCD-EFGH",
    "verification_uri": "https://relay.test/verify",
    "expires_in": 600,
    "interval": 5,
}
TOKEN_BODY = {
    "access_token": "access-1",
    "refresh_token": "refresh-1",
    "expires_in": 3600,
    "user": {"email": "test@example.com", "tier": "free"},
}


class AuthServer(StreamServer):
    """HTTPS server answering ``/auth/device`` and ``/auth/token`` with JSON."""

    def __init__(self) -> None:
        super().__init__(server_tls_context())
        self.requests: list[tuple[str, str]] = []

    async def handle(self, reader, writer) -> None:
        request_line, headers = await _read_head(reader)
        body = await reader.readexactly(int(headers.get("content-length", "0")))
        path = request_line.split(" ")[1]
        self.requests.append((path, body.decode()))
        payload = DEVICE_CODE_BODY if path == "/auth/device" else TOKEN_BODY
        data = json.dumps(payload).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode()
            + data
        )
        await writer.drain()


class RelayServer:
    """TLS WebSocket relay: sends ``tunnel_established`` then closes.

    ``close_codes`` is consumed one per connection (the last one repeats); 1000
    ends the tunnel cleanly, an application code such as 4008 makes the client
    reconnect and 4001 makes it refresh its token first.
    """

    def __init__(self, close_codes: list[int] | None = None) -> None:
        self.close_codes = list(close_codes or [1000])
        self.accepts = 0
        self.port = 0
        self.handshakes: list[dict[str, str]] = []
        self._server: Any = None

    @property
    def url(self) -> str:
        """Direct URL; the literal address keeps resolution out of the picture."""
        return f"wss://127.0.0.1:{self.port}"

    async def start(self) -> "RelayServer":
        relay = self

        class CountingConnection(ServerConnection):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                relay.accepts += 1
                super().__init__(*args, **kwargs)

        async def handler(ws: ServerConnection) -> None:
            relay.handshakes.append(
                {k.lower(): v for k, v in ws.request.headers.raw_items()}
            )
            await ws.send(
                json.dumps({"type": "tunnel_established", "url": PUBLIC_TUNNEL_URL})
            )
            codes = relay.close_codes
            await ws.close(codes.pop(0) if len(codes) > 1 else codes[0])

        self._server = await serve(
            handler,
            "127.0.0.1",
            0,
            ssl=server_tls_context(),
            ping_interval=None,
            create_connection=CountingConnection,
        )
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        self._server.close()
        await asyncio.wait_for(self._server.wait_closed(), 2)


# ---------------------------------------------------------------------------
# Driving a real TunnelReconnectManager
# ---------------------------------------------------------------------------

@dataclass
class Observed:
    """Everything a scenario reported through its callbacks."""

    established: list[tuple[str, Any]] = field(default_factory=list)
    disconnected: list[tuple[int, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    reconnecting: list[tuple[int, float]] = field(default_factory=list)
    stopped: list[tuple[int, str]] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    certificate_errors: list[str] = field(default_factory=list)

    def all_text(self) -> list[str]:
        texts: list[str] = [*self.routes, *self.certificate_errors]
        texts += [url for url, _ in self.established]
        texts += [reason for _, reason in self.disconnected]
        texts += [part for pair in self.errors for part in pair]
        texts += [reason for _, reason in self.stopped]
        return texts


def make_credentials_manager(*, expired: bool = False) -> MagicMock:
    creds = MagicMock()
    creds.user = {"email": "test@example.com", "tier": "free"}
    creds.access_token = "access-token"
    creds.refresh_token = "refresh-token"
    cm = MagicMock()
    cm.load.return_value = creds
    cm.is_token_expired.return_value = expired
    return cm


async def run_tunnel(
    setting: Any,
    server_url: str,
    *,
    expired: bool = False,
    auth: Any = None,
    connection_manager: Any = None,
    on_reconnecting_hook: Callable[[], None] | None = None,
    timeout: float = SCENARIO_TIMEOUT,
) -> Observed:
    """Run a real ``TunnelReconnectManager`` to its terminal stop.

    ``connection_manager``, when given, also receives the error/stopped
    callbacks so the tests can read what the tunnel log panel would show.
    """
    from anki_mcp_server.tunnel import reconnect as reconnect_module

    observed = Observed()

    if auth is None:
        auth = MagicMock()
        auth.refresh_token = _async_none

    def on_established(url: str, user: Any) -> None:
        observed.established.append((url, user))
        if connection_manager is not None:
            connection_manager._on_tunnel_established(url, user)

    def on_disconnected(code: int, reason: str) -> None:
        observed.disconnected.append((code, reason))

    def on_error(code: str, message: str) -> None:
        observed.errors.append((code, message))
        if connection_manager is not None:
            connection_manager._on_tunnel_error(code, message)

    def on_reconnecting(attempt: int, delay: float) -> None:
        observed.reconnecting.append((attempt, delay))
        if on_reconnecting_hook is not None:
            on_reconnecting_hook()

    def on_stopped(code: int, reason: str) -> None:
        observed.stopped.append((code, reason))
        if connection_manager is not None:
            connection_manager._on_tunnel_stopped(code, reason)

    def on_certificate_error(message: str) -> None:
        observed.certificate_errors.append(message)
        if connection_manager is not None:
            connection_manager._on_tunnel_certificate_error(message)

    manager = reconnect_module.TunnelReconnectManager(
        server_url=server_url,
        mcp_server=MagicMock(),
        credentials_manager=make_credentials_manager(expired=expired),
        auth=auth,
        proxy=setting,
        on_tunnel_established=on_established,
        on_disconnected=on_disconnected,
        on_error=on_error,
        on_reconnecting=on_reconnecting,
        on_stopped=on_stopped,
        on_certificate_error=on_certificate_error,
        **{ROUTE_CALLBACK_KWARG: observed.routes.append},
    )
    await asyncio.wait_for(manager.run(), timeout)
    return observed


async def _async_none(*args: Any, **kwargs: Any) -> None:
    return None
