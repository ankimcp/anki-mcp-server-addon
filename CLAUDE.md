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
make e2e-logs                   # Follow container logs (-f, interactive only — never in CI)
make e2e-logs-dump              # Dump last 2000 lines of container logs and exit (CI-safe)
make e2e-filtered               # Filtered tests only: build → Docker (port 3142) → test → teardown
make e2e-filtered-up            # Start filtered container (docker-compose.filtered.yml)
make e2e-filtered-test          # Run test_tool_filtering_e2e.py + test_model_fields_remove.py against port 3142
make e2e-filtered-down          # Stop filtered container
make e2e-filtered-logs-dump     # Dump last 2000 lines of filtered container logs and exit (CI-safe)
make e2e INSPECTOR_VERSION=latest       # Same cycle against the newest MCP Inspector (nightly canary does this)
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
│  - Event-driven drain (run_on_main)     │
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
├── credentials.py           # OAuth credentials file I/O (user_files/credentials.json)
├── mcp_server.py            # FastMCP server in background thread (HTTP via uvicorn)
├── queue_bridge.py          # Thread-safe request/response queue
├── request_processor.py     # Main thread handler dispatcher
├── handler_registry.py      # Maps handler names to functions
├── handler_wrappers.py      # Shared wrappers: _error_handler, _require_col, HandlerError
├── tool_decorator.py        # @Tool decorator implementation
├── resource_decorator.py    # @Resource decorator implementation
├── prompt_decorator.py      # @Prompt decorator implementation
├── dependency_loader.py     # Runtime pydantic_core download from PyPI
├── native_cache_cleanup.py  # Relocate/clean the out-of-folder native cache on uninstall + update
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
│       ├── settings_section.py  # Tunnel status/controls in settings dialog
│       └── toolbar_indicator.py # Persistent "● AnkiMCP" dot in Anki's top toolbar
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
    │   ├── resources/       # system_info, query_syntax, schema, stats, fsrs_config
    │   └── prompts/         # review_session, twenty_rules
    └── gui/tools/           # UI tools: browse, add_cards, edit_note, etc.
```

**Vendored Dependencies**: Located in `vendor/shared/`. The `__init__.py` prepends vendor path to `sys.path` at startup. On load, `_check_vendor_conflicts()` warns if any vendored packages (mcp, pydantic, starlette, uvicorn, etc.) are already in `sys.modules` from other addons — helps debug compatibility issues.

**Build (`package.sh`)**: Downloads wheels pinned to `--python-version 313` (Anki 25.07's Python). Deliberately **excludes `pydantic_core`** from the bundle — it has platform-specific binaries and is instead lazy-loaded at runtime via `dependency_loader.py`. If modifying the build, keep this exclusion intact.

**Keeping vendor lists in sync**: `requirements.txt` is the **vendoring source of truth** — `package.sh` runs `pip download -r requirements.txt`, so its top-level entries (plus transitive deps) are exactly what gets bundled. The `mcp>=1.27.0,<2` ceiling there pins the addon to the v1 MCP SDK (v2 is a breaking rewrite). When adding or removing a vendored dependency, update **both** `requirements.txt` (controls what gets downloaded/bundled) **and** `_VENDOR_PACKAGES` in `__init__.py` (controls conflict detection at startup). They must stay in sync.

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
- `write=True`: Calls `mw.reset()` after the handler runs, on success or error, so open deck browser/overview/reviewer screens refresh (`requireReset`/`maybeReset` are obsolete no-ops in current Anki and are not used)
- `require_col=True` (default): Checks collection is open before running
- `destructive=True`: Hides the tool from MCP clients unless the operator opts in via `enabled_destructive_tools` config (see "Tool Filtering"). Requires `write=True` — `ValueError` at import time otherwise. For multi-action tools, mark individual actions with `_destructive: ClassVar[bool] = True` on the action's Params model instead.

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

**Destructive actions**: An action can be marked destructive by adding `_destructive: ClassVar[bool] = True` to its Params model (in the dispatcher module, alongside `_tool_description`). Destructive actions are hidden from the schema unless the operator opts in via `enabled_destructive_tools` (e.g., `"my_tool:delete"`). Hiding reuses the same union-rebuild machinery as `disabled_tools`.

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
- `credentials.py` — credential file I/O only (addon-owned `user_files/credentials.json`). No auth, no network. Lives at `anki_mcp_server/credentials.py` (not in tunnel/) since it's used across the main addon.
- `auth.py` — async OAuth Device Flow HTTP calls via httpx. No WebSocket, no file I/O.
- `client.py` — single WebSocket connection lifecycle. Receives tunnel requests, proxies via `InMemoryTransport`, handles ping/pong. No retry logic.
- `reconnect.py` — retry/backoff wrapper around `TunnelClient`. Creates a fresh client + transport per attempt. This is the main entry point callers use.
- `in_memory_transport.py` — feeds JSON-RPC into `Server.run()` via anyio memory streams.
- `log.py` — thread-safe ring buffer with Qt signal for cross-thread UI updates.
- `ui/login_dialog.py` — Qt device flow dialog (user code + "Open Browser" button).
- `ui/settings_section.py` — tunnel status/controls in the settings dialog.
- `ui/toolbar_indicator.py` — always-present `● AnkiMCP` item in Anki's top toolbar; its dot color reflects tunnel state (grey=idle, amber=connecting/reconnecting, green=connected), clicking opens the settings dialog. Like `settings_section.py`, it derives state by polling `ConnectionManager` on a 1-second `QTimer` (the established state channel) rather than a Qt signal — this keeps core tunnel modules UI-free. Gated by the `show_toolbar_indicator` config field.

#### Configuration

These config fields control tunnel behavior:

- `http_enabled: bool = True` — when `False`, uvicorn doesn't start. Only tunnel transport is available. Toggle via the settings dialog checkbox.
- `tunnel_server_url: str` — WebSocket URL of the tunnel relay server. Default is `wss://tunnel.ankimcp.ai` (production). Point at `ws://localhost:3004` for local relay development.
- `tunnel_client_id: str` — OAuth client identifier. Default is `ankimcp-cli` (shared with the TypeScript CLI).
- `show_toolbar_indicator: bool = True` — when `True`, adds the persistent `● AnkiMCP` status dot to Anki's top toolbar (see `ui/toolbar_indicator.py`). Set `False` to hide it.

