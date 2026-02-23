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

## Usage

The server starts automatically when you open Anki. Check status via *Tools → AnkiMCP Server Settings...*

### Connect with Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "anki": {
      "url": "http://127.0.0.1:3141/"
    }
  }
}
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
  "auto_connect_on_startup": true
}
```

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
| `card_management` | Manage cards: `reposition` (set learning order), `changeDeck` (move between decks), `bury` (hide until tomorrow), `unbury` (restore buried cards in a deck) |
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
| `store_media_file` | Store a media file (image/audio) |
| `get_media_files_names` | List media files matching a pattern |
| `delete_media_file` | Delete a media file |

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
