"""``parse_tunnel_proxy`` / ``validate_tunnel_proxy`` (tests 6 and 14 of the plan)."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from .helpers import load_proxy

_VALID_KEYWORDS = [
    ("auto", "AUTO"),
    ("none", "NONE"),
    (" AUTO ", "AUTO"),
    ("None", "NONE"),
    ("  nOnE\n", "NONE"),
]
_VALID_URLS = ["http://u:p@x:8080", "https://x", "http://x:3128", "http://x/"]
_INVALID = [
    "",
    "   ",
    123,
    None,
    ["auto"],
    "automatic",
    "ftp://x",
    "socks5h://u:p@x",
    "socks5://127.0.0.1:1080",
    "socks://x",
    "http://x/path",
    "http://x?q=1",
    "http://x#frag",
    "http://u@x",
    "http://",
    "x:8080",
    "http://x:99999",
    "http://x:abc",
    "http://user:s3cret@x:abc",
    "http://user:s3cret@x:99999",
]
# Pieces of the invalid inputs above that must never be echoed back.
_CREDENTIAL_FRAGMENTS = ["u:p", ":p@", "u@x", "s3cret", "user:"]


class TestParse:

    @pytest.mark.parametrize(("raw", "mode"), _VALID_KEYWORDS)
    def test_keywords_are_case_and_whitespace_insensitive(
        self, raw: str, mode: str
    ) -> None:
        px = load_proxy()

        parsed = px.parse_tunnel_proxy(raw)

        assert parsed == px.ProxySetting(px.ProxyMode[mode])
        assert parsed.url is None

    @pytest.mark.parametrize("raw", _VALID_URLS)
    def test_http_proxy_urls_are_explicit(self, raw: str) -> None:
        px = load_proxy()

        parsed = px.parse_tunnel_proxy(raw)

        assert parsed.mode is px.ProxyMode.EXPLICIT
        assert parsed.url == raw

    def test_auto_constant_is_the_auto_setting(self) -> None:
        px = load_proxy()

        assert px.AUTO_PROXY == px.parse_tunnel_proxy("auto")

    @pytest.mark.parametrize("raw", _INVALID)
    def test_invalid_values_yield_a_message_naming_the_field(
        self, raw: Any
    ) -> None:
        px = load_proxy()

        parsed = px.parse_tunnel_proxy(raw)

        assert isinstance(parsed, str)
        assert "tunnel_proxy" in parsed
        for fragment in _CREDENTIAL_FRAGMENTS:
            assert fragment not in parsed

    @pytest.mark.parametrize(
        "raw", ["socks5://127.0.0.1:1080", "socks5h://u:p@x", "socks4://x"]
    )
    def test_socks_urls_are_rejected_with_the_alternatives(self, raw: str) -> None:
        px = load_proxy()

        message = px.parse_tunnel_proxy(raw)

        assert isinstance(message, str)
        assert "tunnel_proxy" in message
        assert "socks" in message.lower()
        assert "none" in message.lower()
        assert "http" in message.lower()


class TestValidate:

    @pytest.mark.parametrize("raw", [r for r, _ in _VALID_KEYWORDS] + _VALID_URLS)
    def test_valid_values_produce_no_warning(self, raw: str) -> None:
        px = load_proxy()

        assert px.validate_tunnel_proxy(SimpleNamespace(tunnel_proxy=raw)) == []

    @pytest.mark.parametrize("raw", _INVALID)
    def test_invalid_values_produce_one_warning_without_credentials(
        self, raw: Any
    ) -> None:
        px = load_proxy()

        warnings = px.validate_tunnel_proxy(SimpleNamespace(tunnel_proxy=raw))

        assert len(warnings) == 1
        assert "tunnel_proxy" in warnings[0]
        for fragment in _CREDENTIAL_FRAGMENTS:
            assert fragment not in warnings[0]

    def test_warning_matches_the_parser_reason(self) -> None:
        px = load_proxy()

        warnings = px.validate_tunnel_proxy(
            SimpleNamespace(tunnel_proxy="socks5://127.0.0.1:1080")
        )

        assert warnings == [px.parse_tunnel_proxy("socks5://127.0.0.1:1080")]
