"""Tests for the model_field_remove tool.

IMPORTANT -- DESTRUCTIVE TOOL, HIDDEN BY DEFAULT
------------------------------------------------
``model_field_remove`` is declared ``@Tool(..., destructive=True)``. Destructive
tools are HIDDEN from tools/list UNLESS the operator opts in via the
``enabled_destructive_tools`` config containing ``"model_field_remove"`` (or the
deprecated alias ``"model_fields:remove"``).

The DEFAULT e2e suite (port 3141, ``.docker/config.json``) opts in only
``change_note_type``; ``enabled_destructive_tools`` requires an EXACT match per
tool, so ``model_field_remove`` is absent from tools/list there and these tests
are SKIPPED.

They actually run in the FILTERED suite (port 3142, ``.docker/config-filtered.json``,
``make e2e-filtered``), whose config opts the tool in through
``enabled_destructive_tools``.

That is also why the ``e2e-filtered-test`` target runs this file alongside
``test_tool_filtering_e2e.py``: it needs that container's config, not because it
tests filtering.

The ``_require_remove_tool`` autouse fixture checks the live tools/list and skips
at runtime when ``model_field_remove`` is not exposed (same runtime-skip style as
test_update_notes.py).

Each test creates a DISPOSABLE, uniquely-named model via create_model so the
shared "Basic" model is never dirtied. Uniquely-named models that no other test
touches are safe to leave behind without cleanup (same rationale as
test_update_model_styling.py).
"""
from __future__ import annotations

import pytest

from .conftest import unique_id
from .helpers import call_tool, list_tools


@pytest.fixture(autouse=True)
def _require_remove_tool():
    """Skip when the destructive model_field_remove tool is not opted in on this server."""
    if "model_field_remove" not in {t["name"] for t in list_tools()}:
        pytest.skip(
            "model_field_remove is destructive and hidden from tools/list unless "
            "enabled_destructive_tools includes 'model_field_remove' (the default "
            "e2e suite does not opt it in)"
        )


def _create_model(fields: list[str]) -> str:
    """Create a disposable model with a unique name and the given fields.

    The card template references the FIRST field so the model is valid for any
    field set -- including the single-field model used by the last-field test
    (a template referencing a nonexistent field could generate no cards).
    """
    model_name = f"FieldsRemoveModel{unique_id()}"
    first = fields[0]
    templates = [
        {
            "Name": "Card 1",
            "Front": f"{{{{{first}}}}}",
            "Back": f"{{{{FrontSide}}}}<hr id=\"answer\">{{{{{first}}}}}",
        },
    ]
    result = call_tool("create_model", {
        "model_name": model_name,
        "in_order_fields": fields,
        "card_templates": templates,
    })
    assert result.get("isError") is not True, f"create_model failed: {result}"
    return model_name


def _field_names(model_name: str) -> list[str]:
    """Return the current ordered field names via model_field_names."""
    result = call_tool("model_field_names", {"model_name": model_name})
    assert result.get("isError") is not True, f"model_field_names failed: {result}"
    return result["field_names"]


class TestModelFieldsRemove:
    """Tests for the model_field_remove tool."""

    def test_remove_field(self):
        """Removing a field drops it and persists the shortened field list."""
        model_name = _create_model(["Front", "Back", "Extra"])

        result = call_tool("model_field_remove", {
            "model_name": model_name,
            "field_name": "Extra",
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["fields"] == ["Front", "Back"]
        # Schema change must surface the full-sync warning.
        assert "warning" in result
        assert "full sync" in result["warning"].lower()

        # Read back: the persisted field list must match.
        assert _field_names(model_name) == ["Front", "Back"]

    def test_reject_field_not_found(self):
        """Removing a field that does not exist is rejected."""
        model_name = _create_model(["Front", "Back"])

        result = call_tool("model_field_remove", {
            "model_name": model_name,
            "field_name": "Nope",
        })

        assert result.get("isError") is True
        assert "not found" in str(result).lower()

    def test_reject_removing_last_field(self):
        """Removing the only remaining field is rejected."""
        model_name = _create_model(["Only"])

        result = call_tool("model_field_remove", {
            "model_name": model_name,
            "field_name": "Only",
        })

        assert result.get("isError") is True
        assert "last field" in str(result).lower()