There is no `mode` field. HTTP is always-on by default (controlled by `http_enabled`). Tunnel never auto-connects — the user must explicitly click "Connect Tunnel" in the settings dialog each time.

#### Credential Storage

Credentials are addon-owned: stored in the addon's `user_files/credentials.json` (preserved across addon updates) with `0o600` file permissions, in a directory created at mode `0o700`. They are **not** shared with the TypeScript CLI — the CLI keeps its own credentials under `~/.ankimcp/`, so the addon and CLI authenticate independently. The on-disk format is identical to the CLI's `CredentialsService`, but there is no migration or read-fallback: a user who previously logged in via the CLI (or an older addon build that used `~/.ankimcp/`) must log in again from the addon. The `Credentials` dataclass holds `access_token`, `refresh_token`, `expires_at`, and `user` (with email and tier).

#### Settings Dialog

The settings dialog (*Tools -> AnkiMCP Server Settings...*) has:
- **HTTP section**: status display, URL, "Copy URL" button. Shows "Disabled" when `http_enabled=False`.
- **Tunnel section**: Connect/Disconnect button, status with user email + tier + URL expiry, Logout link.
- **Log section**: scrollable ring buffer of recent tunnel events (connections, requests, errors, auth).

Connect flow: check credentials -> no credentials? launch device flow login dialog -> credentials expired? silent refresh -> connect WebSocket -> show tunnel URL.

Separately, the **top-toolbar indicator** (`ui/toolbar_indicator.py`, gated by `show_toolbar_indicator`) lives outside this dialog — it sits in Anki's main `Decks | Add | Browse | Stats | Sync` strip and clicking it opens this dialog.

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

Version lives in `__init__.py:__version__`. Release process: bump version (BOTH hand-bumped literals — see below) → commit → push tag `v*.*.*` → `version-gate` job checks the tag against `__version__` (seconds, no Docker) → `test` job runs E2E → `release` job creates the GitHub Release with the `.ankiaddon` artifact → `registry` job publishes to the MCP registry.

There are exactly TWO versions to bump by hand, and they must be equal: `__version__` (the source of truth — it is what the tag is checked against and what the registry is published with) and the `version = "...";` literal in `flake.nix` (a consumer of that value; a stale one makes `nix build .#addon` produce a mislabelled package). `tests/unit/test_version_consistency.py` enforces the equality, and `unit-tests.yml` runs it on push to every branch, so forgetting the flake goes red immediately rather than shipping silently. It is a hand-maintained literal deliberately — deriving it in Nix was considered and rejected. Everything ELSE is derived or verified, never hand-synced. `server.json` also carries a `version`, but the schema makes it required, so it holds the sentinel `0.0.0-set-by-ci` — the `registry` job **overwrites it** from `__version__` before publishing, and NOTHING in the pipeline runs past the `version-gate` job unless the pushed tag equals `v{__version__}` — that gate is `needs`-upstream of `test`, so a mistyped tag fails in seconds instead of after a ~20-minute E2E run. `release` and `registry` still re-derive and re-check the version themselves, so re-running one job alone is verified on its own rather than trusting the gate's earlier verdict. Do not hand-sync `server.json` — it drifted to a stale `0.19.0` once and the registry silently rejected every release from 0.20.0 to 0.27.0 as a duplicate version. Version resolution + tag check live in `.github/scripts/resolve_version.py`, shared by every workflow that needs them so they cannot disagree.

**The registry result is verified, never inferred.** `mcp-publisher login` and `publish` are run, their exit codes are logged, and then **both are ignored**. The job's verdict comes from asking the registry what it actually has, via `.github/scripts/check_registry_version.py`:

`GET https://registry.modelcontextprotocol.io/v0.1/servers/{name url-encoded}/versions/{version}`

- HTTP 200 + matching `name`/`version` + `_meta["io.modelcontextprotocol.registry/official"].status == "active"` → **green**.
- HTTP 404 → **red**: the registry accepted nothing.
- Anything else (transport error, 5xx, 429, unparseable body) → **red**: could not verify, re-run the job.

The `%2F` encoding of the `/` in the server name is mandatory (an unencoded slash 404s), and the exact-version endpoint is used deliberately instead of `?search=`, which is nginx-cached with a 30s TTL and no purge-on-publish and would additionally paginate out of correctness over time.

