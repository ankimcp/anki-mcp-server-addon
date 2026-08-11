"""Move notes to a different note type (model), remapping their fields.

WHAT ANKI ACTUALLY DOES
-----------------------
The backend entry point is ``col.models.change_notetype_of_notes(request)``
(pylib/anki/models.py), which takes a ``ChangeNotetypeRequest`` protobuf
(proto/anki/notetypes.proto)::

    message ChangeNotetypeRequest {
      repeated int64 note_ids = 1;
      // -1 is used to represent null, as nullable repeated fields
      // are unwieldy in protobuf
      repeated int32 new_fields = 2;
      repeated int32 new_templates = 3;
      int64 old_notetype_id = 4;
      int64 new_notetype_id = 5;
      int64 current_schema = 6;
      string old_notetype_name = 7;
      bool is_cloze = 8;
    }

The mapping is BY INDEX and NEW-CENTRIC: ``new_fields`` has one entry per field
of the NEW note type, holding the index of the OLD field whose content that new
field receives (``-1`` = leave empty). ``rslib``'s ``remap_fields``
(rslib/src/notetype/notetypechange.rs) is literally::

    fields = new_fields.map(|idx| idx.map(|i| old[i]).unwrap_or(""))

This tool takes the far friendlier NAME-based, OLD-CENTRIC form
``{old_field_name: new_field_name | null}`` from the caller and translates it
into that index vector.

The request is never built from scratch: ``col.models.change_notetype_info()``
returns a ``ChangeNotetypeInfo`` whose ``input`` field is a fully-populated
request (ids, names, ``current_schema``, and Anki's DEFAULT field/template
maps). Only ``note_ids`` and ``new_fields`` are overwritten here.
``current_schema`` in particular MUST come from that call -- rslib rejects the
operation with "schema changed" if it no longer matches, which is the guard
against acting on a stale plan.

``new_templates`` IS DELIBERATELY LEFT AT ANKI'S DEFAULT (name match, then
positional gap fill). Two reasons: card templates carry the scheduling state --
remapping them by hand is how review history gets destroyed -- and rslib infers
"cloze involved" from ``new_templates`` being EMPTY, not from the ``is_cloze``
field on the wire (see ``From<ChangeNotetypeRequest> for ChangeNotetypeInput``
in rslib/src/notetype/service.rs, which only checks ``v.is_empty()``). Writing
that list ourselves risks silently flipping the cloze path.

ALL NOTES MUST SHARE ONE OLD NOTE TYPE
--------------------------------------
The request carries a SINGLE ``old_notetype_id``, and ``new_fields`` indices are
interpreted against THAT note type's field list -- but
``update_notes_for_new_notetype_and_generate_cards`` never verifies that each
note actually has it. A mixed list is therefore not rejected by the backend; it
is silently mangled, every note's fields reshuffled by indices that mean nothing
for it. Anki's own UI avoids this by routing through
``get_single_notetype_of_notes`` (rslib/src/notetype/mod.rs:249), which raises
``MultipleNotetypesSelected``. This tool validates in Python instead, so the
error can name the note types found and the offending note ids.

DEFAULT FIELD MAP: PURE NAME MATCH, NOT ANKI'S
----------------------------------------------
Anki's ``default_field_map`` matches by name and THEN fills leftover new fields
with leftover old fields POSITIONALLY. That is a reasonable interactive default
(the user sees the result in a dialog and fixes it), but a terrible API default:
content silently lands in an unrelated field. The default here is exact name
match only. Any old field left unmapped that holds content in at least one of
the selected notes turns a real run into an error demanding an explicit
``field_mapping`` -- so the divergence can never lose content, it can only ask.
"""

from typing import Any
import logging

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col
from ....config import get_max_notes_per_batch
from ....schema_state import FULL_SYNC_FLAG_DOC, attach_full_sync_flag

logger = logging.getLogger(__name__)

