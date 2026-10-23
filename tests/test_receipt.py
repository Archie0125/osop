"""receipt.md — one page of evidence per session, built from the transcript."""

import json
import re
from pathlib import Path

from click.testing import CliRunner

from osop.cli.main import cli
from osop.recorder import parse_transcript
from osop.recorder.receipt import render_receipt

MINIMAL_SESSION = Path(__file__).parent / "fixtures" / "minimal_session.jsonl"


def _session(tmp_path: Path, calls: list[tuple[str, dict, str, bool]]) -> Path:
    """Write a transcript: one prompt, then (tool, input, output, is_error) per call."""
    events = [{"type": "user", "timestamp": "2026-10-23T10:00:00Z", "sessionId": "s",
               "message": {"role": "user", "content": "do the task"}}]
    for i, (tool, inp, output, is_error) in enumerate(calls):
        events.append({"type": "assistant", "timestamp": f"2026-10-23T10:00:{2 * i + 1:02d}Z",
                       "message": {"model": "claude-opus-5-5", "content": [
                           {"type": "tool_use", "id": f"t{i}", "name": tool, "input": inp}]}})
        events.append({"type": "user", "timestamp": f"2026-10-23T10:00:{2 * i + 2:02d}Z",
                       "message": {"role": "user", "content": [
                           {"type": "tool_result", "tool_use_id": f"t{i}", "content": output,
                            "is_error": is_error}]}})
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


def _receipt(path: Path) -> str:
    return render_receipt(parse_transcript(path), source=path)


def _section(receipt: str, title: str) -> str:
    return receipt.split(f"## {title}\n", 1)[1].split("\n## ", 1)[0]


def test_goal_and_passing_test():
    receipt = _receipt(MINIMAL_SESSION)
    assert "run the tests" in _section(receipt, "Goal")
    tests = _section(receipt, "Tests")
    assert "python -m pytest -q" in tests
    assert "✅" in tests and "3 passed" in tests


def test_failing_test_by_exit_status(tmp_path):
    tests = _section(_receipt(_session(tmp_path, [
        ("Bash", {"command": "php artisan test"}, "Exit code 1\nTests: 1 failed, 4 passed", True)])), "Tests")
    assert "❌" in tests and "✅" not in tests


def test_failure_in_output_beats_exit_zero(tmp_path):
    tests = _section(_receipt(_session(tmp_path, [
        ("Bash", {"command": "npm test"}, "Tests: 2 failed, 10 passed", False)])), "Tests")
    assert "❌" in tests


def test_piped_test_command_is_flagged_as_masked(tmp_path):
    tests = _section(_receipt(_session(tmp_path, [
        ("Bash", {"command": "python -m pytest -q | tail -3"}, "5 passed in 1.0s", False)])), "Tests")
    assert "⚠️" in tests


def test_no_test_command(tmp_path):
    receipt = _receipt(_session(tmp_path, [("Bash", {"command": "ls"}, "a b", False)]))
    assert "No test command ran." in _section(receipt, "Tests")


def test_long_output_keeps_the_test_summary_at_the_end(tmp_path):
    output = "." * 2000 + "\n1 failed, 99 passed in 3.2s"
    tests = _section(_receipt(_session(tmp_path, [
        ("Bash", {"command": "python -m pytest"}, output, False)])), "Tests")
    assert "❌" in tests and "1 failed" in tests


def test_files_changed_lists_only_successful_edits(tmp_path):
    files = _section(_receipt(_session(tmp_path, [
        ("Edit", {"file_path": "src/app.py", "old_string": "a", "new_string": "b"}, "ok", False),
        ("Write", {"file_path": "README.md", "content": "x"}, "ok", False),
        ("Edit", {"file_path": "src/broken.py", "old_string": "a", "new_string": "b"}, "not found", True),
    ])), "Files changed")
    assert "src/app.py" in files and "README.md" in files
    assert "src/broken.py" not in files


def test_long_file_path_keeps_the_file_name(tmp_path):
    path = "C:/Users/A7/AppData/Local/Temp/claude/" + "x" * 120 + "/scratchpad/splice.py"
    files = _section(_receipt(_session(tmp_path, [("Write", {"file_path": path, "content": "x"}, "ok", False)])),
                     "Files changed")
    assert "splice.py" in files


def test_errors_section_lists_failed_calls(tmp_path):
    errors = _section(_receipt(_session(tmp_path, [
        ("Bash", {"command": "git push"}, "Exit code 128\nfatal: no upstream", True)])), "Errors")
    assert "git push" in errors and "fatal: no upstream" in errors


