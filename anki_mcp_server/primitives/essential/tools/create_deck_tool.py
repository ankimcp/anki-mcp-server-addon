"""Create deck tool - MCP tool and handler in one file."""
from typing import Any, Callable, Coroutine
import logging

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _create_deck_handler(deck_name: str) -> dict[str, Any]:
    """
    Create a new Anki deck.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.
    Includes edit session handling to ensure UI refresh after deck creation.

    Args:
        deck_name: Name of the deck to create. Can include "::" for hierarchy
                  (e.g., "Parent::Child"). Maximum 2 levels of nesting.

    Returns:
        dict: Creation result with structure:
            - success (bool): True if operation completed successfully
            - deckId (int): The deck ID (for new or existing deck)
            - deckName (str): The name of the deck
            - message (str): Human-readable result message
            - created (bool): True if newly created, False if already existed
            - exists (bool): True if deck already existed (only when created=False)
            - parentDeck (str): Parent deck name if hierarchical (optional)
            - childDeck (str): Child deck name if hierarchical (optional)

    Raises:
        RuntimeError: If collection is not loaded
        ValueError: If deck_name validation fails (empty parts, too many levels)
    """
    from aqt import mw

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    # Validate deck name doesn't have more than 2 levels
    parts = deck_name.split("::")
    if len(parts) > 2:
        raise ValueError(
            f"Deck name can have maximum 2 levels (parent::child). "
            f"Provided: {len(parts)} levels"
        )

    # Check for empty parts
    if any(part.strip() == "" for part in parts):
        raise ValueError("Deck name parts cannot be empty")

    # Check if deck already exists before creating
    all_deck_names = mw.col.decks.all_names_and_ids()
    deck_exists = any(d.name == deck_name for d in all_deck_names)

    try:
        # Mark UI as needing refresh (WRITE operation requires edit session)
        mw.requireReset()

        # In Anki's API, decks.id() creates the deck if it doesn't exist
        # and returns the deck ID. It's idempotent.
        deck_id = mw.col.decks.id(deck_name)

        # Trigger UI refresh after successful creation
        if mw.col:
            mw.maybeReset()

    except Exception as e:
        # Ensure UI refresh even on error
        if mw.col:
            mw.maybeReset()
        raise

    # Build response
    response: dict[str, Any] = {
        "success": True,
        "deckId": deck_id,
        "deckName": deck_name,
        "created": not deck_exists,
    }

    if deck_exists:
        response["exists"] = True
        response["message"] = f'Deck "{deck_name}" already exists'
    else:
        # Newly created deck
        if len(parts) == 2:
            response["parentDeck"] = parts[0]
            response["childDeck"] = parts[1]
            response["message"] = (
                f'Successfully created parent deck "{parts[0]}" '
                f'and child deck "{parts[1]}"'
            )
        else:
            response["message"] = f'Successfully created deck "{deck_name}"'

    return response


# Register handler at import time
register_handler("create_deck", _create_deck_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_create_deck_tools(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register deck creation tools with the MCP server."""

    @mcp.tool(
        description=(
            'Create a new empty Anki deck. Supports parent::child structure '
            '(e.g., "Japanese::Tokyo" creates parent deck "Japanese" and child deck "Tokyo"). '
            'Maximum 2 levels of nesting allowed. Will not overwrite existing decks. '
            'IMPORTANT: This tool ONLY creates an empty deck. DO NOT add cards or notes after '
            'creating a deck unless the user EXPLICITLY asks to add them. Wait for user instructions '
            'before adding any content.'
        )
    )
    async def create_deck(deck_name: str) -> dict[str, Any]:
        """Create a new empty Anki deck.

        Creates a new deck with the specified name. Supports hierarchical deck structure
        using "::" separator (e.g., "Parent::Child"). Maximum 2 levels of nesting allowed.

        Args:
            deck_name: The name of the deck to create. Use "::" for parent::child structure
                      (max 2 levels). Examples: "Japanese", "Japanese::Vocabulary"

        Returns:
            Dictionary with creation result containing:
            - success (bool): Whether the deck was created successfully
            - deckId (int): The ID of the created deck (only if created)
            - deckName (str): The name of the deck
            - message (str): Human-readable result message
            - created (bool): True if newly created, False if already existed
            - exists (bool): True if deck already existed (optional)
            - parentDeck (str): Parent deck name if hierarchical (optional)
            - childDeck (str): Child deck name if hierarchical (optional)

        Raises:
            Exception: If deck creation fails on the main thread

        Note:
            - Deck names cannot be empty
            - Maximum 2 levels of nesting (Parent::Child)
            - If deck already exists, returns success with exists=True
            - Creates parent deck automatically if using hierarchical structure
        """
        return await call_main_thread("create_deck", {"deck_name": deck_name})
