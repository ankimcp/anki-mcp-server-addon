from typing import Any
from datetime import datetime

from anki_mcp_server.tool_decorator import Tool
from anki_mcp_server.handler_wrappers import HandlerError, get_col


@Tool(
    "rate_card",
    "Submit a rating for a card to update Anki's spaced repetition scheduling. "
    "Use this ONLY after the user confirms or modifies your suggested rating. "
    "Do not rate automatically without user input.",
    write=True,
)
def rate_card(card_id: int, rating: int) -> dict[str, Any]:
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
            hint="Use get_due_cards or findCards to get valid card IDs",
        )

    try:
        card = col.get_card(card_id)
    except Exception:
        raise HandlerError(
            f"Card not found: {card_id}",
            hint="Verify the card ID is correct using get_due_cards or other card operations",
        )

    scheduler = col.sched
    card.start_timer()
    scheduler.answerCard(card, rating)
    card.load()

    rating_names = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}
    rating_name = rating_names[rating]

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
