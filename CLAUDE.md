# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick Reference

```bash
./package.sh                    # Build .ankiaddon package
make e2e                        # Full E2E cycle: build → Docker → test → teardown
make e2e-up                     # Build addon + start headless Anki container
make e2e-test                   # Run E2E tests (assumes container is running)
make e2e-down                   # Stop container
make e2e-debug                  # Start container and keep it running (VNC at localhost:5900)
make e2e-logs                   # Tail container logs
pytest tests/e2e/ -v            # Run tests directly (container must be up)
pytest tests/e2e/test_note_tools.py -v  # Run a single test file
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
    ├── tools.py             # Triggers auto-discovery of tool modules
    ├── resources.py         # Triggers auto-discovery of resource modules
    ├── prompts.py           # Explicit imports of prompt modules (no auto-discovery)
    ├── essential/
    │   ├── tools/           # Core tools: sync, notes, decks, models, media, FSRS, cards
    │   │   ├── *_tool.py         # Single-file tools (auto-discovered)
    │   │   ├── _fsrs_helpers.py  # _ prefix = helper, not auto-discovered
    │   │   ├── card_management/  # Multi-action tool (subpackage)
    │   │   │   ├── __init__.py          # Must import tool to trigger @Tool registration
    │   │   │   ├── card_management_tool.py  # Dispatcher with Pydantic discriminated union
    │   │   │   └── actions/             # One file per action
    │   │   ├── tag_management/  # Multi-action tool (subpackage)
    │   │   │   ├── __init__.py
    │   │   │   ├── tag_management_tool.py
    │   │   │   └── actions/
    │   │   └── filtered_deck/   # Multi-action tool (subpackage)
    │   │       ├── __init__.py
    │   │       ├── filtered_deck_tool.py
    │   │       └── actions/
    │   ├── resources/       # system_info, query_syntax, schema, stats
    │   └── prompts/         # review_session, twenty_rules
    └── gui/tools/           # UI tools: browse, add_cards, edit_note, etc.
```

**Vendored Dependencies**: Located in `vendor/shared/`. The `__init__.py` prepends vendor path to `sys.path` at startup. On load, `_check_vendor_conflicts()` warns if any vendored packages (mcp, pydantic, starlette, uvicorn, etc.) are already in `sys.modules` from other addons — helps debug compatibility issues.

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

#### Multi-Action Tools (Subpackage Pattern)

When a tool has multiple actions (like `card_management`, `tag_management`, or `filtered_deck`), use a subpackage instead of a single file:

```
primitives/essential/tools/my_multi_tool/
├── __init__.py              # MUST import the tool: `from .my_tool import my_tool`
├── my_tool.py               # Dispatcher with Pydantic discriminated union
└── actions/
    ├── _validate.py         # Shared helpers (_ prefix = not a tool)
    ├── action_one.py        # action_one_impl()
    └── action_two.py        # action_two_impl()
```

The dispatcher uses Pydantic `Annotated[Union[...], Field(discriminator="action")]` so MCP clients get a proper JSON schema with all action variants. Each action lives in its own file under `actions/` and exports an `_impl()` function. The dispatcher uses `match`/`case` to route.

**Critical**: The `__init__.py` must import the tool module — `pkgutil.walk_packages` discovers subpackages but only triggers `@Tool` registration if the decorated function is actually imported.

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

**Single-file tool:**
1. Create `primitives/essential/tools/my_tool.py` (or `gui/tools/` for UI tools)
2. Use `@Tool` decorator with name, description, and optional `write=True`
3. Rebuild: `./package.sh` — auto-discovered via `pkgutil.walk_packages`

**Multi-action tool:** Create a subpackage (see "Multi-Action Tools" pattern above)

**Helper files:** Prefix with `_` (e.g., `_fsrs_helpers.py`) — they won't be treated as tool modules

### Adding a Resource

1. Create `primitives/essential/resources/my_resource.py`
2. Use `@Resource` decorator with URI, description, and explicit `name`
3. Rebuild: `./package.sh` — auto-discovered via `pkgutil.walk_packages` in `__init__.py`

### Adding a Prompt

