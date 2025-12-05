# primitives/essential/resources/system_info_resource.py
"""System info resource - provides information about the Anki MCP server and environment."""

from typing import Any, Callable, Coroutine
import logging

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _system_info_handler() -> dict[str, Any]:
    """
    Get system information about Anki and the MCP server.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.

    Returns:
        dict: System information including:
            - anki_version (str): Anki application version
            - anki_build (int): Anki build number
            - profile_name (str): Current Anki profile name
            - collection_path (str): Path to the collection database
            - scheduler_version (int): Scheduler version (1, 2, or 3)
            - mcp_server_version (str): MCP server addon version
            - python_version (str): Python interpreter version
            - platform (str): Operating system platform

    Raises:
        RuntimeError: If collection is not loaded
    """
    import sys
    import platform
    from aqt import mw

    # Check if collection is loaded
    if mw is None or mw.col is None:
        raise RuntimeError("Collection not loaded")

    try:
        # Get Anki version info
        from anki.buildinfo import version as anki_version, buildhash

        # Get scheduler version
        scheduler_version = mw.col.sched_ver()

        # Get profile name
        profile_name = mw.pm.name if mw.pm else "Unknown"

        # Get collection path
        col_path = str(mw.col.path) if mw.col.path else "Unknown"

        return {
            "success": True,
            "anki_version": anki_version,
            "anki_build": buildhash[:8] if buildhash else "unknown",
            "profile_name": profile_name,
            "collection_path": col_path,
            "scheduler_version": scheduler_version,
            "mcp_server_version": "0.1.0",  # TODO: Read from manifest
            "python_version": sys.version.split()[0],
            "platform": platform.system(),
        }

    except ImportError:
        # Fallback for older Anki versions
        return {
            "success": True,
            "anki_version": "unknown",
            "anki_build": "unknown",
            "profile_name": mw.pm.name if mw.pm else "Unknown",
            "collection_path": str(mw.col.path) if mw.col.path else "Unknown",
            "scheduler_version": mw.col.sched_ver(),
            "mcp_server_version": "0.1.0",
            "python_version": sys.version.split()[0],
            "platform": platform.system(),
        }


# Register handler at import time
register_handler("system_info", _system_info_handler)


# ============================================================================
# MCP RESOURCE - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_system_info_resource(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register the system_info resource with the MCP server."""

    @mcp.resource("anki://system-info")
    async def system_info() -> dict[str, Any]:
        """Get Anki system information.

        Provides information about the Anki installation, current profile,
        collection, and MCP server environment.

        Returns:
            Dictionary containing:
            - anki_version (str): Anki application version
            - anki_build (str): Anki build hash
            - profile_name (str): Current Anki profile name
            - collection_path (str): Path to the collection database
            - scheduler_version (int): Scheduler version (1, 2, or 3)
            - mcp_server_version (str): MCP server addon version
            - python_version (str): Python interpreter version
            - platform (str): Operating system platform

        Example:
            >>> info = await read_resource("anki://system-info")
            >>> print(f"Anki {info['anki_version']} on {info['platform']}")

        Note:
            - This resource is read-only
            - Collection must be loaded for full information
            - Scheduler version 3 is the V3 scheduler introduced in Anki 2.1.45
        """
        return await call_main_thread("system_info", {})
