"""Render a card's HTML without the GUI and without saving anything.

Two rendering paths, both of which are exactly what Anki itself uses:

* existing note -- ``card.question()`` / ``card.answer()`` on the note's real
  cards (the reviewer path, ``render_existing_card`` in the backend).
* unsaved content -- ``note.ephemeral_card()``, which is what Anki's own card
  layout screen (``aqt/clayout.py``) calls to preview a template/field
  combination. It renders through ``render_uncommitted_card_legacy``, a
  read-only backend call: it looks up the note type, synthesizes an in-memory
  card and renders. Nothing is inserted or updated, so the note never reaches
  the collection.

Both paths return ``question_and_style()`` / ``answer_and_style()``, i.e. the
rendered HTML prefixed with a ``<style>`` block carrying the note type CSS, so
the two modes have one consistent output shape.
"""
from typing import Any, Literal, Optional

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col


_DESCRIPTION = (
    "Render a card's HTML exactly as Anki would produce it, without opening the GUI. "
    "Use it to preview a note type + field combination BEFORE creating notes, or to "
    "inspect how an existing note actually renders.\n"
    "\n"
    "Two mutually exclusive modes -- pass exactly one:\n"
    "1. note_id: render a card of a note that already exists. card_ord is the 0-based "
    "index into that note's cards (ordered by template ordinal).\n"
    "2. model_name + fields: render UNSAVED content. An in-memory note is built from the "
    "given note type and field values and rendered WITHOUT being added to the collection "
    "-- nothing is created, nothing is modified. Unknown field names are rejected; omitted "
    "fields render as empty. For a regular note type card_ord picks the card template "
    "(0-based); for a CLOZE note type card_ord is the cloze index, so card_ord=0 renders "
    "cloze number 1, card_ord=1 renders cloze number 2, and so on.\n"
    "\n"
    "side selects the sides to return: \"question\", \"answer\" or \"both\" (default).\n"
    "\n"
    "Returns question_html and/or answer_html (each ALREADY starts with a <style> block "
    "holding the note type CSS), css (that same CSS, raw -- it is a convenience copy, NOT "
    "additional CSS to append), mode (\"note\" or \"unsaved\"), model_name, card_ord, "
    "card_ordinal, template_name, is_cloze, and in note_id mode note_id, card_id and "
    "card_count. card_ordinal is the rendered card's REAL template ordinal: it equals "
    "card_ord in unsaved mode, but in note mode card_ord indexes the note's EXISTING "
    "cards, so when a note is missing some of its cards the two differ (e.g. card_ord=1 "
    "on a note whose only cards are ordinals 0 and 2 renders card_ordinal=2).\n"
    "\n"
    "LIMITATIONS -- this is the HTML BEFORE a browser touches it:\n"
    "- No JavaScript runs. <script> tags come back as literal HTML.\n"
    "- MathJax (\\(...\\), \\[...\\]) is NOT typeset; the delimiters are returned verbatim "
    "because Anki typesets them in the webview.\n"
    "- [latex], [$]...[$] and [$$]...[$$] blocks ARE preprocessed into "
    "<img src=\"latex-<hash>.png\"> references, and if the image is not in the media folder "
    "yet Anki shells out to latex/dvipng to build it (same as the reviewer). When LaTeX "
    "generation is disabled or the tools are missing, an error message is appended to the "
    "HTML instead of an image.\n"
    "- Media keeps bare relative filenames (<img src=\"pic.jpg\">); paths are not rewritten "
    "to absolute URLs, so the HTML is not directly viewable outside Anki's media folder.\n"
    "- [sound:...] and TTS tags are stripped out of the HTML by Anki's renderer (they are "
    "carried as separate AV tags, which this tool does not return).\n"
    "- No screenshot, no post-JavaScript DOM, no browser console output."
)


def _resolve_mode(
    note_id: Optional[int],
    model_name: Optional[str],
    fields: Optional[dict[str, str]],
) -> str:
    """Return "note" or "unsaved", rejecting mixed or empty mode arguments.

    Args:
        note_id: Note to render, or None.
        model_name: Note type for unsaved rendering, or None.
        fields: Field values for unsaved rendering, or None.

    Returns:
        "note" when note_id was given, "unsaved" for model_name + fields.

    Raises:
        HandlerError: If both modes or neither mode was requested, or if
            fields was given without model_name.
    """
    unsaved_args = model_name is not None or fields is not None

    if note_id is not None and unsaved_args:
        raise HandlerError(
            "note_id cannot be combined with model_name/fields",
            hint="Pass note_id to render an existing note, OR model_name + fields to "
                 "render unsaved content -- not both",
            code="validation_error",
        )

    if note_id is not None:
        return "note"

    if not unsaved_args:
        raise HandlerError(
            "No render target given",
            hint="Pass note_id to render an existing note, or model_name + fields to "
                 "render unsaved content",
            code="validation_error",
        )

    if model_name is None:
        raise HandlerError(
            "fields requires model_name",
            hint="Field values are meaningless without a note type -- pass model_name too "
                 "(use model_names to see available note types)",
            code="validation_error",
        )

    return "unsaved"


