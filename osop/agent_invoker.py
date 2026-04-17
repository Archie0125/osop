"""Subprocess wrapper around `claude -p` (Claude Code headless mode).

Used by `osop replay` v2 to execute `agent`-type nodes by spawning Claude
Code with a reconstructed prompt. We use `claude -p` instead of the raw
Anthropic API because the headless CLI gives us the full Claude Code tool
surface (Read/Write/Edit/Bash/Grep/Glob/...) that captured sessions
actually used — direct API calls don't.

The wrapper deliberately catches the "sharp edges" of `claude -p`:

  - Missing claude binary → return FAILED with a clear error
  - Empty prompt → reject before spawning (would otherwise produce a
    "Hello! What can I help you with?" greeting that wastes a turn)
  - --max-budget-usd hit → BUDGET_EXCEEDED status (parsed from the JSON
    error subtype)
  - Tool permission denial → TOOL_DENIED status (raw_json.permission_denials)
  - Auth failure → AUTH_FAILED status
  - Subprocess timeout → FAILED with timeout reason
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any


# Status taxonomy. We expose more detail than LiveLog's COMPLETED/FAILED/
# SKIPPED so the replayer can decide how to map each case.
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
STATUS_BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
STATUS_TOOL_DENIED = "TOOL_DENIED"
STATUS_AUTH_FAILED = "AUTH_FAILED"
STATUS_TIMEOUT = "TIMEOUT"


@dataclass
class AgentInvocationResult:
    status: str
    cost_usd: float = 0.0
    tokens_input: int = 0
    tokens_output: int = 0
    model: str = ""
    result_text: str = ""
    num_turns: int = 0
    permission_denials: list[str] = field(default_factory=list)
    raw_json: dict | None = None
    error: str | None = None


def _parse_json_response(stdout: str) -> dict | None:
    """Claude Code -p emits a single JSON object with --output-format json."""
    if not stdout.strip():
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        # Some versions emit JSONL; try last line.
        for line in reversed(stdout.strip().splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        return None


def _extract_token_counts(raw: dict) -> tuple[int, int]:
    """Token field naming varies across Claude Code versions; try several."""
    usage = raw.get("usage") or {}
    if not isinstance(usage, dict):
        return 0, 0
    inp = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    out = usage.get("output_tokens") or usage.get("completion_tokens") or 0
    return int(inp or 0), int(out or 0)


def _classify_response(raw: dict) -> tuple[str, str | None]:
    """Map the JSON response into our status taxonomy."""
    if raw.get("is_error"):
        subtype = (raw.get("subtype") or "").lower()
        if "budget" in subtype:
            return STATUS_BUDGET_EXCEEDED, f"max-budget exceeded: {subtype}"
        if "auth" in subtype:
            return STATUS_AUTH_FAILED, f"authentication failed: {subtype}"
        if "max_turn" in subtype:
            # Hitting max-turns is technically still a result; treat as completed
            # but flag in the result text. Caller can decide.
            return STATUS_COMPLETED, None
        msg = raw.get("error") or raw.get("message") or subtype or "unknown error"
        return STATUS_FAILED, str(msg)

    denials = raw.get("permission_denials") or []
    if isinstance(denials, list) and denials:
        return STATUS_TOOL_DENIED, f"tool permissions denied: {', '.join(map(str, denials))}"

    return STATUS_COMPLETED, None


def invoke_claude_p(
    *,
    prompt: str,
    cwd: str | None = None,
    max_budget_usd: float = 5.0,
    max_turns: int = 10,
    allowed_tools: list[str] | None = None,
    model: str | None = None,
    timeout_seconds: int = 600,
) -> AgentInvocationResult:
    """Spawn `claude -p` with the given prompt and parse the JSON result.

    Returns a structured AgentInvocationResult. Never raises for
    Claude-side problems — surfaces them as `status` / `error`. Raises
    only on programming errors (e.g. invalid types).
    """
    if not isinstance(prompt, str):
        raise TypeError("prompt must be a string")

    if not prompt.strip():
        return AgentInvocationResult(
            status=STATUS_FAILED,
            error="empty prompt; would produce a greeting from claude -p",
        )

    if shutil.which("claude") is None:
        return AgentInvocationResult(
            status=STATUS_FAILED,
            error="claude CLI not found on PATH (install Claude Code)",
        )

    args = [
        "claude",
        "-p",
        "--output-format", "json",
        "--max-turns", str(max_turns),
    ]
    # --max-budget-usd was added in newer Claude Code; pass best-effort
    if max_budget_usd is not None and max_budget_usd > 0:
        args.extend(["--max-budget-usd", f"{max_budget_usd:.4f}"])
    if allowed_tools:
        args.extend(["--allowedTools", ",".join(allowed_tools)])
    if model:
        args.extend(["--model", model])

    try:
        proc = subprocess.run(
            args,
            input=prompt,
            text=True,
            capture_output=True,
            cwd=cwd,
            timeout=timeout_seconds,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return AgentInvocationResult(
            status=STATUS_TIMEOUT,
            error=f"claude -p timed out after {timeout_seconds}s",
        )
    except Exception as e:
        return AgentInvocationResult(
            status=STATUS_FAILED,
            error=f"{type(e).__name__}: {e}",
        )

    raw = _parse_json_response(proc.stdout)

    if raw is None:
        # Subprocess produced no parseable JSON. Surface stderr for debugging.
        return AgentInvocationResult(
            status=STATUS_FAILED,
            error=(
                f"claude -p returned no JSON (exit={proc.returncode}); "
                f"stderr: {(proc.stderr or '')[:500]}"
            ),
            raw_json={"stdout": proc.stdout[:1000], "stderr": (proc.stderr or "")[:500]},
        )

    status, err = _classify_response(raw)
    inp, out = _extract_token_counts(raw)

    return AgentInvocationResult(
        status=status,
        cost_usd=float(raw.get("total_cost_usd") or raw.get("cost_usd") or 0.0),
        tokens_input=inp,
        tokens_output=out,
        model=str(raw.get("model") or ""),
        result_text=str(raw.get("result") or ""),
        num_turns=int(raw.get("num_turns") or 0),
        permission_denials=list(raw.get("permission_denials") or []),
        raw_json=raw,
        error=err,
    )
