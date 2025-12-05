# primitives/gui/tools/gui_deck_browser_tool.py
"""GUI Deck Browser tool - opens the Anki Deck Browser dialog."""

from typing import Any, Callable, Coroutine
import logging

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _gui_deck_browser_handler() -> dict[str, Any]:
    """
    Open the Anki Deck Browser dialog.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.

    Opens the main Deck Browser window in Anki, showing all decks in the collection.
    This is the default view where users can see their deck list, select decks to
    study, and manage their deck structure.

    Returns:
        dict: Response with structure:
            - success (bool): Whether the Deck Browser was opened successfully
            - message (str): Human-readable result message
            - hint (str): Helpful hint about what the user sees in the GUI

    Raises:
        RuntimeError: If collection is not loaded or main window not available

    Note:
        - Opens the Deck Browser GUI window in Anki
        - Only use when explicitly requested by the user
        - This is for deck management, not for starting review sessions
        - The dialog shows all decks with their due card counts
        - Uses mw.moveToState('deckBrowser') to change UI state
    """
    from aqt import mw

    # Check if main window is available
    if mw is None:
        raise RuntimeError("Anki main window not available")

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    try:
        # Move to deck browser state
        # This is the main deck list view in Anki
        mw.moveToState('deckBrowser')

        return {
            "success": True,
            "message": "Deck Browser opened successfully",
            "hint": "All decks are now visible in the Anki GUI. User can select a deck to study or manage.",
        }

    except Exception as e:
        logger.error(f"Failed to open Deck Browser: {e}")
        return {
            "success": False,
            "error": f"Failed to open Deck Browser: {str(e)}",
            "hint": "Make sure Anki is running and the GUI is visible",
        }


# Register handler at import time
register_handler("gui_deck_browser", _gui_deck_browser_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_gui_deck_browser_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register gui_deck_browser tool with the MCP server."""

    @mcp.tool(
        description=(
            "Open Anki Deck Browser dialog showing all decks. "
            "IMPORTANT: Only use when user explicitly requests opening the deck browser. "
            "This tool is for deck management and organization workflows, NOT for review sessions. "
            "Use this when user wants to see all decks or manage deck structure."
        )
    )
    async def gui_deck_browser() -> dict[str, Any]:
        """Open the Anki Deck Browser dialog.

        Opens the Anki Deck Browser window which displays all decks in the collection.
        This is the main view in Anki where users can see their deck list, select decks
        to study, and manage deck organization. Use this when the user wants to view
        or manage their deck structure.

        Returns:
            Dictionary containing:
            - success (bool): Whether the Deck Browser was opened successfully
            - message (str): Human-readable result message
            - hint (str): Helpful hint about what the user sees in the GUI
            - error (str): Error message (if failed)

        Raises:
            Exception: If opening the Deck Browser fails on the main thread

        Note:
            - This opens the Deck Browser GUI window in Anki
            - Only use when explicitly requested by the user
            - This is for deck management, not for starting review sessions
            - The dialog shows all decks with their due card counts
            - User can interact with the GUI to select decks or manage structure
            - Make sure Anki is running and the GUI is visible

        Examples:
            Open the Deck Browser:
            >>> result = await gui_deck_browser()
            >>> print(result["message"])
            "Deck Browser opened successfully"

            Handle the response:
            >>> result = await gui_deck_browser()
            >>> if result["success"]:
            ...     print(result["hint"])
            ... else:
            ...     print(f"Failed: {result['error']}")
        """
        return await call_main_thread("gui_deck_browser", {})
