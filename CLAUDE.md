# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick Reference

```bash
./package.sh                    # Build .ankiaddon package
# Install: double-click anki_mcp_server.ankiaddon or Tools → Add-ons → Install from file
# Restart Anki after installation
```

## Project Overview

Anki addon that runs an MCP server inside Anki, exposing collection operations to AI assistants. Uses FastMCP + uvicorn for HTTP transport.

- **Package**: `anki_mcp_server.ankiaddon`
- **Default Port**: 3141
- **License**: AGPL-3.0-or-later

## Architecture

### Threading Model

```
┌─────────────────────────────────────────┐
│          AI Client (HTTP)               │
└───────────────┬─────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────┐
│    Background Thread (asyncio)          │
│  - MCP Server (FastMCP + uvicorn)       │
│  - Tool handlers bridge to main thread  │
└───────────────┬─────────────────────────┘
                │
                │ queue.Queue (thread-safe)
                ▼
┌─────────────────────────────────────────┐
│        QueueBridge                      │
│  - request_queue                        │
│  - response_queue                       │
└───────────────┬─────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────┐
│     Qt Main Thread                      │
│  - QTimer (25ms polling)                │
│  - RequestProcessor                     │
│  - Access to mw.col (safe here)         │
└─────────────────────────────────────────┘
```

**Key Principle**: Never access `mw.col` from background threads. All Anki operations must go through the queue bridge to execute on the main Qt thread.

### Core Files

```
anki_mcp_server/
├── __init__.py              # Entry point, vendor path setup, lifecycle hooks
├── connection_manager.py    # Manages MCP server lifecycle
├── config.py                # Configuration from Anki's addon config
├── mcp_server.py            # FastMCP server in background thread (HTTP via uvicorn)
├── queue_bridge.py          # Thread-safe request/response queue
├── request_processor.py     # Main thread handler dispatcher
├── handler_registry.py      # Maps handler names to functions
├── handler_wrappers.py      # Shared wrappers: _error_handler, _require_col, HandlerError
├── tool_decorator.py        # @Tool decorator implementation
├── resource_decorator.py    # @Resource decorator implementation
├── prompt_decorator.py      # @Prompt decorator implementation
├── dependency_loader.py     # Runtime pydantic_core download from PyPI
└── primitives/
    ├── tools.py             # Imports all tool modules to trigger @Tool registration
    ├── resources.py         # Imports all resource modules to trigger @Resource registration
    ├── prompts.py           # Imports all prompt modules to trigger @Prompt registration
    ├── essential/
    │   ├── tools/           # Core tools: sync, notes, decks, models, media
    │   ├── resources/       # system_info, query_syntax, schema, stats
    │   └── prompts/         # review_session
    └── gui/tools/           # UI tools: browse, add_cards, edit_note, etc.
```

**Vendored Dependencies**: Located in `vendor/shared/`. The `__init__.py` prepends vendor path to `sys.path` at startup.

### Decorator Patterns

All MCP primitives use decorator-based registration. At import time, decorators automatically:
1. Wrap functions with error handling and collection checks
2. Register handlers for main-thread dispatch
3. Store metadata for MCP registration at server startup

#### @Tool Decorator

```python
# primitives/essential/tools/my_tool.py
from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError

@Tool(
    "my_tool",                    # Tool name exposed to MCP clients
    "Description for AI",         # Shown to AI to understand usage
    write=True,                   # Set True for operations that modify collection
)
def my_tool(arg: str) -> dict[str, Any]:
    from aqt import mw
    # Runs on Qt main thread - safe to access mw.col
    if not arg:
        raise HandlerError("Invalid arg", hint="Provide a non-empty string")
    return {"status": "success"}
```

Options:
- `write=True`: Wraps with Anki's undo system (`requireReset`/`maybeReset`)
- `require_col=True` (default): Checks collection is open before running

#### @Resource Decorator

```python
# primitives/essential/resources/my_resource.py
from ....resource_decorator import Resource

@Resource(
    "anki://deck/{deck_id}/stats",  # URI with template variables
    "Get statistics for a deck",
    name="deck_stats",               # Handler name (required, explicit)
    title="Deck Statistics",         # Human-readable title (optional)
)
def deck_stats(deck_id: str) -> dict[str, Any]:
    # deck_id extracted from URI template
    from aqt import mw
    return {"cards": 100}
```

Resources are read-only - no `write` option. URI template variables become function parameters.

#### @Prompt Decorator

```python
# primitives/essential/prompts/my_prompt.py
from ....prompt_decorator import Prompt

@Prompt("review_tips", "Tips for effective review")
def review_tips(deck_name: str = "Default") -> str:
    return f"When reviewing {deck_name}, focus on..."
```

