"""Tests for get_due_cards include_answer parameter (issue #72)."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool


class TestGetDueCardsIncludeAnswer:
    """get_due_cards must not leak the answer unless include_answer=True."""

    def test_default_omits_back_and_front_is_question(self):
        """Without include_answer, back is absent and front is the question."""
        uid = unique_id()
        deck_name = f"E2E::IncludeAnswerDefault{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Test Question {uid}",
                "Back": f"Test Answer {uid}"
            }
        })

        result = call_tool("get_due_cards", {"deck_name": deck_name})

        assert result.get("isError") is not True
        assert len(result["cards"]) == 1
        card = result["cards"][0]
        assert "back" not in card
        assert f"Test Question {uid}" in card["front"]

    def test_include_answer_true_returns_back(self):
        """include_answer=true returns back with the rendered answer."""
        uid = unique_id()
        deck_name = f"E2E::IncludeAnswerTrue{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Test Question {uid}",
                "Back": f"Test Answer {uid}"
            }
        })

        result = call_tool("get_due_cards", {
            "deck_name": deck_name,
            "include_answer": "true"
        })

        assert result.get("isError") is not True
        assert len(result["cards"]) == 1
        card = result["cards"][0]
        assert "back" in card
        assert f"Test Answer {uid}" in card["back"]

    def test_include_answer_false_omits_back(self):
        """include_answer=false explicitly still omits back."""
        uid = unique_id()
        deck_name = f"E2E::IncludeAnswerFalse{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Test Question {uid}",
                "Back": f"Test Answer {uid}"
            }
        })

        result = call_tool("get_due_cards", {
            "deck_name": deck_name,
            "include_answer": "false"
        })

        assert result.get("isError") is not True
        assert len(result["cards"]) == 1
        assert "back" not in result["cards"][0]

    def test_cloze_note_does_not_leak_answer_through_front(self):
        """Cloze notes must not leak the deletion answer through front."""
        models_result = call_tool("model_names", {})
        assert "Cloze" in models_result.get("modelNames", []), (
            f"default collection should provide the Cloze note type: {models_result}"
        )

        uid = unique_id()
        deck_name = f"E2E::IncludeAnswerCloze{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Cloze",
            "fields": {
                "Text": f"The capital of France is {{{{c1::Paris}}}} ({uid})",
                "Back Extra": ""
            }
        })

        # Default call: front must not contain the cloze answer or markup.
        result = call_tool("get_due_cards", {"deck_name": deck_name})
        assert result.get("isError") is not True
        assert len(result["cards"]) == 1
        card = result["cards"][0]
        assert "back" not in card
        assert "Paris" not in card["front"]
        assert "{{c1::" not in card["front"]
        assert "data-cloze" not in card["front"]

        # With include_answer=true: back contains the revealed answer.
        result = call_tool("get_due_cards", {
            "deck_name": deck_name,
            "include_answer": "true"
        })
        assert result.get("isError") is not True
        assert len(result["cards"]) == 1
        card = result["cards"][0]
        assert "back" in card
        assert "Paris" in card["back"]
