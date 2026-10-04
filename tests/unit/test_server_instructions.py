"""Unit test: the MCP server sends SERVER_INSTRUCTIONS in its initialize result.

Cross-tool workflow guidance (sync at session start/end, the review loops,
"only what the user asked for") lives in the server instructions rather than
in tool descriptions, per the Anthropic Software Directory Policy. This checks
``build_fastmcp()`` wires the constant through to the initialize options.

conftest.py installs aqt + primitives stubs before this module is collected,
so the addon import below is safe even without a running Anki.
"""

from __future__ import annotations

from anki_mcp_server.mcp_server import SERVER_INSTRUCTIONS, build_fastmcp


def test_build_fastmcp_sends_server_instructions() -> None:
    mcp = build_fastmcp("/", None)

    assert mcp.instructions == SERVER_INSTRUCTIONS
    init_options = mcp._mcp_server.create_initialization_options()
    assert init_options.instructions == SERVER_INSTRUCTIONS


def test_server_instructions_name_the_review_loops() -> None:
    for tool in (
        "sync",
        "get_due_cards",
        "present_card",
        "rate_card",
        "gui_deck_review",
        "gui_current_card",
        "gui_show_answer",
        "gui_answer_card",
    ):
        assert tool in SERVER_INSTRUCTIONS
