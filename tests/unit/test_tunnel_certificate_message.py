"""The shared certificate-failure helper in ``tunnel/tls.py`` and the local error code.

Detection is by exception class along the ``__cause__`` / ``__context__``
chain, never by message text; the text is plain language built from the host,
the OS/OpenSSL reason and (when proxied) the masked route. The panel side of
the channel is covered in ``tunnel_proxy/test_certificate_errors_e2e.py``.
"""
from __future__ import annotations

# The package __init__ must run before the MCP SDK is imported.
import anki_mcp_server  # noqa: F401

import ssl
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_mcp_server import connection_manager as cm_module
from anki_mcp_server.config import Config
from anki_mcp_server.tunnel.client import TunnelClient
from anki_mcp_server.tunnel.proxy import ProxyRoute
from anki_mcp_server.tunnel.tls import certificate_failure_message, find_certificate_error

from .fakes import FakeTunnelLog

DIRECT = ProxyRoute(None, "no proxy configured")

MACOS_TEXT = "“host.example” certificate is not trusted"
WINDOWS_TEXT = (
    "A certificate chain processed, but terminated in a root certificate "
    "which is not trusted by the trust provider."
)
OPENSSL_VERIFY_MESSAGE = "unable to get local issuer certificate"
OPENSSL_TEXT = (
    f"[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: {OPENSSL_VERIFY_MESSAGE} (_ssl.c:1006)"
)


def _cert_error(text: str, verify_message: str | None = None) -> ssl.SSLCertVerificationError:
    exc = ssl.SSLCertVerificationError(1, text)
    exc.verify_message = verify_message  # type: ignore[assignment]
    return exc


def _message(exc: ssl.SSLCertVerificationError, proxy_route: str | None = None) -> str:
    return certificate_failure_message(exc, host="host.example", proxy_route=proxy_route)


class TestFindCertificateError:

    def test_finds_it_directly(self) -> None:
        exc = _cert_error(MACOS_TEXT)
        assert find_certificate_error(exc) is exc

    def test_finds_it_several_levels_deep_via_cause_and_context(self) -> None:
        inner = _cert_error(OPENSSL_TEXT, OPENSSL_VERIFY_MESSAGE)
        middle = OSError("connect failed")
        middle.__cause__ = inner
        outer = RuntimeError("wrapper")
        outer.__context__ = middle
        top = ConnectionError("top")
        top.__cause__ = outer

        assert find_certificate_error(top) is inner

    def test_certificate_error_is_found_by_class_whatever_the_text(self) -> None:
        exc = _cert_error("anything at all")
        wrapper = Exception("no ssl words here")
        wrapper.__cause__ = exc
        assert find_certificate_error(wrapper) is exc

    def test_other_ssl_errors_are_not_certificate_failures(self) -> None:
        handshake = ssl.SSLError(1, "[SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] handshake failure")
        wrapper = OSError("wrapped")
        wrapper.__cause__ = handshake
        assert find_certificate_error(wrapper) is None

    def test_message_text_alone_never_classifies(self) -> None:
        assert find_certificate_error(OSError(OPENSSL_TEXT)) is None

    def test_cyclic_chain_terminates(self) -> None:
        a, b = Exception("a"), Exception("b")
        a.__cause__ = b
        b.__context__ = a
        assert find_certificate_error(a) is None


