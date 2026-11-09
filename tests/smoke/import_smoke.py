#!/usr/bin/env python3
"""Headless startup smoke test for the addon's native-dependency loading.

This script simulates the *exact* dependency-bootstrap sequence the addon runs
at startup (``anki_mcp_server/__init__.py``) without importing the addon
package itself, and by default without importing aqt either (``--preload-aqt``
changes that, see below). It is meant to run in CI on every OS/arch/Python combo to
prove that the platform-agnostic ``.ankiaddon`` bundle can fetch and load the
correct native wheels (``pydantic_core``, ``rpds``) for the *running* platform,
and that the full ``mcp -> jsonschema -> referencing -> rpds`` import chain
works.

Guards two classes of regression:

* #52 — ``pydantic_core`` is downloaded at runtime; arch detection must pick the
  right wheel for the running platform (uses ``platform.machine()``).
* #54 — the bundle no longer vendors ``rpds``; it is ensured at runtime. In the
  default mode Anki is absent, so the rpds path here exercises the DOWNLOAD
  fallback for the running platform (the very scenario that crashed Windows /
  Linux-arm / macOS-Intel before the fix).

With ``--preload-aqt`` it also guards a third:

* Host shadowing — Anki imports ``aqt`` (and with it its own
  ``typing_extensions``, via ``aqt/webview.py``) before any add-on loads, so the
  vendored copy is shadowed via ``sys.modules`` and every vendored package must
  run on Anki's older one. The flag imports ``aqt`` first, exactly as Anki does,
  so a vendored package that needs a newer ``typing_extensions`` fails here
  (0.41.0: anyio 4.16.0 -> ``from typing_extensions import sentinel``).
  Anki's other packages (``rpds`` included) are preloaded too and shadow the
  vendored copies as inside real Anki, so the rpds check takes the fast path
  instead of the download fallback. Also imports the rest of the add-on's real
  load path (``mcp.types``, ``mcp.server.transport_security``,
  ``websockets.asyncio.client``, ``httpx``). Requires ``aqt`` (and PyQt6)
  installed in the running interpreter.

Usage:
    python tests/smoke/import_smoke.py [--preload-aqt] [ADDON_DIR]

``ADDON_DIR`` is the directory containing the addon's ``__init__.py`` and
``vendor/`` (i.e. the ``anki_mcp_server`` package contents). It defaults to the
``anki_mcp_server`` package inside the repo. In CI the ``.ankiaddon`` zip is
extracted *flat* (the zip is built from inside ``anki_mcp_server/``), so the
extraction directory itself is the package dir — pass that path.

Note: this script does NOT ``import anki_mcp_server`` — that package's
``__init__`` imports aqt, which isn't available headless in the default mode,
and would boot the whole add-on otherwise. Instead it loads
``dependency_loader.py`` standalone via ``importlib.util``.

Exits 0 on success, non-zero with a clear message on any failure. Prints
platform / arch / python diagnostics up front for triage.
"""

import argparse
import importlib.metadata
import importlib.util
import platform
import sys
import sysconfig
import traceback
from pathlib import Path


def _fail(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"\n[SMOKE] FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def _locate_addon_dir(arg: "str | None") -> Path:
    """Resolve the addon package dir from the CLI argument or the repo default."""
    if arg:
        addon_dir = Path(arg).resolve()
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
    vendored = sorted(
        p.name
        for pattern in ("anyio-*.dist-info", "typing_extensions-*.dist-info")
        for p in shared.glob(pattern)
    )
    print(f"[SMOKE] vendored: {', '.join(vendored) or '(none found)'}")


def _loaded_dist_version(module_name: str, dist_name: str) -> str:
    """Version of the copy of ``module_name`` actually in ``sys.modules``.

    Read from the dist-info next to the loaded file, not via a plain
    ``importlib.metadata.version()`` lookup, which would answer for whichever
    copy comes first on ``sys.path`` — the vendored one, once it is prepended.
    """
    module = sys.modules.get(module_name)
    location = getattr(module, "__file__", None)
    if location is None:
        return "not loaded"
    path = Path(location).parent
    if path.name == module_name:  # a package: dist-info sits beside the package dir
        path = path.parent
    for dist in importlib.metadata.distributions(path=[str(path)]):
        if (dist.metadata["Name"] or "").lower().replace("-", "_") == dist_name:
            return f"{dist.version} ({location})"
    return f"? ({location})"


def _preload_aqt() -> None:
    """Import aqt before the vendor path is set up, as Anki does before add-ons."""
    print("\n[SMOKE] importing aqt (Anki loads it, and its typing_extensions, first)...")
    try:
        import aqt
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _fail(f"import aqt failed — the host environment is broken, not the add-on: {exc!r}")
    print(f"  aqt {importlib.metadata.version('aqt')} OK ({aqt.__file__})")
    if "typing_extensions" not in sys.modules:
        _fail(
            "typing_extensions is not in sys.modules after `import aqt`, so this "
            "run would not reproduce Anki's shadowing of the vendored copy"
        )
    print(
        "  host typing_extensions: "
        f"{_loaded_dist_version('typing_extensions', 'typing_extensions')}"
    )


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
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("addon_dir", nargs="?", help="addon package dir")
    parser.add_argument(
        "--preload-aqt",
        action="store_true",
        help="import aqt before the vendor path is set up, as Anki does",
    )
    args = parser.parse_args()

    addon_dir = _locate_addon_dir(args.addon_dir)
    _print_diagnostics(addon_dir)
    if args.preload_aqt:
        _preload_aqt()
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
    # In the default mode Anki is absent, so `import rpds` fails in the loader's
    # fast path and this exercises the wheel-download fallback — exactly the
    # platform-specific path that crashed pre-#54. With --preload-aqt, Anki's
    # rpds is already loaded and the fast path wins, as inside real Anki.
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
        traceback.print_exc()
        _fail(f"import mcp.server.fastmcp failed: {exc!r}")
    print(f"  anyio {_loaded_dist_version('anyio', 'anyio')}")
    print(
        "  typing_extensions "
        f"{_loaded_dist_version('typing_extensions', 'typing_extensions')}"
    )

    if args.preload_aqt:
        # The rest of the add-on's load path, each against Anki's preloaded
        # packages: tool_decorator, mcp_server, tunnel/client.py, tunnel/auth.py.
        for module_name in (
            "mcp.types",
            "mcp.server.transport_security",
            "websockets.asyncio.client",
            "httpx",
        ):
            try:
                importlib.import_module(module_name)
                print(f"  import {module_name} OK")
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                _fail(f"import {module_name} failed: {exc!r}")

    try:
        server = fastmcp.FastMCP("smoke")
        print(f"  FastMCP('smoke') instantiated OK: {server!r}")
    except Exception as exc:  # noqa: BLE001
        _fail(f"FastMCP('smoke') instantiation failed: {exc!r}")

    print("\n[SMOKE] PASS — all dependency loading + import checks succeeded")
    sys.exit(0)


if __name__ == "__main__":
    main()
