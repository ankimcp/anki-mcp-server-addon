"""E2E tests for the GUI review-session tools: gui_deck_review and gui_answer_card.

Scope note: test_gui_review_tools.py only exercises the "not in review" guards of
gui_current_card / gui_show_answer / gui_show_question, because nothing else in
the suite ever starts a review session. gui_deck_review is the first MCP tool
that actually drives Anki's own reviewer into "review" state, so this file also
exercises the in-review branches of those tools for the first time.
"""
from __future__ import annotations

import time

from .conftest import unique_id
from .helpers import call_tool, list_tools

# Bound on polling gui_current_card for the reviewer to finish advancing after
# gui_answer_card. gui_answer_card returns before Anki's CollectionOp completes
# (see gui_answer_card_tool.py), so the outcome is only observable this way.
_ADVANCE_POLL_TIMEOUT_SECONDS = 5.0
_ADVANCE_POLL_INTERVAL_SECONDS = 0.2


def _make_deck_with_note(uid: str) -> tuple[str, int]:
    """Create a deck with one fresh Basic note and return (deck_name, note_id)."""
    deck_name = f"E2E::GuiReview{uid}"
    call_tool("create_deck", {"deck_name": deck_name})

    note_result = call_tool("add_note", {
        "deck_name": deck_name,
        "model_name": "Basic",
        "fields": {"Front": f"Q {uid}", "Back": f"A {uid}"},
    })
    assert note_result.get("isError") is not True, f"Setup failed: {note_result}"
    return deck_name, note_result["note_id"]


def _card_type(deck_name: str) -> int:
    """Return the (only) card's Anki type int (0 new, 1 learning, 2 review, 3 relearning)."""
    result = call_tool("cards_stats", {"deck": deck_name})
    assert result.get("isError") is not True, result
    assert len(result["cards"]) == 1, result
    return result["cards"][0]["type"]


def _wait_for_advance_to_finish() -> dict:
    """Poll gui_current_card until the reviewer stops reporting advancing=True.

    Returns the last gui_current_card result (either the settled next-card
    response, or the inReview=False response once the session ends).
    """
    deadline = time.monotonic() + _ADVANCE_POLL_TIMEOUT_SECONDS
    result = call_tool("gui_current_card")
    while result.get("advancing") and time.monotonic() < deadline:
        time.sleep(_ADVANCE_POLL_INTERVAL_SECONDS)
        result = call_tool("gui_current_card")
    return result


def _delete_notes(note_ids: list[int]) -> None:
    if note_ids:
        call_tool("delete_notes", {"notes": note_ids, "confirmDeletion": True})


# -- TestGuiReviewToolsRegistered ---------------------------------------------

class TestGuiReviewToolsRegistered:
    def test_gui_deck_review_and_answer_card_exist(self):
        tool_names = {t["name"] for t in list_tools()}
        for name in ("gui_deck_review", "gui_answer_card"):
            assert name in tool_names, f"{name} not in tools/list"


# -- TestGuiDeckReviewUnknownDeck ----------------------------------------------

class TestGuiDeckReviewUnknownDeck:
    def test_unknown_deck_is_error(self):
        """gui_deck_review must never auto-create the deck it can't find."""
        deck_name = f"NonExist{unique_id()}"

        result = call_tool("gui_deck_review", {"deck_name": deck_name})
        assert result.get("isError") is True

        decks = call_tool("list_decks")
        deck_names = [d["name"] for d in decks["decks"]]
        assert deck_name not in deck_names


# -- TestGuiReviewSessionFlow --------------------------------------------------

class TestGuiReviewSessionFlow:
    """Drives one card through the full gui_deck_review -> gui_current_card ->
    gui_show_answer -> gui_answer_card loop."""

    def test_full_review_flow(self):
        uid = unique_id()
        deck_name, note_id = _make_deck_with_note(uid)

        try:
            assert _card_type(deck_name) == 0, "fixture note should start as a new card"

            opened = call_tool("gui_deck_review", {"deck_name": deck_name})
            assert opened.get("isError") is not True, opened
            assert opened["inReview"] is True
            card_id = opened["cardId"]

            current = call_tool("gui_current_card")
            assert current.get("isError") is not True, current
            assert current["inReview"] is True
            assert current["cardInfo"]["cardId"] == card_id
            # Answer (and fields, which can themselves contain the answer) must
            # not be exposed before the user has answered.
            assert "answer" not in current["cardInfo"]
            assert "fields" not in current["cardInfo"]

            # The answer isn't on screen yet -- must fail with a hint to show it.
            too_early = call_tool("gui_answer_card", {"ease": 3})
            assert too_early.get("isError") is True
            assert "gui_show_answer" in str(too_early)

            shown = call_tool("gui_show_answer")
            assert shown.get("isError") is not True, shown
            assert shown["inReview"] is True

            with_answer = call_tool("gui_current_card", {"include_answer": "true"})
            assert with_answer.get("isError") is not True, with_answer
            assert with_answer["cardInfo"]["cardId"] == card_id
            assert with_answer["cardInfo"].get("answer")
            assert with_answer["cardInfo"].get("fields")

            # Out-of-range ease must fail without corrupting reviewer state.
            bad_ease = call_tool("gui_answer_card", {"ease": 9})
            assert bad_ease.get("isError") is True

            still_current = call_tool("gui_current_card")
            assert still_current.get("isError") is not True, still_current
            assert still_current["inReview"] is True
            assert still_current["cardInfo"]["cardId"] == card_id

            # Easy (4): a fresh card graduates deterministically. Good (3) would
            # re-queue it into the learning step's 20-minute learn-ahead window,
            # making "session ended" flaky for this single-card deck.
            answered = call_tool("gui_answer_card", {"ease": 4})
            assert answered.get("isError") is not True, answered
            assert answered["answeredCardId"] == card_id
            assert answered["ease"] == 4
            assert answered["easeName"] == "Easy"
            assert answered["pending"] is True

            settled = _wait_for_advance_to_finish()
            assert settled.get("isError") is not True, settled
            assert settled.get("advancing") is not True, (
                f"reviewer never finished advancing: {settled}"
            )
            # Only one card was due, so the session should have ended.
            assert settled["inReview"] is False

            # The card graduated out of the "new" queue -- proof it was
            # actually answered through the reviewer, not left desynced.
            assert _card_type(deck_name) == 2
        finally:
            # Close the reviewer so it doesn't hold this deck selected for
            # later test files, then clean up the note. The deck itself is
            # left behind because the addon exposes no delete-deck tool --
            # deck names are uniquely suffixed, so leftovers cannot collide.
            call_tool("gui_deck_browser")
            _delete_notes([note_id])