class TestCertificateFailureMessage:

    @pytest.mark.parametrize(
        ("text", "verify_message", "reason"),
        [
            (MACOS_TEXT, None, MACOS_TEXT),
            (WINDOWS_TEXT, None, WINDOWS_TEXT[:-1]),
            (OPENSSL_TEXT, OPENSSL_VERIFY_MESSAGE, OPENSSL_VERIFY_MESSAGE),
            (OPENSSL_TEXT, None, OPENSSL_VERIFY_MESSAGE),
        ],
        ids=["macos", "windows", "openssl-with-verify-message", "openssl-text-only"],
    )
    def test_reason_is_clean_text(self, text, verify_message, reason) -> None:
        message = _message(_cert_error(text, verify_message))

        assert f"({reason})." in message
        assert "_ssl.c" not in message
        assert "CERTIFICATE_VERIFY_FAILED" not in message

    def test_verify_message_wins_over_the_exception_text(self) -> None:
        message = _message(_cert_error("generic text", "self-signed certificate in chain"))

        assert "(self-signed certificate in chain)" in message
        assert "generic text" not in message

    def test_tuple_shaped_args_never_reach_the_text(self) -> None:
        exc = ssl.SSLCertVerificationError((MACOS_TEXT,))

        message = _message(exc)

        assert f"({MACOS_TEXT})." in message
        assert "('" not in message and ",)" not in message

    def test_empty_exception_still_gives_a_reason(self) -> None:
        message = _message(ssl.SSLCertVerificationError())

        assert "(SSLCertVerificationError)." in message

    def test_direct_message_content(self) -> None:
        message = _message(_cert_error(MACOS_TEXT))

        assert "security certificate could not be verified" in message
        assert "host.example" in message
        assert "proxy, antivirus or company network" in message
        assert "root certificate in your operating system" in message
        assert "SSL_CERT_FILE" in message
        assert "restart Anki" in message
        assert "routed" not in message

    def test_hint_covers_inspection_and_a_wrong_clock_but_names_no_hop(self) -> None:
        message = _message(_cert_error(MACOS_TEXT))

        assert message.startswith("A security certificate could not be verified")
        assert "date and time are wrong" in message
        assert "If it is inspection, trust that product's root certificate" in message
        assert "server's" not in message

    def test_proxied_message_names_the_masked_proxy(self) -> None:
        route = ProxyRoute("http://user:s3cret@10.0.0.2:3128", "tunnel_proxy setting")

        message = _message(_cert_error(MACOS_TEXT), route.proxied_display())

        assert "routed via HTTP proxy 10.0.0.2:3128 (tunnel_proxy setting)" in message
        assert "s3cret" not in message and "user" not in message

    def test_https_proxy_is_named_as_such(self) -> None:
        route = ProxyRoute("https://10.0.0.2:3128", "system HTTPS proxy")

        assert "via HTTPS proxy 10.0.0.2:3128" in _message(
            _cert_error(MACOS_TEXT), route.proxied_display()
        )

    def test_a_direct_route_has_no_proxy_display(self) -> None:
        assert DIRECT.proxied_display() is None

    def test_missing_host_falls_back(self) -> None:
        message = certificate_failure_message(
            _cert_error(MACOS_TEXT), host=None, proxy_route=None
        )

        assert "connecting to the server (" in message

    def test_tls_module_imports_no_other_add_on_module(self) -> None:
        import anki_mcp_server.tunnel.tls as tls_module

        source = Path(tls_module.__file__).read_text()

        assert "from ." not in source and "import anki_mcp_server" not in source


@pytest.fixture()
def manager(monkeypatch: pytest.MonkeyPatch) -> cm_module.ConnectionManager:
    monkeypatch.setattr(cm_module, "TunnelLog", FakeTunnelLog)
    monkeypatch.setattr(cm_module, "CredentialsManager", MagicMock)
    monkeypatch.setattr(cm_module, "DeviceFlowAuth", MagicMock)
    return cm_module.ConnectionManager(Config())


class TestConnectionManagerRendering:

    def test_certificate_callback_is_labelled_certificate_error(self, manager, caplog) -> None:
        with caplog.at_level("DEBUG", logger="anki_mcp_server.connection_manager"):
            manager._on_tunnel_certificate_error("the full message")

        assert manager.tunnel_log.entries == [("error", "Certificate error: the full message")]
        assert caplog.records == []

    def test_server_sent_look_alike_code_stays_a_server_error(self, manager) -> None:
        manager._on_tunnel_error("certificate_error", "spoofed text")

        assert manager.tunnel_log.entries == [
            ("error", "Server error [certificate_error]: spoofed text")
        ]

    def test_a_relay_error_message_reaches_the_manager_as_a_plain_string(self, manager) -> None:
        client = TunnelClient(
            "wss://relay.example",
            MagicMock(),
            MagicMock(),
            on_error=manager._on_tunnel_error,
            route=DIRECT,
        )

        client._handle_error(
            {"type": "error", "code": "certificate_error", "message": "relay says so"}
        )

        assert manager.tunnel_log.entries == [
            ("error", "Server error [certificate_error]: relay says so")
        ]

    @pytest.mark.parametrize(
        "code", ["connection_failed", "token_refresh_failed", "rate_limited"]
    )
    def test_other_codes_are_unchanged(self, manager, code) -> None:
        manager._on_tunnel_error(code, "text")

        assert manager.tunnel_log.entries == [("error", f"Server error [{code}]: text")]

    def test_give_up_line_carries_the_cause(self, manager) -> None:
        manager._on_tunnel_stopped(
            0,
            "Max reconnection attempts (10) exhausted "
            "(last error: certificate could not be verified — see above)",
        )

        assert manager.tunnel_log.entries == [
            (
                "error",
                "Gave up reconnecting: Max reconnection attempts (10) exhausted "
                "(last error: certificate could not be verified — see above)",
            )
        ]
