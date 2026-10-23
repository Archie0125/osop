"""Sub-agent work must appear in the record.

Claude Code stores each sub-agent next to the session transcript:
  <session>.jsonl
  <session>/subagents/agent-<id>.jsonl
  <session>/subagents/agent-<id>.meta.json   ← {"toolUseId": ...} links it to the Agent call
"""

import json
from pathlib import Path

from osop.recorder import parse_transcript, synthesize


def _jsonl(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def _session_with_one_subagent(root: Path) -> Path:
    session = root / "sess-1.jsonl"
    _jsonl(session, [
        {"type": "user", "timestamp": "2026-10-23T10:00:00Z", "sessionId": "sess-1",
         "message": {"role": "user", "content": "verify the PR"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:00:01Z",
         "message": {"model": "claude-opus-5-5", "content": [
             {"type": "tool_use", "id": "toolu_agent", "name": "Agent",
              "input": {"description": "Verify PR", "prompt": "run the tests"}}]}},
        {"type": "user", "timestamp": "2026-10-23T10:00:02Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_agent", "content": "launched"}]}},
    ])
    subagents = root / "sess-1" / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-abc123.meta.json").write_text(
        json.dumps({"agentType": "general-purpose", "description": "Verify PR", "toolUseId": "toolu_agent"}),
        encoding="utf-8")
    _jsonl(subagents / "agent-abc123.jsonl", [
        {"type": "user", "timestamp": "2026-10-23T10:00:03Z", "agentId": "abc123", "isSidechain": True,
         "message": {"role": "user", "content": "run the tests"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:00:04Z", "isSidechain": True,
         "message": {"model": "claude-sonnet-5-5", "content": [
             {"type": "tool_use", "id": "toolu_sub_bash", "name": "Bash",
              "input": {"command": "python -m pytest -q"}}]}},
        {"type": "user", "timestamp": "2026-10-23T10:00:09Z", "isSidechain": True,
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_sub_bash", "content": "1 failed, 2 passed",
              "is_error": True}]}},
    ])
    return session


def test_subagent_tool_calls_are_in_the_record(tmp_path):
    log = synthesize(parse_transcript(_session_with_one_subagent(tmp_path)))

    child_records = [r for r in log["node_records"] if r.get("parent_id")]
    calls = [tc for r in child_records for tc in r.get("tool_calls", [])]
    assert [tc["input"]["command"] for tc in calls] == ["python -m pytest -q"]
    assert calls[0]["is_error"] is True


def test_node_ids_are_unique_and_parents_exist(tmp_path):
    log = synthesize(parse_transcript(_session_with_one_subagent(tmp_path)))

    ids = [r["node_id"] for r in log["node_records"]]
    assert len(ids) == len(set(ids))
    for r in log["node_records"]:
        if r.get("parent_id"):
            assert r["parent_id"] in ids
            assert r["parent_id"] != r["node_id"]


def test_nested_subagent_work_is_recorded(tmp_path):
    session = _session_with_one_subagent(tmp_path)
    subagents = tmp_path / "sess-1" / "subagents"
    # Sub-agent abc123 spawns its own sub-agent def456.
    with (subagents / "agent-abc123.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "assistant", "timestamp": "2026-10-23T10:00:10Z", "isSidechain": True,
                            "message": {"model": "claude-sonnet-5-5", "content": [
                                {"type": "tool_use", "id": "toolu_nested_agent", "name": "Agent",
                                 "input": {"description": "Dig deeper"}}]}}) + "\n")
    (subagents / "agent-def456.meta.json").write_text(
        json.dumps({"description": "Dig deeper", "toolUseId": "toolu_nested_agent", "spawnDepth": 2}),
        encoding="utf-8")
    _jsonl(subagents / "agent-def456.jsonl", [
        {"type": "user", "timestamp": "2026-10-23T10:00:11Z", "isSidechain": True,
         "message": {"role": "user", "content": "dig deeper"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:00:12Z", "isSidechain": True,
         "message": {"model": "claude-sonnet-5-5", "content": [
             {"type": "tool_use", "id": "toolu_nested_bash", "name": "Bash", "input": {"command": "git log -1"}}]}},
    ])

    log = synthesize(parse_transcript(session))

    commands = [tc["input"].get("command") for r in log["node_records"] for tc in r.get("tool_calls", [])]
    assert "git log -1" in commands


def test_meta_that_is_not_an_object_is_ignored(tmp_path):
    session = _session_with_one_subagent(tmp_path)
    subagents = tmp_path / "sess-1" / "subagents"
    (subagents / "agent-list.meta.json").write_text("[1, 2]", encoding="utf-8")
    (subagents / "agent-badid.meta.json").write_text('{"toolUseId": ["x"]}', encoding="utf-8")

    parsed = parse_transcript(session)

    agent_call = next(tc for n in parsed["nodes"] for tc in n.tool_calls if tc.tool == "Agent")
    assert agent_call.sub_transcript_id == "abc123"


def test_subagent_is_linked_to_its_agent_call(tmp_path):
    parsed = parse_transcript(_session_with_one_subagent(tmp_path))

    agent_call = next(tc for n in parsed["nodes"] for tc in n.tool_calls if tc.tool == "Agent")
    assert agent_call.sub_transcript_id == "abc123"
