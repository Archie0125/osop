"""Parse Claude Code JSONL session transcripts → OSOP workflow + log.

The transcript at ~/.claude/projects/<slug>/<session-id>.jsonl is the
canonical record of every tool call. We parse it rather than re-asking
an LLM to "remember" what it did.

Phase segmentation (Option B):
  - Each real user message starts a new "phase".
  - All subsequent assistant turns + tool calls in that phase roll up
    into one `agent` (or `cli` / `api` / `human`) node, with full tool
    call detail preserved in `tool_calls[]` on the node record.
  - Sub-agent invocations (Agent tool) get their own child nodes.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """A single tool invocation with real timestamps."""

    tool: str
    input: dict
    started_at: str  # ISO
    ended_at: str | None  # ISO, None if no matching result
    duration_ms: int
    status: str  # COMPLETED | FAILED | PENDING
    output_summary: str
    is_error: bool
    sub_transcript_id: str | None = None  # for Agent tool dispatch


@dataclass
class PhaseNode:
    """One workflow node — either a user prompt or the agent work that follows."""

    node_id: str
    node_type: str  # agent | cli | api | human
    subtype: str
    name: str
    description: str
    started_at: str
    ended_at: str
    duration_ms: int
    status: str  # COMPLETED | FAILED
    tool_calls: list[ToolCall] = field(default_factory=list)
    user_prompt: str | None = None
    assistant_summary: str = ""
    children: list["PhaseNode"] = field(default_factory=list)
    parent_id: str | None = None


# ---------------------------------------------------------------------------
# Tool → node type mapping
# ---------------------------------------------------------------------------


# Tool name → (node_type, subtype)
_TOOL_TYPE: dict[str, tuple[str, str]] = {
    "Bash": ("cli", "script"),
    "BashOutput": ("cli", "script"),
    "KillShell": ("cli", "script"),
    "Read": ("agent", "llm"),
    "Write": ("agent", "llm"),
    "Edit": ("agent", "llm"),
    "Glob": ("agent", "llm"),
    "Grep": ("agent", "llm"),
    "NotebookEdit": ("agent", "llm"),
    "WebFetch": ("api", "rest"),
    "WebSearch": ("api", "rest"),
    "Agent": ("agent", "explore"),
    "Task": ("agent", "explore"),
    "AskUserQuestion": ("human", "review"),
    "TaskCreate": ("agent", "planner"),
    "TaskUpdate": ("agent", "planner"),
    "TaskList": ("agent", "planner"),
    "TodoWrite": ("agent", "planner"),
    "Skill": ("agent", "llm"),
    "ToolSearch": ("agent", "llm"),
    "ScheduleWakeup": ("agent", "planner"),
    "CronCreate": ("agent", "planner"),
    "EnterPlanMode": ("agent", "planner"),
    "ExitPlanMode": ("agent", "planner"),
}


def _tool_type(tool: str) -> tuple[str, str]:
    return _TOOL_TYPE.get(tool, ("agent", "llm"))


# ---------------------------------------------------------------------------
# JSONL event parsing
# ---------------------------------------------------------------------------


def _iter_jsonl(path: Path) -> Iterable[dict]:
    """Yield each non-empty JSON line from a transcript file."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _extract_tool_uses(event: dict) -> list[dict]:
    """Pull tool_use blocks from an assistant event."""
    msg = event.get("message") or {}
    content = msg.get("content") or []
    if not isinstance(content, list):
        return []
    return [c for c in content if isinstance(c, dict) and c.get("type") == "tool_use"]


def _extract_tool_results(event: dict) -> list[dict]:
    """Pull tool_result blocks from a user event."""
    msg = event.get("message") or {}
    content = msg.get("content") or []
    if not isinstance(content, list):
        return []
    return [c for c in content if isinstance(c, dict) and c.get("type") == "tool_result"]


