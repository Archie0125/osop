"""Parser fixes from the full-code review: evidence must not go missing."""

import json
from pathlib import Path

import pytest

from osop.recorder import parse_transcript
from osop.recorder.receipt import render_receipt
from osop.recorder.transcript import _project_slug, _walk, resolve_transcript_path


def _jsonl(path: Path, events: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


def _calls(parsed: dict) -> list:
    return [tc for n in _walk(parsed["nodes"]) for tc in n.tool_calls]


def test_result_arriving_after_the_next_prompt_is_attached(tmp_path):
    # The user typed again while pytest was still running; its failing result came later.
    session = _jsonl(tmp_path / "s.jsonl", [
        {"type": "user", "timestamp": "2026-10-23T10:00:00Z", "message": {"role": "user", "content": "run tests"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:00:01Z", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest"}}]}},
        {"type": "user", "timestamp": "2026-10-23T10:00:02Z", "message": {"role": "user", "content": "also lint"}},
        {"type": "user", "timestamp": "2026-10-23T10:00:30Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "Exit code 1\n2 failed", "is_error": True}]}},
    ])

    (pytest_call,) = _calls(parse_transcript(session))
    assert pytest_call.status == "FAILED" and pytest_call.is_error
    assert "2 failed" in pytest_call.output


def test_subagent_without_meta_is_still_recorded(tmp_path):
    session = _jsonl(tmp_path / "s.jsonl", [
        {"type": "user", "timestamp": "2026-10-23T10:00:00Z", "message": {"role": "user", "content": "go"}},
    ])
    subagents = tmp_path / "s" / "subagents"
    subagents.mkdir(parents=True)
    _jsonl(subagents / "agent-orphan.jsonl", [
        {"type": "user", "timestamp": "2026-10-23T10:01:00Z", "message": {"role": "user", "content": "sub"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:01:01Z", "message": {"content": [
            {"type": "tool_use", "id": "s1", "name": "Bash", "input": {"command": "git push"}}]}},
    ])

    commands = [tc.input.get("command") for tc in _calls(parse_transcript(session))]
    assert "git push" in commands


@pytest.mark.parametrize("orphan, child", [("a", "b"), ("z", "b")])
def test_linked_child_of_an_unlinked_subagent_is_counted_once(tmp_path, orphan, child):
    session = _jsonl(tmp_path / "s.jsonl", [
        {"type": "user", "timestamp": "2026-10-23T10:00:00Z", "message": {"role": "user", "content": "go"}},
    ])
    subagents = tmp_path / "s" / "subagents"
    subagents.mkdir(parents=True)
    # The orphan has no meta file; it spawns `child`, which does have one.
    _jsonl(subagents / f"agent-{orphan}.jsonl", [
        {"type": "user", "timestamp": "2026-10-23T10:01:00Z", "message": {"role": "user", "content": "sub"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:01:01Z", "message": {"content": [
            {"type": "tool_use", "id": "spawn", "name": "Agent", "input": {"description": "child"}}]}},
    ])
    (subagents / f"agent-{child}.meta.json").write_text(json.dumps({"toolUseId": "spawn"}), encoding="utf-8")
    _jsonl(subagents / f"agent-{child}.jsonl", [
        {"type": "user", "timestamp": "2026-10-23T10:02:00Z", "message": {"role": "user", "content": "do"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:02:01Z", "message": {"content": [
            {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "make build"}}]}},
    ])

    commands = [tc.input.get("command") for tc in _calls(parse_transcript(session))]
    assert commands.count("make build") == 1


def test_unreadable_subagent_file_does_not_lose_the_session(tmp_path):
    session = _jsonl(tmp_path / "s.jsonl", [
        {"type": "user", "timestamp": "2026-10-23T10:00:00Z", "message": {"role": "user", "content": "go"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:00:01Z", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git status"}}]}},
    ])
    subagents = tmp_path / "s" / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-bad.jsonl").write_bytes(b'{"type": "user"}\n\xff\xfe broken\n')

    commands = [tc.input.get("command") for tc in _calls(parse_transcript(session))]
    assert "git status" in commands


