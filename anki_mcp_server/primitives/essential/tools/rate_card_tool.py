from typing import Annotated, Any
from datetime import datetime

from pydantic import Field

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col
from ._ease_names import ease_name


@Tool(
    "rate_card",
    "Submit a rating for a card to update Anki's spaced repetition scheduling. "
    "Precondition: the answer must already have been revealed to the user via present_card(show_answer=true), "
    "and the user must have confirmed or modified your suggested rating. "
    "Never submit a rating the user has not confirmed after seeing the answer. "
    "Returns next_review date, new_interval (days for review cards), and card_type.",
    write=True,
)
def rate_card(
    card_id: Annotated[int, Field(description="Card to rate")],
    rating: Annotated[int, Field(description="Rating 1-4 (1=Again, 2=Hard, 3=Good, 4=Easy)")],
) -> dict[str, Any]:
    from anki.consts import CARD_TYPE_REV

    col = get_col()

    if not isinstance(rating, int) or rating < 1 or rating > 4:
        raise HandlerError(
            f"Invalid rating: {rating}. Must be 1-4 (1=Again, 2=Hard, 3=Good, 4=Easy)",
            hint="Rating must be an integer between 1 and 4",
        )

    if not isinstance(card_id, int) or card_id <= 0:
        raise HandlerError(
            f"card_id must be a positive integer, got: {card_id}",
            hint="Use get_due_cards or cards_stats to get valid card IDs (find_notes returns note IDs, not card IDs)",
        )

    try:
        card = col.get_card(card_id)
    except Exception:
        raise HandlerError(
            f"Card not found: {card_id}",
            hint="Verify the card ID is correct using get_due_cards or other card operations",
        )

    # Computed BEFORE answerCard() mutates the card -- see _ease_names.py.
    rating_name = ease_name(col, card, rating)

    scheduler = col.sched
    card.start_timer()
    scheduler.answerCard(card, rating)
    card.load()

    card_type_names = ["new", "learning", "review", "relearning"]
    card_type_name = card_type_names[card.type] if card.type < 4 else "unknown"

    result: dict[str, Any] = {
        "card_id": card_id,
        "rating": rating,
        "card_type": card_type_name,
    }

    if card.type == CARD_TYPE_REV:
        interval_days = card.ivl
        result["new_interval"] = interval_days

        collection_creation_timestamp = col.crt
        due_timestamp = collection_creation_timestamp + (card.due * 86400)
        next_review_date = datetime.fromtimestamp(due_timestamp)
        next_review_str = next_review_date.strftime("%Y-%m-%d")
        result["next_review"] = next_review_str
        result["message"] = (
            f"Card rated as '{rating_name}'. "
            f"Next review: {next_review_str} (in {interval_days} days)"
        )
    else:
        interval_seconds = card.ivl

        if card.due:
            next_review_date = datetime.fromtimestamp(card.due)
            next_review_str = next_review_date.strftime("%Y-%m-%d %H:%M")
            result["next_review"] = next_review_str

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
