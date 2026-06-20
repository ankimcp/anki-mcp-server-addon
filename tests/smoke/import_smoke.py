#!/usr/bin/env python3
"""Headless startup smoke test for the addon's native-dependency loading.

This script simulates the *exact* dependency-bootstrap sequence the addon runs
at startup (``anki_mcp_server/__init__.py``), but WITHOUT importing aqt or the
addon package itself. It is meant to run in CI on every OS/arch/Python combo to
prove that the platform-agnostic ``.ankiaddon`` bundle can fetch and load the
correct native wheels (``pydantic_core``, ``rpds``) for the *running* platform,
and that the full ``mcp -> jsonschema -> referencing -> rpds`` import chain
works.

Guards two classes of regression:

* #52 — ``pydantic_core`` is downloaded at runtime; arch detection must pick the
  right wheel for the running platform (uses ``platform.machine()``).
* #54 — the bundle no longer vendors ``rpds``; it is ensured at runtime. In CI
  Anki is absent, so the rpds path here always exercises the DOWNLOAD fallback
  for the running platform (the very scenario that crashed Windows / Linux-arm /
  macOS-Intel before the fix).

Usage:
    python tests/smoke/import_smoke.py [ADDON_DIR]

``ADDON_DIR`` is the directory containing the addon's ``__init__.py`` and
``vendor/`` (i.e. the ``anki_mcp_server`` package contents). It defaults to the
``anki_mcp_server`` package inside the repo. In CI the ``.ankiaddon`` zip is
extracted *flat* (the zip is built from inside ``anki_mcp_server/``), so the
extraction directory itself is the package dir — pass that path.

Note: this script does NOT ``import anki_mcp_server`` — that package's
``__init__`` imports aqt, which isn't available headless. Instead it loads
``dependency_loader.py`` standalone via ``importlib.util``.

Exits 0 on success, non-zero with a clear message on any failure. Prints
platform / arch / python diagnostics up front for triage.
"""

import importlib.util
import platform
import sys
import sysconfig
from pathlib import Path


