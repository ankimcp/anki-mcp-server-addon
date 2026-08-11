# schema_state.py
"""Pending schema-change state: will the collection's next sync be a full sync?

Single responsibility: read Anki's collection-wide "schema modified since last
sync" flag and shape it for tool results. Nothing here mutates anything.

Why this is collection state, not per-tool knowledge
----------------------------------------------------
Anki tracks the pending full-sync requirement ONCE for the whole collection, as
``scm > ls`` on the ``col`` row (schema-modified time vs. last-sync time). Any
operation that calls ``set_schema_modified`` bumps ``scm``; only a completed
full sync moves ``ls`` back past it. Consequences worth keeping in mind:

- The flag is STICKY. Once anything marks the schema modified, it stays true for
  every subsequent read until a full sync clears it. A tool that changed nothing
  schema-wise will still report ``True`` if an earlier operation dirtied the
  collection.
- Therefore a tool must never hardcode its own answer. Adding a NEW notetype,
  for instance, does not mark the schema modified (rslib's ``add_notetype_inner``
  never calls ``set_schema_modified``), but ``create_model`` still cannot claim
  ``False`` -- the collection may already be dirty from something else.

So every caller reads the ACTUAL post-write state through this module.

pylib surface used
------------------
``Collection.schema_changed()`` -- pylib/anki/collection.py:312::

    def schema_changed(self) -> bool:
        "True if schema changed since last sync."
        return self.db.scalar("select scm > ls from col")

This is the cheapest local check: a single scalar read off the already-open
collection, no backend sync-status round trip (``rslib``'s
``schema_changed_since_sync`` computes the same comparison server-side as part
of the heavier sync-status request). Note the docstring says ``bool`` but
``db.scalar`` hands back SQLite's integer ``1``/``0``, so the value is coerced
below -- callers get a real ``bool``, which is what the JSON result contract
promises.
"""

from typing import Any
import logging

logger = logging.getLogger(__name__)

# Result key carried by every model-mutating tool's success payload.
RESULT_KEY = "will_force_full_sync"

# Shared description text for tools whose results carry RESULT_KEY.
#
# Descriptions are the API contract, so the stickiness caveat is stated once
# here and appended by each tool rather than paraphrased in four places (which
# would drift). It must keep saying that the flag describes the NEXT sync, not
# THIS call.
FULL_SYNC_FLAG_DOC = (
    'The result carries "will_force_full_sync": collection state read AFTER the '
    "write, reporting whether the NEXT sync will be a one-way full sync that "
    "overwrites the collection on AnkiWeb and other devices with this one. It "
    "does NOT mean this call caused it -- the flag is collection-wide and "
    "sticky, so once any earlier operation marked the schema modified it stays "
    "true until a full sync clears it."
)


def will_force_full_sync(col: Any) -> bool:
    """Report whether the collection's next sync will be a one-way full sync.

    Args:
        col: The open Anki collection (from ``handler_wrappers.get_col``).

    Returns:
        True if the schema has been modified since the last sync.

    Note:
        Failure is fail-SAFE rather than fail-silent-false. The read happens
        after a write that already succeeded, so raising would discard a
        completed result; but answering ``False`` when the truth is ``True``
        invites the user to sync a device that then gets overwritten. Answering
        ``True`` when the truth is ``False`` only costs an unnecessary
        precautionary sync, so an unexpected failure resolves to ``True``.
    """
    try:
        return bool(col.schema_changed())
    except Exception:
        logger.warning(
            "Could not read collection schema state; reporting %s conservatively as True",
            RESULT_KEY,
            exc_info=True,
        )
        return True


def attach_full_sync_flag(result: dict[str, Any], col: Any) -> dict[str, Any]:
    """Add ``will_force_full_sync`` to a tool's success payload, in place.

    Call this at ONE site per tool -- ideally the dispatch point -- so multi-path
    tools cannot grow a success path that silently omits the key.

    Args:
        result: The success payload to annotate.
        col: The open Anki collection.

    Returns:
        The same dict, for use as a tail call (``return attach_full_sync_flag(...)``).
    """
    result[RESULT_KEY] = will_force_full_sync(col)
    return result
