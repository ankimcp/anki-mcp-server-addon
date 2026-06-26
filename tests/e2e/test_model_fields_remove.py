"""Tests for the ``remove`` action of the model_fields multi-action tool.

IMPORTANT -- DESTRUCTIVE ACTION, HIDDEN BY DEFAULT
--------------------------------------------------
The ``remove`` action is flagged destructive (its Params model sets
``_destructive = True``). Destructive actions are HIDDEN from the tool schema --
the discriminated union is rebuilt without them -- UNLESS the operator opts in
via the ``enabled_destructive_tools`` config containing ``"model_fields:remove"``.

The DEFAULT e2e suite (port 3141, ``.docker/config.json``) does NOT set
``enabled_destructive_tools``, so ``remove`` is absent from the union there and
these tests are SKIPPED. To exercise them, run against a server whose config
includes::

    "enabled_destructive_tools": ["model_fields:remove"]

The ``_require_remove_action`` autouse fixture inspects the live model_fields
schema and skips at runtime when ``remove`` is not exposed (same runtime-skip
style as test_update_notes.py).

Each test creates a DISPOSABLE, uniquely-named model via create_model so the
shared "Basic" model is never dirtied. Uniquely-named models that no other test
touches are safe to leave behind without cleanup (same rationale as
test_update_model_styling.py).
"""
from __future__ import annotations

import pytest

from .conftest import unique_id
from .helpers import call_tool, list_tools, schema_action_names


def _model_fields_actions() -> set[str]:
    """Return the action names currently exposed by the model_fields schema.

    Destructive actions absent from the rebuilt discriminated union (because they
    are not opted in via enabled_destructive_tools) will not appear here.
    """
    tools = list_tools()
    tool = next((t for t in tools if t["name"] == "model_fields"), None)
    if tool is None:
        return set()
    return schema_action_names(tool)


@pytest.fixture(autouse=True)
def _require_remove_action():
    """Skip when the destructive remove action is not opted in on this server."""
    if "remove" not in _model_fields_actions():
        pytest.skip(
            "model_fields:remove is destructive and hidden from the schema unless "
            "enabled_destructive_tools includes 'model_fields:remove' (the default "
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
    """Tests for the remove action in the model_fields tool."""

    def test_remove_field(self):
        """Removing a field drops it and persists the shortened field list."""
        model_name = _create_model(["Front", "Back", "Extra"])

        result = call_tool("model_fields", {
            "params": {
                "action": "remove",
                "model_name": model_name,
                "field_name": "Extra",
            }
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

        result = call_tool("model_fields", {
            "params": {
                "action": "remove",
                "model_name": model_name,
                "field_name": "Nope",
            }
        })

        assert result.get("isError") is True
        assert "not found" in str(result).lower()

    def test_reject_removing_last_field(self):
        """Removing the only remaining field is rejected."""
        model_name = _create_model(["Only"])

        result = call_tool("model_fields", {
            "params": {
                "action": "remove",
                "model_name": model_name,
                "field_name": "Only",
            }
        })

        assert result.get("isError") is True
        assert "last field" in str(result).lower()
