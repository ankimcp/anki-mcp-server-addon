"""Pure policy for building MCP transport-security settings.

This module decides which Host/Origin headers the local HTTP transport will
accept under DNS-rebinding protection. It is intentionally side-effect free and
free of any aqt / uvicorn / FastMCP imports so it can be unit-tested without a
running Anki.

The defaults mirror the MCP SDK's own loopback auto-default (see
``vendor/shared/mcp/server/fastmcp/server.py`` lines 181-182) so that re-enabling
protection does not change behavior for ordinary localhost clients. Operators
can widen the allowlist for tunnel / reverse-proxy setups via the
``http_allowed_hosts`` / ``http_allowed_origins`` config fields.

NOTE (TDD staging): ``build_transport_security`` is intentionally NOT yet wired
into ``mcp_server.py``'s ``run()`` — that wiring is a later step of the
advisory fix. For now the function exists and is unit-tested in isolation.
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
    """Build ``TransportSecuritySettings`` preserving the addon's CURRENT behavior.

    This is a behavior-preserving structural extraction only. It returns exactly
    what ``mcp_server.py``'s ``run()`` constructs today: DNS-rebinding protection
    DISABLED, with no Host/Origin allowlist. The ``config`` argument is
    deliberately unused for now.

    The secure policy (re-enabling protection and applying the loopback
    allowlist via ``DEFAULT_ALLOWED_HOSTS`` / ``DEFAULT_ALLOWED_ORIGINS`` plus
    operator extras) is intentionally NOT yet implemented — a later commit of
    the advisory fix fills in the body. Until then the unit tests asserting the
    secure policy are expected to be RED (TDD).

    Args:
        config: Addon configuration (currently unused; the secure policy that
            reads ``http_allowed_hosts`` / ``http_allowed_origins`` is not yet
            implemented).

    Returns:
        A ``TransportSecuritySettings`` instance with
        ``enable_dns_rebinding_protection=False`` and no allowlist — matching
        current insecure behavior.
    """
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)
