"""GUI Add Cards tool - MCP tool and handler in one file."""
from typing import Any, Callable, Coroutine

from ....handler_registry import register_handler


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw and dialogs
# ============================================================================

def _gui_add_cards_handler(
    deck_name: str,
    model_name: str,
    fields: dict[str, str],
    tags: list[str] | None = None
) -> dict[str, Any]:
    """
    Open Anki Add Cards dialog.

    This function runs on the Qt MAIN THREAD and has direct access to mw.

    Args:
        deck_name: The deck to add the note to
        model_name: The note type/model to use (e.g., "Basic", "Cloze")
        fields: Field values as key-value pairs
        tags: Optional tags to add to the note

    Returns:
        dict: Result with structure:
            - success (bool): Whether the dialog was opened
            - message (str): Human-readable result message

    Raises:
        RuntimeError: If main window is not available
    """
    from aqt import mw, dialogs

    # Check if main window is available
    if mw is None:
        raise RuntimeError("Main window not available")

    # Open the Add Cards dialog
    dialogs.open('AddCards', mw)

    return {
        "success": True,
        "message": "Add Cards dialog opened successfully",
    }


# Register handler at import time
register_handler("gui_add_cards", _gui_add_cards_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_gui_add_cards_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register gui-add-cards tool with the MCP server."""

    @mcp.tool(
        description=(
            "Open Anki Add Cards dialog with preset note details (deck, model, fields, tags). "
            "Returns potential note ID. "
            "IMPORTANT: Only use when user explicitly requests opening the Add Cards dialog. "
            "This tool is for note editing/creation workflows. Use this when user wants to "
            "manually review and finalize note creation in the GUI."
        )
    )
    async def gui_add_cards(
        deck_name: str,
        model_name: str,
        fields: dict[str, str],
        tags: list[str] | None = None
    ) -> dict[str, Any]:
        """Open Anki Add Cards dialog with preset note details.

        Opens the Anki Add Cards dialog window with pre-filled note details, allowing
        the user to review and finalize the note creation manually in the GUI. This is
        useful when you want to prepare a note but let the user make final adjustments
        before adding it to their collection.

        Args:
            deck_name: The deck to add the note to (e.g., "Default", "Japanese::Vocabulary")
            model_name: The note type/model to use (e.g., "Basic", "Cloze")
            fields: Field values to pre-fill as key-value pairs
                   (e.g., {"Front": "question", "Back": "answer"})
            tags: Optional tags to pre-populate on the note (default: None)

        Returns:
            Dictionary containing:
            - success (bool): Whether the dialog was opened successfully
            - noteId (int | None): Potential note ID (if available)
            - deckName (str): The deck specified for the note
            - modelName (str): The note type specified
            - message (str): Human-readable result message
            - hint (str): Helpful hint about what happens next
            - error (str): Error message (if failed)
            - emptyFields (list[str]): List of empty field names (if validation failed)
            - providedFields (list[str]): List of provided field names (if field error)

        Raises:
            Exception: If opening the dialog fails on the main thread

        Note:
            - All field values must be non-empty strings
            - The note is NOT created until the user clicks "Add" in the dialog
            - User can still modify the note details in the GUI before adding
            - Use modelNames to see available note types
            - Use modelFieldNames to see required fields for a model
            - Use list_decks to see available decks

        Examples:
            Basic note preparation:
            >>> await gui_add_cards(
            ...     deck_name="Default",
            ...     model_name="Basic",
            ...     fields={"Front": "What is Python?", "Back": "A programming language"}
            ... )

            Note with tags:
            >>> await gui_add_cards(
            ...     deck_name="Languages::French",
            ...     model_name="Basic",
            ...     fields={"Front": "bonjour", "Back": "hello"},
            ...     tags=["greetings", "beginner"]
            ... )

            Cloze note:
            >>> await gui_add_cards(
            ...     deck_name="Medical",
            ...     model_name="Cloze",
            ...     fields={"Text": "The {{c1::heart}} pumps blood through the body"},
            ...     tags=["anatomy", "cardiovascular"]
            ... )
        """
        return await call_main_thread("gui_add_cards", {
            "deck_name": deck_name,
            "model_name": model_name,
            "fields": fields,
            "tags": tags or []
        })