# Protobuf sentinel for "no source field" / "discard" in new_fields and
# new_templates (nullable repeated fields are unwieldy in protobuf).
_DROP = -1


@Tool(
    "change_note_type",
    "DESTRUCTIVE. Move existing notes to a different note type (model), "
    "remapping their fields. Hidden from clients unless the operator opts in "
    'via the "enabled_destructive_tools" addon config containing '
    '"change_note_type".\n\n'
    "WHAT IT DOES TO THE NOTES: each note's field layout is REWRITTEN to the "
    "target note type's fields. Content moves only where the mapping says it "
    "moves; any old field with no target is DROPPED, permanently, for every "
    "note in the batch. There is no per-field undo beyond Anki's single undo "
    "step.\n\n"
    "TWO-STEP FLOW, ALWAYS. Call with dry_run=true FIRST: a dry run writes "
    "nothing and returns the fully resolved plan -- the field mapping both "
    "directions, which fields would be dropped and how many of the selected "
    "notes actually have content there, the card-template mapping, and how many "
    "cards would be removed. Then repeat the SAME call with dry_run=false AND "
    "confirm=true. A real run without confirm=true is REFUSED; confirm is "
    "ignored during dry runs.\n\n"
    "field_mapping is {old field name: new field name or null}, e.g. "
    '{"Front": "Text", "Back": "Back Extra", "Notes": null}. null (or simply '
    "omitting an old field) drops that field's content. Two old fields may not "
    "target the same new field. The reverse -- one old field feeding TWO new "
    "fields -- is supported by Anki but NOT expressible in this mapping shape, "
    "which allows one target per source; copy the content into the second field "
    "afterwards with update_note_fields. If field_mapping is omitted, the "
    "default is an EXACT NAME MATCH between the two note types; a real run is "
    "REFUSED if that default would drop content that actually exists, so pass "
    "an explicit mapping in that case.\n\n"
    "note_ids must contain no duplicates (a repeated id would have the field "
    "remap applied to it twice, scrambling its content) and ALL of them must "
    "currently share the SAME note type -- a mixed batch is rejected, because "
    "the mapping is index-based and would scramble notes of the other type. "
    "Query them per type (find_notes with note:\"Type Name\") and call once per "
    "type.\n\n"
    "CARDS AND REVIEW HISTORY: cards are matched to the new note type's "
    "templates by Anki's own default template map (same name first, then "
    "position), and every matched card keeps its id, due date, interval, ease "
    "and full review log. Cards whose template has NO counterpart in the new "
    "note type are DELETED along with their scheduling -- the dry run reports "
    "that count as cards_to_remove. When EITHER note type is a cloze type, "
    "templates are not remapped at all: existing cards keep their ordinal and "
    "their full scheduling, and are removed only if the TARGET is a normal note "
    "type and the card's ordinal is past that type's last template. Anki's card "
    "generation then runs in every case and may ADD cards for target templates "
    "that now render; the dry run does NOT predict additions, a real run "
    "reports them as cards_added.\n\n"
    + FULL_SYNC_FLAG_DOC
    + " Changing note types always marks the schema modified (rslib's "
      "change_notetype_of_notes_inner calls set_schema_modified), so after a "
      "real run it is always true -- the next sync WILL be a full one-way sync. "
      "Sync everything before running this. A dry run writes nothing, so its "
      "will_force_full_sync is the collection's CURRENT state; the separate "
      "would_force_full_sync key carries the prediction.",
    write=True,
    destructive=True,
)
def change_note_type(
    note_ids: list[int],
    new_model_name: str,
    field_mapping: dict[str, str | None] | None = None,
    dry_run: bool = False,
    confirm: bool = False,
) -> dict[str, Any]:
    col = get_col()

    # Per-call confirmation, mirroring delete_notes' confirmDeletion: the field
    # drops this tool performs are irreversible beyond a single undo step, and
    # "the description told it to dry-run first" is not enforcement. Dry runs
    # write nothing, so they ignore it.
    if not dry_run and not confirm:
        raise HandlerError(
            "Note type change not confirmed. This permanently rewrites every "
            "selected note's fields and can delete cards with their review "
            "history.",
            hint="Run once with dry_run=true to see the full plan, then repeat "
                 "the same call with dry_run=false and confirm=true.",
            code="validation_error",
        )

    notes = _load_notes(col, note_ids)
    old_notetype_id = _single_notetype_id(col, notes)

    new_model = col.models.by_name(new_model_name)
    if new_model is None:
        raise HandlerError(
            f'Note type "{new_model_name}" not found',
            hint="Use the model_names tool to list available note types",
            code="not_found",
            new_model_name=new_model_name,
        )

    info = col.models.change_notetype_info(
        old_notetype_id=old_notetype_id,
        new_notetype_id=new_model["id"],
    )
    old_field_names = list(info.old_field_names)
    new_field_names = list(info.new_field_names)

    if field_mapping is None:
        new_fields = _default_new_fields(old_field_names, new_field_names)
    else:
        new_fields = _explicit_new_fields(
            old_field_names, new_field_names, field_mapping
        )

    dropped_indices = _unmapped_old_indices(new_fields, len(old_field_names))
    dropped_with_content = _content_counts(notes, dropped_indices, old_field_names)

    if dropped_with_content and field_mapping is None and not dry_run:
        raise HandlerError(
            "Refusing to change note type: the default name-match mapping would "
            "permanently drop content from "
            f"{len(dropped_with_content)} field(s) that are not empty",
            hint="Pass an explicit field_mapping deciding where each of these "
                 "fields goes, using null for the ones whose content you really "
                 "want to discard. Valid targets: "
                 f"{', '.join(new_field_names)}. Run with dry_run=true to see "
                 "the full plan first.",
            code="validation_error",
            fields_that_would_be_dropped=dropped_with_content,
            new_model_name=new_model_name,
        )

    template_plan = _template_plan(info)
    card_ords = _card_ords_by_note(notes)
    cards_before = sum(len(ords) for ords in card_ords.values())
    cards_to_remove = _count_removed_cards(card_ords, info, new_model)

    plan: dict[str, Any] = {
        "note_count": len(notes),
        "old_model": info.old_notetype_name,
        "new_model": new_model["name"],
        "field_mapping": _old_to_new_view(
            new_fields, old_field_names, new_field_names
        ),
        "new_field_sources": _new_to_old_view(
            new_fields, old_field_names, new_field_names
        ),
        "dropped_fields": [old_field_names[i] for i in dropped_indices],
        "dropped_fields_with_content": dropped_with_content,
        "templates": template_plan,
    }

    if dry_run:
        return _dry_run_result(plan, cards_before, cards_to_remove, col)

    logger.info(
        "change_note_type: moving %d note(s) from %r to %r (new_fields=%s)",
        len(notes), info.old_notetype_name, new_model["name"], new_fields,
    )
    _apply(col, info, [n.id for n in notes], new_fields)

    changed, cards_after = _verify(col, [n.id for n in notes], new_model["id"])

    result: dict[str, Any] = {
        "dry_run": False,
        **plan,
        "notes_changed": changed,
        "cards_before": cards_before,
        "cards_after": cards_after,
        # Counted, not predicted. Both directions are possible: unmapped
        # templates lose cards, while a target note type whose extra templates
        # now render can gain them (card generation runs as part of the change).
        "cards_removed": max(0, cards_before - cards_after),
        "cards_added": max(0, cards_after - cards_before),
        "message": (
            f"Moved {changed} note(s) from \"{info.old_notetype_name}\" to "
            f"\"{new_model['name']}\""
        ),
        "hint": "Field content that was dropped is gone; Anki's undo (Ctrl+Z in "
                "the Anki window) is the only recovery and only for the most "
                "recent operation. Sync now -- the next sync is a full one-way "
                "sync.",
    }
    # Warn on fields that actually held content -- an unmapped field that was
    # empty in every note lost nothing and does not deserve an alarm.
    if dropped_with_content:
        result["warning"] = (
            "Permanently dropped content from field(s): "
            + ", ".join(dropped_with_content)
        )
    return attach_full_sync_flag(result, col)


