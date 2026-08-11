"""Tests for the ``will_force_full_sync`` flag on model-mutating tool results.

ORDER-ROBUSTNESS IS THE POINT OF THIS FILE.

``will_force_full_sync`` mirrors Anki's collection-wide "schema modified since
last sync" state (``scm > ls`` on the ``col`` row). That state is STICKY: once
anything marks the schema modified it stays true until a full sync clears it,
and the Docker test collection never syncs to AnkiWeb, so nothing ever clears
it. Any earlier suite -- test_model_fields_add.py and friends, or an earlier
test in this very file -- permanently flips it true for the rest of the session.

Two rules follow, and every test here obeys them:

1. Always assert the key EXISTS and is a real ``bool`` on each tool's success
   result. That holds regardless of collection history.
2. Only assert ``True`` where it is guaranteed by the call itself -- i.e.
   immediately after a model_fields add / remove / reposition, each of which
   changes field ordinals and so modifies the schema unconditionally.
   model_fields RENAME is not in that set: it only rewrites a field's name,
   leaving ordinals and the sort field alone, so rslib never marks the schema
   modified and rename gets a rule-1 structural assertion instead.

There are deliberately NO unconditional ``False`` assertions. A False assertion
would need a preceding read of the current flag to guard on, and no read-only
tool or resource exposes it, so the value cannot be established without
mutating. create_model / update_model_styling / update_model_templates do not
themselves modify the schema, but they can only be asserted structurally (rule
1) because a prior test may already have dirtied the collection.

Each test creates a DISPOSABLE, uniquely-named model so the shared "Basic" model
is never dirtied (same rationale as test_model_fields_add.py).
"""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool, list_tools

FLAG = "will_force_full_sync"

CARD_TEMPLATES = [
    {
        "Name": "Card 1",
        "Front": "{{Front}}",
        "Back": "{{FrontSide}}<hr id=\"answer\">{{Back}}",
    },
]


def _assert_flag_present(result: dict, context: str) -> bool:
    """Assert the result carries the flag as a real bool, and return its value.

    This is rule 1 from the module docstring -- the only assertion that is safe
    to make unconditionally on any model-mutating tool.
    """
    assert result.get("isError") is not True, f"{context} failed: {result}"
    assert FLAG in result, f"{context} result is missing {FLAG!r}: {result}"
    value = result[FLAG]
    # Explicitly reject SQLite's integer 1/0 leaking through un-coerced:
    # isinstance(1, bool) is False, so this catches it.
    assert isinstance(value, bool), (
        f"{context} returned {FLAG}={value!r} of type {type(value).__name__}, expected bool"
    )
    return value


def _create_model(prefix: str, fields: list[str] | None = None) -> tuple[str, dict]:
    """Create a disposable model with a unique name; return (name, result)."""
    model_name = f"{prefix}{unique_id()}"
    result = call_tool("create_model", {
        "model_name": model_name,
        "in_order_fields": fields or ["Front", "Back"],
        "card_templates": CARD_TEMPLATES,
    })
    return model_name, result