def _extract_assistant_text(event: dict) -> str:
    """Concat text blocks from an assistant turn."""
    msg = event.get("message") or {}
    content = msg.get("content") or []
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for c in content:
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(c.get("text", ""))
    return "\n".join(p for p in parts if p).strip()


def _extract_user_prompt(event: dict) -> str | None:
    """Return the user's real prompt text, or None if this is a tool_result event."""
    msg = event.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip() or None
    if not isinstance(content, list):
        return None
    # If any item is a tool_result, this is a tool-result-carrying user event, not a real prompt.
    for c in content:
        if isinstance(c, dict) and c.get("type") == "tool_result":
            return None
    parts: list[str] = []
    for c in content:
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(c.get("text", ""))
        elif isinstance(c, str):
            parts.append(c)
    combined = "\n".join(p for p in parts if p).strip()
    return combined or None


def _ms_between(start: str, end: str | None) -> int:
    if not end:
        return 0
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return int((b - a).total_seconds() * 1000)
    except (ValueError, TypeError):
        return 0


def _summarize_output(content: Any, limit: int = 400) -> str:
    """Compact one-line summary of a tool_result for node records."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                parts.append(c.get("text", "") or c.get("content", ""))
            else:
                parts.append(str(c))
        text = " ".join(p for p in parts if p)
    elif content is None:
        text = ""
    else:
        text = str(content)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


# ---------------------------------------------------------------------------
# Transcript resolution
# ---------------------------------------------------------------------------


def _project_slug(cwd: Path) -> str:
    """Claude Code's project-dir slug: forward/back slashes + colons → dashes."""
    s = str(cwd).replace("\\", "-").replace("/", "-").replace(":", "")
    # Collapse multiple dashes and trim
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def resolve_transcript_path(
    arg: str | None = None,
    cwd: Path | None = None,
) -> Path:
    """Find the transcript file for the current project or a given session id.

    Resolution order:
      1. If `arg` is an existing path → use as-is.
      2. If `arg` looks like a session id → look under projects/<slug>/<id>.jsonl
         across all project slugs.
      3. If `arg` is None → return the most recently modified transcript under
         projects/<slug-for-cwd>/.
    """
    cwd = cwd or Path.cwd()

    if arg:
        direct = Path(arg)
        if direct.exists():
            return direct
        # Treat as session id
        projects_root = Path.home() / ".claude" / "projects"
        for proj in projects_root.glob("*"):
            candidate = proj / f"{arg}.jsonl"
            if candidate.exists():
                return candidate
        raise FileNotFoundError(f"No transcript found for: {arg}")

    # Auto-resolve: most recent transcript for cwd
    slug = _project_slug(cwd)
    proj_dir = Path.home() / ".claude" / "projects" / slug
    if not proj_dir.exists():
        # Fallback: any project dir ending with last path component
        last = cwd.name
        for p in (Path.home() / ".claude" / "projects").glob(f"*{last}"):
            if p.is_dir():
                proj_dir = p
                break
    if not proj_dir.exists():
        raise FileNotFoundError(
            f"No transcript directory under ~/.claude/projects matching {cwd}"
        )
    candidates = sorted(
        proj_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"No transcripts in {proj_dir}")
    return candidates[0]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_transcript(path: Path, subagent_root: Path | None = None) -> dict:
    """Parse a transcript into a structured session record.

    Returns:
        {
          "session_id": str,
          "cwd": str,
          "model": str,
          "started_at": str,
          "ended_at": str,
          "duration_ms": int,
          "nodes": list[PhaseNode],
        }
    """
    events = list(_iter_jsonl(path))
    if not events:
        raise ValueError(f"Transcript is empty: {path}")

    subagent_root = subagent_root or (path.parent / "subagents")

    # Index tool_use → tool_result by id
    tool_use_by_id: dict[str, tuple[str, dict]] = {}  # id → (timestamp, tool_use)
    tool_result_by_id: dict[str, tuple[str, dict, bool]] = {}  # id → (ts, result, is_error)

    session_id = ""
    cwd = ""
    model = ""
    first_ts = ""
    last_ts = ""

    for ev in events:
        ts = ev.get("timestamp", "")
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        if not session_id:
            session_id = ev.get("sessionId", "") or ev.get("session_id", "")
        if not cwd:
            cwd = ev.get("cwd", "") or ""
        msg = ev.get("message") or {}
        if isinstance(msg, dict) and not model:
            model = msg.get("model", "") or model
        for tu in _extract_tool_uses(ev):
            tid = tu.get("id", "")
            if tid:
                tool_use_by_id[tid] = (ts, tu)
        for tr in _extract_tool_results(ev):
            tid = tr.get("tool_use_id", "")
            if tid:
                tool_result_by_id[tid] = (
                    ts,
                    tr,
                    bool(tr.get("is_error")),
                )

    # Walk events in order to build phase nodes
    phases: list[PhaseNode] = []
    current: PhaseNode | None = None
    phase_counter = 0
    agent_counter = 0

    def _close(node: PhaseNode | None, end_ts: str) -> None:
        if node is None:
            return
        node.ended_at = end_ts or node.started_at
        node.duration_ms = _ms_between(node.started_at, node.ended_at)
        phases.append(node)

    for ev in events:
        ev_type = ev.get("type")
        ts = ev.get("timestamp", "")

        if ev_type == "user":
            prompt = _extract_user_prompt(ev)
            if prompt is not None:
                # Real user message — close prior phase, open human phase
                _close(current, ts or (current.started_at if current else ""))
                phase_counter += 1
                human_node = PhaseNode(
                    node_id=f"user_{phase_counter}",
                    node_type="human",
                    subtype="input",
                    name=_derive_human_name(prompt),
                    description=_truncate(prompt, 240),
                    started_at=ts,
                    ended_at=ts,
                    duration_ms=0,
                    status="COMPLETED",
                    user_prompt=prompt,
                )
                phases.append(human_node)
                # Open a fresh agent phase to collect what follows
                agent_counter += 1
                current = PhaseNode(
                    node_id=f"agent_{agent_counter}",
                    node_type="agent",
                    subtype="llm",
                    name="",  # filled on close
                    description="",
                    started_at=ts,
                    ended_at=ts,
                    duration_ms=0,
                    status="COMPLETED",
                )
            # else: tool_result event — attach results to open tool calls
            else:
                if current is None:
                    continue
                for tr in _extract_tool_results(ev):
                    tid = tr.get("tool_use_id", "")
                    for tc in current.tool_calls:
                        # Match by id stored via Bash/tool_use detection below
                        if tc.input.get("_id") == tid:
                            tc.ended_at = ts
                            tc.duration_ms = _ms_between(tc.started_at, ts)
                            tc.output_summary = _summarize_output(tr.get("content"))
                            tc.is_error = bool(tr.get("is_error"))
                            tc.status = "FAILED" if tc.is_error else "COMPLETED"
                            break

        elif ev_type == "assistant":
            if current is None:
                # Assistant turn with no opened phase (rare — transcript replay)
                phase_counter += 1
                agent_counter += 1
                current = PhaseNode(
                    node_id=f"agent_{agent_counter}",
                    node_type="agent",
                    subtype="llm",
                    name="",
                    description="",
                    started_at=ts,
                    ended_at=ts,
                    duration_ms=0,
                    status="COMPLETED",
                )
            # Accumulate assistant text
            text = _extract_assistant_text(ev)
            if text:
                if current.assistant_summary:
                    current.assistant_summary += "\n"
                current.assistant_summary += text
            # Record tool calls
            for tu in _extract_tool_uses(ev):
                tid = tu.get("id", "")
                tool_name = tu.get("name", "unknown")
                inp = dict(tu.get("input") or {})
                inp["_id"] = tid
                tc = ToolCall(
                    tool=tool_name,
                    input=inp,
                    started_at=ts,
                    ended_at=None,
                    duration_ms=0,
                    status="PENDING",
                    output_summary="",
                    is_error=False,
                )
                # Sub-agent linkage (Agent / Task tool)
                if tool_name in ("Agent", "Task"):
                    sub_id = _find_sub_transcript_id(subagent_root, tid)
                    if sub_id:
                        tc.sub_transcript_id = sub_id
                current.tool_calls.append(tc)

        # attachment / permission-mode / system events: ignored for node graph
        else:
            continue

    # Close trailing phase
    _close(current, last_ts)

    # Post-process: name + classify agent phases based on tool mix
    for p in phases:
        if p.node_type == "agent" and not p.name:
            p.name, p.node_type, p.subtype = _classify_agent_phase(p)

    # Recursively parse sub-agents
    for p in phases:
        for tc in p.tool_calls:
            if tc.sub_transcript_id:
                sub_path = subagent_root / f"agent-{tc.sub_transcript_id}.jsonl"
                if sub_path.exists():
                    try:
                        sub = parse_transcript(sub_path, subagent_root=subagent_root)
                        for child in sub["nodes"]:
                            child.parent_id = p.node_id
                            p.children.append(child)
                    except (ValueError, FileNotFoundError):
                        pass

    started = phases[0].started_at if phases else first_ts
    ended = phases[-1].ended_at if phases else last_ts
    return {
        "session_id": session_id,
        "cwd": cwd,
        "model": model,
        "started_at": started,
        "ended_at": ended,
        "duration_ms": _ms_between(started, ended),
        "nodes": phases,
    }