Prompts don't access `mw.col` - they just generate text templates.

### Error Handling

Use `HandlerError` for structured errors with actionable hints:

```python
from ....handler_wrappers import HandlerError

raise HandlerError(
    "Deck not found",
    hint="Check spelling or use list_decks to see available decks",
    deck_name="Spansh"  # Extra context passed as kwargs
)
```

## Adding New Primitives

### Adding a Tool

1. Create `primitives/essential/tools/my_tool.py` (or `gui/tools/` for UI tools)
2. Use `@Tool` decorator with name, description, and optional `write=True`
3. Import in the package's `__init__.py` (e.g., `primitives/essential/tools/__init__.py`)
4. Rebuild: `./package.sh`

### Adding a Resource

1. Create `primitives/essential/resources/my_resource.py`
2. Use `@Resource` decorator with URI, description, and explicit `name`
3. Import in `primitives/essential/resources/__init__.py`
4. Rebuild: `./package.sh`

### Adding a Prompt

1. Create `primitives/essential/prompts/my_prompt.py`
2. Use `@Prompt` decorator with name and description
3. Import in `primitives/essential/prompts/__init__.py`
4. Rebuild: `./package.sh`

## Key Implementation Details

### Profile Lifecycle

- Server starts on `profile_did_open` hook
- Server stops on `profile_will_close` hook
- Fallback cleanup on `app_will_close`

### pydantic_core Runtime Loading

`pydantic_core` is lazy-loaded from PyPI at runtime via `dependency_loader.py` because it contains platform-specific binaries that can't be bundled in a single addon file.

### DNS Rebinding Protection

Disabled in `mcp_server.py` to allow tunnel/proxy access (Cloudflare, ngrok).

## Development Workflow

No test framework currently. To test changes:
1. Run `./package.sh`
2. Install the `.ankiaddon` in Anki (double-click or *Tools → Add-ons → Install from file...*)
3. Restart Anki and check *Tools → AnkiMCP Server Settings...* for status
4. Test with an MCP client (e.g., Claude Desktop)

### Viewing Debug Output

Run Anki from terminal to see logs:
```bash
# macOS
/Applications/Anki.app/Contents/MacOS/anki

# Linux
anki

# Windows
"C:\Program Files\Anki\anki.exe"
```

All `print()` statements and `logging` output appears in the terminal.

## Documentation

- [Anki Add-on Docs](https://addon-docs.ankiweb.net/) - Official addon development documentation
- [MCP Protocol](https://modelcontextprotocol.io/) - Model Context Protocol specification
- [FastMCP](https://github.com/jlowin/fastmcp) - MCP SDK used by this addon

## Known Gotchas

### Imports Must Be Relative

All imports in this addon use **relative imports** (e.g., `from ....tool_decorator import Tool`). This is the Anki addon ecosystem standard — AnkiConnect does this too. Absolute imports (`from anki_mcp_server.config import ...`) break AnkiWeb installs because AnkiWeb uses the addon ID (`124672614/`) as the directory name, not the package name.

### Anki Scheduler API Pitfalls

- `col.sched.deck_due_tree()` — correct way to get deck stats (AnkiConnect pattern). Tree nodes have: `deck_id`, `name`, `new_count`, `learn_count`, `review_count`, `total_in_deck` (Anki 2.1.46+)
- `col.sched.counts()` — returns (new, learning, review) for the **currently selected** deck
- `col.sched.counts_for_deck_today()` — does **NOT** work in modern Anki, silently returns wrong values
- Raw SQL (`col.db`) is acceptable for analytics/stats (revlog, card stats) — AnkiConnect does this too. For deck stats, always prefer `deck_due_tree()` over SQL.

### Python Version Compatibility

- MCP Python SDK requires Python >= 3.10 (uses `match`/`case`, `X | Y` syntax) — hard blocker, no workaround
- Anki 25.02 and earlier: Python 3.9 (**not supported**)
- Anki 25.07+: Python 3.13 (supported)
- No Anki version ships Python 3.10/3.11/3.12 — went directly from 3.9 → 3.13
- `__init__.py` has an early version check that raises `ImportError` with a clear message on Python < 3.10

### Install Methods

Always test both install methods when making changes:
- `.ankiaddon` file (double-click or *Tools → Add-ons → Install from file...*)
- AnkiWeb code (`124672614`) — directory name is the addon ID, not the package name

### UI Freezes During Operations

Long operations (like `sync`) run synchronously on main thread and can freeze UI. This is acceptable for v1 - same behavior as AnkiConnect.

### Port Already in Use

Change port in Anki's addon config: *Tools → Add-ons → AnkiMCP Server → Config*

### Restart Required for Config Changes

Port/host changes require Anki restart to take effect.
