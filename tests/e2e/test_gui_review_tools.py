"""Tests for the GUI reviewer tools outside review mode.

Scope note (important): the E2E suite runs against a headless Anki that never
enters the GUI reviewer -- no MCP tool starts a review session, so ``mw.state``
stays ``"deckBrowser"`` and ``mw.reviewer.card`` stays ``None`` for the whole
run. Only the "not in review" guard of gui_current_card / gui_show_answer /
gui_show_question is reachable here, and that guard is exactly what these tests
pin down: these tools must SOFT-fail (success=True, inReview=False) instead of
raising, so an AI client can tell "the user isn't reviewing" apart from "the
call blew up".

gui_select_card has the analogous guard for the Card Browser window: it is
tested here for the same reason (the Browser is never opened in this suite --
no test calls gui_browse, and there is no other code path that opens it).
"""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool, list_tools

# Card IDs are epoch-millisecond timestamps, so this one cannot exist.
NONEXISTENT_CARD_ID = 99999999999999


def _make_card(uid: str) -> tuple[int, int]:
    """Create a deck + Basic note and return (note_id, card_id) of its only card."""
    deck_name = f"E2E::GuiSel{uid}"
    call_tool("create_deck", {"deck_name": deck_name})

    note_result = call_tool("add_note", {
        "deck_name": deck_name,
        "model_name": "Basic",
        "fields": {"Front": f"Q {uid}", "Back": f"A {uid}"},
    })
    assert note_result.get("isError") is not True, f"Setup failed: {note_result}"
    note_id = note_result["note_id"]

    info = call_tool("notes_info", {"notes": [note_id]})
    assert info.get("isError") is not True, f"Setup failed: {info}"
    card_ids = info["notes"][0]["cards"]
    assert len(card_ids) == 1, f"Expected exactly one card, got {card_ids}"
    return note_id, card_ids[0]


def _delete_notes(note_ids: list[int]) -> None:
    """Delete notes to keep the shared collection clean."""
    if note_ids:
        call_tool("delete_notes", {"notes": note_ids, "confirmDeletion": True})


# -- TestGuiReviewToolsRegistered ---------------------------------------------

class TestGuiReviewToolsRegistered:
    """The GUI reviewer tools should be exposed to MCP clients."""

    def test_gui_review_tools_exist(self):
        """gui_current_card / gui_show_answer / gui_show_question / gui_select_card
        should all be registered."""
        tool_names = {t["name"] for t in list_tools()}
        for name in (
            "gui_current_card",
            "gui_show_answer",
            "gui_show_question",
            "gui_select_card",
        ):
            assert name in tool_names, f"{name} not in tools/list"


# -- TestGuiCurrentCardOutsideReview -------------------------------------------

class TestGuiCurrentCardOutsideReview:
    """gui_current_card when the reviewer is not active."""

    def test_soft_fails_with_null_card_info(self):
        """Outside review, gui_current_card returns success=True, inReview=False
        and an explicitly null cardInfo -- it must NOT raise."""
        result = call_tool("gui_current_card")

        assert result.get("isError") is not True, result
        assert result["success"] is True
        assert result["inReview"] is False
        # cardInfo must be present AND null: the key is part of the contract,
        # so an AI client can branch on it without a KeyError.
        assert "cardInfo" in result, result
        assert result["cardInfo"] is None

    def test_soft_fail_carries_message_and_hint(self):
        """The not-in-review response should explain itself to the AI client."""
        result = call_tool("gui_current_card")

        assert result.get("isError") is not True, result
        assert isinstance(result["message"], str)
        assert result["message"].strip() != ""
        assert "review" in result["message"].lower()
        assert isinstance(result["hint"], str)
        assert result["hint"].strip() != ""

    def test_no_card_specific_keys_outside_review(self):
        """The in-review payload lives under cardInfo, so no card fields should
        leak to the top level when there is no card."""
        result = call_tool("gui_current_card")

        assert result.get("isError") is not True, result
        for leaked in ("cardId", "noteId", "question", "answer", "deckName"):
            assert leaked not in result, f"{leaked} leaked into the soft-fail payload"


# -- TestGuiShowAnswerOutsideReview --------------------------------------------