# ---------------------------------------------------------------------------
# Note selection and validation
# ---------------------------------------------------------------------------

def _load_notes(col: Any, note_ids: list[int]) -> list[Any]:
    """Fetch every requested note, or raise with the ids that do not exist.

    Fails on ANY missing id rather than skipping it: unlike delete_notes, where
    an already-deleted note is a no-op, a partially applied note-type change is
    a silent half-migration the caller cannot detect from a count.

    Duplicates are rejected for a harder reason -- see ``_reject_duplicates``.
    """
    from anki.errors import NotFoundError

    if not note_ids:
        raise HandlerError(
            "No note IDs provided",
            hint="Pass the note ids to move, e.g. from find_notes",
            code="validation_error",
        )

    max_notes = get_max_notes_per_batch()
    if len(note_ids) > max_notes:
        raise HandlerError(
            f"Too many notes: {len(note_ids)} (maximum is {max_notes})",
            hint=f"Split your request into batches of {max_notes} or fewer. "
                 f"You can increase the limit via the 'max_notes_per_batch' "
                 f"addon config option.",
            code="limit_exceeded",
            requested=len(note_ids),
            maximum=max_notes,
        )

    _reject_duplicates(note_ids)

    notes = []
    missing = []
    for note_id in note_ids:
        try:
            notes.append(col.get_note(note_id))
        # Modern Anki raises anki.errors.NotFoundError for missing notes;
        # KeyError is kept for backward compatibility with older versions.
        except (NotFoundError, KeyError):
            missing.append(note_id)

    if missing:
        raise HandlerError(
            f"{len(missing)} note ID(s) do not exist",
            hint="Nothing was changed. Re-run find_notes to get current ids, "
                 "then retry with only existing notes.",
            code="not_found",
            missing_note_ids=missing,
        )
    return notes


