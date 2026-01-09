"""Auto-discover and import all tool modules."""
from pathlib import Path
import importlib

# Auto-import all .py files in this directory (except __init__.py)
for _file in Path(__file__).parent.glob("*.py"):
    if not _file.stem.startswith("_"):
        importlib.import_module(f".{_file.stem}", __package__)
