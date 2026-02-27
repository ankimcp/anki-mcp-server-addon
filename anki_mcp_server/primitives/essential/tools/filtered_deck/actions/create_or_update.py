"""Create or update filtered deck action."""
from typing import Any

from ......handler_wrappers import HandlerError, get_col
from ..models import ORDER_MAP, SearchTermParam
from ._validate import validate_filtered_deck


def create_or_update_impl(
    deck_id: int,
    name: str,
    search_terms: list[SearchTermParam],
    reschedule: bool,
    allow_empty: bool,
) -> dict[str, Any]:
    from anki.decks import FilteredDeckConfig
    from anki.errors import FilteredDeckError, SearchError

    col = get_col()

    # Update path: validate target is actually a filtered deck
    if deck_id != 0:
        validate_filtered_deck(col, deck_id)

    try:
        deck = col.sched.get_or_create_filtered_deck(deck_id)
    except Exception as e:
        err = str(e).lower()
        if "not found" in err:
            raise HandlerError(
                "Deck not found",
                hint="Check deck_id. Use list_decks to see available decks.",
                deck_id=deck_id,
            )
        raise HandlerError(
            f"Failed to get or create filtered deck: {e}",
            deck_id=deck_id,
        )

    deck.name = name
    deck.allow_empty = allow_empty
    deck.config.reschedule = reschedule

    del deck.config.search_terms[:]
    for term in search_terms:
        proto_term = FilteredDeckConfig.SearchTerm(
            search=term.search,
            limit=term.limit,
            order=ORDER_MAP[term.order],
        )
        deck.config.search_terms.append(proto_term)

    try:
        result = col.sched.add_or_update_filtered_deck(deck)
    except SearchError:
        raise HandlerError(
            "Invalid search query",
            hint="Use Anki search syntax: 'deck:Name is:due', 'tag:verb'",
            search_terms=[t.search for t in search_terms],
        )
    except FilteredDeckError as e:
        err = str(e).lower()
        if "no cards" in err or "matched" in err:
            raise HandlerError(
                "No cards matched the search",
                hint="Check search syntax. Set allow_empty=True to create anyway.",
                search_terms=[t.search for t in search_terms],
            )
        if "child" in err:
            raise HandlerError(
                "Filtered decks cannot have child decks",
                hint="Use a different name, or place under a normal parent deck (e.g., 'Study::MyFilter').",
                name=name,
            )
        raise HandlerError(
            f"Filtered deck error: {e}",
            name=name,
        )
    except Exception as e:
        raise HandlerError(
            f"Failed to create/update filtered deck: {e}",
            name=name,
        )

    actual_deck_id = result.id

    # Read back real deck name (Anki may append '+' for uniqueness)
    actual_deck = col.decks.get(actual_deck_id)
    actual_name = actual_deck["name"] if actual_deck else name

    # Query card count
    card_count = len(col.decks.cids(actual_deck_id, children=False))

    return {
        "deck_id": actual_deck_id,
        "name": actual_name,
        "card_count": card_count,
        "search_terms": [
            {"search": t.search, "limit": t.limit, "order": t.order}
            for t in search_terms
        ],
        "reschedule": reschedule,
    }
