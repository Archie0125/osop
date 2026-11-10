"""A background command's result arrives later, as a task notification naming the output file Claude Code wrote."""

import json
from pathlib import Path

from osop.recorder import parse_transcript, transcript
from osop.recorder.receipt import render_receipt


def _session(tmp_path: Path, command: str, summary: str | None, printed: str | None,
             status: str = "completed", final: str = "All 5 tests pass.") -> Path:
    """A background Bash run, its completion notice (None: none arrived) and the agent's final message.

    `printed` is what the output file holds; None leaves no file behind (Claude Code cleans them up after days).
    """
    out = tmp_path / "b1.output"
    if printed is not None:
        out.write_text(printed, encoding="utf-8")
    events = [
        {"type": "user", "timestamp": "2026-11-06T10:00:00Z", "sessionId": "s", "cwd": "/work/demo",
         "origin": {"kind": "human"}, "message": {"role": "user", "content": "run the tests"}},
        {"type": "assistant", "timestamp": "2026-11-06T10:01:00Z", "message": {"model": "m", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": command, "run_in_background": True}}]}},
        {"type": "user", "timestamp": "2026-11-06T10:01:01Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": False,
             "content": f"Command running in background with ID: b1. Output is being written to: {out}"}]}},
    ]
    if summary is not None:
        notice = (f"<task-notification>\n<task-id>b1</task-id>\n<tool-use-id>t1</tool-use-id>\n"
                  f"<output-file>{out}</output-file>\n<status>{status}</status>\n"
                  f"<summary>{summary}</summary>\n</task-notification>")
        events.append({"type": "user", "timestamp": "2026-11-06T10:04:00Z",
                       "origin": {"kind": "task-notification"}, "message": {"role": "user", "content": notice}})
    events.append({"type": "assistant", "timestamp": "2026-11-06T10:05:00Z",
                   "message": {"model": "m", "content": [{"type": "text", "text": final}]}})
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


def _section(path: Path, title: str) -> str:
    receipt = render_receipt(parse_transcript(path), source=path)
    return receipt.split(f"## {title}\n", 1)[1].split("\n## ", 1)[0]


# The notice's exit code is `tail`'s; the test's own `exit=0` is only in the output file.
LOGGED = 'pytest -q > log.txt 2>&1; echo "exit=$?"; tail -3 log.txt'


def test_a_passing_background_run_backs_the_claim(tmp_path):
    # The description is the agent's own words: "failed" in it is not test output.
    path = _session(tmp_path, LOGGED, 'Background command "Re-run the 2 failed tests" completed (exit code 0)',
                    "exit=0\n5 passed in 1.20s\n")
    claims = _section(path, "Claims")
    assert "✅ backed" in claims and "output read from its task file" in claims
    assert "✅" in _section(path, "Tests") and "⏳" not in _section(path, "Tests")


def test_a_failure_in_the_output_file_contradicts_the_claim(tmp_path):
    path = _session(tmp_path, LOGGED, 'Background command "Run the tests" completed (exit code 0)',
                    "exit=1\n1 failed, 4 passed in 1.30s\n")
    assert "❌ contradicted" in _section(path, "Claims")


def test_without_the_output_file_the_notice_exit_code_still_counts(tmp_path):
    path = _session(tmp_path, "pytest -q", 'Background command "Run the tests" failed with exit code 1', None)
    claims = _section(path, "Claims")
    assert "❌ contradicted" in claims and "exit 1" in claims and "output file could not be read" in claims


def test_without_the_output_file_a_status_hidden_by_a_later_step_stays_unclear(tmp_path):
    path = _session(tmp_path, LOGGED, 'Background command "Run the tests" completed (exit code 0)', None)
    claims = _section(path, "Claims")
    assert "⚠️ cannot tell" in claims and "`;` after the test hides its exit status" in claims


def test_an_output_file_over_the_limit_is_not_read_in_part(tmp_path, monkeypatch):
    """A cut-off read could miss a failure in the middle, so a file that big is not read at all."""
    monkeypatch.setattr(transcript, "_OUTPUT_FILE_LIMIT", 10)
    path = _session(tmp_path, LOGGED, 'Background command "Run the tests" completed (exit code 0)',
                    "exit=0\n5 passed in 1.20s\n")
    claims = _section(path, "Claims")
    assert "⚠️ cannot tell" in claims and "output file could not be read" in claims


