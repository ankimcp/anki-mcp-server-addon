"""An untrusted certificate over the real transports (tunnel WebSocket and login HTTP).

The real ``TunnelReconnectManager`` / ``TunnelClient`` / ``DeviceFlowAuth`` run
with the vendored ``websockets`` / ``httpx`` against the local TLS servers, but
with a client context that trusts no CA (``untrusted_tls``), so every
certificate fails to verify. Direct, through a CONNECT proxy and through an
``https://`` proxy whose own certificate is the untrusted one.
"""
from __future__ import annotations

# The package __init__ must run before the MCP SDK is imported.
import anki_mcp_server  # noqa: F401

import asyncio
import logging
import socket
import ssl
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from anki_mcp_server.tunnel.auth import (
    AuthError,
    CertificateVerificationError,
    DeviceFlowAuth,
)
from anki_mcp_server.tunnel.client import (
    TunnelCertificateError,
    TunnelClient,
    TunnelConnectionError,
)
from anki_mcp_server.tunnel.login_failure import log_login_failure
from anki_mcp_server.tunnel.protocol import RECONNECT_MAX_ATTEMPTS
from anki_mcp_server.tunnel.proxy import ProxyRoute, parse_tunnel_proxy

from .helpers import (
    ADDON_LOGGER,
    RELAY_TARGET,
    RELAY_WS_URL,
    SCENARIO_TIMEOUT,
    SECRET,
    AuthServer,
    ConnectProxy,
    Observed,
    RelayServer,
    run_tunnel,
)

pytestmark = pytest.mark.usefixtures("untrusted_tls")

GAVE_UP = (
    f"Max reconnection attempts ({RECONNECT_MAX_ATTEMPTS}) exhausted "
    "(last error: certificate could not be verified — see above)"
)
NONE_PROXY = parse_tunnel_proxy("none")


def _assert_certificate_message(message: str, host: str, *, proxy_clause: str | None) -> None:
    assert f"connecting to {host} (" in message
    assert message.startswith("A security certificate could not be verified")
    assert "proxy, antivirus or company network" in message
    assert "date and time are wrong" in message
    assert "SSL_CERT_FILE" in message
    assert "restart Anki" in message
    assert "('" not in message
    if proxy_clause is None:
        assert "routed" not in message
    else:
        assert f"routed {proxy_clause}" in message


def _assert_retried_then_gave_up(observed: Observed, panel_manager) -> str:
    """Ten certificate errors, nine backoffs, one give-up line; returns the message."""
    assert observed.established == []
    assert observed.errors == []
    assert len(observed.certificate_errors) == RECONNECT_MAX_ATTEMPTS
    assert [attempt for attempt, _ in observed.reconnecting] == list(
        range(1, RECONNECT_MAX_ATTEMPTS)
    )
    assert observed.stopped == [(0, GAVE_UP)]

    (message,) = set(observed.certificate_errors)
    lines = [text for _, text in panel_manager.tunnel_log.entries]
    assert lines.count(f"Certificate error: {message}") == RECONNECT_MAX_ATTEMPTS
    assert sum(line.startswith("Certificate error:") for line in lines) == (
        RECONNECT_MAX_ATTEMPTS
    )
    assert lines[-1] == f"Gave up reconnecting: {GAVE_UP}"
    assert not any("Server error" in line for line in lines)
    return message


