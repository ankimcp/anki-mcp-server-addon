"""Proxy routing for the tunnel's outbound connections.

One resolver, two consumers: the WebSocket in ``client.py`` and the login /
token-refresh HTTP calls in ``auth.py`` both ask :func:`resolve_route` which
way to go, so a single ``tunnel_proxy`` setting governs both.

Only HTTP-type proxies (``http://`` / ``https://``, used through HTTP CONNECT)
are supported. SOCKS needs a package the add-on does not ship, so a system that
offers nothing but SOCKS is a terminal :class:`TunnelProxyError` that names the
setting -- never a silent fallback to a direct connection.

Credentials may be part of a proxy URL. Nothing in this module puts them into
a message, ``repr`` or log line; :func:`mask_url_credentials` is the tool the
consumers use on any library text that might quote a URL.

Stdlib-only: no Qt, no vendored packages, no I/O beyond the ``urllib.request``
settings lookups in :func:`resolve_route`.
"""

from __future__ import annotations

import enum
import ipaddress
import re
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Mapping
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from ..config import Config

# The ``error_code`` carried by ``ProxyUnusableError`` when the proxy cannot be
# used. Terminal: reconnecting cannot fix it.
PROXY_UNSUPPORTED = "proxy_unsupported"

_HTTP_PROXY_SCHEMES = ("http", "https")
_SOCKS_SCHEMES = ("socks", "socks4", "socks4a", "socks5", "socks5h")

_EXAMPLE = "http://127.0.0.1:7890"
_EXPECTED = (
    'expected "auto", "none" or an http:// or https:// proxy address '
    f"such as {_EXAMPLE}"
)
PROXY_FIX_HINT = (
    'Set "tunnel_proxy" in the AnkiMCP Server add-on config '
    '(Tools > Add-ons > AnkiMCP Server > Config) to "none" to connect '
    f'directly, or to an http:// proxy address such as "{_EXAMPLE}", '
    "then restart Anki."
)

# The lookbehind lets a scheme start only at a boundary; without it every
# position of a long alphanumeric run rescans the run (quadratic).
_USERINFO_RE = re.compile(
    r"(?<![A-Za-z0-9+.\-])([A-Za-z][A-Za-z0-9+.\-]*://)[^/?#\s]*@"
)


# --------------------------------------------------------------------------
# Masking
# --------------------------------------------------------------------------

def mask_url_credentials(text: str) -> str:
    """Replace the ``user:password@`` part of every ``scheme://`` URL in ``text``."""
    return _USERINFO_RE.sub(r"\1***@", text)


def mask_proxy_url(url: str) -> str:
    """``http://user:pw@host:1`` -> ``http://***@host:1`` (unchanged without userinfo)."""
    return mask_url_credentials(url)


def _address(url: str) -> str:
    """The ``host:port`` part of a proxy URL, without credentials."""
    try:
        return urlsplit(url).netloc.rpartition("@")[2]
    except ValueError:
        return mask_proxy_url(url)


# --------------------------------------------------------------------------
# Setting
# --------------------------------------------------------------------------

class ProxyMode(enum.Enum):
    AUTO = "auto"
    NONE = "none"
    EXPLICIT = "explicit"


@dataclass(frozen=True, repr=False)
class ProxySetting:
    """The parsed ``tunnel_proxy`` config value.

    Attributes:
        mode: Which of the three behaviours was configured.
        url: The proxy address; set only for ``EXPLICIT``.
    """

    mode: ProxyMode
    url: str | None = None

    def __repr__(self) -> str:
        shown = None if self.url is None else mask_proxy_url(self.url)
        return f"ProxySetting(mode={self.mode.name}, url={shown!r})"


AUTO_PROXY = ProxySetting(ProxyMode.AUTO)


class TunnelProxyError(Exception):
    """The configured or system proxy cannot be used. Terminal.

    ``str()`` names ``tunnel_proxy`` and what to set; it never contains
    credentials.
    """


def _scheme_of(url: str) -> str | None:
    """The lower-cased scheme, or ``None`` when the URL cannot be parsed at all."""
    try:
        return urlsplit(url).scheme.lower()
    except ValueError:
        return None


