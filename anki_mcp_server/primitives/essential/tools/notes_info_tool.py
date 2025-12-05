"""Notes info tool - MCP tool and handler in one file.

This tool retrieves comprehensive information about Anki notes including all
fields, tags, model info, and associated cards. It's typically used after
findNotes to get complete note data.

Thread Safety:
    - Tool handler runs in background thread
    - Actual note info retrieval happens on main thread via queue bridge
"""

from typing import Any, Callable, Coroutine
import logging

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _notes_info_handler(notes: list[int]) -> dict[str, Any]:
    """
    Get detailed information about specific notes.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.

    Args:
        notes: List of note IDs to retrieve information for

    Returns:
        dict: Response containing:
            - success (bool): True if operation succeeded
            - notes (list): List of note information dictionaries
            - count (int): Number of valid notes returned
            - notFound (int): Number of notes that weren't found
            - requestedIds (list): Original list of requested IDs
            - message (str): Human-readable status message
            - models (list): Unique model names across all notes
            - cssNote (str): Hint about CSS styling
            - hint (str): Additional usage guidance

    Raises:
        RuntimeError: If collection is not loaded
    """
    from aqt import mw

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    # Retrieve note information
    notes_data = []
    for note_id in notes:
        try:
            note = mw.col.get_note(note_id)

            # Build fields dict with value and order
            fields_dict = {}
            for i, (field_name, field_value) in enumerate(note.items()):
                fields_dict[field_name] = {
                    "value": field_value,
                    "order": i
                }

            # Get card IDs for this note
            card_ids = [card.id for card in note.cards()]

            note_info = {
                "noteId": note_id,
                "modelName": note.note_type()["name"],
                "tags": note.tags,
                "fields": fields_dict,
                "cards": card_ids,
                "mod": note.mod,
            }
            notes_data.append(note_info)
        except KeyError as e:
            # Specific error for missing note
            logger.warning(f"Note {note_id} not found: {e}")
            continue
        except Exception as e:
            # Log unexpected errors before skipping
            logger.error(f"Unexpected error retrieving note {note_id}: {e}", exc_info=True)
            continue

    # Calculate statistics
    valid_count = len(notes_data)
    not_found_count = len(notes) - valid_count

    # Get unique model names
    unique_models = list(set(note["modelName"] for note in notes_data))

    # Build response message
    if not_found_count > 0:
        message = (
            f"Retrieved {valid_count} note(s). "
            f"{not_found_count} note(s) not found (possibly deleted)."
        )
    else:
        message = f"Successfully retrieved information for {valid_count} note(s)"

    # Determine hint based on results
    if valid_count > 0:
        hint = (
            "Fields may contain HTML. Use updateNoteFields to modify content. "
            "Do not view notes in Anki browser while updating."
        )
    else:
        hint = "No valid notes found. They may have been deleted."

    return {
        "success": True,
        "notes": notes_data,
        "count": valid_count,
        "notFound": not_found_count,
        "requestedIds": notes,
        "message": message,
        "models": unique_models,
        "cssNote": (
            "Each note model has its own CSS styling. "
            "Use modelStyling tool to get CSS for specific models."
        ),
        "hint": hint,
    }


# Register handler at import time
register_handler("notesInfo", _notes_info_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_notes_info_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register notes info tool with the MCP server.

    Args:
        mcp: FastMCP server instance
        call_main_thread: Bridge function to execute on main thread
    """

    @mcp.tool(
        description=(
            "Get detailed information about specific notes including all fields, tags, model info, and CSS styling. "
            "Use this after findNotes to get complete note data. Includes CSS for proper rendering awareness."
        )
    )
    async def notesInfo(notes: list[int]) -> dict[str, Any]:
        """Get detailed information about specific notes.

        Retrieves comprehensive information about notes including fields, tags,
        model info, and associated card IDs. This is typically used after
        findNotes to get the full details of specific notes.

        Args:
            notes: Array of note IDs to get information for (max 100 at once for
                performance). Get these IDs from findNotes tool.

        Returns:
            Dictionary containing:
            - success: True if operation succeeded
            - notes: List of note information dictionaries
            - count: Number of valid notes returned
            - notFound: Number of notes that weren't found
            - requestedIds: Original list of requested note IDs
            - message: Human-readable status message
            - models: List of unique model names across all notes
            - cssNote: Hint about getting CSS styling
            - hint: Additional guidance for using the results

        Raises:
            Exception: If notes parameter is invalid or Anki operation fails

        Note:
            - Maximum 100 notes at once for performance
            - Fields may contain HTML content
            - Each note model has its own CSS styling
            - Use updateNoteFields to modify content
        """
        # Validate input
        if not notes:
            raise ValueError("notes parameter cannot be empty")

        if len(notes) > 100:
            raise ValueError(f"Maximum 100 notes at once (requested: {len(notes)})")

        # Execute on main thread via bridge
        return await call_main_thread("notesInfo", {"notes": notes})