Do NOT reintroduce error-text classification. Three successive attempts to grade the release by pattern-matching the publisher's output all failed open; the last one because the registry's DNS-auth 401 body embeds the Go resolver's `lookup <domain> on 34.118.224.10:53: no such host`, which any "is this a transport failure?" allowlist reads as an outage. The strings belong to someone else and can be reworded at any time. This design also makes the job idempotent: on a re-run after a successful publish the publisher fails with a duplicate-version error, that is ignored, and verification finds the version live and passes.

**Job split.** `release` (builds + publishes the GitHub Release, `contents: write`) and `registry` (`needs: release`, `contents: read`) are separate jobs so a registry problem can be re-run alone. That matters because `package.sh` does a fresh `pip download` on every build with only `mcp` upper-bounded, so a rebuild of the same tag is not byte-identical — re-running the `release` job would otherwise replace an `.ankiaddon` users may already have downloaded. Two guards: the `registry` job cannot touch the Release (read-only token), and `Create Release` sets `overwrite_files: false` so even an accidental "Re-run all jobs" leaves the published asset alone.

**Drift check.** `.github/workflows/registry-drift.yml` runs weekly (and on demand), reads `__version__` off the default branch and does the same exact-endpoint GET. On anything but a clean yes it opens a `registry-drift`-labelled issue — deduped to at most one open issue at a time — and fails the job. This exists because nothing ever compared registry state to repo state, and that gap is what cost two months.

### Source Install Mode (Nix)

When installed from source (Nix, pip), vendored packages aren't used. `__init__.py` sets `_USING_SYSTEM_PACKAGES = True` and skips vendor path setup + conflict detection. The flag is toggled by checking whether system packages are importable before prepending the vendor path.

### No Linter / Formatter

This project has no pyproject.toml, ruff, flake8, or any configured linter. Don't try to run lint commands — they won't work.

### Profile Lifecycle

- Server starts on `profile_did_open` hook (HTTP auto-starts if `http_enabled`, tunnel never auto-starts)
- Server stops on `profile_will_close` hook (both HTTP and active tunnel)
- Fallback cleanup on `app_will_close`

### pydantic_core / rpds Runtime Loading

`pydantic_core` is lazy-loaded from PyPI at runtime via `dependency_loader.py` because it contains platform-specific binaries that can't be bundled in a single addon file.

`rpds` (from `rpds-py`) is a second native dependency handled by the same loader (`_ensure_rpds_with_callbacks`, wired in `__init__.py`), but it is almost never downloaded: Anki ships `rpds` transitively via its own `jsonschema`, so the common path is a plain `import rpds` with no network access. Both are cached under a resolved cache root (`dependency_loader._resolve_cache_root()`) — see "Native Dependency Cache Location" below for where that is, and "Background-thread / dependency-load resilience" below for the hardened load/retry/atomic-swap behavior shared by both.

### Native Dependency Cache Location

The pydantic_core/rpds cache does NOT always live inside the addon's own folder. Verified on a real Windows 11 VM:

- A loaded native extension (`.pyd`) can be **renamed/moved** on the same volume while the process holds it open, but it **cannot be deleted**.
- Anki updates and uninstalls an addon by **deleting its whole folder** (`aqt/addons.py` `_install` / `AddonsDialog.onDelete`). If the cache — and its locked `.pyd` — lives inside that folder, the delete dies halfway through and guts the addon (hundreds of files gone, `vendor/` emptied).

So the cache root is resolved per ensure-call by `dependency_loader._resolve_cache_root()`:
- **Preferred**: `<mw.pm.base>/ankimcp/<addon_folder_name>/cache` — outside the addon folder entirely, keyed by the installed folder name (`124672614` on AnkiWeb, `anki_mcp_server` from source) for the same one-cache-per-installed-copy isolation the old in-folder cache gave for free.
- **Fallback**: the legacy in-folder `CACHE_DIR` (`anki_mcp_server/_cache/`), used when `mw`/`mw.pm.base` isn't available or the preferred directory can't be created/written to.

`_resolve_cache_root()` only **decides** a root — it has no destructive side effects, and never touches the legacy `_cache/` dir itself. The legacy in-folder cache is removed only by `dependency_loader._cleanup_legacy_cache_dir_if_out_of_folder()`, called from `_ensure_pydantic_core_with_callbacks` / `_ensure_rpds_with_callbacks` **after a SUCCESSFUL outcome only** — i.e. only once the dependency has actually loaded (import or download) from the preferred out-of-folder root. A failed download to the preferred root (e.g. offline) therefore leaves the legacy cache exactly as it was, so it's still available as a working fallback on the next attempt; deleting it eagerly (the pre-fix behavior) could leave a user with no usable cache at all. On top of that, cleanup is a no-op whenever the legacy `CACHE_DIR` (or any path nested under it) is present on `sys.path` in this process — meaning a native extension may already be loaded from it — since deleting around a locked `.pyd`/`.so` in that case would recreate the exact gutting bug this relocation exists to avoid.

Since the cache is no longer guaranteed to be deleted along with the addon folder, `native_cache_cleanup.py` registers TWO hooks to clean it up / get it out of the way explicitly:

- `on_addons_dialog_will_delete_addons` on `gui_hooks.addons_dialog_will_delete_addons` — fires **only for deletion triggered from the Add-ons dialog** (uninstall). It is **cleanup-only** and never touches the download/retry logic:
  - POSIX: deletes the cache directories outright.
  - Windows: **renames** each into the temp dir (`os.rename`, unique `ankimcp-cache-<addon_folder_name>-<unix_ts>` name) instead of deleting, since the `.pyd` may still be loaded. Deliberately never `shutil.move` — its cross-volume fallback is copy-then-delete, which can copy the open `.pyd` successfully and then fail to delete the source, leaving BOTH behind instead of one. A failed `os.rename` (e.g. WinError 17, temp dir on another volume) is swallowed and the target is left in place.
