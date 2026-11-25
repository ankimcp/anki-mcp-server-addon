"""Deck-name rules for tools that may create normal decks (``create_deck``,
``card_change_deck``).

Anki itself accepts almost any ``::``-separated name: it strips ASCII control
characters from each level and silently turns an empty or whitespace-only
level into a deck literally named ``blank`` (``rslib/src/decks/name.rs``,
``normalized_deck_name_component``), and only refuses a new name once 11 or
more missing ancestors would have to be created, with a bare "deck nesting
level too deep" error (``rslib/src/decks/addupdate.rs``,
``first_existing_parent``). None of that is what a client asking for
``Spanish::::Verbs`` means, so both tools reject such names up front with one
shared message instead.

The depth limit only guards creation: an existing deck of any depth (shared,
imported, made in Anki's UI) is always accepted.

``validate_deck_name``, ``validate_new_deck_depth`` and ``deck_path`` are
pure; ``resolve_deck_target`` and ``missing_parent_depths`` touch a
collection, which they take as an argument. Both tools call
``resolve_deck_target``.
"""
from typing import Any, NamedTuple, Optional

from ....handler_wrappers import HandlerError

DECK_SEPARATOR = "::"

# Inside Anki's own limit: with no ancestor existing, an 11-level name is the
# deepest rslib accepts.
MAX_DECK_DEPTH = 10

MAX_DEPTH_DOC = (
    f'Nest with "{DECK_SEPARATOR}" (e.g. "Spanish::Verbs::Irregular"); every '
    "level needs a name, and tabs, line breaks or other control characters are "
    f"not allowed. A deck that has to be created can have at most {MAX_DECK_DEPTH} "
    "levels; an existing deck of any depth is fine."
)


class DeckTarget(NamedTuple):
    """A validated deck name checked against the collection.

    ``exists`` is whether a deck with this name already exists;
    ``missing_parent_depths`` are the depths (1 = top level) of the parent
    decks that would be created (empty for an existing deck).
    """

    exists: bool
    missing_parent_depths: tuple[int, ...]


def _is_control_char(char: str) -> bool:
    # Rust's char::is_ascii_control, which Anki strips from every level.
    return ord(char) < 0x20 or ord(char) == 0x7F


def _is_blank(text: str) -> bool:
    # What Anki trims from a level: whitespace and ':'.
    return all(char.isspace() or char == ":" for char in text)


def _without_control_chars(text: str) -> str:
    return "".join(char for char in text if not _is_control_char(char))


def _suggested_name(name: str) -> Optional[str]:
    """Return ``name`` without its control characters, if that is a name the
    caller can be pointed at: structurally valid, and split into the same
    levels as ``name`` (so removing a character didn't join two ':' into a
    new separator, as in ``A:\\x01:B``).
    """
    cleaned = _without_control_chars(name)
    parts = cleaned.split(DECK_SEPARATOR)
    expected = [_without_control_chars(part) for part in name.split(DECK_SEPARATOR)]
    if parts != expected or any(_is_blank(part) for part in parts):
        return None
    return cleaned


def _control_char_error(name: str) -> Optional[HandlerError]:
    parts = name.split(DECK_SEPARATOR)
    for position, part in enumerate(parts, start=1):
        control = next((char for char in part if _is_control_char(char)), None)
        if control is None:
            continue
        suggestion = _suggested_name(name)
        if suggestion is not None:
            hint = (
                "Remove tabs, line breaks and other control characters; without "
                f'them the name is "{suggestion}", send that if it is the deck '
                "you meant"
            )
        else:
            hint = (
                "Remove tabs, line breaks and other control characters, "
                "U+0000-U+001F and U+007F, then send the deck name you meant; "
                "removing them from this name leaves it blank, empties a level "
                "or changes where its levels split"
            )
        return HandlerError(
            f"Deck name level {position} of {len(parts)} contains control "
            f"character U+{ord(control):04X}",
            hint=hint,
            code="validation_error",
            deck_name=name,
        )
    return None


def validate_deck_name(name: str) -> None:
    """Check the structure of ``name``; the depth limit for new decks is
    ``validate_new_deck_depth``.

    Raises:
        HandlerError: If the name contains an ASCII control character, is
            blank, or any level is empty or whitespace-only (checked in that
            order).
    """
    control_error = _control_char_error(name)
    if control_error is not None:
        raise control_error

    if _is_blank(name):
        raise HandlerError(
            "Deck name cannot be empty",
            hint="Provide a deck name, e.g. 'Spanish' or 'Spanish::Verbs'",
            code="validation_error",
            deck_name=name,
        )

    parts = name.split(DECK_SEPARATOR)
    for position, part in enumerate(parts, start=1):
        if _is_blank(part):
            raise HandlerError(
                f"Deck name level {position} of {len(parts)} is empty",
                hint=(
                    f'Every level between "{DECK_SEPARATOR}" separators needs a name, '
                    "e.g. 'Spanish::Verbs', not 'Spanish::::Verbs'"
                ),
                code="validation_error",
                deck_name=name,
            )


def validate_new_deck_depth(name: str) -> None:
    """Reject a deck that would be created with more than ``MAX_DECK_DEPTH``
    levels.

    Raises:
        HandlerError: If ``name`` has more than ``MAX_DECK_DEPTH`` levels.
    """
    levels = len(name.split(DECK_SEPARATOR))
    if levels > MAX_DECK_DEPTH:
        raise HandlerError(
            f"Deck name has {levels} levels; a new deck can have at most "
            f"{MAX_DECK_DEPTH}",
            hint=(
                "No deck with this name exists, and a deck that has to be "
                f'created can have at most {MAX_DECK_DEPTH} "{DECK_SEPARATOR}"-separated '
                f"levels (given: {levels}). An existing deck of any depth "
                "can be used"
            ),
            code="validation_error",
            deck_name=name,
        )


def deck_path(name: str) -> list[str]:
    """Return the full name of every deck from the top level down to ``name``.

    >>> deck_path("Spanish::Verbs::Irregular")
    ['Spanish', 'Spanish::Verbs', 'Spanish::Verbs::Irregular']
    """
    parts = name.split(DECK_SEPARATOR)
    return [DECK_SEPARATOR.join(parts[:depth]) for depth in range(1, len(parts) + 1)]


def missing_parent_depths(col: Any, name: str) -> list[int]:
    """Return the depths (1 = top level) of the parent decks of ``name`` that
    don't exist in ``col`` yet. The deck itself is not checked.

    Uses ``col.decks.id_for_name``, which matches case-insensitively, the same
    lookup ``col.decks.id()`` uses to reuse an existing deck.
    """
    return [
        depth
        for depth, parent in enumerate(deck_path(name)[:-1], start=1)
        if col.decks.id_for_name(parent) is None
    ]


def resolve_deck_target(col: Any, name: str) -> DeckTarget:
    """Validate ``name`` for a call that reuses the deck or creates it.

    Structural rules always apply; the depth limit applies only when no deck
    with this name exists yet. The existence lookup is ``col.decks.id_for_name``
    (case-insensitive, Anki's own name normalization), the same lookup
    ``col.decks.id()`` uses before it creates a deck.

    Raises:
        HandlerError: From ``validate_deck_name``, or from
            ``validate_new_deck_depth`` when the deck doesn't exist.
    """
    validate_deck_name(name)
    if col.decks.id_for_name(name) is not None:
        return DeckTarget(exists=True, missing_parent_depths=())
    validate_new_deck_depth(name)
    return DeckTarget(exists=False, missing_parent_depths=tuple(missing_parent_depths(col, name)))