class TestTunnelDirect:

    @pytest.mark.asyncio
    async def test_client_raises_the_dedicated_class_chained_from_the_ssl_error(
        self, relay: RelayServer
    ) -> None:
        client = TunnelClient(
            relay.url,
            MagicMock(access_token="t"),
            MagicMock(),
            route=ProxyRoute(None, "no proxy configured"),
        )

        with pytest.raises(TunnelCertificateError) as excinfo:
            await asyncio.wait_for(client.run(), SCENARIO_TIMEOUT)

        assert isinstance(excinfo.value.__cause__, ssl.SSLCertVerificationError)
        _assert_certificate_message(str(excinfo.value), "127.0.0.1", proxy_clause=None)
        assert relay.handshakes == []

    @pytest.mark.asyncio
    async def test_retried_unchanged_then_gives_up_with_the_cause(
        self, relay: RelayServer, panel_manager
    ) -> None:
        observed = await run_tunnel(
            NONE_PROXY, relay.url, connection_manager=panel_manager
        )

        message = _assert_retried_then_gave_up(observed, panel_manager)
        _assert_certificate_message(message, "127.0.0.1", proxy_clause=None)
        assert relay.accepts == RECONNECT_MAX_ATTEMPTS

    @pytest.mark.asyncio
    async def test_log_record_is_one_warning_per_attempt_without_a_traceback_dump(
        self, relay: RelayServer, panel_manager, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG, logger=ADDON_LOGGER)

        await run_tunnel(NONE_PROXY, relay.url, connection_manager=panel_manager)

        records = [
            r for r in caplog.records if "certificate verification failed" in r.getMessage()
        ]
        assert len(records) == RECONNECT_MAX_ATTEMPTS
        assert all(r.levelno == logging.WARNING for r in records)
        assert all("(ssl error: SSLCertVerificationError(" in r.getMessage() for r in records)
        assert all("CERTIFICATE_VERIFY_FAILED" in r.getMessage() for r in records)
        about_it = [
            r
            for r in caplog.records
            if "ertificate" in r.getMessage() and not r.name.endswith("connection_manager")
        ]
        assert len(about_it) == len(records)

    @pytest.mark.asyncio
    async def test_refresh_failure_record_carries_the_ssl_error_once(
        self, relay: RelayServer, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG, logger=ADDON_LOGGER)
        cause = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] raw detail")
        error = CertificateVerificationError("the clear message")
        error.__cause__ = cause
        auth = MagicMock()
        auth.refresh_token = AsyncMock(side_effect=error)

        await run_tunnel(NONE_PROXY, relay.url, expired=True, auth=auth)

        refresh_records = [
            r for r in caplog.records if r.getMessage().startswith("Token refresh failed:")
        ]
        assert len(refresh_records) == RECONNECT_MAX_ATTEMPTS
        assert all(
            "(ssl error: SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] raw detail'))"
            in r.getMessage()
            for r in refresh_records
        )


class TestTunnelThroughHttpProxy:

    @pytest.mark.asyncio
    async def test_message_names_the_proxy_and_the_target(
        self, relay: RelayServer, connect_proxy: ConnectProxy, panel_manager
    ) -> None:
        setting = parse_tunnel_proxy(f"http://127.0.0.1:{connect_proxy.port}")

        observed = await run_tunnel(
            setting, RELAY_WS_URL, connection_manager=panel_manager
        )

        message = _assert_retried_then_gave_up(observed, panel_manager)
        _assert_certificate_message(
            message,
            "relay.test",
            proxy_clause=f"via HTTP proxy 127.0.0.1:{connect_proxy.port} (tunnel_proxy setting)",
        )
        assert connect_proxy.connect_targets == [RELAY_TARGET] * RECONNECT_MAX_ATTEMPTS

    @pytest.mark.asyncio
    async def test_proxy_credentials_never_appear(
        self,
        relay: RelayServer,
        connect_proxy: ConnectProxy,
        panel_manager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG, logger=ADDON_LOGGER)
        setting = parse_tunnel_proxy(f"http://user:{SECRET}@127.0.0.1:{connect_proxy.port}")

        observed = await run_tunnel(
            setting, RELAY_WS_URL, connection_manager=panel_manager
        )

        _assert_retried_then_gave_up(observed, panel_manager)
        panel = [text for _, text in panel_manager.tunnel_log.entries]
        everything = [*observed.all_text(), *panel, caplog.text]
        assert not any(SECRET in text for text in everything)

    @pytest.mark.asyncio
    async def test_cause_chain_carries_no_proxy_credentials(
        self, relay: RelayServer, connect_proxy: ConnectProxy
    ) -> None:
        client = TunnelClient(
            RELAY_WS_URL,
            MagicMock(access_token="t"),
            MagicMock(),
            route=ProxyRoute(
                f"http://user:{SECRET}@127.0.0.1:{connect_proxy.port}", "tunnel_proxy setting"
            ),
        )

        with pytest.raises(TunnelCertificateError) as excinfo:
            await asyncio.wait_for(client.run(), SCENARIO_TIMEOUT)

        chain, exc = [], excinfo.value
        while exc is not None:
            chain.append(exc)
            exc = exc.__cause__
        assert [type(e) for e in chain] == [TunnelCertificateError, ssl.SSLCertVerificationError]
        assert not any(SECRET in str(e) for e in chain)


