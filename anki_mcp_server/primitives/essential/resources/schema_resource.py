# primitives/essential/resources/schema_resource.py
"""Schema resource - static documentation of Anki's data model for AI understanding."""

from typing import Any

from anki_mcp_server.resource_decorator import Resource


# Schema is static documentation - no collection access needed
@Resource(
    "anki://schema",
    "Anki data model documentation: entities, fields, relationships, and key concepts.",
    name="schema",
    title="Anki Data Model Schema",
    require_col=False,
)
def schema() -> dict[str, Any]:
    """Get comprehensive documentation of Anki's data model.

    This is a static resource providing structured documentation about Anki's
    core entities, their fields, relationships, and important concepts. Useful
    for AI assistants to understand the data model before making queries.

    Returns:
        dict: Complete schema documentation with:
            - entities: Core data entities (note, card, deck, model, revlog, tag)
            - concepts: Key Anki concepts explained (note_vs_card, scheduling, etc.)

    Example:
        >>> schema = await read_resource("anki://schema")
        >>> print(schema["entities"]["note"]["description"])

    Note:
        - This is static documentation, not live schema introspection
        - V3 scheduler values only (current standard since Anki 2.1.45)
        - Does not expose raw SQLite tables (security/abstraction)
    """
    return {
        "entities": _get_entities(),
        "concepts": _get_concepts(),
    }


