from typing import Any

from anki_mcp_server.tool_decorator import Tool
from anki_mcp_server.handler_wrappers import HandlerError, get_col


@Tool(
    "present_card",
    "Retrieve a card's content for review. WORKFLOW: 1) Show question, 2) Wait for user answer, 3) Show answer with show_answer=true, 4) Evaluate and suggest rating (1-4), 5) Wait for user confirmation (\"ok\"/\"next\" = accept, or they provide different rating), 6) Only then use rate_card",
)
def present_card(card_id: int, show_answer: bool = False) -> dict[str, Any]:
    col = get_col()

    try:
        card = col.get_card(card_id)
    except Exception:
        raise HandlerError(
            f"Card not found with ID {card_id}",
            hint="Verify the card ID is correct using get_due_cards or findCards",
        )

    note = card.note()
    deck = col.decks.get(card.did)
    deck_name = deck["name"] if deck else "Unknown"
    model = note.note_type()
    note_type = model["name"] if model else "Unknown"

    card_info = {
        "card_id": card.id,
        "deck_name": deck_name,
        "question": card.question(),
        "note_type": note_type,
        "due": card.due,
        "interval": card.ivl,
        "ease_factor": card.factor,
        "reviews": card.reps,
        "lapses": card.lapses,
    }

    if show_answer:
        card_info["answer"] = card.answer()

    return {"card": card_info}
