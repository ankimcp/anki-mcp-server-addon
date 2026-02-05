"""Central tool registration module."""
from typing import Any, Callable, Coroutine

from ..tool_decorator import register_tools

# Import packages to trigger auto-discovery of all tool modules
from .essential import tools as _essential_tools  # noqa: F401
from .gui import tools as _gui_tools  # noqa: F401


def register_all_tools(
    mcp,
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register all MCP tools with the server.

    Args:
        mcp: FastMCP server instance
        call_main_thread: Async function to bridge calls to Anki's main thread
    """
    register_tools(mcp, call_main_thread)
