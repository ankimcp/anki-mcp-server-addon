"""Unit tests for the tunnel client-version normalizer.

``normalize_client_version`` reduces an arbitrary version string to the bare
``MAJOR.MINOR.PATCH`` the relay validates against
(``^\\d+\\.\\d+\\.\\d+(-[0-9A-Za-z.-]+)?$``, no ``+build`` metadata, no ``v``
prefix). It is pure and aqt-free, so it imports without an Anki environment.
"""

from __future__ import annotations

import anki_mcp_server  # noqa: F401 – triggers vendor path setup

import pytest

from anki_mcp_server.tunnel.client import TunnelClient
from anki_mcp_server.tunnel.protocol import (
    CLIENT_TYPE,
    CLIENT_TYPE_HEADER,
    CLIENT_VERSION_HEADER,
    normalize_client_version,
)


class TestNormalizeClientVersion:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("0.24.0", "0.24.0"),        # already-clean semver passes through
            ("v1.2.3", "1.2.3"),         # leading "v" prefix dropped
            ("1.2.3-beta.1", "1.2.3"),   # prerelease suffix dropped
            ("1.2.3+build5", "1.2.3"),   # build metadata dropped
            ("0.24.0-rc1+meta", "0.24.0"),  # both suffix + metadata dropped
        ],
    )
    def test_extracts_bare_triple(self, raw: str, expected: str) -> None:
        assert normalize_client_version(raw) == expected

    @pytest.mark.parametrize("raw", ["not-a-version", ""])
    def test_returns_none_without_triple(self, raw: str) -> None:
        assert normalize_client_version(raw) is None


class TestClientHeaderConstants:
    def test_header_names_and_type_literal(self) -> None:
        assert CLIENT_TYPE_HEADER == "x-ankimcp-client-type"
        assert CLIENT_VERSION_HEADER == "x-ankimcp-client-version"
        assert CLIENT_TYPE == "addon"


class TestBuildConnectHeaders:
    """Cover the wiring in ``TunnelClient._build_connect_headers()``.

    ``normalize_client_version`` is tested in isolation above; this class
    verifies the method that actually assembles the WebSocket upgrade headers
    from the bearer token and the (lazily re-imported) addon ``__version__``.
    """

    @staticmethod
    def _make_client(access_token: str) -> TunnelClient:
        """Build a TunnelClient whose only meaningful state is the token.

        ``_build_connect_headers()`` reads only ``self._bearer_token``; the
        transport is never touched, so a bare sentinel satisfies the
        constructor without opening a socket or building a real MCP server.
        """
        return TunnelClient(
            server_url="wss://tunnel.example/test",
            bearer_token=access_token,
            transport=object(),  # type: ignore[arg-type] -- unused by this method
        )

    def test_always_present_headers_and_real_version(self) -> None:
        import anki_mcp_server

        client = self._make_client("tok-abc123")
        headers = client._build_connect_headers()

        # 1. Authorization is always present, Bearer-prefixing the token.
        assert headers["Authorization"] == "Bearer tok-abc123"
        # 2. Client-type is always present and identifies the addon.
        assert headers[CLIENT_TYPE_HEADER] == CLIENT_TYPE
        # 3. Real __version__ is clean semver, so the version header is present
        #    and matches the normalizer applied to it.
        expected = normalize_client_version(anki_mcp_server.__version__)
        assert expected is not None  # guards against a future broken version
        assert headers[CLIENT_VERSION_HEADER] == expected

    def test_version_header_omitted_when_version_not_normalizable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ``_build_connect_headers`` does ``from .. import __version__`` at call
        # time, which re-reads the ``__version__`` attribute off the
        # ``anki_mcp_server`` package module on every call. Patching that
        # attribute is therefore exactly what the lazy import resolves.
        monkeypatch.setattr("anki_mcp_server.__version__", "nightly")

        client = self._make_client("tok-xyz")
        headers = client._build_connect_headers()

        # Non-semver version -> version header omitted entirely...
        assert CLIENT_VERSION_HEADER not in headers
        # ...while the always-present identity headers still hold.
        assert headers["Authorization"] == "Bearer tok-xyz"
        assert headers[CLIENT_TYPE_HEADER] == CLIENT_TYPE
