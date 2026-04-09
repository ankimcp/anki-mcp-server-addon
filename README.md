# AnkiMCP Server (Addon)

An Anki addon that exposes your collection to AI assistants via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/).

## What is this?

AnkiMCP Server runs a local MCP server inside Anki, allowing AI assistants like Claude to interact with your flashcard collection. This enables AI-powered study sessions, card creation, and collection management.

Part of the [ankimcp.ai](https://ankimcp.ai) project.

## Note on First Run

On first run, this addon downloads `pydantic_core` (~2MB) from PyPI. This is required because pydantic_core contains platform-specific binaries (Windows/macOS/Linux) that cannot be bundled in a single addon file.

## Features

- **Local HTTP server** - Runs on `http://127.0.0.1:3141/` by default
- **MCP protocol** - Compatible with any MCP client (Claude Desktop, etc.)
- **Auto-start** - Server starts automatically when Anki opens
- **Tunnel-friendly** - Works with Cloudflare Tunnel, ngrok, etc.
- **Cross-platform** - Works on macOS, Windows, and Linux (x64 and ARM)

## Installation

### From AnkiWeb (recommended)

1. Open Anki and go to *Tools → Add-ons → Get Add-ons...*
2. Enter code: `124672614`
3. Restart Anki

### From GitHub Releases

1. Download `anki_mcp_server.ankiaddon` from [Releases](https://github.com/ankimcp/anki-mcp-server-addon/releases)
2. Double-click to install, or use *Tools → Add-ons → Install from file...*
3. Restart Anki

### NixOS

#### With flakes (recommended)

Add the flake input and use the pre-built package:

```nix
# flake.nix
{
  inputs.anki-mcp.url = "github:ankimcp/anki-mcp-server-addon";

  outputs = { nixpkgs, anki-mcp, ... }: {
    # Option A: Standalone — Anki with the addon pre-installed
    environment.systemPackages = [
      anki-mcp.packages.${system}.default
    ];

    # Option B: Composable with other addons via overlay
    nixpkgs.overlays = [ anki-mcp.overlays.default ];
    environment.systemPackages = [
      (pkgs.anki.withAddons [ pkgs.ankiAddons.anki-mcp-server ])
    ];
  };
}
```

#### Without flakes

```nix
# configuration.nix
{ pkgs, ... }:
let
  python3 = pkgs.python3;

  ankiMcpPythonDeps = python3.withPackages (ps: with ps; [
    mcp pydantic pydantic-settings starlette uvicorn anyio httpx websockets
  ]);

  anki-mcp-server = pkgs.anki-utils.buildAnkiAddon (finalAttrs: {
    pname = "anki-mcp-server";
    version = "0.13.0";
    src = pkgs.fetchFromGitHub {
      owner = "ankimcp";
      repo = "anki-mcp-server-addon";
      rev = "v${finalAttrs.version}";
      hash = ""; # nix will tell you the correct hash on first build
    };
    sourceRoot = "${finalAttrs.src.name}/anki_mcp_server";
  });

  ankiWithMcp = pkgs.anki.withAddons [ anki-mcp-server ];

  ankiWrapped = pkgs.symlinkJoin {
    name = "anki-with-mcp";
    paths = [ ankiWithMcp ];
    nativeBuildInputs = [ pkgs.makeWrapper ];
    postBuild = ''
      wrapProgram $out/bin/anki \
        --prefix PYTHONPATH ':' "${ankiMcpPythonDeps}/${python3.sitePackages}"
    '';
  };
in
{
  environment.systemPackages = [ ankiWrapped ];
}
```

## Usage

The server starts automatically when you open Anki. Check status via *Tools → AnkiMCP Server Settings...*

### Connect with Claude Desktop

Requires [Node.js](https://nodejs.org/) installed. Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "anki": {
      "command": "npx",
      "args": ["mcp-remote", "http://127.0.0.1:3141"]
    }
  }
}
```

> **Note:** Claude Desktop doesn't natively support HTTP servers in its JSON config — `mcp-remote` bridges the connection via stdio.

### Connect with Claude Code

```bash
claude mcp add anki --transport http http://127.0.0.1:3141/
```

## Configuration

Edit via Anki's *Tools → Add-ons → AnkiMCP Server → Config*:

```json
{
  "mode": "http",
  "http_port": 3141,
  "http_host": "127.0.0.1",
  "http_path": "",
  "cors_origins": [],
  "cors_expose_headers": ["mcp-session-id", "mcp-protocol-version"],
  "auto_connect_on_startup": true,
  "disabled_tools": [],
  "media_import_dir": "",
  "media_allowed_types": [],
  "media_allowed_hosts": []
}
```

### Disabling Tools

Hide specific tools or actions from AI clients to reduce token usage:

```json
{
  "disabled_tools": [
    "sync",
    "card_management:bury",
    "card_management:unbury"
  ]
}
```

- `"tool_name"` — disables the entire tool
- `"tool_name:action"` — disables a specific action within a multi-action tool

Disabled tools are removed from the MCP schema entirely — AI clients never see them. Typos in tool/action names will produce console warnings.

### Custom Path

Set `http_path` to serve the MCP endpoint under a custom path. Useful when exposing Anki via a tunnel (Cloudflare, ngrok) to avoid a fully open endpoint:

```json
{
  "http_path": "my-secret-path"
}
```

The server will be accessible at `http://localhost:3141/my-secret-path/` instead of the root. Leave empty for default behavior.

