# primitives/essential/tools/get_due_cards_tool.py
"""Get due cards tool - retrieve cards that are due for review."""

from typing import Any, Callable, Coroutine
import logging

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _get_due_cards_handler(deck_name: str | None, limit: int) -> dict[str, Any]:
    """
    Retrieve cards that are due for review.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.
    Searches for cards that are currently due for review, optionally filtered by deck.
    Returns simplified card information including front/back content and scheduling data.

    Args:
        deck_name: Optional deck name to filter cards. If None, searches all decks.
        limit: Maximum number of cards to return (1-50, default 10).

    Returns:
        dict: Response with structure:
            - success (bool): True if operation succeeded
            - cards (list): List of simplified card objects, each containing:
                - cardId (int): Unique card identifier
                - front (str): Front side content (question/prompt)
                - back (str): Back side content (answer)
                - deckName (str): Name of the deck containing this card
                - modelName (str): Note type/model name
                - due (int): Due date/position in review queue
                - interval (int): Current interval in days
                - factor (int): Ease factor (e.g., 2500 = 250%)
            - total (int): Total number of due cards found (before limit applied)
            - returned (int): Number of cards actually returned (after limit)
            - message (str): Human-readable result message

    Raises:
        RuntimeError: If collection is not loaded
        Exception: If card search or retrieval fails

    Example:
        >>> _get_due_cards_handler(deck_name=None, limit=10)
        {
            'success': True,
            'cards': [
                {
                    'cardId': 1234567890,
                    'front': 'What is the capital of France?',
                    'back': 'Paris',
                    'deckName': 'Geography',
                    'modelName': 'Basic',
                    'due': 12345,
                    'interval': 7,
                    'factor': 2500
                }
            ],
            'total': 25,
            'returned': 1,
            'message': 'Found 25 due cards, returning 1'
        }

    Note:
        - Uses Anki's internal search with "is:due" query
        - Escapes deck names properly for search queries
        - Extracts front/back from note fields (prioritizes "Front"/"Back" fields)
        - Falls back to empty strings if fields not found
        - Does NOT include suspended or buried cards
        - Limit is capped at 50 for performance
    """
    from aqt import mw

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    # Validate and cap limit
    card_limit = max(1, min(limit, 50))

    # Build search query for due cards
    query = "is:due"
    if deck_name:
        # Escape special characters in deck name for Anki search
        escaped_deck_name = deck_name.replace('"', '\\"')
        query = f'"deck:{escaped_deck_name}" {query}'

    # Find due cards using Anki's collection API
    try:
        card_ids = mw.col.find_cards(query)
    except Exception as e:
        raise Exception(f"Failed to find due cards: {str(e)}")

    if len(card_ids) == 0:
        return {
            "success": True,
            "message": "No cards are due for review",
            "cards": [],
            "total": 0,
            "returned": 0,
        }

    # Limit the number of cards
    selected_card_ids = card_ids[:card_limit]

    # Get detailed information for selected cards
    due_cards = []
    for card_id in selected_card_ids:
        try:
            card = mw.col.get_card(card_id)
            note = card.note()

            # Extract front and back from note fields
            # Try to find "Front" and "Back" fields first
            fields_dict = dict(note.items())
            front = fields_dict.get("Front", "")
            back = fields_dict.get("Back", "")

            # If no Front/Back fields, try to get first two fields
            if not front and not back:
                field_values = list(fields_dict.values())
                front = field_values[0] if len(field_values) > 0 else ""
                back = field_values[1] if len(field_values) > 1 else ""

            # Get deck name
            deck = mw.col.decks.get(card.did)
            deck_name_str = deck["name"] if deck else "Unknown"

            # Get model name
            model = note.note_type()
            model_name = model["name"] if model else "Unknown"

            due_cards.append({
                "cardId": card.id,
                "front": front,
                "back": back,
                "deckName": deck_name_str,
                "modelName": model_name,
                "due": card.due,
                "interval": card.ivl,
                "factor": card.factor,
            })
        except Exception as e:
            # Skip cards that can't be retrieved
            logger.warning(f"Could not retrieve card {card_id}: {e}")
            continue

    return {
        "success": True,
        "cards": due_cards,
        "total": len(card_ids),
        "returned": len(due_cards),
        "message": f"Found {len(card_ids)} due cards, returning {len(due_cards)}",
    }


# Register handler at import time
register_handler("get_due_cards", _get_due_cards_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_get_due_cards_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register get_due_cards tool with the MCP server."""

    @mcp.tool(
        description=(
            "Retrieve cards that are due for review from Anki. IMPORTANT: Use sync tool FIRST "
            "before getting cards to ensure latest data. After getting cards, use present_card "
            "to show them one by one to the user"
        )
    )
    async def get_due_cards(
        deck_name: str = None,
        limit: int = 10
    ) -> dict[str, Any]:
        """Retrieve cards that are due for review from Anki.

        Gets cards that are currently due for review, optionally filtered by deck name.
        Always sync first at the start of a review session to ensure you have the latest data.

        Args:
            deck_name: Specific deck name to get cards from. If not specified, gets cards
                from all decks. Must match the exact deck name.
            limit: Maximum number of cards to return. Must be between 1 and 50.
                Default is 10. Use smaller limits for faster response times.

        Returns:
            Dictionary containing:
            - success (bool): Whether the operation succeeded
            - cards (list): List of due card objects, each containing:
                - cardId (int): Unique card identifier
                - front (str): Front side of the card (question/prompt)
                - back (str): Back side of the card (answer)
                - deckName (str): Name of the deck containing this card
                - modelName (str): Note type/model name (e.g., "Basic", "Cloze")
                - due (int): Due date/position in queue
                - interval (int): Current interval in days
                - factor (int): Ease factor (2500 = 250%)
            - total (int): Total number of due cards found (before limit)
            - returned (int): Number of cards returned (after limit)
            - message (str): Human-readable result message

        Raises:
            Exception: If the main thread returns an error response

        Examples:
            >>> # Get due cards from all decks
            >>> result = await get_due_cards()
            >>> print(f"Found {result['total']} due cards")
            >>> for card in result['cards']:
            ...     print(f"Card {card['cardId']}: {card['front']}")
            >>>
            >>> # Get due cards from specific deck
            >>> result = await get_due_cards(deck_name="Spanish", limit=5)
            >>> print(f"Returned {result['returned']} of {result['total']} due cards")
            >>>
            >>> # Handle no due cards
            >>> result = await get_due_cards()
            >>> if result['total'] == 0:
            ...     print("No cards due for review")

        Note:
            - This operation accesses the Anki collection on the main thread
            - Always sync before calling this at the start of a review session
            - Cards are returned in the order they are due
            - The limit is capped at 50 for performance reasons
            - Front/back extraction attempts to find "Front"/"Back" fields first,
              then falls back to question/answer from card rendering
            - Suspended and buried cards are NOT included
        """
        # Validate and cap the limit
        card_limit = max(1, min(limit, 50))

        # Call main thread with parameters
        return await call_main_thread(
            "get_due_cards",
            {
                "deck_name": deck_name,
                "limit": card_limit
            }
        )