def test_command_with_pipe_does_not_break_the_table(tmp_path):
    commands = _section(_receipt(_session(tmp_path, [
        ("Bash", {"command": "cat a | grep b"}, "b", False)])), "Commands")
    row = next(line for line in commands.splitlines() if "grep" in line)
    assert row.count(" | ") == commands.splitlines()[0].count(" | ")


def test_subagent_work_is_attributed(tmp_path):
    session = _session(tmp_path, [("Agent", {"description": "Verify PR"}, "launched", False)])
    subagents = tmp_path / "s" / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-x1.meta.json").write_text(json.dumps({"toolUseId": "t0"}), encoding="utf-8")
    (subagents / "agent-x1.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"type": "user", "timestamp": "2026-10-23T10:01:00Z", "message": {"role": "user", "content": "verify"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:01:01Z", "message": {"content": [
            {"type": "tool_use", "id": "s1", "name": "Bash", "input": {"command": "python -m pytest"}}]}},
        {"type": "user", "timestamp": "2026-10-23T10:01:05Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "s1", "content": "4 passed", "is_error": False}]}},
    ]) + "\n", encoding="utf-8")

    tests = _section(_receipt(session), "Tests")
    assert "Verify PR" in tests and "python -m pytest" in tests


def test_word_pytest_in_messages_is_not_a_test_run(tmp_path):
    receipt = _receipt(_session(tmp_path, [
        ("Bash", {"command": 'git commit -m "make pytest pass"'}, "ok", False),
        ("Bash", {"command": "gh pr create --body-file - <<'EOF'\nRan python -m pytest: 19 passed\nEOF"}, "ok", False),
    ]))
    assert "No test command ran." in _section(receipt, "Tests")


def test_test_runner_after_cd_and_quoted_interpreter_is_detected(tmp_path):
    tests = _section(_receipt(_session(tmp_path, [
        ("Bash", {"command": 'cd /repo && "$V/Scripts/python.exe" -m pytest -q'}, "7 passed in 1s", False)])), "Tests")
    assert "✅" in tests


def test_compound_command_is_unclear_not_pass_or_fail(tmp_path):
    tests = _section(_receipt(_session(tmp_path, [
        ("Bash", {"command": "python -m pytest -q > log 2>&1; ruff check ."}, "Found 8 errors.", False)])), "Tests")
    row = tests.splitlines()[2]
    assert "⚠️" in row and "✅" not in row and "❌" not in row


def test_ansi_colour_codes_do_not_hide_the_summary(tmp_path):
    tests = _section(_receipt(_session(tmp_path, [
        ("Bash", {"command": "php artisan test"}, "\x1b[32;1m9 passed\x1b[39;22m (59 assertions)", False)])), "Tests")
    row = tests.splitlines()[2]
    assert "✅ passed" in row and "\x1b" not in row and "[32;1m" not in row


def test_binary_garbage_in_output_does_not_reach_the_receipt(tmp_path):
    receipt = _receipt(_session(tmp_path, [
        ("Bash", {"command": "node build.js"}, "failed\x15\x00\x00\x12 interpreter_untrusted\x00", True)]))
    assert not [ch for ch in receipt if ord(ch) < 32 and ch not in "\n\t"]


def test_echoed_exit_code_is_shown_for_unclear_runs(tmp_path):
    tests = _section(_receipt(_session(tmp_path, [
        ("Bash", {"command": 'python -m pytest -q > log 2>&1; echo "pytest exit=$?"'}, "pytest exit=1", False)])),
        "Tests")
    row = tests.splitlines()[2]
    assert "⚠️" in row and "exit=1" in row.split(" | ")[2]


def _verdicts(tmp_path, command, output="5 passed", is_error=False) -> str:
    return _section(_receipt(_session(tmp_path, [("Bash", {"command": command}, output, is_error)])), "Tests")


def test_more_runner_forms_are_detected(tmp_path):
    for command in ["php vendor/bin/phpunit", "timeout 300 python -m pytest", "sudo -E npm test",
                    "env CI=1 go test ./...", "time cargo test", "python -m unittest discover",
                    "python manage.py test", "make test", "tox -e py311"]:
        assert "No test command ran." not in _verdicts(tmp_path, command), command


def test_runner_failure_markers_beat_exit_zero(tmp_path):
    for output in ["FAILURES!\nTests: 5, Failures: 2.", "--- FAIL: TestLogin (0.01s)", "ok 1 - a\nnot ok 2 - b"]:
        row = _verdicts(tmp_path, "npm test", output).splitlines()[2]
        assert "❌" in row, output


def test_failed_chain_before_the_test_is_not_blamed_on_the_test(tmp_path):
    row = _verdicts(tmp_path, "mklink /J vendor ../vendor && php artisan test", "Exit code 1\nAccess denied",
                    True).splitlines()[2]
    assert "❌" in row and "earlier" in row


