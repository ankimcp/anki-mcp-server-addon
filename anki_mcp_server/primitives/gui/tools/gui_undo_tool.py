from typing import Any
import logging

from ....tool_decorator import Tool
from ....handler_wrappers import get_col


logger = logging.getLogger(__name__)


@Tool(
    "gui_undo",
    "Undo the last action or card in Anki. Returns true if undo succeeded, false otherwise. "
    "IMPORTANT: Only use when user explicitly requests undoing an action. "
    "This tool is for note editing/creation workflows, NOT for review sessions. "
    "Use this to undo mistakes in note creation, editing, or card management.",
    write=True,
)
def gui_undo() -> dict[str, Any]:
    from aqt import mw

    col = get_col()

    undo_status = col.undo_status()

    if not undo_status or not undo_status.undo:
        logger.info("No undo operation available")
        return {
            "success": True,
            "undone": False,
            "message": "Nothing to undo",
            "hint": "There are no recent actions to undo in Anki.",
        }

    mw.undo()
    logger.info("Undo operation initiated successfully")

    return {
        "success": True,
        "undone": True,
        "message": "Last action undone successfully",
        "hint": "The previous action has been reversed. Check Anki GUI to verify.",
    }
