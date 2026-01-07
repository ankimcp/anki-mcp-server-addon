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
├── mcp_server.py            # FastMCP server in background thread
├── queue_bridge.py          # Thread-safe request/response queue
├── request_processor.py     # Main thread handler dispatcher
├── handler_registry.py      # Maps tool names to handler functions
├── dependency_loader.py     # Runtime pydantic_core download from PyPI
├── primitives/
│   ├── tools.py             # Central tool registration
│   ├── resources.py         # Central resource registration
│   ├── prompts.py           # Central prompt registration
│   ├── essential/tools/     # Core tools: sync, notes, decks, models, media
│   ├── essential/resources/ # system_info
│   ├── essential/prompts/   # review_session
│   └── gui/tools/           # UI tools: browse, add_cards, edit_note, etc.
└── vendor/                  # Vendored dependencies
```

### Tool Pattern

Each tool file contains both MCP and main-thread handlers in one place:

```python
# primitives/essential/tools/my_tool.py
from ....handler_registry import register_handler

# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================
def _my_handler() -> dict[str, Any]:
    from aqt import mw
    # Safe to access mw.col here
    return {"status": "success"}

register_handler("my_tool", _my_handler)

# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================
def register_my_tool(mcp, call_main_thread):
    @mcp.tool(description="Does something useful")
    async def my_tool(arg: str) -> dict[str, Any]:
        return await call_main_thread("my_tool", {"arg": arg})
```

## Adding New Tools

1. Create `primitives/essential/tools/my_tool.py` (or `gui/tools/` for GUI tools)
2. Add both the handler function (with `register_handler`) and MCP registration function
3. Import and call registration in `primitives/tools.py`
4. Rebuild: `./package.sh`

## Key Implementation Details

### Profile Lifecycle

- Server starts on `profile_did_open` hook
- Server stops on `profile_will_close` hook
- Fallback cleanup on `app_will_close`

### Vendored Dependencies

Dependencies are vendored in `vendor/shared/` to avoid conflicts with other addons. The `__init__.py` prepends vendor path to `sys.path`.

`pydantic_core` is lazy-loaded from PyPI at runtime via `dependency_loader.py` because it contains platform-specific binaries that can't be bundled in a single addon file.

### DNS Rebinding Protection

Disabled in `mcp_server.py` to allow tunnel/proxy access (Cloudflare, ngrok).

## Development Workflow

No test framework currently. To test changes:
1. Run `./package.sh`
2. Install the `.ankiaddon` in Anki
3. Restart Anki and check *Tools → AnkiMCP Server Settings...* for status
4. Test with an MCP client (e.g., Claude Desktop)

Debug output goes to Anki's console/terminal (run Anki from command line to see logs).

## Documentation

- [Anki Add-on Docs](https://addon-docs.ankiweb.net/) - Official addon development documentation
- [MCP Protocol](https://modelcontextprotocol.io/) - Model Context Protocol specification
- [FastMCP](https://github.com/jlowin/fastmcp) - MCP SDK used by this addon

## Common Issues

### UI Freezes During Operations

Long operations (like `sync`) run synchronously on main thread and can freeze UI. This is acceptable for v1 - same behavior as AnkiConnect.

### Port Already in Use

Change port in Anki's addon config: *Tools → Add-ons → AnkiMCP Server → Config*

### Restart Required for Config Changes

Port/host changes require Anki restart to take effect.
