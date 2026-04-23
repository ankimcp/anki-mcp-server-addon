# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick Reference

```bash
./package.sh                    # Build .ankiaddon package
make e2e                        # Full E2E cycle: regular (port 3141) + filtered (port 3142)
make e2e-full                   # Regular tests only: build → Docker → test → teardown
make e2e-up                     # Build addon + start headless Anki container (port 3141)
make e2e-test                   # Run E2E tests (excludes test_tool_filtering_e2e.py)
make e2e-down                   # Stop container
make e2e-debug                  # Start container and keep it running (VNC at localhost:5900)
make e2e-logs                   # Tail container logs
make e2e-filtered               # Filtered tests only: build → Docker (port 3142) → test → teardown
make e2e-filtered-up            # Start filtered container (docker-compose.filtered.yml)
make e2e-filtered-test          # Run test_tool_filtering_e2e.py against port 3142
make e2e-filtered-down          # Stop filtered container
pytest tests/e2e/ -v --ignore=tests/e2e/test_tool_filtering_e2e.py  # Run tests directly
pytest tests/e2e/test_note_tools.py -v  # Run a single test file
pytest tests/unit/ -v                   # Run unit tests (tunnel in-memory transport)
```

## Project Overview

Anki addon that runs an MCP server inside Anki, exposing collection operations to AI assistants. Supports two independent transports: local HTTP (FastMCP + uvicorn) and remote tunnel (WebSocket relay to a public HTTPS URL).

- **Package**: `anki_mcp_server.ankiaddon`
- **Default Port**: 3141 (HTTP)
- **License**: AGPL-3.0-or-later

## Architecture

### Threading Model

```
┌──────────────────┐    ┌───────────────────────────────┐
│  AI Client (HTTP)│    │  AI Client (remote, via tunnel)│
└────────┬─────────┘    └──────────────┬────────────────┘
         │                             │
         ▼                             ▼
┌─────────────────────────────────────────────────────────┐
│    Background Thread (single asyncio event loop)        │
│                                                         │
│  HTTP path:                  Tunnel path:               │
│  uvicorn → StreamableHTTP    WebSocket → InMemoryTransport│
│       ↘                           ↙                     │
│         Server.run() (shared FastMCP)                   │
│         Tool handlers bridge to main thread              │
└───────────────────────┬─────────────────────────────────┘
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

Both HTTP and tunnel transports share the same `Server` object (same handlers, same tools). Each runs its own `Server.run()` with separate streams and session state. Either can be enabled/disabled independently.

### Core Files

```
anki_mcp_server/
├── __init__.py              # Entry point, vendor path setup, lifecycle hooks
├── connection_manager.py    # Manages MCP server + tunnel lifecycle
├── config.py                # Configuration from Anki's addon config
├── credentials.py           # OAuth credentials file I/O (~/.ankimcp/credentials.json)
├── mcp_server.py            # FastMCP server in background thread (HTTP via uvicorn)
├── queue_bridge.py          # Thread-safe request/response queue
├── request_processor.py     # Main thread handler dispatcher
├── handler_registry.py      # Maps handler names to functions
├── handler_wrappers.py      # Shared wrappers: _error_handler, _require_col, HandlerError
├── tool_decorator.py        # @Tool decorator implementation
├── resource_decorator.py    # @Resource decorator implementation
├── prompt_decorator.py      # @Prompt decorator implementation
├── dependency_loader.py     # Runtime pydantic_core download from PyPI
├── media_validators.py      # Path traversal / SSRF guards for media tools
├── tunnel/
│   ├── __init__.py              # Package marker
│   ├── protocol.py              # Close codes, message types, constants (pure data, no I/O)
│   ├── auth.py                  # OAuth 2.0 Device Flow client (async HTTP via httpx)
│   ├── client.py                # Single WebSocket connection lifecycle (no retry)
│   ├── reconnect.py             # Retry/backoff wrapper around client
│   ├── in_memory_transport.py   # Feeds JSON-RPC into Server.run() via anyio streams
│   ├── log.py                   # Thread-safe ring buffer with Qt signal
│   └── ui/
│       ├── login_dialog.py      # Qt device flow dialog (user code + browser button)
│       └── settings_section.py  # Tunnel status/controls in settings dialog
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

