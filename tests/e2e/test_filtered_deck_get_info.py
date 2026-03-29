"""Tests for filtered_deck get_info action."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool


# -- Helpers ------------------------------------------------------------------

def _create_notes_in_deck(deck_name: str, count: int, uid: str) -> list[int]:
    """Create *count* Basic notes in *deck_name* and return their note IDs."""
    call_tool("create_deck", {"deck_name": deck_name})
    note_ids: list[int] = []
    for i in range(count):
        result = call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Q{i} {uid}",
                "Back": f"A{i} {uid}",
            },
        })
        note_ids.append(result["note_id"])
    return note_ids


def _create_filtered_deck(
    name: str, source_deck: str, limit: int = 100, order: str = "random",
    reschedule: bool = True,
) -> dict:
    """Create a filtered deck and return the result."""
    return call_tool("filtered_deck", {
        "params": {
            "action": "create_or_update",
            "name": name,
            "search_terms": [
                {"search": f'deck:"{source_deck}"', "limit": limit, "order": order},
            ],
            "reschedule": reschedule,
        },
    })


# -- TestFilteredDeckGetInfo --------------------------------------------------

class TestFilteredDeckGetInfo:
    """Tests for the get_info action."""

    def test_get_info_single_filtered_deck(self):
        """get_info should return search terms, reschedule, and card count."""
        uid = unique_id()
        source_deck = f"E2E::GISrc1_{uid}"
        _create_notes_in_deck(source_deck, 3, uid)

        fd_name = f"E2E::GIFD1_{uid}"
        create_result = _create_filtered_deck(
            fd_name, source_deck, limit=100, order="due", reschedule=False,
        )
        assert create_result.get("isError") is not True
        deck_id = create_result["deck_id"]

        result = call_tool("filtered_deck", {
            "params": {
                "action": "get_info",
                "deck_ids": [deck_id],
            },
        })

        assert result.get("isError") is not True
        assert result["count"] == 1
        assert result["not_found"] == 0

        deck_info = result["decks"][0]
        assert deck_info["deck_id"] == deck_id
        assert deck_info["name"] == fd_name
        assert deck_info["is_filtered"] is True
        assert deck_info["card_count"] == 3
        assert deck_info["reschedule"] is False
        assert len(deck_info["search_terms"]) == 1

        term = deck_info["search_terms"][0]
        assert source_deck in term["search"]
        assert term["limit"] == 100
        assert term["order"] == "due"

    def test_get_info_batch_multiple_decks(self):
        """get_info should handle multiple deck IDs in a single request."""
        uid = unique_id()
        source_deck = f"E2E::GIBatchSrc_{uid}"
        _create_notes_in_deck(source_deck, 5, uid)

        fd1_name = f"E2E::GIBatch1_{uid}"
        fd2_name = f"E2E::GIBatch2_{uid}"

        r1 = _create_filtered_deck(fd1_name, source_deck, limit=2)
        assert r1.get("isError") is not True
        r2 = _create_filtered_deck(fd2_name, source_deck, limit=3)
        assert r2.get("isError") is not True

        result = call_tool("filtered_deck", {
            "params": {
                "action": "get_info",
                "deck_ids": [r1["deck_id"], r2["deck_id"]],
            },
        })

        assert result.get("isError") is not True
        assert result["count"] == 2
        assert result["not_found"] == 0
        assert len(result["decks"]) == 2

        ids_returned = {d["deck_id"] for d in result["decks"]}
        assert r1["deck_id"] in ids_returned
        assert r2["deck_id"] in ids_returned

    def test_get_info_non_filtered_deck(self):
        """get_info on a regular deck should return is_filtered=false."""
        uid = unique_id()
        deck_name = f"E2E::GIRegular_{uid}"
        create_result = call_tool("create_deck", {"deck_name": deck_name})
        deck_id = create_result["deckId"]

        result = call_tool("filtered_deck", {
            "params": {
                "action": "get_info",
                "deck_ids": [deck_id],
            },
        })

        assert result.get("isError") is not True
        assert result["count"] == 1
        assert result["not_found"] == 0

        deck_info = result["decks"][0]
        assert deck_info["deck_id"] == deck_id
        assert deck_info["is_filtered"] is False
        assert deck_info["search_terms"] == []
        assert deck_info["reschedule"] is False

    def test_get_info_two_search_terms(self):
        """get_info should return both search terms for a deck with two queries."""
        uid = unique_id()
        source1 = f"E2E::GI2TSrc1_{uid}"
        source2 = f"E2E::GI2TSrc2_{uid}"
        _create_notes_in_deck(source1, 2, uid + "a")
        _create_notes_in_deck(source2, 2, uid + "b")

        fd_name = f"E2E::GI2T_{uid}"
        create_result = call_tool("filtered_deck", {
            "params": {
                "action": "create_or_update",
                "name": fd_name,
                "search_terms": [
                    {"search": f'deck:"{source1}"', "limit": 10, "order": "due"},
                    {"search": f'deck:"{source2}"', "limit": 20, "order": "random"},
                ],
                "reschedule": True,
            },
        })
        assert create_result.get("isError") is not True
        deck_id = create_result["deck_id"]

        result = call_tool("filtered_deck", {
            "params": {
                "action": "get_info",
                "deck_ids": [deck_id],
            },
        })

        assert result.get("isError") is not True
        deck_info = result["decks"][0]
        assert deck_info["reschedule"] is True
        assert len(deck_info["search_terms"]) == 2
        assert deck_info["search_terms"][0]["limit"] == 10
        assert deck_info["search_terms"][1]["limit"] == 20

    def test_get_info_nonexistent_deck_skipped(self):
        """Non-existent deck IDs should be skipped with not_found count."""
        uid = unique_id()
        source_deck = f"E2E::GINFSrc_{uid}"
        _create_notes_in_deck(source_deck, 1, uid)

        fd_name = f"E2E::GINF_{uid}"
        create_result = _create_filtered_deck(fd_name, source_deck)
        assert create_result.get("isError") is not True
        real_id = create_result["deck_id"]

        fake_id = 9999999999

        result = call_tool("filtered_deck", {
            "params": {
                "action": "get_info",
                "deck_ids": [real_id, fake_id],
            },
        })

        assert result.get("isError") is not True
        assert result["count"] == 1
        assert result["not_found"] == 1
        assert len(result["decks"]) == 1
        assert result["decks"][0]["deck_id"] == real_id

    def test_get_info_empty_deck_ids_error(self):
        """Empty deck_ids list should return a validation error."""
        result = call_tool("filtered_deck", {
            "params": {
                "action": "get_info",
                "deck_ids": [],
            },
        })

        assert result.get("isError") is True

    def test_get_info_over_limit_error(self):
        """More than 50 deck IDs should return a limit error."""
        result = call_tool("filtered_deck", {
            "params": {
                "action": "get_info",
                "deck_ids": list(range(1, 52)),  # 51 IDs
            },
        })

        assert result.get("isError") is True
