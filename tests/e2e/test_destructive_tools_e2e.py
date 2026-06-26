"""E2E coverage for the destructive-tools opt-in mechanism (issue #46).

SCOPE
-----
A real destructive action now exists: ``model_fields:remove`` (its Params model
sets ``_destructive = True``), making it the codebase's first destructive
action. Destructive actions are HIDDEN from ``tools/list`` unless the operator
opts in via ``enabled_destructive_tools``. Full hide/reveal coverage is split
across the two E2E suites:

  * HIDE half (this file): runs against the DEFAULT, unfiltered server (port
    3141, ``.docker/config.json``), which does NOT set
    ``enabled_destructive_tools``. So ``model_fields`` is present but its schema
    must NOT advertise the ``remove`` action -- it is hidden by default.
  * REVEAL half + behavioral remove tests: run in the filtered suite (port
    3142, ``.docker/config-filtered.json``), which opts in via
    ``enabled_destructive_tools: ["model_fields:remove"]``. See
    ``test_tool_filtering_e2e.py`` (reveal assertion) and
    ``test_model_fields_remove.py`` (behavioral remove tests).

This file also asserts a SAFETY PROPERTY: the destructive gate must not hide or
alter the normal (non-destructive) toolset.

The GATING LOGIC (whole-tool + per-action hide/reveal, precedence with
disabled_tools, opt-in validation, the write-guard ValueError) is unit-covered
in ``tests/unit/test_destructive_tools.py``.

Runs against the default (unfiltered) server -- typically port 3141.
"""
from __future__ import annotations

from .helpers import list_tools, schema_action_names


# Tools that exist today and must remain visible -- the destructive gate must
# not have silently removed any of them (none are flagged destructive).
_EXPECTED_PRESENT_TOOLS = {
    "find_notes",
    "add_note",
    "list_decks",
    "create_deck",
    "card_management",
    "notes_info",
}


class TestDestructiveGateDoesNotHideNormalTools:
    """Safety property: with nothing flagged destructive, nothing is hidden."""

    def test_server_healthy_and_returns_tools(self):
        """list_tools succeeds and returns a non-empty toolset."""
        tools = list_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0, "expected the server to expose tools"

    def test_normal_toolset_unaffected_by_destructive_mechanism(self):
        """The known core tools are all still present.

        If the destructive gating accidentally hid tools when no tool is
        flagged destructive, these would go missing.
        """
        tools = list_tools()
        names = {t["name"] for t in tools}
        missing = _EXPECTED_PRESENT_TOOLS - names
        assert not missing, (
            f"destructive mechanism appears to have hidden tools that should "
            f"be present: {sorted(missing)}. Found: {sorted(names)}"
        )

    def test_multi_action_tool_keeps_all_actions(self):
        """card_management still advertises actions in its schema.

        No card_management action is flagged destructive, so the per-action
        gate must leave its discriminated union untouched (a non-empty schema
        confirms the union was not stripped to nothing).
        """
        tools = list_tools()
        cm = next((t for t in tools if t["name"] == "card_management"), None)
        assert cm is not None, "card_management tool should be present"

        schema = cm.get("inputSchema", {})
        params = schema.get("properties", {}).get("params", {})
        variants = params.get("oneOf", []) or params.get("anyOf", [])
        # Either explicit variants (multi-action union) or a discriminator
        # mapping must be present and non-empty.
        mapping = params.get("discriminator", {}).get("mapping", {})
        assert variants or mapping, (
            "card_management schema lost its action variants -- the per-action "
            "destructive gate may have wrongly filtered a non-destructive tool"
        )


class TestDestructiveActionHiddenByDefault:
    """HIDE half: model_fields:remove is destructive and hidden by default.

    Runs against the default, unfiltered server (port 3141), which does NOT set
    enabled_destructive_tools -- so the remove action must be absent from the
    model_fields schema while the tool itself stays present.
    """

    def test_model_fields_tool_present(self):
        """The model_fields tool itself is exposed (only the action is hidden)."""
        tools = list_tools()
        names = {t["name"] for t in tools}
        assert "model_fields" in names, (
            f"model_fields tool should be present. Found: {sorted(names)}"
        )

    def test_remove_action_hidden_but_others_present(self):
        """remove is absent from the schema; add/rename/reposition remain."""
        tools = list_tools()
        mf = next((t for t in tools if t["name"] == "model_fields"), None)
        assert mf is not None, "model_fields tool should be present"

        actions = schema_action_names(mf)
        assert actions, "Could not extract action names from model_fields schema"
        assert "remove" not in actions, (
            f"remove is destructive and must be hidden by default, but found "
            f"in schema actions: {sorted(actions)}"
        )
        for action in ("add", "rename", "reposition"):
            assert action in actions, (
                f"Expected non-destructive action '{action}' to remain. "
                f"Found: {sorted(actions)}"
            )