def test_nose_and_prove_are_detected(tmp_path):
    for command in ["nosetests -v", "prove -r t/"]:
        assert "No test command ran." not in _verdicts(tmp_path, command), command


def test_dotnet_maven_jest_failure_markers_beat_exit_zero(tmp_path):
    for command, output in [("dotnet test", "Failed!  - Failed:     1, Passed:     9"),
                            ("mvn test", "[INFO] BUILD FAILURE"),
                            ("npm run test", "FAIL src/a.test.js\n  ● renders")]:
        assert "❌" in _verdicts(tmp_path, command, output).splitlines()[2], command


def test_commands_table_does_not_claim_success(tmp_path):
    commands = _section(_receipt(_session(tmp_path, [("Bash", {"command": "npm test"}, "FAIL src/a", False)])),
                        "Commands")
    assert "✅" not in commands and "exit 0" in commands


def test_trailing_newline_does_not_mask_the_test(tmp_path):
    assert "✅" in _verdicts(tmp_path, "python -m pytest -x\n", "4 passed")


def test_fail_inside_a_test_name_is_not_a_failure(tmp_path):
    row = _verdicts(tmp_path, "pytest -rA", "PASSED tests/test_status.py::test_status[FAIL] 1 passed").splitlines()[2]
    assert "✅" in row


def test_disabled_pipefail_still_masks(tmp_path):
    assert "⚠️" in _verdicts(tmp_path, "set +o pipefail; pytest > /tmp/r | cat", "")
    assert "✅" in _verdicts(tmp_path, "set -euo pipefail\npytest -q 2>&1 | tee log", "3 passed")


def test_background_run_has_no_result_yet(tmp_path):
    receipt = _receipt(_session(tmp_path, [
        ("Bash", {"command": "pytest", "run_in_background": True}, "Command running in background with ID: abc",
         False)]))
    assert "✅" not in _section(receipt, "Tests") and "background" in _section(receipt, "Tests")
    assert "background" in _section(receipt, "Commands")


def _columns(row: str) -> int:
    return len(re.findall(r"(?<!\\)\|", row))


def test_or_fallback_after_test_is_unclear_not_passed(tmp_path):
    lines = _verdicts(tmp_path, "pytest -q >/dev/null 2>&1 || echo recovered", "recovered").splitlines()
    assert "⚠️" in lines[2] and "✅" not in lines[2]
    assert _columns(lines[2]) == _columns(lines[0])


def test_quoted_runner_path_is_detected(tmp_path):
    assert "✅" in _verdicts(tmp_path, '"/tmp/my venv/bin/pytest" -q', "3 passed")


def test_backticks_in_command_are_shown_literally(tmp_path):
    commands = _section(_receipt(_session(tmp_path, [("Bash", {"command": "echo `pwd`"}, "/repo", False)])),
                        "Commands")
    assert "`` echo `pwd` ``" in commands


def test_control_characters_in_commands_are_removed(tmp_path):
    receipt = _receipt(_session(tmp_path, [("Bash", {"command": "printf '\x1b[31mred'"}, "red", False)]))
    assert "\x1b" not in receipt


def test_non_string_file_path_does_not_crash(tmp_path):
    receipt = _receipt(_session(tmp_path, [("Edit", {"file_path": ["a.py"], "old_string": "a"}, "ok", False)]))
    assert "## Files changed" in receipt


def test_goal_skips_local_command_and_meta_messages(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in [
        {"type": "user", "timestamp": "2026-10-23T10:00:00Z", "isMeta": True,
         "message": {"role": "user", "content": "<local-command-caveat>Caveat</local-command-caveat>"}},
        {"type": "user", "timestamp": "2026-10-23T10:00:01Z",
         "message": {"role": "user", "content": "<command-name>/model</command-name>"}},
        {"type": "user", "timestamp": "2026-10-23T10:00:02Z", "origin": {"kind": "task-notification"},
         "message": {"role": "user", "content": "<task-notification>done</task-notification>"}},
        {"type": "user", "timestamp": "2026-10-23T10:00:03Z", "origin": {"kind": "human"},
         "message": {"role": "user", "content": "fix the login bug"}},
    ]) + "\n", encoding="utf-8")

    parsed = parse_transcript(path)
    assert "fix the login bug" in _section(render_receipt(parsed, source=path), "Goal")
    assert [n.user_prompt for n in parsed["nodes"] if n.node_type == "human"] == ["fix the login bug"]


def test_log_command_writes_receipt_next_to_osoplog(tmp_path):
    result = CliRunner().invoke(cli, ["log", str(MINIMAL_SESSION), "-o", str(tmp_path), "-d", "demo"])
    assert result.exit_code == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-10-23-demo.osoplog.yaml",
                                                          "2026-10-23-demo.receipt.md"]