def _find_sub_transcript_id(subagent_root: Path, tool_use_id: str) -> str | None:
    """Sub-agent transcripts are named agent-<id>.jsonl; no stable mapping
    from tool_use_id exists, so we return None and let the caller fall back
    to parsing all sub-transcripts if desired.

    Future: if Claude Code adds a mapping, fill this in. For now, sub-agent
    detail is left in the outer transcript (which records the full Agent
    tool_result).
    """
    _ = (subagent_root, tool_use_id)
    return None


# ---------------------------------------------------------------------------
# Node naming / classification
# ---------------------------------------------------------------------------


def _derive_human_name(prompt: str) -> str:
    first = prompt.strip().splitlines()[0] if prompt.strip() else "User prompt"
    return _truncate(first, 60)


def _classify_agent_phase(p: PhaseNode) -> tuple[str, str, str]:
    """Pick name + type + subtype for an agent phase based on its tool mix."""
    if not p.tool_calls:
        # No tools: pure reasoning / text response
        name = _truncate(_first_line(p.assistant_summary) or "Respond", 60)
        return name, "agent", "llm"

    # Count tools by category
    counts: dict[str, int] = {}
    for tc in p.tool_calls:
        counts[tc.tool] = counts.get(tc.tool, 0) + 1

    # Primary category wins
    cli_count = sum(counts.get(t, 0) for t in ("Bash", "BashOutput", "KillShell"))
    api_count = sum(counts.get(t, 0) for t in ("WebFetch", "WebSearch"))
    human_count = counts.get("AskUserQuestion", 0)
    agent_count = len(p.tool_calls) - cli_count - api_count - human_count

    if human_count and human_count >= max(cli_count, api_count, agent_count):
        return "Ask user", "human", "review"
    if cli_count and cli_count >= max(agent_count, api_count):
        name = _summarize_cli(p)
        return name, "cli", "script"
    if api_count and api_count >= max(agent_count, cli_count):
        name = _summarize_api(p)
        return name, "api", "rest"

    # Default: agent work
    name = _summarize_agent(p)
    return name, "agent", "llm"


