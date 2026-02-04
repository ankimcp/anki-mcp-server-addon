# primitives/prompts.py
"""Central prompt registration module."""

from anki_mcp_server.prompt_decorator import register_prompts

# Import triggers registration via @Prompt decorator
from .essential.prompts import review_session_prompt  # noqa: F401


def register_all_prompts(mcp) -> None:
    """Register all MCP prompts with the server.

    Note:
        Prompts don't need call_main_thread as they just generate
        text templates - they don't access Anki data directly.

    Args:
        mcp: FastMCP server instance
    """
    register_prompts(mcp)
