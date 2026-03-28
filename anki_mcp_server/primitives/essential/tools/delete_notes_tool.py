from typing import Any
import logging

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col

logger = logging.getLogger(__name__)


@Tool(
    "delete_notes",
    "Delete notes by their IDs. This will permanently remove the notes and ALL associated cards. "
    "Requires confirmDeletion=true as a safeguard — the call will fail without it. "
    "Use dry_run=true to preview what would be deleted without actually deleting anything "
    "(confirmDeletion is ignored during dry runs). Maximum 100 notes per request. "
    "Returns deletedCount, cardsDeleted, and notFoundCount.",
    write=True,
)
def delete_notes(
    notes: list[int],
    confirmDeletion: bool,
    dry_run: bool = False,
) -> dict[str, Any]:
    col = get_col()

    if not dry_run and not confirmDeletion:
        raise HandlerError(
            "Deletion not confirmed. Set confirmDeletion to true to permanently "
            "delete these notes and all their cards. This action cannot be undone!",
            hint="Set confirmDeletion to true, or use dry_run=true to preview the deletion first",
            code="validation_error",
        )

    if len(notes) > 100:
        raise HandlerError(
            f"Cannot delete more than 100 notes at once for safety. Requested: {len(notes)} notes",
            hint="Delete notes in smaller batches (maximum 100 at a time) for safety",
            code="limit_exceeded",
        )

    if len(notes) == 0:
        raise HandlerError(
            "No note IDs provided",
            code="validation_error",
        )

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
            "dry_run": dry_run,
            "deletedCount": 0,
            "deletedNoteIds": [],
            "cardsDeleted": 0,
            "notFoundCount": len(notes),
            "requestedIds": notes,
            "message": "No notes were deleted (none of the provided IDs were valid)",
            "hint": "The notes may have already been deleted or the IDs are invalid",
        }

    # Dry run: return preview without deleting
    if dry_run:
        if not_found_count > 0:
            message = (
                f"Dry run: would delete {len(valid_note_ids)} note(s) and {total_cards} card(s). "
                f"{not_found_count} note(s) were not found."
            )
        else:
            message = f"Dry run: would delete {len(valid_note_ids)} note(s) and {total_cards} card(s)"

        return {
            "dry_run": True,
            "deletedCount": len(valid_note_ids),
            "deletedNoteIds": valid_note_ids,
            "cardsDeleted": total_cards,
            "notFoundCount": not_found_count,
            "requestedIds": notes,
            "message": message,
            "hint": "Set dry_run=false and confirmDeletion=true to perform the actual deletion",
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
        "dry_run": False,
        "deletedCount": len(valid_note_ids),
        "deletedNoteIds": valid_note_ids,
        "cardsDeleted": total_cards,
        "notFoundCount": not_found_count,
        "requestedIds": notes,
        "message": message,
        "warning": "These notes and cards have been permanently deleted",
        "hint": "Consider syncing with AnkiWeb to propagate deletions to other devices",
    }
