"""Reconstruct an "imitation" prompt for `osop replay` v2.

When replaying a captured `.osop` (produced by `osop log` from a Claude
Code transcript), each agent node represents a phase of LLM work that
originally happened in a real session. To replay it faithfully, we hand
the new AI:

  1. The original user prompt (from the preceding human node)
  2. The complete list of tool calls the original agent made (input +
     output summaries)
  3. An instruction to reproduce the same actions, adapting only when
     the world has changed (file moved, API changed, etc.)

This is the "完全 imitation" mode the user explicitly chose. Less faithful
options (purist / guided) are deliberately not implemented in v2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Reference log discovery
# ---------------------------------------------------------------------------


def find_reference_log(osop_path: str | Path) -> Path | None:
    """Auto-discover the .osoplog paired with a given .osop file.

    Convention from `osop log`: outputs both files with the same stem,
    differing only in suffix (.osop.yaml vs .osoplog.yaml).
    """
    p = Path(osop_path)
    if not p.exists():
        return None
    name = p.name
    if name.endswith(".osop.yaml"):
        candidate = p.parent / (name[: -len(".osop.yaml")] + ".osoplog.yaml")
    elif name.endswith(".osop.yml"):
        candidate = p.parent / (name[: -len(".osop.yml")] + ".osoplog.yml")
    else:
        return None
    return candidate if candidate.exists() else None


def load_reference_log(log_path: str | Path) -> dict | None:
    """Load a .osoplog YAML; return None if missing or malformed."""
    p = Path(log_path)
    if not p.exists():
        return None
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def index_tool_calls_by_node(log: dict) -> dict[str, list[dict]]:
    """Build node_id → tool_calls[] mapping from a loaded .osoplog."""
    out: dict[str, list[dict]] = {}
    for rec in log.get("node_records") or []:
        if not isinstance(rec, dict):
            continue
        nid = rec.get("node_id")
        if not nid:
            continue
        calls = rec.get("tool_calls")
        if isinstance(calls, list) and calls:
            out[nid] = calls
    return out


def index_outputs_by_node(log: dict) -> dict[str, dict]:
    """Build node_id → outputs mapping for human-prompt extraction."""
    out: dict[str, dict] = {}
    for rec in log.get("node_records") or []:
        if not isinstance(rec, dict):
            continue
        nid = rec.get("node_id")
        if not nid:
            continue
        outputs = rec.get("outputs") or {}
        if isinstance(outputs, dict):
            out[nid] = outputs
    return out


# ---------------------------------------------------------------------------
# Preceding-prompt walk
# ---------------------------------------------------------------------------


def find_preceding_user_prompt(
    nodes: list[dict],
    edges: list[dict],
    target_node_id: str,
    outputs_by_node: dict[str, dict],
) -> str | None:
    """Walk backward from target_node to find the nearest human node prompt.

    The captured .osop graph is a sequential chain of human → agent → human
    → agent ... For an agent node, the immediately preceding human node
    holds the user_prompt that initiated this agent's work.

    We BFS backwards through edges; first human node whose outputs carry
    a `user_prompt` wins.
    """
    by_id = {n["id"]: n for n in nodes if isinstance(n, dict) and "id" in n}
    parents: dict[str, list[str]] = {nid: [] for nid in by_id}
    for e in edges:
        if not isinstance(e, dict):
            continue
        src = e.get("from")
        dst = e.get("to")
        if src in by_id and dst in by_id:
            parents[dst].append(src)

    if target_node_id not in by_id:
        return None

    visited: set[str] = set()
    queue: list[str] = list(parents.get(target_node_id, []))
    while queue:
        nid = queue.pop(0)
        if nid in visited:
            continue
        visited.add(nid)
        node = by_id[nid]
        if node.get("type") == "human":
            outs = outputs_by_node.get(nid) or {}
            prompt = outs.get("user_prompt")
            if isinstance(prompt, str) and prompt.strip():
                return prompt.strip()
        queue.extend(parents.get(nid, []))
    return None


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


_PROMPT_TEMPLATE = """\
[REPLAY CONTEXT]
You are replaying a previously recorded workflow step from an OSOP capture.
The original session was recorded as the .osop / .osoplog files; this is
a fresh execution of the same step. Goal: reproduce the original actions
on the same files / inputs so the new run can be diffed against the old.

[ORIGINAL USER REQUEST]
{user_prompt}

[ORIGINAL ACTIONS — perform these in order]
{actions_block}

[INSTRUCTIONS]
1. Execute the actions above in order, using the same tools when possible.
2. If a file no longer exists or has materially different content, use
   your judgment to recreate the intended outcome rather than failing —
   we want a comparable run, not a brittle mirror.
3. Keep responses brief. Don't re-narrate what you're about to do; just
   do it. Final reply: one short summary of what changed.
"""


def _format_actions(tool_calls: list[dict], max_per_call: int = 280) -> str:
    """Render the original tool calls as a numbered list for the prompt."""
    if not tool_calls:
        return "(no recorded tool actions for this step)"
    lines: list[str] = []
    for i, tc in enumerate(tool_calls, start=1):
        if not isinstance(tc, dict):
            continue
        tool = tc.get("tool", "?")
        inp = tc.get("input") or {}
        out = tc.get("output") or ""

        if isinstance(inp, dict):
            inp_summary = _format_input(inp, limit=max_per_call // 2)
        else:
            inp_summary = str(inp)[: max_per_call // 2]

        out_summary = _truncate(str(out), max_per_call // 2)
        lines.append(f"{i}. {tool}: {inp_summary}  →  {out_summary}")
    return "\n".join(lines) if lines else "(no actions)"


def _format_input(inp: dict, limit: int) -> str:
    """Compact one-line representation of a tool input dict."""
    if not inp:
        return "(no input)"
    parts = []
    for k, v in inp.items():
        if k.startswith("_"):
            continue
        if isinstance(v, str):
            v_str = _truncate(v, limit // max(len(inp), 1))
        else:
            v_str = str(v)
        parts.append(f"{k}={v_str}")
    return _truncate(", ".join(parts), limit)


def _truncate(text: str, n: int) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: max(n - 1, 1)] + "…"


def build_imitation_prompt(
    *,
    node: dict,
    user_prompt: str | None,
    original_tool_calls: list[dict],
) -> str:
    """Assemble the full imitation prompt sent to claude -p."""
    user_prompt_str = (user_prompt or "(no original user prompt — agent-only step)").strip()

    # Soft cap on prompt size: per-call summary + total length safeguard
    actions_block = _format_actions(original_tool_calls)
    if len(actions_block) > 12000:
        actions_block = _truncate(actions_block, 12000)

    return _PROMPT_TEMPLATE.format(
        user_prompt=user_prompt_str,
        actions_block=actions_block,
    )
