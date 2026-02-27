"""Tests for filtered_deck multi-action tool."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool, list_tools


# -- Helpers ------------------------------------------------------------------

def _create_notes_in_deck(deck_name: str, count: int, uid: str) -> list[int]:
    """Create *count* Basic notes in *deck_name* and return their note IDs."""
    call_tool("create_deck", {"deck_name": deck_name})
    note_ids: list[int] = []
    for i in range(count):
        result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Q{i} {uid}",
                "Back": f"A{i} {uid}",
            },
        })
        note_ids.append(result["note_id"])
    return note_ids


def _get_deck_names() -> list[str]:
    """Return all deck names from list_decks."""
    result = call_tool("list_decks")
    return [d["name"] for d in result["decks"]]


def _find_deck(name: str) -> dict | None:
    """Find a deck by exact name in list_decks output, or None."""
    result = call_tool("list_decks")
    for d in result["decks"]:
        if d["name"] == name:
            return d
    return None


# -- TestFilteredDeckCreateOrUpdate -------------------------------------------

class TestFilteredDeckCreateOrUpdate:
    """Tests for the create_or_update action."""

    def test_tool_exists(self):
        """filtered_deck tool should be registered."""
        tools = list_tools()
        tool_names = [t["name"] for t in tools]
        assert "filtered_deck" in tool_names

    def test_create_with_one_search_term(self):
        """Create a filtered deck with a single search term."""
        uid = unique_id()
        source_deck = f"E2E::FDSource1_{uid}"
        _create_notes_in_deck(source_deck, 3, uid)

        fd_name = f"E2E::FD1_{uid}"
        result = call_tool("filtered_deck", {
            "params": {
                "action": "create_or_update",
                "name": fd_name,
                "search_terms": [
                    {"search": f"deck:\"{source_deck}\"", "limit": 100, "order": "random"},
                ],
            },
        })

        assert result.get("isError") is not True
        assert result["deck_id"] > 0
        assert result["name"] == fd_name
        assert result["card_count"] == 3
        assert len(result["search_terms"]) == 1
        assert result["reschedule"] is True

    def test_create_with_two_search_terms(self):
        """Create a filtered deck with two search terms (boundary)."""
        uid = unique_id()
        source_deck = f"E2E::FDSource2_{uid}"
        _create_notes_in_deck(source_deck, 5, uid)

        fd_name = f"E2E::FD2_{uid}"
        result = call_tool("filtered_deck", {
            "params": {
                "action": "create_or_update",
                "name": fd_name,
                "search_terms": [
                    {"search": f"deck:\"{source_deck}\"", "limit": 3, "order": "due"},
                    {"search": f"deck:\"{source_deck}\"", "limit": 2, "order": "added"},
                ],
            },
        })

        assert result.get("isError") is not True
        assert result["deck_id"] > 0
        assert result["name"] == fd_name
        # Card count should be <= 5 (combined limits, but cards won't be
        # pulled twice -- once a card is in the filtered deck it's excluded
        # from the second term's search).
        assert result["card_count"] <= 5
        assert result["card_count"] > 0
        assert len(result["search_terms"]) == 2

    def test_create_allow_empty_true_with_zero_matches(self):
        """Create with allow_empty=True and a search matching 0 cards."""
        uid = unique_id()
        fd_name = f"E2E::FDEmpty_{uid}"

        result = call_tool("filtered_deck", {
            "params": {
                "action": "create_or_update",
                "name": fd_name,
                "search_terms": [
                    {"search": f"deck:\"NonExistentDeck_{uid}\"", "limit": 100},
                ],
                "allow_empty": True,
            },
        })

        assert result.get("isError") is not True
        assert result["deck_id"] > 0
        assert result["card_count"] == 0

    def test_create_name_collision_appends_plus(self):
        """Creating a filtered deck with a name that already exists
        should result in Anki appending '+' to make it unique."""
        uid = unique_id()
        existing_deck = f"E2E::Collision_{uid}"

        # Create a regular deck with that name first
        call_tool("create_deck", {"deck_name": existing_deck})

        # Now create a filtered deck with the same name
        result = call_tool("filtered_deck", {
            "params": {
                "action": "create_or_update",
                "name": existing_deck,
                "search_terms": [
                    {"search": "deck:*", "limit": 1},
                ],
                "allow_empty": True,
            },
        })

        assert result.get("isError") is not True
        assert result["deck_id"] > 0
        # Anki should have appended '+' to avoid the collision
        assert result["name"] != existing_deck
        assert result["name"].startswith(existing_deck)
        assert "+" in result["name"]

    def test_update_existing_filtered_deck(self):
        """Update an existing filtered deck (change search terms)."""
        uid = unique_id()
        source_deck_a = f"E2E::FDUpdSrcA_{uid}"
        source_deck_b = f"E2E::FDUpdSrcB_{uid}"
        _create_notes_in_deck(source_deck_a, 2, uid + "a")
        _create_notes_in_deck(source_deck_b, 4, uid + "b")

        fd_name = f"E2E::FDUpd_{uid}"

        # Create filtered deck pulling from source A
        create_result = call_tool("filtered_deck", {
            "params": {
                "action": "create_or_update",
                "name": fd_name,
                "search_terms": [
                    {"search": f"deck:\"{source_deck_a}\"", "limit": 100},
                ],
            },
        })

        assert create_result.get("isError") is not True
        assert create_result["card_count"] == 2
        deck_id = create_result["deck_id"]

        # Update: switch to pulling from source B
        update_result = call_tool("filtered_deck", {
            "params": {
                "action": "create_or_update",
                "deck_id": deck_id,
                "name": fd_name,
                "search_terms": [
                    {"search": f"deck:\"{source_deck_b}\"", "limit": 100},
                ],
            },
        })

        assert update_result.get("isError") is not True
        assert update_result["deck_id"] == deck_id
        # After update + rebuild, should have 4 cards from source B
        # (the 2 from source A were returned first)
        assert update_result["card_count"] == 4


# -- TestFilteredDeckRebuild --------------------------------------------------

class TestFilteredDeckRebuild:
    """Tests for the rebuild action."""

    def test_rebuild_returns_card_count(self):
        """Rebuild should return updated card_count."""
        uid = unique_id()
        source_deck = f"E2E::FDRebSrc_{uid}"
        _create_notes_in_deck(source_deck, 3, uid)

        fd_name = f"E2E::FDReb_{uid}"

        # Create filtered deck
        create_result = call_tool("filtered_deck", {
            "params": {
                "action": "create_or_update",
                "name": fd_name,
                "search_terms": [
                    {"search": f"deck:\"{source_deck}\"", "limit": 100},
                ],
            },
        })
        assert create_result.get("isError") is not True
        deck_id = create_result["deck_id"]
        assert create_result["card_count"] == 3

        # Rebuild (should re-pull the same 3 cards)
        rebuild_result = call_tool("filtered_deck", {
            "params": {
                "action": "rebuild",
                "deck_id": deck_id,
            },
        })

        assert rebuild_result.get("isError") is not True
        assert rebuild_result["deck_id"] == deck_id
        assert rebuild_result["card_count"] == 3
        assert "message" in rebuild_result

    def test_rebuild_picks_up_new_notes(self):
        """After adding more notes, rebuild should pull them in."""
        uid = unique_id()
        source_deck = f"E2E::FDRebNew_{uid}"
        _create_notes_in_deck(source_deck, 2, uid)

        fd_name = f"E2E::FDRebNewFD_{uid}"

        # Create filtered deck with 2 cards
        create_result = call_tool("filtered_deck", {
            "params": {
                "action": "create_or_update",
                "name": fd_name,
                "search_terms": [
                    {"search": f"deck:\"{source_deck}\"", "limit": 100},
                ],
            },
        })
        assert create_result.get("isError") is not True
        deck_id = create_result["deck_id"]
        assert create_result["card_count"] == 2

        # Add 3 more notes to source deck
        _create_notes_in_deck(source_deck, 3, uid + "extra")

        # Rebuild -- should now have 5 cards
        rebuild_result = call_tool("filtered_deck", {
            "params": {
                "action": "rebuild",
                "deck_id": deck_id,
            },
        })

        assert rebuild_result.get("isError") is not True
        assert rebuild_result["card_count"] == 5


# -- TestFilteredDeckEmpty ----------------------------------------------------

class TestFilteredDeckEmpty:
    """Tests for the empty action."""

    def test_empty_returns_cards_to_home_decks(self):
        """Empty should return cards to their original decks."""
        uid = unique_id()
        source_deck = f"E2E::FDEmptySrc_{uid}"
        _create_notes_in_deck(source_deck, 3, uid)

        fd_name = f"E2E::FDEmptyFD_{uid}"

        # Create filtered deck
        create_result = call_tool("filtered_deck", {
            "params": {
                "action": "create_or_update",
                "name": fd_name,
                "search_terms": [
                    {"search": f"deck:\"{source_deck}\"", "limit": 100},
                ],
            },
        })
        assert create_result.get("isError") is not True
        deck_id = create_result["deck_id"]
        assert create_result["card_count"] == 3

        # Verify cards are now in the filtered deck (source deck should show 0
        # in its own card count via list_decks with stats)
        decks_before = call_tool("list_decks", {"include_stats": True})
        source_before = next(
            d for d in decks_before["decks"] if d["name"] == source_deck
        )
        assert source_before["stats"]["total_in_deck"] == 0

        # Empty the filtered deck
        empty_result = call_tool("filtered_deck", {
            "params": {
                "action": "empty",
                "deck_id": deck_id,
            },
        })

        assert empty_result.get("isError") is not True
        assert empty_result["deck_id"] == deck_id
        assert "message" in empty_result

        # Verify cards are back in the source deck
        decks_after = call_tool("list_decks", {"include_stats": True})
        source_after = next(
            d for d in decks_after["decks"] if d["name"] == source_deck
        )
        assert source_after["stats"]["total_in_deck"] == 3


# -- TestFilteredDeckDelete ---------------------------------------------------

class TestFilteredDeckDelete:
    """Tests for the delete action."""

    def test_delete_removes_deck_preserves_cards(self):
        """Delete should remove the filtered deck but preserve cards
        in their original decks."""
        uid = unique_id()
        source_deck = f"E2E::FDDelSrc_{uid}"
        _create_notes_in_deck(source_deck, 3, uid)

        fd_name = f"E2E::FDDelFD_{uid}"

        # Create filtered deck
        create_result = call_tool("filtered_deck", {
            "params": {
                "action": "create_or_update",
                "name": fd_name,
                "search_terms": [
                    {"search": f"deck:\"{source_deck}\"", "limit": 100},
                ],
            },
        })
        assert create_result.get("isError") is not True
        deck_id = create_result["deck_id"]

        # Delete the filtered deck
        delete_result = call_tool("filtered_deck", {
            "params": {
                "action": "delete",
                "deck_id": deck_id,
            },
        })

        assert delete_result.get("isError") is not True
        assert delete_result["deck_id"] == deck_id
        assert "message" in delete_result

        # Verify filtered deck is gone
        deck_names = _get_deck_names()
        assert fd_name not in deck_names

        # Verify cards are back in the source deck
        decks_after = call_tool("list_decks", {"include_stats": True})
        source_after = next(
            d for d in decks_after["decks"] if d["name"] == source_deck
        )
        assert source_after["stats"]["total_in_deck"] == 3


# -- TestFilteredDeckErrors ---------------------------------------------------

class TestFilteredDeckErrors:
    """Tests for error cases."""

    def test_rebuild_invalid_deck_id(self):
        """Rebuild with a non-existent deck_id should error."""
        result = call_tool("filtered_deck", {
            "params": {
                "action": "rebuild",
                "deck_id": 9999999999,
            },
        })

        assert result.get("isError") is True
        assert "not found" in str(result).lower() or "deck" in str(result).lower()

    def test_empty_invalid_deck_id(self):
        """Empty with a non-existent deck_id should error."""
        result = call_tool("filtered_deck", {
            "params": {
                "action": "empty",
                "deck_id": 9999999999,
            },
        })

        assert result.get("isError") is True

    def test_delete_invalid_deck_id(self):
        """Delete with a non-existent deck_id should error."""
        result = call_tool("filtered_deck", {
            "params": {
                "action": "delete",
                "deck_id": 9999999999,
            },
        })

        assert result.get("isError") is True

    def test_rebuild_non_filtered_deck(self):
        """Rebuild on a regular (non-filtered) deck should error."""
        uid = unique_id()
        deck_name = f"E2E::FDNotFiltered_{uid}"
        create_result = call_tool("create_deck", {"deck_name": deck_name})
        deck_id = create_result["deckId"]

        result = call_tool("filtered_deck", {
            "params": {
                "action": "rebuild",
                "deck_id": deck_id,
            },
        })

        assert result.get("isError") is True
        assert "not a filtered deck" in str(result).lower() or "filtered" in str(result).lower()

    def test_empty_non_filtered_deck(self):
        """Empty on a regular (non-filtered) deck should error."""
        uid = unique_id()
        deck_name = f"E2E::FDNotFilteredE_{uid}"
        create_result = call_tool("create_deck", {"deck_name": deck_name})
        deck_id = create_result["deckId"]

        result = call_tool("filtered_deck", {
            "params": {
                "action": "empty",
                "deck_id": deck_id,
            },
        })

        assert result.get("isError") is True
        assert "not a filtered deck" in str(result).lower() or "filtered" in str(result).lower()

    def test_create_invalid_search_syntax(self):
        """Create with invalid search syntax should error."""
        uid = unique_id()
        fd_name = f"E2E::FDBadSearch_{uid}"

        result = call_tool("filtered_deck", {
            "params": {
                "action": "create_or_update",
                "name": fd_name,
                "search_terms": [
                    {"search": "invalid::(((syntax", "limit": 100},
                ],
                "allow_empty": True,
            },
        })

        assert result.get("isError") is True


# -- TestListDecksFiltered ----------------------------------------------------

class TestListDecksFiltered:
    """Tests for list_decks enhancements (deck_id and is_filtered)."""

    def test_list_decks_includes_deck_id(self):
        """list_decks response should include deck_id for each deck."""
        uid = unique_id()
        deck_name = f"E2E::FDListId_{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        result = call_tool("list_decks")
        assert result.get("isError") is not True

        deck = next(
            (d for d in result["decks"] if d["name"] == deck_name), None
        )
        assert deck is not None
        assert "deck_id" in deck
        assert deck["deck_id"] > 0

    def test_list_decks_includes_is_filtered(self):
        """list_decks should distinguish filtered vs regular decks."""
        uid = unique_id()
        source_deck = f"E2E::FDListSrc_{uid}"
        _create_notes_in_deck(source_deck, 1, uid)

        fd_name = f"E2E::FDListFilt_{uid}"
        create_result = call_tool("filtered_deck", {
            "params": {
                "action": "create_or_update",
                "name": fd_name,
                "search_terms": [
                    {"search": f"deck:\"{source_deck}\"", "limit": 100},
                ],
            },
        })
        assert create_result.get("isError") is not True

        result = call_tool("list_decks")

        # Regular deck should have is_filtered=False
        source = next(
            (d for d in result["decks"] if d["name"] == source_deck), None
        )
        assert source is not None
        assert source["is_filtered"] is False

        # Filtered deck should have is_filtered=True
        filtered = next(
            (d for d in result["decks"] if d["name"] == fd_name), None
        )
        assert filtered is not None
        assert filtered["is_filtered"] is True

    def test_list_decks_with_stats_includes_is_filtered(self):
        """list_decks with include_stats=True should also have is_filtered."""
        uid = unique_id()
        source_deck = f"E2E::FDListStatSrc_{uid}"
        _create_notes_in_deck(source_deck, 1, uid)

        fd_name = f"E2E::FDListStatFilt_{uid}"
        create_result = call_tool("filtered_deck", {
            "params": {
                "action": "create_or_update",
                "name": fd_name,
                "search_terms": [
                    {"search": f"deck:\"{source_deck}\"", "limit": 100},
                ],
            },
        })
        assert create_result.get("isError") is not True

        result = call_tool("list_decks", {"include_stats": True})

        filtered = next(
            (d for d in result["decks"] if d["name"] == fd_name), None
        )
        assert filtered is not None
        assert filtered["is_filtered"] is True
        assert "stats" in filtered
