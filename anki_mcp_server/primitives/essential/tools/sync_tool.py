"""Sync tool - MCP tool and handler in one file."""
from typing import Any, Callable, Coroutine
import logging

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _sync_handler() -> dict[str, Any]:
    """
    Trigger Anki sync with AnkiWeb.

    This function runs on the Qt MAIN THREAD and has direct access to mw.
    This method triggers the sync process but returns immediately.
    Anki's sync is asynchronous and runs in the background.

    Returns:
        dict: Sync result with structure:
            - status (str): "started" or "error"
            - message (str): Human-readable status message

    Raises:
        RuntimeError: If collection is not loaded

    Note:
        This implementation has some limitations:
        1. Returns immediately without waiting for sync to complete
        2. Does not check if sync is already in progress
        3. Uses mw.onSync() which is a UI callback - may not be the
           most appropriate API for programmatic sync triggering
        4. syncKey check might not be accurate for all Anki versions
    """
    from aqt import mw

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    # Check if sync is configured
    # Note: This check might not be accurate for all Anki versions
    # In modern Anki (2.1.50+), sync config is more complex
    if not mw.pm.profile.get("syncKey"):
        return {
            "status": "error",
            "message": "Sync not configured - please sync manually first to set up AnkiWeb credentials"
        }

    # Trigger sync - this is async in Anki, returns immediately
    # Note: mw.onSync() is the UI callback for the sync button
    # There might be a more appropriate programmatic API
    mw.onSync()

    return {
        "status": "started",
        "message": "Sync started"
    }


# Register handler at import time
register_handler("sync", _sync_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_sync_tools(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register sync-related tools with the MCP server."""

    @mcp.tool(
        description="Synchronize local Anki collection with AnkiWeb. IMPORTANT: Always sync at the START of a review session (before getting cards) and at the END when user indicates they are done. This ensures data consistency across devices."
    )
    async def sync() -> dict[str, Any]:
        """Trigger Anki sync with AnkiWeb.

        Initiates a full sync between the local Anki collection and AnkiWeb.
        This is equivalent to clicking the sync button in Anki's main window.

        Returns:
            Dictionary with sync result containing:
            - status: "started" or "error"
            - message: Human-readable result message

        Raises:
            Exception: If sync fails on the main thread

        Note:
            This operation may take several seconds to complete for large
            collections. The sync runs asynchronously in Anki, so this
            function returns immediately after triggering the sync.
        """
        return await call_main_thread("sync", {})
