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

    mw.reviewer._showAnswer()

    return {
        "success": True,
        "inReview": True,
        "advancing": mw.reviewer.state == "transition",
        "message": "Answer side is now displayed",
        "hint": "Use gui_current_card to get full card details including the answer content.",
    }
