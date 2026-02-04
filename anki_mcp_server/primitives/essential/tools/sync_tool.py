"""Sync tool - trigger Anki sync with AnkiWeb."""
from typing import Any

from anki_mcp_server.tool_decorator import Tool
from anki_mcp_server.handler_wrappers import HandlerError


@Tool(
    "sync",
    "Synchronize local Anki collection with AnkiWeb. IMPORTANT: Always sync at the START of a review session (before getting cards) and at the END when user indicates they are done. This ensures data consistency across devices.",
)
def sync() -> dict[str, Any]:
    from aqt import mw

    if not mw.pm.profile.get("syncKey"):
        raise HandlerError(
            "Sync not configured",
            hint="Please sync manually first to set up AnkiWeb credentials",
        )

    mw.onSync()

    return {"status": "started", "message": "Sync started"}
