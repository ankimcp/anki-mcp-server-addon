"""Unit tests for get_model_copy_or_raise (leak-safety contract, issue #47).

``col.models.by_name()`` hands back a LIVE reference to Anki's cached notetype
dict. Mutating that reference can leak partial state into the in-memory cache if
the subsequent update is rejected or abandoned (issue #47). The helper defends
against this by returning a *deep* copy so callers can mutate freely without ever
touching the live cache, and raises a standardized ``HandlerError`` when the
model is absent.

These are pure-logic tests: no Docker, no running Anki. A hand-rolled fake
``col`` stands in for the collection. The import path resolves without Anki
because ``handler_wrappers`` imports ``aqt`` only lazily (inside ``_get_mw``),
not at module top -- so importing ``_model_helpers`` (which imports only
``copy``, ``typing`` and ``HandlerError``) never reaches Anki. The vendored
pydantic + ``aqt``/``primitives`` stubbing that lets ``anki_mcp_server`` import
at all is set up once in ``tests/unit/conftest.py``; no per-test bootstrap is
needed here (this mirrors how ``test_destructive_tools.py`` imports addon
modules directly). Because ``conftest.py`` stubs ``anki_mcp_server.primitives``
as a non-package (to suppress tool auto-discovery), the helper is loaded as a
single file via ``importlib`` rather than through the normal dotted path.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from anki_mcp_server.handler_wrappers import HandlerError

# _model_helpers lives under primitives/essential/tools/, whose __init__.py runs
# pkgutil auto-discovery (importing every tool module) — and conftest stubs
# `anki_mcp_server.primitives` to a non-package to suppress that. So we can't
# import the helper through the normal dotted path. Load the single file
# directly (same technique conftest.py uses for dependency_loader.py), using the
# full dotted name so the helper's `from ....handler_wrappers import ...`
# relative import still resolves to the real anki_mcp_server.handler_wrappers.
_HELPER_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "anki_mcp_server" / "primitives" / "essential" / "tools" / "_model_helpers.py"
)
_spec = importlib.util.spec_from_file_location(
    "anki_mcp_server.primitives.essential.tools._model_helpers", _HELPER_PATH
)
_model_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_model_helpers)
get_model_copy_or_raise = _model_helpers.get_model_copy_or_raise


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeModels:
    """Stand-in for ``col.models`` exposing just ``by_name``.

    Records each call so tests can assert the lookup name, and (when
    configured with a dict) returns the *exact same object* on every call so a
    test can compare the source the fake still holds against the copy the
    helper returned.
    """

    def __init__(self, model: dict | None) -> None:
        self._model = model
        self.calls: list[str] = []

    def by_name(self, name: str) -> dict | None:
        self.calls.append(name)
        return self._model


class _FakeCol:
    """Minimal fake collection: only ``.models`` is needed by the helper."""

    def __init__(self, model: dict | None) -> None:
        self.models = _FakeModels(model)


def _notetype() -> dict:
    """A nested, notetype-shaped dict for isolation testing.

    Shape mirrors a real Anki notetype closely enough to exercise deep copying
    at multiple levels: top-level scalars, a list of nested template dicts, and
    a list of nested field dicts.
    """
    return {
        "id": 1,
        "name": "M",
        "css": "old",
        "tmpls": [{"name": "Card 1", "qfmt": "Q", "afmt": "A"}],
        "flds": [{"name": "Front"}],
    }


# ---------------------------------------------------------------------------
# 1. Isolation -- the core guarantee (top-down deep copy)
# ---------------------------------------------------------------------------


class TestIsolation:
    """Mutating the returned copy must never touch the source object."""

    def test_mutations_at_every_depth_do_not_leak_into_source(self):
        source = _notetype()
        col = _FakeCol(source)

        result = get_model_copy_or_raise(col, "M")

        # Mutate the copy at multiple depths.
        result["css"] = "new"                      # top-level scalar
        result["tmpls"][0]["qfmt"] = "leaked"       # nested list-element field
        result["tmpls"].append({"name": "Card 2"})  # grow a nested list
        result["flds"][0]["name"] = "Back"          # nested field dict

        # The source the fake still holds must be byte-for-byte the original.
        # (This is the assertion that fails if deepcopy is swapped for dict()
        # or .copy() -- a shallow copy would let the nested mutations leak.)
        assert source == {
            "id": 1,
            "name": "M",
            "css": "old",
            "tmpls": [{"name": "Card 1", "qfmt": "Q", "afmt": "A"}],
            "flds": [{"name": "Front"}],
        }

        # And the fake genuinely handed back the very object we mutated against:
        # by_name() returns the same source instance, still unchanged.
        assert col.models.by_name("M") is source
        assert col.models.by_name("M")["css"] == "old"
        assert col.models.by_name("M")["tmpls"][0]["qfmt"] == "Q"
        assert len(col.models.by_name("M")["tmpls"]) == 1


# ---------------------------------------------------------------------------
# 2. Returns equal-but-not-identical (proves the copy is deep, not shallow)
# ---------------------------------------------------------------------------


class TestEqualButNotIdentical:
    def test_top_level_equal_but_distinct_object(self):
        source = _notetype()
        col = _FakeCol(source)

        result = get_model_copy_or_raise(col, "M")

        assert result == source        # equal by value
        assert result is not source    # but a different object

    def test_nested_containers_are_also_copied(self):
        source = _notetype()
        col = _FakeCol(source)

        result = get_model_copy_or_raise(col, "M")

        # Depth proof: the nested list AND its dict elements are fresh objects.
        assert result["tmpls"] is not source["tmpls"]
        assert result["tmpls"][0] is not source["tmpls"][0]
        assert result["flds"][0] is not source["flds"][0]


# ---------------------------------------------------------------------------
# 3. Not-found raises HandlerError with the standardized payload
# ---------------------------------------------------------------------------


class TestNotFound:
    def test_missing_model_raises_handler_error(self):
        col = _FakeCol(None)  # by_name returns None

        with pytest.raises(HandlerError) as excinfo:
            get_model_copy_or_raise(col, "Nope")

        err = excinfo.value
        # .message holds the human-readable text (see HandlerError.__init__).
        assert err.message == 'Model "Nope" not found'
        assert 'Model "Nope" not found' in str(err)
        # .hint is the actionable suggestion (set positionally by the helper).
        assert err.hint == "Use model_names tool to see available models"
        # No explicit code is passed -> .code defaults to None.
        assert err.code is None
        # Extra kwargs land in the .data dict; the helper passes model_name=...
        assert err.data == {"model_name": "Nope"}


# ---------------------------------------------------------------------------
# 4. by_name is called once with the exact requested name
# ---------------------------------------------------------------------------


class TestLookupCall:
    def test_by_name_called_once_with_given_name_on_success(self):
        col = _FakeCol(_notetype())

        get_model_copy_or_raise(col, "M")

        assert col.models.calls == ["M"]

    def test_by_name_called_once_with_given_name_on_missing(self):
        col = _FakeCol(None)

        with pytest.raises(HandlerError):
            get_model_copy_or_raise(col, "Ghost")

        assert col.models.calls == ["Ghost"]
