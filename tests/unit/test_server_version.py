"""Unit test: the MCP server advertises the addon's own version.

FastMCP's vendored SDK exposes no ``version`` kwarg, so the lowlevel Server
defaults to ``version=None``. ``Server.create_initialization_options()`` then
falls back to ``importlib.metadata.version("mcp")``, which in production
resolved to ``None`` (a stale empty dist-info dir on sys.path) and crashed
every ``initialize`` handshake with a pydantic ValidationError.

``build_fastmcp()`` (extracted from ``McpServer._async_main``) sets
``mcp._mcp_server.version`` explicitly so that fallback is never consulted.
This test exercises that function directly -- no Anki, no network.

conftest.py installs aqt + primitives stubs before this module is collected,
so the addon import below is safe even without a running Anki.
"""

from __future__ import annotations

from anki_mcp_server import __version__
from anki_mcp_server.mcp_server import build_fastmcp


def test_build_fastmcp_advertises_addon_version() -> None:
    mcp = build_fastmcp("/", None)

    init_options = mcp._mcp_server.create_initialization_options()

    assert init_options.server_version == __version__