**Build (`package.sh`)**: Downloads wheels pinned to `--python-version 313` (Anki 25.07's Python). Deliberately **excludes `pydantic_core`** from the bundle — it has platform-specific binaries and is instead lazy-loaded at runtime via `dependency_loader.py`. If modifying the build, keep this exclusion intact.

**Keeping vendor lists in sync**: When adding or removing a vendored dependency, update **both** `PURE_PACKAGES` in `package.sh` (controls what gets downloaded/bundled) **and** `_VENDOR_PACKAGES` in `__init__.py` (controls conflict detection at startup). They must stay in sync.

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

**Description metadata**: Each Params model has a `_tool_description: ClassVar[str]` with the action's description line, and each tool module has a `_BASE_DESCRIPTION` constant. Descriptions are **always** built dynamically from these — the static string in `@Tool()` is dead code for multi-action tools. This ensures a single source of truth.

**Tool filtering**: The `disabled_tools` config can hide entire tools or specific actions. Per-action filtering rebuilds the Pydantic discriminated union at registration time, removing disabled actions from the JSON schema entirely. See `tool_decorator.py` for the filtering helpers.

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

### Tunnel Architecture

The tunnel provides remote access to the MCP server via a public HTTPS URL, allowing AI clients that cannot reach `localhost` (e.g., Claude Desktop on a different machine, mobile clients) to connect.

#### How It Works

The tunnel client connects to a WebSocket relay server (SaaS). The relay assigns a public HTTPS URL. When an AI client sends an MCP request to that URL, the relay forwards it over WebSocket to the addon, which processes it via an in-memory transport directly into FastMCP and sends the response back.

```
AI Client (remote)
    → HTTPS request to public URL
    → Tunnel relay server (WebSocket)
    → TunnelClient._handle_request()
    → InMemoryTransport.handle_request(json_rpc_body)
    → Server.run() → FastMCP handlers → QueueBridge → Qt main thread
    → Response flows back the same path
```

#### In-Memory Transport

The tunnel does NOT proxy through HTTP. Instead, `in_memory_transport.py` feeds JSON-RPC strings directly into the MCP SDK's `Server.run()` via anyio memory streams. This makes the tunnel a first-class transport peer to HTTP, not a proxy layer.

Key details:
- Uses raw `anyio.create_memory_object_stream[SessionMessage | Exception](1)` (not the SDK's context manager helper)
- Runs `Server.run(stateless=True)` — skips MCP initialize handshake since the remote client drives initialization through the relay
- Matches responses to requests by JSON-RPC `id` via `asyncio.Future`
- Notifications (no `id`) are fire-and-forget
- One fresh `InMemoryTransport` per connection attempt (clean session on reconnect)

#### Threading

The tunnel runs on the **same asyncio event loop** as HTTP (the background thread in `mcp_server.py`). Both HTTP and tunnel share one `Server` object (via `FastMCP._mcp_server`). Each transport runs its own `Server.run()` with independent streams — no cross-contamination. When `http_enabled=False`, the asyncio loop stays alive via an `asyncio.Event` wait instead of uvicorn.

#### Module Responsibilities

Each tunnel module has a single responsibility. Dependencies flow one direction (downward). UI is never imported by core tunnel logic.

- `protocol.py` — pure data types and constants (close codes, message TypedDicts, timeouts). Zero I/O, zero state.
- `credentials.py` — credential file I/O only (`~/.ankimcp/credentials.json`). No auth, no network. Lives at `anki_mcp_server/credentials.py` (not in tunnel/) since it's shared with the main addon.
- `auth.py` — async OAuth Device Flow HTTP calls via httpx. No WebSocket, no file I/O.
- `client.py` — single WebSocket connection lifecycle. Receives tunnel requests, proxies via `InMemoryTransport`, handles ping/pong. No retry logic.
- `reconnect.py` — retry/backoff wrapper around `TunnelClient`. Creates a fresh client + transport per attempt. This is the main entry point callers use.
- `in_memory_transport.py` — feeds JSON-RPC into `Server.run()` via anyio memory streams.
- `log.py` — thread-safe ring buffer with Qt signal for cross-thread UI updates.
- `ui/login_dialog.py` — Qt device flow dialog (user code + "Open Browser" button).
- `ui/settings_section.py` — tunnel status/controls in the settings dialog.

#### Configuration

Three config fields control tunnel behavior:

- `http_enabled: bool = True` — when `False`, uvicorn doesn't start. Only tunnel transport is available. Toggle via the settings dialog checkbox.
- `tunnel_server_url: str` — WebSocket URL of the tunnel relay server. Default is `ws://localhost:3004` (development). Production will be `wss://tunnel.ankimcp.ai`.
- `tunnel_client_id: str` — OAuth client identifier. Default is `ankimcp-cli` (shared with the TypeScript CLI).

There is no `mode` field and no `auto_connect_on_startup` field. HTTP is always-on by default (controlled by `http_enabled`). Tunnel never auto-connects — the user must explicitly click "Connect Tunnel" in the settings dialog each time.

#### Credential Sharing

Credentials are stored at `~/.ankimcp/credentials.json` with `0o600` file permissions. The format is identical to the TypeScript CLI's `CredentialsService`, so logging in via either the CLI or the addon works for both. The `Credentials` dataclass holds `access_token`, `refresh_token`, `expires_at`, and `user` (with email and tier).

#### Settings Dialog

The settings dialog (*Tools -> AnkiMCP Server Settings...*) has:
- **HTTP section**: status display, URL, "Copy URL" button. Shows "Disabled" when `http_enabled=False`.
- **Tunnel section**: Connect/Disconnect button, status with user email + tier + URL expiry, Logout link.
- **Log section**: scrollable ring buffer of recent tunnel events (connections, requests, errors, auth).

Connect flow: check credentials -> no credentials? launch device flow login dialog -> credentials expired? silent refresh -> connect WebSocket -> show tunnel URL.

#### Known Issues

**SIGKILL crash**: Anki can crash with SIGKILL when the tunnel relay server restarts while a tunnel connection is active. This is under investigation. The in-memory transport redesign was partly motivated by eliminating HTTP proxying as a potential crash vector, but the issue may persist.

#### Adding Tunnel-Related Code

Follow the existing module patterns:
- Pure data/constants go in `protocol.py`
- Network I/O gets its own module (like `auth.py` for HTTP, `client.py` for WebSocket)
- UI code goes in `tunnel/ui/` and must never be imported by core tunnel modules
- Use `TunnelLog` for user-visible events (not `print()` or `logging` alone)
- All callbacks are fire-and-forget — never let callback exceptions crash the tunnel

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

### Versioning & Releases

Version lives in `__init__.py:__version__`. Release process: bump version → commit → push tag `v*.*.*` → CI runs E2E tests → creates GitHub Release with `.ankiaddon` artifact.

### Source Install Mode (Nix)

When installed from source (Nix, pip), vendored packages aren't used. `__init__.py` sets `_USING_SYSTEM_PACKAGES = True` and skips vendor path setup + conflict detection. The flag is toggled by checking whether system packages are importable before prepending the vendor path.

### No Linter / Formatter

This project has no pyproject.toml, ruff, flake8, or any configured linter. Don't try to run lint commands — they won't work.

### Profile Lifecycle

- Server starts on `profile_did_open` hook (HTTP auto-starts if `http_enabled`, tunnel never auto-starts)
- Server stops on `profile_will_close` hook (both HTTP and active tunnel)
- Fallback cleanup on `app_will_close`

### pydantic_core Runtime Loading

`pydantic_core` is lazy-loaded from PyPI at runtime via `dependency_loader.py` because it contains platform-specific binaries that can't be bundled in a single addon file.

### DNS Rebinding Protection

Disabled in `mcp_server.py` to allow tunnel/proxy access (Cloudflare, ngrok).

### CORS Configuration

Configured via addon settings (`cors_origins`, `cors_expose_headers`). Empty `cors_origins` = CORS disabled. The `mcp-session-id` and `mcp-protocol-version` headers must be exposed for browser-based MCP clients (Streamable HTTP protocol requirement). See `config.py` for the full `Config` dataclass. CORS only applies to the HTTP transport — the tunnel path bypasses HTTP entirely.

### Connection Modes

`Config.mode` is `Literal["http"]` — only one mode is supported today. `is_valid_for_mode()` and the surrounding scaffolding exist for future modes (e.g., tunnel) but are unused right now. Don't conditionalize behavior on `mode` until a second mode actually lands.

### Tool Filtering

`disabled_tools` config hides tools/actions from AI clients. Supports whole-tool (`"sync"`) and per-action (`"card_management:bury"`) granularity. Typos produce `print()` warnings visible in Anki's console. See `tool_decorator.py` for implementation.

## Development Workflow

### E2E Tests

Most tests are E2E — the addon runs inside Anki's Qt event loop and most code touches `mw.col`, making unit testing impractical without a full Anki environment. Unit tests exist only for pure-logic modules that don't depend on Anki (e.g., `tests/unit/test_in_memory_transport.py`).

Tests run against a real Anki instance in Docker using [headless-anki](https://github.com/ankimcp/headless-anki). The test client is `npx @modelcontextprotocol/inspector --cli` (MCP Inspector CLI), which means **Node.js is required** in addition to Python.

```bash
# One-time setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# Full cycle (build → start Docker → wait for server → test → teardown)
make e2e                        # Runs BOTH regular and filtered suites

# Or step by step:
make e2e-up                     # Build + start container (waits 5s)
make e2e-test                   # Run pytest (excludes tool filtering tests)
make e2e-down                   # Stop container
```

**Two test suites**: `make e2e` runs both the regular suite (port 3141, all tools enabled) and the filtered suite (port 3142, `docker-compose.filtered.yml` with `disabled_tools` config). The filtering tests live in `test_tool_filtering_e2e.py` and are excluded from `make e2e-test` — they have their own `make e2e-filtered-*` targets.

**Environment variables:**
- `MCP_SERVER_URL` — override server URL (default: `http://localhost:3141`)
- `E2E_MAX_WAIT` — seconds to wait for server readiness (default: `60`)
- `E2E_KEEP_RUNNING` — set to `1` to keep container running after tests

**Server readiness**: `conftest.py` has a `session`-scoped `wait_for_server` fixture that polls the server up to `E2E_MAX_WAIT` seconds before any tests run — no need to manually wait.

**Docker setup** (`.docker/`): The `docker-compose.yml` mounts `config.json` that binds the MCP server to `0.0.0.0` inside the container (instead of the default `127.0.0.1`) so the host can reach port 3141. It also mounts a custom `entrypoint.sh` that installs the `.ankiaddon` and starts headless Anki. CI pins `ghcr.io/ankimcp/headless-anki:qt-vnc-v1.0.0`.

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
result = call_tool("find_notes", {"query": "deck:*", "limit": "5"})

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

For changes that can't be tested via E2E (UI interactions, config dialog, tunnel):
1. Run `./package.sh`
2. Install `.ankiaddon` in Anki (double-click or *Tools → Add-ons → Install from file...*)
3. Restart Anki and check *Tools → AnkiMCP Server Settings...* for status

Tunnel testing is manual-only — there are no E2E tests for the tunnel path. Test by connecting via the settings dialog and verifying the tunnel URL works from an external MCP client.

### No Linters or Type Checkers

This project has **no configured linters, formatters, or type checkers** (no ruff, flake8, mypy, black, etc.). Dev dependencies are just `pytest` and `pytest-asyncio`. Don't try to run linting commands or add linting configuration.

### CI / Release

- **E2E tests** run on every push and PR to `main` (`.github/workflows/e2e.yml`). Uses `concurrency: cancel-in-progress: true` — pushing again auto-cancels any in-progress E2E run for the same branch.
- **Releases** trigger on `v*.*.*` tags — runs E2E first, then creates GitHub Release with the `.ankiaddon` artifact (`.github/workflows/release.yml`)
- **Version** lives in `__init__.py` as `__version__`. Bump it there before tagging a release.

## Known Gotchas

### Media Security Boundary

All media inputs (file paths, URLs, filenames) must pass through `media_validators.py` before any I/O occurs. It enforces `media_import_dir` containment (path traversal prevention) and blocks private-network URLs (SSRF prevention). Custom error subclasses of `HandlerError` (`MediaFileTypeError`, `MediaImportDirError`, etc.) carry actionable hints for the AI client, while security-relevant details (resolved paths, MIME types, resolved IPs) are logged at WARNING level for the operator's audit trail. **Never bypass these validators** when adding media-related tools.

### Imports Must Be Relative

All imports in this addon use **relative imports** (e.g., `from ....tool_decorator import Tool`). This is the Anki addon ecosystem standard — AnkiConnect does this too. Absolute imports (`from anki_mcp_server.config import ...`) break AnkiWeb installs because AnkiWeb uses the addon ID (`124672614/`) as the directory name, not the package name.

### Anki Scheduler API Pitfalls

- `col.sched.deck_due_tree()` — correct way to get deck stats (AnkiConnect pattern). Tree nodes have: `deck_id`, `name`, `new_count`, `learn_count`, `review_count`, `total_in_deck` (Anki 2.1.46+)
- `col.sched.counts()` — returns (new, learning, review) for the **currently selected** deck
- `col.sched.counts_for_deck_today()` — does **NOT** work in modern Anki, silently returns wrong values
- Raw SQL (`col.db`) is acceptable for analytics/stats (revlog, card stats) — AnkiConnect does this too. For deck stats, always prefer `deck_due_tree()` over SQL.
- `col.sched.suspend_cards(ids)` → `OpChangesWithCount` (has `.count`), but `col.sched.unsuspend_cards(ids)` → `OpChanges` (no `.count`). Similar asymmetry: `set_due_date` and `schedule_cards_as_new` return `OpChanges`, not `OpChangesWithCount`.
- `col.tags.bulk_add(ids, tags)` and `col.tags.bulk_remove(ids, tags)` → `OpChangesWithCount`. `col.tags.clear_unused_tags()` → `OpChangesWithCount`. `col.tags.all()` → `list[str]`.
- `col.add_notes(requests: Iterable[AddNoteRequest])` → `OpChanges` — native batch API, single Rust backend call, atomic undo. Use `from anki.collection import AddNoteRequest`. Note IDs are assigned in-place on each `Note` object after the call. All-or-nothing at the backend level — pre-validate and filter before calling.

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
