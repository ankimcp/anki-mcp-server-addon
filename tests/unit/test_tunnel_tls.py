"""Unit tests for the tunnel's TLS context factory and its two call sites.

``tunnel/tls.py`` builds a fresh OS-trust-store context for both the
login/refresh HTTP calls (``auth.py``) and the tunnel WebSocket
(``client.py``). The bug it fixes: httpx's default ``verify=True`` loads a CA
*file* (``SSL_CERT_FILE`` or certifi's ``cacert.pem``) inside the
``AsyncClient`` constructor, and an unreadable file there raised a bare
``OSError`` that escaped ``_post`` (``Login failed: Unexpected error:
[Errno 13] Permission denied``).

No network, no Anki: the WebSocket ``connect`` is a fake, and every
``httpx.AsyncClient`` built by ``_post`` either fails before a request or has
its transport swapped for an ``httpx.MockTransport`` after the real
constructor has run.
"""

from __future__ import annotations

# The package __init__ must run before the MCP SDK is imported.
import anki_mcp_server  # noqa: F401

import asyncio
import json
import logging
import re
import ssl
import urllib.request
from pathlib import Path
from typing import Callable
from unittest.mock import AsyncMock, MagicMock

import certifi
import httpx
import pytest

from anki_mcp_server.tunnel import auth as auth_module
from anki_mcp_server.tunnel import client as client_module
from anki_mcp_server.tunnel import tls as tls_module
from anki_mcp_server.tunnel.auth import AuthError, DeviceCodeResponse, DeviceFlowAuth
from anki_mcp_server.tunnel.client import (
    TunnelClient,
    TunnelConnectionError,
    _is_secure_ws_url,
)
from anki_mcp_server.tunnel.proxy import AUTO_PROXY, ProxyRoute
from anki_mcp_server.tunnel.tls import TLSContextError, create_tls_context

_TLS_LOGGER = "anki_mcp_server.tunnel.tls"
_SERVER_URL = "wss://tunnel.example"
_DEVICE_ENDPOINT = "https://tunnel.example/auth/device"
# Read by ssl.create_default_context() (SSLKEYLOGFILE) and by
# tls.create_tls_context() / httpx's default verify (SSL_CERT_FILE/DIR).
_TLS_ENV_VARS = ("SSLKEYLOGFILE", "SSL_CERT_FILE", "SSL_CERT_DIR")
_DEVICE_CODE_BODY = {
    "device_code": "dev-123",
    "user_code": "ABCD-EFGH",
    "verification_uri": "https://tunnel.example/verify",
    "expires_in": 600,
    "interval": 5,
}


@pytest.fixture(autouse=True)
def _clean_tls_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove ambient env vars these tests are sensitive to.

    A developer shell's ``SSLKEYLOGFILE`` pointing at a missing directory makes
    every ``ssl.create_default_context()`` fail. Tests that need one of these
    set it again with ``monkeypatch.setenv``. Ambient proxy settings are
    neutralised suite-wide by ``_no_ambient_proxy`` in ``conftest.py``.
    """
    for name in _TLS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _reset_warned_cert_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test an empty warn-once set so results don't depend on order."""
    monkeypatch.setattr(tls_module, "_warned_cert_files", set())


def _tls_records(
    caplog: pytest.LogCaptureFixture, level: int
) -> list[logging.LogRecord]:
    return [
        r for r in caplog.records
        if r.name == _TLS_LOGGER and r.levelno == level
    ]


def _tls_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return _tls_records(caplog, logging.WARNING)


def _junk_cert_file(tmp_path: Path, name: str = "junk.pem") -> Path:
    path = tmp_path / name
    path.write_text("this is not a PEM certificate bundle\n")
    return path


# ===========================================================================
# create_tls_context()
# ===========================================================================

