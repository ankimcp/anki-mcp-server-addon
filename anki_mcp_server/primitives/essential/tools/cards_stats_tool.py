"""cards_stats tool - compact, FSRS-independent per-card scheduling stats for a deck.

Returns a flat list of scheduling metrics (type / queue / interval / tags / dueToday)
for every card in a deck (subdecks included), paginated. Designed for bulk analytics
consumers (e.g. a "knowledge map" service) that need per-card metrics without dragging
full note payloads and without requiring FSRS to be enabled.

Payload-size constraint
-----------------------
Each reply crosses NATS with a hard 1 MiB cap in production, and FastMCP dual-encodes
every result (text content + structuredContent) ~= 2x the JSON, so each JSON copy must
stay under ~512 KiB. Base per-card JSON is ~100 bytes; with a generous tags budget that
climbs to ~400-500 bytes/card. At limit=1000 that is <= ~500 KB per copy -- comfortably
safe even for tag-heavy notes. limit=2000 would risk breaching the cap for pathological
tag-heavy decks, so _MAX_LIMIT is capped at 1000 (see below).
"""
from typing import Any

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col

# Cap the page size at 1000 (see the "Payload-size constraint" note above): each reply
# is dual-encoded (~2x) under a 1 MiB NATS cap, so each copy must stay < ~512 KiB.
# At ~400-500 bytes/card, 1000 cards is a safe <= ~500 KB; 2000 would risk breaching it.
_MAX_LIMIT = 1000


def _split_tags(tags: str) -> list[str]:
    """Convert the raw ``notes.tags`` DB column into a list of tags.

    The column is a space-delimited string carrying sentinel spaces
    (e.g. ``" verbs grammar "``). Anki tags cannot contain spaces, so a plain
    whitespace split cleanly drops the sentinels. Empty / whitespace-only -> [].
    """
    return tags.split()


def _is_due_today(queue: int, due: int, sched_today: int, day_cutoff: int) -> bool:
    """Return whether a card is due today.

    Two clocks are in play, and the correct one depends on the queue:

    - Review (2) and day/inter-day learning-relearn (3) are scheduled by DAY NUMBER,
      so we compare ``due`` against ``col.sched.today`` (an integer day count).
    - Intraday learning (1) is scheduled by a UNIX TIMESTAMP, so we compare ``due``
      against ``col.sched.day_cutoff`` (the unix ts when today's Anki day ends).
      A learning step 10 minutes out is still "today"; one past the cutoff rolls to
      tomorrow.

    Everything else -- new (0), suspended (-1), buried (-2/-3), preview (4) -- is not
    due today.
    """
    from anki.consts import (
        QUEUE_TYPE_LRN,                # 1 : intraday learning; due is a UNIX TIMESTAMP
        QUEUE_TYPE_REV,                # 2 : review;            due is a DAY NUMBER
        QUEUE_TYPE_DAY_LEARN_RELEARN,  # 3 : day learning/relearn; due is a DAY NUMBER
    )

    # Review and day-learning are scheduled by day number.
    if queue in (QUEUE_TYPE_REV, QUEUE_TYPE_DAY_LEARN_RELEARN):
        return due <= sched_today
    # Intraday learning is scheduled by a unix timestamp; it's "due today"
    # if it comes up before today's rollover.
    if queue == QUEUE_TYPE_LRN:
        return due <= day_cutoff
    # New (0), suspended (-1), buried (-2/-3), preview (4): not due today.
    return False