### CORS Configuration

To allow browser-based MCP clients (like web-hosted MCP Inspector), add allowed origins:

```json
{
  "cors_origins": ["https://inspector.example.com", "http://localhost:5173"]
}
```

Use `["*"]` to allow all origins (not recommended for production).

The `cors_expose_headers` setting controls which response headers browsers can read. The defaults (`mcp-session-id`, `mcp-protocol-version`) are required for the MCP Streamable HTTP protocol to work in browsers.

### Media Security

The `store_media_file` tool validates all inputs to prevent path traversal and SSRF attacks:

- **File paths** are restricted to media files only (images, audio, video) via MIME type checking
- **URLs** must use `http://` or `https://` and cannot target private/internal networks
- **Filenames** are sanitized to remove path traversal sequences

Optional hardening via config:

```json
{
  "media_import_dir": "/Users/me/anki-media",
  "media_allowed_types": ["application/pdf"],
  "media_allowed_hosts": ["192.168.1.50", "my-nas.local"]
}
```

- `media_import_dir` — restrict file path imports to this directory tree (empty = no restriction)
- `media_allowed_types` — allow additional MIME types beyond image/audio/video
- `media_allowed_hosts` — allow specific hosts to bypass private network blocking

## Available Tools

### Essential Tools

| Tool | Description |
|------|-------------|
| `sync` | Synchronize collection with AnkiWeb |
| `list_decks` | List all decks in the collection |
| `create_deck` | Create a new deck |
| `find_notes` | Search for notes using Anki's search syntax |
| `notes_info` | Get detailed information about notes |
| `add_note` | Add a new note to a deck |
| `add_notes` | Batch-add up to 100 notes sharing the same deck and model. Uses Anki's native batch API for atomic undo. Supports partial success — individual failures don't affect others |
| `card_management` | Manage cards with 9 actions: `reposition` (set learning order), `change_deck` (move between decks), `bury`/`unbury` (hide until tomorrow), `suspend`/`unsuspend` (indefinitely exclude from review), `set_flag` (color flags 0-7), `set_due_date` (reschedule with days DSL), `forget_cards` (reset to new) |
| `tag_management` | Manage tags with 5 actions: `add_tags`/`remove_tags` (bulk add/remove on notes), `replace_tags` (swap one tag for another), `get_tags` (list all), `clear_unused_tags` (remove orphans) |
| `filtered_deck` | Filtered deck lifecycle: `create_or_update` (create or modify filtered decks with search terms), `rebuild` (repopulate), `empty` (return cards to home decks), `delete` |
| `update_note_fields` | Update fields of existing notes |
| `delete_notes` | Delete notes from the collection |
| `get_due_cards` | Get next due card for review (supports `skip_images`/`skip_audio` for voice mode) |
| `present_card` | Get card content for review |
| `rate_card` | Rate a card after review (Again/Hard/Good/Easy) |
| `model_names` | List available note types |
| `model_field_names` | Get field names and descriptions for a note type |
| `model_styling` | Get CSS styling for a note type |
| `update_model_styling` | Update CSS styling for a note type |
| `create_model` | Create a new note type |
| `store_media_file` | Store a media file (image/audio) via base64, file path, or URL. File paths are validated against a media-type allowlist; URLs are checked for SSRF |
| `get_media_files_names` | List media files matching a pattern |
| `delete_media_file` | Move a media file to Anki's trash (recoverable via Check Media) |

