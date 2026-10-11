"""Remove tags that no note uses."""
from typing import Any

from .....tool_decorator import Tool

from .actions.clear_unused_tags import clear_unused_tags_impl


@Tool(
    "tag_clear_unused",
    "Remove tags that are not used by any note. Takes no parameters. Returns cleared_count.",
    title="Clear Unused Tags",
    write=True,
    idempotent_hint=True,
)
def tag_clear_unused() -> dict[str, Any]:
    return clear_unused_tags_impl()
