"""Unit tests for the transport-security policy builder.

These tests exercise the pure ``build_transport_security`` function without a
running Anki. They lock in the secure defaults (DNS-rebinding protection ON,
loopback-only allowlist) and verify operator-configured extras are merged in
while the loopback defaults are preserved.

A drift-regression guard asserts our defaults still match the MCP SDK's own
loopback auto-default (vendor/shared/mcp/server/fastmcp/server.py:181-182).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# ``transport_security_config`` imports ``TransportSecuritySettings`` from the
# vendored MCP SDK (``mcp.server.transport_security``). Importing
# ``anki_mcp_server`` first puts ``vendor/shared`` on ``sys.path`` so that the
# ``mcp.*`` package is importable -- mirrors test_in_memory_transport.py.
# ---------------------------------------------------------------------------
import anki_mcp_server  # noqa: F401 -- triggers vendor path setup

from anki_mcp_server.config import Config
from anki_mcp_server.transport_security_config import (
    DEFAULT_ALLOWED_HOSTS,
    DEFAULT_ALLOWED_ORIGINS,
    build_transport_security,
    validate_http_allowlist,
)


class TestBuildTransportSecurity:
    """Tests for ``build_transport_security`` policy."""

    def test_protection_enabled_by_default(self) -> None:
        """Default config -> DNS-rebinding protection is ON."""
        settings = build_transport_security(Config())
        assert settings.enable_dns_rebinding_protection is True

    def test_default_allowed_hosts(self) -> None:
        """Default config -> allowed_hosts equals the loopback default list."""
        settings = build_transport_security(Config())
        assert settings.allowed_hosts == DEFAULT_ALLOWED_HOSTS

    def test_default_allowed_origins(self) -> None:
        """Default config -> allowed_origins equals the loopback default list."""
        settings = build_transport_security(Config())
        assert settings.allowed_origins == DEFAULT_ALLOWED_ORIGINS

    def test_all_default_origins_have_http_scheme(self) -> None:
        """Guards the scheme-format bug: origins are full origins WITH scheme."""
        settings = build_transport_security(Config())
        assert all(o.startswith("http://") for o in settings.allowed_origins)

    def test_defaults_match_sdk_auto_default(self) -> None:
        """Drift guard: our defaults match the SDK's literal loopback auto-default.

        Mirrors vendor/shared/mcp/server/fastmcp/server.py lines 181-182. If the
        SDK changes its auto-default, this test should be revisited deliberately.
        """
        assert DEFAULT_ALLOWED_HOSTS == ["127.0.0.1:*", "localhost:*", "[::1]:*"]
        assert DEFAULT_ALLOWED_ORIGINS == [
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
        ]

    def test_extra_host_merged_with_defaults(self) -> None:
        """Configured extra host is present AND loopback defaults remain."""
        settings = build_transport_security(
            Config(http_allowed_hosts=["myapp.ngrok.io"])
        )
        assert "myapp.ngrok.io" in settings.allowed_hosts
        for default_host in DEFAULT_ALLOWED_HOSTS:
            assert default_host in settings.allowed_hosts

    def test_extra_origin_merged_with_defaults(self) -> None:
        """Configured extra origin is present AND loopback origin defaults remain."""
        settings = build_transport_security(
            Config(http_allowed_origins=["https://foo.example"])
        )
        assert "https://foo.example" in settings.allowed_origins
        for default_origin in DEFAULT_ALLOWED_ORIGINS:
            assert default_origin in settings.allowed_origins

    def test_builder_does_not_mutate_module_defaults(self) -> None:
        """Merging extras must not mutate the shared DEFAULT_* lists."""
        build_transport_security(Config(http_allowed_hosts=["myapp.ngrok.io"]))
        assert DEFAULT_ALLOWED_HOSTS == ["127.0.0.1:*", "localhost:*", "[::1]:*"]
        assert "myapp.ngrok.io" not in DEFAULT_ALLOWED_HOSTS


class TestValidateHttpAllowlist:
    """Tests for the advisory ``validate_http_allowlist`` misconfig detector.

    These guard the two silent fail-closed mistakes: a Host allowlist entry
    written WITH a scheme, and an Origin allowlist entry written WITHOUT one.
    The function is advisory-only -- it returns warning strings and never
    rejects or alters settings.
    """

    def test_default_config_no_warnings(self) -> None:
        """Default config (both lists empty) -> no warnings."""
        assert validate_http_allowlist(Config()) == []

    def test_host_entry_with_scheme_warns(self) -> None:
        """A host entry carrying a scheme looks like an origin -> one warning."""
        warnings = validate_http_allowlist(
            Config(http_allowed_hosts=["https://bad.example"])
        )
        assert len(warnings) == 1
        assert "http_allowed_hosts" in warnings[0]
        assert "https://bad.example" in warnings[0]

    def test_origin_entry_without_scheme_warns(self) -> None:
        """An origin entry missing a scheme -> one warning."""
        warnings = validate_http_allowlist(
            Config(http_allowed_origins=["bad.example"])
        )
        assert len(warnings) == 1
        assert "http_allowed_origins" in warnings[0]
        assert "bad.example" in warnings[0]

    def test_correct_entries_no_warnings(self) -> None:
        """Well-formed host and origin entries -> no warnings."""
        warnings = validate_http_allowlist(
            Config(
                http_allowed_hosts=["ok.ngrok.io"],
                http_allowed_origins=["https://ok.example"],
            )
        )
        assert warnings == []
