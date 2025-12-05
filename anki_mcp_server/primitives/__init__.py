# primitives/__init__.py
"""MCP primitives module - tools, prompts, and resources."""

from .tools import register_all_tools
from .resources import register_all_resources
from .prompts import register_all_prompts


__all__ = ["register_all_tools", "register_all_resources", "register_all_prompts"]
