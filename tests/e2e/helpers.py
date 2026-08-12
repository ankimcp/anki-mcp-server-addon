"""Helper functions for E2E tests using MCP Inspector CLI."""
from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
from typing import Any

SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:3141")

# Pinned EXACTLY, for reproducibility: an Inspector release that changes the CLI
# contract must not be able to break a release build the moment it lands on npm.
# The cost -- discovering upstream breakage late -- is paid instead by the
# `e2e-inspector-latest` nightly workflow, which runs this same suite with
# INSPECTOR_VERSION=latest so a breaking release shows up as a red canary.
#
# MUST stay in sync with INSPECTOR_VERSION in the Makefile, which uses the same
# value for its readiness probes. Override either via the environment:
#
#     INSPECTOR_VERSION=latest pytest tests/e2e/ -v
#     make e2e INSPECTOR_VERSION=latest
#
# `or` rather than a dict default: the E2E workflow exports the variable
# unconditionally and an empty value means "use the pin".
INSPECTOR_DEFAULT_VERSION = "2.1.0"
INSPECTOR_VERSION = os.environ.get("INSPECTOR_VERSION") or INSPECTOR_DEFAULT_VERSION
INSPECTOR_PACKAGE = f"@modelcontextprotocol/inspector@{INSPECTOR_VERSION}"

# The suite requires Inspector 2.x specifically, on both channels: a tool error
# exits non-zero, writes a `{"error":{"code":...}}` envelope to stderr (see
# RESULT_EMITTED_ERROR_CODES and _cli_error_code) and STILL writes the result
# envelope to stdout (see _is_error_result). 1.x does neither, so pointing
# INSPECTOR_VERSION at a 1.x release will fail the suite wholesale rather than
# degrade gracefully.

# Inspector CLI 2.x stderr error codes that still leave a usable result envelope
# on stdout: the call reached the server and completed, the result just reports
# that the tool itself failed.
RESULT_EMITTED_ERROR_CODES = {"tool_is_error"}


# Deliberately ABOVE the queue bridge's own 30s response timeout: when the two
# are equal, a client giving up and the server's bridge timing out are the same
# 30s wall of silence and the logs cannot tell them apart. With a wider client
# window a server-side stall comes back as an actual error envelope.
INSPECTOR_TIMEOUT = 45


# Bound on the post-kill drain. Killing the whole process group should make the
# pipes hit EOF immediately; this only exists so a descendant that escaped the
# group (double-fork into its own session) cannot wedge the suite forever.
_DRAIN_TIMEOUT = 10


def _as_text(captured: str | bytes | None) -> str:
    """Normalize captured output from a ``TimeoutExpired``.

    ``TimeoutExpired.stdout``/``.stderr`` carry the raw pre-timeout byte chunks
    even when the process was opened in text mode, so they need decoding before
    they can be treated like the ``str`` the rest of this module expects.
    """
    if captured is None:
        return ""
    if isinstance(captured, bytes):
        return captured.decode(errors="replace")
    return captured


# stderr lines that npm/node emit while BOOTSTRAPPING the CLI -- before the
# Inspector has even been loaded, let alone opened an HTTP client. Their
# presence is therefore no evidence that a request was delivered, so they must
# not veto the silent-timeout retry below.
#
# This is an allowlist, not a denylist, on purpose: anything unrecognised is
# treated as the process having genuinely said something and blocks the retry.
# `npm error` is deliberately absent -- that is a real failure signal.
_BOOTSTRAP_NOISE_PATTERNS = (
    # npm's own chatter, e.g. "npm warn exec The following package was not
    # found and will be installed: @modelcontextprotocol/inspector@2.1.0".
    re.compile(r"^npm (warn|notice)\b", re.I),
    # node process warnings, e.g. "(node:41) [DEP0040] DeprecationWarning: The
    # `punycode` module is deprecated." -- emitted by node itself at startup.
    re.compile(r"^\(node:\d+\)\s"),
    # The follow-up hint node prints after a warning.
    re.compile(r"^\(Use `node --trace-"),
    # npx's cold-cache install PROMPT banner, which is three lines: the header,
    # the package spec on its own line, and the "Ok to proceed?" echo. All three
    # are printed before node has loaded anything, so none of them is evidence
    # of a delivered request.
    #
    # Belt-and-braces for older npm only -- unreachable on the npm we run.
    # Verified against npm 10.9.8 (bundled with the node 22 CI uses): `npx -y`
    # on a cold cache prints NO install banner at all, and without -y it prints
    # a one-line "npm warn exec The following package was not found and will be
    # installed: <spec>", which the `^npm (warn|notice)` pattern above already
    # covers. Since run_inspector always passes -y, these three fire only under
    # an npm old enough to still prompt.
    #
    # Caveat, left as-is deliberately: the spec pattern is built from the
    # RESOLVED package string, so under INSPECTOR_VERSION=latest (the nightly
    # canary) an old npm's banner would name the concrete version and not match
    # -- the retry would then decline, which is the fail-safe direction. Making
    # the pattern version-agnostic would widen the allowlist for no benefit on
    # any supported npm.
    re.compile(r"^Need to install the following packages"),
    re.compile(r"^" + re.escape(INSPECTOR_PACKAGE)),
    re.compile(r"^Ok to proceed\?"),
)