def _reject_duplicates(note_ids: list[int]) -> None:
    """Refuse a note id list containing the same id more than once.

    ``update_notes_for_new_notetype_and_generate_cards`` (notetypechange.rs)
    loops over ``note_ids`` and RE-READS each note from storage inside the loop,
    then applies ``remap_fields``. A repeated id therefore has the index map
    applied to its ALREADY-CONVERTED fields a second time: the indices now point
    at the new layout, so content is permuted or blanked -- silent corruption of
    exactly the note the caller named twice. The verification counts would be
    inflated too, since ``_verify`` walks the same list.

    Rejected rather than silently de-duplicated: this tool never guesses on the
    caller's behalf, and Anki's own UI path (``get_single_notetype_of_notes``)
    likewise errors instead of massaging the selection.
    """
    seen: set[int] = set()
    duplicated: list[int] = []
    for note_id in note_ids:
        if note_id in seen and note_id not in duplicated:
            duplicated.append(note_id)
        seen.add(note_id)

    if duplicated:
        raise HandlerError(
            f"note_ids contains {len(duplicated)} duplicated note id(s)",
            hint="Nothing was changed. A repeated id would have the field remap "
                 "applied to it twice, scrambling that note's content. "
                 "De-duplicate note_ids and call again.",
            code="validation_error",
            duplicated_note_ids=duplicated,
        )


