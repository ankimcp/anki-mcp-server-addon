"""Unit tests for the hosted-mode credentials loader.

Pure-logic module (no Anki/Qt/``mw.col``), so it is genuinely unit-testable.
Covers both the pure ``_parse`` dict-level semantics and the ``load()``
end-to-end path through real temp files (missing file, bad JSON, fresh-read).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from anki_mcp_server.tunnel.hosted_credentials import (
    HostedCredentials,
    HostedCredentialsLoader,
    _parse,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, data) -> Path:
    """Write ``data`` (dict → JSON, str → verbatim) to ``path``; return it."""
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data), encoding="utf-8")
    return path


_FULL = {
    "v": 1,
    "token": "opaque-bearer-token",
    "tunnelUrl": "wss://internal-tunnel/connect",
    "user": {"id": "keycloak-123"},
}


# ---------------------------------------------------------------------------
# load() end-to-end through real temp files
# ---------------------------------------------------------------------------

class TestLoadFromFile:
    """``HostedCredentialsLoader.load`` reading real files."""

    def test_valid_full_file(self, tmp_path: Path) -> None:
        """A complete file yields token, tunnel_url, and user_id populated."""
        path = _write(tmp_path / "creds.json", _FULL)

        creds = HostedCredentialsLoader(path).load()

        assert creds == HostedCredentials(
            token="opaque-bearer-token",
            tunnel_url="wss://internal-tunnel/connect",
            user_id="keycloak-123",
        )

    def test_valid_minimal_file(self, tmp_path: Path) -> None:
        """A minimal ``{v, token}`` file yields None tunnel_url and user_id."""
        path = _write(tmp_path / "creds.json", {"v": 1, "token": "tok"})

        creds = HostedCredentialsLoader(path).load()

        assert creds == HostedCredentials(token="tok", tunnel_url=None, user_id=None)

    def test_missing_file_returns_none_without_raising(self, tmp_path: Path) -> None:
        """A missing file returns None and must NOT raise."""
        path = tmp_path / "does-not-exist.json"

        # The assertion is that this call simply returns None, no exception.
        assert HostedCredentialsLoader(path).load() is None

    def test_malformed_json_returns_none(self, tmp_path: Path) -> None:
        """Invalid JSON returns None."""
        path = _write(tmp_path / "creds.json", "not valid json {{{")

        assert HostedCredentialsLoader(path).load() is None

    def test_invalid_utf8_bytes_returns_none_without_raising(self, tmp_path: Path) -> None:
        """Non-UTF-8 bytes (UnicodeDecodeError) return None and must NOT raise."""
        path = tmp_path / "creds.json"
        path.write_bytes(b"\xff\xfe\x00")

        # UnicodeDecodeError is a ValueError subclass; the read must swallow it.
        assert HostedCredentialsLoader(path).load() is None

    def test_unreadable_path_returns_none_without_raising(self, tmp_path: Path) -> None:
        """An OSError on read (path is a directory) returns None without raising."""
        path = tmp_path / "creds-dir"
        path.mkdir()  # read_text on a directory raises IsADirectoryError (OSError).

        assert HostedCredentialsLoader(path).load() is None

    def test_non_object_json_returns_none(self, tmp_path: Path) -> None:
        """Valid JSON that is not an object (e.g. a list) returns None."""
        path = _write(tmp_path / "creds.json", "[1, 2, 3]")

        assert HostedCredentialsLoader(path).load() is None

    def test_accepts_str_or_path(self, tmp_path: Path) -> None:
        """The loader accepts both ``str`` and ``Path`` for its path arg."""
        path = _write(tmp_path / "creds.json", {"v": 1, "token": "tok"})

        from_str = HostedCredentialsLoader(str(path)).load()
        from_path = HostedCredentialsLoader(path).load()

        assert from_str == from_path == HostedCredentials("tok", None, None)

    def test_fresh_read_no_caching(self, tmp_path: Path) -> None:
        """load() re-reads the file every call — no caching across calls."""
        path = tmp_path / "creds.json"
        loader = HostedCredentialsLoader(path)

        _write(path, {"v": 1, "token": "token-A", "user": {"id": "user-A"}})
        first = loader.load()
        assert first == HostedCredentials("token-A", None, "user-A")

        # Overwrite with a different payload; the same loader must reflect it.
        _write(path, {"v": 1, "token": "token-B", "tunnelUrl": "wss://b/connect"})
        second = loader.load()
        assert second == HostedCredentials("token-B", "wss://b/connect", None)


# ---------------------------------------------------------------------------
# _parse — pure dict-level reader semantics
# ---------------------------------------------------------------------------

class TestParseVersion:
    """The ``v`` field must strict-equal integer 1."""

    def test_version_missing(self) -> None:
        assert _parse({"token": "tok"}) is None

    def test_version_wrong_value(self) -> None:
        assert _parse({"v": 2, "token": "tok"}) is None

    def test_version_wrong_type_string(self) -> None:
        assert _parse({"v": "1", "token": "tok"}) is None

    def test_version_bool_rejected(self) -> None:
        # bool is an int subclass; ``True == 1`` must NOT sneak through.
        assert _parse({"v": True, "token": "tok"}) is None


class TestParseToken:
    """The ``token`` field must be a non-empty string."""

    def test_token_missing(self) -> None:
        assert _parse({"v": 1}) is None

    def test_token_empty(self) -> None:
        assert _parse({"v": 1, "token": ""}) is None

    def test_token_non_string(self) -> None:
        assert _parse({"v": 1, "token": 12345}) is None


class TestParseTunnelUrl:
    """``tunnelUrl`` is optional; empty/absent/non-string → None."""

    def test_tunnel_url_present(self) -> None:
        creds = _parse({"v": 1, "token": "tok", "tunnelUrl": "wss://x/connect"})
        assert creds is not None
        assert creds.tunnel_url == "wss://x/connect"

    def test_tunnel_url_empty_becomes_none(self) -> None:
        creds = _parse({"v": 1, "token": "tok", "tunnelUrl": ""})
        assert creds is not None
        assert creds.tunnel_url is None

    def test_tunnel_url_non_string_becomes_none(self) -> None:
        creds = _parse({"v": 1, "token": "tok", "tunnelUrl": 123})
        assert creds is not None
        assert creds.tunnel_url is None


class TestParseUser:
    """``user.id`` is optional/display-only and never fails the load."""

    def test_user_without_id(self) -> None:
        creds = _parse({"v": 1, "token": "tok", "user": {"name": "x"}})
        assert creds is not None
        assert creds.user_id is None

    def test_user_malformed_string(self) -> None:
        creds = _parse({"v": 1, "token": "tok", "user": "not-a-dict"})
        assert creds is not None
        assert creds.user_id is None

    def test_user_id_non_string_becomes_none(self) -> None:
        creds = _parse({"v": 1, "token": "tok", "user": {"id": 999}})
        assert creds is not None
        assert creds.user_id is None

    def test_user_id_populated(self) -> None:
        creds = _parse({"v": 1, "token": "tok", "user": {"id": "kc-1"}})
        assert creds is not None
        assert creds.user_id == "kc-1"


class TestParseForwardCompat:
    """Unknown extra keys are ignored."""

    def test_unknown_keys_ignored(self) -> None:
        creds = _parse(
            {
                "v": 1,
                "token": "tok",
                "future": {"nested": True},
                "somethingNew": 42,
            }
        )
        assert creds == HostedCredentials("tok", None, None)


class TestFrozenDataclass:
    """HostedCredentials is immutable."""

    def test_is_frozen(self) -> None:
        creds = HostedCredentials("tok", None, None)
        with pytest.raises(dataclasses.FrozenInstanceError):
            creds.token = "other"  # type: ignore[misc]
