"""Parse Claude Code JSONL session transcripts into phases of tool calls.

The transcript at ~/.claude/projects/<slug>/<session-id>.jsonl is the
canonical record of every tool call. We parse it rather than re-asking
an LLM to "remember" what it did.

Phase segmentation:
  - Each real user message starts a new "phase".
  - The assistant turns and tool calls that follow roll up into one agent
    phase, with full per-call detail in `tool_calls`.
  - Sub-agent transcripts become child phases.
"""

from __future__ import annotations

import json
import re
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
    output: str = ""  # full result text; verdicts read this, never the truncated summary


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
# JSONL event parsing
# ---------------------------------------------------------------------------


def _iter_jsonl(path: Path) -> Iterable[dict]:
    """Yield each JSON object line from a transcript file.

    Transcripts are external data: undecodable bytes are replaced rather than losing the file,
    and lines that are not JSON objects (bad JSON, null, arrays) are skipped.
    """
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                yield event


def _message(event: dict) -> dict:
    """The event's message object, or {} when it is missing or not an object."""
    msg = event.get("message")
    return msg if isinstance(msg, dict) else {}


def _extract_tool_uses(event: dict) -> list[dict]:
    """Pull tool_use blocks from an assistant event."""
    msg = _message(event)
    content = msg.get("content") or []
    if not isinstance(content, list):
        return []
    return [c for c in content if isinstance(c, dict) and c.get("type") == "tool_use"]


def _extract_tool_results(event: dict) -> list[dict]:
    """Pull tool_result blocks from a user event."""
    msg = _message(event)
    content = msg.get("content") or []
    if not isinstance(content, list):
        return []
    return [c for c in content if isinstance(c, dict) and c.get("type") == "tool_result"]


def _extract_assistant_text(event: dict) -> str:
    """Concat text blocks from an assistant turn."""
    msg = _message(event)
    content = msg.get("content") or []
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [c.get("text") for c in content if isinstance(c, dict) and c.get("type") == "text"]
    return "\n".join(p for p in parts if isinstance(p, str) and p).strip()


def _extract_user_prompt(event: dict) -> str | None:
    """Return the text a human typed, or None for tool results and injected messages.

    Claude Code marks typed prompts with origin.kind == "human". Slash-command
    echoes, skill bodies (isMeta) and task notifications are not prompts.
    """
    origin = event.get("origin")
    kind = origin.get("kind") if isinstance(origin, dict) else None
    if event.get("isMeta") or (kind and kind != "human"):
        return None
    msg = _message(event)
    content = msg.get("content")
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        # If any item is a tool_result, this is a tool-result-carrying user event, not a real prompt.
        if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
            return None
        parts = [c.get("text") if isinstance(c, dict) and c.get("type") == "text" else c
                 for c in content if isinstance(c, str) or (isinstance(c, dict) and c.get("type") == "text")]
        text = "\n".join(p for p in parts if isinstance(p, str) and p).strip()
    else:
        return None
    # Transcripts written before origin existed: recognise slash-command echoes by their tags.
    if not text or (not kind and text.startswith(("<command-", "<local-command-"))):
        return None
    return text


def _ms_between(start: str, end: str | None) -> int:
    if not end:
        return 0
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return int((b - a).total_seconds() * 1000)
    except (ValueError, TypeError):
        return 0


def _output_text(content: Any) -> str:
    """Full text of a tool_result, with terminal colour codes and binary noise removed."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for c in content:
            value = (c.get("text", "") or c.get("content", "")) if isinstance(c, dict) else c
            parts.append(value if isinstance(value, str) else str(value))
        text = " ".join(p for p in parts if p)
    elif content is None:
        text = ""
    else:
        text = str(content)
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)  # terminal colour codes
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)  # binary noise, e.g. crash dumps


def _summarize_output(text: str, limit: int = 400) -> str:
    """Compact one-line summary of a tool result, for display only."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        # Keep both ends: errors tend to come first, test summaries last.
        half = (limit - 3) // 2
        text = text[:half] + " … " + text[-half:]
    return text


# ---------------------------------------------------------------------------
# Transcript resolution
# ---------------------------------------------------------------------------


def _project_slug(cwd: Path) -> str:
    """Claude Code's project-dir name: every non-alphanumeric character becomes '-'.

    C:\\Users\\A7\\Desktop\\osop → C--Users-A7-Desktop-osop; /work/team-a/app → -work-team-a-app.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


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
    # No guessing by name suffix: that can pick another project's transcript.
    proj_dir = Path.home() / ".claude" / "projects" / _project_slug(cwd)
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


@dataclass
class _ParseState:
    """Shared by one session's main transcript and all its sub-agent transcripts."""

    attached: set[str] = field(default_factory=set)  # sub-agent ids already attached
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (file name, why it could not be read)


