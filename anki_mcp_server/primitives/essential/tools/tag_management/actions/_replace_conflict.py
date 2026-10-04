"""Pre-write conflict check for the replace_tags action.

replace_tags adds new_tag, then removes old_tag with ``col.tags.bulk_remove``.
rslib's TagMatcher (rslib/src/tags/matcher.rs) builds a case-insensitive regex
that matches each space-separated tag exactly OR as a ``tag::`` prefix, so the
remove also strips every child tag and any case variant. A new_tag that equals
old_tag ignoring case, or that is a child of it, would therefore be removed by
the second step, leaving the notes without either tag.
"""
from typing import Literal, Optional

ReplaceConflict = Literal["same_tag", "child_tag"]


def find_replace_conflict(old_tag: str, new_tag: str) -> Optional[ReplaceConflict]:
    """Return the conflict that would make replace_tags drop new_tag, or None.

    Both arguments are split on whitespace the way Anki splits them, so a
    multi-tag string is checked tag by tag.
    """
    old_tags = [t.lower() for t in old_tag.split()]
    for new in (t.lower() for t in new_tag.split()):
        for old in old_tags:
            if new == old:
                return "same_tag"
            if new.startswith(old + "::"):
                return "child_tag"
    return None
