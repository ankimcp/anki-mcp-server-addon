"""BatchTags action implementation for tag_management tool."""
from typing import Any
import logging

from ......handler_wrappers import get_col

logger = logging.getLogger(__name__)

_MAX_OPERATIONS = 50


def batch_tags_impl(operations: list[dict[str, Any]]) -> dict[str, Any]:
    """Execute multiple add/remove tag operations in sequence.

    Args:
        operations: List of dicts, each with 'type' ('add'/'remove'),
                   'note_ids' (list[int]), and 'tags' (str).

    Returns:
        Dict with per-operation results and summary counts.
    """
    col = get_col()

    results: list[dict[str, Any]] = []

    for i, op in enumerate(operations):
        op_type = op["type"]
        note_ids = op["note_ids"]
        tags = op["tags"]

        # Per-operation validation
        if not note_ids:
            results.append({
                "index": i,
                "type": op_type,
                "status": "failed",
                "error": "note_ids is required and cannot be empty",
            })
            continue

        if not tags.strip():
            results.append({
                "index": i,
                "type": op_type,
                "status": "failed",
                "error": "tags is required and cannot be empty",
            })
            continue

        try:
            if op_type == "add":
                result = col.tags.bulk_add(note_ids, tags)
                results.append({
                    "index": i,
                    "type": "add",
                    "status": "ok",
                    "affected_count": result.count,
                    "tags": tags,
                })
            elif op_type == "remove":
                result = col.tags.bulk_remove(note_ids, tags)
                results.append({
                    "index": i,
                    "type": "remove",
                    "status": "ok",
                    "affected_count": result.count,
                    "tags": tags,
                })
            else:
                # Should never reach here due to Pydantic Literal validation,
                # but defensive coding
                results.append({
                    "index": i,
                    "type": op_type,
                    "status": "failed",
                    "error": f"Unknown operation type: {op_type}",
                })
        except Exception as e:
            logger.warning("batch_tags operation %d failed: %s", i, e)
            results.append({
                "index": i,
                "type": op_type,
                "status": "failed",
                "error": str(e),
            })

    # Summary
    succeeded = sum(1 for r in results if r["status"] == "ok")
    failed = sum(1 for r in results if r["status"] == "failed")
    total = len(operations)

    parts = []
    if failed:
        parts.append(f"{failed} failed")
    detail = f" ({', '.join(parts)})" if parts else ""

    return {
        "succeeded": succeeded,
        "failed": failed,
        "total_operations": total,
        "results": results,
        "message": f"Completed {succeeded} of {total} tag operation(s){detail}",
    }