def _require_model(col: Any, model_name: str) -> dict[str, Any]:
    """Look up a note type by name.

    Args:
        col: Anki collection.
        model_name: Note type name.

    Returns:
        The note type dict.

    Raises:
        HandlerError: If no note type carries that name.
    """
    model = col.models.by_name(model_name)
    if model is None:
        raise HandlerError(
            f"Model not found: {model_name}",
            hint="Use model_names tool to see available note types",
            code="not_found",
            model_name=model_name,
        )
    return model


def _is_cloze(model: dict[str, Any]) -> bool:
    """Report whether a note type is a cloze type."""
    from anki.consts import MODEL_CLOZE

    return model.get("type") == MODEL_CLOZE


def _template_name(model: dict[str, Any], card_ord: int) -> str:
    """Return the display name of the template used for a given ordinal.

    Cloze note types have exactly one template that serves every cloze number,
    so the ordinal is ignored for them (mirrors ``Card.template()``).
    """
    templates = model.get("tmpls") or []
    if not templates:
        return ""
    index = 0 if _is_cloze(model) else card_ord
    if index >= len(templates):
        return ""
    return templates[index].get("name", "")


def _render_existing(col: Any, note_id: int, card_ord: int) -> dict[str, Any]:
    """Render a card of an existing note through the normal reviewer path.

    Args:
        col: Anki collection.
        note_id: Note whose card should be rendered.
        card_ord: 0-based index into the note's cards (ordered by ordinal).

    Returns:
        Dict with the rendered card plus the note-mode response fields.

    Raises:
        HandlerError: If the note does not exist, has no cards, or card_ord is
            out of range.
    """
    # Modern Anki raises anki.errors.NotFoundError for missing notes; KeyError is
    # kept for backward compatibility with older versions (same pattern as
    # notes_info_tool). Anything else is a real backend failure and must
    # propagate rather than be reported to the client as "not found".
    from anki.errors import NotFoundError

    try:
        note = col.get_note(note_id)
    except (NotFoundError, KeyError) as e:
        raise HandlerError(
            f"Note not found with ID {note_id}",
            hint="Use find_notes to look up note IDs",
            code="not_found",
            note_id=note_id,
        ) from e

    cards = note.cards()
    if not cards:
        raise HandlerError(
            f"Note {note_id} has no cards",
            hint="The note generates no cards -- check its fields against the note type's "
                 "templates with model_templates",
            code="not_found",
            note_id=note_id,
        )

    if card_ord < 0 or card_ord >= len(cards):
        raise HandlerError(
            f"card_ord {card_ord} is out of range for note {note_id}",
            hint=f"This note has {len(cards)} card(s); card_ord must be between 0 and "
                 f"{len(cards) - 1}",
            code="validation_error",
            note_id=note_id,
            card_count=len(cards),
        )

    card = cards[card_ord]
    model = note.note_type()
    if model is None:
        raise HandlerError(
            f"Note type missing for note {note_id}",
            hint="The note references a note type that no longer exists",
            code="not_found",
            note_id=note_id,
        )

    return {
        "card": card,
        "model": model,
        "extra": {
            "note_id": note.id,
            "card_id": card.id,
            "card_count": len(cards),
        },
    }


def _validate_fields(model: dict[str, Any], fields: dict[str, str]) -> None:
    """Reject field names the note type does not define.

    Args:
        model: Note type dict.
        fields: Caller-supplied field values.

    Raises:
        HandlerError: If any supplied name is not a field of the note type.
    """
    valid_names = [field["name"] for field in model.get("flds", [])]
    unknown = [name for name in fields if name not in valid_names]
    if unknown:
        raise HandlerError(
            f"Unknown fields for this note type: {', '.join(unknown)}",
            hint=f"Valid field names are: {', '.join(valid_names)}",
            code="validation_error",
            model_name=model.get("name"),
            valid_fields=valid_names,
        )


