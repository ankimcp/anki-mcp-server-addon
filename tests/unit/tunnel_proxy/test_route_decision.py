"""``decide_route`` / ``resolve_route`` / ``ProxyRoute`` (tests 9-12 of the plan).

Pure: no sockets. The system proxy is described as the dict
``urllib.request.getproxies()`` would return, so every real platform shape
(macOS, Windows registry, environment variables) is a literal here.
"""
from __future__ import annotations

import urllib.request
from typing import Any, Callable

import pytest

from .helpers import (
    SECRET,
    WINDOWS_SHAPE_IDS,
    load_proxy,
    set_system_proxies,
    windows_socks_only_shapes,
)

_HOST = "relay.test"


def _decide(
    px: Any,
    proxies: dict[str, str],
    *,
    setting: Any = None,
    host: str = _HOST,
    port: int = 443,
    secure: bool = True,
    bypass: Callable[[str], bool] = lambda host: False,
) -> Any:
    return px.decide_route(
        setting if setting is not None else px.AUTO_PROXY,
        host=host,
        port=port,
        secure=secure,
        proxies=proxies,
        bypass=bypass,
    )


def _assert_socks_error(exc: BaseException) -> None:
    text = str(exc)
    assert "tunnel_proxy" in text
    assert "socks" in text.lower()
    assert "none" in text.lower()
    assert "http" in text.lower()


CLASH = {
    "http": "http://127.0.0.1:7890",
    "https": "http://127.0.0.1:7890",
    "socks": "http://127.0.0.1:7890",
}
WINDOWS_SOCKS_ONLY_SHAPES = windows_socks_only_shapes(1080)


class TestAutoMode:

    def test_no_proxy_means_direct(self) -> None:
        px = load_proxy()

        route = _decide(px, {})

        assert route.proxy_url is None
        assert "direct" in route.display().lower()

    def test_clash_shape_uses_the_http_proxy_and_says_socks_was_ignored(self) -> None:
        px = load_proxy()

        route = _decide(px, CLASH)

        assert route.proxy_url == "http://127.0.0.1:7890"
        assert "127.0.0.1:7890" in route.display()
        assert "socks" in (route.display() + route.source).lower()

    def test_socks_only_macos_shape_is_a_terminal_error(self) -> None:
        px = load_proxy()

        with pytest.raises(px.TunnelProxyError) as excinfo:
            _decide(px, {"socks": "http://127.0.0.1:1080"})

        _assert_socks_error(excinfo.value)

    @pytest.mark.parametrize(
        "shape", WINDOWS_SOCKS_ONLY_SHAPES, ids=WINDOWS_SHAPE_IDS
    )
    def test_windows_socks_only_shapes_are_terminal_errors(
        self, shape: dict[str, str]
    ) -> None:
        px = load_proxy()

        with pytest.raises(px.TunnelProxyError) as excinfo:
            _decide(px, shape)

        _assert_socks_error(excinfo.value)

    def test_windows_mixed_shape_uses_the_http_proxy(self) -> None:
        px = load_proxy()
        shape = {
            "http": "http://127.0.0.1:8080",
            "https": "http://127.0.0.1:8080",
            "socks": "socks://127.0.0.1:1080",
        }

        assert _decide(px, shape).proxy_url == "http://127.0.0.1:8080"

    def test_https_scheme_proxy_is_used(self) -> None:
        px = load_proxy()

        route = _decide(px, {"https": "https://127.0.0.1:3128"})

        assert route.proxy_url == "https://127.0.0.1:3128"

    def test_first_usable_candidate_wins_over_an_earlier_socks_one(self) -> None:
        px = load_proxy()

        route = _decide(
            px, {"https": "socks5://127.0.0.1:1080", "all": "http://127.0.0.1:2"}
        )

        assert route.proxy_url == "http://127.0.0.1:2"

    def test_unusable_scheme_is_a_terminal_error_naming_the_setting(self) -> None:
        px = load_proxy()

        with pytest.raises(px.TunnelProxyError) as excinfo:
            _decide(px, {"https": "ftp://127.0.0.1:21"})

        assert "tunnel_proxy" in str(excinfo.value)


