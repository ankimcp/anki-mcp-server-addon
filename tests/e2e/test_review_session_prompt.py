"""E2E tests for the review_session MCP prompt's review_style='gui' branch."""
from __future__ import annotations

from .helpers import get_prompt


def _prompt_text(result: dict) -> str:
    return result["messages"][0]["content"]["text"]


class TestReviewSessionGuiStyle:
    """The 'gui' review_style should drive the hands-free GUI reviewer workflow."""

    def test_mentions_gui_deck_review_and_gui_answer_card(self):
        result = get_prompt("review_session", {
            "deck_name": "Default",
            "card_limit": "5",
            "review_style": "gui",
        })
        text = _prompt_text(result)
        assert "gui_deck_review" in text
        assert "gui_answer_card" in text
        assert "gui_current_card" in text
        assert "gui_show_answer" in text

    def test_workflow_does_not_use_ai_driven_review_tools(self):
        """The gui workflow steps must never call get_due_cards, present_card,
        or rate_card -- only the GUI reviewer tools drive this mode."""
        result = get_prompt("review_session", {
            "deck_name": "Default",
            "card_limit": "5",
            "review_style": "gui",
        })
        text = _prompt_text(result)

        workflow = text.split("WORKFLOW:", 1)[1].split("RATING GUIDE:", 1)[0]
        for forbidden in ("get_due_cards", "present_card", "rate_card"):
            assert forbidden not in workflow, (
                f"'{forbidden}' should not appear in the gui workflow steps"
            )

        # The prohibition itself is stated elsewhere in the prompt.
        assert "Never call get_due_cards, present_card, or rate_card" in text

    def test_no_unbury_step(self):
        """gui mode never buries cards, so it must not mention unbury."""
        result = get_prompt("review_session", {
            "deck_name": "Default",
            "card_limit": "5",
            "review_style": "gui",
        })
        text = _prompt_text(result)
        assert "unbury" not in text.lower()
