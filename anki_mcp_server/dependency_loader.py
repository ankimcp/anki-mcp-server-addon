"""Lazy-loader for pydantic_core - downloads correct wheel from PyPI on first run."""

import sys
import sysconfig
import json
import urllib.request
import zipfile
import shutil
from pathlib import Path

# Qt imports for progress dialog
from aqt.qt import QProgressDialog, QMessageBox, QApplication

CACHE_DIR = Path(__file__).parent / "_cache"
PYPI_URL = "https://pypi.org/pypi/pydantic-core/json"


def _get_platform_tag() -> str:
    """Convert sysconfig.get_platform() to PyPI wheel tag."""
    platform = sysconfig.get_platform()

    # Map to wheel platform tags
    if platform.startswith("win-amd64"):
        return "win_amd64"
    elif platform.startswith("win-arm64"):
        return "win_arm64"
    elif "linux" in platform and "x86_64" in platform:
        return "manylinux"  # Will match manylinux2014_x86_64 etc
    elif "linux" in platform and ("aarch64" in platform or "arm64" in platform):
        return "manylinux"  # Will match manylinux2014_aarch64
    elif "macos" in platform and "arm64" in platform:
        return "macosx"  # Will need to match macosx_*_arm64
    elif "macos" in platform and "x86_64" in platform:
        return "macosx"  # Will match macosx_*_x86_64
    else:
        raise RuntimeError(f"Unsupported platform: {platform}")


def _get_python_tag() -> str:
    """Get Python version tag like cp312, cp313."""
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def _find_wheel_url(pypi_data: dict) -> str:
    """Find matching wheel URL from PyPI JSON data."""
    py_tag = _get_python_tag()
    platform = sysconfig.get_platform()

    # Determine what we're looking for
    is_arm = "arm64" in platform or "aarch64" in platform
    is_windows = platform.startswith("win")
    is_macos = "macos" in platform
    is_linux = "linux" in platform

    for url_info in pypi_data["urls"]:
        filename = url_info["filename"]

        # Must be a wheel
        if not filename.endswith(".whl"):
            continue

        # Must match Python version
        if py_tag not in filename:
            continue

        # Platform matching
        if is_windows:
            if is_arm and "win_arm64" in filename:
                return url_info["url"]
            elif not is_arm and "win_amd64" in filename:
                return url_info["url"]
        elif is_macos:
            if is_arm and "arm64" in filename and "macosx" in filename:
                return url_info["url"]
            elif not is_arm and "x86_64" in filename and "macosx" in filename:
                return url_info["url"]
        elif is_linux:
            if is_arm and "aarch64" in filename and "manylinux" in filename:
                return url_info["url"]
            elif not is_arm and "x86_64" in filename and "manylinux" in filename:
                return url_info["url"]

    raise RuntimeError(f"No matching wheel found for {py_tag} on {platform}")


def _fix_windows_pyd(cache_dir: Path) -> None:
    """Windows needs untagged .pyd files."""
    if not sys.platform == "win32":
        return

    pydantic_core_dir = cache_dir / "pydantic_core"
    if not pydantic_core_dir.exists():
        return

    for pyd_file in pydantic_core_dir.glob("*.pyd"):
        if ".cp" in pyd_file.name:
            # _pydantic_core.cp313-win_amd64.pyd -> _pydantic_core.pyd
            simple_name = pyd_file.name.split(".cp")[0] + ".pyd"
            simple_path = pyd_file.parent / simple_name
            if not simple_path.exists():
                shutil.copy(pyd_file, simple_path)


def ensure_pydantic_core() -> bool:
    """Ensure pydantic_core is available. Downloads from PyPI if needed.

    Returns True if pydantic_core is ready, False if failed.
    Shows progress dialog during download.
    """
    cache_dir = CACHE_DIR / "pydantic_core_pkg"
    marker_file = cache_dir / ".complete"

    # Already cached?
    if marker_file.exists():
        sys.path.insert(0, str(cache_dir))
        return True

    # Need to download
    try:
        # Create progress dialog
        progress = QProgressDialog(
            "Downloading pydantic_core (first run only)...",
            "Cancel",
            0, 100
        )
        progress.setWindowTitle("AnkiMCP Server - Setup")
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.show()
        QApplication.processEvents()

        # Fetch PyPI metadata
        progress.setLabelText("Fetching package info...")
        QApplication.processEvents()

        with urllib.request.urlopen(PYPI_URL, timeout=30) as response:
            pypi_data = json.loads(response.read().decode())

        if progress.wasCanceled():
            return False

        # Find correct wheel
        wheel_url = _find_wheel_url(pypi_data)
        wheel_name = wheel_url.split("/")[-1]

        progress.setLabelText(f"Downloading {wheel_name}...")
        progress.setValue(10)
        QApplication.processEvents()

        # Download wheel
        cache_dir.mkdir(parents=True, exist_ok=True)
        wheel_path = cache_dir / wheel_name

        def download_progress(block_num, block_size, total_size):
            if progress.wasCanceled():
                raise InterruptedError("Download cancelled")
            if total_size > 0:
                downloaded = block_num * block_size
                percent = min(10 + int(downloaded * 70 / total_size), 80)
                mb_done = downloaded / (1024 * 1024)
                mb_total = total_size / (1024 * 1024)
                progress.setLabelText(f"Downloading... {mb_done:.1f}/{mb_total:.1f} MB")
                progress.setValue(percent)
                QApplication.processEvents()

        urllib.request.urlretrieve(wheel_url, wheel_path, reporthook=download_progress)

        if progress.wasCanceled():
            return False

        # Extract wheel
        progress.setLabelText("Extracting...")
        progress.setValue(85)
        QApplication.processEvents()

        with zipfile.ZipFile(wheel_path, 'r') as zf:
            for member in zf.namelist():
                # Skip dist-info directory
                if ".dist-info" in member:
                    continue
                zf.extract(member, cache_dir)

        # Remove wheel file
        wheel_path.unlink()

        # Fix Windows .pyd naming
        _fix_windows_pyd(cache_dir)

        # Mark complete
        marker_file.touch()

        progress.setValue(100)
        progress.setLabelText("Done!")
        QApplication.processEvents()
        progress.close()

        # Add to path
        sys.path.insert(0, str(cache_dir))
        return True

    except InterruptedError:
        # User cancelled
        shutil.rmtree(cache_dir, ignore_errors=True)
        return False

    except Exception as e:
        # Show error
        shutil.rmtree(cache_dir, ignore_errors=True)
        QMessageBox.critical(
            None,
            "AnkiMCP Server - Setup Failed",
            f"Failed to download pydantic_core:\n\n{e}\n\n"
            "Please check your internet connection and try again."
        )
        return False
