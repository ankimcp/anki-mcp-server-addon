"""Delete notes tool - MCP tool and handler in one file.

Permanently removes notes and ALL associated cards from the collection.
This action cannot be undone unless you have a backup.

Thread Safety:
    - Tool handler runs in background thread
    - Actual note deletion happens on main thread via queue bridge
"""

from typing import Any, Callable, Coroutine
import logging

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _delete_notes_handler(notes: list[int], confirmDeletion: bool) -> dict[str, Any]:
    """
    Delete notes by their IDs.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.
    Permanently removes notes and ALL associated cards from the collection.
    This action cannot be undone unless you have a backup.

    Args:
        notes: List of note IDs to delete (max 100 at once for safety)
        confirmDeletion: Must be True to confirm permanent deletion

    Returns:
        dict: Deletion result with structure:
            - success (bool): Whether operation succeeded
            - deletedCount (int): Number of notes actually deleted
            - deletedNoteIds (list[int]): IDs of deleted notes
            - cardsDeleted (int): Total number of cards deleted
            - notFoundCount (int): Number of requested notes not found
            - requestedIds (list[int]): Original list of requested IDs
            - message (str): Human-readable result message
            - warning (str): Warning about permanent deletion
            - hint (str): Suggestion for next steps

    Raises:
        RuntimeError: If collection is not loaded
        ValueError: If confirmDeletion is False or validation fails
    """
    from aqt import mw

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    # Safety check - require explicit confirmation
    if not confirmDeletion:
        raise ValueError(
            "Deletion not confirmed. Set confirmDeletion to true to permanently "
            "delete these notes and all their cards. This action cannot be undone!"
        )

    # Validate note count
    if len(notes) > 100:
        raise ValueError(
            f"Cannot delete more than 100 notes at once for safety. "
            f"Requested: {len(notes)} notes"
        )

    if len(notes) == 0:
        raise ValueError("No note IDs provided")

    # Get info about notes before deletion (for logging and confirmation)
    valid_notes = []
    total_cards = 0

    for note_id in notes:
        try:
            note = mw.col.get_note(note_id)
            card_count = len(note.cards())
            valid_notes.append({
                "noteId": note_id,
                "cardCount": card_count
            })
            total_cards += card_count
        except KeyError:
            # Note not found - already deleted
            logger.info(f"Note {note_id} not found (already deleted)")
            continue
        except Exception as e:
            # Log unexpected errors
            logger.error(f"Unexpected error checking note {note_id}: {e}", exc_info=True)
            continue

    valid_note_ids = [n["noteId"] for n in valid_notes]
    not_found_count = len(notes) - len(valid_note_ids)

    # If no valid notes found, return early
    if len(valid_note_ids) == 0:
        return {
            "success": True,
            "deletedCount": 0,
            "notFoundCount": len(notes),
            "requestedIds": notes,
            "message": "No notes were deleted (none of the provided IDs were valid)",
            "hint": "The notes may have already been deleted or the IDs are invalid"
        }

    # Wrap the actual deletion in an edit session
    # This ensures Anki's UI is refreshed after changes
    try:
        # Mark UI as needing refresh
        mw.requireReset()

        # Anki's remove_notes() permanently deletes notes and their cards
        mw.col.remove_notes(valid_note_ids)

        # Trigger UI refresh if collection is still available
        if mw.col:
            mw.maybeReset()

    except Exception as e:
        # Ensure UI refresh even on error
        if mw.col:
            mw.maybeReset()
        raise

    # Build success message
    if not_found_count > 0:
        message = (
            f"Successfully deleted {len(valid_note_ids)} note(s) and {total_cards} card(s). "
            f"{not_found_count} note(s) were not found."
        )
    else:
        message = f"Successfully deleted {len(valid_note_ids)} note(s) and {total_cards} card(s)"

    return {
        "success": True,
        "deletedCount": len(valid_note_ids),
        "deletedNoteIds": valid_note_ids,
        "cardsDeleted": total_cards,
        "notFoundCount": not_found_count,
        "requestedIds": notes,
        "message": message,
        "warning": "These notes and cards have been permanently deleted",
        "hint": "Consider syncing with AnkiWeb to propagate deletions to other devices"
    }


