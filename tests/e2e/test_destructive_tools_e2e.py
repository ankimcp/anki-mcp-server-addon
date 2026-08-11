"""E2E coverage for the destructive-tools opt-in mechanism (issue #46).

SCOPE
-----
Two real destructive things ship today: the ACTION ``model_fields:remove`` (its
Params model sets ``_destructive = True``) and the whole TOOL
``change_note_type`` (``@Tool(..., destructive=True)``). Both are HIDDEN from
``tools/list`` unless the operator opts in via ``enabled_destructive_tools``.
Neither server opts in to both, so hide/reveal coverage is split across the two
E2E suites -- each one is the HIDE side for what the other reveals:

  * This file runs against the DEFAULT server (port 3141,
    ``.docker/config.json``), whose ``enabled_destructive_tools`` lists ONLY
    ``"change_note_type"``. So the destructive ACTION is hidden here:
    ``model_fields`` is present but its schema must NOT advertise ``remove``.
    The opted-in ``change_note_type`` is exercised in
    ``test_change_note_type.py``, which runs against this same server.
  * The filtered suite (port 3142, ``.docker/config-filtered.json``) opts in the
    other way round, via ``enabled_destructive_tools: ["model_fields:remove"]``:
    it REVEALS the remove action (``test_tool_filtering_e2e.py`` reveal
    assertion, plus behavioral tests in ``test_model_fields_remove.py``) and is
    the HIDE side for the whole-tool case, asserting ``change_note_type`` is
    absent there.

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
# not have silently removed any of them (none of THESE are flagged destructive;
# the destructive ones are covered by the hide/reveal classes below).
_EXPECTED_PRESENT_TOOLS = {
    "find_notes",
    "add_note",
    "list_decks",
    "create_deck",
    "card_management",
    "notes_info",
}


class TestDestructiveGateDoesNotHideNormalTools:
    """Safety property: the gate hides ONLY what is flagged destructive."""

    def test_server_healthy_and_returns_tools(self):
        """list_tools succeeds and returns a non-empty toolset."""
        tools = list_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0, "expected the server to expose tools"

    def test_normal_toolset_unaffected_by_destructive_mechanism(self):
        """The known core tools are all still present.

        None of them is flagged destructive, so if the gating over-reached
        beyond the tools/actions that actually are, these would go missing.
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

    Runs against the default, unfiltered server (port 3141), whose
    enabled_destructive_tools lists only "change_note_type" and therefore does
    NOT opt in "model_fields:remove" -- so the remove action must be absent from
    the model_fields schema while the tool itself stays present.
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
