from typing import Any
import logging

from ....tool_decorator import Tool


logger = logging.getLogger(__name__)


@Tool(
    "gui_show_question",
    "Show the question side of the card currently on screen in Anki's reviewer. "
    "Returns inReview=false when the reviewer is not active. "
    "Use this when the user is reviewing in Anki's GUI and asks to go back to the question "
    "side of the card in front of them. "
    "This only changes what is displayed: while the user is reviewing in Anki's own reviewer, "
    "never answer or rate cards for them, not with GUI tools and not with rate_card, the user "
    "presses the answer buttons. get_due_cards, present_card and rate_card are for AI-driven "
    "review sessions outside the GUI reviewer.",
    write=False,
)
def gui_show_question() -> dict[str, Any]:
    from aqt import mw

    if not mw.reviewer or not mw.reviewer.card or mw.state != "review":
        return {
            "success": True,
            "inReview": False,
            "message": "Not in review mode - question cannot be shown",
            "hint": "Start reviewing a deck in Anki to use this tool.",
        }

    mw.reviewer._showQuestion()
    logger.info("Question side shown successfully")

    return {
        "success": True,
        "inReview": True,
        "message": "Question side is now displayed",
        "hint": "Use gui_current_card to get the card details, or gui_show_answer to reveal the answer.",
    }