1. Create `primitives/essential/prompts/my_prompt.py`
2. Use `@Prompt` decorator with name and description
3. **Manually import** in `primitives/prompts.py` (prompts are NOT auto-discovered)
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

### CORS Configuration

Configured via addon settings (`cors_origins`, `cors_expose_headers`). Empty `cors_origins` = CORS disabled. The `mcp-session-id` and `mcp-protocol-version` headers must be exposed for browser-based MCP clients (Streamable HTTP protocol requirement). See `config.py` for the full `Config` dataclass.

## Development Workflow

### E2E Tests

Tests run against a real Anki instance in Docker using [headless-anki](https://github.com/ankimcp/headless-anki). The test client is `npx @modelcontextprotocol/inspector --cli` (MCP Inspector CLI), which means **Node.js is required** in addition to Python.

```bash
# One-time setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# Full cycle (build → start Docker → wait for server → test → teardown)
make e2e

# Or step by step:
make e2e-up                     # Build + start container (waits 5s)
make e2e-test                   # Run pytest
make e2e-down                   # Stop container
```

**Environment variables:**
- `MCP_SERVER_URL` — override server URL (default: `http://localhost:3141`)
- `E2E_MAX_WAIT` — seconds to wait for server readiness (default: `60`)
- `E2E_KEEP_RUNNING` — set to `1` to keep container running after tests

**Server readiness**: `conftest.py` has a `session`-scoped `wait_for_server` fixture that polls the server up to `E2E_MAX_WAIT` seconds before any tests run — no need to manually wait.

**Docker setup** (`.docker/`): The `docker-compose.yml` mounts `config.json` that binds the MCP server to `0.0.0.0` inside the container (instead of the default `127.0.0.1`) so the host can reach port 3141. It also mounts a custom `entrypoint.sh` that installs the `.ankiaddon` and starts headless Anki.

**Debugging failed tests:**
- `make e2e-debug` — keeps container running after start; VNC available at `localhost:5900`
- `make e2e-logs` — tail Docker container logs
- Run Anki from terminal to see `print()`/`logging` output:
  ```bash
  # macOS
  /Applications/Anki.app/Contents/MacOS/anki
  ```

### Writing E2E Tests

Tests use `tests/e2e/helpers.py` which wraps the MCP Inspector CLI. Available helpers:

```python
from .helpers import call_tool, list_tools, read_resource, list_resources, list_prompts, get_prompt

# Call a tool
result = call_tool("findNotes", {"query": "deck:*", "limit": "5"})

# Read a resource
info = read_resource("anki://system-info")

# Get a prompt
prompt = get_prompt("review_session", {"review_style": "quick"})
```

Test conventions:
- One test file per feature area (e.g., `test_note_tools.py`, `test_fsrs_tools.py`)
- Group related tests in classes (e.g., `class TestNoteTools`)
- Tool args are always strings (MCP CLI serialization)
- Check `result.get("isError") is True` for expected error responses

### Manual Testing

For changes that can't be tested via E2E (UI interactions, config dialog):
1. Run `./package.sh`
2. Install `.ankiaddon` in Anki (double-click or *Tools → Add-ons → Install from file...*)
3. Restart Anki and check *Tools → AnkiMCP Server Settings...* for status

### CI / Release

- **E2E tests** run on every push and PR to `main` (`.github/workflows/e2e.yml`)
- **Releases** trigger on `v*.*.*` tags — runs E2E first, then creates GitHub Release with the `.ankiaddon` artifact (`.github/workflows/release.yml`)

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
- `col.sched.suspend_cards(ids)` → `OpChangesWithCount` (has `.count`), but `col.sched.unsuspend_cards(ids)` → `OpChanges` (no `.count`). Similar asymmetry: `set_due_date` and `schedule_cards_as_new` return `OpChanges`, not `OpChangesWithCount`.
- `col.tags.bulk_add(ids, tags)` and `col.tags.bulk_remove(ids, tags)` → `OpChangesWithCount`. `col.tags.clear_unused_tags()` → `OpChangesWithCount`. `col.tags.all()` → `list[str]`.

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
