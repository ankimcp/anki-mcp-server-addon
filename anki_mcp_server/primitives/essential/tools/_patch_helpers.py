"""Shared helpers for the ``old_str``/``new_str`` patch mode on write tools.

Three write tools (``update_model_styling``, ``update_model_templates``,
``update_note_fields``) accept an alternative "patch" input mode that mirrors a
text editor's replace: the caller supplies ``old_str`` + ``new_str`` and the
substring is replaced in place. This module centralizes the two rules that make
that contract safe and identical across all three:

1. **Exactly-once matching.** ``old_str`` must occur EXACTLY ONCE in the current
   content. Zero matches means the caller's view of the content is stale (or the
   whitespace/HTML does not match byte-for-byte); more than one match means the
   edit is ambiguous. Either way nothing is written -- the caller gets a
   ``HandlerError`` and the content on disk is untouched.

2. **Re-sync without an extra read.** The zero-match error carries a short
   excerpt of the CURRENT content, anchored near the closest cheap partial match
   when one exists, so the client can correct ``old_str`` and retry without
   issuing a separate read call.

Mode selection (full-content params vs. patch params are mutually exclusive)
lives here too, so every tool rejects mixed/absent input with the same wording.
"""
from typing import Any

from ....handler_wrappers import HandlerError

# How much of the current content to echo back on a zero-match failure, and how
# far before the anchor point the excerpt starts. Small on purpose: the excerpt
# exists to let the caller re-sync, not to substitute for a real read.
_EXCERPT_LEN = 200
_EXCERPT_LEAD = 60

# Appended to the description of every patch-capable tool.
#
# FastMCP pre-parses any string argument whose annotation is not exactly ``str``
# (vendor/shared/mcp/server/fastmcp/utilities/func_metadata.py, pre_parse_json).
# Our patch params are ``str | None``, so a value that is itself valid JSON is
# json.loads'd before validation: ``"null"`` arrives as None (indistinguishable
# from an omitted argument) and a JSON array/object arrives as a list/dict
# (which then fails validation with an opaque message). Values parsing to
# str/int/float/bool are explicitly skipped by that code, so only those two
# shapes are affected. The vendored SDK is not ours to patch, so we warn the
# caller instead and give them a workaround that always works.
JSON_LITERAL_CAVEAT = (
    "TRANSPORT CAVEAT: if 'old_str' or 'new_str' would consist ENTIRELY of a "
    "JSON value -- exactly `null`, or a complete JSON array/object such as "
    '`["x"]` or `{"a": 1}` -- it can be mis-read in transit and the call '
    "rejected. Include an adjacent character of surrounding context in the "
    "string to avoid it."
)

# Prefix lengths tried (longest first) when looking for a cheap anchor for the
# excerpt. A plain ``str.find`` on a shrinking prefix of ``old_str`` is O(n) and
# good enough to locate "almost matched here" without pulling in difflib.
_ANCHOR_PROBE_SIZES = (60, 30, 15)


def _find_anchor(content: str, old_str: str) -> int:
    """Return an index in ``content`` near where ``old_str`` almost matched.

    Tries successively shorter prefixes of ``old_str``; the first one found in
    ``content`` wins. Returns ``-1`` when no prefix matches, in which case the
    caller falls back to excerpting the head of the content.
    """
    probes = [size for size in _ANCHOR_PROBE_SIZES if size <= len(old_str)]
    if not probes:
        probes = [len(old_str)]
    for size in probes:
        idx = content.find(old_str[:size])
        if idx != -1:
            return idx
    return -1


def build_excerpt(content: str, old_str: str) -> str:
    """Build a short, re-sync-friendly excerpt of ``content``.

    The window is centred just after the closest cheap partial match of
    ``old_str`` when one exists, otherwise it is the head of the content.
    Truncation on either side is marked with an ellipsis so the caller never
    mistakes the excerpt for the whole content.
    """
    if not content:
        return ""

    anchor = _find_anchor(content, old_str)
    start = 0 if anchor < 0 else max(0, anchor - _EXCERPT_LEAD)
    end = min(len(content), start + _EXCERPT_LEN)

    excerpt = content[start:end]
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(content):
        excerpt = excerpt + "..."
    return excerpt


