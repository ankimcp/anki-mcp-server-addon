"""GetTags action implementation for tag_management tool."""
from typing import Any

from ......handler_wrappers import get_col

# SQLite's bound-variable limit is 999 on versions <3.32.0 and 32766 on
# newer ones. Chunking at 900 keeps every IN (...) query safely under the
# lowest floor regardless of which SQLite the host Anki ships with.
_SQL_VAR_CHUNK = 900


def _split_tags(tags: str) -> list[str]:
    """Convert the raw ``notes.tags`` DB column into a list of tags.

    The column is a space-delimited string carrying sentinel spaces
    (e.g. ``" verbs grammar "``). Anki tags cannot contain spaces, so a plain
    whitespace split cleanly drops the sentinels. Empty / whitespace-only -> [].
    """
    return tags.split()


def get_tags_impl(deck: str = "") -> dict[str, Any]:
    """List tags, optionally scoped to notes with a card in a given deck.

    Args:
        deck: Optional deck name. Matches Anki ``deck:"..."`` semantics,
            including subdecks. Empty (default) returns all collection tags.

    Returns:
        Dict with tags list, count, and message
    """
    col = get_col()
    deck = deck.strip()

    if not deck:
        tags = col.tags.all()
        return {
            "tags": tags,
            "count": len(tags),
            "message": f"Found {len(tags)} tag(s)",
        }

    from anki.collection import SearchNode

    # Escaping-safe query, includes subdecks. Unknown/empty decks simply
    # resolve to no notes -- there is no existence check here, unlike
    # cards_stats, since an empty result is a valid answer for this action.
    query = col.build_search_string(SearchNode(deck=deck))
    note_ids = col.find_notes(query)

    distinct_tags: set[str] = set()
    if note_ids:
        # Chunked SQL queries for the whole set of notes, de-duped in Python --
        # mirrors cards_stats' approach rather than loading full Note objects.
        # Chunking avoids blowing past SQLite's bound-variable limit on
        # large decks (see _SQL_VAR_CHUNK above).
        for start in range(0, len(note_ids), _SQL_VAR_CHUNK):
            chunk = note_ids[start : start + _SQL_VAR_CHUNK]
            placeholders = ",".join("?" for _ in chunk)
            rows = col.db.all(
                f"SELECT tags FROM notes WHERE id IN ({placeholders})",
                *chunk,
            )
            for (raw_tags,) in rows:
                distinct_tags.update(_split_tags(raw_tags))

    tags = sorted(distinct_tags)
    return {
        "tags": tags,
        "count": len(tags),
        "message": f"Found {len(tags)} tag(s) in deck '{deck}'",
    }