def _summarize_cli(p: PhaseNode) -> str:
    cmds = [tc.input.get("command", "") for tc in p.tool_calls if tc.tool == "Bash"]
    if cmds:
        verb = cmds[0].split()[0] if cmds[0] else "Run"
        if len(cmds) == 1:
            return _truncate(f"Run `{verb}`", 60)
        return f"Run {len(cmds)} shell commands (e.g. {verb})"
    return f"Shell work ({len(p.tool_calls)} calls)"


def _summarize_api(p: PhaseNode) -> str:
    urls = [tc.input.get("url", "") for tc in p.tool_calls if tc.tool == "WebFetch"]
    queries = [tc.input.get("query", "") for tc in p.tool_calls if tc.tool == "WebSearch"]
    if urls:
        return _truncate(f"Fetch {urls[0]}", 60) if len(urls) == 1 else f"Fetch {len(urls)} URLs"
    if queries:
        return _truncate(f"Web search: {queries[0]}", 60)
    return f"API calls ({len(p.tool_calls)})"


def _summarize_agent(p: PhaseNode) -> str:
    by_tool: dict[str, int] = {}
    for tc in p.tool_calls:
        by_tool[tc.tool] = by_tool.get(tc.tool, 0) + 1
    parts = [f"{n}× {t}" for t, n in sorted(by_tool.items(), key=lambda x: -x[1])[:3]]
    return _truncate(", ".join(parts) or "Agent work", 60)


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _truncate(text: str, n: int) -> str:
    text = text.strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


