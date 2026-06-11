"""E2E smoke test for the destructive-tools opt-in mechanism (issue #46).

SCOPE / DELIBERATE LIMITATION
-----------------------------
This is a MINIMAL smoke test, not full hide/reveal coverage. The shipping
codebase flags NOTHING as destructive yet -- the ``destructive=True`` /
``_destructive`` machinery exists, but no real tool or action uses it. A full
end-to-end hide/reveal test (assert a destructive tool is absent from
``tools/list`` by default, then present after opting it in via
``enabled_destructive_tools``) requires a real destructive tool/action to
exist in the schema.

That tool does not exist yet: it arrives with the deferred deck-management
work (the planned ``deck_management`` subpackage / ``delete_decks`` etc., see
PR #45 group 2, which issue #46 explicitly unblocks). We intentionally do NOT
add a fake destructive tool to the shipping code just to enable an E2E case --
that would pollute the production tool inventory. Wiring a destructive-tool
config into the filtered E2E compose would likewise require shipping-code
changes, so it is intentionally NOT done here.

Until then:
  * The GATING LOGIC (whole-tool + per-action hide/reveal, precedence with
    disabled_tools, opt-in validation, the write-guard ValueError) is fully
    covered by the unit suite: ``tests/unit/test_destructive_tools.py``.
  * This file only asserts the SAFETY PROPERTY for the current state: adding
    the ``enabled_destructive_tools`` config key + the gating code path did
    NOT accidentally hide or alter the normal toolset when no tool is flagged
    destructive.

When a real destructive tool lands, extend this file (and likely the filtered
compose under ``.docker/``) with the proper hide/reveal assertions.

Runs against the default (unfiltered) server -- typically port 3141.
"""
from __future__ import annotations

from .helpers import list_tools


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