- `on_addon_manager_will_install_addon` on `gui_hooks.addon_manager_will_install_addon` — closes the update-path gap: Anki's own update path (`download_addons` → `install` → `_install` → `backupUserFiles` → `deleteAddon`) never fires `addons_dialog_will_delete_addons`, so an install with a legacy in-folder cache still present was still exposed to the original Windows update failure. This hook fires right before `_install`, only acts when `module` is our own add-on folder name, and does two things in order: (1) on Windows only, relocates the **legacy** in-folder cache (`relocate_legacy_cache_before_update`, which reuses the exact same `_remove_or_relocate` primitive the delete hook uses) **whenever that directory exists** — there is no "fallback mode" gate, presence of the directory is the only signal checked; (2) releases the file-log handle via `file_log.release_log_handle` (see "Diagnostic File Logging" below) so it can't block `backupUserFiles`'s rename of `user_files`. Relocation runs FIRST and log release LAST, deliberately, so the handler's own INFO/WARNING lines about the relocation still land in the file before the handle closes. It NEVER touches the out-of-folder cache — that lives outside the folder being replaced and survives updates untouched. For AnkiWeb updates this hook runs on Anki's **background thread** (`taskman.run_in_background`), so it stays Qt/UI-free. Relocation deliberately does NOT apply the `_legacy_cache_in_use()` guard that gates `dependency_loader._cleanup_legacy_cache_dir`'s *deletion* of the legacy cache: that guard exists to avoid deleting around a loaded `.pyd`/`.so` (a delete of an open native extension fails outright on Windows), but this path *renames* the cache, which Windows permits even while the extension is loaded — renaming a possibly-loaded cache before Anki's own delete is the whole point of this hook.

Both hooks are wrapped so no exception can escape them: Anki drops a hook callback that raises (and never re-invokes it), so a raise here would look like nothing happened; on `addon_manager_will_install_addon` specifically, a re-raise aborts Anki's **whole batch update of every add-on**, not just ours. The registration itself lives in `__init__.py` **before** the `ensure_pydantic_core()` / `ensure_rpds()` gates on purpose: a gutted add-on (native extension deleted/moved mid-update on Windows) aborts import at one of those gates, and the hooks need to already exist at that point so the user's next uninstall/reinstall from the Add-ons dialog can still clean up the orphaned out-of-folder cache.

**Known residual gotcha**: the update TO the first version carrying the install-hook fix still fails once on Windows, because the OLD (already-loaded) code doesn't have the hook yet to release its own lock. Verified workaround: disable the addon → restart Anki → Check for Updates → re-enable → restart again. Every update after that first one is covered by the hook.

`dependency_loader._get_mw()` reads `sys.modules["aqt"]` instead of importing `aqt` — this keeps the standalone-module path (`spec_from_file_location`, e.g. the `anki-compat.yml` CI smoke script and the unit-test loaders) from dragging PyQt6 into a process that never otherwise touched Qt. Inside real Anki, `aqt` is always already imported before add-ons load, so behaviour there is unchanged.

### DNS Rebinding Protection

ENABLED on the HTTP transport with a loopback Host/Origin allowlist, built in `transport_security_config.build_transport_security(config)` and passed to `FastMCP` in `mcp_server.py`. The defaults (`DEFAULT_ALLOWED_HOSTS` / `DEFAULT_ALLOWED_ORIGINS`) mirror the MCP SDK loopback auto-default so ordinary localhost clients keep working. To expose the HTTP server through a tunnel/reverse proxy (Cloudflare, ngrok), operators ADD their hostname/origin to the `http_allowed_hosts` / `http_allowed_origins` config fields (which are appended to the defaults) — NOT by disabling protection. The tunnel (WebSocket) transport bypasses HTTP middleware entirely and is unaffected by this policy.

### CORS Configuration

Configured via addon settings (`cors_origins`, `cors_expose_headers`). Empty `cors_origins` = CORS disabled. The `mcp-session-id` and `mcp-protocol-version` headers must be exposed for browser-based MCP clients (Streamable HTTP protocol requirement). See `config.py` for the full `Config` dataclass. CORS only applies to the HTTP transport — the tunnel path bypasses HTTP entirely.

### API Key (Optional HTTP Auth)

`Config.http_api_key` (default `""`) adds an OPTIONAL shared-secret auth layer on the HTTP transport, gated by a non-empty value. Implemented in `http_auth.py`: the pure `is_authorized` helper (constant-time `hmac.compare_digest`, Bearer scheme) plus `ApiKeyAuthMiddleware`, a RAW ASGI middleware — NOT `BaseHTTPMiddleware`, which would buffer the full response and break SSE (`text/event-stream`) streaming. Wired in `mcp_server.py` `_run_http_mode`, applied to the MCP app FIRST so CORS ends up OUTERMOST (CORS → Auth → MCP); empty key = middleware not applied. ALL http methods (including `OPTIONS`) require the key — genuine CORS preflight is short-circuited by the outer CORS layer and never reaches auth. On failure it returns `403` with NO `WWW-Authenticate` header (never `401`, which would trigger MCP OAuth 2.1 discovery and fail confusingly). HTTP-only: it does not touch the tunnel / in-memory transport path, which has its own OAuth. `validate_http_api_key` (advisory, never rejects) warns on a whitespace-bearing key (never authenticates) or a short/weak key, wired into the startup-validation site in `__init__.py` alongside `validate_http_allowlist`.

