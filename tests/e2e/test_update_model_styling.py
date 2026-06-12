"""Tests for the update_model_styling tool.

These tests create DISPOSABLE, uniquely-named models via create_model so the
shared "Basic" model is never dirtied. Uniquely-named models that no other test
touches are safe to leave behind without cleanup (same rationale as
test_update_model_templates_partial_mutation.py).

The round-trip test is the behavior-parity guard for the
get_model_copy_or_raise refactor (issue #47 follow-up): the tool now mutates a
deepcopy instead of the live cached notetype dict, so a successful update must
still persist and be readable back via model_styling.
"""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool, list_tools

CARD_TEMPLATES = [
    {
        "Name": "Card 1",
        "Front": "{{Front}}",
        "Back": "{{FrontSide}}<hr id=\"answer\">{{Back}}",
    },
]


def _create_disposable_model(css: str | None = None) -> str:
    """Create a disposable model with a unique name, optionally with initial CSS."""
    model_name = f"StylingModel{unique_id()}"
    args: dict = {
        "model_name": model_name,
        "in_order_fields": ["Front", "Back"],
        "card_templates": CARD_TEMPLATES,
    }
    if css is not None:
        args["css"] = css
    result = call_tool("create_model", args)
    assert result.get("isError") is not True, f"create_model failed: {result}"
    return model_name


class TestUpdateModelStyling:
    """Tests for updating the CSS styling of a note type."""

    def test_tool_appears_in_tools_list(self):
        """Both styling tools should be registered and visible."""
        tool_names = [t["name"] for t in list_tools()]
        assert "update_model_styling" in tool_names
        assert "model_styling" in tool_names

    def test_update_css_round_trips(self):
        """A successful update must persist and be readable back via model_styling.

        This protects the deepcopy refactor: mutating a copy instead of the live
        cached dict must still result in a persisted, readable change.
        """
        uid = unique_id()
        model_name = _create_disposable_model()
        new_css = f".card {{ color: red; }} /* marker-{uid} */"

        result = call_tool("update_model_styling", {
            "model_name": model_name,
            "css": new_css,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["model_name"] == model_name
        assert result["css_length"] == len(new_css)

        # Read back: the persisted CSS must equal exactly what was set.
        after = call_tool("model_styling", {"model_name": model_name})
        assert after.get("isError") is not True, f"Read failed: {after}"
        assert after["css"] == new_css

    def test_update_css_info_flags(self):
        """css_info flags should reflect the content of the new CSS."""
        model_name = _create_disposable_model()
        new_css = (
            ".card { direction: rtl; text-align: right; }\n"
            ".cloze { font-weight: bold; }"
        )

        result = call_tool("update_model_styling", {
            "model_name": model_name,
            "css": new_css,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["css_info"]["has_rtl_support"] is True
        assert result["css_info"]["has_card_styling"] is True
        assert result["css_info"]["has_cloze_styling"] is True

    def test_update_reports_old_css_length_change(self):
        """Updating a model with existing CSS should report old length and delta."""
        old_css = ".card { color: blue; }"
        model_name = _create_disposable_model(css=old_css)
        new_css = ".card { color: green; font-size: 20px; }"

        result = call_tool("update_model_styling", {
            "model_name": model_name,
            "css": new_css,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["old_css_length"] == len(old_css)
        assert result["css_length"] == len(new_css)
        assert result["css_length_change"] == len(new_css) - len(old_css)

    def test_unknown_model_errors(self):
        """update_model_styling with a non-existent model should return isError."""
        uid = unique_id()
        result = call_tool("update_model_styling", {
            "model_name": f"NoSuchModel{uid}",
            "css": ".card { color: red; }",
        })

        assert result.get("isError") is True
        assert "not found" in str(result).lower()
