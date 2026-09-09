from typing import Any

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col


@Tool(
    "gui_deck_review",
    "Open a deck in Anki's own reviewer window, the entry point of the GUI review flow "
    "(gui_deck_review -> gui_current_card -> gui_show_answer -> gui_answer_card). Use this "
    "when the user asks to review with the Anki window visible, instead of get_due_cards / "
    "present_card / rate_card, which drive a review without touching the reviewer UI. "
    "Anki's reviewer drops back to the deck overview on its own when nothing is due, so "
    "inReview=false with no error is a normal outcome, not a failure.",
    write=False,
)
def gui_deck_review(deck_name: str) -> dict[str, Any]:
    from aqt import mw

    col = get_col()

    # by_name(), never decks.id() -- id() silently creates the deck if it
    # doesn't exist.
    deck = col.decks.by_name(deck_name)
    if not deck:
        raise HandlerError(
            f"Deck '{deck_name}' not found",
            hint="Check spelling or use list_decks to see available decks",
            deck_name=deck_name,
        )

    col.decks.select(deck["id"])
    mw.moveToState("review")

    if mw.state == "review" and mw.reviewer.card:
        return {
            "success": True,
            "inReview": True,
            "cardId": mw.reviewer.card.id,
            "message": f'Reviewing deck "{deck_name}" in Anki\'s reviewer',
            "hint": "Use gui_current_card to read the question on screen.",
        }

    return {
        "success": True,
        "inReview": False,
        "message": f'No cards are due in deck "{deck_name}" right now',
        "hint": "Nothing to review -- try again later or pick a different deck.",
    }
