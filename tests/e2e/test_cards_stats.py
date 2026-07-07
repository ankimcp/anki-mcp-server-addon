"""Tests for cards_stats tool."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool, list_tools


def _add_note(deck_name: str, front: str, back: str, tags=None) -> int:
    """Add a Basic note and return its note_id. Tags optional."""
    args = {
        "deck_name": deck_name,
        "model_name": "Basic",
        "fields": {"Front": front, "Back": back},
    }
    if tags is not None:
        args["tags"] = tags
    result = call_tool("add_note", args)
    assert "note_id" in result, result
    return result["note_id"]


def _card_ids_for(deck_name: str) -> list[int]:
    """Return the card ids reported by cards_stats for a deck."""
    result = call_tool("cards_stats", {"deck": deck_name})
    assert result.get("isError") is not True, result
    return [c["cid"] for c in result["cards"]]


class TestCardsStats:
    """Tests for cards_stats tool."""

    def test_cards_stats_tool_exists(self):
        """cards_stats tool should be registered."""
        tools = list_tools()
        tool_names = [t["name"] for t in tools]
        assert "cards_stats" in tool_names

    def test_cards_stats_unknown_deck(self):
        """cards_stats should error for a non-existent deck."""
        result = call_tool("cards_stats", {"deck": f"NonExist{unique_id()}"})
        assert result.get("isError") is True

    def test_cards_stats_empty_deck(self):
        """cards_stats on an existing but empty deck returns an empty page."""
        uid = unique_id()
        deck_name = f"E2E::EmptyStats{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        result = call_tool("cards_stats", {"deck": deck_name})

        assert result.get("isError") is not True
        assert result["deck"] == deck_name
        assert result["total"] == 0
        assert result["count"] == 0
        assert result["cards"] == []
        assert result["hasMore"] is False

    def test_cards_stats_new_card_fields(self):
        """A newly added card exposes int type/queue/ivl and dueToday False."""
        uid = unique_id()
        deck_name = f"E2E::NewStats{uid}"
        call_tool("create_deck", {"deck_name": deck_name})
        _add_note(deck_name, f"Q {uid}", f"A {uid}")

        result = call_tool("cards_stats", {"deck": deck_name})
        assert result.get("isError") is not True
        assert result["total"] == 1
        assert len(result["cards"]) == 1

        card = result["cards"][0]
        assert isinstance(card["cid"], int)
        assert isinstance(card["nid"], int)
        assert isinstance(card["type"], int)
        assert isinstance(card["queue"], int)
        assert isinstance(card["ivl"], int)
        assert isinstance(card["tags"], list)
        # A brand-new card is type 0 / queue 0 and not due today.
        assert card["type"] == 0
        assert card["queue"] == 0
        assert card["dueToday"] is False

    def test_cards_stats_note_tag_join(self):
        """The note's tags appear in the card's tags list (the join is the point)."""
        uid = unique_id()
        deck_name = f"E2E::TagStats{uid}"
        tag = f"knowledgemap{uid}"
        call_tool("create_deck", {"deck_name": deck_name})
        _add_note(deck_name, f"Q {uid}", f"A {uid}", tags=[tag])

        result = call_tool("cards_stats", {"deck": deck_name})
        assert result.get("isError") is not True
        assert len(result["cards"]) == 1
        assert tag in result["cards"][0]["tags"]

    def test_cards_stats_no_tags_is_empty_list(self):
        """A note with no tags yields an empty tags list, not None or junk."""
        uid = unique_id()
        deck_name = f"E2E::NoTagStats{uid}"
        call_tool("create_deck", {"deck_name": deck_name})
        _add_note(deck_name, f"Q {uid}", f"A {uid}")

        result = call_tool("cards_stats", {"deck": deck_name})
        assert result.get("isError") is not True
        assert result["cards"][0]["tags"] == []

    def test_cards_stats_review_card_due_today(self):
        """set_due_date '0' makes a review card that reads as queue 2 / dueToday True."""
        uid = unique_id()
        deck_name = f"E2E::ReviewStats{uid}"
        call_tool("create_deck", {"deck_name": deck_name})
        _add_note(deck_name, f"Q {uid}", f"A {uid}")

        card_id = _card_ids_for(deck_name)[0]

        sched = call_tool("card_management", {
            "params": {
                "action": "set_due_date",
                "card_ids": [card_id],
                "days": "0",
            }
        })
        assert sched.get("isError") is not True, sched

        result = call_tool("cards_stats", {"deck": deck_name})
        assert result.get("isError") is not True
        card = next(c for c in result["cards"] if c["cid"] == card_id)
        # set_due_date graduates the card into the review queue.
        assert card["queue"] == 2
        assert card["dueToday"] is True

    def test_cards_stats_suspended_card(self):
        """A suspended card reads as queue -1 and is not due today."""
        uid = unique_id()
        deck_name = f"E2E::SuspendStats{uid}"
        call_tool("create_deck", {"deck_name": deck_name})
        _add_note(deck_name, f"Q {uid}", f"A {uid}")

        card_id = _card_ids_for(deck_name)[0]

        susp = call_tool("card_management", {
            "params": {
                "action": "suspend",
                "card_ids": [card_id],
            }
        })
        assert susp.get("isError") is not True, susp

        result = call_tool("cards_stats", {"deck": deck_name})
        assert result.get("isError") is not True
        card = next(c for c in result["cards"] if c["cid"] == card_id)
        assert card["queue"] == -1
        assert card["dueToday"] is False

    def test_cards_stats_pagination_determinism(self):
        """Pagination covers all cards exactly once and ordering is stable."""
        uid = unique_id()
        deck_name = f"E2E::PageStats{uid}"
        call_tool("create_deck", {"deck_name": deck_name})
        for i in range(3):
            _add_note(deck_name, f"Q{i} {uid}", f"A{i} {uid}")

        page1 = call_tool("cards_stats", {"deck": deck_name, "limit": "2"})
        assert page1.get("isError") is not True
        assert page1["total"] == 3
        assert page1["count"] == 2
        assert page1["hasMore"] is True
        assert page1["offset"] == 0

        page2 = call_tool("cards_stats", {"deck": deck_name, "limit": "2", "offset": "2"})
        assert page2.get("isError") is not True
        assert page2["total"] == 3
        assert page2["count"] == 1
        assert page2["hasMore"] is False
        assert page2["offset"] == 2

        page1_cids = [c["cid"] for c in page1["cards"]]
        page2_cids = [c["cid"] for c in page2["cards"]]
        # Together the two pages cover all 3 distinct cids.
        combined = page1_cids + page2_cids
        assert len(combined) == 3
        assert len(set(combined)) == 3

        # Determinism: repeating the identical first-page call returns the same order.
        page1_again = call_tool("cards_stats", {"deck": deck_name, "limit": "2"})
        assert [c["cid"] for c in page1_again["cards"]] == page1_cids

    def test_cards_stats_includes_subdecks(self):
        """cards_stats on a parent deck includes cards from its subdecks."""
        uid = unique_id()
        # create_deck caps nesting at 2 levels (parent::child), so the parent
        # is a top-level deck here rather than under the usual E2E:: namespace.
        parent = f"CardsStatsParent{uid}"
        child = f"{parent}::Child"
        call_tool("create_deck", {"deck_name": parent})
        call_tool("create_deck", {"deck_name": child})

        _add_note(parent, f"Parent Q {uid}", f"Parent A {uid}")
        _add_note(child, f"Child Q {uid}", f"Child A {uid}")

        result = call_tool("cards_stats", {"deck": parent})
        assert result.get("isError") is not True
        # Both the parent-deck card and the subdeck card are returned.
        assert result["total"] == 2
        assert len(result["cards"]) == 2