class TestVerificationAlwaysOn:
    @pytest.mark.parametrize("cert_file", ["unset", "valid", "unloadable"])
    def test_context_requires_verification_and_hostname_check(
        self,
        cert_file: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        if cert_file == "valid":
            monkeypatch.setenv("SSL_CERT_FILE", certifi.where())
        elif cert_file == "unloadable":
            monkeypatch.setenv("SSL_CERT_FILE", str(_junk_cert_file(tmp_path)))

        ctx = create_tls_context()

        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True


class TestSslCertFileUnusable:
    """An unloadable ``SSL_CERT_FILE`` is logged and skipped, never fatal."""

    @pytest.mark.parametrize("kind", ["directory", "missing", "junk"])
    def test_unusable_cert_file_warns_and_returns_context(
        self,
        kind: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        if kind == "directory":
            cert_path = tmp_path
        elif kind == "missing":
            cert_path = tmp_path / "does-not-exist.pem"
        else:
            cert_path = _junk_cert_file(tmp_path)
        monkeypatch.setenv("SSL_CERT_FILE", str(cert_path))
        caplog.set_level(logging.WARNING, logger=_TLS_LOGGER)

        ctx = create_tls_context()

        assert isinstance(ctx, ssl.SSLContext)
        warnings = _tls_warnings(caplog)
        assert len(warnings) == 1, [r.getMessage() for r in warnings]
        message = warnings[0].getMessage()
        assert f"Could not load SSL_CERT_FILE={str(cert_path)!r}" in message
        assert "continuing without it" in message
        assert "If TLS verification fails, fix or unset SSL_CERT_FILE." in message
        assert "system certificate store only" not in message

    def test_warning_names_exception_type_and_text(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        cert_path = tmp_path / "does-not-exist.pem"
        monkeypatch.setenv("SSL_CERT_FILE", str(cert_path))
        caplog.set_level(logging.WARNING, logger=_TLS_LOGGER)

        create_tls_context()

        (warning,) = _tls_warnings(caplog)
        assert "(FileNotFoundError: " in warning.getMessage()
        assert "No such file or directory" in warning.getMessage()


class TestWarnOncePerPath:
    def test_same_path_warns_once_then_logs_debug(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("SSL_CERT_FILE", str(_junk_cert_file(tmp_path)))
        caplog.set_level(logging.DEBUG, logger=_TLS_LOGGER)

        create_tls_context()
        create_tls_context()

        assert len(_tls_warnings(caplog)) == 1
        debug = [
            r for r in _tls_records(caplog, logging.DEBUG)
            if "SSL_CERT_FILE" in r.getMessage()
        ]
        assert len(debug) == 1

    def test_different_path_warns_again(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        first = _junk_cert_file(tmp_path, "first.pem")
        second = _junk_cert_file(tmp_path, "second.pem")
        caplog.set_level(logging.WARNING, logger=_TLS_LOGGER)

        monkeypatch.setenv("SSL_CERT_FILE", str(first))
        create_tls_context()
        create_tls_context()
        monkeypatch.setenv("SSL_CERT_FILE", str(second))
        create_tls_context()

        warnings = _tls_warnings(caplog)
        assert len(warnings) == 2, [r.getMessage() for r in warnings]
        assert repr(str(first)) in warnings[0].getMessage()
        assert repr(str(second)) in warnings[1].getMessage()


class TestSslCertFileAbsent:
    @pytest.mark.parametrize("value", [None, ""])
    def test_no_cert_file_returns_context_without_warning(
        self,
        value: str | None,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        if value is not None:
            monkeypatch.setenv("SSL_CERT_FILE", value)
        caplog.set_level(logging.WARNING, logger=_TLS_LOGGER)

        ctx = create_tls_context()

        assert isinstance(ctx, ssl.SSLContext)
        assert _tls_warnings(caplog) == []


class TestSslCertFileValid:
    def test_valid_bundle_is_honoured(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        bundle = certifi.where()
        first_pem = re.search(
            r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----",
            Path(bundle).read_text(encoding="ascii"),
            re.DOTALL,
        )
        assert first_pem is not None, f"no PEM certificate found in {bundle}"
        first_der = ssl.PEM_cert_to_DER_cert(first_pem.group(0))
        monkeypatch.setenv("SSL_CERT_FILE", bundle)
        caplog.set_level(logging.WARNING, logger=_TLS_LOGGER)

        ctx = create_tls_context()

        assert isinstance(ctx, ssl.SSLContext)
        assert _tls_warnings(caplog) == []
        try:
            ca_count = ctx.cert_store_stats()["x509_ca"]
            loaded = ctx.get_ca_certs(binary_form=True)
        except NotImplementedError:
            pytest.skip(
                f"{type(ctx).__name__} doesn't expose its CA store; only the "
                "no-warning / context-returned assertions could run"
            )
        assert ca_count > 0
        assert first_der in loaded


class TestContextCreationFailure:
    def test_create_default_context_failure_raises_tls_context_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = PermissionError(13, "Permission denied")

        def _raise(*args: object, **kwargs: object) -> ssl.SSLContext:
            raise original

        monkeypatch.setattr(tls_module.ssl, "create_default_context", _raise)

        with pytest.raises(TLSContextError) as excinfo:
            create_tls_context()

        assert str(excinfo.value) == (
            "Could not create a TLS context: PermissionError: "
            "[Errno 13] Permission denied"
        )
        assert excinfo.value.__cause__ is original


class TestFreshContextPerCall:
    def test_each_call_returns_a_new_context(self) -> None:
        assert create_tls_context() is not create_tls_context()


# ===========================================================================
# Regression: httpx's default CA-file load vs. an explicit context
# ===========================================================================

class TestHttpxUnreadableCaFileRegression:
    @pytest.mark.asyncio
    async def test_default_verify_breaks_explicit_context_does_not(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(certifi, "where", lambda: str(tmp_path))

        # Proves the scenario bites: if this stops raising, the test fails
        # here rather than passing vacuously below.
        with pytest.raises(OSError):
            httpx.AsyncClient()

        client = httpx.AsyncClient(verify=create_tls_context())
        await client.aclose()


# ===========================================================================
# auth.py _post (via DeviceFlowAuth.request_device_code)
# ===========================================================================

def _patch_client_with_handler(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[dict[str, object]]:
    """Run the REAL ``AsyncClient`` constructor, then swap in a mock transport.

    Passing ``transport=`` to the constructor would make httpx skip building
    its default transport — and with it the ``verify`` / certifi CA-file load
    this suite guards. Swapping ``_transport`` afterwards keeps that load on
    the real path while still never touching the network; ``_mounts`` is
    cleared too, so a proxy picked up from the OS settings can't route around
    the mock. These are private httpx attributes, so an httpx upgrade could
    rename them; the type assertions below make that fail loudly instead of
    leaving the real transport in place to send a real request. Returns the
    list of constructor kwargs, one entry per client.
    """
    real_async_client = httpx.AsyncClient
    constructed: list[dict[str, object]] = []

    def _factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        client = real_async_client(*args, **kwargs)
        assert isinstance(
            getattr(client, "_transport", None), httpx.AsyncHTTPTransport
        ), "httpx.AsyncClient._transport is gone or changed type; update this helper"
        assert isinstance(getattr(client, "_mounts", None), dict), (
            "httpx.AsyncClient._mounts is gone or changed type; update this helper"
        )
        client._mounts = {}
        client._transport = httpx.MockTransport(handler)
        constructed.append(kwargs)
        return client

    monkeypatch.setattr(auth_module.httpx, "AsyncClient", _factory)
    return constructed


def _forbid_client_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    def _factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        pytest.fail("httpx.AsyncClient must not be constructed here")

    monkeypatch.setattr(auth_module.httpx, "AsyncClient", _factory)


def _make_auth() -> DeviceFlowAuth:
    return DeviceFlowAuth(
        server_url=_SERVER_URL, client_id="test-client", proxy=AUTO_PROXY
    )


class TestPostErrorMapping:
    @pytest.mark.asyncio
    async def test_unreadable_certifi_bundle_does_not_break_login(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(certifi, "where", lambda: str(tmp_path))
        constructed = _patch_client_with_handler(
            monkeypatch,
            lambda request: httpx.Response(200, json=_DEVICE_CODE_BODY),
        )

        result = await _make_auth().request_device_code()

        assert isinstance(result, DeviceCodeResponse)
        assert result.user_code == "ABCD-EFGH"
        assert len(constructed) == 1
        assert isinstance(constructed[0].get("verify"), ssl.SSLContext)

    @pytest.mark.asyncio
    async def test_factory_context_is_passed_as_verify(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sentinel = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        monkeypatch.setattr(auth_module, "create_tls_context", lambda: sentinel)
        constructed = _patch_client_with_handler(
            monkeypatch,
            lambda request: httpx.Response(200, json=_DEVICE_CODE_BODY),
        )

        await _make_auth().request_device_code()

        assert len(constructed) == 1
        assert constructed[0].get("verify") is sentinel

    @pytest.mark.asyncio
    async def test_tls_context_error_becomes_auth_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = TLSContextError(
            "Could not create a TLS context: FileNotFoundError: "
            "[Errno 2] No such file or directory"
        )

        def _raise() -> ssl.SSLContext:
            raise original

        monkeypatch.setattr(auth_module, "create_tls_context", _raise)
        _forbid_client_construction(monkeypatch)

        with pytest.raises(AuthError) as excinfo:
            await _make_auth().request_device_code()

        assert str(excinfo.value) == (
            "Could not create a TLS context: FileNotFoundError: "
            "[Errno 2] No such file or directory"
        )
        assert excinfo.value.__cause__ is original

    @pytest.mark.asyncio
    async def test_os_error_on_client_construction_becomes_auth_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(*args: object, **kwargs: object) -> httpx.AsyncClient:
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(auth_module.httpx, "AsyncClient", _raise)

        with pytest.raises(AuthError) as excinfo:
            await _make_auth().request_device_code()

        assert not isinstance(excinfo.value, PermissionError)
        assert "PermissionError" in str(excinfo.value)
        assert isinstance(excinfo.value.__cause__, PermissionError)

    @pytest.mark.asyncio
    async def test_proxy_address_rejected_by_httpx_becomes_proxy_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # With http(s)-only routes, InvalidURL is the one construction failure
        # left; ValueError (scheme) and ImportError (socksio) need a SOCKS proxy.
        def _raise(*args: object, **kwargs: object) -> httpx.AsyncClient:
            raise httpx.InvalidURL("Invalid IPv6 address: '[v1.x]'")

        monkeypatch.setattr(auth_module.httpx, "AsyncClient", _raise)
        monkeypatch.setattr(
            urllib.request, "getproxies", lambda: {"https": "http://proxy.example:3128"}
        )

        with pytest.raises(AuthError) as excinfo:
            await _make_auth().request_device_code()

        assert isinstance(excinfo.value, auth_module.ProxyUnusableError)
        assert excinfo.value.error_code == "proxy_unsupported"
        assert "tunnel_proxy" in str(excinfo.value)
        assert "Invalid auth URL" not in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_unsupported_env_proxy_scheme_becomes_auth_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The route decision rejects the scheme before any client is built, so
        # nothing reaches the network.
        monkeypatch.setattr(
            urllib.request, "getproxies", lambda: {"https": "ftp://proxy.example:21"}
        )
        _forbid_client_construction(monkeypatch)

        with pytest.raises(AuthError) as excinfo:
            await _make_auth().request_device_code()

        assert excinfo.value.error_code == "proxy_unsupported"
        assert "tunnel_proxy" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_value_error_from_request_propagates_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = ValueError("raised while sending")

        def _handler(request: httpx.Request) -> httpx.Response:
            raise original

        _patch_client_with_handler(monkeypatch, _handler)

        # A ValueError from the request itself is not an httpx.HTTPError or
        # OSError, so _post lets it propagate unchanged.
        with pytest.raises(ValueError) as excinfo:
            await _make_auth().request_device_code()

        assert excinfo.value is original
        assert not isinstance(excinfo.value, AuthError)

    @pytest.mark.asyncio
    async def test_connect_error_keeps_existing_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("x")

        _patch_client_with_handler(monkeypatch, _handler)

        with pytest.raises(AuthError) as excinfo:
            await _make_auth().request_device_code()

        assert str(excinfo.value) == "HTTP request failed: x"

    @pytest.mark.asyncio
    async def test_timeout_keeps_existing_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow")

        _patch_client_with_handler(monkeypatch, _handler)

        with pytest.raises(AuthError) as excinfo:
            await _make_auth().request_device_code()

        assert str(excinfo.value) == f"Request timed out: {_DEVICE_ENDPOINT}"


# ===========================================================================
# client.py: ssl= on the WebSocket connect
# ===========================================================================

class _FakeCleanCloseWebSocket:
    """Completes the handshake, then closes cleanly with zero messages."""

    def __init__(self) -> None:
        self._handshake = json.dumps(
            {"type": "tunnel_established", "url": "https://tunnel.example/abc"}
        )
        self._handshake_consumed = False
        self.send = AsyncMock()
        self.close = AsyncMock()

    async def recv(self) -> str:
        if not self._handshake_consumed:
            self._handshake_consumed = True
            return self._handshake
        raise StopAsyncIteration

    def __aiter__(self) -> "_FakeCleanCloseWebSocket":
        return self

    async def __anext__(self) -> str:
        raise StopAsyncIteration


def _make_client(server_url: str) -> TunnelClient:
    return TunnelClient(
        server_url=server_url,
        credentials=MagicMock(),
        transport=MagicMock(),
        route=ProxyRoute(None, "test"),
    )


class TestIsSecureWsUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("wss://tunnel.ankimcp.ai", True),
            ("WSS://tunnel.ankimcp.ai", True),
            ("ws://localhost:3004", False),
            ("WS://localhost:3004", False),
            ("https://wss.example", False),
        ],
    )
    def test_scheme_detection(self, url: str, expected: bool) -> None:
        assert _is_secure_ws_url(url) is expected


class TestConnectSslKwarg:
    @pytest.mark.asyncio
    async def test_wss_passes_ssl_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_connect = AsyncMock(return_value=_FakeCleanCloseWebSocket())
        monkeypatch.setattr(client_module, "connect", fake_connect)

        await asyncio.wait_for(_make_client("wss://tunnel.example").run(), timeout=2)

        fake_connect.assert_awaited_once()
        assert isinstance(fake_connect.await_args.kwargs.get("ssl"), ssl.SSLContext)

    @pytest.mark.asyncio
    async def test_wss_passes_factory_context_as_ssl(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sentinel = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        monkeypatch.setattr(client_module, "create_tls_context", lambda: sentinel)
        fake_connect = AsyncMock(return_value=_FakeCleanCloseWebSocket())
        monkeypatch.setattr(client_module, "connect", fake_connect)

        await asyncio.wait_for(_make_client("wss://tunnel.example").run(), timeout=2)

        fake_connect.assert_awaited_once()
        assert fake_connect.await_args.kwargs.get("ssl") is sentinel

    @pytest.mark.asyncio
    async def test_ws_passes_no_ssl_kwarg(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_connect = AsyncMock(return_value=_FakeCleanCloseWebSocket())
        monkeypatch.setattr(client_module, "connect", fake_connect)

        await asyncio.wait_for(_make_client("ws://localhost:3004").run(), timeout=2)

        fake_connect.assert_awaited_once()
        assert "ssl" not in fake_connect.await_args.kwargs

    @pytest.mark.asyncio
    async def test_tls_context_error_surfaces_as_connection_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_connect = AsyncMock(return_value=_FakeCleanCloseWebSocket())
        monkeypatch.setattr(client_module, "connect", fake_connect)
        original = TLSContextError("no trust store")

        def _raise() -> ssl.SSLContext:
            raise original

        monkeypatch.setattr(client_module, "create_tls_context", _raise)

        with pytest.raises(TunnelConnectionError) as excinfo:
            await asyncio.wait_for(
                _make_client("wss://tunnel.example").run(), timeout=2
            )

        assert not isinstance(excinfo.value, TLSContextError)
        assert excinfo.value.__cause__ is original
        fake_connect.assert_not_awaited()