def _validate_ord_for_model(
    model: dict[str, Any], note: Any, card_ord: int
) -> None:
    """Check card_ord against the note type's templates (or cloze numbers).

    Args:
        model: Note type dict.
        note: The unsaved note carrying the caller's field values.
        card_ord: Requested ordinal.

    Raises:
        HandlerError: If the ordinal selects no template / no cloze deletion.
    """
    if card_ord < 0:
        raise HandlerError(
            f"card_ord must be 0 or greater, got {card_ord}",
            hint="card_ord is a 0-based index",
            code="validation_error",
        )

    if _is_cloze(model):
        # A cloze note type has one template; the ordinal is the cloze index,
        # so cloze number N lives at card_ord N-1.
        cloze_numbers = sorted(note.cloze_numbers_in_fields())
        if not cloze_numbers:
            raise HandlerError(
                "No cloze deletions found in the given fields",
                hint="Cloze note types need {{c1::...}} markers in a field before a card "
                     "can be rendered",
                code="validation_error",
                model_name=model.get("name"),
            )
        if card_ord + 1 not in cloze_numbers:
            raise HandlerError(
                f"card_ord {card_ord} selects cloze number {card_ord + 1}, which the given "
                f"fields do not contain",
                hint=f"Available cloze numbers are {cloze_numbers} -- use card_ord = "
                     f"cloze number - 1",
                code="validation_error",
                model_name=model.get("name"),
                cloze_numbers=cloze_numbers,
            )
        return

    template_count = len(model.get("tmpls") or [])
    if card_ord >= template_count:
        raise HandlerError(
            f"card_ord {card_ord} is out of range for note type "
            f"\"{model.get('name')}\"",
            hint=f"This note type has {template_count} card template(s); card_ord must be "
                 f"between 0 and {template_count - 1}. Use model_templates to list them",
            code="validation_error",
            model_name=model.get("name"),
            template_count=template_count,
        )


def _render_unsaved(
    col: Any, model_name: str, fields: dict[str, str], card_ord: int
) -> dict[str, Any]:
    """Render field values against a note type without saving anything.

    Builds an in-memory note and renders it with ``Note.ephemeral_card``, the
    same call Anki's card layout preview uses. The backend synthesizes a card
    in memory -- no row is written for either the note or the card.

    Args:
        col: Anki collection.
        model_name: Note type to render with.
        fields: Field values; omitted fields stay empty.
        card_ord: Template ordinal, or cloze index for cloze note types.

    Returns:
        Dict with the rendered (unsaved) card and its note type.

    Raises:
        HandlerError: On unknown note type, unknown field names or a bad ordinal.
    """
    model = _require_model(col, model_name)
    _validate_fields(model, fields)

    note = col.new_note(model)
    for name, value in fields.items():
        note[name] = value

    _validate_ord_for_model(model, note, card_ord)

    # Renders through render_uncommitted_card_legacy: read-only, never persisted.
    card = note.ephemeral_card(card_ord)

    return {"card": card, "model": model, "extra": {}}


@Tool("render_card", _DESCRIPTION)
def render_card(
    note_id: Optional[int] = None,
    model_name: Optional[str] = None,
    fields: Optional[dict[str, str]] = None,
    card_ord: int = 0,
    side: Literal["question", "answer", "both"] = "both",
) -> dict[str, Any]:
    col = get_col()

    mode = _resolve_mode(note_id, model_name, fields)

    if mode == "note":
        rendered = _render_existing(col, note_id, card_ord)  # type: ignore[arg-type]
    else:
        rendered = _render_unsaved(
            col,
            model_name,  # type: ignore[arg-type]
            fields or {},
            card_ord,
        )

    card = rendered["card"]
    model = rendered["model"]

    response: dict[str, Any] = {
        "mode": mode,
        "model_name": model.get("name", ""),
        "card_ord": card_ord,
        # The card's real template ordinal. Equal to card_ord in unsaved mode;
        # in note mode it can differ when a note is missing some of its cards.
        "card_ordinal": card.ord,
        "template_name": _template_name(model, card.ord),
        "is_cloze": _is_cloze(model),
        "css": model.get("css", ""),
        "hint": "question_html/answer_html already embed the note type CSS in a leading "
                "<style> block; the css field is the same CSS, not an extra stylesheet. "
                "The HTML is pre-browser: no JavaScript has run and MathJax is not typeset.",
    }
    response.update(rendered["extra"])

    # question_and_style() / answer_and_style() -- the same accessors the
    # reviewer and the card layout preview use.
    if side in ("question", "both"):
        response["question_html"] = card.question()
    if side in ("answer", "both"):
        response["answer_html"] = card.answer()

    return response
