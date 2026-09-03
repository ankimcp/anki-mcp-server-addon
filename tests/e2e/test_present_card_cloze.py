"""Tests for present_card with Cloze notes (issue #72).

present_card must not leak the cloze deletion answer through `question`,
even though it renders with the note type CSS included (unlike
get_due_cards' `front`).
"""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool


def _delete_notes(note_ids: list[int]) -> None:
    """Delete notes to keep the shared collection clean."""
    if note_ids:
        call_tool("delete_notes", {"notes": note_ids, "confirmDeletion": True})


class TestPresentCardCloze:
    """present_card must render Cloze questions without leaking the answer."""

    def test_question_hides_answer_and_answer_reveals_it(self):
        models_result = call_tool("model_names", {})
        assert "Cloze" in models_result.get("modelNames", []), (
            f"default collection should provide the Cloze note type: {models_result}"
        )

        uid = unique_id()
        deck_name = f"E2E::PCCloze{uid}"
        create = call_tool("create_deck", {"deck_name": deck_name})
        assert create.get("isError") is not True, f"Setup failed: {create}"

        note_result = call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Cloze",
            "fields": {
                "Text": f"The capital of France is {{{{c1::Paris}}}} ({uid})",
                "Back Extra": "",
            },
        })
        assert note_result.get("isError") is not True, f"Setup failed: {note_result}"
        note_id = note_result["note_id"]

        try:
            due = call_tool("get_due_cards", {"deck_name": deck_name})
            assert due.get("isError") is not True, due
            assert len(due["cards"]) == 1
            card_id = due["cards"][0]["cardId"]

            question_only = call_tool("present_card", {"card_id": card_id})
            assert question_only.get("isError") is not True, question_only
            question = question_only["card"]["question"]
            assert "Paris" not in question
            assert "data-cloze" not in question
            assert "<style>" in question

            with_answer = call_tool("present_card", {
                "card_id": card_id,
                "show_answer": "true",
            })
            assert with_answer.get("isError") is not True, with_answer
            assert "Paris" in with_answer["card"]["answer"]
        finally:
            _delete_notes([note_id])