@pytest.mark.parametrize("bad", ["meta_not_utf8", "null_line", "directory"])
def test_one_broken_subagent_file_does_not_lose_the_session(tmp_path, bad):
    session = _jsonl(tmp_path / "s.jsonl", [
        {"type": "user", "timestamp": "2026-10-23T10:00:00Z", "message": {"role": "user", "content": "go"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:00:01Z", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git status"}}]}},
    ])
    subagents = tmp_path / "s" / "subagents"
    subagents.mkdir(parents=True)
    if bad == "meta_not_utf8":
        (subagents / "agent-x.meta.json").write_bytes(b'{"toolUseId": "\xff"}')
    elif bad == "null_line":
        (subagents / "agent-x.jsonl").write_text('null\n[1, 2]\n{"type": "user"}\n', encoding="utf-8")
    else:
        (subagents / "agent-x.jsonl").mkdir()

    commands = [tc.input.get("command") for tc in _calls(parse_transcript(session))]
    assert "git status" in commands


def _session_with_subagent_lines(tmp_path, sub_lines: list[dict], meta: dict | None = None) -> Path:
    session = _jsonl(tmp_path / "s.jsonl", [
        {"type": "user", "timestamp": "2026-10-23T10:00:00Z", "message": {"role": "user", "content": "go"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:00:01Z", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git status"}}]}},
    ])
    subagents = tmp_path / "s" / "subagents"
    subagents.mkdir(parents=True)
    _jsonl(subagents / "agent-a.jsonl", sub_lines)
    if meta is not None:
        (subagents / "agent-a.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return session


def test_non_object_message_field_does_not_crash(tmp_path):
    session = _session_with_subagent_lines(tmp_path, [{"type": "assistant", "message": "broken"},
                                                      {"type": "user", "message": ["x"]}])
    assert "git status" in [tc.input.get("command") for tc in _calls(parse_transcript(session))]


def test_subagent_that_links_to_itself_does_not_recurse_forever(tmp_path):
    session = _session_with_subagent_lines(tmp_path, [
        {"type": "user", "timestamp": "2026-10-23T10:01:00Z", "message": {"role": "user", "content": "sub"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:01:01Z", "message": {"content": [
            {"type": "tool_use", "id": "loop", "name": "Agent", "input": {}},
            {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "make"}}]}},
    ], meta={"toolUseId": "loop"})
    commands = [tc.input.get("command") for tc in _calls(parse_transcript(session))]
    assert "git status" in commands and commands.count("make") == 1


def test_one_malformed_tool_input_keeps_the_rest_of_the_subagent(tmp_path):
    session = _session_with_subagent_lines(tmp_path, [
        {"type": "user", "timestamp": "2026-10-23T10:01:00Z", "message": {"role": "user", "content": "sub"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:01:01Z", "message": {"content": [
            {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "git push"}},
            {"type": "tool_use", "id": "bad", "name": "Bash", "input": "x"}]}},
    ])
    assert "git push" in [tc.input.get("command") for tc in _calls(parse_transcript(session))]


def test_non_string_text_does_not_crash(tmp_path):
    session = _jsonl(tmp_path / "s.jsonl", [
        {"type": "user", "timestamp": "2026-10-23T10:00:00Z", "message": {"role": "user", "content": [
            {"type": "text", "text": 7}, {"type": "text", "text": "go"}]}},
        {"type": "assistant", "timestamp": "2026-10-23T10:00:01Z", "message": {"content": [
            {"type": "text", "text": 123},
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git status"}}]}},
        {"type": "user", "timestamp": "2026-10-23T10:00:02Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": 5}]}]}},
    ])
    assert [tc.input.get("command") for tc in _calls(parse_transcript(session))] == ["git status"]


