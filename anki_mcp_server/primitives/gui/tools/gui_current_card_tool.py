from typing import Any
import logging

from ....tool_decorator import Tool
from ....handler_wrappers import get_col


logger = logging.getLogger(__name__)


def _answer_buttons(col: Any, reviewer: Any, card: Any) -> tuple[list[int], list[str]]:
    """Return (button eases, next-review labels) for the card on screen.

    Prefers the reviewer's own v3 scheduling states, which is what Anki itself
    uses to label the answer buttons. This keeps the labels correct when a
    custom scheduler rewrites the upcoming states. Falls back to the legacy
    per-ease query when the private ``_v3`` state is unavailable, when the v3
    call raises, or when it yields no labels.
    """
    v3 = getattr(reviewer, "_v3", None)
    labels: list[str] = []

    if v3 is not None:
        try:
            labels = list(col.sched.describe_next_states(v3.states))
        except Exception:
            logger.warning(
                "describe_next_states failed, falling back to nextIvlStr", exc_info=True
            )
            labels = []

    if labels:
        return list(range(1, len(labels) + 1)), labels

    buttons = list(range(1, col.sched.answerButtons(card) + 1))
    return buttons, [col.sched.nextIvlStr(card, ease) for ease in buttons]


@Tool(
    "gui_current_card",
    "Get the card currently displayed in Anki's reviewer window: question and answer HTML, "
    "fields, note ID, card ID, deck name, note type, and the next-review interval behind each "
    "answer button. deckName is the card's home deck; filteredDeckName is always present and is "
    "null unless the card is currently being studied from a filtered deck. "
    "Returns inReview=false when the reviewer is not active. "
    "Use this when the user is reviewing in Anki's GUI and asks about the card in front of them "
    "(for example 'explain this card'), or to find the note behind it before editing. "
    "Read-only: while the user is reviewing in Anki's own reviewer, never answer or rate cards "
    "for them, not with GUI tools and not with rate_card, the user presses the answer buttons. "
    "get_due_cards, present_card and rate_card are for AI-driven review sessions outside the "
    "GUI reviewer.",
    write=False,
)
def gui_current_card() -> dict[str, Any]:
    from aqt import mw

    col = get_col()

    if not mw.reviewer or not mw.reviewer.card or mw.state != "review":
        return {
            "success": True,
            "cardInfo": None,
            "inReview": False,
            "message": "Not currently in review mode",
            "hint": "Open a deck in Anki and start reviewing to see current card information.",
        }

    card = mw.reviewer.card
    card_id = card.id
    note = card.note()

    # name_if_exists() -> None for a dangling deck id. col.decks.get() defaults
    # to default=True and would silently return the Default deck instead.
    deck_name = col.decks.name_if_exists(card.current_deck_id()) or "Unknown"

    filtered_deck_name = None
    if card.odid:
        filtered_deck_name = col.decks.name_if_exists(card.did) or "Unknown"

    model = note.note_type()
    model_name = model["name"] if model else "Unknown"

    question_html = card.question()
    answer_html = card.answer()

    buttons, next_reviews = _answer_buttons(col, mw.reviewer, card)

    fields_dict = {}
    for i, (field_name, field_value) in enumerate(note.items()):
        fields_dict[field_name] = {"value": field_value, "order": i}

    card_info = {
        "cardId": card_id,
        "noteId": note.id,
        "question": question_html,
        "answer": answer_html,
        "deckName": deck_name,
        "filteredDeckName": filtered_deck_name,
        "modelName": model_name,
        "buttons": buttons,
        "nextReviews": next_reviews,
        "fields": fields_dict,
    }

    message = f'Current card: {card_id} from deck "{deck_name}"'
    if filtered_deck_name:
        message += f' (studying in filtered deck "{filtered_deck_name}")'

    return {
        "success": True,
        "cardInfo": card_info,
        "inReview": True,
        "message": message,
        "hint": "Use gui_edit_note with the noteId from this response to edit the note behind this card.",
    }