@Tool(
    "cards_stats",
    "Bulk per-card scheduling stats for a deck (subdecks included), FSRS-independent. "
    "For every matching card returns a compact record: cid, nid, the note's tags, and "
    "raw Anki ints for type (0 new, 1 learning, 2 review, 3 relearning) and queue "
    "(-3/-2 buried, -1 suspended, 0 new, 1 lrn, 2 rev, 3 day-lrn), plus interval (days) "
    "and a computed dueToday flag. No note fields, no HTML, no human-readable names -- "
    "just the scheduling metrics, for bulk analytics. Paginated with limit (default "
    "1000, max 1000) and offset; cards are ordered by card id for stable paging. "
    "Prefer this over find_notes + notes_info + get_card_memory_state when you only need "
    "scheduling metrics: it is one compact read and does not require FSRS.",
)
def cards_stats(deck: str, limit: int = 1000, offset: int = 0) -> dict[str, Any]:
    """Return compact per-card scheduling stats for a deck (subdecks included).

    Args:
        deck: Deck name. Matches Anki ``deck:"..."`` semantics, including subdecks.
        limit: Maximum number of cards to return (default 1000, max 1000).
        offset: Number of cards to skip for pagination (default 0).

    Returns:
        Dictionary with the page of card stats and pagination metadata.
    """
    if limit <= 0:
        raise HandlerError(
            "limit must be positive",
            hint="Use a value >= 1",
            code="validation_error",
            provided_value=limit,
        )
    if limit > _MAX_LIMIT:
        raise HandlerError(
            f"limit exceeds maximum of {_MAX_LIMIT} (requested: {limit})",
            hint=f"Use a limit <= {_MAX_LIMIT} and paginate with offset",
            code="limit_exceeded",
            provided_value=limit,
            maximum=_MAX_LIMIT,
        )
    if offset < 0:
        raise HandlerError(
            "offset cannot be negative",
            hint="Use a value >= 0",
            code="validation_error",
            provided_value=offset,
        )

    from anki.collection import SearchNode

    col = get_col()

    # Deck-not-found convention (matches get_due_cards). Anki auto-creates parent
    # decks, so a real roadmap root always resolves via by_name.
    if col.decks.by_name(deck) is None:
        raise HandlerError(
            f"Deck '{deck}' not found",
            hint="Check spelling or use list_decks to see available decks",
            deck_name=deck,
        )

    # Build the search with the escaping-safe API (never string concatenation).
    # SearchNode(deck=...) matches the deck and all its subdecks.
    query = col.build_search_string(SearchNode(deck=deck))

    # Sort by card id for a stable ordering across paged calls within a sync.
    all_cids = sorted(col.find_cards(query))
    total = len(all_cids)

    if total == 0:
        # Deck exists but has no matching cards -- a valid empty state, not an error.
        return {
            "deck": deck,
            "total": 0,
            "offset": offset,
            "count": 0,
            "hasMore": False,
            "cards": [],
        }

    page_cids = all_cids[offset : offset + limit]

    cards: list[dict[str, Any]] = []
    if page_cids:
        sched_today = col.sched.today       # int day-count for "today"
        day_cutoff = col.sched.day_cutoff   # unix ts when today's Anki day ends

        # ONE SQL query joining cards -> notes for the whole page. col.db is safe on
        # the main thread and blessed for analytics. Row order from an IN clause is not
        # guaranteed, so index rows by cid and emit in page_cids order for determinism.
        placeholders = ",".join("?" for _ in page_cids)
        rows = col.db.all(
            f"""
            SELECT c.id, c.nid, c.type, c.queue, c.ivl, c.due, n.tags
            FROM cards c JOIN notes n ON c.nid = n.id
            WHERE c.id IN ({placeholders})
            """,
            *page_cids,
        )
        by_cid = {row[0]: row for row in rows}

        for cid in page_cids:
            row = by_cid.get(cid)
            if row is None:
                # Card vanished between find_cards and the join (rare race); skip it.
                continue
            _cid, nid, ctype, queue, ivl, due, tags = row
            cards.append(
                {
                    "cid": cid,
                    "nid": nid,
                    "tags": _split_tags(tags),
                    "type": ctype,
                    "queue": queue,
                    "ivl": ivl,
                    "dueToday": _is_due_today(queue, due, sched_today, day_cutoff),
                }
            )

    return {
        "deck": deck,
        "total": total,
        "offset": offset,
        "count": len(cards),
        "hasMore": offset + limit < total,
        "cards": cards,
    }