def _single_notetype_id(col: Any, notes: list[Any]) -> int:
    """Return the note type shared by all notes, or raise listing the mix.

    See the module docstring: the backend does NOT check this and would
    reinterpret each note's fields by the wrong indices.
    """
    by_notetype: dict[int, list[int]] = {}
    for note in notes:
        by_notetype.setdefault(note.mid, []).append(note.id)

    if len(by_notetype) == 1:
        return next(iter(by_notetype))

    found = {
        _notetype_name(col, mid): {"note_count": len(ids), "example_note_ids": ids[:5]}
        for mid, ids in by_notetype.items()
    }
    raise HandlerError(
        f"The selected notes use {len(by_notetype)} different note types",
        hint="Field mapping is index-based against ONE source note type, so a "
             "mixed batch would scramble content. Nothing was changed. Split "
             "the batch by note type (find_notes with 'note:\"Type Name\"') and "
             "call change_note_type once per type.",
        code="validation_error",
        note_types_found=found,
    )


def _notetype_name(col: Any, notetype_id: int) -> str:
    """Best-effort note type name for error messages."""
    try:
        model = col.models.get(notetype_id)
    except Exception:
        model = None
    if model is None:
        return f"<unknown note type {notetype_id}>"
    return model.get("name", f"<unnamed note type {notetype_id}>")


# ---------------------------------------------------------------------------
# Field mapping: names in, new-centric index vector out
# ---------------------------------------------------------------------------

def _default_new_fields(
    old_field_names: list[str], new_field_names: list[str]
) -> list[int]:
    """Exact-name-match map. See the module docstring for why not Anki's."""
    old_index = {name: idx for idx, name in enumerate(old_field_names)}
    return [old_index.get(name, _DROP) for name in new_field_names]


def _explicit_new_fields(
    old_field_names: list[str],
    new_field_names: list[str],
    field_mapping: dict[str, str | None],
) -> list[int]:
    """Translate {old name: new name | null} into the new-centric index vector.

    An explicit mapping is AUTHORITATIVE: an old field the caller did not
    mention is dropped, exactly as if it had been mapped to null. That is the
    only self-consistent reading -- falling back to name matching for unmentioned
    fields would make the result depend on names the caller never wrote down.
    """
    old_index = {name: idx for idx, name in enumerate(old_field_names)}
    new_index = {name: idx for idx, name in enumerate(new_field_names)}

    unknown_old = [name for name in field_mapping if name not in old_index]
    unknown_new = [
        target for target in field_mapping.values()
        if target is not None and target not in new_index
    ]
    if unknown_old or unknown_new:
        raise HandlerError(
            "field_mapping refers to fields that do not exist",
            hint="Keys must be fields of the CURRENT note type and values must "
                 "be fields of the TARGET note type (or null to drop). "
                 f"Current: {', '.join(old_field_names)}. "
                 f"Target: {', '.join(new_field_names)}.",
            code="validation_error",
            unknown_source_fields=unknown_old,
            unknown_target_fields=unknown_new,
            valid_source_fields=old_field_names,
            valid_target_fields=new_field_names,
        )

    new_fields = [_DROP] * len(new_field_names)
    claimed_by: dict[str, str] = {}
    for old_name, target in field_mapping.items():
        if target is None:
            continue
        if target in claimed_by:
            raise HandlerError(
                f'Two fields both map to "{target}"',
                hint="Each target field can receive content from at most one "
                     "source field -- Anki stores one source index per target "
                     "field. Map one of them elsewhere or to null.",
                code="validation_error",
                target_field=target,
                source_fields=[claimed_by[target], old_name],
            )
        claimed_by[target] = old_name
        new_fields[new_index[target]] = old_index[old_name]
    return new_fields


def _unmapped_old_indices(new_fields: list[int], old_field_count: int) -> list[int]:
    """Old field indices whose content no new field receives."""
    kept = {idx for idx in new_fields if idx != _DROP}
    return [idx for idx in range(old_field_count) if idx not in kept]


def _content_counts(
    notes: list[Any], indices: list[int], old_field_names: list[str]
) -> dict[str, int]:
    """Map dropped field name -> how many of these notes have content there.

    Fields that are empty in every selected note are omitted: dropping them
    loses nothing, so they must not trigger the real-run refusal.
    """
    counts: dict[str, int] = {}
    for idx in indices:
        count = sum(
            1 for note in notes
            if idx < len(note.fields) and note.fields[idx].strip()
        )
        if count:
            counts[old_field_names[idx]] = count
    return counts


