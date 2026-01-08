"""List decks tool - list all Anki decks with optional statistics."""
from typing import Any
import logging

from ....tool_decorator import Tool, get_col

logger = logging.getLogger(__name__)


@Tool(
    "list_decks",
    "List all available Anki decks, optionally with statistics. Remember to sync first at the start of a review session for latest data.",
)
def list_decks(include_stats: bool = False) -> dict[str, Any]:
    col = get_col()
    from aqt import mw

    deck_name_id_pairs = col.decks.all_names_and_ids()

    if not deck_name_id_pairs:
        return {
            "message": "No decks found in Anki",
            "decks": [],
            "total": 0,
        }

    decks: list[dict[str, Any]] = []

    if include_stats:
        summary = {
            "total_cards": 0,
            "new_cards": 0,
            "learning_cards": 0,
            "review_cards": 0,
        }

        for deck_pair in deck_name_id_pairs:
            deck_name = deck_pair.name
            deck_id = deck_pair.id

            try:
                counts = col.sched.counts_for_deck_today(deck_id)
                new_count = counts[0] if len(counts) > 0 else 0
                learn_count = counts[1] if len(counts) > 1 else 0
                review_count = counts[2] if len(counts) > 2 else 0

                total_cards = col.db.scalar(
                    "SELECT count() FROM cards WHERE did = ? OR odid = ?",
                    deck_id,
                    deck_id,
                ) or 0

                deck_info: dict[str, Any] = {
                    "name": deck_name,
                    "stats": {
                        "deck_id": deck_id,
                        "name": deck_name,
                        "new_count": new_count,
                        "learn_count": learn_count,
                        "review_count": review_count,
                        "total_new": new_count,
                        "total_cards": total_cards,
                    },
                }

                summary["total_cards"] += total_cards
                summary["new_cards"] += new_count
                summary["learning_cards"] += learn_count
                summary["review_cards"] += review_count

            except Exception as e:
                logger.warning(f"Could not get stats for deck {deck_name}: {e}")
                deck_info = {"name": deck_name}

            decks.append(deck_info)

        return {
            "decks": decks,
            "total": len(decks),
            "summary": summary,
        }
    else:
        decks = [{"name": deck_pair.name} for deck_pair in deck_name_id_pairs]
        return {
            "decks": decks,
            "total": len(decks),
        }