def test_unreadable_subagent_is_reported_not_silently_dropped(tmp_path):
    session = _session_with_subagent_lines(tmp_path, [{"type": "user", "message": {"content": "ok"}}])
    (tmp_path / "s" / "subagents" / "agent-x.jsonl").mkdir()

    parsed = parse_transcript(session)
    assert [name for name, _ in parsed["skipped"]] == ["agent-x.jsonl"]
    receipt = render_receipt(parsed, source=session)
    assert "could not be read" in receipt and "agent-x.jsonl" in receipt


def test_linked_subagent_with_missing_transcript_is_reported(tmp_path):
    session = _jsonl(tmp_path / "s.jsonl", [
        {"type": "user", "timestamp": "2026-10-23T10:00:00Z", "message": {"role": "user", "content": "go"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:00:01Z", "message": {"content": [
            {"type": "tool_use", "id": "toolu_1", "name": "Agent", "input": {"description": "check"}}]}},
    ])
    subagents = tmp_path / "s" / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-abc.meta.json").write_text(json.dumps({"toolUseId": "toolu_1"}), encoding="utf-8")

    assert [name for name, _ in parse_transcript(session)["skipped"]] == ["agent-abc.jsonl"]


def test_non_string_ids_do_not_abort(tmp_path):
    session = _session_with_subagent_lines(tmp_path, [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Agent", "id": []}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": {}, "content": "x"}]}},
    ])
    assert "git status" in [tc.input.get("command") for tc in _calls(parse_transcript(session))]


def test_undecodable_bytes_do_not_drop_the_rest_of_a_transcript(tmp_path):
    session = tmp_path / "s.jsonl"
    session.write_bytes(
        b'{"type": "user", "timestamp": "2026-10-23T10:00:00Z", "message": {"role": "user", "content": "go \xff"}}\n'
        b'{"type": "assistant", "timestamp": "2026-10-23T10:00:01Z", "message": {"content": '
        b'[{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git status"}}]}}\n')

    assert [tc.input.get("command") for tc in _calls(parse_transcript(session))] == ["git status"]


@pytest.mark.parametrize("cwd, slug", [
    (r"C:\Users\A7\Desktop\osop", "C--Users-A7-Desktop-osop"),
    (r"C:\Users\A7\Desktop\other_web\medirank", "C--Users-A7-Desktop-other-web-medirank"),
    ("/work/team-a/app", "-work-team-a-app"),
])
def test_project_slug_matches_claude_code(cwd, slug):
    assert _project_slug(Path(cwd)) == slug


def test_no_fallback_to_another_project_with_the_same_suffix(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    other = tmp_path / ".claude" / "projects" / "-work-team-b-app"
    other.mkdir(parents=True)
    (other / "x.jsonl").write_text("{}\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        resolve_transcript_path(None, cwd=Path("/work/team-a/app"))


def test_full_tool_output_is_kept(tmp_path):
    long_output = "." * 3000 + " 1 failed, 99 passed"
    session = _jsonl(tmp_path / "s.jsonl", [
        {"type": "user", "timestamp": "2026-10-23T10:00:00Z", "message": {"role": "user", "content": "test"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:00:01Z", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest"}}]}},
        {"type": "user", "timestamp": "2026-10-23T10:00:09Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": long_output, "is_error": False}]}},
    ])

    (call,) = _calls(parse_transcript(session))
    assert call.output.endswith("1 failed, 99 passed") and len(call.output) > 3000


def test_whitespace_only_command_does_not_crash(tmp_path):
    session = _jsonl(tmp_path / "s.jsonl", [
        {"type": "user", "timestamp": "2026-10-23T10:00:00Z", "message": {"role": "user", "content": "x"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:00:01Z", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "\n"}}]}},
    ])
    assert len(_calls(parse_transcript(session))) == 1