### HTTP Path Prefix (Secret Path)

`Config.http_path` (default `""`) lets the operator move the MCP endpoint off `/` to an obscure prefix like `"my-secret"` → served at `/my-secret/`. Used for security-through-obscurity when exposing the server through a tunnel. Normalization happens in `mcp_server.py` (`streamable_path = f"/{http_path.strip('/')}/"`). Tests live in `tests/e2e/test_secret_path.py`.

### Diagnostic File Logging (`file_log.py`)

`Config.log_to_file` (default `False`, opt-in) enables a rotating log at `user_files/ankimcp.log` (`RotatingFileHandler`, ~1MB x 3 backups). `file_log.py` is **STDLIB-ONLY** by design — it must survive failures in the vendored layer (pydantic/mcp/...), so it must never import a vendored/third-party package. It is initialized in `__init__.py` **before** vendor-path setup and `ensure_pydantic_core()`, reading the flag via `_read_log_to_file_flag()` (stdlib-only: tries `mw.addonManager.getConfig`, falls back to reading `config.json` + `meta.json` off disk).

Key pieces:
- **Redaction**: `_RedactingFilter` masks the `http_api_key`, OAuth tokens, and any `Bearer <token>` before a record hits disk. Exact secrets are registered at runtime via `register_secret()` (api key in `_on_profile_opened`, tokens in `CredentialsManager.load/save`); `Bearer` tokens are masked structurally by regex. Never let a secret reach the file.
- **Diagnostics snapshot**: `build_diagnostics_snapshot()` is the SINGLE source of truth for the startup-log block AND the "Copy diagnostics" settings button. It reports addon/Anki/Qt/Python versions and, for each shared lib (pydantic, pydantic_core, typing_extensions, mcp, starlette, uvicorn, google.protobuf, certifi, urllib3), inspects `sys.modules` WITHOUT force-importing — reporting `__version__`/`__file__` of the already-loaded copy (provenance for cross-add-on conflict diagnosis). Logged twice: pristine at `startup`, again `post-dependency-load`. Every field is gathered defensively.
- **Logger naming**: the handler attaches to the addon's top-level logger (`__name__.split(".")[0]`), so every `logging.getLogger(__name__)` in the addon propagates into the file. The same handler instance is additionally attached to the explicit `_FORWARDED_LOGGER_NAMES` list (currently just `mcp.server.transport_security`) so the vendored SDK's DNS-rebinding-protection Host/Origin rejections reach the file even though that logger doesn't propagate into ours. The root logger is still untouched (no unrelated Anki logging captured).

The settings dialog embeds `DiagnosticsSection` (a QWidget like `TunnelSettingsSection`) with "Open log folder" (`QDesktopServices.openUrl`) and "Copy diagnostics" buttons. UI stays out of `file_log.py`; the snapshot builder lives in the stdlib-only module and is called from both startup and the button.

**Releasing the handle**: `file_log.release_log_handle(reason)` logs `reason` then tears the handler down (`init_file_logging(enabled=False, ...)` — idempotent, never raises). Two call sites use it: (1) `__init__.py`'s dependency-gate abort branches (`ensure_pydantic_core()` / `ensure_rpds()` failing, and the vendor-package-missing branch in `_setup_vendor_path()`) call it right before raising `ImportError`, so the abort reason still reaches the file but the handle is closed before the add-on gives up; (2) `native_cache_cleanup.on_addon_manager_will_install_addon` calls it before Anki's own install/update path runs `backupUserFiles` (an `os.rename` of `user_files`), because an open `ankimcp.log` blocks that rename on Windows and fails every update retry until the user deletes the folder by hand. Both call sites treat a failure in the release itself as non-fatal — it's wrapped so it can never mask the original failure it's cleaning up after.

The teardown branch (`init_file_logging(enabled=False, ...)`) makes the handler inert — `setLevel(logging.CRITICAL + 1)` — **before** `removeHandler`/`close()`, not after. This runs on a background thread while other code may still be logging: `Logger.callHandlers` can already hold a reference to the handler between our `removeHandler()` and `close()` calls, and `FileHandler.emit()` **re-opens** its file whenever `self.stream is None` (our mode is `'a'`, not `'w'`) — exactly the state `close()` leaves it in. Raising the level first makes `callHandlers`' `record.levelno >= hdlr.level` check reject the record before `emit()` can run, closing that race before it can silently recreate the very file handle being released.

**A deliberate trade-off**: once `on_addon_manager_will_install_addon` releases the handle, file logging stays OFF for the rest of that Anki session — nothing re-attaches it, even if the update then fails for an unrelated reason. Restarting Anki is what re-initializes it. This is intentional: re-attaching mid-session would reopen exactly the handle this hook exists to release, and the failure mode being guarded against (a blocked `os.rename` on Windows) is worse than a temporarily quiet log.

### Background-thread / dependency-load resilience