def _unrecognised_stderr_line(stderr: str) -> str | None:
    """Return the first stderr line that is NOT npm/node bootstrap noise.

    Args:
        stderr: Captured stderr, already decoded.

    Returns:
        The offending line, or None when every non-blank line is recognised
        bootstrap noise (or there are none).
    """
    for line in stderr.splitlines():
        line = line.strip()
        if not line:
            continue
        if any(pattern.search(line) for pattern in _BOOTSTRAP_NOISE_PATTERNS):
            continue
        return line
    return None


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    """SIGKILL the child's entire process group, falling back to the child.

    ``start_new_session=True`` makes the child a process-group leader, so its
    pgid equals its pid and every process it spawns inherits that group.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (AttributeError, OSError):
        # No killpg (non-POSIX), or the group is already gone. Either way the
        # direct child still has to be killed and reaped.
        proc.kill()


def _spawn_inspector(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run one Inspector CLI process, killing its whole group if it hangs.

    Deliberately NOT ``subprocess.run(timeout=...)``: that kills only the direct
    child, which here is the ``npx`` shim. The real node process is a separate
    child of the shim, so it would survive the kill with its HTTP request still
    in flight and then run CONCURRENTLY with the retry -- two attempts at the
    same call against the same collection. Killing the process group instead
    genuinely serializes the attempts and leaves no orphaned node process
    behind.

    Raises:
        subprocess.TimeoutExpired: carrying whatever the process managed to
            emit, decoded as text, so the caller can distinguish a silent stall
            from a process that was talking.
    """
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=INSPECTOR_TIMEOUT)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            try:
                # communicate() keeps what it already read, so this resumes
                # rather than restarts and returns the full output as decoded
                # text. It also reaps the child.
                stdout, stderr = proc.communicate(timeout=_DRAIN_TIMEOUT)
            except subprocess.TimeoutExpired as drain:
                stdout, stderr = _as_text(drain.stdout), _as_text(drain.stderr)
            raise subprocess.TimeoutExpired(
                cmd, INSPECTOR_TIMEOUT, output=stdout, stderr=stderr
            ) from None
        except BaseException:
            # KeyboardInterrupt very much included. start_new_session=True puts
            # the child outside the terminal's foreground process group, so a
            # local Ctrl-C is delivered to pytest ONLY -- npx/node never see
            # SIGINT and would survive as orphans with a request still in
            # flight against the collection. subprocess.run() kills the child on
            # any exception; this restores that guarantee for the whole group.
            _kill_process_group(proc)
            raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _run_inspector(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run one Inspector CLI call, retrying once if the process never answers.

    Every call spawns a fresh ``npx`` process and a full suite makes hundreds of
    them, so a rare spawn stall is near-certain over a run: one produced no
    bytes at all on either channel and took down the v0.27.0 release.

    The retry deliberately covers ``tools/call`` as well -- the observed stall
    WAS a ``tools/call``, so restricting the retry to read-only methods would
    not have prevented the failure this exists for. That means it can in
    principle replay a write. Two things keep that acceptable:

    * Only a SILENT timeout is retried, where "silent" means: nothing on stdout,
      and nothing on stderr beyond recognised npm/node bootstrap noise. Real
      output means the process was talking, which is not the spawn stall this
      covers, so it propagates instead (see below).
    * The 30s/45s stagger is what makes the silence diagnostic, not merely what
      makes the logs legible. The queue bridge answers within ~30s
      (``anki_mcp_server/queue_bridge.py``, ``response_q.get(timeout=30)``), so
      a request that actually reaches the bridge comes back as a result or as a
      real error envelope before the client's 45s bound and never raises
      ``TimeoutExpired`` at all. A fully silent 45s therefore means the request
      almost certainly never reached the bridge -- and so there is no delivered
      write to replay.

    Residual risk: "almost certainly" is not "certainly". A request that the
    HTTP layer accepted but that stalled before being handed to the bridge is
    invisible to that reasoning, and the retry would re-send it. Nothing here
    makes the retry idempotent; it only makes the window it fires in narrow.
    """
    try:
        return _spawn_inspector(cmd)
    except subprocess.TimeoutExpired as exc:
        # TimeoutExpired.stdout/.stderr are raw BYTES even for a text-mode
        # process, so they always go through _as_text before being inspected.
        # (The TimeoutExpired _spawn_inspector raises already carries str, but
        # normalising unconditionally keeps this correct either way.)
        #
        # .strip() matters: a lone newline is truthy, so a bare `if exc.stdout`
        # would count "the process emitted one empty line" as evidence that it
        # was talking.
        stdout = _as_text(exc.stdout).strip()
        stderr = _as_text(exc.stderr)
        blocking_line = _unrecognised_stderr_line(stderr)

        if stdout or blocking_line is not None:
            # The process was talking, so this is a slow or stuck call rather
            # than a spawn that never got off the ground. Retrying would prove
            # nothing and could replay a write that really was delivered.
            #
            # ALWAYS announced: gating on stdout AND stderr previously meant a
            # single stray npm warning silently disabled the retry for the whole
            # run, with no signal at all that it had stopped working.
            reason = (
                "it wrote to stdout"
                if stdout
                else f"stderr had a non-bootstrap line: {blocking_line!r}"
            )
            print(
                f"Inspector CLI timed out after {INSPECTOR_TIMEOUT}s; NOT retrying "
                f"({reason}): {shlex.join(cmd)}"
            )
            raise
        # Visible under `pytest -s`, and in the report of any test that goes on
        # to fail -- a silent retry would hide a server that is genuinely slow.
        print(
            f"Inspector CLI produced nothing in {INSPECTOR_TIMEOUT}s, retrying once: "
            f"{shlex.join(cmd)}"
        )

    return _spawn_inspector(cmd)


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

    result = _run_inspector(cmd)

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