# ---------------------------------------------------------------------------
# Synthesis → .osop + .osoplog dicts
# ---------------------------------------------------------------------------


def synthesize(
    parsed: dict,
    short_desc: str = "session",
    tags: list[str] | None = None,
) -> tuple[dict, dict]:
    """Turn a parsed transcript into (osop_dict, osoplog_dict)."""
    tags = list(tags or [])
    tags = ["claude-code"] + [t for t in tags if t and t != "claude-code"]

    nodes = parsed["nodes"]

    safe_desc = short_desc[len("session-"):] if short_desc.startswith("session-") else short_desc
    wf_id = f"session-{safe_desc}"
    run_id = str(uuid.uuid4())

    # Flatten children for .osoplog (they keep parent_id)
    flat: list[PhaseNode] = []
    for n in nodes:
        flat.append(n)
        for child in n.children:
            flat.append(child)

    # Build .osop workflow (nodes + edges)
    osop_nodes = []
    for n in flat:
        entry = {
            "id": n.node_id,
            "type": n.node_type,
            "name": n.name or n.node_id,
            "description": n.description or (n.assistant_summary[:240] if n.assistant_summary else ""),
        }
        if n.subtype:
            entry["subtype"] = n.subtype
        if n.parent_id:
            entry["parent"] = n.parent_id
        osop_nodes.append(entry)

    # Edges: sequential between top-level nodes
    edges = []
    for i in range(len(nodes) - 1):
        edges.append({
            "from": nodes[i].node_id,
            "to": nodes[i + 1].node_id,
            "mode": "sequential",
        })

    osop_doc = {
        "osop_version": "1.0",
        "id": wf_id,
        "name": _derive_workflow_name(nodes, short_desc),
        "description": _derive_workflow_description(nodes),
        "tags": tags,
        "nodes": osop_nodes,
        "edges": edges,
    }

    # Build .osoplog
    started = parsed["started_at"]
    ended = parsed["ended_at"]
    node_records = []
    overall_status = "COMPLETED"
    for n in flat:
        tool_calls_raw = []
        for tc in n.tool_calls:
            inp = {k: v for k, v in tc.input.items() if k != "_id"}
            tool_calls_raw.append({
                "tool": tc.tool,
                "started_at": tc.started_at,
                "ended_at": tc.ended_at or tc.started_at,
                "duration_ms": tc.duration_ms,
                "status": tc.status if tc.status != "PENDING" else "COMPLETED",
                "input": _trim_input(inp),
                "output": tc.output_summary,
                "is_error": tc.is_error,
            })
        by_tool: dict[str, int] = {}
        for tc in n.tool_calls:
            by_tool[tc.tool] = by_tool.get(tc.tool, 0) + 1
        tools_used = [{"tool": t, "calls": c} for t, c in sorted(by_tool.items(), key=lambda x: -x[1])]

        rec = {
            "node_id": n.node_id,
            "node_type": n.node_type,
            "attempt": 1,
            "status": n.status,
            "started_at": n.started_at,
            "ended_at": n.ended_at,
            "duration_ms": n.duration_ms,
        }
        if n.parent_id:
            rec["parent_id"] = n.parent_id
        if n.user_prompt:
            rec["outputs"] = {"user_prompt": _truncate(n.user_prompt, 2000)}
        elif n.assistant_summary:
            rec["outputs"] = {"assistant_summary": _truncate(n.assistant_summary, 2000)}
        if tools_used:
            rec["tools_used"] = tools_used
        if tool_calls_raw:
            rec["tool_calls"] = tool_calls_raw
        node_records.append(rec)
        if n.status == "FAILED":
            overall_status = "FAILED"

    osoplog_doc = {
        "osoplog_version": "1.0",
        "run_id": run_id,
        "workflow_id": wf_id,
        "status": overall_status,
        "started_at": started,
        "ended_at": ended,
        "duration_ms": parsed["duration_ms"],
        "runtime": {
            "agent": "claude-code",
            "model": parsed.get("model") or "unknown",
            "session_id": parsed.get("session_id", ""),
            "source": "transcript-parser",
        },
        "node_records": node_records,
        "result_summary": _derive_result_summary(nodes),
    }

    return osop_doc, osoplog_doc


