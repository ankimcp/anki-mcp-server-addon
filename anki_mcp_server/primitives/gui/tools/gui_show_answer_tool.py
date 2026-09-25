from typing import Any

from ....tool_decorator import Tool


@Tool(
    "gui_show_answer",
    "Flip the card currently on screen in Anki's reviewer to its answer side. "
    "Returns inReview=false when the reviewer is not active. advancing=true means Anki is "
    "still transitioning to the next card after a rating -- wait and check gui_current_card "
    "before flipping. "
    "Use this when the user is reviewing in Anki's GUI and asks to reveal or flip the "
    "card in front of them. "
    "This only changes what is displayed: by default the user presses the answer buttons "
    "themselves. Only use gui_answer_card, after this call and an explicit user-confirmed "
    "rating, when the user has asked for hands-free rating -- never use rate_card on a card "
    "in the reviewer, it bypasses the reviewer and leaves it desynced. get_due_cards, "
    "present_card and rate_card are for AI-driven review sessions outside the GUI reviewer.",
    write=False,
)
def gui_show_answer() -> dict[str, Any]:
    from aqt import mw

    if not mw.reviewer or not mw.reviewer.card or mw.state != "review":
        return {
            "success": True,
            "inReview": False,
            "message": "Not in review mode - answer cannot be shown",
            "hint": "Start reviewing a deck in Anki to use this tool.",
        }

    # _showAnswer() flips reviewer.state to "answer" unconditionally -- if
    # Anki is still mid-"transition" from a previous gui_answer_card call,
    # calling it here would make a subsequent gui_answer_card pass its state
    # check and re-answer the same card. Bail out without touching the
    # reviewer; the caller should poll gui_current_card until advancing=false.
    if mw.reviewer.state == "transition":
        return {
            "success": True,
            "inReview": True,
            "advancing": True,
            "message": "Anki is still advancing the reviewer from the previous answer",
            "hint": "Call gui_current_card and wait until advancing=false before showing the answer.",
        }

    mw.reviewer._showAnswer()

    return {
        "success": True,
        "inReview": True,
        # Always False: the transition guard above already returned early if
        # the reviewer was mid-transition, and _showAnswer() itself always
        # sets state to "answer", never "transition".
        "advancing": False,
        "message": "Answer side is now displayed",
        "hint": "Use gui_current_card to get full card details including the answer content.",
    }