# Register handler at import time
register_handler("deleteNotes", _delete_notes_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_delete_notes_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register deleteNotes tool with the MCP server."""

    @mcp.tool(
        description=(
            "Delete notes by their IDs. This will permanently remove the notes and ALL associated cards. "
            "This action cannot be undone unless you have a backup. CRITICAL: This is destructive and permanent - "
            "only delete notes the user explicitly confirmed for deletion."
        )
    )
    async def deleteNotes(notes: list[int], confirmDeletion: bool) -> dict[str, Any]:
        """Delete notes by their IDs.

        This will permanently remove notes and ALL associated cards from the collection.
        This action cannot be undone unless you have a backup.

        Args:
            notes: Array of note IDs to delete (max 100 at once for safety).
                Get these IDs from findNotes tool. ALL cards associated with
                these notes will be deleted.
            confirmDeletion: Must be set to true to confirm you want to permanently
                delete these notes and their cards. This is a safety check.

        Returns:
            Dictionary containing:
            - success: Boolean indicating if the operation succeeded
            - deletedCount: Number of notes actually deleted
            - deletedNoteIds: List of note IDs that were deleted
            - cardsDeleted: Total number of cards that were deleted
            - notFoundCount: Number of requested notes that were not found
            - requestedIds: Original list of requested note IDs
            - message: Human-readable result message
            - warning: Warning about permanent deletion
            - hint: Suggestion for next steps

        Raises:
            ValueError: If confirmDeletion is False or validation fails
            Exception: If the deletion operation fails

        Examples:
            >>> # First, confirm with user, then delete
            >>> await deleteNotes(notes=[1234, 5678], confirmDeletion=True)
            {
                'success': True,
                'deletedCount': 2,
                'deletedNoteIds': [1234, 5678],
                'cardsDeleted': 4,
                'notFoundCount': 0,
                'requestedIds': [1234, 5678],
                'message': 'Successfully deleted 2 note(s) and 4 card(s)',
                'warning': 'These notes and cards have been permanently deleted',
                'hint': 'Consider syncing with AnkiWeb to propagate deletions to other devices'
            }

            >>> # Attempting to delete without confirmation
            >>> await deleteNotes(notes=[1234], confirmDeletion=False)
            # Raises ValueError: Deletion not confirmed

        Safety Features:
            - Requires explicit confirmDeletion=True parameter
            - Maximum 100 notes per deletion operation
            - Validates notes exist before deletion
            - Returns detailed statistics about what was deleted
            - Provides hints if notes were not found
        """
        try:
            # Call main thread to execute the deletion via handler
            result = await call_main_thread("deleteNotes", {
                "notes": notes,
                "confirmDeletion": confirmDeletion
            })

            # Return the result from handler
            return result

        except ValueError as e:
            # Handle confirmation and validation errors
            error_msg = str(e)

            if "not confirmed" in error_msg.lower():
                return {
                    "success": False,
                    "requestedNotes": notes,
                    "noteCount": len(notes),
                    "error": error_msg,
                    "hint": "Set confirmDeletion to true to permanently delete these notes and all their cards",
                    "warning": "This action cannot be undone!"
                }
            elif "more than 100" in error_msg.lower():
                return {
                    "success": False,
                    "requestedNotes": notes,
                    "noteCount": len(notes),
                    "error": error_msg,
                    "hint": "Delete notes in smaller batches (maximum 100 at a time) for safety"
                }
            else:
                return {
                    "success": False,
                    "requestedNotes": notes,
                    "error": error_msg,
                    "hint": "Check the note IDs and ensure confirmDeletion is set to true"
                }

        except Exception as e:
            # Handle other errors
            error_msg = str(e)

            if "collection not loaded" in error_msg.lower():
                return {
                    "success": False,
                    "requestedNotes": notes,
                    "error": error_msg,
                    "hint": "Make sure Anki is running and a profile is loaded"
                }

            # Generic error
            return {
                "success": False,
                "requestedNotes": notes,
                "error": error_msg,
                "hint": "Make sure Anki is running and the note IDs are valid"
            }
