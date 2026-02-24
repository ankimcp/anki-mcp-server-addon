"""E2E tests for the twenty_rules MCP prompt."""
from __future__ import annotations

import pytest

from .helpers import get_prompt, list_prompts


class TestTwentyRulesDiscovery:
    """Tests that twenty_rules appears correctly in prompts/list."""

    def test_prompts_list_includes_twenty_rules(self):
        """twenty_rules should be registered in prompts/list."""
        prompts = list_prompts()
        names = [p["name"] for p in prompts]
        assert "twenty_rules" in names

    def test_twenty_rules_has_no_required_parameters(self):
        """twenty_rules prompt should have no required parameters."""
        prompts = list_prompts()
        prompt = next(p for p in prompts if p["name"] == "twenty_rules")
        arguments = prompt.get("arguments", [])
        required = [a for a in arguments if a.get("required", False)]
        assert required == []


@pytest.fixture(scope="class")
def twenty_rules_result():
    """Fetch the twenty_rules prompt once and share across content tests."""
    return get_prompt("twenty_rules")


class TestTwentyRulesContent:
    """Tests that prompts/get returns well-formed content for twenty_rules."""

    def test_get_twenty_rules_returns_messages(self, twenty_rules_result):
        """prompts/get for twenty_rules should return a messages list."""
        assert "messages" in twenty_rules_result
        assert len(twenty_rules_result["messages"]) > 0

    def test_message_has_text_content(self, twenty_rules_result):
        """The returned message should have text content."""
        message = twenty_rules_result["messages"][0]
        assert "content" in message
        content = message["content"]
        assert content.get("type") == "text"
        assert len(content.get("text", "")) > 0

    def test_contains_minimum_information_principle(self, twenty_rules_result):
        """Content should reference the Minimum Information Principle."""
        text = twenty_rules_result["messages"][0]["content"]["text"]
        assert "Minimum Information Principle" in text

    def test_contains_cloze_deletion(self, twenty_rules_result):
        """Content should mention Cloze Deletion."""
        text = twenty_rules_result["messages"][0]["content"]["text"]
        assert "Cloze Deletion" in text

    def test_contains_do_not_learn_if_you_do_not_understand(self, twenty_rules_result):
        """Content should include the first rule about understanding first."""
        text = twenty_rules_result["messages"][0]["content"]["text"]
        assert "Do Not Learn If You Do Not Understand" in text

    def test_references_store_media_file_tool(self, twenty_rules_result):
        """Content should reference the store_media_file tool name, not mediaActions."""
        text = twenty_rules_result["messages"][0]["content"]["text"]
        assert "store_media_file" in text
        assert "mediaActions" not in text

    def test_references_add_note_tool(self, twenty_rules_result):
        """Content should reference the addNote tool name (AnkiConnect convention)."""
        text = twenty_rules_result["messages"][0]["content"]["text"]
        assert "addNote" in text