class TestTunnelThroughHttpsProxy:

    @pytest.mark.asyncio
    async def test_untrusted_proxy_certificate_is_a_certificate_error_too(
        self, relay: RelayServer, tls_connect_proxy: ConnectProxy, panel_manager
    ) -> None:
        setting = parse_tunnel_proxy(f"https://127.0.0.1:{tls_connect_proxy.port}")

        observed = await run_tunnel(
            setting, RELAY_WS_URL, connection_manager=panel_manager
        )

        message = _assert_retried_then_gave_up(observed, panel_manager)
        _assert_certificate_message(
            message,
            "relay.test",
            proxy_clause=f"via HTTPS proxy 127.0.0.1:{tls_connect_proxy.port} (tunnel_proxy setting)",
        )
        assert tls_connect_proxy.connect_targets == []
        assert tls_connect_proxy.accepts >= RECONNECT_MAX_ATTEMPTS


class TestOtherFailuresAreUnchanged:

    @pytest.mark.asyncio
    async def test_connection_refused_is_still_connection_failed(
        self, panel_manager
    ) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        observed = await run_tunnel(
            NONE_PROXY, f"wss://127.0.0.1:{port}", connection_manager=panel_manager
        )

        assert len(observed.errors) == RECONNECT_MAX_ATTEMPTS
        for code, message in observed.errors:
            assert code == "connection_failed"
            assert message.startswith(f"Failed to connect to wss://127.0.0.1:{port}: ")
        lines = [text for _, text in panel_manager.tunnel_log.entries]
        assert lines[0].startswith("Server error [connection_failed]: Failed to connect")
        assert lines[-1] == (
            f"Gave up reconnecting: Max reconnection attempts ({RECONNECT_MAX_ATTEMPTS}) exhausted"
        )
        assert not any("Certificate error" in line for line in lines)

    @pytest.mark.asyncio
    async def test_certificate_failure_then_other_failure_drops_the_note(
        self, monkeypatch: pytest.MonkeyPatch, relay: RelayServer
    ) -> None:
        calls = {"n": 0}
        real = TunnelClient.run

        async def run_with_late_failure(self):
            calls["n"] += 1
            if calls["n"] < RECONNECT_MAX_ATTEMPTS:
                return await real(self)
            raise TunnelConnectionError("Failed to connect: boom")

        monkeypatch.setattr(TunnelClient, "run", run_with_late_failure)

        observed = await run_tunnel(NONE_PROXY, relay.url)

        assert observed.stopped == [
            (0, f"Max reconnection attempts ({RECONNECT_MAX_ATTEMPTS}) exhausted")
        ]


