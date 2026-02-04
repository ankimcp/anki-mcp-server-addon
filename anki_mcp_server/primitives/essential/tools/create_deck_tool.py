"""Create deck tool - create a new Anki deck."""
from typing import Any

from anki_mcp_server.tool_decorator import Tool
from anki_mcp_server.handler_wrappers import HandlerError, get_col


@Tool(
    "create_deck",
    'Create a new empty Anki deck. Supports parent::child structure '
    '(e.g., "Japanese::Tokyo" creates parent deck "Japanese" and child deck "Tokyo"). '
    'Maximum 2 levels of nesting allowed. Will not overwrite existing decks. '
    'IMPORTANT: This tool ONLY creates an empty deck. DO NOT add cards or notes after '
    'creating a deck unless the user EXPLICITLY asks to add them. Wait for user instructions '
    'before adding any content.',
    write=True,
)
def create_deck(deck_name: str) -> dict[str, Any]:
    col = get_col()

    parts = deck_name.split("::")
    if len(parts) > 2:
        raise HandlerError(
            f"Deck name can have maximum 2 levels (parent::child). Provided: {len(parts)} levels",
            hint="Use format like 'Parent::Child', not 'A::B::C'",
        )

    if any(part.strip() == "" for part in parts):
        raise HandlerError("Deck name parts cannot be empty")

    all_deck_names = col.decks.all_names_and_ids()
    deck_exists = any(d.name == deck_name for d in all_deck_names)

    deck_id = col.decks.id(deck_name)

    response: dict[str, Any] = {
        "deckId": deck_id,
        "deckName": deck_name,
        "created": not deck_exists,
    }

    if deck_exists:
        response["exists"] = True
        response["message"] = f'Deck "{deck_name}" already exists'
    else:
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
