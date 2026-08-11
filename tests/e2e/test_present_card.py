"""Tests for the present_card tool.

Contract under test:
  * ``deck_name`` is the card's HOME deck (``card.current_deck_id()``), which
    stays the home deck even while the card sits in a filtered deck.
  * ``filtered_deck_name`` is ALWAYS present and is ``None`` unless the card is
    currently being studied from a filtered deck.
  * ``answer`` is only included when ``show_answer=true``.

The filtered-deck half of this is fully exercisable headlessly: the
``filtered_deck`` tool moves cards at the collection level (setting
``card.did``/``card.odid``), which is independent of any GUI review state.

Cleanup: notes are deleted and filtered decks are removed via
``filtered_deck action=delete``. Regular decks are left behind -- the addon
exposes no delete-deck tool -- but every deck name carries a uuid4 suffix, so
leftovers never collide.
"""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool, delete_filtered_deck, list_tools

# Card IDs are epoch-millisecond timestamps, so this one cannot exist.
NONEXISTENT_CARD_ID = 99999999999999


# -- Helpers ------------------------------------------------------------------

def _create_card(uid: str, label: str) -> tuple[str, int, int]:
    """Create a uniquely-named deck holding one Basic note.

    Returns (deck_name, note_id, card_id).
    """
    deck_name = f"E2E::PC{label}_{uid}"
    create = call_tool("create_deck", {"deck_name": deck_name})
    assert create.get("isError") is not True, f"Setup failed: {create}"

    note_result = call_tool("add_note", {
        "deck_name": deck_name,
        "model_name": "Basic",
        "fields": {
            "Front": f"Present Q {uid}",
            "Back": f"Present A {uid}",
        },
    })
    assert note_result.get("isError") is not True, f"Setup failed: {note_result}"
    note_id = note_result["note_id"]

    info = call_tool("notes_info", {"notes": [note_id]})
    assert info.get("isError") is not True, f"Setup failed: {info}"
    card_ids = info["notes"][0]["cards"]
    assert len(card_ids) == 1, f"Expected one card for a Basic note, got {card_ids}"

    return deck_name, note_id, card_ids[0]


def _delete_notes(note_ids: list[int]) -> None:
    """Delete notes to keep the shared collection clean."""
    if note_ids:
        call_tool("delete_notes", {"notes": note_ids, "confirmDeletion": True})


def _present(card_id: int, show_answer: bool | None = None) -> dict:
    """Call present_card and return the ``card`` payload (asserting no error)."""
    args: dict = {"card_id": card_id}
    if show_answer is not None:
        args["show_answer"] = show_answer
    result = call_tool("present_card", args)
    assert result.get("isError") is not True, result
    assert "card" in result, result
    return result["card"]


# -- TestPresentCardBasics -----------------------------------------------------

class TestPresentCardBasics:
    """Happy-path shape of the present_card response."""

    def test_tool_exists(self):
        """present_card should be registered."""
        tool_names = {t["name"] for t in list_tools()}
        assert "present_card" in tool_names

    def test_returns_home_deck_and_null_filtered_deck(self):
        """A card that is not in any filtered deck reports its own deck as
        deck_name and a null (but present) filtered_deck_name."""
        uid = unique_id()
        deck_name, note_id, card_id = _create_card(uid, "Home")

        try:
            card = _present(card_id)

            assert card["card_id"] == card_id
            assert card["deck_name"] == deck_name
            # "always present" is part of the tool's documented contract, so the
            # key must exist even though its value is null.
            assert "filtered_deck_name" in card, card
            assert card["filtered_deck_name"] is None
        finally:
            _delete_notes([note_id])

    def test_question_present_and_answer_absent_by_default(self):
        """Without show_answer, the question renders and no answer key exists."""
        uid = unique_id()
        _deck_name, note_id, card_id = _create_card(uid, "Q")

        try:
            card = _present(card_id)

            assert isinstance(card["question"], str)
            assert card["question"].strip() != ""
            assert f"Present Q {uid}" in card["question"]
            assert "answer" not in card, "answer must be omitted unless show_answer=true"
        finally:
            _delete_notes([note_id])

    def test_show_answer_includes_answer(self):
        """With show_answer=true, the rendered answer is included."""
        uid = unique_id()
        _deck_name, note_id, card_id = _create_card(uid, "A")

        try:
            card = _present(card_id, show_answer=True)

            assert "answer" in card, card
            assert isinstance(card["answer"], str)
            assert card["answer"].strip() != ""
            assert f"Present A {uid}" in card["answer"]
        finally:
            _delete_notes([note_id])

    def test_scheduling_and_note_type_fields(self):
        """The scheduling block and note_type should be present with int types."""
        uid = unique_id()
        _deck_name, note_id, card_id = _create_card(uid, "Sched")

        try:
            card = _present(card_id)

            assert card["note_type"] == "Basic"
            for key in ("due", "interval", "ease_factor", "reviews", "lapses"):
                assert key in card, f"{key} missing from present_card payload"
                assert isinstance(card[key], int), f"{key} should be an int"
            # A brand-new card has never been reviewed.
            assert card["reviews"] == 0
            assert card["lapses"] == 0
        finally:
            _delete_notes([note_id])


