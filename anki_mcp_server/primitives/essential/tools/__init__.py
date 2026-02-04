"""Auto-discover and import all tool modules."""
import pkgutil
import importlib

for importer, modname, ispkg in pkgutil.walk_packages(__path__, __name__ + "."):
    importlib.import_module(modname)