class TestTokenRefreshCertificateFailure:

    @pytest.mark.asyncio
    async def test_silent_refresh_reports_the_clear_message(
        self, monkeypatch: pytest.MonkeyPatch, relay: RelayServer, panel_manager
    ) -> None:
        from anki_mcp_server.tunnel import client as client_module

        from .helpers import client_tls_context

        monkeypatch.setattr(client_module, "create_tls_context", client_tls_context)
        auth = MagicMock()
        auth.refresh_token = AsyncMock(
            side_effect=CertificateVerificationError("the clear message")
        )

        observed = await run_tunnel(
            NONE_PROXY, relay.url, expired=True, auth=auth, connection_manager=panel_manager
        )

        assert observed.errors == []
        assert panel_manager.tunnel_log.entries[0] == (
            "error", "Certificate error: Token refresh failed (will retry): the clear message"
        )
        assert len(observed.established) == 1

    @pytest.mark.asyncio
    async def test_refresh_line_differs_from_the_connect_failure_line(
        self, relay: RelayServer, panel_manager
    ) -> None:
        auth = MagicMock()
        auth.refresh_token = AsyncMock(
            side_effect=CertificateVerificationError("the clear message")
        )

        await run_tunnel(
            NONE_PROXY, relay.url, expired=True, auth=auth, connection_manager=panel_manager
        )

        lines = [text for kind, text in panel_manager.tunnel_log.entries if kind == "error"]
        refresh_lines = [l for l in lines if "Token refresh failed" in l]
        connect_lines = [
            l for l in lines if "while connecting to" in l and l not in refresh_lines
        ]
        assert len(refresh_lines) == len(connect_lines) == RECONNECT_MAX_ATTEMPTS
        assert not set(refresh_lines) & set(connect_lines)

    @pytest.mark.asyncio
    async def test_refresh_after_an_auth_close_code_keeps_retrying_and_gives_up_with_the_cause(
        self, monkeypatch: pytest.MonkeyPatch, panel_manager
    ) -> None:
        async def closed_with_auth_failure(self):
            return 4001, "token rejected"

        monkeypatch.setattr(TunnelClient, "run", closed_with_auth_failure)
        auth = MagicMock()
        auth.refresh_token = AsyncMock(
            side_effect=CertificateVerificationError("the clear message")
        )

        observed = await run_tunnel(
            NONE_PROXY, "wss://127.0.0.1:1", auth=auth, connection_manager=panel_manager
        )

        assert auth.refresh_token.await_count == RECONNECT_MAX_ATTEMPTS
        assert observed.errors == []
        assert observed.certificate_errors == [
            "Token refresh failed: the clear message"
        ] * RECONNECT_MAX_ATTEMPTS
        assert [attempt for attempt, _ in observed.reconnecting] == list(
            range(1, RECONNECT_MAX_ATTEMPTS)
        )
        assert observed.stopped == [(0, GAVE_UP)]
        lines = [text for _, text in panel_manager.tunnel_log.entries]
        assert lines.count("Certificate error: Token refresh failed: the clear message") == (
            RECONNECT_MAX_ATTEMPTS
        )
        assert lines[-1] == f"Gave up reconnecting: {GAVE_UP}"

    @pytest.mark.asyncio
    async def test_a_raising_callback_does_not_break_the_tunnel(
        self, relay: RelayServer
    ) -> None:
        manager = MagicMock()
        manager._on_tunnel_certificate_error.side_effect = RuntimeError("panel broke")

        observed = await run_tunnel(NONE_PROXY, relay.url, connection_manager=manager)

        assert len(observed.certificate_errors) == RECONNECT_MAX_ATTEMPTS
        assert observed.stopped == [(0, GAVE_UP)]


def _auth(setting, url: str) -> DeviceFlowAuth:
    return DeviceFlowAuth(url, "client", proxy=setting)