class TestSchemaSyncFlagPresence:
    """Every model-mutating tool must carry the flag on success."""

    def test_create_model_carries_flag(self):
        """create_model reports the post-write collection state."""
        _, result = _create_model("SyncFlagCreate")
        # Structural only: create_model does not modify the schema, but a
        # previous test may already have -- see module docstring.
        _assert_flag_present(result, "create_model")

    def test_update_model_styling_full_replace_carries_flag(self):
        """Full-replace CSS mode carries the flag."""
        model_name, create_result = _create_model("SyncFlagStylingFull")
        assert create_result.get("isError") is not True, create_result

        result = call_tool("update_model_styling", {
            "model_name": model_name,
            "css": ".card { font-size: 21px; }",
        })

        _assert_flag_present(result, "update_model_styling (full replace)")

    def test_update_model_styling_patch_carries_flag(self):
        """Patch CSS mode carries the flag on its own success path."""
        model_name, create_result = _create_model("SyncFlagStylingPatch")
        assert create_result.get("isError") is not True, create_result

        seeded = call_tool("update_model_styling", {
            "model_name": model_name,
            "css": ".card { font-size: 21px; }",
        })
        assert seeded.get("isError") is not True, f"seed styling failed: {seeded}"

        result = call_tool("update_model_styling", {
            "model_name": model_name,
            "old_str": "21px",
            "new_str": "32px",
        })

        assert result.get("patched") is True, f"expected a patch result: {result}"
        _assert_flag_present(result, "update_model_styling (patch)")

    def test_update_model_templates_full_replace_carries_flag(self):
        """Full-replace template mode carries the flag."""
        model_name, create_result = _create_model("SyncFlagTemplatesFull")
        assert create_result.get("isError") is not True, create_result

        result = call_tool("update_model_templates", {
            "model_name": model_name,
            "templates": {
                "Card 1": {"Front": "{{Front}}<br>full", "Back": "{{Back}}"},
            },
        })

        _assert_flag_present(result, "update_model_templates (full replace)")

    def test_update_model_templates_patch_carries_flag(self):
        """Patch template mode carries the flag on its own success path."""
        model_name, create_result = _create_model("SyncFlagTemplatesPatch")
        assert create_result.get("isError") is not True, create_result

        result = call_tool("update_model_templates", {
            "model_name": model_name,
            "template_name": "Card 1",
            "side": "Front",
            "old_str": "{{Front}}",
            "new_str": "{{Front}}<br>patched",
        })

        assert result.get("patched") is True, f"expected a patch result: {result}"
        _assert_flag_present(result, "update_model_templates (patch)")

    def test_rename_field_carries_flag(self):
        """rename is STRUCTURAL-ONLY -- it does not itself modify the schema.

        It lives here rather than with the other model_fields actions below,
        and deliberately makes no ``is True`` assertion. rslib only calls ``set_schema_modified`` when field ORDINALS change
        (rslib/src/notetype/schemachange.rs, ``update_notes_for_changed_fields``
        -> ``ords_changed``) or when the sort field moves; pylib's
        ``rename_field`` just rewrites ``field["name"]`` and leaves both alone.
        So a rename on a clean collection legitimately reports False, and rule 2
        from the module docstring does not apply -- only rule 1 does.

        A False assertion is equally unavailable here: the flag is sticky and an
        earlier test in the session may already have dirtied the collection.
        """
        model_name, create_result = _create_model("SyncFlagFieldsRename")
        assert create_result.get("isError") is not True, create_result

        result = call_tool("model_fields", {
            "params": {
                "action": "rename",
                "model_name": model_name,
                "field_name": "Back",
                "new_name": "Reverse",
            }
        })

        _assert_flag_present(result, "model_fields rename")

class TestSchemaSyncFlagTrueAfterSchemaChange:
    """model_fields add / remove / reposition modify the schema unconditionally.

    This is the only place a value assertion is sound (rule 2): each of these
    actions changes field ordinals, which is what makes rslib mark the schema
    modified, so the assertion holds whatever the collection's prior state was.
    rename is deliberately NOT here -- see test_rename_field_carries_flag.
    """

    def test_add_field_forces_full_sync(self):
        """add is the guaranteed-True anchor for the whole file."""
        model_name, create_result = _create_model("SyncFlagFieldsAdd")
        assert create_result.get("isError") is not True, create_result

        result = call_tool("model_fields", {
            "params": {
                "action": "add",
                "model_name": model_name,
                "field_name": "Hint",
            }
        })

        assert _assert_flag_present(result, "model_fields add") is True

    def test_reposition_field_forces_full_sync(self):
        """reposition changes field ordinals."""
        model_name, create_result = _create_model(
            "SyncFlagFieldsReposition", ["Front", "Back", "Extra"]
        )
        assert create_result.get("isError") is not True, create_result

        result = call_tool("model_fields", {
            "params": {
                "action": "reposition",
                "model_name": model_name,
                "field_name": "Extra",
                "index": 0,
            }
        })

        assert _assert_flag_present(result, "model_fields reposition") is True

    def test_prose_warning_still_accompanies_the_flag(self):
        """The pre-existing FULL_SYNC_WARNING prose must not be replaced by the flag."""
        model_name, create_result = _create_model("SyncFlagFieldsWarning")
        assert create_result.get("isError") is not True, create_result

        result = call_tool("model_fields", {
            "params": {
                "action": "add",
                "model_name": model_name,
                "field_name": "Hint",
            }
        })

        assert _assert_flag_present(result, "model_fields add") is True
        assert "warning" in result, f"prose warning was dropped: {result}"
        assert "full sync" in result["warning"].lower()


class TestSchemaSyncFlagDocumented:
    """The flag is part of the API contract, so descriptions must explain it."""

    def test_descriptions_document_the_flag_and_its_stickiness(self):
        """Every tool carrying the key must describe it as NEXT-sync state.

        The stickiness caveat is what stops a client from reading the flag as
        "this call caused a full sync".
        """
        descriptions = {
            tool["name"]: tool.get("description", "")
            for tool in list_tools()
        }

        for name in (
            "create_model",
            "update_model_styling",
            "update_model_templates",
            "model_fields",
        ):
            assert name in descriptions, f"{name} is not registered"
            description = descriptions[name]
            assert FLAG in description, f"{name} description does not mention {FLAG}"
            assert "sticky" in description.lower(), (
                f"{name} description does not state the flag is sticky"
            )
