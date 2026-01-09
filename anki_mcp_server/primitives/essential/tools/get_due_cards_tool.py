from typing import Any, Optional
import logging

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col

logger = logging.getLogger(__name__)


@Tool(
    "get_due_cards",
    "Retrieve cards that are due for review from Anki. IMPORTANT: Use sync tool FIRST before getting cards to ensure latest data. After getting cards, use present_card to show them one by one to the user",
)
def get_due_cards(deck_name: Optional[str] = None, limit: int = 10) -> dict[str, Any]:
    col = get_col()

    card_limit = max(1, min(limit, 50))

    query = "is:due"
    if deck_name:
        escaped_deck_name = deck_name.replace('"', '\\"')
        query = f'"deck:{escaped_deck_name}" {query}'

    try:
        card_ids = col.find_cards(query)
    except Exception as e:
        raise HandlerError(f"Failed to find due cards: {str(e)}")

    if len(card_ids) == 0:
        return {
            "message": "No cards are due for review",
            "cards": [],
            "total": 0,
            "returned": 0,
        }

    selected_card_ids = card_ids[:card_limit]

    from aqt import mw

    due_cards = []
    for card_id in selected_card_ids:
        try:
            card = col.get_card(card_id)
            note = card.note()

            fields_dict = dict(note.items())
            front = fields_dict.get("Front", "")
            back = fields_dict.get("Back", "")

            if not front and not back:
                field_values = list(fields_dict.values())
                front = field_values[0] if len(field_values) > 0 else ""
                back = field_values[1] if len(field_values) > 1 else ""

            deck = mw.col.decks.get(card.did)
            deck_name_str = deck["name"] if deck else "Unknown"

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
            logger.warning(f"Could not retrieve card {card_id}: {e}")
            continue

    return {
        "cards": due_cards,
        "total": len(card_ids),
        "returned": len(due_cards),
        "message": f"Found {len(card_ids)} due cards, returning {len(due_cards)}",
    }
