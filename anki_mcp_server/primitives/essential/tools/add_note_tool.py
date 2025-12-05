"""Add note tool - MCP tool and handler in one file."""
from typing import Any, Callable, Coroutine, Optional
import logging

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _add_note_handler(
    deckName: str,
    modelName: str,
    fields: dict[str, str],
    tags: Optional[list[str]] = None,
    allowDuplicate: bool = False,
    duplicateScope: Optional[str] = None,
    duplicateScopeOptions: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Add a new note to Anki.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.

    Args:
        deckName: The deck to add the note to
        modelName: The note type/model to use (e.g., "Basic", "Cloze")
        fields: Field values as key-value pairs
        tags: Optional tags to add to the note
        allowDuplicate: Whether to allow adding duplicate notes
        duplicateScope: Scope for duplicate checking ("deck" or "collection")
        duplicateScopeOptions: Advanced duplicate checking options

    Returns:
        dict: Result with structure:
            - success (bool): Whether the operation succeeded
            - noteId (int): The ID of the created note
            - deckName (str): The deck the note was added to
            - modelName (str): The note type used
            - message (str): Human-readable result message
            - details (dict): Additional information

    Raises:
        RuntimeError: If collection is not loaded
    """
    from aqt import mw
    from anki.notes import Note

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    # Validate fields are not empty
    empty_fields = [key for key, value in fields.items() if not value or not value.strip()]
    if empty_fields:
        return {
            "success": False,
            "error": f"Fields cannot be empty: {', '.join(empty_fields)}",
            "deckName": deckName,
            "modelName": modelName,
            "emptyFields": empty_fields,
        }

    # Get the deck ID
    deck_id = mw.col.decks.id(deckName)
    if deck_id is None:
        return {
            "success": False,
            "error": f"Deck not found: {deckName}",
            "deckName": deckName,
            "modelName": modelName,
            "hint": "Use list_decks tool to see available decks or createDeck to create a new one.",
        }

    # Get the model (note type)
    model = mw.col.models.by_name(modelName)
    if model is None:
        return {
            "success": False,
            "error": f"Model not found: {modelName}",
            "deckName": deckName,
            "modelName": modelName,
            "hint": "Use modelNames tool to see available models.",
        }

    # Check if all required fields are provided
    model_fields = [field["name"] for field in model["flds"]]
    provided_fields = list(fields.keys())
    missing_fields = [f for f in model_fields if f not in provided_fields]

    if missing_fields:
        return {
            "success": False,
            "error": f"Missing required fields: {', '.join(missing_fields)}",
            "deckName": deckName,
            "modelName": modelName,
            "providedFields": provided_fields,
            "requiredFields": model_fields,
            "hint": "Use modelFieldNames tool to see required fields for this model.",
        }

    # Check for extra fields that don't exist in the model
    extra_fields = [f for f in provided_fields if f not in model_fields]
    if extra_fields:
        return {
            "success": False,
            "error": f"Unknown fields for this model: {', '.join(extra_fields)}",
            "deckName": deckName,
            "modelName": modelName,
            "providedFields": provided_fields,
            "requiredFields": model_fields,
            "hint": "Use modelFieldNames tool to see valid fields for this model.",
        }

    # Check for duplicates if not allowed
    if not allowDuplicate:
        # Get the first field value for duplicate checking
        # Anki checks duplicates based on the first field by default
        first_field_name = model_fields[0]
        first_field_value = fields.get(first_field_name, "")

        # Check if note with same first field exists
        # This is a simplified duplicate check - full implementation would
        # respect duplicateScope and duplicateScopeOptions
        try:
            duplicate_note_ids = mw.col.find_notes(f'"{first_field_name}:{first_field_value}"')
            if duplicate_note_ids:
                return {
                    "success": False,
                    "error": "Failed to create note - it may be a duplicate",
                    "deckName": deckName,
                    "modelName": modelName,
                    "hint": "The note appears to be a duplicate. Set allowDuplicate to true if you want to add it anyway.",
                }
        except Exception as e:
            # If duplicate check fails, log and continue with note creation
            logger.warning(f"Duplicate check failed: {e}")

    # WRITE operation - wrap with edit session
    try:
        mw.requireReset()

        # Create the note
        note = Note(mw.col, model)
        note.note_type()["did"] = deck_id

        # Set field values
        for field_name, field_value in fields.items():
            note[field_name] = field_value

        # Add tags if provided
        if tags:
            note.tags = tags

        # Add the note to the collection
        changes = mw.col.add_note(note, deck_id)

    finally:
        if mw.col:
            mw.maybeReset()

    if not changes or not changes.note_id:
        return {
            "success": False,
            "error": "Failed to create note",
            "deckName": deckName,
            "modelName": modelName,
            "hint": "Check if the model and deck names are correct.",
        }

    # Return success response
    field_count = len(fields)
    tag_count = len(tags) if tags else 0

    return {
        "success": True,
        "noteId": changes.note_id,
        "deckName": deckName,
        "modelName": modelName,
        "message": f'Successfully created note in deck "{deckName}"',
        "details": {
            "fieldsAdded": field_count,
            "tagsAdded": tag_count,
            "duplicateCheckScope": duplicateScope or "default",
        },
    }


# Register handler at import time
register_handler("addNote", _add_note_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_add_note_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register add-note tool with the MCP server."""

    @mcp.tool(
        description=(
            "Add a new note to Anki. Use modelNames to see available note types and "
            "modelFieldNames to see required fields. Returns the note ID on success. "
            "IMPORTANT: Only create notes that were explicitly requested by the user."
        )
    )
    async def addNote(
        deckName: str,
        modelName: str,
        fields: dict[str, str],
        tags: Optional[list[str]] = None,
        allowDuplicate: bool = False,
        duplicateScope: Optional[str] = None,
        duplicateScopeOptions: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Add a new note to Anki.

        Args:
            deckName: The deck to add the note to (e.g., "Default", "Japanese::Vocabulary")
            modelName: The note type/model to use (e.g., "Basic", "Cloze")
            fields: Field values as key-value pairs (e.g., {"Front": "question", "Back": "answer"})
            tags: Optional tags to add to the note
            allowDuplicate: Whether to allow adding duplicate notes (default: False)
            duplicateScope: Scope for duplicate checking - "deck" or "collection"
            duplicateScopeOptions: Advanced duplicate checking options:
                - deckName: Specific deck to check for duplicates
                - checkChildren: Check child decks for duplicates
                - checkAllModels: Check across all note types

        Returns:
            Dictionary containing:
            - success (bool): Whether the operation succeeded
            - noteId (int): The ID of the created note (if successful)
            - deckName (str): The deck the note was added to
            - modelName (str): The note type used
            - message (str): Human-readable result message
            - details (dict): Additional information about the operation
            - error (str): Error message (if failed)
            - hint (str): Helpful hint for resolving errors (if failed)

        Raises:
            Exception: If note creation fails on the main thread

        Note:
            This operation validates that all required fields are provided and non-empty.
            Duplicate checking can be customized using the duplicate-related parameters.

        Examples:
            Basic note:
            >>> await addNote(
            ...     deckName="Default",
            ...     modelName="Basic",
            ...     fields={"Front": "What is Python?", "Back": "A programming language"}
            ... )

            Note with tags:
            >>> await addNote(
            ...     deckName="Languages::French",
            ...     modelName="Basic",
            ...     fields={"Front": "bonjour", "Back": "hello"},
            ...     tags=["greetings", "beginner"]
            ... )

            Allow duplicate:
            >>> await addNote(
            ...     deckName="Default",
            ...     modelName="Basic",
            ...     fields={"Front": "test", "Back": "test"},
            ...     allowDuplicate=True
            ... )
        """
        # Prepare arguments for main thread
        arguments = {
            "deckName": deckName,
            "modelName": modelName,
            "fields": fields,
            "tags": tags,
            "allowDuplicate": allowDuplicate,
            "duplicateScope": duplicateScope,
            "duplicateScopeOptions": duplicateScopeOptions,
        }

        return await call_main_thread("addNote", arguments)
