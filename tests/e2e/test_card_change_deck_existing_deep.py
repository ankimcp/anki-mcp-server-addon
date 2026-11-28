"""E2E tests: card_change_deck's 10-level limit applies only to creating a deck.

Moving cards into an existing deck deeper than 10 levels works as it always
did; a target over 10 levels that doesn't exist yet is rejected and the card
stays where it was. The deep deck is built with create_deck_past_create_limit
(see its docstring for why that takes a filtered deck).
"""
from __future__ import annotations

from .conftest import unique_id
from .helpers import (
    call_tool,
    card_deck_name,
    create_basic_card,
    create_deck_past_create_limit,
    deck_names,
)


class TestCardChangeDeckExistingDeep:
    def test_move_into_existing_eleven_level_deck(self):
        uid = unique_id()
        source = f"E2EMoveDeepSrc{uid}"
        card_id = create_basic_card(uid, source)
        deep = create_deck_past_create_limit(f"E2EMoveDeepOk{uid}")

        result = call_tool("card_change_deck", {"card_ids": [card_id], "deck": deep})

        assert result.get("isError") is not True
        assert result["moved"] == 1
        assert result["deck_id"] > 0
        assert result["message"] == f"Moved 1 cards to deck '{deep}'"
        assert card_deck_name(card_id) == deep

    def test_move_into_new_eleven_level_deck_rejected(self):
        uid = unique_id()
        source = f"E2EMoveDeepNewSrc{uid}"
        card_id = create_basic_card(uid, source)
        deep = create_deck_past_create_limit(f"E2EMoveDeepNew{uid}")
        sibling = deep.rsplit("::", 1)[0] + "::Other"

        result = call_tool("card_change_deck", {"card_ids": [card_id], "deck": sibling})

        assert result.get("isError") is True
        text = str(result)
        assert "11 levels" in text
        assert "a new deck can have at most 10" in text
        assert sibling not in deck_names()
        assert card_deck_name(card_id) == source
