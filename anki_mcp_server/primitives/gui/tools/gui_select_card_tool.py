# primitives/gui/tools/gui_select_card_tool.py
"""GUI Select Card tool - select a specific card in the Card Browser."""

from typing import Any, Callable, Coroutine
import logging

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _gui_select_card_handler(card_id: int) -> dict[str, Any]:
    """
    Select a specific card in an open Card Browser window.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.

    Args:
        card_id: Card ID to select in the browser

    Returns:
        dict: Result with structure:
            - success (bool): Whether the operation succeeded
            - selected (bool): True if card was selected, False if browser not open
            - cardId (int): The card ID that was selected (or attempted)
            - browserOpen (bool): Whether the browser is currently open
            - message (str): Human-readable result message
            - hint (str): Helpful hint about next steps

    Raises:
        RuntimeError: If Anki is not ready
    """
    from aqt import mw, dialogs

    # Check if Anki is ready
    if mw is None or mw.col is None:
        raise RuntimeError("Anki not ready")

    try:
        # Get browser if open
        browser = dialogs._dialogs.get("Browser", [None, None])[1]

        if browser is None:
            return {
                "success": True,
                "selected": False,
                "cardId": card_id,
                "browserOpen": False,
                "message": "Card Browser is not open",
                "hint": "Use guiBrowse to open the Card Browser first, then try selecting the card again.",
            }

        # Verify the card exists
        try:
            card = mw.col.get_card(card_id)
            if not card:
                return {
                    "success": False,
                    "selected": False,
                    "cardId": card_id,
                    "browserOpen": True,
                    "error": f"Card {card_id} not found",
                    "hint": "Card ID not found. Make sure the card exists and is visible in the current browser search.",
                }
        except Exception:
            return {
                "success": False,
                "selected": False,
                "cardId": card_id,
                "browserOpen": True,
                "error": f"Card {card_id} not found",
                "hint": "Card ID not found. Make sure the card exists and is visible in the current browser search.",
            }

        # Select the card in the browser
        # Modern Anki (2.1.45+) has browser.table.select_cards()
        if hasattr(browser, 'table') and hasattr(browser.table, 'select_cards'):
            # Modern Anki - use table.select_cards() with card IDs
            browser.table.select_cards([card_id])
            logger.debug(f"Selected card {card_id} using table.select_cards()")
        elif hasattr(browser, 'table') and hasattr(browser.table, 'select_rows'):
            # Alternative: try select_rows with card IDs
            # In some Anki versions, select_rows can accept card IDs
            try:
                browser.table.select_rows([card_id])
                logger.debug(f"Selected card {card_id} using table.select_rows()")
            except Exception as e:
                logger.warning(f"select_rows failed with card ID: {e}, card may not be in current view")
                raise
        else:
            # Fallback for older/unknown Anki versions
            logger.warning("Browser doesn't have expected selection methods, card selection may not work")
            # Return success=False to indicate unsupported browser version
            return {
                "success": False,
                "selected": False,
                "cardId": card_id,
                "browserOpen": True,
                "error": "Browser card selection not supported in this Anki version",
                "hint": "This Anki version may not support programmatic card selection in the browser.",
            }

        # Activate the browser window to make selection visible
        browser.activateWindow()

        return {
            "success": True,
            "selected": True,
            "cardId": card_id,
            "browserOpen": True,
            "message": f"Successfully selected card {card_id} in Card Browser",
            "hint": "The card is now selected. Use guiEditNote to edit the associated note, or guiSelectedNotes to get note IDs.",
        }

    except Exception as e:
        logger.error(f"Failed to select card in browser: {e}", exc_info=True)
        browser_open = browser is not None if 'browser' in locals() else False
        return {
            "success": False,
            "selected": False,
            "cardId": card_id,
            "browserOpen": browser_open,
            "error": f"Failed to select card: {str(e)}",
            "hint": "Make sure Anki is running, the Card Browser is open, and the card ID is valid and visible in the current search.",
        }


# Register handler at import time
register_handler("gui_select_card", _gui_select_card_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_gui_select_card_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register gui_select_card tool with the MCP server."""

    @mcp.tool(
        description=(
            "Select a specific card in an open Card Browser window. "
            "Returns true if browser is open and card was selected, false if browser is not open. "
            "IMPORTANT: Only use when user explicitly requests selecting a card in the browser. "
            "This tool is for note editing/creation workflows, NOT for review sessions. "
            "The Card Browser must already be open (use guiBrowse first)."
        )
    )
    async def gui_select_card(card_id: int) -> dict[str, Any]:
        """Select a specific card in the Card Browser.

        Selects a specific card in an already-open Card Browser window.
        The browser must be open and the card must be visible in the current search results.

        Args:
            card_id: Card ID to select in the browser (get from guiBrowse results).
                Must be a positive integer representing a valid card.

        Returns:
            Dictionary containing:
            - success (bool): Whether the operation succeeded
            - selected (bool): True if card was selected, False if browser not open
            - cardId (int): The card ID that was selected (or attempted)
            - browserOpen (bool): Whether the browser is currently open
            - message (str): Human-readable result message
            - hint (str): Helpful hint about next steps or error resolution
            - error (str): Error message (if failed)

        Raises:
            Exception: If the operation fails on the main thread

        Examples:
            Select a card after opening the browser:
            >>> browse_result = await gui_browse(query="deck:Spanish")
            >>> if browse_result['cardCount'] > 0:
            ...     card_id = browse_result['cardIds'][0]
            ...     result = await gui_select_card(card_id=card_id)
            ...     if result['selected']:
            ...         print(f"Selected card {card_id}")

            Handle browser not open:
            >>> result = await gui_select_card(card_id=1234567890)
            >>> if not result['browserOpen']:
            ...     print(result['hint'])  # "Use guiBrowse to open the Card Browser first..."

            Handle card not found:
            >>> result = await gui_select_card(card_id=9999999999)
            >>> if not result['success']:
            ...     print(result['error'])  # "Card 9999999999 not found"

        Note:
            - This operation runs on Anki's main thread
            - The Card Browser must already be open (use guiBrowse to open it)
            - The card must exist and be visible in the current browser search
            - If the card is not in the current search results, selection may fail
            - Only use this when the user explicitly requests selecting a card
            - After selecting, you can use guiEditNote to edit the card's note
            - Use guiSelectedNotes to get the note IDs of selected cards
            - Works with Anki 2.1.45+ (the minimum supported version for this add-on)
        """
        return await call_main_thread("gui_select_card", {"card_id": card_id})