def _old_to_new_view(
    new_fields: list[int], old_field_names: list[str], new_field_names: list[str]
) -> dict[str, str | None]:
    """Resolved mapping as {old name: new name | null} -- the input's own shape.

    Echoed so a caller can lift it straight out of a dry run into the real call.
    """
    view: dict[str, str | None] = {name: None for name in old_field_names}
    for new_idx, old_idx in enumerate(new_fields):
        if old_idx != _DROP:
            view[old_field_names[old_idx]] = new_field_names[new_idx]
    return view


def _new_to_old_view(
    new_fields: list[int], old_field_names: list[str], new_field_names: list[str]
) -> dict[str, str | None]:
    """Resolved mapping as {new name: source old name | null} -- Anki's own shape."""
    return {
        new_field_names[new_idx]: (
            None if old_idx == _DROP else old_field_names[old_idx]
        )
        for new_idx, old_idx in enumerate(new_fields)
    }


# ---------------------------------------------------------------------------
# Templates and cards
# ---------------------------------------------------------------------------

def _template_plan(info: Any) -> dict[str, Any]:
    """Describe Anki's default template map, or explain why there isn't one.

    ``remapped`` is False when either note type is a cloze type: rslib's
    ``default_template_map`` returns None there ("clozes can't be remapped"),
    which reaches the wire as an EMPTY new_templates list. The plan still names
    both template sets so the caller can see what it is walking into.

    "Not remapped" is NOT "cards thrown away". rslib takes the
    ``maybe_remove_cards_with_missing_template`` branch instead, which leaves
    every existing card -- ordinal, due date, interval and review log intact --
    and deletes only the ones sitting past the target's last template, and only
    when the target is a NORMAL note type. Card generation then fills in cards
    that are missing for the new type.
    """
    new_templates = list(info.input.new_templates)
    old_names = list(info.old_template_names)
    new_names = list(info.new_template_names)

    if not new_templates:
        return {
            "remapped": False,
            "reason": "a cloze note type is involved, so card templates are not "
                      "remapped; existing cards keep their ordinal and their "
                      "full scheduling, and are removed only if the target is a "
                      "normal note type and the ordinal is past its last "
                      "template. Card generation adds any missing cards.",
            "old_templates": old_names,
            "new_templates": new_names,
        }

    kept = {idx for idx in new_templates if idx != _DROP}
    return {
        "remapped": True,
        "mapping": {
            new_names[new_idx]: (None if old_idx == _DROP else old_names[old_idx])
            for new_idx, old_idx in enumerate(new_templates)
            if new_idx < len(new_names)
        },
        "removed_templates": [
            name for idx, name in enumerate(old_names) if idx not in kept
        ],
    }


def _card_ords_by_note(notes: list[Any]) -> dict[int, list[int]]:
    """Template ordinal of every card of every selected note."""
    return {note.id: [card.ord for card in note.cards()] for note in notes}


def _count_removed_cards(
    card_ords: dict[int, list[int]], info: Any, new_model: dict
) -> int:
    """Predict how many cards the change deletes, mirroring rslib exactly.

    Two rslib paths (notetypechange.rs):
      * templates remapped -> ``remove_unmapped_cards`` deletes cards whose old
        ordinal appears nowhere in new_templates;
      * no template map (cloze on either side) ->
        ``maybe_remove_cards_with_missing_template`` deletes cards above the new
        note type's last template ordinal, and ONLY when the target is a normal
        note type.

    The remapped path is bounded by the OLD template count. ``TemplateMap::new``
    builds its removed set as ``(0..old_template_count).filter(not seen)``, so an
    ORPHAN card -- one whose ordinal is past the old note type's last template,
    which a damaged collection can hold -- is never in that set and is not
    deleted. Counting it here would over-predict the loss.
    """
    from anki.consts import MODEL_CLOZE

    new_templates = list(info.input.new_templates)

    if new_templates:
        kept = {idx for idx in new_templates if idx != _DROP}
        old_template_count = len(info.old_template_names)
        return sum(
            1
            for ords in card_ords.values()
            for ord_ in ords
            if ord_ < old_template_count and ord_ not in kept
        )

    if new_model.get("type") == MODEL_CLOZE:
        return 0

    last_ord = len(new_model.get("tmpls", [])) - 1
    return sum(
        1 for ords in card_ords.values() for ord_ in ords if ord_ > last_ord
    )


