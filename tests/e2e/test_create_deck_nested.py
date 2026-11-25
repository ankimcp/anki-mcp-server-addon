"""E2E test: create_deck's response reflects which levels were actually created.

With parent "A" already present, create_deck("A::B") creates only the child,
so neither createdParentDecks nor the message may claim the parent was created.
"""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool


class TestCreateDeckNested:
    def test_existing_parent_only_child_created(self):
        parent = f"E2ENestedParent{unique_id()}"
        parent_result = call_tool("create_deck", {"deck_name": parent})
        assert parent_result["created"] is True

        child = f"{parent}::Child"
        result = call_tool("create_deck", {"deck_name": child})

        assert result.get("isError") is not True
        assert result["created"] is True
        assert result["deckId"] != parent_result["deckId"]
        assert result["decks"] == [parent, child]
        assert result["depth"] == 2
        assert result["createdParentDecks"] == []
        assert result["message"] == f'Successfully created deck "{child}"'
        assert "parent deck" not in result["message"]
