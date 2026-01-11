#!/bin/bash
set -e

ADDON_DIR="/data/addons21/anki_mcp_server"

echo "=== Anki MCP Server E2E Setup ==="

# Unpack addon if .ankiaddon file exists
if [ -f /app/addon.ankiaddon ]; then
    echo "Unpacking addon..."
    rm -rf "$ADDON_DIR"
    mkdir -p "$ADDON_DIR"
    unzip -q /app/addon.ankiaddon -d "$ADDON_DIR"
    echo "Addon unpacked to $ADDON_DIR"
fi

# Copy config override if exists (overwrite both config.json and meta.json)
if [ -f /app/config.json ]; then
    echo "Applying config override (http_host: 0.0.0.0)..."
    cp /app/config.json "$ADDON_DIR/config.json"
    cp /app/config.json "$ADDON_DIR/meta.json"
fi

# Fix permissions
chown -R anki:anki /data/addons21

echo "Starting Anki..."
exec /startup.sh
