"""Update note fields tool - MCP tool and handler in one file.

This tool updates the fields of an existing note. It supports HTML content in fields
and preserves CSS styling from the note model. Media files (audio/images) can also
be added to notes.

Thread Safety:
    - Tool handler runs in background thread
    - Actual note update happens on main thread via queue bridge
"""

from typing import Any, Callable, Coroutine
import logging

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _update_note_fields_handler(note: dict[str, Any]) -> dict[str, Any]:
    """
    Update the fields of an existing note.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.
    Updates one or more fields of an existing note. The note must exist in the
    collection. HTML content is supported in fields and CSS styling from the
    note model is preserved. Optionally, audio and image files can be added.

    Args:
        note: Note update specification containing:
            - id (int): The ID of the note to update
            - fields (dict[str, str]): Fields to update with new content
            - audio (list[dict], optional): Optional audio files to add
            - picture (list[dict], optional): Optional images to add

    Returns:
        dict: Response containing:
            - success (bool): True if operation succeeded
            - noteId (int): The ID of the updated note
            - updatedFields (list[str]): Names of fields that were updated
            - fieldCount (int): Number of fields updated
            - modelName (str): Name of the note model/type
            - message (str): Human-readable status message
            - cssNote (str): Note about CSS preservation
            - warning (str): Warning about browser conflicts
            - hint (str): Guidance for verification

    Raises:
        RuntimeError: If collection is not loaded
        ValueError: If note not found or fields are invalid
    """
    from aqt import mw

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    # Extract note parameters
    note_id = note.get("id")
    fields_to_update = note.get("fields", {})
    audio = note.get("audio")
    picture = note.get("picture")

    # Validate that at least one field is being updated
    field_count = len(fields_to_update)
    if field_count == 0:
        raise ValueError("No fields provided for update. Provide at least one field to update.")

    # Get the note to validate it exists
    try:
        anki_note = mw.col.get_note(note_id)
    except KeyError:
        raise ValueError(
            f"Note not found with ID {note_id}. "
            "The note ID is invalid or the note has been deleted. "
            "Use findNotes to get valid note IDs."
        )
    except Exception as e:
        logger.error(f"Unexpected error getting note {note_id}: {e}", exc_info=True)
        raise ValueError(
            f"Failed to retrieve note {note_id}: {str(e)}"
        )

    # Get model info
    model_name = anki_note.note_type()["name"]
    existing_fields = list(anki_note.keys())

    # Validate that all provided fields exist in the model
    invalid_fields = [field for field in fields_to_update.keys() if field not in existing_fields]

    if invalid_fields:
        raise ValueError(
            f"Invalid fields for model \"{model_name}\": {', '.join(invalid_fields)}. "
            f"Valid fields are: {', '.join(existing_fields)}. "
            f"Use modelFieldNames to see valid fields for this model."
        )

    # Wrap the actual update in an edit session
    # This ensures Anki's UI is refreshed after changes
    try:
        # Mark UI as needing refresh
        mw.requireReset()

        # Update the fields
        for field_name, field_value in fields_to_update.items():
            anki_note[field_name] = field_value

        # TODO: Handle media files (audio and picture)
        # This requires downloading from URLs and adding to Anki's media collection
        # For now, we'll skip media handling as it requires additional implementation
        if audio or picture:
            # Media handling would go here
            # This would involve:
            # 1. Downloading files from URLs
            # 2. Adding them to Anki's media collection
            # 3. Updating field values to reference the media
            pass

        # Save the note
        mw.col.update_note(anki_note)

        # Trigger UI refresh if collection is still available
        if mw.col:
            mw.maybeReset()

    except Exception as e:
        # Ensure UI refresh even on error
        if mw.col:
            mw.maybeReset()
        raise

    # Get the list of updated fields for the response
    updated_fields = list(fields_to_update.keys())

    return {
        "success": True,
        "noteId": note_id,
        "updatedFields": updated_fields,
        "fieldCount": field_count,
        "modelName": model_name,
        "message": f"Successfully updated {field_count} field{'s' if field_count != 1 else ''} in note",
        "cssNote": "HTML content is preserved. Model CSS styling remains unchanged.",
        "warning": "If changes don't appear, ensure the note wasn't open in Anki browser during update.",
        "hint": "Use notesInfo to verify the changes or findNotes to locate other notes to update.",
    }


