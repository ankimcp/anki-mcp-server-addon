from typing import Any
import logging

from ....tool_decorator import Tool


logger = logging.getLogger(__name__)


@Tool(
    "gui_show_question",
    "Show the question side of the current card in review mode. "
    "Returns true if in review mode, false otherwise. "
    "CRITICAL: This tool is ONLY for note editing/creation workflows when user needs to view "
    "the question side to verify content. NEVER use this for conducting review sessions. "
    "Use the dedicated review tools (present_card) instead. "
    "IMPORTANT: Only use when user explicitly requests showing the question.",
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
        "hint": "Use guiCurrentCard to get the card details, or guiShowAnswer to reveal the answer.",
    }
