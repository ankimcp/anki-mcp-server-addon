from typing import Any
import logging

from ....tool_decorator import Tool, ToolError, get_col


logger = logging.getLogger(__name__)


@Tool(
    "gui_edit_note",
    "Open Anki note editor dialog for a specific note ID. Allows manual editing "
    "of note fields, tags, and cards in the GUI. The note editor is opened in the "
    "Anki Browser, which provides full editing capabilities. "
    "IMPORTANT: Only use when user explicitly requests editing a note via GUI. "
    "This tool is for note editing workflows when user wants to manually edit in "
    "the Anki interface. For programmatic editing, use updateNoteFields instead.",
    write=False,
)
def gui_edit_note(note_id: int) -> dict[str, Any]:
    from aqt import mw, dialogs

    col = get_col()

    try:
        col.get_note(note_id)
    except Exception as e:
        logger.error(f"Note {note_id} not found: {e}")
        raise ToolError(
            f"Note {note_id} not found",
            hint="Use findNotes to search for notes and get valid note IDs.",
            noteId=note_id,
        )

    browser = dialogs.open("Browser", mw)
    browser.activateWindow()

    query = f"nid:{note_id}"
    browser.form.searchEdit.lineEdit().setText(query)

    if hasattr(browser, "onSearch"):
        browser.onSearch()
    else:
        browser.onSearchActivated()

    return {
        "noteId": note_id,
        "message": f"Note editor opened for note {note_id}",
        "hint": "The user can now edit the note fields, tags, and cards in the Anki browser editor panel. Changes will be saved automatically.",
    }