def parse_transcript(path: Path, subagent_root: Path | None = None, _state: _ParseState | None = None) -> dict:
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

    is_top_level = subagent_root is None
    subagent_root = subagent_root or (path.parent / path.stem / "subagents")
    subagents = _subagent_index(subagent_root)
    # A sub-agent is attached once per session, so a link cycle (a sub-agent spawning itself) cannot recurse.
    state = _state or _ParseState()

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

    # Walk events in order to build phase nodes
    phases: list[PhaseNode] = []
    current: PhaseNode | None = None
    # Calls still waiting for a result, by tool_use id. A result can arrive after the user has
    # already typed the next prompt, so results are matched across phases, not only the current one.
    pending: dict[str, ToolCall] = {}
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
            else:
                for tr in _extract_tool_results(ev):
                    tc = pending.pop(tr.get("tool_use_id", ""), None)
                    if tc is None:
                        continue
                    tc.ended_at = ts
                    tc.duration_ms = _ms_between(tc.started_at, ts)
                    tc.output = _output_text(tr.get("content"))
                    tc.output_summary = _summarize_output(tc.output)
                    tc.is_error = bool(tr.get("is_error"))
                    tc.status = "FAILED" if tc.is_error else "COMPLETED"

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
                raw_input = tu.get("input")
                inp = dict(raw_input) if isinstance(raw_input, dict) else {}
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
                    tc.sub_transcript_id = subagents.get(tid)
                current.tool_calls.append(tc)
                if tid:
                    pending[tid] = tc

        # attachment / permission-mode / system events: ignored for node graph
        else:
            continue

    # Close trailing phase
    _close(current, last_ts)

    # Recursively parse sub-agents
    for p in phases:
        for tc in p.tool_calls:
            if tc.sub_transcript_id:
                _attach_subagent(p, subagent_root, tc.sub_transcript_id, tc.sub_transcript_id, state)

    # Sub-agent transcripts no Agent call links to (no meta file, or a stale one) still hold evidence.
    # "Linked" means spawned by an Agent call in ANY transcript of this session, including orphans:
    # an orphan's own children get attached through the orphan and must not be added twice.
    if is_top_level and phases:
        sub_paths = sorted(subagent_root.glob("agent-*.jsonl"))
        spawn_ids = {tid for p in [path, *sub_paths] for tid in _agent_call_ids(p)}
        spawned = {subagents[tid] for tid in spawn_ids if tid in subagents}
        ids = [sub_path.stem[len("agent-"):] for sub_path in sub_paths]
        # Roots first (nothing spawned them), so their children attach under them, not as orphans.
        for sub_id in [i for i in ids if i not in spawned] + [i for i in ids if i in spawned]:
            _attach_subagent(phases[-1], subagent_root, sub_id, f"unlinked-{sub_id}", state)

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
        "skipped": state.skipped,
    }


def _subagent_index(subagent_root: Path) -> dict[str, str]:
    """Map Agent tool_use id → sub-agent id, from agent-<id>.meta.json files."""
    index: dict[str, str] = {}
    for meta_path in subagent_root.glob("agent-*.meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # unreadable, not UTF-8, or not JSON
            continue
        tool_use_id = meta.get("toolUseId") if isinstance(meta, dict) else None
        if isinstance(tool_use_id, str) and tool_use_id:
            index[tool_use_id] = meta_path.name[len("agent-"):-len(".meta.json")]
    return index


def _agent_call_ids(path: Path) -> set[str]:
    """tool_use ids of the Agent / Task calls in a transcript; none if the file can't be read."""
    try:
        return {
            tu.get("id", "") for ev in _iter_jsonl(path) for tu in _extract_tool_uses(ev)
            if tu.get("name") in ("Agent", "Task")
        }
    except (OSError, ValueError):  # UnicodeDecodeError is a ValueError
        return set()


def _attach_subagent(parent: PhaseNode, subagent_root: Path, sub_id: str, prefix: str,
                     state: _ParseState) -> None:
    sub_path = subagent_root / f"agent-{sub_id}.jsonl"
    if sub_id in state.attached or not sub_path.exists():
        return
    state.attached.add(sub_id)  # before parsing, so a sub-agent that spawns itself stops here
    try:
        sub = parse_transcript(sub_path, subagent_root=subagent_root, _state=state)
    except Exception as e:  # noqa: BLE001 — one malformed file must not lose the session; it is reported
        state.skipped.append((sub_path.name, f"{type(e).__name__}: {e}"))
        return
    # Every transcript numbers its phases from 1, so prefix the sub-agent's ids.
    _prefix_ids(sub["nodes"], prefix)
    for child in sub["nodes"]:
        child.parent_id = parent.node_id
        parent.children.append(child)


def _prefix_ids(nodes: list[PhaseNode], prefix: str) -> None:
    for n in nodes:
        n.node_id = f"{prefix}.{n.node_id}"
        if n.parent_id:
            n.parent_id = f"{prefix}.{n.parent_id}"
        _prefix_ids(n.children, prefix)


def _walk(nodes: list[PhaseNode]) -> Iterable[PhaseNode]:
    for n in nodes:
        yield n
        yield from _walk(n.children)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _derive_human_name(prompt: str) -> str:
    first = prompt.strip().splitlines()[0] if prompt.strip() else "User prompt"
    return _truncate(first, 60)


def _truncate(text: str, n: int) -> str:
    text = text.strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"