# -- TestPresentCardFilteredDeck -----------------------------------------------

class TestPresentCardFilteredDeck:
    """deck_name stays the HOME deck while filtered_deck_name tracks the move."""

    def test_filtered_deck_name_tracks_card_location(self):
        """Pull the card into a filtered deck: deck_name must stay the home
        deck and filtered_deck_name must name the filtered deck. After deleting
        the filtered deck, filtered_deck_name goes back to None."""
        uid = unique_id()
        home_deck, note_id, card_id = _create_card(uid, "Filt")
        filtered_deck_id: int | None = None

        try:
            # Sanity: before any filtered deck exists.
            before = _present(card_id)
            assert before["deck_name"] == home_deck
            assert before["filtered_deck_name"] is None

            fd_requested_name = f"E2E::PCFiltDeck_{uid}"
            create = call_tool("filtered_deck", {
                "params": {
                    "action": "create_or_update",
                    "name": fd_requested_name,
                    "search_terms": [
                        {"search": f'deck:"{home_deck}"', "limit": 100, "order": "due"},
                    ],
                },
            })
            assert create.get("isError") is not True, create
            filtered_deck_id = create["deck_id"]

            # Anki may rename on collision -- always trust the returned name.
            fd_actual_name = create["name"]

            # Fail loudly if the card was NOT captured: everything below is
            # meaningless otherwise (new cards can be excluded from filtered
            # decks depending on scheduler/deck settings).
            assert create["card_count"] == 1, (
                "Filtered deck did not capture the test card "
                f"(card_count={create['card_count']}). The rest of this test "
                "cannot verify filtered_deck_name without a captured card."
            )

            during = _present(card_id)
            assert during["card_id"] == card_id
            # HOME deck, not the filtered deck.
            assert during["deck_name"] == home_deck
            assert during["filtered_deck_name"] == fd_actual_name

            # Delete the filtered deck -> card returns home.
            delete = delete_filtered_deck(filtered_deck_id)
            assert delete.get("isError") is not True, delete
            filtered_deck_id = None

            after = _present(card_id)
            assert after["deck_name"] == home_deck
            assert after["filtered_deck_name"] is None
        finally:
            if filtered_deck_id is not None:
                delete_filtered_deck(filtered_deck_id)
            _delete_notes([note_id])

    def test_empty_returns_card_home(self):
        """filtered_deck action=empty also clears filtered_deck_name, without
        removing the filtered deck itself."""
        uid = unique_id()
        home_deck, note_id, card_id = _create_card(uid, "Empty")
        filtered_deck_id: int | None = None

        try:
            create = call_tool("filtered_deck", {
                "params": {
                    "action": "create_or_update",
                    "name": f"E2E::PCEmptyDeck_{uid}",
                    "search_terms": [
                        {"search": f'deck:"{home_deck}"', "limit": 100, "order": "due"},
                    ],
                },
            })
            assert create.get("isError") is not True, create
            filtered_deck_id = create["deck_id"]
            assert create["card_count"] == 1, (
                "Filtered deck did not capture the test card "
                f"(card_count={create['card_count']})."
            )

            assert _present(card_id)["filtered_deck_name"] == create["name"]

            empty = call_tool("filtered_deck", {
                "params": {"action": "empty", "deck_id": filtered_deck_id},
            })
            assert empty.get("isError") is not True, empty

            after = _present(card_id)
            assert after["deck_name"] == home_deck
            assert after["filtered_deck_name"] is None
        finally:
            if filtered_deck_id is not None:
                delete_filtered_deck(filtered_deck_id)
            _delete_notes([note_id])


# -- TestPresentCardErrors -----------------------------------------------------

class TestPresentCardErrors:
    """Error paths."""

    def test_nonexistent_card_id_errors(self):
        """present_card raises HandlerError (isError=True) for an unknown card."""
        result = call_tool("present_card", {"card_id": NONEXISTENT_CARD_ID})

        assert result.get("isError") is True, result
        assert "card not found" in str(result).lower()

    def test_deleted_card_errors(self):
        """A card whose note was deleted should no longer be presentable."""
        uid = unique_id()
        _deck_name, note_id, card_id = _create_card(uid, "Deleted")

        try:
            # It resolves while the note exists...
            assert _present(card_id)["card_id"] == card_id

            _delete_notes([note_id])

            # ...and errors once the note (and therefore its card) is gone.
            result = call_tool("present_card", {"card_id": card_id})
            assert result.get("isError") is True, result
        finally:
            # delete_notes reports already-deleted ids as notFoundCount instead
            # of failing, so this is safe whether or not the deletion above ran.
            _delete_notes([note_id])

    def test_missing_card_id_errors(self):
        """card_id is required."""
        result = call_tool("present_card", {})
        assert result.get("isError") is True, result