def _get_entities() -> dict[str, Any]:
    """Entity definitions for Anki's data model."""
    return {
        "note": {
            "description": (
                "A note is a unit of information containing fields. "
                "Notes generate one or more cards based on their model (note type). "
                "Example: A vocabulary note with Front/Back fields."
            ),
            "fields": {
                "id": {
                    "type": "int",
                    "description": "Unique note ID (millisecond timestamp when created)",
                },
                "guid": {
                    "type": "str",
                    "description": "Globally unique identifier for syncing across devices",
                },
                "mid": {
                    "type": "int",
                    "description": "Model (note type) ID - references the model entity",
                },
                "mod": {
                    "type": "int",
                    "description": "Modification timestamp (seconds since epoch)",
                },
                "tags": {
                    "type": "list[str]",
                    "description": "List of tag strings attached to this note",
                },
                "fields": {
                    "type": "list[str]",
                    "description": (
                        "Field values in order defined by the model. "
                        "Index corresponds to model's field definitions."
                    ),
                },
            },
            "relations": {
                "model": "Note belongs to one model (note type) via mid",
                "cards": "Note has one or more cards generated from its templates",
                "tags": "Note can have many tags (stored as list)",
            },
        },
        "card": {
            "description": (
                "A card is a single flashcard generated from a note. "
                "Each note type template creates one card per note. "
                "Cards track review history and scheduling state."
            ),
            "fields": {
                "id": {
                    "type": "int",
                    "description": "Unique card ID (millisecond timestamp when created)",
                },
                "nid": {
                    "type": "int",
                    "description": "Note ID this card belongs to",
                },
                "did": {
                    "type": "int",
                    "description": "Deck ID where this card resides",
                },
                "ord": {
                    "type": "int",
                    "description": "Template ordinal (0-indexed) - which template generated this card",
                },
                "type": {
                    "type": "int",
                    "description": "Card type: 0=new, 1=learning, 2=review, 3=relearning",
                },
                "queue": {
                    "type": "int",
                    "description": (
                        "Queue status: 0=new, 1=learning, 2=review, 3=day-learn, "
                        "-1=suspended, -2=user-buried, -3=scheduler-buried"
                    ),
                },
                "due": {
                    "type": "int",
                    "description": (
                        "Due value (meaning varies by queue): "
                        "new=position, review=day number, learning=timestamp"
                    ),
                },
                "ivl": {
                    "type": "int",
                    "description": "Current interval in days (0 for new/learning cards)",
                },
                "factor": {
                    "type": "int",
                    "description": "Ease factor (permille, e.g., 2500 = 250%). 0 for new cards.",
                },
                "reps": {
                    "type": "int",
                    "description": "Total number of reviews (excluding failures)",
                },
                "lapses": {
                    "type": "int",
                    "description": "Number of times card went from review to relearning",
                },
                "left": {
                    "type": "int",
                    "description": (
                        "Remaining learning/relearning steps. "
                        "Encodes both remaining and total steps."
                    ),
                },
                "odid": {
                    "type": "int",
                    "description": "Original deck ID (non-zero if card is in a filtered deck)",
                },
                "odue": {
                    "type": "int",
                    "description": "Original due value (when in filtered deck)",
                },
                "flags": {
                    "type": "int",
                    "description": "Card flags (1=red, 2=orange, 3=green, 4=blue, 5=pink, 6=turquoise, 7=purple)",
                },
            },
            "relations": {
                "note": "Card belongs to one note via nid",
                "deck": "Card belongs to one deck via did (or odid if filtered)",
                "revlog": "Card has many review log entries",
            },
        },
        "deck": {
            "description": (
                "A deck is a container for cards with its own study settings. "
                "Decks can be nested using :: separator (e.g., 'Languages::Spanish'). "
                "There are regular decks and filtered decks."
            ),
            "fields": {
                "id": {
                    "type": "int",
                    "description": "Unique deck ID (1 is the Default deck)",
                },
                "name": {
                    "type": "str",
                    "description": "Deck name with :: for hierarchy (e.g., 'Parent::Child')",
                },
                "mod": {
                    "type": "int",
                    "description": "Modification timestamp",
                },
                "collapsed": {
                    "type": "bool",
                    "description": "Whether deck is collapsed in the deck browser",
                },
                "desc": {
                    "type": "str",
                    "description": "Deck description (shown when deck is selected)",
                },
                "conf": {
                    "type": "int",
                    "description": "Deck config (options group) ID for regular decks",
                },
                "dyn": {
                    "type": "bool",
                    "description": "True if this is a filtered (dynamic) deck",
                },
            },
            "relations": {
                "cards": "Deck contains many cards",
                "parent": "Deck may have a parent (via :: in name)",
                "children": "Deck may have child decks (via :: in name)",
                "config": "Regular deck uses one deck config for study settings",
            },
        },
        "model": {
            "description": (
                "A model (note type) defines the structure of notes: "
                "which fields they have and which card templates generate cards. "
                "Examples: Basic, Basic (and reversed), Cloze."
            ),
            "fields": {
                "id": {
                    "type": "int",
                    "description": "Unique model ID",
                },
                "name": {
                    "type": "str",
                    "description": "Model name (e.g., 'Basic', 'Cloze')",
                },
                "type": {
                    "type": "int",
                    "description": "Model type: 0=standard, 1=cloze",
                },
                "flds": {
                    "type": "list[dict]",
                    "description": (
                        "Field definitions: name, ord, sticky, rtl, font, size, etc. "
                        "Order determines how fields are stored in notes."
                    ),
                },
                "tmpls": {
                    "type": "list[dict]",
                    "description": (
                        "Card templates: name, qfmt (question format), afmt (answer format), "
                        "ord (ordinal). Each template generates one card per note."
                    ),
                },
                "css": {
                    "type": "str",
                    "description": "CSS styling shared by all templates in this model",
                },
                "sortf": {
                    "type": "int",
                    "description": "Index of field used for sorting in browser",
                },
            },
            "relations": {
                "notes": "Model has many notes using it",
                "fields": "Model defines many field definitions",
                "templates": "Model defines many card templates",
            },
        },
        "revlog": {
            "description": (
                "Review log entry recording a single card review. "
                "Used for statistics and can be exported for external analysis."
            ),
            "fields": {
                "id": {
                    "type": "int",
                    "description": "Unique ID (millisecond timestamp of review)",
                },
                "cid": {
                    "type": "int",
                    "description": "Card ID that was reviewed",
                },
                "usn": {
                    "type": "int",
                    "description": "Update sequence number for syncing",
                },
                "ease": {
                    "type": "int",
                    "description": "Answer button: 1=Again, 2=Hard, 3=Good, 4=Easy",
                },
                "ivl": {
                    "type": "int",
                    "description": "New interval after review (negative = seconds for learning)",
                },
                "lastIvl": {
                    "type": "int",
                    "description": "Previous interval before review",
                },
                "factor": {
                    "type": "int",
                    "description": "New ease factor (permille)",
                },
                "time": {
                    "type": "int",
                    "description": "Time spent on review in milliseconds",
                },
                "type": {
                    "type": "int",
                    "description": "Review type: 0=learn, 1=review, 2=relearn, 3=filtered, 4=manual",
                },
            },
            "relations": {
                "card": "Review log belongs to one card via cid",
            },
        },
        "tag": {
            "description": (
                "Tags are labels attached to notes for organization. "
                "Tags can be hierarchical using :: separator. "
                "Stored as strings in note.tags list."
            ),
            "fields": {
                "name": {
                    "type": "str",
                    "description": (
                        "Tag name, can include :: for hierarchy (e.g., 'language::spanish::verbs'). "
                        "Case-insensitive for matching, preserves case for display."
                    ),
                },
            },
            "relations": {
                "notes": "Tag can be attached to many notes",
            },
        },
    }