def _http_proxy_problem(url: str) -> str | None:
    """Why ``url`` is not a usable HTTP(S) proxy address, or ``None`` if it is.

    The phrase never quotes the address, so it is safe to show.
    """
    if not url.isprintable() or any(ch.isspace() for ch in url):
        return "the address contains spaces or control characters"
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "the address or its port is not valid"
    if parts.scheme.lower() not in _HTTP_PROXY_SCHEMES:
        return "only http:// and https:// proxy addresses are supported"
    if not parts.hostname:
        return "the address has no host"
    if not parts.hostname.isascii():
        return (
            "the host name must use ASCII characters only "
            "(write an international name in its xn-- form)"
        )
    if all(ch.isdigit() or ch == "." for ch in parts.hostname):
        try:
            ipaddress.IPv4Address(parts.hostname)
        except ValueError:
            return "the host is made of digits and dots but is not a valid IPv4 address"
    if "[" in parts.netloc:
        try:
            ipaddress.IPv6Address(parts.hostname)
        except ValueError:
            return "the host in square brackets is not a valid IPv6 address"
    if port == 0:
        return "the port must be between 1 and 65535"
    if parts.path not in ("", "/") or "?" in url or "#" in url:
        return "the address must not contain a path, query or fragment"
    if parts.username is not None and parts.password is None:
        return "a user name needs a password"
    return None


def parse_tunnel_proxy(raw: object) -> ProxySetting | str:
    """Validate the raw ``tunnel_proxy`` config value.

    Returns:
        A :class:`ProxySetting` when valid, otherwise the human-readable
        reason (a ``str``). Never raises and never echoes the value, which
        may carry credentials.
    """
    invalid = "Invalid tunnel_proxy"
    if not isinstance(raw, str) or not raw.strip():
        return f"{invalid}: {_EXPECTED}."

    text = raw.strip()
    keyword = text.lower()
    if keyword == "auto":
        return AUTO_PROXY
    if keyword == "none":
        return ProxySetting(ProxyMode.NONE)
    if "://" not in text:
        return f"{invalid}: {_EXPECTED}."

    if _scheme_of(text) in _SOCKS_SCHEMES:
        return (
            f"{invalid}: SOCKS proxies are not supported. Use "
            f'"none" to connect directly, or an http:// proxy address such as '
            f'"{_EXAMPLE}".'
        )
    problem = _http_proxy_problem(text)
    if problem is not None:
        return f"{invalid}: {problem}; {_EXPECTED}."
    return ProxySetting(ProxyMode.EXPLICIT, text)


def validate_tunnel_proxy(config: "Config") -> list[str]:
    """Advisory startup check of ``config.tunnel_proxy``.

    Same contract as ``validate_tunnel_server_url``: pure, never rejects or
    alters the setting, returns zero or one warning for the caller to surface.
    """
    result = parse_tunnel_proxy(config.tunnel_proxy)
    return [result] if isinstance(result, str) else []


# --------------------------------------------------------------------------
# Route
# --------------------------------------------------------------------------

@dataclass(frozen=True, repr=False)
class ProxyRoute:
    """How one outbound connection is made.

    Attributes:
        proxy_url: The HTTP(S) proxy to connect through, or ``None`` for a
            direct connection.
        source: Why this route was chosen, as short human text.
    """

    proxy_url: str | None
    source: str

    @property
    def uses_https_proxy(self) -> bool:
        """Whether the proxy itself is reached over TLS (``https://`` proxy)."""
        return self.proxy_url is not None and _scheme_of(self.proxy_url) == "https"

    def display(self) -> str:
        """One line for the log, with credentials masked."""
        if self.proxy_url is None:
            return f"direct ({self.source})"
        kind = "HTTPS" if self.uses_https_proxy else "HTTP"
        return f"via {kind} proxy {_address(self.proxy_url)} ({self.source})"

    def __repr__(self) -> str:
        shown = None if self.proxy_url is None else mask_proxy_url(self.proxy_url)
        return f"ProxyRoute(proxy_url={shown!r}, source={self.source!r})"


