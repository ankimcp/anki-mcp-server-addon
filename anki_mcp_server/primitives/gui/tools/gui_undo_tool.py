from typing import Any
import logging

from ....tool_decorator import Tool
from ....handler_wrappers import get_col


logger = logging.getLogger(__name__)


@Tool(
    "gui_undo",
    "Undo the last action in Anki -- a note edit, a card-management change, or a rating just made in Anki's reviewer. "
    "Returns undone=true if there was something to undo. "
    "IMPORTANT: only call this when the user explicitly asks to undo; in a hands-free GUI review session it is the right way to take back a mis-heard or mistaken rating (never 'correct' a rating by rating again). "
    "After undoing a reviewer rating, the reviewer only re-shows the undone card once the Anki window regains focus, so a gui_current_card call made immediately afterwards may still report the previous card -- ask the user to click into Anki, then read again.",
    # write=False: mw.undo() is an async CollectionOp that refreshes Anki's
    # UI itself once it completes; write=True's immediate post-handler
    # mw.reset() would race it.
    write=False,
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