class TestEntryShapes:

    def test_scheme_less_entry_is_treated_as_http(self) -> None:
        px = load_proxy()

        route = _decide(px, {"https": "127.0.0.1:7890"})

        assert route.proxy_url == "http://127.0.0.1:7890"
        assert "127.0.0.1:7890" in route.display()

    def test_scheme_less_entry_under_all_is_treated_as_http(self) -> None:
        px = load_proxy()

        assert _decide(px, {"all": "proxy.corp:3128"}).proxy_url == (
            "http://proxy.corp:3128"
        )

    def test_socks_under_a_non_candidate_key_is_ignored(self) -> None:
        px = load_proxy()

        route = _decide(px, {"ftp": "socks://127.0.0.1:1080"})

        assert route.proxy_url is None

    @pytest.mark.parametrize(
        "entry",
        [
            f"http://user:{SECRET}@127.0.0.1:99999",
            f"http://user:{SECRET}@127.0.0.1:abc",
            f"http://user:{SECRET}@127.0.0.1:1/path",
            f"http://user:{SECRET}@127.0.0.1:1?x=1",
            "http://user@127.0.0.1:1",
            f"ftp://user:{SECRET}@127.0.0.1:21",
        ],
    )
    def test_malformed_candidate_is_a_terminal_error_without_credentials(
        self, entry: str
    ) -> None:
        px = load_proxy()

        with pytest.raises(px.TunnelProxyError) as excinfo:
            _decide(px, {"https": entry})

        text = str(excinfo.value)
        assert "tunnel_proxy" in text
        assert SECRET not in text
        assert "user:" not in text
        assert "user@" not in text


class TestCandidateKeys:

    def test_all_key_with_socks_is_a_terminal_error(self) -> None:
        px = load_proxy()

        with pytest.raises(px.TunnelProxyError) as excinfo:
            _decide(px, {"all": "socks5://127.0.0.1:1080"})

        _assert_socks_error(excinfo.value)

    def test_all_key_with_http_is_used(self) -> None:
        px = load_proxy()

        assert _decide(px, {"all": "http://127.0.0.1:3128"}).proxy_url == (
            "http://127.0.0.1:3128"
        )

    def test_secure_target_prefers_https_over_all(self) -> None:
        px = load_proxy()

        route = _decide(
            px, {"https": "http://127.0.0.1:1", "all": "http://127.0.0.1:2"}
        )

        assert route.proxy_url == "http://127.0.0.1:1"

    def test_secure_target_ignores_the_http_key(self) -> None:
        px = load_proxy()

        assert _decide(px, {"http": "http://127.0.0.1:3128"}).proxy_url is None

    def test_plain_target_uses_the_http_key(self) -> None:
        px = load_proxy()

        route = _decide(
            px, {"http": "http://127.0.0.1:3128"}, port=80, secure=False
        )

        assert route.proxy_url == "http://127.0.0.1:3128"

    def test_plain_target_ignores_the_https_key(self) -> None:
        px = load_proxy()

        route = _decide(
            px, {"https": "http://127.0.0.1:3128"}, port=80, secure=False
        )

        assert route.proxy_url is None

    @pytest.mark.parametrize("key", ["wss", "ws"])
    def test_websocket_scheme_keys_are_not_consulted(self, key: str) -> None:
        px = load_proxy()

        route = _decide(px, {key: "http://127.0.0.1:3128"})

        assert route.proxy_url is None


class TestBypass:

    def test_macos_exception_list_matches_despite_the_port_quirk(self) -> None:
        px = load_proxy()
        settings = {"exclude_simple": False, "exceptions": [_HOST]}

        def bypass(host: str) -> bool:
            return urllib.request._proxy_bypass_macosx_sysconf(host, settings)

        # The quirk this guards: macOS matches against whatever string it is
        # given, and websockets passes "host:port", so a bare-host exception
        # never matched.
        assert bypass(f"{_HOST}:443") is False
        assert bypass(_HOST) is True

        route = _decide(px, CLASH, bypass=bypass)

        assert route.proxy_url is None

    def test_macos_wildcard_exception_matches(self) -> None:
        px = load_proxy()
        settings = {"exclude_simple": False, "exceptions": ["*.test"]}

        route = _decide(
            px,
            CLASH,
            bypass=lambda h: urllib.request._proxy_bypass_macosx_sysconf(h, settings),
        )

        assert route.proxy_url is None

    def test_environment_no_proxy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        px = load_proxy()
        monkeypatch.setenv("no_proxy", "localhost,.test")

        route = _decide(px, CLASH, bypass=urllib.request.proxy_bypass_environment)

        assert route.proxy_url is None

    def test_windows_registry_override(self) -> None:
        px = load_proxy()

        route = _decide(
            px,
            CLASH,
            bypass=lambda h: urllib.request._proxy_bypass_winreg_override(
                h, "example.com;*.test;<local>"
            ),
        )

        assert route.proxy_url is None

    def test_a_non_matching_exception_list_does_not_bypass(self) -> None:
        px = load_proxy()
        settings = {"exclude_simple": False, "exceptions": ["other.example"]}

        route = _decide(
            px,
            CLASH,
            bypass=lambda h: urllib.request._proxy_bypass_macosx_sysconf(h, settings),
        )

        assert route.proxy_url == "http://127.0.0.1:7890"

    def test_bypassed_target_is_direct_even_on_a_socks_only_system(self) -> None:
        px = load_proxy()

        route = _decide(
            px, {"socks": "http://127.0.0.1:1080"}, bypass=lambda h: True
        )

        assert route.proxy_url is None

    @pytest.mark.parametrize("matching", [_HOST, f"{_HOST}:443"])
    def test_bypass_is_asked_with_host_and_with_host_and_port(
        self, matching: str
    ) -> None:
        px = load_proxy()

        route = _decide(px, CLASH, bypass=lambda h: h == matching)

        assert route.proxy_url is None


