# primitives/gui/tools/gui_edit_note_tool.py
"""GUI Edit Note tool - open note editor for a specific note."""

from typing import Any, Callable, Coroutine
import logging

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col and dialogs
# ============================================================================

def _gui_edit_note_handler(note_id: int) -> dict[str, Any]:
    """
    Open Anki note editor for a specific note ID.

    This function runs on the Qt MAIN THREAD and has direct access to mw.

    The note editor is opened via the Browser dialog, which provides a full-featured
    editor interface for modifying note fields, tags, and associated cards.

    Args:
        note_id: The ID of the note to edit

    Returns:
        dict: Result with structure:
            - success (bool): Whether the operation succeeded
            - noteId (int): The note ID that was opened
            - message (str): Human-readable result message
            - hint (str): Helpful hint about next steps

    Raises:
        RuntimeError: If collection is not loaded or note not found
    """
    from aqt import mw, dialogs

    # Check if collection is loaded
    if mw is None or mw.col is None:
        raise RuntimeError("Collection not loaded")

    try:
        # Verify the note exists
        note = mw.col.get_note(note_id)
    except Exception as e:
        logger.error(f"Note {note_id} not found: {e}")
        return {
            "success": False,
            "noteId": note_id,
            "error": f"Note {note_id} not found",
            "hint": "Use findNotes to search for notes and get valid note IDs."
        }

    try:
        # Open the browser
        browser = dialogs.open('Browser', mw)
        browser.activateWindow()

        # Search for this specific note
        query = f"nid:{note_id}"
        browser.form.searchEdit.lineEdit().setText(query)

        # Trigger the search (different methods in different Anki versions)
        if hasattr(browser, 'onSearch'):
            browser.onSearch()
        else:
            browser.onSearchActivated()

        # The browser automatically shows the note editor panel when a note is selected
        # The note should now be selected and visible in the editor panel

        return {
            "success": True,
            "noteId": note_id,
            "message": f"Note editor opened for note {note_id}",
            "hint": "The user can now edit the note fields, tags, and cards in the Anki browser editor panel. Changes will be saved automatically.",
        }

    except Exception as e:
        logger.error(f"Failed to open note editor for note {note_id}: {e}")
        return {
            "success": False,
            "noteId": note_id,
            "error": f"Failed to open note editor: {str(e)}",
            "hint": "Make sure Anki is running and the GUI is visible",
        }


# Register handler at import time
register_handler("gui_edit_note", _gui_edit_note_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_gui_edit_note_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register gui_edit_note tool with the MCP server."""

    @mcp.tool(
        description=(
            "Open Anki note editor dialog for a specific note ID. Allows manual editing "
            "of note fields, tags, and cards in the GUI. The note editor is opened in the "
            "Anki Browser, which provides full editing capabilities. "
            "IMPORTANT: Only use when user explicitly requests editing a note via GUI. "
            "This tool is for note editing workflows when user wants to manually edit in "
            "the Anki interface. For programmatic editing, use updateNoteFields instead."
        )
    )
    async def gui_edit_note(note_id: int) -> dict[str, Any]:
        """Open Anki note editor for a specific note.

        Opens the Anki Browser with the specified note selected and ready for editing.
        The browser provides a full-featured editor interface where users can modify
        note fields, tags, card templates, and more.

        Args:
            note_id: Note ID to edit (obtained from findNotes, notesInfo, or other note queries).
                    Must be a valid note ID that exists in the collection.

        Returns:
            Dictionary containing:
            - success (bool): Whether the operation succeeded
            - noteId (int): The note ID that was opened for editing
            - message (str): Human-readable result message
            - hint (str): Helpful hint about next steps or error resolution
            - error (str): Error message (if failed)

        Raises:
            Exception: If opening the editor fails on the main thread

        Examples:
            Open editor for a specific note:
            >>> result = await gui_edit_note(note_id=1234567890)
            >>> if result['success']:
            ...     print(f"Editor opened for note {result['noteId']}")
            ...     print(result['hint'])

            Handle note not found:
            >>> result = await gui_edit_note(note_id=9999999)
            >>> if not result['success']:
            ...     print(result['error'])
            ...     print(result['hint'])

        Note:
            - This operation runs on Anki's main thread and opens the GUI
            - The browser window must be visible for this to work
            - Changes made in the editor are saved automatically by Anki
            - The browser provides access to all note editing features:
              * Edit field contents
              * Modify tags
              * Change note type
              * View/edit associated cards
              * Access card templates
            - Only use this when the user explicitly requests opening the editor
            - For programmatic note updates without GUI, use updateNoteFields instead
            - Invalid note IDs will return an error with a helpful hint
        """
        return await call_main_thread("gui_edit_note", {"note_id": note_id})
