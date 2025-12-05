# primitives/gui/tools/gui_show_answer_tool.py
"""GUI Show Answer tool - shows the answer side of the current card in review mode."""

from typing import Any, Callable, Coroutine
import logging

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _gui_show_answer_handler() -> dict[str, Any]:
    """
    Show the answer side of the current card in review mode.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.

    Triggers the display of the answer side for the card currently being reviewed.
    This simulates the user clicking the "Show Answer" button or pressing spacebar
    during review. Only works when actively reviewing a card (before the answer
    has been shown).

    Returns:
        dict: Response with structure:
            - success (bool): True if operation succeeded
            - inReview (bool): Whether user is currently in review mode
            - message (str): Human-readable result message
            - hint (str, optional): Usage hint or suggestion

    Raises:
        RuntimeError: If collection is not loaded or main window not available

    Note:
        - Only works when in review mode with a card displayed
        - Does nothing if answer is already shown
        - This is for note editing workflows, NOT for conducting reviews
        - Uses mw.reviewer._showAnswer() to trigger answer display
        - Returns inReview=False if not currently reviewing
    """
    from aqt import mw

    # Check if main window is available
    if mw is None:
        raise RuntimeError("Anki main window not available")

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    # Check if reviewer is active and has a card
    if not mw.reviewer or not mw.reviewer.card:
        return {
            "success": True,
            "inReview": False,
            "message": "Not in review mode - answer cannot be shown",
            "hint": "Start reviewing a deck in Anki to use this tool.",
        }

    try:
        # Show the answer using Anki's internal method
        # This triggers the same action as clicking "Show Answer" button
        mw.reviewer._showAnswer()

        return {
            "success": True,
            "inReview": True,
            "message": "Answer side is now displayed",
            "hint": "Use guiCurrentCard to get full card details including the answer content.",
        }

    except Exception as e:
        logger.error(f"Failed to show answer: {e}", exc_info=True)
        raise RuntimeError(f"Failed to show answer: {str(e)}")


# Register handler at import time
register_handler("gui_show_answer", _gui_show_answer_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_gui_show_answer_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register gui_show_answer tool with the MCP server."""

    @mcp.tool(
        description=(
            "Show the answer side of the current card in review mode. "
            "Returns true if in review mode, false otherwise. "
            "CRITICAL: This tool is ONLY for note editing/creation workflows when user needs to "
            "view the answer side to verify content. NEVER use this for conducting review sessions. "
            "Use the dedicated review tools (present_card) instead. "
            "IMPORTANT: Only use when user explicitly requests showing the answer."
        )
    )
    async def gui_show_answer() -> dict[str, Any]:
        """Show the answer side of the current card in review mode.

        Triggers the display of the answer side for the card currently being reviewed.
        This simulates the user clicking the "Show Answer" button or pressing spacebar
        during review. Useful for note editing workflows where you need to see the
        answer content to verify changes.

        Returns:
            Dictionary containing:
            - success (bool): True if operation succeeded
            - inReview (bool): Whether user is currently in review mode
            - message (str): Human-readable result message
            - hint (str, optional): Usage hint or suggestion

        Raises:
            Exception: If the main thread returns an error response

        Examples:
            Show answer in review mode:
            >>> result = await gui_show_answer()
            >>> if result['inReview']:
            ...     print(result['message'])  # "Answer side is now displayed"
            ...     # Now use gui_current_card() to get the answer content
            ... else:
            ...     print(result['message'])  # "Not in review mode - answer cannot be shown"

            Not in review mode:
            >>> result = await gui_show_answer()
            >>> print(result['inReview'])  # False
            >>> print(result['hint'])  # "Start reviewing a deck in Anki to use this tool."

        Note:
            - This operation accesses Anki's reviewer state on the main thread
            - Only works when actively reviewing a card (before answer is shown)
            - Does nothing if answer is already displayed
            - ONLY use this for note editing workflows, NOT for conducting reviews
            - After showing answer, use gui_current_card() to get the answer content
            - Make sure Anki is running, GUI is visible, and in review mode
            - Returns success=True with inReview=False if not currently reviewing
        """
        return await call_main_thread("gui_show_answer", {})