def apply_patch(
    content: str,
    old_str: str,
    new_str: str,
    *,
    target: str,
    read_hint: str,
    **context: Any,
) -> str:
    """Replace the single occurrence of ``old_str`` in ``content`` with ``new_str``.

    Args:
        content: Current content to patch.
        old_str: Substring that must occur exactly once in ``content``.
        new_str: Replacement text (may be empty, which deletes ``old_str``).
        target: Human-readable name of what is being patched, used in error
            messages (e.g. ``'CSS of model "Basic"'``).
        read_hint: Name of the read tool the caller should use to re-sync
            (e.g. ``"model_styling"``).
        **context: Extra structured context attached to raised errors.

    Returns:
        The patched content.

    Raises:
        HandlerError: If ``old_str`` matches zero times or more than once.
            Nothing is written in either case -- this function is pure.

    Note:
        "Exactly once" is measured with ``str.count``, which counts
        NON-OVERLAPPING occurrences, and the replacement uses the same scan
        order (left to right). So a self-overlapping ``old_str`` can be reported
        as unique when a human would call it ambiguous: ``"aaa".count("aa")``
        is 1, and the patch rewrites the LEFTMOST occurrence. This is the same
        semantics every text editor's "replace first" gives, and it is
        deliberate -- tightening it to overlapping matches would reject edits
        that are in fact well-defined. Callers wanting certainty should extend
        ``old_str`` with surrounding context, exactly as the multi-match error
        already advises.
    """
    if not old_str:
        raise HandlerError(
            "old_str must not be empty",
            hint="Provide the exact text to replace. To set the full content "
                 "instead, use this tool's full-content parameter.",
            code="validation_error",
            **context,
        )

    count = content.count(old_str)

    if count == 0:
        raise HandlerError(
            f"old_str not found in {target} (0 matches) -- nothing was written",
            hint=(
                f"old_str must match the current content EXACTLY (whitespace, "
                f"newlines and HTML markup are all significant). Re-sync using "
                f"the excerpt below, or read the full current content with "
                f"{read_hint}, then retry."
            ),
            code="patch_no_match",
            content_length=len(content),
            current_content_excerpt=build_excerpt(content, old_str),
            **context,
        )

    if count > 1:
        raise HandlerError(
            f"old_str matches {count} times in {target} -- ambiguous, "
            f"nothing was written",
            hint="Extend old_str with surrounding context until it identifies "
                 "exactly one location, then retry.",
            code="patch_multiple_matches",
            match_count=count,
            content_length=len(content),
            **context,
        )

    return content.replace(old_str, new_str, 1)


def require_patch_pair(
    old_str: str | None,
    new_str: str | None,
    **context: Any,
) -> None:
    """Reject a half-specified patch (one of ``old_str``/``new_str`` missing).

    ``new_str`` may legitimately be an empty string (a deletion), so presence is
    tested against ``None``, never truthiness.

    A caller that DID send both can still land here: a value of exactly ``null``
    is JSON-decoded to None in transit (see ``JSON_LITERAL_CAVEAT``), so the
    hint names that possibility rather than only blaming a missing argument.
    """
    if (old_str is None) != (new_str is None):
        missing = "new_str" if new_str is None else "old_str"
        raise HandlerError(
            f"Patch mode requires both old_str and new_str -- {missing} is missing",
            hint="Pass old_str (text to find) and new_str (replacement). "
                 "new_str may be an empty string to delete the matched text. "
                 f"If you DID send {missing}, check whether its value was "
                 "exactly `null` -- such a value is decoded to a missing "
                 "argument in transit; add an adjacent context character to it "
                 "and retry.",
            code="validation_error",
            **context,
        )


def reject_empty_full_input(
    payload: dict,
    *,
    full_params: str,
    what: str,
    **context: Any,
) -> None:
    """Reject a full-mode call whose mapping was supplied but empty.

    Mode selection is driven by PRESENCE (``is not None``), never truthiness, so
    that ``{}`` selects full mode rather than silently falling through to "no
    input provided" (which would be a confusing diagnosis for a caller who did
    send the parameter). The cost of that choice is that full mode must then
    reject the empty mapping itself -- which is what this does, with an error
    that names the actual mistake.

    Args:
        payload: The full-mode mapping as supplied by the caller.
        full_params: Human-readable name of the full-content parameter.
        what: What at least one entry would describe (e.g. ``"field"``).
        **context: Extra structured context attached to raised errors.

    Raises:
        HandlerError: If ``payload`` is empty.
    """
    if not payload:
        raise HandlerError(
            f"{full_params} was provided but is empty -- nothing to update",
            hint=f"Include at least one {what} in {full_params}, or use this "
                 f"tool's patch parameters instead.",
            code="validation_error",
            **context,
        )


def select_mode(
    *,
    full_present: bool,
    patch_present: bool,
    full_params: str,
    patch_params: str,
    allow_neither: bool = False,
    **context: Any,
) -> str:
    """Pick exactly one input mode, rejecting mixed (and usually empty) input.

    Args:
        full_present: Whether the full-content parameter(s) were supplied.
        patch_present: Whether the patch parameter(s) were supplied.
        full_params: Human-readable name of the full-content parameter(s).
        patch_params: Human-readable name of the patch parameter(s).
        allow_neither: When True, supplying neither is legal and ``"none"`` is
            returned (used by ``update_model_styling``, where the LaTeX-only
            call touches no content at all).
        **context: Extra structured context attached to raised errors.

    Returns:
        ``"full"``, ``"patch"`` or ``"none"``.

    Raises:
        HandlerError: If both modes or (unless allowed) neither is supplied.
    """
    if full_present and patch_present:
        raise HandlerError(
            f"Cannot combine {full_params} with {patch_params} -- "
            f"these are mutually exclusive input modes",
            hint=f"Use {full_params} to replace the content wholesale, OR "
                 f"{patch_params} to patch it in place. Not both.",
            code="validation_error",
            **context,
        )
    if full_present:
        return "full"
    if patch_present:
        return "patch"
    if allow_neither:
        return "none"
    raise HandlerError(
        f"No input provided -- supply either {full_params} or {patch_params}",
        hint=f"Use {full_params} to replace the content wholesale, OR "
             f"{patch_params} to patch it in place.",
        code="validation_error",
        **context,
    )
