"""Unit tests for _deck_name_helpers -- the deck-name rules shared by
``create_deck`` and ``card_change_deck``.

Pure-logic tests: no Docker, no running Anki. The helper imports only
``typing`` and ``HandlerError``, and the functions that need a collection take
it as an argument, so a tiny fake stands in for it.

``conftest.py`` stubs ``anki_mcp_server.primitives`` as a non-package, so the
helper is loaded as a single file under its real dotted name, exactly as
``test_patch_helpers.py`` does.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from anki_mcp_server.handler_wrappers import HandlerError

_HELPER_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "anki_mcp_server" / "primitives" / "essential" / "tools" / "_deck_name_helpers.py"
)
_spec = importlib.util.spec_from_file_location(
    "anki_mcp_server.primitives.essential.tools._deck_name_helpers", _HELPER_PATH
)
_deck_name_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_deck_name_helpers)

MAX_DECK_DEPTH = _deck_name_helpers.MAX_DECK_DEPTH
validate_deck_name = _deck_name_helpers.validate_deck_name
validate_new_deck_depth = _deck_name_helpers.validate_new_deck_depth
deck_path = _deck_name_helpers.deck_path
missing_parent_depths = _deck_name_helpers.missing_parent_depths
resolve_deck_target = _deck_name_helpers.resolve_deck_target


def _levels(count: int) -> str:
    return "::".join(f"L{i}" for i in range(1, count + 1))


class _FakeDecks:
    """Case-insensitive name -> id lookup, like ``col.decks.id_for_name``."""

    def __init__(self, names: set[str]):
        self._ids = {name.lower(): index for index, name in enumerate(sorted(names), start=100)}
        self.looked_up: list[str] = []

    def id_for_name(self, name: str):
        self.looked_up.append(name)
        return self._ids.get(name.lower())


class _FakeCol:
    def __init__(self, names: set[str] = frozenset()):
        self.decks = _FakeDecks(set(names))


def _with_ancestors(name: str) -> set[str]:
    return set(deck_path(name))


# ---------------------------------------------------------------------------
# validate_deck_name
# ---------------------------------------------------------------------------


def test_limit_is_ten():
    assert MAX_DECK_DEPTH == 10


@pytest.mark.parametrize("name", ["Spanish", "Spanish::Verbs::Irregular"])
def test_valid_name_accepted(name):
    assert validate_deck_name(name) is None


def test_depth_is_not_checked_structurally():
    assert validate_deck_name(_levels(15)) is None


@pytest.mark.parametrize("name", ["", "   ", "::", ":", " : ", "\u00a0", "\u3000::"])
def test_blank_name_rejected(name):
    with pytest.raises(HandlerError) as exc:
        validate_deck_name(name)
    assert exc.value.message == "Deck name cannot be empty"
    assert exc.value.code == "validation_error"
    assert exc.value.data == {"deck_name": name}


@pytest.mark.parametrize(
    "name,message",
    [
        ("::Verbs", "Deck name level 1 of 2 is empty"),
        ("Spanish::::Verbs", "Deck name level 2 of 3 is empty"),
        ("Spanish::", "Deck name level 2 of 2 is empty"),
        ("Spanish::   ::Verbs", "Deck name level 2 of 3 is empty"),
        ("Spanish::\u3000", "Deck name level 2 of 2 is empty"),
        ("Spanish:::", "Deck name level 2 of 2 is empty"),
    ],
)
def test_empty_level_rejected(name, message):
    with pytest.raises(HandlerError) as exc:
        validate_deck_name(name)
    assert exc.value.message == message
    assert exc.value.code == "validation_error"
    assert exc.value.data == {"deck_name": name}


def test_single_colon_inside_a_level_is_not_a_separator():
    assert validate_deck_name("A:B::C") is None
    assert deck_path("A:B::C") == ["A:B", "A:B::C"]


def test_unicode_name_accepted():
    assert validate_deck_name("Español::日本語") is None


# Every character Rust's char::is_ascii_control matches, which is what Anki's
# normalized_deck_name_component strips: U+0000-U+001F and U+007F.
_CONTROL_CHARS = [chr(code) for code in range(0x20)] + ["\x7f"]

_GENERIC_CONTROL_HINT = (
    "Remove tabs, line breaks and other control characters, U+0000-U+001F and "
    "U+007F, then send the deck name you meant; removing them from this name "
    "leaves it blank, empties a level or changes where its levels split"
)


def _suggestion_hint(name: str) -> str:
    return (
        "Remove tabs, line breaks and other control characters; without them "
        f'the name is "{name}", send that if it is the deck you meant'
    )


def _control_error(name: str) -> HandlerError:
    with pytest.raises(HandlerError) as exc:
        validate_deck_name(name)
    assert exc.value.code == "validation_error"
    assert exc.value.data == {"deck_name": name}
    assert not any(_deck_name_helpers._is_control_char(c) for c in exc.value.message)
    assert not any(_deck_name_helpers._is_control_char(c) for c in exc.value.hint)
    assert "(" not in exc.value.hint and ")" not in exc.value.hint
    return exc.value


@pytest.mark.parametrize("char", _CONTROL_CHARS, ids=lambda c: f"U+{ord(c):04X}")
def test_control_character_rejected(char):
    error = _control_error(f"Spanish{char}::Verbs")
    assert error.message == (
        f"Deck name level 1 of 2 contains control character U+{ord(char):04X}"
    )
    assert error.hint == _suggestion_hint("Spanish::Verbs")


def test_trailing_newline():
    error = _control_error("Spanish::Verbs\n")
    assert error.message == "Deck name level 2 of 2 contains control character U+000A"
    assert error.hint == _suggestion_hint("Spanish::Verbs")


def test_tab_inside_a_level():
    error = _control_error("Span\tish")
    assert error.message == "Deck name level 1 of 1 contains control character U+0009"
    assert error.hint == _suggestion_hint("Spanish")


def test_control_character_that_would_hide_a_separator():
    # Anki splits on "::" before stripping \x01, so it would store a one-level
    # deck whose name contains a literal "::". Stripping first gives "A::B",
    # which splits differently, so "A::B" is not offered as what the caller meant.
    error = _control_error("A:\x01:B")
    assert error.message == "Deck name level 1 of 1 contains control character U+0001"
    assert error.hint == _GENERIC_CONTROL_HINT


def test_control_character_that_would_move_a_separator():
    # Same level count after cleaning ("A:::B"), but the levels split differently.
    error = _control_error("A:\x01::B")
    assert error.hint == _GENERIC_CONTROL_HINT


@pytest.mark.parametrize("name", ["\n", "\t\r", " \x01 ", ":\x7f:"])
def test_name_blank_once_cleaned(name):
    error = _control_error(name)
    assert error.message.startswith("Deck name level 1 of ")
    assert error.hint == _GENERIC_CONTROL_HINT


def test_level_empty_once_cleaned():
    error = _control_error("Spanish::\t::Verbs")
    assert error.message == "Deck name level 2 of 3 contains control character U+0009"
    assert error.hint == _GENERIC_CONTROL_HINT


def test_first_control_character_is_reported():
    error = _control_error("A\x7f::B\x01")
    assert error.message == "Deck name level 1 of 2 contains control character U+007F"
    assert error.hint == _suggestion_hint("A::B")


def test_level_of_first_control_character_is_reported():
    error = _control_error("A::B::C\x1f::D\x01")
    assert error.message == "Deck name level 3 of 4 contains control character U+001F"


@pytest.mark.parametrize("name", ["\t", "\n", "Spanish::\t", "Spanish::\x01::Verbs"])
def test_control_character_reported_before_blank_or_empty_level(name):
    assert "control character" in _control_error(name).message


def test_control_character_error_wire_text():
    # The text the client sees, assembled as _error_handler does.
    error = _control_error("Spanish::Verbs\n")
    wire = (
        f"[{error.code}] {error.message} (hint: {error.hint}) "
        f"(context: {error.data})"
    )
    assert wire == (
        "[validation_error] Deck name level 2 of 2 contains control character "
        "U+000A (hint: Remove tabs, line breaks and other control characters; "
        'without them the name is "Spanish::Verbs", send that if it is the '
        "deck you meant) (context: {'deck_name': 'Spanish::Verbs\\n'})"
    )


def test_control_character_renders_escaped_in_context():
    # _error_handler formats the context with the dict's repr, so the raw
    # character never reaches the error text.
    with pytest.raises(HandlerError) as exc:
        validate_deck_name("A\x00B")
    assert str(exc.value.data) == "{'deck_name': 'A\\x00B'}"


@pytest.mark.parametrize("char", ["\x80", "\x9f", "\u200b"])
def test_non_ascii_control_like_characters_accepted(char):
    assert validate_deck_name(f"A{char}B") is None


# ---------------------------------------------------------------------------
# validate_new_deck_depth
# ---------------------------------------------------------------------------


def test_new_deck_ten_levels_accepted():
    assert validate_new_deck_depth(_levels(10)) is None


def test_new_deck_depth_counts_double_colon_only():
    assert validate_new_deck_depth(":".join(_levels(11).split("::"))) is None


def test_new_deck_eleven_levels_rejected():
    name = _levels(11)
    with pytest.raises(HandlerError) as exc:
        validate_new_deck_depth(name)
    assert exc.value.message == "Deck name has 11 levels; a new deck can have at most 10"
    assert "at most 10" in exc.value.hint
    assert "given: 11" in exc.value.hint
    assert "existing deck of any depth" in exc.value.hint
    assert exc.value.code == "validation_error"
    assert exc.value.data == {"deck_name": name}


# ---------------------------------------------------------------------------
# deck_path
# ---------------------------------------------------------------------------


def test_deck_path_single():
    assert deck_path("Spanish") == ["Spanish"]


def test_deck_path_nested():
    assert deck_path("Spanish::Verbs::Irregular") == [
        "Spanish", "Spanish::Verbs", "Spanish::Verbs::Irregular",
    ]


# ---------------------------------------------------------------------------
# missing_parent_depths
# ---------------------------------------------------------------------------


def test_missing_parents_none_exist():
    col = _FakeCol()
    assert missing_parent_depths(col, "A::B::C") == [1, 2]


def test_missing_parents_some_exist():
    col = _FakeCol({"A"})
    assert missing_parent_depths(col, "a::B::C") == [2]


def test_missing_parents_does_not_check_the_deck_itself():
    col = _FakeCol({"A", "A::B"})
    assert missing_parent_depths(col, "A::B::C") == []
    assert col.decks.looked_up == ["A", "A::B"]


def test_missing_parents_single_level():
    col = _FakeCol()
    assert missing_parent_depths(col, "A") == []
    assert col.decks.looked_up == []


# ---------------------------------------------------------------------------
# resolve_deck_target
# ---------------------------------------------------------------------------


def test_new_deck_reports_missing_parents():
    col = _FakeCol({"A"})
    target = resolve_deck_target(col, "A::B::C")
    assert target.exists is False
    assert target.missing_parent_depths == (2,)
    assert col.decks.looked_up == ["A::B::C", "A", "A::B"]


def test_new_deck_at_ten_levels_accepted():
    target = resolve_deck_target(_FakeCol(), _levels(10))
    assert target.exists is False
    assert target.missing_parent_depths == tuple(range(1, 10))


def test_existing_deck_is_reused():
    col = _FakeCol(_with_ancestors("A::B"))
    target = resolve_deck_target(col, "a::b")
    assert target.exists is True
    assert target.missing_parent_depths == ()
    assert col.decks.looked_up == ["a::b"]


def test_existing_deck_deeper_than_limit_accepted():
    name = _levels(15)
    col = _FakeCol(_with_ancestors(name))
    target = resolve_deck_target(col, name)
    assert target.exists is True
    assert target.missing_parent_depths == ()
    # The deck itself is found, so no parent is looked up.
    assert col.decks.looked_up == [name]


def test_existing_deck_deeper_than_limit_matched_case_insensitively():
    name = _levels(12)
    col = _FakeCol(_with_ancestors(name))
    assert resolve_deck_target(col, name.lower()).exists is True


def test_missing_deck_deeper_than_limit_rejected():
    with pytest.raises(HandlerError) as exc:
        resolve_deck_target(_FakeCol(), _levels(11))
    assert exc.value.message == "Deck name has 11 levels; a new deck can have at most 10"


def test_missing_deck_deeper_than_limit_rejected_even_with_deep_parent():
    # The limit counts the name's levels, not the missing ancestors: an
    # existing 10-level parent doesn't make a new 11-level deck acceptable.
    parent = _levels(10)
    col = _FakeCol(_with_ancestors(parent))
    with pytest.raises(HandlerError) as exc:
        resolve_deck_target(col, f"{parent}::L11")
    assert "11 levels" in exc.value.message


@pytest.mark.parametrize("name", ["A:\x01:B", "A::\x7f", "A\t"])
def test_control_character_rejected_even_if_the_deck_exists(name):
    # Anki's lookup strips control characters, so the deck would be found.
    col = _FakeCol({name, "A::B", "A"})
    with pytest.raises(HandlerError) as exc:
        resolve_deck_target(col, name)
    assert "control character" in exc.value.message
    assert col.decks.looked_up == []


def test_structure_checked_before_any_lookup():
    col = _FakeCol()
    with pytest.raises(HandlerError) as exc:
        resolve_deck_target(col, "Spanish::::Verbs::" + _levels(10))
    assert "is empty" in exc.value.message
    assert "levels;" not in exc.value.message
    assert col.decks.looked_up == []
