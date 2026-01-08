from typing import Any
import logging

from ....tool_decorator import Tool, ToolError, get_col

logger = logging.getLogger(__name__)


@Tool(
    "deleteNotes",
    "Delete notes by their IDs. This will permanently remove the notes and ALL associated cards. This action cannot be undone unless you have a backup. CRITICAL: This is destructive and permanent - only delete notes the user explicitly confirmed for deletion.",
    write=True,
)
def delete_notes(notes: list[int], confirmDeletion: bool) -> dict[str, Any]:
    col = get_col()

    if not confirmDeletion:
        raise ToolError(
            "Deletion not confirmed. Set confirmDeletion to true to permanently "
            "delete these notes and all their cards. This action cannot be undone!",
            hint="Set confirmDeletion to true to permanently delete these notes and all their cards",
        )

    if len(notes) > 100:
        raise ToolError(
            f"Cannot delete more than 100 notes at once for safety. Requested: {len(notes)} notes",
            hint="Delete notes in smaller batches (maximum 100 at a time) for safety",
        )

    if len(notes) == 0:
        raise ToolError("No note IDs provided")

    # Get info about notes before deletion
    valid_notes = []
    total_cards = 0

    for note_id in notes:
        try:
            note = col.get_note(note_id)
            card_count = len(note.cards())
            valid_notes.append({"noteId": note_id, "cardCount": card_count})
            total_cards += card_count
        except KeyError:
            logger.info(f"Note {note_id} not found (already deleted)")
            continue
        except Exception as e:
            logger.error(f"Unexpected error checking note {note_id}: {e}", exc_info=True)
            continue

    valid_note_ids = [n["noteId"] for n in valid_notes]
    not_found_count = len(notes) - len(valid_note_ids)

    if len(valid_note_ids) == 0:
        return {
            "deletedCount": 0,
            "notFoundCount": len(notes),
            "requestedIds": notes,
            "message": "No notes were deleted (none of the provided IDs were valid)",
            "hint": "The notes may have already been deleted or the IDs are invalid",
        }

    col.remove_notes(valid_note_ids)

    if not_found_count > 0:
        message = (
            f"Successfully deleted {len(valid_note_ids)} note(s) and {total_cards} card(s). "
            f"{not_found_count} note(s) were not found."
        )
    else:
        message = f"Successfully deleted {len(valid_note_ids)} note(s) and {total_cards} card(s)"

    return {
        "deletedCount": len(valid_note_ids),
        "deletedNoteIds": valid_note_ids,
        "cardsDeleted": total_cards,
        "notFoundCount": not_found_count,
        "requestedIds": notes,
        "message": message,
        "warning": "These notes and cards have been permanently deleted",
        "hint": "Consider syncing with AnkiWeb to propagate deletions to other devices",
    }