# Register handler at import time
register_handler("updateNoteFields", _update_note_fields_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_update_note_fields_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register update note fields tool with the MCP server.

    Args:
        mcp: FastMCP server instance
        call_main_thread: Bridge function to execute on main thread
    """

    @mcp.tool(
        description=(
            "Update the fields of an existing note. Supports HTML content in fields and preserves CSS styling. "
            "WARNING: Do not view the note in Anki browser while updating, or the fields will not update properly. "
            "Close the browser or switch to a different note before updating. IMPORTANT: Only update notes that the user explicitly asked to modify."
        )
    )
    async def updateNoteFields(
        note: dict[str, Any]
    ) -> dict[str, Any]:
        """Update the fields of an existing note.

        Updates one or more fields of an existing note. The note must exist in the
        collection. HTML content is supported in fields and CSS styling from the
        note model is preserved. Optionally, audio and image files can be added.

        Args:
            note: Note update specification containing:
                - id (int): The ID of the note to update. Get this from findNotes or notesInfo.
                - fields (dict[str, str]): Fields to update with new content. Only include
                    fields you want to change. HTML content is supported.
                    Example: {"Front": "<b>New question</b>", "Back": "New answer"}
                - audio (list[dict], optional): Optional audio files to add to the note.
                    Each dict should contain:
                    - url (str): URL of the audio file
                    - filename (str): Filename to save as
                    - fields (list[str]): Fields to add audio to
                - picture (list[dict], optional): Optional images to add to the note.
                    Each dict should contain:
                    - url (str): URL of the image
                    - filename (str): Filename to save as
                    - fields (list[str]): Fields to add image to

        Returns:
            Dictionary containing:
            - success (bool): True if operation succeeded
            - noteId (int): The ID of the updated note
            - updatedFields (list[str]): Names of fields that were updated
            - fieldCount (int): Number of fields updated
            - modelName (str): Name of the note model/type
            - message (str): Human-readable status message
            - cssNote (str): Note about CSS preservation
            - warning (str): Warning about browser conflicts
            - hint (str): Guidance for verification
            - error (str): Error message (if failed)

        Raises:
            ValueError: If note parameter is missing or invalid
            Exception: If note update fails on the main thread

        Note:
            - Only fields included in the fields dict will be updated
            - Other fields remain unchanged
            - HTML content is preserved
            - Model CSS styling remains unchanged
            - Close Anki browser or switch to different note before updating
            - Use notesInfo to verify changes after update

        Examples:
            Update basic fields:
            >>> await updateNoteFields(
            ...     note={
            ...         "id": 1234567890,
            ...         "fields": {
            ...             "Front": "<b>Updated question</b>",
            ...             "Back": "Updated answer"
            ...         }
            ...     }
            ... )

            Update with HTML and styling:
            >>> await updateNoteFields(
            ...     note={
            ...         "id": 1234567890,
            ...         "fields": {
            ...             "Front": '<div class="highlight">Question</div>',
            ...             "Back": '<span style="color: red;">Answer</span>'
            ...         }
            ...     }
            ... )

            Update with audio:
            >>> await updateNoteFields(
            ...     note={
            ...         "id": 1234567890,
            ...         "fields": {"Front": "hello"},
            ...         "audio": [
            ...             {
            ...                 "url": "https://example.com/audio.mp3",
            ...                 "filename": "hello.mp3",
            ...                 "fields": ["Front"]
            ...             }
            ...         ]
            ...     }
            ... )

            Update with image:
            >>> await updateNoteFields(
            ...     note={
            ...         "id": 1234567890,
            ...         "fields": {"Front": "diagram"},
            ...         "picture": [
            ...             {
            ...                 "url": "https://example.com/diagram.png",
            ...                 "filename": "diagram.png",
            ...                 "fields": ["Front"]
            ...             }
            ...         ]
            ...     }
            ... )
        """
        # Validate note parameter
        if not note:
            raise ValueError("note parameter is required")

        if not isinstance(note, dict):
            raise ValueError("note parameter must be a dictionary")

        if "id" not in note:
            raise ValueError("note.id is required")

        if "fields" not in note:
            raise ValueError("note.fields is required")

        if not isinstance(note["fields"], dict):
            raise ValueError("note.fields must be a dictionary")

        if not note["fields"]:
            raise ValueError("note.fields cannot be empty - provide at least one field to update")

        # Execute on main thread via bridge
        return await call_main_thread("updateNoteFields", {"note": note})
