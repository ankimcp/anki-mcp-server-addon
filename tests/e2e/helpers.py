"""Helper functions for E2E tests using MCP Inspector CLI."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any

SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:3141")


def run_inspector(method: str, **kwargs) -> dict[str, Any]:
    """Run MCP Inspector CLI and return parsed JSON response.

    Args:
        method: MCP method (e.g., "tools/list", "tools/call", "resources/list")
        **kwargs: Additional arguments (tool_name, tool_args, uri, etc.)

    Returns:
        Parsed JSON response from the server.

    Raises:
        RuntimeError: If CLI fails or returns invalid JSON.
    """
    cmd = [
        "npx", "@modelcontextprotocol/inspector", "--cli",
        SERVER_URL,
        "--transport", "http",
        "--method", method,
    ]

    # Add tool-specific arguments
    if "tool_name" in kwargs:
        cmd.extend(["--tool-name", kwargs["tool_name"]])

    if "tool_args" in kwargs:
        for key, value in kwargs["tool_args"].items():
            # Serialize complex types as JSON for MCP CLI
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            cmd.extend(["--tool-arg", f"{key}={value}"])

    if "uri" in kwargs:
        cmd.extend(["--uri", kwargs["uri"]])

    # Add prompt-specific arguments
    if "prompt_name" in kwargs:
        cmd.extend(["--prompt-name", kwargs["prompt_name"]])

    if "prompt_args" in kwargs:
        for key, value in kwargs["prompt_args"].items():
            cmd.extend(["--prompt-args", f"{key}={value}"])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Inspector failed: {result.stderr}")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON response: {result.stdout}") from e


def call_tool(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call an MCP tool and return the result.

    Args:
        name: Tool name (e.g., "list_decks", "find_notes")
        args: Tool arguments as dict

    Returns:
        Tool execution result (extracted from MCP envelope).
    """
    result = run_inspector(
        "tools/call",
        tool_name=name,
        tool_args=args or {},
    )
    # Extract structured content from MCP response envelope
    if "structuredContent" in result:
        return result["structuredContent"]
    # Fallback to raw result
    return result


def list_tools() -> list[dict[str, Any]]:
    """List all available MCP tools.

    Returns:
        List of tool definitions.
    """
    result = run_inspector("tools/list")
    return result.get("tools", [])


def read_resource(uri: str) -> dict[str, Any]:
    """Read an MCP resource by URI.

    Args:
        uri: Resource URI (e.g., "anki://schema", "anki://stats/today")

    Returns:
        Resource content (extracted from MCP envelope).
    """
    result = run_inspector("resources/read", uri=uri)
    # Extract content from MCP response envelope
    if "contents" in result and len(result["contents"]) > 0:
        content = result["contents"][0]
        # Parse JSON text if present
        if "text" in content:
            try:
                return json.loads(content["text"])
            except json.JSONDecodeError:
                return {"text": content["text"]}
    # Fallback to raw result
    return result


def list_resources() -> list[dict[str, Any]]:
    """List all available MCP resources.

    Returns:
        List of resource definitions.
    """
    result = run_inspector("resources/list")
    return result.get("resources", [])


def list_prompts() -> list[dict[str, Any]]:
    """List all available MCP prompts.

    Returns:
        List of prompt definitions.
    """
    result = run_inspector("prompts/list")
    return result.get("prompts", [])


def schema_action_names(tool: dict) -> set[str]:
    """Extract the discriminated-union action names from a tool's inputSchema.

    Multi-action tools (card_management, model_fields, ...) expose a Pydantic
    discriminated union under ``inputSchema.properties.params``. Depending on the
    Pydantic/MCP-SDK version the action names surface in two places, and which
    one the live schema emits is not guaranteed -- so this collects from BOTH and
    unions them (ordering is therefore irrelevant):

    * ``params.oneOf`` / ``params.anyOf`` variants, each carrying an ``action``
      property whose ``const`` (single ``Literal``) or ``enum`` lists the value.
    * ``params.discriminator.mapping`` keys (the literal action values).

    Returns the set of action names; empty if the tool is not a multi-action
    tool or its schema advertises no actions.
    """
    params_schema = (
        tool.get("inputSchema", {})
        .get("properties", {})
        .get("params", {})
    )

    action_names: set[str] = set()

    # Collect from oneOf/anyOf union variants.
    for variant in params_schema.get("oneOf", []) or params_schema.get("anyOf", []):
        action_prop = variant.get("properties", {}).get("action", {})
        if "const" in action_prop:
            action_names.add(action_prop["const"])
        action_names.update(action_prop.get("enum", []))

    # Collect from the discriminator mapping keys.
    mapping = params_schema.get("discriminator", {}).get("mapping", {})
    action_names.update(mapping.keys())

    return action_names


def get_prompt(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Get an MCP prompt by name.

    Args:
        name: Prompt name (e.g., "twenty_rules")
        args: Optional prompt arguments as dict

    Returns:
        Prompt result including messages list.
    """
    kwargs: dict[str, Any] = {"prompt_name": name}
    if args:
        kwargs["prompt_args"] = args
    return run_inspector("prompts/get", **kwargs)
