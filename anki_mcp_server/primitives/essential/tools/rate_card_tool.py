"""Rate card tool - MCP tool and handler in one file."""
from typing import Any, Callable, Coroutine
import logging
from datetime import datetime

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _rate_card_handler(card_id: int, rating: int) -> dict[str, Any]:
    """
    Rate a card and update Anki's spaced repetition scheduling.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.
    This is a WRITE operation that modifies card scheduling.

    Args:
        card_id: The unique identifier of the card to rate
        rating: The user's rating (1-4):
            1 = Again (Failed)
            2 = Hard
            3 = Good
            4 = Easy

    Returns:
        dict: Result with structure:
            - success (bool): Whether the rating was successfully applied
            - card_id (int): The ID of the rated card
            - rating (int): The rating that was applied
            - message (str): Human-readable confirmation message
            - next_review (str, optional): When the card will next be due
            - new_interval (int, optional): The new interval in days

    Raises:
        RuntimeError: If collection is not loaded
        ValueError: If card is not found or rating is invalid
    """
    from aqt import mw
    from anki.errors import NotFoundError
    from anki.consts import CARD_TYPE_REV

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    # Get the card
    try:
        card = mw.col.get_card(card_id)
    except NotFoundError:
        return {
            "success": False,
            "error": f"Card not found: {card_id}",
            "card_id": card_id,
            "hint": "Verify the card ID is correct using get_due_cards or other card operations.",
        }

    # Validate rating
    if rating not in [1, 2, 3, 4]:
        return {
            "success": False,
            "error": f"Invalid rating: {rating}. Must be 1-4 (1=Again, 2=Hard, 3=Good, 4=Easy)",
            "card_id": card_id,
        }

    # WRITE operation - wrap with edit session
    try:
        mw.requireReset()

        # Get the scheduler
        scheduler = mw.col.sched

        # Start the timer (required before answering)
        card.start_timer()

        # Answer the card with the given rating
        changes = scheduler.answerCard(card, rating)

        # Reload the card to get updated state
        card.load()

    finally:
        if mw.col:
            mw.maybeReset()

    # Build response with rating information
    rating_names = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}
    rating_name = rating_names[rating]

    # Get card type name
    card_type_names = ["new", "learning", "review", "relearning"]
    card_type_name = card_type_names[card.type] if card.type < 4 else "unknown"

    # Format interval and due date information
    result = {
        "success": True,
        "card_id": card_id,
        "rating": rating,
        "card_type": card_type_name,
    }

    # Calculate next review information based on card type
    if card.type == CARD_TYPE_REV:
        # Review cards: interval is in days
        interval_days = card.ivl
        result["new_interval"] = interval_days

        # Calculate next review date (due is relative to collection creation)
        collection_creation_timestamp = mw.col.crt
        due_timestamp = collection_creation_timestamp + (card.due * 86400)
        next_review_date = datetime.fromtimestamp(due_timestamp)
        next_review_str = next_review_date.strftime("%Y-%m-%d")
        result["next_review"] = next_review_str

        result["message"] = (
            f"Card rated as '{rating_name}'. "
            f"Next review: {next_review_str} (in {interval_days} days)"
        )
    else:
        # Learning/new cards: due is a timestamp, interval is in seconds
        interval_seconds = card.ivl

        if card.due:
            next_review_date = datetime.fromtimestamp(card.due)
            next_review_str = next_review_date.strftime("%Y-%m-%d %H:%M")
            result["next_review"] = next_review_str

            # Format interval in human-readable form
            if interval_seconds < 60:
                interval_str = f"{interval_seconds} seconds"
            elif interval_seconds < 3600:
                interval_str = f"{interval_seconds // 60} minutes"
            else:
                interval_str = f"{interval_seconds // 3600} hours"

            result["message"] = (
                f"Card rated as '{rating_name}'. "
                f"Next review: {next_review_str} (in {interval_str})"
            )
        else:
            result["message"] = f"Card rated as '{rating_name}'"

    return result


# Register handler at import time
register_handler("rate_card", _rate_card_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_rate_card_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register rate_card tool with the MCP server."""

    @mcp.tool(
        description=(
            "Submit a rating for a card to update Anki's spaced repetition scheduling. "
            "Use this ONLY after the user confirms or modifies your suggested rating. "
            "Do not rate automatically without user input."
        )
    )
    async def rate_card(
        card_id: int,
        rating: int
    ) -> dict[str, Any]:
        """Submit a rating for a card to update Anki's spaced repetition scheduling.

        Rates a card and updates its scheduling according to Anki's spaced repetition
        algorithm. This should only be called after the user has explicitly provided
        or confirmed their rating choice.

        IMPORTANT: Always ask the user to confirm or modify your suggested rating
        before calling this tool. Never rate cards automatically.

        Args:
            card_id: The unique identifier of the card to rate. Obtained from
                get_due_cards or other card-related operations.
            rating: The user's rating for the card. Must be between 1 and 4:
                - 1 = Again (Failed): Card will be shown again soon, resets learning
                - 2 = Hard: Increases interval less than 'Good', indicates difficulty
                - 3 = Good: Standard passing grade, normal interval increase
                - 4 = Easy: Largest interval increase, card was very easy

        Returns:
            Dictionary containing:
            - success (bool): Whether the rating was successfully applied
            - card_id (int): The ID of the rated card
            - rating (int): The rating that was applied (1-4)
            - message (str): Human-readable confirmation message
            - next_review (str, optional): When the card will next be due
            - new_interval (int, optional): The new interval in days (for review cards)
            - card_type (str): The type of card (new/learning/review/relearning)

        Raises:
            ValueError: If rating is not between 1 and 4
            Exception: If the main thread returns an error response

        Examples:
            >>> # Rate a card as "Good" after user confirmation
            >>> result = await rate_card(card_id=1234567890, rating=3)
            >>> print(result['message'])
            'Card rated as 'Good'. Next review: 2025-12-09 (in 4 days)'
            >>>
            >>> # Rate a card as "Again" (failed)
            >>> result = await rate_card(card_id=1234567890, rating=1)
            >>> if result['success']:
            ...     print(f"Card {result['card_id']} will be reviewed again soon")
            >>>
            >>> # Invalid rating will raise an error
            >>> try:
            ...     result = await rate_card(card_id=1234567890, rating=5)
            ... except ValueError as e:
            ...     print(f"Error: {e}")

        Note:
            - This operation modifies the Anki collection on the main thread
            - The rating affects the card's scheduling according to Anki's SM-2 algorithm
            - After rating, the card's due date, interval, and ease factor are updated
            - Ratings cannot be undone through this API
            - Always confirm with the user before rating
            - Rating a card that doesn't exist will return an error
        """
        # Validate rating parameter
        if not isinstance(rating, int) or rating < 1 or rating > 4:
            raise ValueError(
                f"Rating must be an integer between 1 and 4, got: {rating}. "
                "Valid ratings: 1=Again, 2=Hard, 3=Good, 4=Easy"
            )

        # Validate card_id is a positive integer
        if not isinstance(card_id, int) or card_id <= 0:
            raise ValueError(
                f"card_id must be a positive integer, got: {card_id}"
            )

        # Call main thread with parameters
        return await call_main_thread(
            "rate_card",
            {
                "card_id": card_id,
                "rating": rating
            }
        )
