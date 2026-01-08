from typing import Any

from ....tool_decorator import Tool, get_col


@Tool(
    "gui_current_card",
    "Get information about the current card displayed in review mode. "
    "Returns card details (question, answer, deck, model, card ID, buttons, next reviews, fields) "
    "or null if not currently in review. "
    "CRITICAL: This tool is ONLY for note editing/creation workflows when user needs to check "
    "what card is currently displayed in the GUI. NEVER use this for conducting review sessions. "
    "Use the dedicated review tools (get_due_cards, present_card, rate_card) instead. "
    "IMPORTANT: Only use when user explicitly requests current card information.",
    write=False,
)
def gui_current_card() -> dict[str, Any]:
    from aqt import mw

    col = get_col()

    if not mw.reviewer or not mw.reviewer.card:
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

    deck = col.decks.get(card.did)
    deck_name = deck["name"] if deck else "Unknown"

    model = note.note_type()
    model_name = model["name"] if model else "Unknown"

    question_html = mw.reviewer.card.question()
    answer_html = mw.reviewer.card.answer()

    buttons = []
    next_reviews = []
    button_count = col.sched.answerButtons(card)

    for ease in range(1, button_count + 1):
        buttons.append(ease)
        interval_text = col.sched.nextIvlStr(card, ease)
        next_reviews.append(interval_text)

    fields_dict = {}
    for i, (field_name, field_value) in enumerate(note.items()):
        fields_dict[field_name] = {"value": field_value, "order": i}

    card_info = {
        "cardId": card_id,
        "question": question_html,
        "answer": answer_html,
        "deckName": deck_name,
        "modelName": model_name,
        "buttons": buttons,
        "nextReviews": next_reviews,
        "fields": fields_dict,
    }

    return {
        "success": True,
        "cardInfo": card_info,
        "inReview": True,
        "message": f'Current card: {card_id} from deck "{deck_name}"',
        "hint": "Use guiEditNote to edit the note associated with this card.",
    }