class TestNoneAndExplicit:

    def test_none_ignores_system_proxies(self) -> None:
        px = load_proxy()
        setting = px.ProxySetting(px.ProxyMode.NONE)

        assert _decide(px, CLASH, setting=setting).proxy_url is None

    def test_none_is_direct_even_when_the_system_is_socks_only(self) -> None:
        px = load_proxy()
        setting = px.ProxySetting(px.ProxyMode.NONE)

        route = _decide(
            px, {"socks": "http://127.0.0.1:1080"}, setting=setting
        )

        assert route.proxy_url is None

    def test_explicit_is_used_exactly_and_ignores_system_and_bypass(self) -> None:
        px = load_proxy()
        setting = px.ProxySetting(px.ProxyMode.EXPLICIT, "http://u:p@10.0.0.1:3128")

        route = _decide(px, CLASH, setting=setting, bypass=lambda h: True)

        assert route.proxy_url == "http://u:p@10.0.0.1:3128"

    def test_explicit_works_on_a_socks_only_system(self) -> None:
        px = load_proxy()
        setting = px.ProxySetting(px.ProxyMode.EXPLICIT, "http://10.0.0.1:3128")

        route = _decide(px, {"socks": "http://127.0.0.1:1080"}, setting=setting)

        assert route.proxy_url == "http://10.0.0.1:3128"


class TestProxyRoute:

    def test_direct_display(self) -> None:
        px = load_proxy()

        assert "direct" in px.ProxyRoute(None, "no proxy configured").display().lower()

    def test_display_masks_credentials_and_keeps_the_address(self) -> None:
        px = load_proxy()
        route = px.ProxyRoute(f"http://user:{SECRET}@127.0.0.1:7890", "explicit")

        shown = route.display()

        assert SECRET not in shown
        assert "user:" not in shown
        assert "127.0.0.1:7890" in shown

    def test_repr_does_not_expose_credentials(self) -> None:
        px = load_proxy()
        url = f"http://user:{SECRET}@127.0.0.1:7890"

        assert SECRET not in repr(px.ProxyRoute(url, "explicit"))
        assert SECRET not in repr(px.parse_tunnel_proxy(url))
        assert SECRET not in repr(px.ProxySetting(px.ProxyMode.EXPLICIT, url))

    def test_the_route_is_immutable(self) -> None:
        px = load_proxy()
        route = px.ProxyRoute(None, "x")

        with pytest.raises(AttributeError):
            route.proxy_url = "http://h:1"  # type: ignore[misc]


class TestResolveRoute:
    """``resolve_route`` reads urllib's lookups at call time, as attributes."""

    def test_reads_the_system_proxies_on_every_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        px = load_proxy()
        set_system_proxies(monkeypatch, {})
        assert px.resolve_route(px.AUTO_PROXY, "wss://relay.test").proxy_url is None

        set_system_proxies(monkeypatch, CLASH)
        route = px.resolve_route(px.AUTO_PROXY, "wss://relay.test")

        assert route.proxy_url == "http://127.0.0.1:7890"

    def test_secure_target_defaults_to_port_443_for_the_bypass_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        px = load_proxy()
        asked: list[str] = []
        set_system_proxies(
            monkeypatch, CLASH, bypass=lambda h: asked.append(h) or False
        )

        px.resolve_route(px.AUTO_PROXY, "wss://relay.test")

        assert "relay.test" in asked
        assert "relay.test:443" in asked

    def test_plain_target_uses_the_http_key_and_its_port(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        px = load_proxy()
        asked: list[str] = []
        set_system_proxies(
            monkeypatch,
            {"http": "http://127.0.0.1:3128", "https": "http://127.0.0.1:9"},
            bypass=lambda h: asked.append(h) or False,
        )

        route = px.resolve_route(px.AUTO_PROXY, "ws://127.0.0.1:3004")

        assert route.proxy_url == "http://127.0.0.1:3128"
        assert "127.0.0.1:3004" in asked

    @pytest.mark.parametrize("url", ["https://relay.test", "wss://relay.test:8443"])
    def test_https_and_explicit_port_targets(
        self, monkeypatch: pytest.MonkeyPatch, url: str
    ) -> None:
        px = load_proxy()
        set_system_proxies(monkeypatch, CLASH)

        assert px.resolve_route(px.AUTO_PROXY, url).proxy_url == (
            "http://127.0.0.1:7890"
        )

    def test_socks_only_system_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        px = load_proxy()
        set_system_proxies(monkeypatch, {"socks": "http://127.0.0.1:1080"})

        with pytest.raises(px.TunnelProxyError):
            px.resolve_route(px.AUTO_PROXY, "wss://relay.test")
