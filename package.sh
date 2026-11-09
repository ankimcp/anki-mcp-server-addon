#!/bin/bash
# No pipefail: the leak check below pipes `find` into `grep -q .`, and with
# pipefail a SIGPIPE'd find would turn a found leak into a pass. Nothing that
# can fail the build is piped.
set -eu

ADDON_DIR="anki_mcp_server"
OUTPUT="anki_mcp_server.ankiaddon"
WHEELS_DIR="wheels"
VENDOR_DIR="$ADDON_DIR/vendor"
BUILD_PYTHON_FILE=".github/build-python-version"

# Interpreter that runs pip. Override with PYTHON=/path/to/python3.13.
PYTHON="${PYTHON:-python3}"

# The Python Anki ships (3.13 for Anki 25.07+). Same file CI's setup-python reads.
BUILD_PYTHON="$(sed -e 's/#.*//' -e 's/[[:space:]]//g' "$BUILD_PYTHON_FILE" | grep -m1 . || true)"
[ -n "$BUILD_PYTHON" ] || { echo "ERROR: no version found in $BUILD_PYTHON_FILE" >&2; exit 1; }
PYTHON_VERSION="${BUILD_PYTHON//./}"

echo "=== Checking build Python ==="
# pip evaluates dependency environment markers (e.g. anyio's
# `typing_extensions>=4.16.0; python_version < "3.15"`) against the interpreter
# running pip, not against --python-version, so the host Python decides which
# versions get vendored. 0.41.0 was built on 3.15 and could not load in Anki.
FOUND_PYTHON="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
if [ "$FOUND_PYTHON" != "$BUILD_PYTHON" ]; then
    echo "ERROR: package.sh must run on Python $BUILD_PYTHON (from $BUILD_PYTHON_FILE); '$PYTHON' is ${FOUND_PYTHON:-not runnable}." >&2
    echo "       pip evaluates dependency environment markers against the host interpreter," >&2
    echo "       so building on another Python vendors packages that may not load inside Anki." >&2
    echo "       Use e.g. \`uv run --python $BUILD_PYTHON ./package.sh\`, or \`brew install python@$BUILD_PYTHON\`" >&2
    echo "       and run \`PYTHON=python$BUILD_PYTHON ./package.sh\`." >&2
    exit 1
fi
echo "Using $PYTHON (Python $FOUND_PYTHON)"

echo "=== Cleaning previous builds ==="
rm -rf "$WHEELS_DIR" "$VENDOR_DIR" "$OUTPUT"
mkdir -p "$WHEELS_DIR"

echo "=== Downloading pure Python packages (with transitive deps) ==="
# Versions come from requirements.txt (single source of truth); pip resolves
# transitive deps automatically. The mcp<2 pin there keeps us on the v1 SDK.
# Note: pydantic-core is NOT included here - it will be lazy-loaded at runtime
"$PYTHON" -m pip download -r requirements.txt \
    --dest "$WHEELS_DIR/pure" \
    --only-binary=:all: \
    --python-version "$PYTHON_VERSION" \
    --platform macosx_11_0_arm64

echo "=== Extracting wheels to vendor directory ==="

# Extract pure Python packages to shared location
mkdir -p "$VENDOR_DIR/shared"
for wheel in "$WHEELS_DIR/pure"/*.whl; do
    if [ -f "$wheel" ]; then
        wheel_name="$(basename "$wheel")"
        # Skip platform-specific binary wheels. These are NOT vendored because the
        # bundle can only carry one platform's .so/.pyd, which crashes on other
        # platforms. pydantic_core and rpds are instead ensured/downloaded at
        # runtime (see dependency_loader.py). cryptography/cffi/pycparser are
        # unused on the server path, so they're dropped entirely as dead weight.
        case "$wheel_name" in
            pydantic_core-*)
                echo "  Skipping pydantic_core (downloaded at runtime — platform-specific binary)"
                continue
                ;;
            rpds_py-*)
                echo "  Skipping rpds_py (ensured/downloaded at runtime — platform-specific binary, issue #54)"
                continue
                ;;
            cryptography-*)
                echo "  Skipping cryptography (unused on server path — dropping native dead weight)"
                continue
                ;;
            cffi-*)
                echo "  Skipping cffi (unused on server path — dropping native dead weight)"
                continue
                ;;
            pycparser-*)
                echo "  Skipping pycparser (only a cffi dep — dead weight once cffi is gone)"
                continue
                ;;
        esac
        echo "  Extracting $wheel_name to shared/"
        unzip -q -o "$wheel" -d "$VENDOR_DIR/shared"
    fi
done

echo "=== Verifying no native binaries leaked into vendor ==="
# Guardrail (issue #54): a future `mcp` bump could pull in a wrong-platform
# native binary as a transitive dep and silently reintroduce the crash. Fail
# the build if any compiled artifact from rpds/cryptography/cffi survived into
# the bundle — these MUST be dropped or runtime-downloaded, never vendored.
if find "$VENDOR_DIR/shared" \
        \( -path '*/rpds/*' -o -path '*/cryptography/*' -o -path '*/cffi/*' \) \
        \( -name '*.so' -o -name '*.pyd' \) 2>/dev/null | grep -q . \
   || find "$VENDOR_DIR/shared" -maxdepth 1 -name '_cffi_backend.*' 2>/dev/null | grep -q .; then
    echo "ERROR: native binary leaked into $VENDOR_DIR/shared (rpds/cryptography/cffi/_cffi_backend)." >&2
    echo "       These must be skipped in the extraction loop or downloaded at runtime, not vendored." >&2
    find "$VENDOR_DIR/shared" \
        \( -path '*/rpds/*' -o -path '*/cryptography/*' -o -path '*/cffi/*' \) \
        \( -name '*.so' -o -name '*.pyd' \) 2>/dev/null >&2
    find "$VENDOR_DIR/shared" -maxdepth 1 -name '_cffi_backend.*' 2>/dev/null >&2
    exit 1
fi

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
# Exclude any developer's local tunnel credentials (and the transient .tmp
# written during CredentialsManager.save()). The README.txt is kept so the
# user_files/ directory still ships. Paths are relative to this cwd.
#
# Also exclude _cache/: the LEGACY/fallback runtime download location for
# pydantic_core/rpds (see dependency_loader.py's _resolve_cache_root — the
# preferred cache now lives outside the addon folder entirely, but this
# fallback dir can still get populated on a dev machine, e.g. Anki not
# available when the addon last ran from source). It holds large,
# PLATFORM-SPECIFIC native binaries (e.g. a darwin .so) that must never ship —
# pydantic_core is downloaded per-platform at runtime by design. It's
# .gitignored, but this zip packages the working tree (not git), so
# .gitignore alone won't keep it out of the bundle. CI builds from a clean
# checkout so this only bites local builds, but excluding it here makes the
# build robust regardless of working-tree state.
zip -r -q "../$OUTPUT" . -x "*.pyc" -x "__pycache__/*" -x ".DS_Store" -x "*.git*" \
    -x "_cache/*" -x "user_files/credentials.json" -x "user_files/credentials.tmp"
cd ..

echo ""
echo "=== Build complete ==="
echo "Output: $OUTPUT"
echo "Size: $(du -h $OUTPUT | cut -f1)"
echo ""
echo "Vendor directory structure:"
du -sh "$VENDOR_DIR"/* 2>/dev/null || true
