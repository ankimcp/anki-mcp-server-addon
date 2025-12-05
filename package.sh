#!/bin/bash
set -e

ADDON_DIR="anki_mcp_server"
OUTPUT="anki_mcp_server.ankiaddon"
WHEELS_DIR="wheels"
VENDOR_DIR="$ADDON_DIR/vendor"

# Use python3 -m pip for portability
PIP="python3 -m pip"

# Anki 25.x uses Python 3.13
PYTHON_VERSION="313"

# Pure Python packages (same for all platforms)
# Let pip resolve all transitive dependencies automatically
PURE_PACKAGES="mcp uvicorn starlette websockets pydantic pydantic-settings anyio httpx sniffio h11 idna certifi httpcore typing_extensions annotated-types click sse-starlette httpx-sse typing_inspection"

echo "=== Cleaning previous builds ==="
rm -rf "$WHEELS_DIR" "$VENDOR_DIR" "$OUTPUT"
mkdir -p "$WHEELS_DIR"

echo "=== Downloading pure Python packages (with transitive deps) ==="
# Let pip resolve all transitive dependencies automatically
# Note: pydantic-core is NOT included here - it will be lazy-loaded at runtime
$PIP download mcp uvicorn starlette websockets \
    --dest "$WHEELS_DIR/pure" \
    --only-binary=:all: \
    --python-version $PYTHON_VERSION \
    --platform macosx_11_0_arm64 \
    2>&1 | grep -v "already satisfied" || true

# Also grab any pure Python wheels we might have missed
$PIP download mcp uvicorn starlette websockets \
    --dest "$WHEELS_DIR/pure" \
    2>&1 | grep -v "already satisfied" || true

echo "=== Extracting wheels to vendor directory ==="

# Extract pure Python packages to shared location
mkdir -p "$VENDOR_DIR/shared"
for wheel in "$WHEELS_DIR/pure"/*.whl; do
    if [ -f "$wheel" ]; then
        # Skip pydantic-core if it somehow got downloaded
        if [[ "$(basename "$wheel")" == pydantic_core-* ]]; then
            echo "  Skipping pydantic_core (will be lazy-loaded at runtime)"
            continue
        fi
        echo "  Extracting $(basename "$wheel") to shared/"
        unzip -q -o "$wheel" -d "$VENDOR_DIR/shared"
    fi
done

echo "=== Cleaning up ==="
# Keep dist-info directories - needed for importlib.metadata.version()
# Remove pycache
find "$VENDOR_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
# Remove test directories
find "$VENDOR_DIR" -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
find "$VENDOR_DIR" -type d -name "test" -exec rm -rf {} + 2>/dev/null || true
# Remove .pyc files
find "$VENDOR_DIR" -type f -name "*.pyc" -delete 2>/dev/null || true

# Clean up wheels directory
rm -rf "$WHEELS_DIR"

echo "=== Creating .ankiaddon package ==="
cd "$ADDON_DIR"
zip -r -q "../$OUTPUT" . -x "*.pyc" -x "__pycache__/*" -x ".DS_Store" -x "*.git*"
cd ..

echo ""
echo "=== Build complete ==="
echo "Output: $OUTPUT"
echo "Size: $(du -h $OUTPUT | cut -f1)"
echo ""
echo "Vendor directory structure:"
du -sh "$VENDOR_DIR"/* 2>/dev/null || true
