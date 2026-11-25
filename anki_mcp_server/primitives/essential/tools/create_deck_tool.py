"""Create deck tool - create a new Anki deck."""
from typing import Annotated, Any

from pydantic import Field

from ....tool_decorator import Tool
from ....handler_wrappers import get_col
from ._deck_name_helpers import (
    MAX_DECK_DEPTH,
    MAX_DEPTH_DOC,
    deck_path,
    resolve_deck_target,
)


def _quoted(names: list[str]) -> str:
    return ", ".join(f'"{name}"' for name in names)


@Tool(
    "create_deck",
    "Create a new empty Anki deck, along with any parent decks that don't exist yet. "
    f"{MAX_DEPTH_DOC} "
    "Names match existing decks case-insensitively; an existing deck is reused, "
    "never overwritten. Creates the deck only; it adds no notes or cards. "
    "Returns deckId, deckName, created (false if the deck already existed), "
    "exists (present, and true, only when it already existed), decks (full names from the "
    "top-level deck down to this one), depth, createdParentDecks (full names of "
    "the parent decks this call created) and message. deckName and decks use the "
    "spelling stored in Anki: an existing deck's or parent's spelling wins over "
    "the input's.",
    title="Create Deck",
    write=True,
    destructive_hint=False,
    idempotent_hint=True,
)
def create_deck(
    deck_name: Annotated[
        str,
        Field(
            description=(
                'Deck name; nest with "::", e.g. "Spanish::Verbs" '
                f"(a new deck can have at most {MAX_DECK_DEPTH} levels)"
            )
        ),
    ],
) -> dict[str, Any]:
    col = get_col()
    target = resolve_deck_target(col, deck_name)

    deck_id = col.decks.id(deck_name)
    # The stored name keeps the spelling of whichever deck (or parent) existed
    # first, which can differ in case from the input, and Anki trims each level.
    stored_name = col.decks.name_if_exists(deck_id) or deck_name
    decks = deck_path(stored_name)
    created_parents = [decks[depth - 1] for depth in target.missing_parent_depths]

    response: dict[str, Any] = {
        "deckId": deck_id,
        "deckName": stored_name,
        "created": not target.exists,
        "decks": decks,
        "depth": len(decks),
        "createdParentDecks": created_parents,
    }

    if target.exists:
        response["exists"] = True
        response["message"] = f'Deck "{stored_name}" already exists'
    elif created_parents:
        noun = "parent deck" if len(created_parents) == 1 else "parent decks"
        response["message"] = (
            f'Successfully created deck "{stored_name}" '
            f"and {noun} {_quoted(created_parents)}"
        )
    else:
        response["message"] = f'Successfully created deck "{stored_name}"'

    return response
