"""Create deck tool - create a new Anki deck."""
from typing import Annotated, Any

from pydantic import Field

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col


@Tool(
    "create_deck",
    'Create a new empty Anki deck. Supports parent::child structure '
    '(e.g., "Japanese::Tokyo" creates parent deck "Japanese" and child deck "Tokyo"). '
    'Maximum 2 levels of nesting allowed. Will not overwrite existing decks. '
    'Creates the deck only; it adds no notes or cards. '
    'Returns deckId and created flag (false if deck already existed).',
    title="Create Deck",
    write=True,
    destructive_hint=False,
    idempotent_hint=True,
)
def create_deck(
    deck_name: Annotated[
        str, Field(description='Deck name, optionally "Parent::Child" (max 2 levels)')
    ],
) -> dict[str, Any]:
    col = get_col()

    parts = deck_name.split("::")
    if len(parts) > 2:
        raise HandlerError(
            f"Deck name can have maximum 2 levels (parent::child). Provided: {len(parts)} levels",
            hint="Use format like 'Parent::Child', not 'A::B::C'",
        )

    if any(part.strip() == "" for part in parts):
        raise HandlerError("Deck name parts cannot be empty")

    # id_for_name() matches case-insensitively, the same lookup decks.id() uses
    # to reuse an existing deck.
    deck_exists = col.decks.id_for_name(deck_name) is not None
    parent_exists = len(parts) == 2 and col.decks.id_for_name(parts[0]) is not None

    deck_id = col.decks.id(deck_name)
    # The stored name keeps the spelling of whichever deck (or parent) existed
    # first, which can differ in case from the input.
    stored_name = col.decks.name_if_exists(deck_id) or deck_name

    response: dict[str, Any] = {
        "deckId": deck_id,
        "deckName": stored_name,
        "created": not deck_exists,
    }

    if deck_exists:
        response["exists"] = True
        response["message"] = f'Deck "{stored_name}" already exists'
    else:
        stored_parts = stored_name.split("::")
        if len(stored_parts) == 2:
            parent, child = stored_parts
            response["parentDeck"] = parent
            response["childDeck"] = child
            if parent_exists:
                response["message"] = (
                    f'Successfully created child deck "{child}" '
                    f'under existing parent deck "{parent}"'
                )
            else:
                response["message"] = (
                    f'Successfully created parent deck "{parent}" '
                    f'and child deck "{child}"'
                )
        else:
            response["message"] = f'Successfully created deck "{stored_name}"'

    return response
