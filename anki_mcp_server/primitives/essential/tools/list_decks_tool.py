"""List decks tool - list all Anki decks with optional statistics."""
from typing import Any
import logging

from ....tool_decorator import Tool
from ....handler_wrappers import get_col

logger = logging.getLogger(__name__)


@Tool(
    "list_decks",
    "List all available Anki decks, optionally with statistics. Remember to sync first at the start of a review session for latest data.",
)
def list_decks(include_stats: bool = False) -> dict[str, Any]:
    col = get_col()

    deck_name_id_pairs = col.decks.all_names_and_ids()

    if not deck_name_id_pairs:
        return {
            "message": "No decks found in Anki",
            "decks": [],
            "total": 0,
        }

    # Build set of filtered deck IDs for efficient lookup
    filtered_ids: set[int] = set()
    for d in col.decks.all():
        if d.get("dyn"):
            filtered_ids.add(d["id"])

    decks: list[dict[str, Any]] = []

    if include_stats:
        summary = {
            "total_cards": 0,
            "new_cards": 0,
            "learning_cards": 0,
            "review_cards": 0,
        }

        # Use deck_due_tree() for efficient stats retrieval
        deck_tree = col.sched.deck_due_tree()

        # Build a map of deck_id -> tree node for quick lookup
        def build_node_map(node, node_map):
            node_map[node.deck_id] = node
            for child in node.children:
                build_node_map(child, node_map)

        node_map: dict[int, Any] = {}
        for child in deck_tree.children:
            build_node_map(child, node_map)

        for deck_pair in deck_name_id_pairs:
            deck_name = deck_pair.name
            deck_id = deck_pair.id

            tree_node = node_map.get(deck_id)

            if tree_node:
                new_count = tree_node.new_count
                learn_count = tree_node.learn_count
                review_count = tree_node.review_count
                total_in_deck = tree_node.total_in_deck  # Available in Anki 2.1.46+

                deck_info: dict[str, Any] = {
                    "deck_id": deck_id,
                    "name": deck_name,
                    "is_filtered": deck_id in filtered_ids,
                    "stats": {
                        "new_count": new_count,
                        "learn_count": learn_count,
                        "review_count": review_count,
                        "total_in_deck": total_in_deck,
                    },
                }

                summary["total_cards"] += total_in_deck
                summary["new_cards"] += new_count
                summary["learning_cards"] += learn_count
                summary["review_cards"] += review_count
            else:
                logger.warning(f"Could not find stats for deck {deck_name}")
                deck_info = {
                    "deck_id": deck_id,
                    "name": deck_name,
                    "is_filtered": deck_id in filtered_ids,
                }

            decks.append(deck_info)

        return {
            "decks": decks,
            "total": len(decks),
            "summary": summary,
        }
    else:
        decks = [
            {
                "deck_id": deck_pair.id,
                "name": deck_pair.name,
                "is_filtered": deck_pair.id in filtered_ids,
            }
            for deck_pair in deck_name_id_pairs
        ]
        return {
            "decks": decks,
            "total": len(decks),
        }
