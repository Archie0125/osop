"""Execute a .osop workflow with live .osoplog streaming.

Uses the LiveLog SDK (osop.live_log) to flush a partial record on every
node boundary, so a crash mid-run still leaves a readable osoplog.

v1 scope: cli + human node types. agent and api nodes are recorded as
SKIPPED with a clear TODO marker — wiring in LLM providers and HTTP
fetchers is a deliberate v2 follow-up so v1 remains predictable and
side-effect-free without explicit flags.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable

from osop.agent_invoker import (
    STATUS_COMPLETED as _AGENT_COMPLETED,
    invoke_claude_p,
)
from osop.imitation import (
    build_imitation_prompt,
    find_preceding_user_prompt,
    find_reference_log,
    index_outputs_by_node,
    index_tool_calls_by_node,
    load_reference_log,
)


# Patterns that match destructive shell intents. We don't try to be
# exhaustive — the goal is to catch the common foot-guns and force a
# human ack before execution. The user can pass --yes to bypass.
_DESTRUCTIVE_PATTERNS = [
    r"\brm\s+-[rRfF]+\b",
    r"\bDROP\s+(TABLE|DATABASE|SCHEMA)\b",
    r"\bTRUNCATE\s+TABLE\b",
    r"\bDELETE\s+FROM\s+\w+(\s|;|$)(?!.*\bWHERE\b)",
    r"\bgit\s+push\s+.*--force\b",
    r"\bgit\s+push\s+.*-f\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+branch\s+-D\b",
    r"\bgit\s+clean\s+-[fxXd]+\b",
    r":\(\)\s*\{[^}]*:\|:[^}]*\}",  # fork bomb
    r"\bmkfs\.\w+\b",
    r"\bdd\s+if=.*of=/dev/",
    r">\s*/dev/sd\w",
]


def is_destructive(cmd: str) -> bool:
    """Return True if the command matches a known destructive pattern."""
    return any(re.search(p, cmd, re.IGNORECASE) for p in _DESTRUCTIVE_PATTERNS)


def topo_sort(nodes: list[dict], edges: list[dict]) -> list[dict]:
    """Kahn's algorithm. Returns nodes in execution order.

    Raises ValueError if the graph has a cycle or references unknown ids.
    """
    by_id: dict[str, dict] = {}
    for n in nodes:
        if not isinstance(n, dict) or "id" not in n:
            raise ValueError(f"node missing id: {n!r}")
        nid = n["id"]
        if nid in by_id:
            raise ValueError(f"duplicate node id: {nid}")
        by_id[nid] = n

    in_degree: dict[str, int] = {nid: 0 for nid in by_id}
    children: dict[str, list[str]] = {nid: [] for nid in by_id}
    for e in edges:
        if not isinstance(e, dict):
            continue
        src = e.get("from")
        dst = e.get("to")
        if src not in by_id or dst not in by_id:
            raise ValueError(f"edge references unknown node: {src} → {dst}")
        in_degree[dst] += 1
        children[src].append(dst)

    queue = [nid for nid, d in in_degree.items() if d == 0]
    # Preserve original node order for determinism among independent roots
    order_index = {n["id"]: i for i, n in enumerate(nodes)}
    queue.sort(key=lambda nid: order_index[nid])

    out: list[dict] = []
    while queue:
        nid = queue.pop(0)
        out.append(by_id[nid])
        new_ready: list[str] = []
        for c in children[nid]:
            in_degree[c] -= 1
            if in_degree[c] == 0:
                new_ready.append(c)
        new_ready.sort(key=lambda x: order_index[x])
        queue.extend(new_ready)

    if len(out) != len(by_id):
        unreached = [nid for nid, d in in_degree.items() if d > 0]
        raise ValueError(f"cycle or unreachable dependency in workflow: {unreached}")
    return out


def _extract_command(node: dict) -> str:
    """A cli node may carry its command in several places depending on author style."""
    for key in ("command", "cmd"):
        if node.get(key):
            return str(node[key])
    inp = node.get("inputs") or node.get("io") or {}
    if isinstance(inp, dict):
        for key in ("command", "cmd", "shell"):
            if inp.get(key):
                return str(inp[key])
    # Fall back: nothing to run
    return ""


def execute_cli_node(
    node: dict,
    *,
    allow_exec: bool,
    confirm_destructive: Callable[[str], bool],
    default_timeout_seconds: int = 300,
    env: dict | None = None,
) -> dict:
    """Run a cli node. Returns a dict suitable for LiveLog ctx.output().

    Per-node ``timeout_sec`` (in the node dict) overrides the default.
    """
    cmd = _extract_command(node)
    if not cmd:
        return {"status": "SKIPPED", "reason": "no command on node"}

    if not allow_exec:
        return {
            "status": "SKIPPED",
            "dry_run": True,
            "command": cmd,
            "reason": "dry-run; pass --allow-exec to execute",
        }

    if is_destructive(cmd):
        if not confirm_destructive(cmd):
            return {
                "status": "SKIPPED",
                "command": cmd,
                "reason": "destructive command not confirmed",
            }

    timeout = int(node.get("timeout_sec") or default_timeout_seconds)

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "FAILED",
            "command": cmd,
            "error": f"timeout after {timeout}s",
        }
    except Exception as e:
        return {
            "status": "FAILED",
            "command": cmd,
            "error": f"{type(e).__name__}: {e}",
        }

    return {
        "status": "COMPLETED" if result.returncode == 0 else "FAILED",
        "command": cmd,
        "exit_code": result.returncode,
        "stdout": _trim(result.stdout, 2000),
        "stderr": _trim(result.stderr, 2000),
    }


def execute_agent_node(
    node: dict,
    *,
    user_prompt: str | None,
    original_tool_calls: list[dict],
    cwd: str | None,
    max_budget_usd: float,
    max_turns: int,
    allowed_tools: list[str] | None,
    timeout_seconds: int = 600,
) -> dict:
    """Execute an agent node by spawning `claude -p` in imitation mode.

    Returns a dict for LiveLog ctx.output(). When no reference behavior
    exists (no user_prompt + no tool_calls) we SKIP — imitation needs a
    source of truth.
    """
    if not (user_prompt and user_prompt.strip()) and not original_tool_calls:
        return {
            "status": "SKIPPED",
            "reason": "no reference behavior (user_prompt + tool_calls both empty); cannot imitate",
        }

    prompt = build_imitation_prompt(
        node=node,
        user_prompt=user_prompt,
        original_tool_calls=original_tool_calls,
    )
    res = invoke_claude_p(
        prompt=prompt,
        cwd=cwd,
        max_budget_usd=max_budget_usd,
        max_turns=max_turns,
        allowed_tools=allowed_tools,
        timeout_seconds=timeout_seconds,
    )

    out: dict = {
        "cost_usd": round(res.cost_usd, 6),
        "tokens_input": res.tokens_input,
        "tokens_output": res.tokens_output,
        "model": res.model,
        "num_turns": res.num_turns,
        "result_text": _trim(res.result_text, 2000),
    }
    if res.permission_denials:
        out["permission_denials"] = res.permission_denials

    if res.status == _AGENT_COMPLETED:
        out["status"] = "COMPLETED"
    else:
        out["status"] = "FAILED"
        out["error"] = res.error or f"claude -p returned {res.status}"
        out["claude_status"] = res.status  # surface the original taxonomy
    return out


def execute_human_node(node: dict, *, interactive: bool, prompt_fn: Callable[[str], str] | None = None) -> dict:
    """Pause for human input when --interactive; otherwise SKIP."""
    if not interactive:
        return {"status": "SKIPPED", "reason": "human node; pass --interactive to prompt"}

    name = node.get("name", node.get("id", "?"))
    desc = node.get("description", "")
    hint = f"\n[HUMAN] {name}"
    if desc:
        hint += f"\n  {desc}"
    hint += "\n  Reply: <text> = COMPLETED, 'skip' = SKIPPED, 'fail' = FAILED\n  > "

    ask = prompt_fn or input
    answer = ask(hint)
    s = (answer or "").strip()
    low = s.lower()
    if low == "skip":
        return {"status": "SKIPPED", "reason": "user skipped"}
    if low == "fail":
        return {"status": "FAILED", "error": "user marked failed"}
    return {"status": "COMPLETED", "user_input": s}


def detect_non_sequential_edges(edges: list[dict]) -> list[str]:
    """Return a list of edge-mode strings that aren't 'sequential'.

    v1 collapses everything to topological execution order; this helper
    lets the CLI surface a loud warning before running, and the workflow
    metadata records the limitation in the .osoplog.
    """
    seen: dict[str, int] = {}
    for e in edges:
        if not isinstance(e, dict):
            continue
        mode = e.get("mode", "sequential")
        if mode != "sequential":
            seen[mode] = seen.get(mode, 0) + 1
    return [f"{mode}×{n}" for mode, n in sorted(seen.items())]


def execute_workflow(
    workflow: dict,
    log,  # osop.live_log.LiveLog
    *,
    allow_exec: bool,
    interactive: bool,
    continue_on_error: bool,
    confirm_destructive: Callable[[str], bool],
    cli_timeout_seconds: int = 300,
    inputs: dict | None = None,
    env: dict | None = None,
    on_node_start: Callable[[dict], None] | None = None,
    on_node_done: Callable[[dict, dict], None] | None = None,
    # Agent imitation execution (v2). When no reference log is given,
    # agent nodes are SKIPPED unless skip_agents is False AND a paired
    # log can be auto-discovered.
    osop_path: str | Path | None = None,
    reference_log_path: str | Path | None = None,
    skip_agents: bool = False,
    agent_max_budget_usd: float = 5.0,
    agent_max_turns: int = 10,
    agent_allowed_tools: list[str] | None = None,
    agent_cwd: str | None = None,
    agent_timeout_seconds: int = 600,
) -> dict:
    """Execute every node in topological order, streaming via LiveLog.

    Args:
        inputs: workflow-level inputs (reserved; v1 ignores).
        env: subprocess env passed to cli nodes (None = inherit).

    Returns:
        {
            "status": "COMPLETED" | "FAILED" | "HALTED",
            "counts": {"COMPLETED": n, "FAILED": n, "SKIPPED": n, "BLOCKED": n},
            "halted_on": <node_id or None>,
            "non_sequential_modes": [...],   # v1 limitation surface
        }
    """
    _ = inputs  # v1 placeholder; reserved for v2 input templating
    nodes = workflow.get("nodes") or []
    edges = workflow.get("edges") or []
    ordered = topo_sort(nodes, edges)
    non_seq = detect_non_sequential_edges(edges)

    # Resolve reference .osoplog for agent imitation. Auto-discover from
    # osop_path if not explicit. Missing reference → agent nodes will SKIP.
    ref_log: dict | None = None
    if not skip_agents:
        ref_path: Path | None
        if reference_log_path:
            ref_path = Path(reference_log_path)
        elif osop_path:
            ref_path = find_reference_log(osop_path)
        else:
            ref_path = None
        if ref_path:
            ref_log = load_reference_log(ref_path)

    tool_calls_by_node: dict[str, list[dict]] = (
        index_tool_calls_by_node(ref_log) if ref_log else {}
    )
    outputs_by_node: dict[str, dict] = (
        index_outputs_by_node(ref_log) if ref_log else {}
    )

    counts = {"COMPLETED": 0, "FAILED": 0, "SKIPPED": 0, "BLOCKED": 0}
    halted_on: str | None = None

    def _emit(node: dict, payload: dict) -> str:
        """Run one node through LiveLog. Returns its terminal status."""
        nid_inner = node["id"]
        ntype_inner = node.get("type", "agent")
        if on_node_start:
            on_node_start(node)

        with log.node(nid_inner, node_type=ntype_inner) as ctx:
            data = dict(payload)
            status_inner = data.pop("status", "COMPLETED")
            if status_inner == "FAILED":
                error_msg = data.pop("error", "see outputs")
                if data:
                    ctx.output(**data)
                ctx.fail(error=error_msg)
            elif status_inner in ("SKIPPED", "BLOCKED"):
                reason = data.pop("reason", "")
                if data:
                    ctx.output(**data)
                # LiveLog has no .block(); reuse skip and tag the kind
                ctx.skip(reason=(f"[{status_inner}] {reason}").strip())
            else:
                if data:
                    ctx.output(**data)

        # Counts use the unified status set even for BLOCKED (recorded as
        # SKIPPED in LiveLog, but tracked separately here).
        counts[status_inner] = counts.get(status_inner, 0) + 1
        if on_node_done:
            on_node_done(node, {"status": status_inner, **payload})
        return status_inner

    halted_index: int | None = None
    for i, node in enumerate(ordered):
        ntype = node.get("type", "agent")

        if ntype == "cli":
            result = execute_cli_node(
                node,
                allow_exec=allow_exec,
                confirm_destructive=confirm_destructive,
                default_timeout_seconds=cli_timeout_seconds,
                env=env,
            )
        elif ntype == "human":
            result = execute_human_node(node, interactive=interactive)
        elif ntype == "agent":
            if skip_agents:
                result = {
                    "status": "SKIPPED",
                    "reason": "agent execution disabled (--no-agent)",
                }
            elif ref_log is None:
                result = {
                    "status": "SKIPPED",
                    "reason": "no reference .osoplog found; cannot imitate agent step",
                }
            else:
                result = execute_agent_node(
                    node,
                    user_prompt=find_preceding_user_prompt(
                        nodes, edges, node["id"], outputs_by_node
                    ),
                    original_tool_calls=tool_calls_by_node.get(node["id"], []),
                    cwd=agent_cwd,
                    max_budget_usd=agent_max_budget_usd,
                    max_turns=agent_max_turns,
                    allowed_tools=agent_allowed_tools,
                    timeout_seconds=agent_timeout_seconds,
                )
        elif ntype == "api":
            result = {
                "status": "SKIPPED",
                "reason": "api node not yet executable; tracked for v3",
            }
        else:
            result = {"status": "SKIPPED", "reason": f"unknown node type: {ntype}"}

        status = _emit(node, result)

        if status == "FAILED" and not continue_on_error:
            halted_on = node["id"]
            halted_index = i
            break

    # If we halted, mark every remaining node as BLOCKED so the .osoplog
    # has a complete record (no silent gaps that look like a crash).
    if halted_index is not None:
        for blocked_node in ordered[halted_index + 1:]:
            _emit(blocked_node, {
                "status": "BLOCKED",
                "reason": f"upstream node '{halted_on}' failed; not reached",
            })

    overall_status = "HALTED" if halted_on else ("FAILED" if counts["FAILED"] else "COMPLETED")
    return {
        "status": overall_status,
        "counts": counts,
        "halted_on": halted_on,
        "non_sequential_modes": non_seq,
    }


def _trim(text: str | None, limit: int) -> str:
    if not text:
        return ""
    text = text.rstrip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