- The MCP server's background daemon thread target (`mcp_server.py` `_run`) wraps `asyncio.run(_async_main())` in a `try/except BaseException` that logs the full traceback (`exc_info=True`). Without it, any exception — setup phase (building FastMCP, registering tools) or serve phase (uvicorn) — would silently kill the thread, leaving a client to hang. Mirrors the existing `_run_tunnel` guard.
- `dependency_loader.py` hardens the cached-`pydantic_core`/`rpds` load: a **pre-flight `open()`** of the native `.pyd`/`.so` surfaces lock/permission problems as an `OSError` with a real `winerror`/`errno` (a bare `import` masks them as an errno-less `ImportError`). `_classify_native_load_error` maps WinError 32 → locked (transient), 5 → access-denied, ENOENT → missing. `_import_with_lock_retry` retries ONLY the lock class with short exponential backoff (~50/100/200/400ms), never access-denied or missing. Re-downloads use an **atomic temp-swap** (`_atomic_swap_dir`): extract into a fresh sibling `.tmp-*` dir, write markers there, then `os.replace`-swap into the cache (moving any existing dir aside first, restoring it if the swap fails). A failed re-download therefore NEVER destroys an existing good cache.
- **Download timeout + bounded retry + overall budget** (issue #73): the PyPI metadata request and the wheel fetch each get a `_NETWORK_TIMEOUT_SECONDS` (30s) `urlopen` timeout — but that bounds only a SINGLE socket operation, not the whole download: a server trickling one byte every 29s never trips it. On top of the per-socket timeout there are two more layers:
  - Up to `_DOWNLOAD_MAX_ATTEMPTS` (3) attempts per request with a sliced, UI-pumping, cancellable backoff (`_wait_with_ui_pump`, polling every `_BACKOFF_POLL_INTERVAL_SECONDS` = 0.05s) between them, via the shared `_retry_network_call` helper. The backoff is sliced (not a single blocking `time.sleep`) because a plain sleep freezes Qt: `QProgressDialog.wasCanceled()` only reflects a click once an event-loop pump delivers it, so a single long sleep makes Cancel unresponsive for the whole backoff.
  - A single overall wall-clock budget, `_DOWNLOAD_TOTAL_BUDGET_SECONDS` (180s), covering the ENTIRE network phase (metadata fetch + all its retries + wheel fetch + all its retries), captured once at the start as a deadline and checked after every `read1()` call, before every retry attempt, and inside the sliced backoff wait. Both fetches read via `response.read1(_DOWNLOAD_CHUNK_SIZE)`, not `response.read(n)`: `read(n)` LOOPS on the socket until it has the full `n` bytes or EOF — a server trickling one byte every 29s would let a single `read(65536)` call block for the entire download, and the budget check between calls would never run. Each underlying socket operation inside a `read1()` call is bounded by the 30s socket timeout, but "at most one such operation per call" is exact only for a NON-CHUNKED response — a CHUNKED response's `read1()` can cost a small constant number of additional socket reads (trailing CRLF, next chunk-size line, data), each independently bounded the same way. Exceeding the budget raises `_DownloadBudgetExceeded` — a dedicated exception, deliberately NOT a `TimeoutError`, so the retry classifier never retries it. It is TERMINAL: same cleanup as any other failure, plus a clear user-facing message ("download timed out after N seconds...").

    Concretely: a dead connection that never accepts a socket read fails fast, in roughly `3 × 30s + 1s + 2s` ≈ 93s (three timed-out attempts plus the two backoff waits between them) before the error dialog appears — well under the 180s budget. Worst case is the budget plus one in-flight `read1()` call for a plain response — roughly `180s + 30s` ≈ 210s — and up to roughly `180s + 90s` ≈ 270s for a chunked response, whose `read1()` can span more than one bounded socket read.

    A truncated body — a server that advertises a `Content-Length` and then closes early — is caught explicitly: both fetches compare the received byte count against the header (when present) and raise `urllib.error.ContentTooShortError` on any mismatch, short or long. It's a `URLError` subclass, so it's retried like any other network-class failure, restoring the detection `urllib.request.urlretrieve` used to provide (`if size >= 0 and read < size: raise ContentTooShortError`) before it was replaced by the chunked read above. Without it, `read1()` returning `b""` on an early close looks identical to a clean EOF, and the truncated wheel would only be caught later by `zipfile` as a non-network-class `BadZipFile` that is never retried. The check is skipped when the response is chunked (`response.chunked`): `http.client` ignores `Content-Length` for a chunked body's framing, so a non-conformant server sending both headers must not have the irrelevant header used to reject a good body — the header is still used for the progress percentage, which is clamped.

  The wheel fetch itself is a chunked `urlopen` read (`_fetch_wheel_bytes`, `_DOWNLOAD_CHUNK_SIZE` = 64KB) rather than `urllib.request.urlretrieve`, which has no timeout at all. `yield_ui()` fires on every chunk unconditionally (not just when `Content-Length` is known), so Cancel stays responsive even against a server that never sends that header.

  Retries are strictly network-class-only (`_is_retryable_network_error`): timeouts (`TimeoutError`, which `socket.timeout` has aliased since Python 3.10), `ConnectionError` (reset/aborted), HTTP 5xx, `ssl.SSLError` (raised mid-read, a plain `OSError` subclass — not caught by the `ConnectionError`/`URLError` checks), and `http.client.IncompleteRead` (server closed before delivering the promised bytes). NEVER retried: a user cancel, an HTTP 4xx, a version mismatch, a wheel-selection failure, `_DownloadBudgetExceeded`, and — deliberately — generic `OSError` (a full disk during the write must fail fast, not retry). A partial wheel file from a failed/cancelled/budget-exceeded attempt is discarded, never reused on the next attempt.

### Transports (No Mode Enum)

There is no `Config.mode` field and no `is_valid_for_mode()` — the addon does not model connectivity as a single selectable "mode." Instead it has two **independent** transports that can each be on or off at the same time:

- **HTTP** — gated by `http_enabled` (default `True`). When enabled, uvicorn serves the Streamable HTTP endpoint.
- **Tunnel** — never auto-started. The user explicitly clicks "Connect Tunnel" in the settings dialog; configured via `tunnel_server_url` / `tunnel_client_id`.

Both share one `Server` object and run on the same asyncio loop (see "Tunnel Architecture"). Don't add `mode`-based conditionals — branch on `http_enabled` and the tunnel's connection state instead.

### Tool Filtering

`disabled_tools` config hides tools/actions from AI clients. Supports whole-tool (`"sync"`) and per-action (`"card_management:bury"`) granularity. Typos produce `print()` warnings visible in Anki's console. See `tool_decorator.py` for implementation.

**Destructive tools (opt-in)**: Tools declared with `@Tool(..., destructive=True)` — or actions whose Params model sets `_destructive: ClassVar[bool] = True` — are **hidden from `tools/list` by default**. The operator must opt in via the `enabled_destructive_tools` config allow-list (server-side enforcement, not just an advisory hint). Semantics:
- Exact match, same syntax as `disabled_tools`: `"tool"` opts in a whole-tool-destructive tool; `"tool:action"` opts in a destructive action. A whole-tool entry does NOT implicitly opt in destructive sub-actions.
- Composes with `disabled_tools`: destructive-not-opted-in is always hidden; `disabled_tools` applies on top (an opted-in tool/action can still be disabled).
- `destructive=True` requires `write=True` (`ValueError` at import time otherwise).
- Startup validation (`validate_enabled_destructive_tools`) warns on typos and on no-op entries that name a real but non-destructive tool/action.
- Shipped destructive primitives: `change_note_type` is the first whole-tool destructive tool (`@Tool(..., destructive=True)`), and `model_fields:remove` is the first destructive action (`_destructive: ClassVar[bool] = True` on its Params model). Both stay hidden until named in `enabled_destructive_tools`.

### Pending Full-Sync Flag (`schema_state.py`)

Every model-mutating tool's success payload carries `will_force_full_sync` (`RESULT_KEY`), added by `attach_full_sync_flag(result, col)`. Contract:

- It reports the **actual post-write collection state** (`Collection.schema_changed()`, i.e. `scm > ls`), never a hardcoded per-tool guess.
- The flag is **collection-wide and sticky**: once anything marks the schema modified it stays `True` until a full sync clears it. So a tool that changed nothing schema-wise can still legitimately report `True`, and no tool may claim `False` on its own authority.
- Reads are **fail-safe**: an unexpected failure logs and resolves to `True` (an unnecessary precautionary sync beats an overwritten device).
- Attach at **one site per tool — the dispatch point** — so a new success path can't silently omit the key.
- Which operations dirty the schema: field ordinal changes (`add` / `remove` / `reposition`), `change_note_type`. A pure **`rename` does NOT** — rslib's `schemachange.rs` only calls `set_schema_modified` when ordinals move. Adding a new notetype doesn't either.
- `FULL_SYNC_FLAG_DOC` is the single shared description snippet; append it rather than paraphrasing the caveat per tool.

### Patch Mode (`_patch_helpers.py`)

`update_model_styling`, `update_model_templates` and `update_note_fields` each accept an alternative `old_str`/`new_str` input mode alongside their full-content parameters. The shared contract lives in `_patch_helpers.py`:

- `old_str` must match **exactly once** (`str.count`, non-overlapping). Zero matches = stale client view, more than one = ambiguous; both raise `HandlerError` and write nothing.
- The zero-match error embeds a short excerpt of the current content anchored near the closest partial match, so the caller can re-sync without an extra read call.
- `select_mode` enforces that full-content and patch params are mutually exclusive (presence-based, so `{}` selects full mode and is rejected by `reject_empty_full_input`); `require_patch_pair` rejects a half-specified patch.
- **JSON-literal caveat** (`JSON_LITERAL_CAVEAT`): FastMCP pre-parses string args whose annotation isn't exactly `str`. Our patch params are `str | None`, so a value that is entirely valid JSON (`null`, or a whole array/object) is `json.loads`'d in transit — `null` arrives as `None`, a list/dict fails validation. The vendored SDK isn't ours to patch, so the tool descriptions tell callers to include an adjacent context character.

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

**Two test suites**: `make e2e` runs both the regular suite (port 3141, all tools enabled) and the filtered suite (port 3142, `docker-compose.filtered.yml`, which sets both `disabled_tools` and `enabled_destructive_tools`). `make e2e-filtered-test` runs `test_tool_filtering_e2e.py` **and `test_model_fields_remove.py`** — the latter is not a filtering test, it just needs the filtered container's `enabled_destructive_tools: ["model_fields:remove"]` to see the destructive action at all (it self-skips on port 3141). Both are excluded from `make e2e-test`.

**MCP Inspector pin**: the test client is pinned to an exact npm version, in **three literals that must stay in sync** — `INSPECTOR_DEFAULT_VERSION` in `tests/e2e/helpers.py` (used by the suite), and in the `Makefile` both `INSPECTOR_VERSION ?=` and the `override INSPECTOR_VERSION :=` empty-env fallback (used by the readiness probes). `tests/unit/test_version_consistency.py` asserts all three agree. Both files honour an `INSPECTOR_VERSION` environment variable, and `make` exports it, so `make e2e INSPECTOR_VERSION=latest` moves both at once. The pin buys reproducibility; the cost — finding upstream breakage late — is paid by `.github/workflows/e2e-inspector-latest.yml`, a nightly that reuses `e2e.yml` via `workflow_call` with `latest`. A breaking Inspector release therefore shows up as a red canary, not as a blocked release. The suite needs Inspector **2.x** specifically: a tool error must exit non-zero, write a `{"error":{"code":...}}` envelope to stderr, and still write the result envelope to stdout.

**Environment variables:**
- `MCP_SERVER_URL` — override server URL (default: `http://localhost:3141`)
- `E2E_MAX_WAIT` — wall-clock seconds to wait for server readiness (default: `60`)
- `E2E_KEEP_RUNNING` — set to `1` to keep container running after tests
- `INSPECTOR_VERSION` — npm version/dist-tag of the Inspector CLI (default: the pin; empty = the pin)

**Server readiness**: `conftest.py` has a `session`-scoped `wait_for_server` fixture that polls the server before any tests run — no need to manually wait. `E2E_MAX_WAIT` is a **wall-clock budget measured against `time.monotonic()`, not an attempt count**: it used to be `range(MAX_WAIT_SECONDS)`, i.e. 60 attempts, and one attempt can cost ~111s (helpers.py's 45s Inspector timeout + a silent-timeout retry + the drain), so the real ceiling was ~110 minutes. The budget bounds when polling STOPS STARTING, so the true worst case is the budget plus one in-flight probe.

**Docker setup** (`.docker/`): The `docker-compose.yml` mounts `config.json` that binds the MCP server to `0.0.0.0` inside the container (instead of the default `127.0.0.1`) so the host can reach port 3141. It also mounts a custom `entrypoint.sh` that installs the `.ankiaddon` and starts headless Anki. CI pins `ghcr.io/ankimcp/headless-anki:qt-vnc-v1.0.0` — the tag appears in FOUR files (both compose files, `e2e.yml`, `release.yml`) and `tests/unit/test_version_consistency.py` asserts they agree, so CI can't pull one image while compose runs another.

**Debugging failed tests:**
- `make e2e-debug` — keeps container running after start; VNC available at `localhost:5900`
- `make e2e-logs` — follow Docker container logs (`-f`; interactive only, it would hang CI)
- `make e2e-logs-dump` / `make e2e-filtered-logs-dump` — dump the last 2000 lines and exit (what CI uses)
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

- **E2E tests** run on every push and PR to `main` (`.github/workflows/e2e.yml`). Uses `concurrency: cancel-in-progress: true` — pushing again auto-cancels any in-progress E2E run for the same branch. The concurrency group includes `github.workflow`, so a nightly canary run and a push to the same ref cannot cancel each other. It also takes an `inspector_version` input on BOTH `workflow_call` (the nightly canary) and `workflow_dispatch` (re-testing one Inspector version by hand from the Actions UI) — the same input declared twice, read through the single `inputs` context, empty on push/PR.
- **Releases** trigger on `v*.*.*` tags (`.github/workflows/release.yml`): `version-gate` (tag == `v{__version__}`, seconds) → `test` → `release` (GitHub Release + `.ankiaddon`) → `registry` (MCP registry publish + outcome verification). The last two are separate jobs on purpose — see "Versioning & Releases".
- **Nightly Inspector canary** (`e2e-inspector-latest.yml`) reuses `e2e.yml` with `INSPECTOR_VERSION=latest`. Expected to go red occasionally; NOT a merge gate.
- **Weekly registry drift check** (`registry-drift.yml`) compares the registry against `__version__` and files a deduped issue on mismatch.
- **Anki compatibility canary** (`anki-compat.yml`) tests the import chain daily against the latest `aqt`, incl. betas.
- **Version** lives in `__init__.py` as `__version__`. Bump it there before tagging a release.
- Scheduled workflows only run from the **default branch** — new cron triggers do nothing until merged to `main`; use `workflow_dispatch` in the meantime.

## Known Gotchas

### Media Security Boundary

All media inputs (file paths, URLs, filenames) must pass through `media_validators.py` before any I/O occurs. It enforces `media_import_dir` containment (path traversal prevention) and blocks private-network URLs (SSRF prevention). Custom error subclasses of `HandlerError` (`MediaFileTypeError`, `MediaImportDirError`, etc.) carry actionable hints for the AI client, while security-relevant details (resolved paths, MIME types, resolved IPs) are logged at WARNING level for the operator's audit trail. **Never bypass these validators** when adding media-related tools.

Operator-facing config knobs (all in `config.py`):
- `media_import_dir` — restrict file-path imports to this directory (empty = no restriction)
- `media_allowed_types` — extra MIME types beyond the built-in image/audio/video set
- `media_allowed_hosts` — hosts/IPs allowed to bypass the private-network block (for `192.168.x.x` NAS-style setups)

### Cloze answers in rendered questions

`card.question()` / `card.render_output().question_text` embed the cloze deletion's answer verbatim in a `data-cloze="..."` attribute on the `<span class="cloze">` — rendering alone does not hide it from an AI client. Every tool that hands a question to an AI client must render through `primitives/essential/tools/_render_helpers.py`, which strips that attribute. Never call `card.question()` directly in a tool.

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
