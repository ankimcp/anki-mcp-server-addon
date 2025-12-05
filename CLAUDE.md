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
- **License**: MIT

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

### File Structure

```
anki_mcp_server/
├── __init__.py              # Entry point, hooks registration, pydantic_core loader
├── addon.py                 # Main addon class, lifecycle management
├── config.py                # Configuration from Anki's addon config
├── mcp_server.py            # FastMCP server in background thread
├── queue_bridge.py          # Thread-safe request/response queue
├── request_processor.py     # Main thread handler dispatcher
├── handler_registry.py      # Maps tool names to handler functions
├── settings_dialog.py       # Qt settings UI
├── primitives/              # MCP tools, resources, prompts
│   ├── tools.py             # Central tool registration
│   ├── resources.py         # Central resource registration
│   ├── prompts.py           # Central prompt registration
│   ├── essential/           # Core Anki operations
│   │   ├── tools/           # sync, add_note, find_notes, etc.
│   │   ├── resources/       # system_info
│   │   └── prompts/         # review_session
│   └── gui/                 # GUI interaction tools
│       └── tools/           # gui_browse, gui_add_cards, etc.
└── vendor/                  # Vendored dependencies (mcp, uvicorn, starlette, etc.)
```

### Tool Pattern

Each tool has two parts:

1. **MCP Handler** (`primitives/.../tools/*_tool.py`) - Runs in background thread
   - Registers with FastMCP via decorator
   - Calls `call_main_thread(tool_name, arguments)`
   - Returns result to MCP client

2. **Main Thread Handler** (`primitives/.../tools/*_tool.py`) - Runs on Qt main thread
   - Registers via `@register_handler("tool_name")`
   - Has safe access to `mw.col`
   - Returns result dict

Example structure:
```python
# MCP HANDLER - Background thread
def register_my_tool(mcp, call_main_thread):
    @mcp.tool(name="my_tool", description="...")
    async def my_tool(arg: str) -> str:
        result = await call_main_thread("my_tool", {"arg": arg})
        return json.dumps(result)

# MAIN THREAD HANDLER - Qt main thread
@register_handler("my_tool")
def handle_my_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    # Safe to access mw.col here
    return {"status": "success"}
```

## Adding New Tools

1. Create `primitives/essential/tools/my_tool.py` (or `gui/tools/` for GUI tools)
2. Implement both MCP handler and main thread handler
3. Add import and registration call to `primitives/tools.py`
4. Rebuild: `./package.sh`

## Key Implementation Details

### Profile Lifecycle

- Server starts on `profile_did_open` hook
- Server stops on `profile_will_close` hook
- Fallback cleanup on `app_will_close` (Edge case: called on profile switch)

### DNS Rebinding Protection

Disabled in `mcp_server.py` to allow tunnel/proxy access (Cloudflare, ngrok). Users explicitly configure tunnel access.

### Vendored Dependencies

Dependencies are vendored in `vendor/shared/` to avoid conflicts with other addons. The `__init__.py` prepends vendor path to `sys.path`.

`pydantic_core` is special - it's lazy-loaded from PyPI at runtime due to platform-specific binaries.

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
