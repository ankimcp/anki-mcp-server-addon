from typing import Any

from ....tool_decorator import Tool


@Tool(
    "gui_show_answer",
    "Show the answer side of the current card in review mode. "
    "Returns true if in review mode, false otherwise. "
    "CRITICAL: This tool is ONLY for note editing/creation workflows when user needs to "
    "view the answer side to verify content. NEVER use this for conducting review sessions. "
    "Use the dedicated review tools (present_card) instead. "
    "IMPORTANT: Only use when user explicitly requests showing the answer.",
    write=False,
)
def gui_show_answer() -> dict[str, Any]:
    from aqt import mw

    if not mw.reviewer or not mw.reviewer.card:
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
        "message": "Answer side is now displayed",
        "hint": "Use guiCurrentCard to get full card details including the answer content.",
    }