### FSRS Tools

| Tool | Description |
|------|-------------|
| `get_fsrs_params` | Get FSRS scheduler parameters for deck presets |
| `set_fsrs_params` | Update FSRS parameters (weights, desired retention, max interval) |
| `get_card_memory_state` | Get FSRS memory state (stability, difficulty, retrievability) for cards |
| `optimize_fsrs_params` | Run FSRS parameter optimization using Anki's built-in optimizer |

### GUI Tools

These tools interact with Anki's user interface:

| Tool | Description |
|------|-------------|
| `gui_browse` | Open the card browser with a search query |
| `gui_add_cards` | Open the Add Cards dialog |
| `gui_edit_note` | Open the note editor for a specific note |
| `gui_current_card` | Get info about the currently displayed card |
| `gui_show_question` | Show the question side of current card |
| `gui_show_answer` | Show the answer side of current card |
| `gui_select_card` | Select a specific card in the reviewer |
| `gui_deck_browser` | Navigate to deck browser |
| `gui_undo` | Undo the last operation |

### Resources

| Resource | URI | Description |
|----------|-----|-------------|
| `system_info` | `anki://system-info` | Anki version, profile, and scheduler info |
| `query_syntax` | `anki://query-syntax` | Anki search query syntax reference |
| `schema` | `anki://schema` | Data model documentation (entities, fields, relationships) |
| `stats_today` | `anki://stats/today` | Today's study statistics |
| `stats_forecast` | `anki://stats/forecast` | 30-day review forecast |
| `stats_collection` | `anki://stats/collection` | Overall collection statistics |
| `fsrs_config` | `anki://fsrs/config` | FSRS configuration summary and parameters |

### Prompts

| Prompt | Description |
|--------|-------------|
| `review_session` | Guided review session workflow (interactive, quick, or voice mode) |

## Requirements

- **Anki 25.07 or later** (ships Python 3.13)
- Anki 25.02 and earlier ship Python 3.9, which is **not supported** — the MCP SDK requires Python 3.10+ ([#8](https://github.com/ankimcp/anki-mcp-server-addon/issues/8))

## Architecture

The addon runs an MCP server in a background thread with HTTP transport (FastMCP + uvicorn). All Anki operations are bridged to the main Qt thread via a queue system, following the same proven pattern as AnkiConnect.

For details, see [Anki Add-on Development Documentation](https://addon-docs.ankiweb.net/).

## Development

### Running E2E Tests

E2E tests run against a real Anki instance in Docker using [headless-anki](https://github.com/ankimcp/headless-anki).

```bash
# Install test dependencies
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# Build the addon
./package.sh

# Start Anki container
cd .docker && docker compose up -d && cd ..

# Run tests (waits for server automatically)
pytest tests/e2e/ -v

# Stop container
cd .docker && docker compose down
```

Or use the Makefile shortcuts:
```bash
make e2e        # Build, start container, run tests, stop
make e2e-up     # Just start container
make e2e-test   # Just run tests
make e2e-down   # Just stop container
```

### CI

E2E tests run automatically on push to any branch and on PRs to `main`. See `.github/workflows/e2e.yml`.

## License

AGPL-3.0-or-later

## Links

- [ankimcp.ai](https://ankimcp.ai) - Project homepage
- [MCP Protocol](https://modelcontextprotocol.io/) - Model Context Protocol specification
- [Anki Add-on Docs](https://addon-docs.ankiweb.net/) - Official Anki addon development documentation
