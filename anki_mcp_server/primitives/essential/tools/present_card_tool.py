"""Present card tool - MCP tool and handler in one file.

This tool retrieves card content for review, showing question and optionally answer.
Follows the standard Anki review workflow: show question first, then reveal answer.

Thread Safety:
    - Tool handler runs in background thread
    - Actual card retrieval happens on main thread via queue bridge
"""

from typing import Any, Callable, Coroutine
import logging

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _present_card_handler(card_id: int, show_answer: bool) -> dict[str, Any]:
    """
    Retrieve a card's content for review.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.
    This is a READ operation that retrieves card information without modifying state.

    Args:
        card_id: The ID of the card to retrieve
        show_answer: Whether to include the answer/back content in the response

    Returns:
        dict: Response containing:
            - success (bool): True if card was retrieved successfully
            - card (dict): Card information containing:
                - card_id (int): The card's unique identifier
                - deck_name (str): Name of the deck containing this card
                - question (str): The front/question content of the card (HTML)
                - answer (str, optional): The back/answer content (HTML), only included
                    when show_answer=True
                - note_type (str): The note type name (e.g., "Basic", "Cloze")
                - due (int): Due date/position in queue
                - interval (int): Current review interval in days
                - ease_factor (int): Ease factor in permille (e.g., 2500 = 250%)
                - reviews (int): Number of times this card has been reviewed
                - lapses (int): Number of times the card has been forgotten

    Raises:
        RuntimeError: If collection is not loaded
        ValueError: If card not found
    """
    from aqt import mw

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    # Get the card
    try:
        card = mw.col.get_card(card_id)
    except KeyError:
        raise ValueError(f"Card not found with ID {card_id}")
    except Exception as e:
        logger.error(f"Unexpected error retrieving card {card_id}: {e}", exc_info=True)
        raise ValueError(f"Failed to retrieve card {card_id}: {str(e)}")

    # Get the note associated with the card
    note = card.note()

    # Get deck name
    deck = mw.col.decks.get(card.did)
    deck_name = deck["name"] if deck else "Unknown"

    # Get model/note type name
    model = note.note_type()
    note_type = model["name"] if model else "Unknown"

    # Get question (front) content
    # Use card's question() method which renders the front template
    question_html = card.question()

    # Build card info response
    card_info = {
        "card_id": card.id,
        "deck_name": deck_name,
        "question": question_html,
        "note_type": note_type,
        "due": card.due,
        "interval": card.ivl,
        "ease_factor": card.factor,
        "reviews": card.reps,
        "lapses": card.lapses,
    }

    # Conditionally include answer if requested
    if show_answer:
        # Use card's answer() method which renders the back template
        answer_html = card.answer()
        card_info["answer"] = answer_html

    return {
        "success": True,
        "card": card_info,
    }


# Register handler at import time
register_handler("present_card", _present_card_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_present_card_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register card presentation tools with the MCP server.

    Args:
        mcp: FastMCP server instance
        call_main_thread: Bridge function to execute on main thread
    """

    @mcp.tool(
        description="Retrieve a card's content for review. WORKFLOW: 1) Show question, 2) Wait for user answer, 3) Show answer with show_answer=true, 4) Evaluate and suggest rating (1-4), 5) Wait for user confirmation (\"ok\"/\"next\" = accept, or they provide different rating), 6) Only then use rate_card"
    )
    async def present_card(card_id: int, show_answer: bool = False) -> dict[str, Any]:
        """Retrieve a card's content for review, showing question and optionally answer.

        Presents a card for review following the standard Anki review workflow.
        First call should show only the question (show_answer=False), then after
        the user provides their answer, call again with show_answer=True to reveal
        the answer content. This allows for active recall testing before seeing
        the correct answer.

        Args:
            card_id: The ID of the card to retrieve. Obtain this from get_due_cards
                or other card listing operations.
            show_answer: Whether to include the answer/back content in the response.
                Default is False. Set to True after the user has attempted to answer
                to reveal the back of the card.

        Returns:
            Dictionary containing:
            - success (bool): True if card was retrieved successfully
            - card (dict): Card information containing:
                - card_id (int): The card's unique identifier
                - deck_name (str): Name of the deck containing this card
                - question (str): The front/question content of the card (HTML)
                - answer (str, optional): The back/answer content (HTML), only included
                    when show_answer=True
                - note_type (str): The note type name (e.g., "Basic", "Cloze")
                - due (int): Due date/position in queue
                - interval (int): Current review interval in days
                - ease_factor (int): Ease factor in permille (e.g., 2500 = 250%)
                - reviews (int): Number of times this card has been reviewed
                - lapses (int): Number of times the card has been forgotten

        Raises:
            Exception: If the main thread returns an error response or card not found

        Example:
            >>> # Step 1: Show only the question
            >>> result = await present_card(card_id=1234567890, show_answer=False)
            >>> print(result['card']['question'])  # "What is the capital of France?"
            >>>
            >>> # Step 2: User attempts to answer, then reveal the answer
            >>> result = await present_card(card_id=1234567890, show_answer=True)
            >>> print(result['card']['answer'])  # "Paris"
            >>>
            >>> # Now evaluate and rate using rate_card tool

        Note:
            - This operation accesses the Anki collection on the main thread
            - Follow the complete workflow: question -> answer -> evaluation -> rating
            - The question and answer may contain HTML formatting
            - Always wait for user confirmation before proceeding to rate_card
            - Card must exist and be accessible in the current collection
            - Use this in combination with get_due_cards and rate_card for full review
        """
        return await call_main_thread("present_card", {"card_id": card_id, "show_answer": show_answer})
