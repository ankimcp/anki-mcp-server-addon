from typing import Any
import logging

from ....tool_decorator import Tool
from ....handler_wrappers import get_col


logger = logging.getLogger(__name__)


@Tool(
    "gui_undo",
    "Undo the last action in Anki -- a note edit, a card-management change, or a rating just made in Anki's reviewer. "
    "Returns undone=true if there was something to undo. "
    "Applies when the user asks to undo. In a hands-free GUI review session it is how a mis-heard or mistaken rating is taken back; rating the card again does not correct a rating, it records a second one. "
    "After undoing a reviewer rating, the reviewer only re-shows the undone card once the Anki window regains focus, so a gui_current_card call made immediately afterwards may still report the previous card until the user clicks into Anki.",
    title="Undo Last Action",
    write=True,
    # mw.undo() is an async CollectionOp that refreshes Anki's UI itself once
    # it completes; _write_lock's immediate post-handler mw.reset() would race
    # it.
    refresh_ui=False,
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
        "hint": "The previous action has been reversed. If a reviewer rating was undone, the card reappears once the Anki window is focused; then call gui_current_card to re-read it.",
    }
