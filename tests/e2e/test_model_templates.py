"""Tests for model_templates and update_model_templates tools."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool, list_tools

# Shared model used for template round-trips. Tests that modify its templates
# MUST restore them (try/finally) so the shared collection stays clean.
MODEL_NAME = "Basic"


def _read_templates(model_name: str = MODEL_NAME) -> dict[str, dict[str, str]]:
    """Read templates for a model, asserting the read succeeds."""
    result = call_tool("model_templates", {"model_name": model_name})
    assert result.get("isError") is not True, f"Unexpected error: {result}"
    return result["templates"]


class TestModelTemplates:
    """Tests for reading and updating card templates of a note type."""

    def test_tools_appear_in_tools_list(self):
        """Both template tools should be registered and visible."""
        tool_names = [t["name"] for t in list_tools()]
        assert "model_templates" in tool_names
        assert "update_model_templates" in tool_names

    def test_read_templates_basic_model(self):
        """model_templates should return Front/Back HTML for each card type."""
        result = call_tool("model_templates", {"model_name": MODEL_NAME})

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["model_name"] == MODEL_NAME
        assert result["template_count"] == len(result["templates"])
        assert result["template_count"] > 0

        for name, tmpl in result["templates"].items():
            assert "Front" in tmpl
            assert "Back" in tmpl
            assert isinstance(tmpl["Front"], str)
            assert isinstance(tmpl["Back"], str)

        # Basic's question template references the Front field
        first = next(iter(result["templates"].values()))
        assert "{{" in first["Front"]

    def test_read_unknown_model_errors(self):
        """model_templates with a non-existent model should return isError."""
        uid = unique_id()
        result = call_tool("model_templates", {"model_name": f"NoSuchModel{uid}"})

        assert result.get("isError") is True
        assert "not found" in str(result).lower()

    def test_update_unknown_model_errors(self):
        """update_model_templates with a non-existent model should return isError."""
        uid = unique_id()
        result = call_tool("update_model_templates", {
            "model_name": f"NoSuchModel{uid}",
            "templates": {"Card 1": {"Front": "{{Front}}"}},
        })

        assert result.get("isError") is True
        assert "not found" in str(result).lower()

    def test_round_trip_update_and_restore(self):
        """Read -> edit -> write -> read back, then restore the original template."""
        uid = unique_id()
        original = _read_templates()
        card_name = next(iter(original.keys()))
        original_front = original[card_name]["Front"]
        original_back = original[card_name]["Back"]

        marker = f"<!-- e2e-roundtrip-{uid} -->"
        new_front = original_front + marker

        try:
            # Update only Front
            result = call_tool("update_model_templates", {
                "model_name": MODEL_NAME,
                "templates": {card_name: {"Front": new_front}},
            })
            assert result.get("isError") is not True, f"Unexpected error: {result}"
            assert result["template_count"] == 1
            assert card_name in result["updated_templates"]

            # Read back: Front changed, Back untouched
            after = _read_templates()
            assert after[card_name]["Front"] == new_front
            assert after[card_name]["Back"] == original_back
        finally:
            # Restore the original template so the shared model stays clean
            restore = call_tool("update_model_templates", {
                "model_name": MODEL_NAME,
                "templates": {
                    card_name: {"Front": original_front, "Back": original_back},
                },
            })
            assert restore.get("isError") is not True, f"Restore failed: {restore}"

        # Verify restoration round-tripped
        restored = _read_templates()
        assert restored[card_name]["Front"] == original_front
        assert restored[card_name]["Back"] == original_back

    def test_update_both_front_and_back(self):
        """Updating Front and Back together should report a single updated template."""
        uid = unique_id()
        original = _read_templates()
        card_name = next(iter(original.keys()))
        original_front = original[card_name]["Front"]
        original_back = original[card_name]["Back"]

        try:
            result = call_tool("update_model_templates", {
                "model_name": MODEL_NAME,
                "templates": {
                    card_name: {
                        "Front": original_front + f"<!-- f-{uid} -->",
                        "Back": original_back + f"<!-- b-{uid} -->",
                    },
                },
            })
            assert result.get("isError") is not True, f"Unexpected error: {result}"
            assert result["template_count"] == 1

            after = _read_templates()
            assert after[card_name]["Front"].endswith(f"<!-- f-{uid} -->")
            assert after[card_name]["Back"].endswith(f"<!-- b-{uid} -->")
        finally:
            restore = call_tool("update_model_templates", {
                "model_name": MODEL_NAME,
                "templates": {
                    card_name: {"Front": original_front, "Back": original_back},
                },
            })
            assert restore.get("isError") is not True, f"Restore failed: {restore}"

    def test_unrecognized_key_lowercase_front_errors(self):
        """A lowercase 'front' key must be rejected, not silently ignored."""
        original = _read_templates()
        card_name = next(iter(original.keys()))

        result = call_tool("update_model_templates", {
            "model_name": MODEL_NAME,
            "templates": {card_name: {"front": "<div>bogus</div>"}},
        })

        assert result.get("isError") is True
        assert "front" in str(result).lower()

        # Must not have mutated the template (no silent no-op, no partial write)
        after = _read_templates()
        assert after[card_name] == original[card_name]

    def test_unrecognized_key_answer_errors(self):
        """An 'Answer' key (AnkiConnect-style) must be rejected with an error."""
        original = _read_templates()
        card_name = next(iter(original.keys()))

        result = call_tool("update_model_templates", {
            "model_name": MODEL_NAME,
            "templates": {card_name: {"Answer": "<div>bogus</div>"}},
        })

        assert result.get("isError") is True
        assert "answer" in str(result).lower()

        after = _read_templates()
        assert after[card_name] == original[card_name]

    def test_mixed_valid_and_invalid_keys_rejects_whole_update(self):
        """A valid 'Front' mixed with an invalid key must reject without mutating."""
        original = _read_templates()
        card_name = next(iter(original.keys()))

        result = call_tool("update_model_templates", {
            "model_name": MODEL_NAME,
            "templates": {
                card_name: {
                    "Front": "<div>should never be written</div>",
                    "back": "<div>bogus key</div>",
                },
            },
        })

        assert result.get("isError") is True

        # Pre-pass validation must reject before any mutation happens
        after = _read_templates()
        assert after[card_name] == original[card_name]

    def test_unknown_card_template_name_errors(self):
        """Updating a non-existent card template name should return isError."""
        uid = unique_id()
        original = _read_templates()

        result = call_tool("update_model_templates", {
            "model_name": MODEL_NAME,
            "templates": {f"NoSuchCard{uid}": {"Front": "{{Front}}"}},
        })

        assert result.get("isError") is True
        assert "not found" in str(result).lower()

        # Nothing should have changed
        assert _read_templates() == original
