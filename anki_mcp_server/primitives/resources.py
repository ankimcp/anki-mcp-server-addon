# primitives/resources.py
"""Central resource registration module."""

from typing import Any, Callable, Coroutine

# Import all resources (this triggers handler registration at import time)
from .essential.resources.system_info_resource import register_system_info_resource


def register_all_resources(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register all MCP resources with the server.

    Args:
        mcp: FastMCP server instance
        call_main_thread: Async function to bridge calls to Anki's main thread
    """
    # Register essential resources
    register_system_info_resource(mcp, call_main_thread)