# ---------------------------------------------------------------------------
# Write and verify
# ---------------------------------------------------------------------------

def _apply(col: Any, info: Any, note_ids: list[int], new_fields: list[int]) -> None:
    """Fill in the request returned by change_notetype_info and run it.

    Only note_ids and new_fields are touched -- ids, names, current_schema and
    the template map stay exactly as Anki computed them (module docstring).
    """
    request = info.input
    request.note_ids.extend(note_ids)
    del request.new_fields[:]
    request.new_fields.extend(new_fields)

    try:
        col.models.change_notetype_of_notes(request)
    except HandlerError:
        raise
    except Exception as exc:
        logger.error("change_notetype_of_notes failed", exc_info=True)
        raise HandlerError(
            f"Anki refused the note type change: {exc}",
            hint="Nothing was changed. If this says the schema changed, the "
                 "collection was modified between planning and applying -- "
                 "re-run the dry run and try again.",
            code="operation_failed",
        ) from exc


def _verify(col: Any, note_ids: list[int], new_notetype_id: int) -> tuple[int, int]:
    """Re-read the notes after the write: (notes now on the new type, cards).

    Counted rather than assumed so the result reports what the collection
    actually holds, including cards the template remap removed.
    """
    from anki.errors import NotFoundError

    changed = 0
    cards = 0
    for note_id in note_ids:
        try:
            note = col.get_note(note_id)
        except (NotFoundError, KeyError):
            continue
        if note.mid == new_notetype_id:
            changed += 1
        cards += len(note.cards())
    return changed, cards


def _dry_run_result(
    plan: dict[str, Any], cards_before: int, cards_to_remove: int, col: Any
) -> dict[str, Any]:
    """Shape the plan for a dry run: nothing was written, nothing was read back."""
    result: dict[str, Any] = {
        "dry_run": True,
        **plan,
        "cards_now": cards_before,
        "cards_to_remove": cards_to_remove,
        # Card generation runs as part of the real change and can ADD cards for
        # target templates that now render. That depends on the rendered output
        # of the new type's templates against the remapped fields, which is not
        # modelled here -- so it is declared unpredicted rather than guessed at.
        "cards_to_add": "not predicted -- Anki's card generation may add cards "
                        "for the target note type's templates; a real run "
                        "reports the actual number as cards_added",
        # RESULT_KEY keeps its contract: the ACTUAL current collection state.
        # A dry run writes nothing, so hardcoding True here would have reported
        # a state the collection is not in. The prediction lives in its own key.
        "would_force_full_sync": True,
        "message": (
            f"Dry run: would move {plan['note_count']} note(s) from "
            f"\"{plan['old_model']}\" to \"{plan['new_model']}\". "
            "Nothing was changed."
        ),
        "hint": "Review field_mapping and dropped_fields_with_content, then "
                "re-run with dry_run=false AND confirm=true (passing an explicit "
                "field_mapping if any non-empty field would be dropped).",
    }
    if plan["dropped_fields_with_content"]:
        result["warning"] = (
            "This would permanently drop content from: "
            + ", ".join(plan["dropped_fields_with_content"])
        )
    if cards_to_remove:
        result["cards_warning"] = (
            f"{cards_to_remove} card(s) would be DELETED with their review "
            "history because their card template has no counterpart in the "
            "target note type"
        )
    return attach_full_sync_flag(result, col)
