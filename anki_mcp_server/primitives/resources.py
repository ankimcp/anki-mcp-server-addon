# primitives/resources.py
"""Central resource registration module."""

from typing import Any, Callable, Coroutine

from ..resource_decorator import register_resources

# Import triggers registration via @Resource decorator
from .essential.resources import system_info_resource  # noqa: F401


def register_all_resources(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register all MCP resources with the server.

    Args:
        mcp: FastMCP server instance
        call_main_thread: Async function to bridge calls to Anki's main thread
    """
    register_resources(mcp, call_main_thread)
