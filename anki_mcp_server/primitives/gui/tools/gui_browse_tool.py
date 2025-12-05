# primitives/gui/tools/gui_browse_tool.py
"""GUI Browse tool - open Anki Card Browser and search for cards."""

from typing import Any, Callable, Coroutine, Optional
import logging

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _gui_browse_handler(
    query: str,
    reorder_order: Optional[str] = None,
    reorder_column: Optional[str] = None
) -> dict[str, Any]:
    """
    Open Anki Card Browser and search for cards.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.

    Args:
        query: Anki search query using standard syntax
        reorder_order: Optional sort order ("ascending" or "descending") - NOT YET IMPLEMENTED
        reorder_column: Optional column ID to sort by - NOT YET IMPLEMENTED

    Returns:
        dict: Result with structure:
            - success (bool): Whether the operation succeeded
            - cardIds (list[int]): Array of card IDs matching the query
            - cardCount (int): Number of cards found
            - query (str): The search query that was executed
            - message (str): Human-readable result message
            - hint (str): Helpful hint about next steps

    Raises:
        RuntimeError: If collection is not loaded
    """
    from aqt import mw, dialogs

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    try:
        # Open the browser with the query
        browser = dialogs.open('Browser', mw)
        browser.activateWindow()

        # Set the search query
        if query:
            browser.form.searchEdit.lineEdit().setText(query)
            # Trigger the search (different methods in different Anki versions)
            if hasattr(browser, 'onSearch'):
                browser.onSearch()
            else:
                browser.onSearchActivated()

        # Find cards matching the query
        card_ids = mw.col.find_cards(query)
        card_count = len(card_ids)

        # TODO: Implement reordering if both parameters are provided
        # The Anki browser API for programmatic sorting is not well documented
        # For now, we log a warning if reordering was requested
        if reorder_order and reorder_column:
            logger.warning(
                f"Browser reordering requested ({reorder_column}, {reorder_order}) "
                "but not yet implemented. User can manually sort in the browser UI."
            )

        # Build success message
        if card_count == 0:
            message = f'Browser opened with query "{query}" - no cards found'
            hint = "Try a different query or check if the deck/tags exist"
        elif card_count == 1:
            message = f'Browser opened with query "{query}" - found 1 card'
            hint = "You can now edit or review the card in the browser"
        else:
            message = f'Browser opened with query "{query}" - found {card_count} cards'
            hint = "You can now select, edit, or export these cards in the browser"

        return {
            "success": True,
            "cardIds": card_ids,
            "cardCount": card_count,
            "query": query,
            "message": message,
            "hint": hint,
        }

    except Exception as e:
        logger.error(f"Failed to open browser: {e}")
        return {
            "success": False,
            "error": f"Failed to open browser: {str(e)}",
            "query": query,
            "hint": "Make sure Anki is running and the GUI is visible",
        }


# Register handler at import time
register_handler("gui_browse", _gui_browse_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_gui_browse_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register gui_browse tool with the MCP server."""

    @mcp.tool(
        description=(
            "Open Anki Card Browser and search for cards using Anki query syntax. "
            "Returns array of card IDs found. IMPORTANT: Only use when user explicitly "
            "requests opening the browser. This tool is for note editing/creation workflows, "
            "NOT for review sessions. Use this to find and select cards/notes that need editing."
        )
    )
    async def gui_browse(
        query: str,
        reorder_order: str | None = None,
        reorder_column: str | None = None
    ) -> dict[str, Any]:
        """Open Anki Card Browser and search for cards.

        Opens the Anki Card Browser window and performs a search using the provided query.
        Optionally reorders the displayed cards by a specified column and sort order.

        Args:
            query: Anki search query using standard syntax. Examples:
                - "deck:Spanish tag:verb" - cards from Spanish deck with verb tag
                - "is:due" - cards that are due for review
                - "added:7" - cards added in the last 7 days
                - "deck:MyDeck is:new" - new cards in MyDeck
                - "tag:important -is:suspended" - important cards that aren't suspended
            reorder_order: Optional sort order, either "ascending" or "descending".
                Must be provided together with reorder_column.
                NOTE: Reordering is not yet implemented - cards can be sorted manually in UI.
            reorder_column: Optional column ID to sort by. Examples:
                - "noteFld" - note field (first field)
                - "noteCrt" - note creation time
                - "cardDue" - card due date
                - "cardIvl" - card interval
                - "cardEase" - card ease factor
                Must be provided together with reorder_order.
                NOTE: Reordering is not yet implemented - cards can be sorted manually in UI.

        Returns:
            Dictionary containing:
            - success (bool): Whether the operation succeeded
            - cardIds (list[int]): Array of card IDs matching the query
            - cardCount (int): Number of cards found
            - query (str): The search query that was executed
            - message (str): Human-readable result message
            - hint (str): Helpful hint about next steps or error resolution
            - error (str): Error message (if failed)

        Raises:
            Exception: If opening the browser fails on the main thread

        Examples:
            Open browser with all cards in a deck:
            >>> result = await gui_browse(query="deck:Spanish")
            >>> print(f"Found {result['cardCount']} cards")

            Search for due cards and sort by due date:
            >>> result = await gui_browse(
            ...     query="is:due",
            ...     reorder_order="ascending",
            ...     reorder_column="cardDue"
            ... )

            Find recently added cards:
            >>> result = await gui_browse(query="added:7")
            >>> if result['cardCount'] == 0:
            ...     print(result['hint'])

        Note:
            - This operation runs on Anki's main thread and opens the GUI
            - The browser window must be visible for this to work
            - Invalid queries will return an error with a helpful hint
            - If no cards are found, cardIds will be an empty array
            - Only use this when the user explicitly requests opening the browser
            - For programmatic card searching without GUI, use findCards instead
        """
        # Prepare arguments for main thread
        arguments: dict[str, Any] = {"query": query}

        # Add reordering parameters (even though not implemented yet)
        if reorder_order and reorder_column:
            arguments["reorder_order"] = reorder_order
            arguments["reorder_column"] = reorder_column

        return await call_main_thread("gui_browse", arguments)
