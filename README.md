# AnkiMCP Server (Addon)

An Anki addon that exposes your collection to AI assistants via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/).

## What is this?

AnkiMCP Server runs a local MCP server inside Anki, allowing AI assistants like Claude to interact with your flashcard collection. This enables AI-powered study sessions, card creation, and collection management.

Part of the [ankimcp.ai](https://ankimcp.ai) project.

## Features

- **Local HTTP server** - Runs on `http://127.0.0.1:3141/` by default
- **MCP protocol** - Compatible with any MCP client (Claude Desktop, etc.)
- **Auto-start** - Server starts automatically when Anki opens
- **Tunnel-friendly** - Works with Cloudflare Tunnel, ngrok, etc.
- **Cross-platform** - Works on macOS, Windows, and Linux (x64 and ARM)

## Installation

1. Download `anki_mcp_server.ankiaddon` from [Releases](https://github.com/anthropics/anki-mcp-server/releases)
2. Double-click to install, or use Anki's *Tools → Add-ons → Install from file...*
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
  "auto_connect_on_startup": true
}
```

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
| `update_note_fields` | Update fields of existing notes |
| `delete_notes` | Delete notes from the collection |
| `get_due_cards` | Get cards due for review |
| `present_card` | Get card content for review |
| `rate_card` | Rate a card after review (Again/Hard/Good/Easy) |
| `model_names` | List available note types |
| `model_field_names` | Get field names for a note type |
| `model_styling` | Get CSS styling for a note type |
| `update_model_styling` | Update CSS styling for a note type |
| `create_model` | Create a new note type |
| `store_media_file` | Store a media file (image/audio) |
| `get_media_files_names` | List media files matching a pattern |
| `delete_media_file` | Delete a media file |

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

| Resource | Description |
|----------|-------------|
| `system_info` | Anki version and system information |

### Prompts

| Prompt | Description |
|--------|-------------|
| `review_session` | Guided review session workflow |

## Requirements

- Anki 25.x or later (Python 3.13)

## Architecture

The addon runs an MCP server in a background thread with HTTP transport (FastMCP + uvicorn). All Anki operations are bridged to the main Qt thread via a queue system, following the same proven pattern as AnkiConnect.

For details, see [Anki Add-on Development Documentation](https://addon-docs.ankiweb.net/).

## License

AGPL-3.0-or-later

## Links

- [ankimcp.ai](https://ankimcp.ai) - Project homepage
- [MCP Protocol](https://modelcontextprotocol.io/) - Model Context Protocol specification
- [Anki Add-on Docs](https://addon-docs.ankiweb.net/) - Official Anki addon development documentation