class TestGuiShowAnswerOutsideReview:
    """gui_show_answer when the reviewer is not active."""

    def test_soft_fails_outside_review(self):
        """gui_show_answer returns success=True / inReview=False, not an error."""
        result = call_tool("gui_show_answer")

        assert result.get("isError") is not True, result
        assert result["success"] is True
        assert result["inReview"] is False

    def test_soft_fail_carries_message_and_hint(self):
        """The soft-fail should tell the client why nothing happened."""
        result = call_tool("gui_show_answer")

        assert result.get("isError") is not True, result
        assert isinstance(result["message"], str)
        assert result["message"].strip() != ""
        assert isinstance(result["hint"], str)
        assert result["hint"].strip() != ""

    def test_repeated_calls_stay_soft(self):
        """Calling it twice outside review must not corrupt reviewer state."""
        first = call_tool("gui_show_answer")
        second = call_tool("gui_show_answer")

        assert first.get("isError") is not True, first
        assert second.get("isError") is not True, second
        assert first["inReview"] is False
        assert second["inReview"] is False


# -- TestGuiShowQuestionOutsideReview ------------------------------------------

class TestGuiShowQuestionOutsideReview:
    """gui_show_question when the reviewer is not active."""

    def test_soft_fails_outside_review(self):
        """gui_show_question returns success=True / inReview=False, not an error."""
        result = call_tool("gui_show_question")

        assert result.get("isError") is not True, result
        assert result["success"] is True
        assert result["inReview"] is False

    def test_soft_fail_carries_message_and_hint(self):
        """The soft-fail should tell the client why nothing happened."""
        result = call_tool("gui_show_question")

        assert result.get("isError") is not True, result
        assert isinstance(result["message"], str)
        assert result["message"].strip() != ""
        assert isinstance(result["hint"], str)
        assert result["hint"].strip() != ""

    def test_show_question_after_show_answer_stays_soft(self):
        """The flip-back pairing must also stay soft outside review."""
        answer = call_tool("gui_show_answer")
        question = call_tool("gui_show_question")

        assert answer["inReview"] is False
        assert question.get("isError") is not True, question
        assert question["success"] is True
        assert question["inReview"] is False


# -- TestGuiSelectCardBrowserClosed --------------------------------------------

class TestGuiSelectCardBrowserClosed:
    """gui_select_card when the Card Browser window is not open.

    ASSUMPTION: nothing in this suite opens the Card Browser -- no test calls
    gui_browse, and headless Anki opens no dialogs on its own. If a Browser test
    is ever added, it must close the Browser again (or these tests must be moved
    behind a fixture that asserts the Browser is closed), because an open Browser
    flips these responses to browserOpen=True.
    """

    def test_soft_fails_when_browser_closed(self):
        """With the Browser closed, gui_select_card reports selected=False /
        browserOpen=False instead of raising."""
        result = call_tool("gui_select_card", {"card_id": NONEXISTENT_CARD_ID})

        assert result.get("isError") is not True, result
        assert result["success"] is True
        assert result["selected"] is False
        assert result["browserOpen"] is False
        assert result["cardId"] == NONEXISTENT_CARD_ID

    def test_soft_fail_carries_message_and_hint(self):
        """The soft-fail should point the client at gui_browse."""
        result = call_tool("gui_select_card", {"card_id": NONEXISTENT_CARD_ID})

        assert result.get("isError") is not True, result
        assert isinstance(result["message"], str)
        assert "browser" in result["message"].lower()
        assert isinstance(result["hint"], str)
        assert "gui_browse" in result["hint"]

    def test_browser_guard_precedes_card_lookup(self):
        """A REAL card ID must produce the same browser-closed response as a
        bogus one: the browser check short-circuits before the card is resolved,
        so card validity never changes the outcome while the Browser is closed."""
        uid = unique_id()
        note_id, card_id = _make_card(uid)

        try:
            result = call_tool("gui_select_card", {"card_id": card_id})

            assert result.get("isError") is not True, result
            assert result["success"] is True
            assert result["selected"] is False
            assert result["browserOpen"] is False
            assert result["cardId"] == card_id
        finally:
            # Notes are cleaned up; the deck itself is left behind because the
            # addon exposes no delete-deck tool. Deck names are uniquely
            # suffixed, so leftovers cannot collide across runs.
            _delete_notes([note_id])