def _system_proxy_error(what: str) -> TunnelProxyError:
    return TunnelProxyError(
        f"Your system proxy settings use {what}, which the AnkiMCP tunnel "
        f"cannot use. {PROXY_FIX_HINT}"
    )


def _with_default_scheme(entry: str) -> str:
    """System entries may be bare ``host:port`` (e.g. ``HTTPS_PROXY=proxy:3128``)."""
    text = entry.strip()
    return text if "://" in text else f"http://{text}"


def decide_route(
    setting: ProxySetting,
    *,
    host: str,
    port: int,
    secure: bool,
    proxies: Mapping[str, str],
    bypass: Callable[[str], bool],
) -> ProxyRoute:
    """Pick the route for one target. Pure.

    ``proxies`` and ``bypass`` stand for ``urllib.request.getproxies()`` and
    ``urllib.request.proxy_bypass``; they are consulted for ``AUTO`` only.
    In ``AUTO`` the bypass list is checked first, then the first usable
    HTTP(S) entry among the target's keys wins (``https`` then ``all`` for a
    secure target, ``http`` then ``all`` for a plain one).

    Raises:
        TunnelProxyError: ``AUTO`` found a proxy it cannot use and no usable
            one -- SOCKS only, an unsupported type, or a malformed address.
    """
    if setting.mode is ProxyMode.NONE:
        return ProxyRoute(None, 'tunnel_proxy is "none"')
    if setting.mode is ProxyMode.EXPLICIT:
        return ProxyRoute(setting.url, "tunnel_proxy setting")

    # macOS matches its exception list against whatever string it is given and
    # websockets passes "host:port", so a bare-host exception only matches the
    # bare form.
    if bypass(host) or bypass(f"{host}:{port}"):
        return ProxyRoute(None, "target is on the system proxy bypass list")

    keys = ("https", "all") if secure else ("http", "all")
    entries = [
        (key, _with_default_scheme(proxies[key]))
        for key in keys
        if proxies.get(key, "").strip()
    ]
    socks_listed = bool(proxies.get("socks", "").strip()) or any(
        _scheme_of(url) in _SOCKS_SCHEMES for _, url in entries
    )

    unsupported_type = False
    for key, url in entries:
        scheme = _scheme_of(url)
        if scheme is None:
            raise _system_proxy_error(
                f"an invalid proxy address ({_http_proxy_problem(url)})"
            )
        if scheme in _SOCKS_SCHEMES:
            continue
        if scheme not in _HTTP_PROXY_SCHEMES:
            unsupported_type = True
            continue
        problem = _http_proxy_problem(url)
        if problem is not None:
            raise _system_proxy_error(f"an invalid proxy address ({problem})")
        label = "proxy" if key == "all" else f"{key.upper()} proxy"
        source = f"system {label}"
        if socks_listed:
            source += "; system SOCKS proxy ignored"
        return ProxyRoute(url, source)

    if socks_listed:
        raise _system_proxy_error("only a SOCKS proxy")
    if unsupported_type:
        raise _system_proxy_error("a proxy type that is not HTTP or HTTPS")
    return ProxyRoute(None, "no proxy configured")


def resolve_route(setting: ProxySetting, target_url: str) -> ProxyRoute:
    """Decide the route for ``target_url`` from the live system settings.

    The lookups are read as attributes of ``urllib.request`` on every call, so
    a VPN toggled between two attempts applies to the next one.

    A malformed target is not a proxy problem and never raises here: the host
    or port that cannot be read is replaced by an empty host / the scheme's
    default port, and the connection attempt itself reports the bad URL.

    Raises:
        TunnelProxyError: See :func:`decide_route`.
    """
    secure = target_url.partition(":")[0].lower() in ("wss", "https")
    host = ""
    port = None
    try:
        parts = urlsplit(target_url)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        pass
    port = port or (443 if secure else 80)
    proxies = urllib.request.getproxies() if setting.mode is ProxyMode.AUTO else {}
    return decide_route(
        setting,
        host=host,
        port=port,
        secure=secure,
        proxies=proxies,
        bypass=urllib.request.proxy_bypass,
    )
