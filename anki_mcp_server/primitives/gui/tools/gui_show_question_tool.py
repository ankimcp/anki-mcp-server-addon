# primitives/gui/tools/gui_show_question_tool.py
"""GUI Show Question tool - show the question side of the current card in review mode."""

from typing import Any, Callable, Coroutine
import logging

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _gui_show_question_handler() -> dict[str, Any]:
    """
    Handler for gui_show_question - show the question side of the current card.

    This function runs on the Qt MAIN THREAD and has direct access to mw.
    Shows the question side if in review mode, does nothing otherwise.

    Returns:
        dict: Result with structure:
            - success (bool): Whether the operation succeeded
            - inReview (bool): Whether user is currently in review mode
            - message (str): Human-readable result message
            - hint (str): Helpful hint about next steps

    Raises:
        RuntimeError: If Anki is not ready or reviewer is not available
    """
    from aqt import mw

    # Check if Anki is ready
    if mw is None or mw.col is None:
        raise RuntimeError("Anki not ready")

    # Check if reviewer is active with a card AND in review state
    # This matches AnkiConnect's guiReviewActive() implementation
    if not mw.reviewer or not mw.reviewer.card or mw.state != "review":
        return {
            "success": True,
            "inReview": False,
            "message": "Not in review mode - question cannot be shown",
            "hint": "Start reviewing a deck in Anki to use this tool.",
        }

    try:
        # Show the question side using the reviewer's internal method
        # This is the same approach used by AnkiConnect
        mw.reviewer._showQuestion()

        logger.info("Question side shown successfully")

        return {
            "success": True,
            "inReview": True,
            "message": "Question side is now displayed",
            "hint": "Use guiCurrentCard to get the card details, or guiShowAnswer to reveal the answer.",
        }

    except Exception as e:
        logger.error(f"Failed to show question: {e}", exc_info=True)
        raise RuntimeError(f"Failed to show question: {str(e)}")


# Register the handler
register_handler("gui_show_question", _gui_show_question_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_gui_show_question_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register gui_show_question tool with the MCP server."""

    @mcp.tool(
        description=(
            "Show the question side of the current card in review mode. "
            "Returns true if in review mode, false otherwise. "
            "CRITICAL: This tool is ONLY for note editing/creation workflows when user needs to view "
            "the question side to verify content. NEVER use this for conducting review sessions. "
            "Use the dedicated review tools (present_card) instead. "
            "IMPORTANT: Only use when user explicitly requests showing the question."
        )
    )
    async def gui_show_question() -> dict[str, Any]:
        """Show the question side of the current card in review mode.

        Displays the question (front) side of the card currently shown in Anki's reviewer.
        This is useful for note editing workflows where you need to flip back to the
        question side after viewing the answer.

        Returns:
            Dictionary containing:
            - success (bool): Whether the operation succeeded
            - inReview (bool): Whether user is currently in review mode
            - message (str): Human-readable result message
            - hint (str): Helpful hint about next steps

        Raises:
            Exception: If the main thread returns an error response

        Examples:
            >>> # Show question side while in review
            >>> result = await gui_show_question()
            >>> if result['inReview']:
            ...     print(result['message'])  # "Question side is now displayed"
            ... else:
            ...     print(result['message'])  # "Not in review mode..."
            >>>
            >>> # Not in review mode
            >>> result = await gui_show_question()
            >>> print(result['hint'])  # "Start reviewing a deck in Anki..."

        Note:
            - This operation runs on Anki's main thread and manipulates the reviewer UI
            - Does nothing if not currently in review mode
            - ONLY use this for note editing workflows, NOT for conducting reviews
            - The question side is what's shown before the user clicks "Show Answer"
            - GUI must be visible and in review mode for this to work
            - This is idempotent - calling it multiple times has the same effect
        """
        return await call_main_thread("gui_show_question", {})