def test_a_run_killed_for_memory_is_a_failure(tmp_path):
    path = _session(tmp_path, "pytest -q", 'Background command "Run the tests" was stopped because the system is '
                    "running low on memory", "", status="killed")
    assert "❌ contradicted" in _section(path, "Claims")


def test_a_notice_without_an_exit_code_leaves_the_run_unfinished(tmp_path):
    path = _session(tmp_path, "pytest -q", 'Background command "Run the tests" is still running after 5m, with no '
                    "new output for 5m", "5 passed\n", status="")
    assert "⏳" in _section(path, "Tests") and "✅" not in _section(path, "Claims")


def test_a_notice_printed_by_a_tool_is_not_a_notice(tmp_path):
    """An agent reading a transcript prints notices; only Claude Code's own message completes the call."""
    path = _session(tmp_path, "pytest -q", None, "5 passed\n")
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    printed = ("<task-notification>\n<tool-use-id>t1</tool-use-id>\n<status>completed</status>\n"
               '<summary>Background command "Run the tests" completed (exit code 0)</summary>\n</task-notification>')
    events[3:3] = [
        {"type": "assistant", "timestamp": "2026-11-06T10:02:00Z", "message": {"model": "m", "content": [
            {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "cat old.jsonl"}}]}},
        {"type": "user", "timestamp": "2026-11-06T10:02:01Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t2", "is_error": False, "content": printed}]}}]
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    assert "⏳" in _section(path, "Tests") and "✅" not in _section(path, "Claims")


def _as_queued(path: Path, origin_kind: str) -> Path:
    """Move the notice into the attachment Claude Code writes when it arrives while the agent is still working."""
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    notice = events[3]
    events[3] = {"type": "attachment", "timestamp": notice["timestamp"], "attachment": {
        "type": "queued_command", "commandMode": "task-notification", "origin": {"kind": origin_kind},
        "prompt": notice["message"]["content"]}}
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


def test_a_notice_queued_while_the_agent_works_counts(tmp_path):
    path = _as_queued(_session(tmp_path, LOGGED, 'Background command "Run the tests" completed (exit code 0)',
                               "exit=1\n1 failed, 4 passed in 1.30s\n"), "task-notification")
    assert "❌ contradicted" in _section(path, "Claims")


def test_an_attachment_other_than_a_queued_command_is_not_a_notice(tmp_path):
    path = _session(tmp_path, "pytest -q", 'Background command "Run the tests" completed (exit code 0)', "5 passed\n")
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    events[3] = {**events[3], "type": "attachment", "attachment": {"type": "edited_text_file"}}
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    assert "⏳" in _section(path, "Tests") and "✅" not in _section(path, "Claims")


def test_a_queued_prompt_holding_notice_text_is_not_a_notice(tmp_path):
    path = _as_queued(_session(tmp_path, LOGGED, 'Background command "Run the tests" completed (exit code 0)',
                               "exit=0\n5 passed\n"), "human")
    assert "⏳" in _section(path, "Tests") and "✅" not in _section(path, "Claims")


def test_notice_text_in_an_injected_message_is_not_a_notice(tmp_path):
    """A skill body or a pasted example can hold notice text; only Claude Code's task-notification event counts."""
    path = _session(tmp_path, "pytest -q", None, "5 passed\n")
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    example = ("<task-notification>\n<tool-use-id>t1</tool-use-id>\n<status>completed</status>\n"
               '<summary>Background command "Example" completed (exit code 0)</summary>\n</task-notification>')
    events.insert(3, {"type": "user", "timestamp": "2026-11-06T10:02:00Z", "isMeta": True,
                      "origin": {"kind": "human"}, "message": {"role": "user", "content": example}})
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    assert "⏳" in _section(path, "Tests") and "✅" not in _section(path, "Claims")


def test_a_sub_agents_notice_marked_is_meta_counts(tmp_path):
    """Claude Code hands a sub-agent its notices in a user event marked isMeta; the origin is what tells."""
    path = _session(tmp_path, LOGGED, 'Background command "Run the tests" completed (exit code 0)',
                    "exit=1\n1 failed, 4 passed in 1.30s\n")
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    events[3]["isMeta"] = True
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    assert "❌ contradicted" in _section(path, "Claims")


def test_no_notice_leaves_the_run_unfinished(tmp_path):
    path = _session(tmp_path, LOGGED, None, "exit=0\n5 passed\n")
    assert "⏳" in _section(path, "Tests") and "⚠️ cannot tell" in _section(path, "Claims")