def _trim_input(inp: dict, limit: int = 500) -> dict:
    """Trim large tool inputs so .osoplog stays readable."""
    out: dict = {}
    for k, v in inp.items():
        if isinstance(v, str) and len(v) > limit:
            out[k] = v[: limit - 1] + "…"
        else:
            out[k] = v
    return out


def _derive_workflow_name(nodes: list[PhaseNode], short_desc: str) -> str:
    human_nodes = [n for n in nodes if n.node_type == "human" and n.user_prompt]
    if human_nodes:
        return _truncate(human_nodes[0].name or short_desc, 80)
    return short_desc.replace("-", " ").title()


def _derive_workflow_description(nodes: list[PhaseNode]) -> str:
    human_nodes = [n for n in nodes if n.node_type == "human" and n.user_prompt]
    if human_nodes:
        return _truncate(human_nodes[0].user_prompt or "", 240)
    return f"Session captured from Claude Code transcript ({len(nodes)} nodes)."


def _derive_result_summary(nodes: list[PhaseNode]) -> str:
    if not nodes:
        return "Empty session."
    total = sum(len(n.tool_calls) for n in nodes)
    phases = len([n for n in nodes if n.node_type != "human"])
    humans = len([n for n in nodes if n.node_type == "human"])
    failed = sum(1 for n in nodes if n.status == "FAILED")
    summary = (
        f"{humans} user turn(s), {phases} agent phase(s), {total} tool call(s)"
    )
    if failed:
        summary += f", {failed} failed"
    return summary + "."


# ---------------------------------------------------------------------------
# YAML rendering (pretty, stable key order)
# ---------------------------------------------------------------------------


def to_yaml(doc: dict) -> str:
    """Render a dict as YAML with stable key order and readable formatting."""
    try:
        import yaml  # type: ignore
    except ImportError as e:
        raise RuntimeError("PyYAML is required. pip install pyyaml") from e

    class _Dumper(yaml.SafeDumper):
        pass

    def _str_representer(dumper, data):
        if "\n" in data or len(data) > 120:
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data)

    _Dumper.add_representer(str, _str_representer)
    return yaml.dump(
        doc,
        Dumper=_Dumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )
