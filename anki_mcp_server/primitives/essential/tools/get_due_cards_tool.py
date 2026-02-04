from typing import Any
import logging

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col

logger = logging.getLogger(__name__)


@Tool(
    "get_due_cards",
    "Retrieve the next single card due for review from a specified deck in true scheduler order. IMPORTANT: Use sync tool FIRST before getting cards to ensure latest data. After getting the card, use present_card to show it to the user. Returns one card per call to ensure correct scheduler interleaving. The deck_name parameter is required - you must specify which deck to study.",
)
def get_due_cards(deck_name: str) -> dict[str, Any]:
    from aqt import mw
    from anki.consts import QUEUE_TYPE_NEW, QUEUE_TYPE_LRN, QUEUE_TYPE_REV

    col = get_col()

    # Select the specified deck
    deck = col.decks.by_name(deck_name)
    if not deck:
        raise HandlerError(
            f"Deck '{deck_name}' not found",
            hint="Check spelling or use list_decks to see available decks",
            deck_name=deck_name
        )
    col.decks.select(deck["id"])

    # Use scheduler's queue (idempotent peek - doesn't modify state)
    # Hardcoded to 1 to ensure true scheduler order on each call
    try:
        queued = col.sched.get_queued_cards(fetch_limit=1)
    except Exception as e:
        raise HandlerError(
            f"Failed to retrieve queued cards: {str(e)}",
            hint="Ensure a profile is open and the deck has due cards"
        )

    # Map queue type integers to human-readable names
    queue_names = {
        QUEUE_TYPE_NEW: "new",
        QUEUE_TYPE_LRN: "learning",
        QUEUE_TYPE_REV: "review"
    }

    # If no cards are due, return early
    if not queued.cards:
        return {
            "message": "No cards are due for review",
            "cards": [],
            "counts": {
                "new": queued.new_count,
                "learning": queued.learning_count,
                "review": queued.review_count
            },
            "total": 0,
            "returned": 0,
        }

    # Process each queued card
    due_cards = []
    for qc in queued.cards:
        try:
            # Get full card and note objects
            card = col.get_card(qc.card.id)
            note = card.note()

            # Extract front/back from note fields
            fields_dict = dict(note.items())
            front = fields_dict.get("Front", "")
            back = fields_dict.get("Back", "")

            # Fallback: if no Front/Back fields, use first two fields
            if not front and not back:
                field_values = list(fields_dict.values())
                front = field_values[0] if len(field_values) > 0 else ""
                back = field_values[1] if len(field_values) > 1 else ""

            # Get deck name
            deck = col.decks.get(card.did)
            deck_name_str = deck["name"] if deck else "Unknown"

            # Get model name
            model = note.note_type()
            model_name = model["name"] if model else "Unknown"

            # Get queue type name
            queue_type = queue_names.get(qc.queue, "unknown")

            due_cards.append({
                "cardId": card.id,
                "front": front,
                "back": back,
                "deckName": deck_name_str,
                "modelName": model_name,
                "queueType": queue_type,
                "due": card.due,
                "interval": card.ivl,
                "factor": card.factor,
            })
        except Exception as e:
            logger.warning(f"Could not retrieve card {qc.card.id}: {e}")
            continue

    # Calculate total cards available across all queues
    total_due = queued.new_count + queued.learning_count + queued.review_count

    return {
        "cards": due_cards,
        "counts": {
            "new": queued.new_count,
            "learning": queued.learning_count,
            "review": queued.review_count
        },
        "total": total_due,
        "returned": len(due_cards),
        "message": f"Next card in scheduler order (total {total_due} due cards)",
    }
