from typing import Any
import logging

from ....tool_decorator import Tool
from ....handler_wrappers import get_col
from ...essential.tools._render_helpers import render_answer, render_question_with_style


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
    "Get the card currently displayed in Anki's reviewer window: question HTML, "
    "note ID, card ID, deck name, note type, and the next-review interval behind each "
    "answer button. deckName is the card's home deck; filteredDeckName is always present and is "
    "null unless the card is currently being studied from a filtered deck. "
    "The answer and the note's fields are omitted by default (no 'answer' or 'fields' key in "
    "cardInfo) -- a note's fields can themselves contain the answer (e.g. a Basic note's Back "
    "field), so both are gated together. Pass include_answer=True to receive both -- during a "
    "review, do not request it until the user has answered the question. "
    "Returns inReview=false when the reviewer is not active. advancing=true means Anki is "
    "still transitioning to the next card after a rating -- the card shown may be the one "
    "just answered, so call this again to get the actual next card. "
    "Use this when the user is reviewing in Anki's GUI and asks about the card in front of them "
    "(for example 'explain this card'), or to find the note behind it before editing. "
    "Read-only: by default the user presses the answer buttons themselves. Only use "
    "gui_answer_card, after gui_show_answer and an explicit user-confirmed rating, when the "
    "user has asked for hands-free rating -- never use rate_card on a card in the reviewer, "
    "it bypasses the reviewer and leaves it desynced. get_due_cards, present_card and "
    "rate_card are for AI-driven review sessions outside the GUI reviewer.",
    write=False,
)
def gui_current_card(include_answer: bool = False) -> dict[str, Any]:
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

    question_html = render_question_with_style(card)

    buttons, next_reviews = _answer_buttons(col, mw.reviewer, card)

    card_info = {
        "cardId": card_id,
        "noteId": note.id,
        "question": question_html,
        "deckName": deck_name,
        "filteredDeckName": filtered_deck_name,
        "modelName": model_name,
        "buttons": buttons,
        "nextReviews": next_reviews,
    }
    if include_answer:
        fields_dict = {}
        for i, (field_name, field_value) in enumerate(note.items()):
            fields_dict[field_name] = {"value": field_value, "order": i}
        card_info["fields"] = fields_dict
        card_info["answer"] = render_answer(card)

    message = f'Current card: {card_id} from deck "{deck_name}"'
    if filtered_deck_name:
        message += f' (studying in filtered deck "{filtered_deck_name}")'

    return {
        "success": True,
        "cardInfo": card_info,
        "inReview": True,
        "advancing": mw.reviewer.state == "transition",
        "message": message,
        "hint": "Use gui_edit_note with the noteId from this response to edit the note behind this card.",
    }
