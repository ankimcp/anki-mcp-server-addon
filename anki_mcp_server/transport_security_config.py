"""Pure policy for building MCP transport-security settings.

This module decides which Host/Origin headers the local HTTP transport will
accept under DNS-rebinding protection. It is intentionally side-effect free and
free of any aqt / uvicorn / FastMCP imports so it can be unit-tested without a
running Anki.

The defaults mirror the MCP SDK's own loopback auto-default (see
``vendor/shared/mcp/server/fastmcp/server.py`` lines 181-182) so that DNS-rebinding
protection does not change behavior for ordinary localhost clients. Operators
can widen the allowlist for tunnel / reverse-proxy setups via the
``http_allowed_hosts`` / ``http_allowed_origins`` config fields.

``build_transport_security`` is wired into ``mcp_server.py``'s ``run()``: its
returned ``TransportSecuritySettings`` are passed to ``FastMCP`` so the local
HTTP transport enforces the policy. DNS-rebinding protection is always enabled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.transport_security import TransportSecuritySettings

if TYPE_CHECKING:  # avoid an import cycle / aqt at runtime
    from .config import Config


# Loopback allowlist copied verbatim from the MCP SDK auto-default
# (vendor/shared/mcp/server/fastmcp/server.py:181-182). Keep in sync.
DEFAULT_ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
DEFAULT_ALLOWED_ORIGINS = [
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
]


def build_transport_security(config: "Config") -> TransportSecuritySettings:
    """Build ``TransportSecuritySettings`` with DNS-rebinding protection ENABLED.

    Returns settings that enable DNS-rebinding protection and apply the loopback
    allowlist (``DEFAULT_ALLOWED_HOSTS`` / ``DEFAULT_ALLOWED_ORIGINS``) so that
    ordinary localhost clients keep working, while rejecting forged Host/Origin
    headers from DNS-rebinding attacks. Operators can widen the allowlist for
    tunnel / reverse-proxy setups by populating the ``http_allowed_hosts`` /
    ``http_allowed_origins`` config fields, which are appended to the defaults.

    The module-level default lists are never mutated: each call produces fresh
    lists via concatenation.

    Args:
        config: Addon configuration. ``http_allowed_hosts`` and
            ``http_allowed_origins`` provide operator-supplied additions to the
            built-in loopback allowlist.

    Returns:
        A ``TransportSecuritySettings`` instance with
        ``enable_dns_rebinding_protection=True`` and an allowlist combining the
        loopback defaults with the operator-configured extras.
    """
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[*DEFAULT_ALLOWED_HOSTS, *config.http_allowed_hosts],
        allowed_origins=[*DEFAULT_ALLOWED_ORIGINS, *config.http_allowed_origins],
    )


def validate_http_allowlist(config: "Config") -> list[str]:
    """Detect the two scheme-format mistakes in the HTTP allowlist config.

    Both ``http_allowed_hosts`` and ``http_allowed_origins`` fail CLOSED but
    SILENTLY on an easy operator mistake: a Host allowlist entry written WITH a
    scheme never matches a (scheme-less) Host header, and an Origin allowlist
    entry written WITHOUT a scheme never matches a (scheme-bearing) Origin
    header. Either mistake produces a confusing "why is my proxy 403'd" debug
    session. This validator surfaces those mistakes as advisory warnings.

    This function is pure: it has no side effects and never prints. The caller
    (startup wiring in ``__init__.py``) is responsible for surfacing the
    returned strings, mirroring ``validate_disabled_tools`` /
    ``validate_enabled_destructive_tools``. It does NOT reject or alter any
    setting -- ``build_transport_security`` still uses the raw config verbatim.

    Args:
        config: Addon configuration. ``http_allowed_hosts`` should hold
            ``host[:port]`` values WITHOUT a scheme; ``http_allowed_origins``
            should hold full origins WITH a scheme.

    Returns:
        List of human-readable warning messages, one per misconfigured entry.
        Empty list when both fields are empty or every entry is well-formed.
    """
    warnings: list[str] = []

    for host in config.http_allowed_hosts:
        if "://" in host:
            warnings.append(
                f"http_allowed_hosts: '{host}' looks like an origin "
                f"(it has a scheme). Host allowlist entries must be "
                f"host[:port] without a scheme (e.g. 'myapp.ngrok.io' or "
                f"'myapp.ngrok.io:443')."
            )

    for origin in config.http_allowed_origins:
        if "://" not in origin:
            warnings.append(
                f"http_allowed_origins: '{origin}' is missing a scheme. "
                f"Origin allowlist entries must be full origins with a scheme "
                f"(e.g. 'https://myapp.example')."
            )

    return warnings