def _get_concepts() -> dict[str, Any]:
    """Key concepts for understanding Anki's data model."""
    return {
        "note_vs_card": {
            "title": "Notes vs Cards",
            "explanation": (
                "A NOTE is the source data (fields like Front/Back). "
                "A CARD is what you actually study. "
                "One note can generate multiple cards via templates. "
                "Example: A 'Basic (and reversed)' note creates two cards - "
                "one asking Front->Back, another asking Back->Front. "
                "When you edit a note, all its cards update automatically."
            ),
        },
        "model": {
            "title": "Models (Note Types)",
            "explanation": (
                "Models define note structure. Each model has: "
                "(1) FIELDS - what data to store (e.g., Front, Back, Extra), "
                "(2) TEMPLATES - how to generate cards from fields. "
                "Built-in models: Basic (1 card), Basic (and reversed) (2 cards), "
                "Cloze (variable cards based on {{c1::deletions}}). "
                "Users can create custom models for specific needs."
            ),
        },
        "deck_hierarchy": {
            "title": "Deck Hierarchy",
            "explanation": (
                "Decks organize cards. Hierarchy uses :: separator. "
                "Example: 'Languages::Spanish::Vocabulary' has parent 'Languages::Spanish'. "
                "Stats roll up to parents. Child decks inherit parent settings unless overridden. "
                "The Default deck (ID=1) cannot be deleted. "
                "Moving a card changes its did field."
            ),
        },
        "scheduling": {
            "title": "Scheduling (V3 Scheduler)",
            "explanation": (
                "Cards progress through states: New -> Learning -> Review. "
                "NEW: Never seen, due=position in new queue. "
                "LEARNING: Being learned, due=timestamp of next step. "
                "REVIEW: Graduated, due=day number, ivl=days between reviews. "
                "RELEARNING: Failed review, back to learning steps. "
                "Ease factor (factor field) adjusts interval growth: "
                "2500 means 250%, so intervals multiply by 2.5 on Good answers. "
                "FSRS (optional) uses memory_state for more accurate scheduling."
            ),
        },
        "filtered_decks": {
            "title": "Filtered (Dynamic) Decks",
            "explanation": (
                "Filtered decks temporarily borrow cards from other decks based on search. "
                "When card enters filtered deck: odid=original deck, odue=original due. "
                "When card leaves (rebuilt/emptied): returns to original deck. "
                "dyn=True identifies filtered decks. "
                "Cards in filtered decks have did=filtered deck, odid=source deck."
            ),
        },
        "card_states": {
            "title": "Card Queue States",
            "explanation": (
                "Queue determines when/if card appears for study: "
                "0=new (waiting to be introduced), "
                "1=learning (intraday learning steps), "
                "2=review (due for review on specific day), "
                "3=day-learning (learning step spanning days), "
                "-1=suspended (manually excluded from study), "
                "-2=user-buried (hidden until tomorrow by user), "
                "-3=scheduler-buried (auto-buried, e.g., sibling card seen today)."
            ),
        },
        "ids_and_timestamps": {
            "title": "IDs and Timestamps",
            "explanation": (
                "Most IDs are millisecond timestamps from creation time. "
                "This ensures uniqueness and provides implicit creation date. "
                "mod (modification time) is in seconds. "
                "Review timestamps (revlog.id) are milliseconds. "
                "due for review cards is day number (days since collection creation). "
                "due for learning cards is Unix timestamp (seconds)."
            ),
        },
    }