def _fail(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"\n[SMOKE] FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def _locate_addon_dir() -> Path:
    """Resolve the addon package dir from argv[1] or the repo default."""
    if len(sys.argv) > 1:
        addon_dir = Path(sys.argv[1]).resolve()
    else:
        # Default: <repo>/anki_mcp_server (script lives at tests/smoke/).
        repo_root = Path(__file__).resolve().parents[2]
        addon_dir = repo_root / "anki_mcp_server"

    if not addon_dir.is_dir():
        _fail(f"addon dir does not exist: {addon_dir}")
    if not (addon_dir / "dependency_loader.py").is_file():
        _fail(
            f"dependency_loader.py not found in {addon_dir} — "
            "is this the addon package dir (it should contain __init__.py + vendor/)?"
        )
    return addon_dir


def _print_diagnostics(addon_dir: Path) -> None:
    print("=" * 70)
    print("[SMOKE] AnkiMCP headless dependency-loading smoke test")
    print("=" * 70)
    print(f"[SMOKE] addon dir       : {addon_dir}")
    print(f"[SMOKE] python version  : {platform.python_version()} ({sys.executable})")
    print(f"[SMOKE] python tag      : cp{sys.version_info.major}{sys.version_info.minor}")
    print(f"[SMOKE] platform.system : {platform.system()}")
    print(f"[SMOKE] platform.machine: {platform.machine()}")
    print(f"[SMOKE] sysconfig plat  : {sysconfig.get_platform()}")
    print("=" * 70)


def _setup_vendor_path(addon_dir: Path) -> None:
    """Mirror ``__init__._setup_vendor_path``: prepend vendor/shared to sys.path.

    Only the pure-Python stack lives there (jsonschema, referencing, mcp, etc.);
    the native deps are fetched at runtime by the loader, into its own cache dir.
    """
    shared = addon_dir / "vendor" / "shared"
    if not shared.is_dir():
        _fail(
            f"vendor/shared not found at {shared} — "
            "did you run ./package.sh / extract a built .ankiaddon?"
        )
    sys.path.insert(0, str(shared))
    print(f"[SMOKE] prepended to sys.path: {shared}")


def _load_dependency_loader(addon_dir: Path):
    """Load dependency_loader.py standalone (no ``import anki_mcp_server``).

    The addon's package ``__init__`` imports aqt, which is absent headless, so we
    load just the loader module by file path via importlib.
    """
    loader_path = addon_dir / "dependency_loader.py"
    spec = importlib.util.spec_from_file_location(
        "ankimcp_smoke_dependency_loader", loader_path
    )
    if spec is None or spec.loader is None:
        _fail(f"could not create import spec for {loader_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001 — want the full message on any failure
        _fail(f"failed to exec dependency_loader.py: {exc!r}")
    return module


def main() -> None:
    addon_dir = _locate_addon_dir()
    _print_diagnostics(addon_dir)
    _setup_vendor_path(addon_dir)

    loader = _load_dependency_loader(addon_dir)

    # --- Guard #52: pydantic_core download + load for the running platform -----
    print("\n[SMOKE] ensuring pydantic_core (download path, guards #52)...")
    try:
        ok = loader._ensure_pydantic_core_with_callbacks(
            on_status=lambda m: print(f"  [pydantic_core] {m}"),
            on_error=lambda m: print(f"  [pydantic_core][error] {m}", file=sys.stderr),
        )
    except Exception as exc:  # noqa: BLE001
        _fail(f"_ensure_pydantic_core_with_callbacks raised: {exc!r}")
    if not ok:
        _fail("_ensure_pydantic_core_with_callbacks returned False (see errors above)")
    print("[SMOKE] pydantic_core ensured OK")

    # --- Guard #54: rpds DOWNLOAD fallback for the running platform ------------
    # Anki is absent in CI, so `import rpds` fails in the loader's fast path and
    # this exercises the wheel-download fallback — exactly the platform-specific
    # path that crashed pre-#54.
    print("\n[SMOKE] ensuring rpds (download fallback, guards #54)...")
    try:
        ok = loader._ensure_rpds_with_callbacks(
            on_status=lambda m: print(f"  [rpds] {m}"),
            on_error=lambda m: print(f"  [rpds][error] {m}", file=sys.stderr),
        )
    except Exception as exc:  # noqa: BLE001
        _fail(f"_ensure_rpds_with_callbacks raised: {exc!r}")
    if not ok:
        _fail("_ensure_rpds_with_callbacks returned False (see errors above)")
    print("[SMOKE] rpds ensured OK")

    # --- Walk the real import chain -------------------------------------------
    print("\n[SMOKE] importing the native + mcp import chain...")
    try:
        import pydantic_core  # noqa: F401

        print(f"  pydantic_core {getattr(pydantic_core, '__version__', '?')} OK")
    except Exception as exc:  # noqa: BLE001
        _fail(f"import pydantic_core failed: {exc!r}")

    try:
        import rpds  # noqa: F401

        print(f"  rpds OK ({rpds.__file__})")
    except Exception as exc:  # noqa: BLE001
        _fail(f"import rpds failed: {exc!r}")

    try:
        # This is the chain that broke on wrong-platform rpds:
        # mcp.server.fastmcp -> jsonschema -> referencing -> rpds
        import mcp.server.fastmcp as fastmcp  # noqa: F401

        print("  import mcp.server.fastmcp OK")
    except Exception as exc:  # noqa: BLE001
        _fail(f"import mcp.server.fastmcp failed: {exc!r}")

    try:
        server = fastmcp.FastMCP("smoke")
        print(f"  FastMCP('smoke') instantiated OK: {server!r}")
    except Exception as exc:  # noqa: BLE001
        _fail(f"FastMCP('smoke') instantiation failed: {exc!r}")

    print("\n[SMOKE] PASS — all dependency loading + import checks succeeded")
    sys.exit(0)


if __name__ == "__main__":
    main()
