"""Helper functions for E2E tests using MCP Inspector CLI."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any

SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:3141")

# Deliberately unpinned -- the suite tracks the latest Inspector release and
# requires 2.x: a tool error exits non-zero with a JSON error envelope on
# stderr while the result envelope is still written to stdout.
INSPECTOR_PACKAGE = "@modelcontextprotocol/inspector"

# Inspector CLI 2.x stderr error codes that still leave a usable result envelope
# on stdout: the call reached the server and completed, the result just reports
# that the tool itself failed.
RESULT_EMITTED_ERROR_CODES = {"tool_is_error"}


def _cli_error_code(stderr: str) -> str | None:
    """Extract ``error.code`` from the Inspector CLI's JSON error envelope.

    The 2.x CLI writes exactly ``{"error":{"code":...,"message":...}}`` to
    stderr on failure, but npx and node may prepend unrelated warning lines, so
    each line is tried and the first one carrying a code wins.

    Args:
        stderr: Captured stderr from the CLI process.

    Returns:
        The error code, or None if stderr holds no recognizable envelope.
    """
    for line in stderr.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError:
            continue
        error = envelope.get("error") if isinstance(envelope, dict) else None
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"]
    return None


def _is_error_result(stdout: str) -> bool:
    """Report whether stdout holds an MCP result envelope flagged ``isError``.

    Checked alongside the stderr error code so a non-zero exit is tolerated even
    if the envelope on stderr is missing or reshaped: stdout carrying a real
    ``isError`` result is proof on its own that the call completed. Stays
    fail-closed for aborts that emit no result -- empty stdout (unknown tool) or
    non-result JSON -- which report False and let the caller raise.

    Args:
        stdout: Captured stdout from the CLI process.

    Returns:
        True if stdout parses as a JSON object with a truthy ``isError``.
    """
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and bool(payload.get("isError"))


def run_inspector(method: str, **kwargs) -> dict[str, Any]:
    """Run MCP Inspector CLI and return parsed JSON response.

    A tool returning ``isError: true`` is NOT treated as a failure: the CLI
    exits non-zero in that case but still prints the result envelope to
    stdout, so the envelope is returned and callers assert on ``isError``
    themselves. Invalid arguments to an
    existing tool land here too -- the server answers with an ``isError``
    envelope, which is returned, not raised. Only failures that leave no usable
    result raise: CLI-level usage errors (missing ``--tool-name``, an unknown
    ``--method``), unknown tool names, and an unreachable server.

    Args:
        method: MCP method (e.g., "tools/list", "tools/call", "resources/list")
        **kwargs: Additional arguments (tool_name, tool_args, uri, etc.)

    Returns:
        Parsed JSON response from the server.

    Raises:
        RuntimeError: If CLI fails or returns invalid JSON.
    """
    cmd = [
        # -y: a no-op once the package is cached, but under `pytest -s` stdin is
        # the real TTY and npx's install prompt would block invisibly until the
        # timeout (default pytest capture points stdin at /dev/null, no prompt).
        "npx", "-y", INSPECTOR_PACKAGE, "--cli",
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

    # A non-zero exit is only tolerated when the call demonstrably completed with
    # an error *result*: either channel saying so is enough, since the stderr
    # envelope is the less reliable of the two.
    if result.returncode != 0 and not (
        _cli_error_code(result.stderr) in RESULT_EMITTED_ERROR_CODES
        or _is_error_result(result.stdout)
    ):
        raise RuntimeError(
            f"Inspector failed (exit {result.returncode}): "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Invalid JSON response (exit {result.returncode}): "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        ) from e


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


def delete_filtered_deck(deck_id: int) -> dict[str, Any]:
    """Delete a filtered deck, returning its cards to their home decks.

    Intended for test cleanup (``finally`` blocks), so it never raises on a
    failed delete: a cleanup failure is printed and surfaces in pytest output
    instead of masking the assertion that actually failed.

    Args:
        deck_id: ID of the filtered deck to delete.

    Returns:
        Tool execution result.
    """
    try:
        result = call_tool("filtered_deck", {
            "params": {"action": "delete", "deck_id": deck_id},
        })
    except Exception as e:
        print(f"cleanup failed: could not delete filtered deck {deck_id}: {e}")
        return {"isError": True, "error": str(e)}
    if result.get("isError"):
        print(
            f"cleanup failed: could not delete filtered deck {deck_id}: {result}"
        )
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
