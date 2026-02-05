# primitives/essential/resources/query_syntax_resource.py
"""Query syntax resource - provides Anki search query syntax documentation."""

from typing import Any

from ....resource_decorator import Resource


@Resource(
    "anki://query-syntax",
    "Get Anki search query syntax documentation for constructing card/note searches.",
    name="query_syntax",
    title="Search Query Syntax",
    require_col=False,  # Static documentation, no collection needed
)
def query_syntax() -> dict[str, Any]:
    """Get comprehensive documentation for Anki's search query syntax.

    This is static documentation that doesn't require collection access.
    Use this to understand how to construct search queries for find_notes,
    find_cards, and other search-based tools.

    Returns:
        dict: Query syntax documentation organized by category:
            - basic_searches: Text, exact phrase, and wildcard searches
            - field_searches: Searching within specific note fields
            - deck_and_tag: Deck and tag filtering
            - card_state: Card state filters (due, new, suspended, etc.)
            - card_properties: Numeric property filters (interval, ease, etc.)
            - date_searches: Date-based filtering
            - combining: Boolean operators and grouping
            - examples: Practical query examples with descriptions

    Example:
        >>> syntax = await read_resource("anki://query-syntax")
        >>> print(syntax["basic_searches"])
    """
    return {
        "basic_searches": {
            "description": "Search for text anywhere in notes",
            "syntax": {
                "text": {
                    "example": "dog",
                    "description": "Find notes containing 'dog' in any field",
                },
                "exact_phrase": {
                    "example": '"a]dog"',
                    "description": "Find exact phrase (use quotes for special chars like ])",
                },
                "wildcards": {
                    "example": "d_g, do*",
                    "description": "_ matches single char, * matches zero or more chars",
                },
            },
        },
        "field_searches": {
            "description": "Search within specific note fields",
            "syntax": {
                "field:value": {
                    "example": "front:dog",
                    "description": "Find notes where 'front' field contains 'dog'",
                },
                "field:*value*": {
                    "example": "front:*dog*",
                    "description": "Find 'dog' anywhere in the front field",
                },
                "field:": {
                    "example": "front:",
                    "description": "Find notes where front field is empty",
                },
                "field:_*": {
                    "example": "front:_*",
                    "description": "Find notes where front field is not empty",
                },
                "field:re:pattern": {
                    "example": "front:re:\\d{3}",
                    "description": "Search using regular expression",
                },
            },
        },
        "deck_and_tag": {
            "description": "Filter by deck or tag",
            "syntax": {
                "deck:NAME": {
                    "example": "deck:Default",
                    "description": "Cards in deck 'Default' (case insensitive)",
                },
                "deck:NAME::CHILD": {
                    "example": "deck:Languages::Spanish",
                    "description": "Cards in subdeck (use :: for hierarchy)",
                },
                "deck:*": {
                    "example": "deck:Lang*",
                    "description": "Wildcard matching for deck names",
                },
                '"deck:name with spaces"': {
                    "example": '"deck:My Deck"',
                    "description": "Quote deck names containing spaces",
                },
                "tag:NAME": {
                    "example": "tag:marked",
                    "description": "Notes with tag 'marked' (case insensitive)",
                },
                "tag:none": {
                    "example": "tag:none",
                    "description": "Notes without any tags",
                },
                "tag:*": {
                    "example": "tag:vocab::*",
                    "description": "Notes with any tag starting with 'vocab::'",
                },
            },
        },
        "card_state": {
            "description": "Filter by card learning state",
            "syntax": {
                "is:due": {
                    "example": "is:due",
                    "description": "Cards due for review today (review + learning)",
                },
                "is:new": {
                    "example": "is:new",
                    "description": "New cards (never studied)",
                },
                "is:learn": {
                    "example": "is:learn",
                    "description": "Cards in learning phase",
                },
                "is:review": {
                    "example": "is:review",
                    "description": "Cards in review phase (graduated)",
                },
                "is:suspended": {
                    "example": "is:suspended",
                    "description": "Suspended cards (won't appear in reviews)",
                },
                "is:buried": {
                    "example": "is:buried",
                    "description": "Buried cards (hidden until tomorrow)",
                },
                "-is:suspended": {
                    "example": "-is:suspended",
                    "description": "Cards that are NOT suspended",
                },
            },
        },
        "card_properties": {
            "description": "Filter by numeric card properties",
            "syntax": {
                "prop:ivl": {
                    "example": "prop:ivl>=30",
                    "description": "Cards with interval >= 30 days",
                },
                "prop:due": {
                    "example": "prop:due=1",
                    "description": "Cards due tomorrow (1 = tomorrow, -1 = yesterday)",
                },
                "prop:ease": {
                    "example": "prop:ease<2.0",
                    "description": "Cards with ease factor < 2.0 (struggling cards)",
                },
                "prop:lapses": {
                    "example": "prop:lapses>3",
                    "description": "Cards failed more than 3 times (leeches)",
                },
                "prop:reps": {
                    "example": "prop:reps>0",
                    "description": "Cards reviewed at least once",
                },
                "prop:rated": {
                    "example": "prop:rated:1:1",
                    "description": "Cards rated 'Again' in last 1 day (1=again,2=hard,3=good,4=easy)",
                },
            },
            "operators": ["=", "!=", "<", ">", "<=", ">="],
        },
        "date_searches": {
            "description": "Filter by dates (N = number of days ago)",
            "syntax": {
                "added:N": {
                    "example": "added:7",
                    "description": "Notes added in last 7 days",
                },
                "edited:N": {
                    "example": "edited:1",
                    "description": "Notes edited today (last 1 day)",
                },
                "rated:N": {
                    "example": "rated:3",
                    "description": "Cards reviewed in last 3 days",
                },
                "rated:N:ANSWER": {
                    "example": "rated:7:1",
                    "description": "Cards answered 'Again' in last 7 days",
                },
                "introduced:N": {
                    "example": "introduced:30",
                    "description": "Cards first studied in last 30 days",
                },
            },
        },
        "note_and_card_types": {
            "description": "Filter by note type or card template",
            "syntax": {
                "note:NAME": {
                    "example": "note:Basic",
                    "description": "Notes using the 'Basic' note type",
                },
                "card:NAME": {
                    "example": "card:Card 1",
                    "description": "Cards using the 'Card 1' template",
                },
                "card:N": {
                    "example": "card:2",
                    "description": "Second card template of each note",
                },
                "mid:ID": {
                    "example": "mid:1234567890",
                    "description": "Notes with specific model/note type ID",
                },
            },
        },
        "special_searches": {
            "description": "Special search terms",
            "syntax": {
                "nid:ID": {
                    "example": "nid:1234567890123",
                    "description": "Find specific note by ID",
                },
                "cid:ID": {
                    "example": "cid:1234567890123",
                    "description": "Find specific card by ID",
                },
                "flag:N": {
                    "example": "flag:1",
                    "description": "Cards with red flag (1=red,2=orange,3=green,4=blue)",
                },
                "flag:0": {
                    "example": "flag:0",
                    "description": "Cards without any flag",
                },
                "dupe:NOTETYPE,TEXT": {
                    "example": "dupe:Basic,hello",
                    "description": "Find duplicate notes",
                },
            },
        },
        "combining": {
            "description": "Combine multiple search terms",
            "syntax": {
                "AND (implicit)": {
                    "example": "deck:Default tag:marked",
                    "description": "Space between terms = AND (both must match)",
                },
                "OR": {
                    "example": "tag:vocab OR tag:grammar",
                    "description": "Match either condition (OR must be uppercase)",
                },
                "NOT (-)": {
                    "example": "-tag:easy",
                    "description": "Prefix with - to exclude matches",
                },
                "grouping ()": {
                    "example": "(tag:a OR tag:b) deck:Default",
                    "description": "Use parentheses to group conditions",
                },
            },
        },
        "examples": [
            {
                "query": "deck:Spanish is:due",
                "description": "Due cards in Spanish deck",
            },
            {
                "query": "tag:leech -is:suspended",
                "description": "Leech-tagged cards that aren't suspended",
            },
            {
                "query": "prop:ease<2.1 prop:ivl>21",
                "description": "Struggling cards with long intervals (candidates for extra review)",
            },
            {
                "query": "added:7 deck:Vocabulary",
                "description": "Recently added vocabulary cards",
            },
            {
                "query": '"deck:My Language Deck" (tag:verb OR tag:noun)',
                "description": "Verbs or nouns in a deck with spaces in name",
            },
            {
                "query": "front:re:^[A-Z]",
                "description": "Cards where front starts with capital letter (regex)",
            },
            {
                "query": "rated:1:1 deck:*",
                "description": "Cards answered 'Again' today in any deck",
            },
            {
                "query": "is:new -is:suspended deck:Default::Subdeck",
                "description": "New, unsuspended cards in a specific subdeck",
            },
            {
                "query": "prop:lapses>=8 -tag:leech",
                "description": "High-lapse cards not yet tagged as leeches",
            },
            {
                "query": "note:Cloze card:1",
                "description": "First cloze deletion of Cloze notes",
            },
        ],
    }
