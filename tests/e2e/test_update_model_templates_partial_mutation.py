"""Regression tests for issue #47: update_model_templates partial in-place mutation.

The bug: update_model_templates validates template *keys* (Front/Back) in a
pre-pass before mutating, but unknown card-template *names* are only detected
inside the mutation loop. col.models.by_name() returns the LIVE cached notetype
dict, and the loop writes tmpl["qfmt"]/tmpl["afmt"] in place. So when a single
call mixes a VALID template name with an UNKNOWN one, the valid template is
already mutated in the in-memory cache by the time the "Card template(s) not
found" error is raised. col.models.update_dict() is never reached (nothing is
written to disk), but a follow-up model_templates READ re-fetches the same
cached dict via col.models.by_name() and sees the leaked value.

These tests create a DISPOSABLE, uniquely-named model with two card templates
and run the repro on it. A disposable model is deliberate: the leak lives in the
in-memory cache and could later be persisted to disk by ANY unrelated successful
save of the same notetype. Running the repro on the shared "Basic" model would
leave its cache dirty and risk a permanent disk write if a later test does a
successful template update on Basic. A uniquely-named model that no other test
touches is safe to leave dirty.
"""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool

# Marker that must NEVER end up in a valid template after a FAILED mixed update.
LEAK_MARKER = "SHOULD-NOT-PERSIST"


def _create_two_template_model() -> tuple[str, str, str, str, str]:
    """Create a disposable model with two card templates.

    Returns:
        (model_name, valid_card_name, unknown_card_name,
         original_valid_front, original_valid_back)
    """
    uid = unique_id()
    model_name = f"PartialMutationModel{uid}"
    valid_card = "Card A"
    # An unknown name guaranteed not to exist on this model.
    unknown_card = f"NoSuchCard{uid}"

    original_front = "<div class=\"front\">{{Front}}</div>"
    original_back = "{{FrontSide}}<hr id=\"answer\">{{Back}}"

    result = call_tool("create_model", {
        "model_name": model_name,
        "in_order_fields": ["Front", "Back"],
        "card_templates": [
            {"Name": valid_card, "Front": original_front, "Back": original_back},
            {
                "Name": "Card B",
                "Front": "<div>{{Back}}</div>",
                "Back": "{{FrontSide}}<hr>{{Front}}",
            },
        ],
    })
    assert result.get("isError") is not True, f"create_model failed: {result}"
    return model_name, valid_card, unknown_card, original_front, original_back


class TestUpdateModelTemplatesPartialMutation:
    """Reproduces issue #47: a failed mixed update must not leak a partial write."""

    def test_valid_template_not_mutated_when_paired_with_unknown_name(self):
        """A failed mixed update (valid + unknown name) must not leak the valid write.

        The call mixes a VALID template name (whose Front carries LEAK_MARKER) with
        an UNKNOWN template name. The tool returns isError "Card template(s) not
        found" and never calls update_dict. But on buggy code the valid template's
        qfmt was already mutated in the cached dict, so the subsequent
        model_templates READ (re-fetching the same cached dict) sees LEAK_MARKER.

        The reproducing assertion is the final one: the valid template's Front must
        still equal its ORIGINAL value, NOT the leaked marker. On current buggy code
        this assertion FAILS, which is the reproduction.
        """
        model_name, valid_card, unknown_card, original_front, original_back = (
            _create_two_template_model()
        )

        # Mixed update: valid name carries the marker, unknown name triggers the error.
        result = call_tool("update_model_templates", {
            "model_name": model_name,
            "templates": {
                valid_card: {"Front": LEAK_MARKER},
                unknown_card: {"Front": "x"},
            },
        })

        # The whole update must be rejected because of the unknown card name.
        assert result.get("isError") is True, f"Expected an error, got: {result}"
        assert "not found" in str(result).lower()

        # Read the templates back. On buggy code the valid template's Front was
        # mutated in place in the cache before the error was raised, so this read
        # observes the leak.
        after = call_tool("model_templates", {"model_name": model_name})
        assert after.get("isError") is not True, f"Read failed: {after}"

        # THE REPRODUCING ASSERTION: a rejected update must not have mutated the
        # valid template. On current buggy code the Front reads LEAK_MARKER and
        # this fails.
        assert after["templates"][valid_card]["Front"] == original_front, (
            "Partial in-place mutation leaked: the valid template's Front was "
            "changed by an update that returned an error."
        )
        # Back was never touched in the request and must be untouched too.
        assert after["templates"][valid_card]["Back"] == original_back

    def test_leak_is_order_independent_unknown_name_first(self):
        """Same leak when the UNKNOWN name is listed BEFORE the valid name.

        Issue #47 states the leak is order-independent: because the mutation loop
        mutates the valid template in place regardless of dict ordering, the result
        is the same whether the unknown name comes first or last. This variant lists
        the unknown name first to lock in that order-independence.

        Same reproducing assertion: after the failed update, the valid template's
        Front must equal its ORIGINAL value, not LEAK_MARKER. Fails on buggy code.
        """
        model_name, valid_card, unknown_card, original_front, original_back = (
            _create_two_template_model()
        )

        result = call_tool("update_model_templates", {
            "model_name": model_name,
            "templates": {
                unknown_card: {"Front": "x"},
                valid_card: {"Front": LEAK_MARKER},
            },
        })

        assert result.get("isError") is True, f"Expected an error, got: {result}"
        assert "not found" in str(result).lower()

        after = call_tool("model_templates", {"model_name": model_name})
        assert after.get("isError") is not True, f"Read failed: {after}"

        # THE REPRODUCING ASSERTION (order-independent variant).
        assert after["templates"][valid_card]["Front"] == original_front, (
            "Partial in-place mutation leaked (unknown-name-first ordering): the "
            "valid template's Front was changed by an update that returned an error."
        )
        assert after["templates"][valid_card]["Back"] == original_back
