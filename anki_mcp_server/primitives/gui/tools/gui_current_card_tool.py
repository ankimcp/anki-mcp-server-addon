# primitives/gui/tools/gui_current_card_tool.py
"""Get current card tool - retrieve information about the card currently displayed in review mode."""

from typing import Any, Callable, Coroutine
import logging

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


def _gui_current_card_handler() -> dict[str, Any]:
    """
    Handler for gui_current_card - get information about the current card in review mode.

    This is a READ operation - no edit session needed.

    Returns:
        dict: Response containing:
            - success (bool): True if operation succeeded
            - cardInfo (dict | None): Card information object or None if not in review, containing:
                - cardId (int): Unique card identifier
                - question (str): HTML question/front text
                - answer (str): HTML answer/back text
                - deckName (str): Name of the deck containing this card
                - modelName (str): Note type/model name
                - buttons (list[int]): Available rating buttons (e.g., [1, 2, 3, 4])
                - nextReviews (list[str]): Next review intervals for each button
                - fields (dict, optional): Card note fields with values and order
            - inReview (bool): Whether user is currently in review mode
            - message (str): Human-readable result message
            - hint (str, optional): Usage hint or suggestion

    Raises:
        RuntimeError: If collection is not loaded or reviewer is not available
    """
    from aqt import mw

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    # Check if reviewer is active and has a card
    if not mw.reviewer or not mw.reviewer.card:
        return {
            "success": True,
            "cardInfo": None,
            "inReview": False,
            "message": "Not currently in review mode",
            "hint": "Open a deck in Anki and start reviewing to see current card information.",
        }

    try:
        # Get the current card from the reviewer
        card = mw.reviewer.card
        card_id = card.id

        # Get the note associated with the card
        note = card.note()

        # Get deck name
        deck = mw.col.decks.get(card.did)
        deck_name = deck["name"] if deck else "Unknown"

        # Get model name
        model = note.note_type()
        model_name = model["name"] if model else "Unknown"

        # Get rendered question and answer HTML
        # Use the card's rendering methods to get the HTML as shown in review
        question_html = mw.reviewer.card.question()
        answer_html = mw.reviewer.card.answer()

        # Get available buttons and their intervals
        # The reviewer's _answerButtonList() returns a list of (ease, label) tuples
        buttons = []
        next_reviews = []

        # Get button count (Anki dynamically determines this based on card state)
        button_count = mw.col.sched.answerButtons(card)

        for ease in range(1, button_count + 1):
            buttons.append(ease)
            # Get the interval text for this ease
            interval_text = mw.col.sched.nextIvlStr(card, ease)
            next_reviews.append(interval_text)

        # Build fields dictionary with values and order
        fields_dict = {}
        for i, (field_name, field_value) in enumerate(note.items()):
            fields_dict[field_name] = {
                "value": field_value,
                "order": i
            }

        # Build card info response
        card_info = {
            "cardId": card_id,
            "question": question_html,
            "answer": answer_html,
            "deckName": deck_name,
            "modelName": model_name,
            "buttons": buttons,
            "nextReviews": next_reviews,
            "fields": fields_dict,
        }

        return {
            "success": True,
            "cardInfo": card_info,
            "inReview": True,
            "message": f'Current card: {card_id} from deck "{deck_name}"',
            "hint": "Use guiEditNote to edit the note associated with this card.",
        }

    except Exception as e:
        logger.error(f"Failed to get current card information: {e}", exc_info=True)
        raise RuntimeError(f"Failed to get current card information: {str(e)}")


# Register the handler
register_handler("gui_current_card", _gui_current_card_handler)


def register_gui_current_card_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register gui_current_card tool with the MCP server."""

    @mcp.tool(
        description=(
            "Get information about the current card displayed in review mode. "
            "Returns card details (question, answer, deck, model, card ID, buttons, next reviews, fields) "
            "or null if not currently in review. "
            "CRITICAL: This tool is ONLY for note editing/creation workflows when user needs to check "
            "what card is currently displayed in the GUI. NEVER use this for conducting review sessions. "
            "Use the dedicated review tools (get_due_cards, present_card, rate_card) instead. "
            "IMPORTANT: Only use when user explicitly requests current card information."
        )
    )
    async def gui_current_card() -> dict[str, Any]:
        """Get information about the current card displayed in review mode.

        Retrieves detailed information about the card currently shown in Anki's reviewer.
        This is useful for note editing workflows where you need to know which card is
        being displayed to the user.

        Returns:
            Dictionary containing:
            - success (bool): True if operation succeeded
            - cardInfo (dict | None): Card information object or None if not in review, containing:
                - cardId (int): Unique card identifier
                - question (str): HTML question text
                - answer (str): HTML answer text
                - deckName (str): Name of the deck containing this card
                - modelName (str): Note type/model name
                - buttons (list[int]): Available rating buttons (e.g., [1, 2, 3, 4])
                - nextReviews (list[str]): Next review intervals for each button (e.g., ["<1m", "<10m", "4d"])
                - fields (dict, optional): Card fields with values and order
                    Format: {field_name: {"value": str, "order": int}}
            - inReview (bool): Whether user is currently in review mode
            - message (str): Human-readable result message
            - hint (str, optional): Usage hint or suggestion

        Raises:
            Exception: If the main thread returns an error response

        Examples:
            >>> # Get current card information
            >>> result = await gui_current_card()
            >>> if result['inReview']:
            ...     card = result['cardInfo']
            ...     print(f"Current card ID: {card['cardId']}")
            ...     print(f"Deck: {card['deckName']}")
            ...     print(f"Question: {card['question']}")
            ...     print(f"Answer: {card['answer']}")
            ...     print(f"Available buttons: {card['buttons']}")
            ...     print(f"Next intervals: {card['nextReviews']}")
            ... else:
            ...     print("Not currently in review mode")
            >>>
            >>> # Not in review mode
            >>> result = await gui_current_card()
            >>> print(result['message'])  # "Not currently in review mode"
            >>> print(result['hint'])  # "Open a deck in Anki and start reviewing..."

        Note:
            - This operation accesses Anki's reviewer state on the main thread
            - Returns None for cardInfo if not currently in review mode
            - This is a read-only operation - does not modify any state
            - ONLY use this for note editing workflows, NOT for conducting reviews
            - The question and answer fields contain HTML markup
            - The buttons list typically contains 1-4 values representing rating options
            - The nextReviews list shows interval strings like "<1m", "10m", "4d", etc.
            - The fields dict is optional and may not be present for all cards
            - GUI must be visible and in review mode for this to return card data
        """
        return await call_main_thread("gui_current_card", {})