class TestLoginHttp:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("call", ["request_device_code", "refresh_token"])
    async def test_direct(self, auth_server: AuthServer, call: str) -> None:
        auth = _auth(NONE_PROXY, f"wss://127.0.0.1:{auth_server.port}")
        args = ("refresh",) if call == "refresh_token" else ()

        with pytest.raises(CertificateVerificationError) as excinfo:
            await asyncio.wait_for(getattr(auth, call)(*args), SCENARIO_TIMEOUT)

        error = excinfo.value
        assert isinstance(error, AuthError)
        assert error.error_code is None
        assert isinstance(error.__cause__, ssl.SSLCertVerificationError)
        _assert_certificate_message(str(error), "127.0.0.1", proxy_clause=None)
        assert "HTTP request failed" not in str(error)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("call", ["request_device_code", "refresh_token"])
    async def test_through_http_proxy(
        self, auth_server: AuthServer, connect_proxy: ConnectProxy, call: str
    ) -> None:
        connect_proxy.upstreams[RELAY_TARGET] = auth_server.port
        setting = parse_tunnel_proxy(f"http://user:{SECRET}@127.0.0.1:{connect_proxy.port}")
        args = ("refresh",) if call == "refresh_token" else ()

        with pytest.raises(CertificateVerificationError) as excinfo:
            await asyncio.wait_for(
                getattr(_auth(setting, RELAY_WS_URL), call)(*args), SCENARIO_TIMEOUT
            )

        message = str(excinfo.value)
        _assert_certificate_message(
            message,
            "relay.test",
            proxy_clause=f"via HTTP proxy 127.0.0.1:{connect_proxy.port} (tunnel_proxy setting)",
        )
        assert SECRET not in message
        assert connect_proxy.connect_targets == [RELAY_TARGET]

    @pytest.mark.asyncio
    async def test_through_https_proxy_with_untrusted_certificate(
        self, auth_server: AuthServer, tls_connect_proxy: ConnectProxy
    ) -> None:
        tls_connect_proxy.upstreams[RELAY_TARGET] = auth_server.port
        setting = parse_tunnel_proxy(f"https://127.0.0.1:{tls_connect_proxy.port}")

        with pytest.raises(CertificateVerificationError) as excinfo:
            await asyncio.wait_for(
                _auth(setting, RELAY_WS_URL).request_device_code(), SCENARIO_TIMEOUT
            )

        _assert_certificate_message(
            str(excinfo.value),
            "relay.test",
            proxy_clause=f"via HTTPS proxy 127.0.0.1:{tls_connect_proxy.port} (tunnel_proxy setting)",
        )
        assert tls_connect_proxy.connect_targets == []

    @pytest.mark.asyncio
    async def test_login_failure_is_logged_as_an_auth_error_with_its_cause(
        self, auth_server: AuthServer, caplog: pytest.LogCaptureFixture
    ) -> None:
        auth = _auth(NONE_PROXY, f"wss://127.0.0.1:{auth_server.port}")
        with pytest.raises(CertificateVerificationError) as excinfo:
            await asyncio.wait_for(auth.request_device_code(), SCENARIO_TIMEOUT)

        with caplog.at_level(logging.DEBUG, logger="anki_mcp_server.tunnel.login_failure"):
            log_login_failure("request a device code", excinfo.value)

        (record,) = [r for r in caplog.records if r.name.endswith("login_failure")]
        assert record.levelno == logging.WARNING
        assert record.exc_info is not None
        assert isinstance(record.exc_info[1].__cause__, ssl.SSLCertVerificationError)


class TestLoginHttpRawOsError:
    """The ``except OSError`` branch of ``_post``.

    The vendored httpx maps every socket and ssl failure to an ``httpx`` error,
    so no real transport reaches this branch; it is a safety net, exercised here
    by making ``post`` leak the ssl error unwrapped, as OSError, and chained.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("wrapped", [False, True], ids=["raw-ssl-error", "chained-os-error"])
    async def test_certificate_failure_is_still_reported_clearly(
        self, monkeypatch: pytest.MonkeyPatch, wrapped: bool
    ) -> None:
        cert = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] bad chain")

        async def leaking_post(self, *args, **kwargs):
            if not wrapped:
                raise cert
            raise OSError("wrapped") from cert

        monkeypatch.setattr(httpx.AsyncClient, "post", leaking_post)

        with pytest.raises(CertificateVerificationError) as excinfo:
            await _auth(NONE_PROXY, "wss://tunnel.example").request_device_code()

        assert excinfo.value.__cause__ is cert
        _assert_certificate_message(str(excinfo.value), "tunnel.example", proxy_clause=None)

    @pytest.mark.asyncio
    async def test_a_non_certificate_os_error_keeps_its_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def leaking_post(self, *args, **kwargs):
            raise OSError("boom")

        monkeypatch.setattr(httpx.AsyncClient, "post", leaking_post)

        with pytest.raises(AuthError) as excinfo:
            await _auth(NONE_PROXY, "wss://tunnel.example").request_device_code()

        assert not isinstance(excinfo.value, CertificateVerificationError)
        assert str(excinfo.value) == "HTTP request failed: OSError: boom"
