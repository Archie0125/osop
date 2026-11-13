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


def _table(section: str) -> list[str]:
    """The markdown table rows of a section, skipping a <details> wrapper and the blank lines around it."""
    return [line for line in section.splitlines() if line.startswith("|")]


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
        ("Bash", {"command": "python -m pytest -q | tail -3"}, "collected 5 items", False)])), "Tests")
    assert "⚠️" in tests and "✅" not in tests


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
    table = _table(commands)
    row = next(line for line in table if "grep" in line)
    assert row.count(" | ") == table[0].count(" | ")


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


def test_an_exit_code_echoed_right_after_the_test_is_the_tests_own(tmp_path):
    tests = _section(_receipt(_session(tmp_path, [
        ("Bash", {"command": 'python -m pytest -q > log 2>&1; echo "pytest exit=$?"'}, "pytest exit=1", False)])),
        "Tests")
    row = tests.splitlines()[2]
    assert "❌ failed" in row and "exit=1" in row.split(" | ")[2]


def test_pipestatus_echoed_after_the_pipeline_is_the_tests_own(tmp_path):
    command = 'python -m pytest -q 2>&1 | tail -3; echo "pytest exit=${PIPESTATUS[0]}"'
    row = _section(_receipt(_session(tmp_path, [
        ("Bash", {"command": command}, "1 failed, 2 passed\npytest exit=1", False)])), "Tests").splitlines()[2]
    assert "❌ failed" in row and "${PIPESTATUS[0]}" in row and "exit=1" in row
    row = _section(_receipt(_session(tmp_path, [
        ("Bash", {"command": command}, "3 passed\npytest exit=0", False)])), "Tests").splitlines()[2]
    assert "✅ passed" in row and "exit=0" in row
    # A test that does not open the pipeline, and `$?` after a pipe (the last command's): both stay unclear.
    for command in ('echo tests | python -m pytest -q | tail -3; echo "exit=${PIPESTATUS[0]}"',
                    'python -m pytest -q | tail -3; echo "exit=$?"'):
        row = _section(_receipt(_session(tmp_path, [
            ("Bash", {"command": command}, "collected 3 items\nexit=0", False)])), "Tests").splitlines()[2]
        assert "⚠️" in row, command


def test_an_exit_code_echoed_after_a_pipe_is_only_shown(tmp_path):
    # `$?` here is `tee`'s, so the run stays unclear; the echoed value is still worth showing.
    command = 'python -m pytest -q 2>&1 | tee log; echo "exit=$?"'
    tests = _section(_receipt(_session(tmp_path, [("Bash", {"command": command}, "collected 3 items\nexit=0", False)])),
                     "Tests")
    row = tests.splitlines()[2]
    assert "⚠️" in row and "exit=0" in row.split(" | ")[2]


def _verdicts(tmp_path, command, output="5 passed", is_error=False) -> str:
    return _section(_receipt(_session(tmp_path, [("Bash", {"command": command}, output, is_error)])), "Tests")


def test_more_runner_forms_are_detected(tmp_path):
    for command in ["php vendor/bin/phpunit", "timeout 300 python -m pytest", "sudo -E npm test",
                    "env CI=1 go test ./...", "time cargo test", "python -m unittest discover",
                    "python manage.py test", "make test", "tox -e py311"]:
        assert "No test command ran." not in _verdicts(tmp_path, command), command


def test_env_that_unsets_or_clears_variables_is_a_wrapper(tmp_path):
    for command in ["env -u FORCE_COLOR php artisan test tests/Feature/PdfTest.php",
                    "env -u FORCE_COLOR -u CLICOLOR php vendor/bin/phpunit --no-progress tests/Feature",
                    "env -i PATH=/usr/bin pytest -q", "env --unset=CI npm test", "env -uCI pytest"]:
        assert "No test command ran." not in _verdicts(tmp_path, command), command


def test_node_running_a_test_file_is_a_test_run(tmp_path):
    for command in ["node --test test/parser.test.ts", "node --test --test-name-pattern='login' test/a.test.ts",
                    "node --test tests/js/", "node tests/js/run.mjs", "node test.js", "node src/cart.spec.mjs",
                    "env -u FORCE_COLOR node tests/js/run.mjs", "node --no-warnings test/unit/cart.mjs",
                    "node -r ts-node/register tests/api.test.ts", "node --import tsx --test test/a.test.ts",
                    # `--test` among node's options, whatever options with values come before it.
                    "node --test-concurrency 1 --test tests/smoke.js",
                    "node --env-file-if-exists .env tests/js/run.mjs",
                    # A `\` continues the command on the next line; `observer` is no helper name.
                    "node \\\n  tests/js/run.mjs", "node tests/js/observer.mjs",
                    # An option with a plain value osop does not list (`--dns-result-order ipv4first`).
                    "node --dns-result-order ipv4first tests/http.js"]:
        assert "No test command ran." not in _verdicts(tmp_path, command), command
    # The path ends where the shell command does: `||true`, `;echo …` and `|tail` hide the script's exit status.
    for command, output in [(c, o) for c in ("node tests/a.test.js||true", "node tests/a.test.mjs;echo done",
                                             "node tests/a.test.mjs|tail -3")
                            for o in ("AssertionError: expected 3, got 2", "5 passed")] + [
            (c, "5 passed") for c in ("node tests/js/a.mjs||true", "node tests/js/a.mjs;echo done",
                                      "node tests/js/a.mjs|tail -3")]:
        row = _verdicts(tmp_path, command, output).splitlines()[2]
        assert "✅" not in row and " hid" in row, (command, output)
    # A script that is not a test file, inline code, a syntax check and a version query are not test runs.
    for command in ["node scripts/test-data.js", "node abtest.js", "node server.js", "node latests/x.js",
                    "node -e 'require(\"./tests/js/x.mjs\")'", "node --check tests/js/form.mjs",
                    "node -c tests/js/form.mjs", "node --version",
                    # The value of an option is not the script: setup files are not tests.
                    "node --require ./tests/setup.js scripts/build.js", "node -r ./test/env.js server.js",
                    # An option osop does not know may take a value; only a JS or TS file under tests/ is a script.
                    "node --experimental-sea-config ./tests/sea.json scripts/build.js",
                    # `--test` after the script is the script's argument, not node's test runner.
                    "node scripts/build.js --test", "node dist/cli.mjs --test", "node cli --test",
                    # node's version and help print and exit.
                    "node --version --test", "node --test -v", "node -h tests/x.js",
                    # node's words are on its own line: the next line is another command.
                    "node app\ncheck --test",
                    # Helpers by name: setup, server, seed, teardown, fixtures, mocks.
                    "node tests/e2e/global-setup.js", "node test/__fixtures__/app.js", "node tests/mock-server.js",
                    "node tests/e2e/start-server.mjs"]:
        assert "No test command ran." in _verdicts(tmp_path, command), command
    # A helper in the same call is not a second test command: the status echoed right after the test is the test's.
    row = _verdicts(tmp_path, 'pytest -q 2>&1 | tail -3; echo "exit=${PIPESTATUS[0]}"; node tests/helpers/cleanup.js',
                    "5 passed\nexit=0").splitlines()[2]
    assert "✅" in row and "test commands in one call" not in row
    # A quoted option value: the `$?` echoed after a helper is the helper's, not the piped test's.
    command = ('node --require "./tests/setup.js" tests/run.js 2>&1 | tail -20; '
               'node tests/helpers/cleanup.js; echo "exit=$?"')
    assert "✅" not in _verdicts(tmp_path, command, "AssertionError: x\nexit=0").splitlines()[2]


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


def test_pipefail_switched_off_again_still_masks(tmp_path):
    assert "⚠️" in _verdicts(tmp_path, "set -o pipefail && set +o pipefail && pytest | cat", "")


def test_commented_out_pipefail_does_not_count(tmp_path):
    row = _verdicts(tmp_path, "# set -o pipefail\npytest -q | grep passed", "2 passed").splitlines()[2]
    assert "✅" not in row and "🟡" in row


def test_background_launch_failure_is_a_failure(tmp_path):
    receipt = _receipt(_session(tmp_path, [
        ("Bash", {"command": "pytest", "run_in_background": True}, "Error: failed to start background command",
         True)]))
    assert "❌" in _section(receipt, "Tests") and "❌" in _section(receipt, "Commands")


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


def test_log_command_writes_the_receipt(tmp_path):
    result = CliRunner().invoke(cli, ["log", str(MINIMAL_SESSION), "-o", str(tmp_path), "-d", "demo"])
    assert result.exit_code == 0
    assert [p.name for p in tmp_path.iterdir()] == ["2026-10-23-demo.receipt.md"]


# --- Verdicts that must not overstate the evidence ---------------------------------------------


def test_background_ampersand_hides_the_exit_status(tmp_path):
    for command in ["pytest -q &", "python -m pytest > log 2>&1 &", "nohup npm test &\nsleep 1"]:
        row = _verdicts(tmp_path, command, "").splitlines()[2]
        assert "⚠️" in row and "✅" not in row, command


def test_stderr_redirect_is_not_a_background_run(tmp_path):
    assert "✅" in _verdicts(tmp_path, "python -m pytest -q 2>&1", "3 passed")
    assert "✅" in _verdicts(tmp_path, "pytest &> log", "")


def test_probably_passed_when_a_pipe_hides_a_clean_run(tmp_path):
    row = _verdicts(tmp_path, "python -m pytest -q 2>&1 | tail -3", "5 passed in 1.0s").splitlines()[2]
    assert "🟡 probably passed" in row and "✅" not in row and "⚠️" not in row


def test_probably_passed_is_counted_apart_from_passed(tmp_path):
    receipt = _receipt(_session(tmp_path, [
        ("Bash", {"command": "pytest -q | tail -3"}, "5 passed in 1.0s", False),
        ("Bash", {"command": "pytest -q"}, "5 passed in 1.0s", False)]))
    assert "2 runs: 1 passed, 1 probably passed" in receipt


def test_failure_marker_or_echoed_exit_keeps_a_piped_run_unclear(tmp_path):
    for output in ["1 failed, 4 passed in 1.0s", "5 passed in 1.0s\npytest exit=1"]:
        row = _verdicts(tmp_path, "pytest -q | tail -3; echo pytest exit=$?", output).splitlines()[2]
        assert "⚠️" in row and "🟡" not in row, output


def test_verdict_reads_the_whole_output_not_a_summary(tmp_path):
    # Two packages: the first fails in the middle of a long output, the last one passes, exit 0.
    output = "." * 300 + "\nTests: 1 failed, 5 total\n" + "." * 300 + "\nTests: 10 passed, 10 total"
    row = _verdicts(tmp_path, "npm test", output).splitlines()[2]
    assert "❌" in row and "✅" not in row


def test_pending_edit_is_not_a_changed_file(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in [
        {"type": "user", "timestamp": "2026-10-23T10:00:00Z", "sessionId": "s",
         "message": {"role": "user", "content": "do the task"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:00:01Z", "message": {"content": [
            {"type": "tool_use", "id": "t0", "name": "Edit",
             "input": {"file_path": "src/app.py", "old_string": "a", "new_string": "b"}}]}},
    ]) + "\n", encoding="utf-8")
    files = _section(_receipt(path), "Files changed")
    assert "Edit ×1" not in files
    assert "no result recorded" in files and "src/app.py" in files


def test_multiedit_is_a_changed_file(tmp_path):
    files = _section(_receipt(_session(tmp_path, [
        ("MultiEdit", {"file_path": "src/app.py", "edits": [{"old_string": "a", "new_string": "b"}]}, "ok", False),
    ])), "Files changed")
    assert "src/app.py" in files and "MultiEdit ×1" in files


def test_shell_commands_that_may_change_files_are_listed(tmp_path):
    files = _section(_receipt(_session(tmp_path, [
        ("Bash", {"command": "sed -i 's/a/b/' src/app.py"}, "", False),
        ("Bash", {"command": "echo hi > notes.txt"}, "", False),
        ("Bash", {"command": "cat a.log | tee -a b.log"}, "", False),
        ("Bash", {"command": "mv old.py new.py"}, "", False),
        ("Bash", {"command": "git status"}, "clean", False),
        ("Bash", {"command": "python -m pytest -q 2>&1"}, "3 passed", False),
        ("Bash", {"command": "cat big.log > /dev/null"}, "", False),
        ("Bash", {"command": 'git commit -m "a > b"'}, "", False),
    ])), "Files changed")
    may = files.split("may have changed", 1)[1]
    assert "<summary>4 shell commands may have changed" in files  # the count stays visible, the list is collapsed
    for command in ["sed -i", "echo hi > notes.txt", "tee -a b.log", "mv old.py new.py"]:
        assert command in may, command
    for command in ["git status", "pytest", "/dev/null", "git commit"]:
        assert command not in may, command


def test_test_inside_bash_c_is_detected(tmp_path):
    assert "✅" in _verdicts(tmp_path, "bash -lc 'cd /repo && python -m pytest -q'", "3 passed")
    assert "✅" in _verdicts(tmp_path, 'sh -c "npm test"', "Tests: 3 passed")
    assert "🟡" in _verdicts(tmp_path, "bash -c 'pytest -q | tail -3'", "3 passed").splitlines()[2]
    assert "🟡" in _verdicts(tmp_path, "bash -c 'pytest -q' | tail -3", "3 passed").splitlines()[2]


# --- Round 2: independent verifier and Codex findings on PR #11 -------------------------------


def test_inner_pipefail_does_not_protect_an_outer_pipe(tmp_path):
    row = _verdicts(tmp_path, "bash -c 'set -o pipefail; pytest -q' | tail -1", "no tests ran in 0.01s").splitlines()[2]
    assert "⚠️" in row and "✅" not in row
    assert "✅" in _verdicts(tmp_path, "bash -c 'set -o pipefail; pytest -q | tee log'", "3 passed")


def test_inner_comment_does_not_erase_the_outer_fallback(tmp_path):
    row = _verdicts(tmp_path, "bash -c 'pytest -q # smoke tests' || true", "no tests ran in 0.01s").splitlines()[2]
    assert "⚠️" in row and "✅" not in row


def test_two_test_runs_in_one_call_are_not_probably_passed(tmp_path):
    row = _verdicts(tmp_path, "pytest tests/unit -q >/dev/null 2>&1; pytest tests/smoke -q",
                    "3 passed in 0.01s").splitlines()[2]
    assert "⚠️" in row and "🟡" not in row and "2 test commands" in row
    assert "✅" in _verdicts(tmp_path, "pytest tests/unit -q && pytest tests/smoke -q", "3 passed\n5 passed")


def test_doubtful_output_is_not_probably_passed(tmp_path):
    for output in [
        "FAIL Required test coverage of 80% not reached. Total coverage: 50.00%\n5 passed in 1.0s",
        "ERROR: Coverage failure: total of 75 is less than fail-under=90\n5 passed",
        'Tests: 5 passed, 5 total\nJest: "global" coverage threshold for statements (80%) not met: 70%',
        "Tests: 5 passed, 5 total\nnpm ERR! Test failed.",
        "5 passed in 1s\nmake: *** [Makefile:3: test] Error 1",
        "5 passed\nTraceback (most recent call last):\n  File x, line 1",
    ]:
        row = _verdicts(tmp_path, "pytest -q 2>&1 | tail -5", output).splitlines()[2]
        assert "⚠️" in row and "🟡" not in row and "failures" in row, output


def test_multiline_echoed_exit_keeps_one_table_row(tmp_path):
    lines = _verdicts(tmp_path, "pytest -q >/dev/null 2>&1; printf 'pytest exit=\n%s\n' \"$?\"",
                      "pytest exit=\n1\n").splitlines()
    assert len(lines) == 3 and _columns(lines[2]) == _columns(lines[0])
    assert "⚠️" in lines[2] and "exit= 1" in lines[2]


def test_unquoted_and_later_bash_c_scripts_are_seen(tmp_path):
    assert "✅" in _verdicts(tmp_path, "bash -c pytest", "3 passed")
    assert "✅" in _verdicts(tmp_path, "bash -c true && bash -c 'pytest -q'", "3 passed")


def test_pipe_with_stderr_is_a_pipe_not_a_background_run(tmp_path):
    row = _verdicts(tmp_path, "pytest -q |& tee log", "3 passed").splitlines()[2]
    assert "🟡" in row and "pipe" in row and "background" not in row


def test_failed_inside_a_test_id_is_not_a_failure(tmp_path):
    assert "✅" in _verdicts(tmp_path, "pytest -v", "test_x.py::test_state[FAILED] PASSED\n5 passed")


def test_more_shell_writes_are_listed(tmp_path):
    files = _section(_receipt(_session(tmp_path, [
        ("Bash", {"command": "git reset --hard HEAD~1"}, "", False),
        ("Bash", {"command": "git checkout ."}, "", False),
        ("Bash", {"command": "perl -pi -e 's/a/b/' x.py"}, "", False),
        ("Bash", {"command": "perl -Ilib t/basic.t"}, "ok", False),
        ("Bash", {"command": "git checkout main"}, "", False),
    ])), "Files changed")
    may = files.split("may have changed", 1)[1]
    for command in ["git reset --hard", "git checkout .", "perl -pi"]:
        assert command in may, command
    for command in ["perl -Ilib", "git checkout main"]:
        assert command not in may, command


# --- Round 3: nested shells, pipefail scope, chains that fail after the test ----------------------


def test_nested_shell_c_is_detected(tmp_path):
    assert "🟡" in _verdicts(tmp_path, "bash -c \"bash -c 'pytest | tail'\"", "3 passed").splitlines()[2]
    assert "✅" in _verdicts(tmp_path, "bash -c 'sh -c \"pytest -q\"'", "3 passed")
    assert "✅" in _verdicts(tmp_path, "timeout 300 bash -c 'pytest -q'", "3 passed")
    row = _verdicts(tmp_path, "pytest tests/unit -q >/dev/null 2>&1; bash -c \"bash -c 'pytest tests/smoke -q'\"",
                    "3 passed in 0.01s").splitlines()[2]
    assert "⚠️" in row and "2 test commands" in row


def test_pipefail_scope_follows_the_shell(tmp_path):
    # A subshell inherits the option; a new shell (`bash -c`) does not; a closed group no longer applies.
    assert "✅" in _verdicts(tmp_path, "set -o pipefail; ( pytest -q | tee log )", "3 passed")
    assert "✅" in _verdicts(tmp_path, "set -o pipefail; bash -c 'ruff check .'; pytest -q | tee log", "3 passed")
    for command in ["set -o pipefail; bash -c 'pytest -q | cat'",
                    "(set -o pipefail; ruff check .) && (pytest -q | tail -1)",
                    "bash -c 'set -o pipefail; ruff check .' && bash -c 'pytest -q | tail -1'",
                    "( set -o pipefail ); ( pytest -q | tee log )",
                    "bash -c 'set -o pipefail; pytest -q | cat' | cat"]:
        row = _verdicts(tmp_path, command, "3 passed").splitlines()[2]
        assert "🟡" in row and "✅" not in row, command


def test_or_fallback_is_never_probably_passed(tmp_path):
    row = _verdicts(tmp_path, "pytest -q || echo recovered", "5 passed\nrecovered").splitlines()[2]
    assert "⚠️" in row and "🟡" not in row


def test_failure_after_the_test_is_not_blamed_on_the_test(tmp_path):
    row = _verdicts(tmp_path, "pytest -q && ruff check .",
                    "Exit code 1\n5 passed in 0.01s\nF401 unused import", True).splitlines()[2]
    assert "⚠️" in row and "❌" not in row and "later" in row
    # "Found 1 error." is ruff's, but it reads like a test failure: the chain is ❌, and the blame stays open.
    for output in ["Exit code 1\n5 passed in 0.01s\nF401 unused import\nFound 1 error.",
                   "Exit code 1\n1 failed, 4 passed"]:
        row = _verdicts(tmp_path, "pytest -q && ruff check .", output, True).splitlines()[2]
        assert "❌" in row and "later" in row, output


def test_internal_error_is_not_probably_passed(tmp_path):
    for output in ["5 passed in 0.01s\nINTERNALERROR> RuntimeError: session cleanup failed",
                   "5 passed\nRuntimeError: boom"]:
        row = _verdicts(tmp_path, "pytest -q 2>&1 | cat", output).splitlines()[2]
        assert "⚠️" in row and "🟡" not in row, output


def test_backslash_before_a_pipe_is_shown_as_it_ran(tmp_path):
    # cmark-gfm (GitHub) treats a pipe behind any backslash as escaped, so `\|` in a command is written
    # `\\|`: the row stays one row and the command renders with its backslash, as it ran.
    command = "pytest -q | grep 'passed" + chr(92) + "|failed'"
    receipt = _receipt(_session(tmp_path, [("Bash", {"command": command}, "3 passed", False)]))
    for title in ("Tests", "Commands"):
        lines = _table(_section(receipt, title))
        assert _columns(lines[2]) == _columns(lines[0]), title
        assert "passed" + chr(92) * 2 + "|failed" in lines[2], title


def test_unquoted_bash_c_word_stops_at_the_separator(tmp_path):
    assert "✅" in _verdicts(tmp_path, "bash -c true; pytest -q", "3 passed")
    assert "🟡" in _verdicts(tmp_path, "bash -c pytest| tail -1", "3 passed").splitlines()[2]


def test_git_checkout_of_a_path_is_a_possible_write(tmp_path):
    files = _section(_receipt(_session(tmp_path, [("Bash", {"command": "git checkout ./x.py"}, "", False)])),
                     "Files changed")
    assert "git checkout ./x.py" in files.split("may have changed", 1)[1]


def test_interrupted_or_crashed_run_is_not_probably_passed(tmp_path):
    for output in ["5 passed\n!!!!!!!!!!!! KeyboardInterrupt !!!!!!!!!!!!",
                   "5 passed\nFatal Python error: Segmentation fault"]:
        row = _verdicts(tmp_path, "pytest -q | cat", output).splitlines()[2]
        assert "⚠️" in row and "🟡" not in row, output


def test_shell_options_before_c_are_skipped(tmp_path):
    assert "❌" in _verdicts(tmp_path, "bash -o pipefail -c 'pytest -q'", "Exit code 1\n1 failed", True)
    # `-o pipefail` on the shell itself protects the pipes in its script.
    assert "✅" in _verdicts(tmp_path, "bash --norc -o pipefail -c 'pytest -q | tee log'", "3 passed")
    assert "🟡" in _verdicts(tmp_path, "bash --norc -c 'pytest -q | tee log'", "3 passed").splitlines()[2]


def test_many_sibling_shell_scripts_are_all_unwrapped(tmp_path):
    command = "; ".join(["bash -c 'true'"] * 6 + ["bash -c 'pytest -q'"])
    assert "❌" in _verdicts(tmp_path, command, "Exit code 1\n1 failed", True)


def test_combined_shell_flags_before_c_are_skipped(tmp_path):
    assert "✅" in _verdicts(tmp_path, "bash -euo pipefail -c 'pytest -q | tee log'", "3 passed")
    assert "❌" in _verdicts(tmp_path, "bash -euo pipefail -c 'pytest -q'", "Exit code 1\n1 failed", True)
    assert "🟡" in _verdicts(tmp_path, "bash +o pipefail -c 'pytest -q | tee log'", "3 passed").splitlines()[2]
    for command in ["bash -c -- 'pytest -q'", "bash -O extglob -c 'pytest -q'", "bash --rcfile x -c 'pytest -q'",
                    "C:/Git/bin/bash.exe -c 'pytest -q'", "/usr/bin/bash -lc 'pytest -q'"]:
        assert "✅" in _verdicts(tmp_path, command, "3 passed"), command


def test_set_pipefail_before_the_test_is_not_an_earlier_step(tmp_path):
    for command in ["bash -o pipefail -c 'pytest -q'", "set -o pipefail; pytest -q"]:
        row = _verdicts(tmp_path, command, "Exit code 1\n1 failed", True).splitlines()[2]
        assert "❌" in row and "earlier" not in row, command


def test_env_assignment_and_syntax_check_shell(tmp_path):
    assert "❌" in _verdicts(tmp_path, "env PYTHONPATH=. bash -c 'pytest -q'", "Exit code 1\n1 failed in 0.01s", True)
    assert "✅" in _verdicts(tmp_path, "/bin/bash -c 'pytest -q'", "3 passed")
    # `-n` checks syntax only: nothing ran, so there is no test run to report.
    assert "No test command ran." in _verdicts(tmp_path, "bash -n -c 'pytest -q'", "")
    assert "No test command ran." in _verdicts(tmp_path, "bash -nc 'pytest -q'", "")
    assert "✅" in _verdicts(tmp_path, "bash -n -c 'true'; bash -c 'pytest -q'", "3 passed")


def test_wrapper_options_are_not_the_shells(tmp_path):
    for command in ["sudo -n bash -c 'pytest -q'", "sudo -u ci bash -c 'pytest -q'",
                    "env -i PATH=/usr/bin bash -c 'pytest -q'", "nice -n 10 bash -c 'pytest -q'",
                    "timeout -s KILL 60 bash -c 'pytest -q'"]:
        assert "✅" in _verdicts(tmp_path, command, "3 passed"), command
    row = _verdicts(tmp_path, "bash -o pipefail +o pipefail -c 'pytest -q | tee log'", "3 passed").splitlines()[2]
    assert "🟡" in row and "✅" not in row


# --- one-page layout: facts first, the full command table collapsed ---------------------------------


def _pending_session(tmp_path: Path, command: str) -> Path:
    """A Bash call whose result never arrived: the transcript ends after the tool_use."""
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in [
        {"type": "user", "timestamp": "2026-10-23T10:00:00Z", "sessionId": "s",
         "message": {"role": "user", "content": "do the task"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:00:01Z", "message": {"content": [
            {"type": "tool_use", "id": "t0", "name": "Bash", "input": {"command": command}}]}},
    ]) + "\n", encoding="utf-8")
    return path


def test_commands_table_is_collapsed_after_the_facts(tmp_path):
    receipt = _receipt(_session(tmp_path, [("Bash", {"command": "ls -la"}, "a b", False)]))
    assert receipt.index("## Tests") < receipt.index("## Files changed") < receipt.index("## Errors")
    assert receipt.index("## Errors") < receipt.index("## Commands")
    commands = _section(receipt, "Commands")
    assert commands.startswith("<details>") and "<summary>1 shell command" in commands
    assert "ls -la" in commands and "</details>" in commands


def test_commands_with_no_result_are_listed_outside_the_collapsed_table(tmp_path):
    receipt = _receipt(_pending_session(tmp_path, "npm run build"))
    unfinished = _section(receipt, "No result recorded")
    assert "npm run build" in unfinished and "⏳" in unfinished
    assert "1 without result" in receipt.split("## Goal", 1)[0]  # counted in the overview too


def test_background_command_is_listed_as_unfinished(tmp_path):
    receipt = _receipt(_session(tmp_path, [
        ("Bash", {"command": "npm run dev", "run_in_background": True}, "Command running in background", False)]))
    assert "npm run dev" in _section(receipt, "No result recorded")


def test_pending_test_run_is_not_listed_twice(tmp_path):
    receipt = _receipt(_pending_session(tmp_path, "pytest -q"))
    assert "⏳" in _section(receipt, "Tests")
    assert "## No result recorded" not in receipt


def test_git_commit_and_push_results_are_visible(tmp_path):
    receipt = _receipt(_session(tmp_path, [
        ("Bash", {"command": "git commit -m 'fix: x'"}, "[main abc1234] fix: x", False),
        ("Bash", {"command": "cd repo && git push origin main"}, "Exit code 1\nerror: failed to push", True),
        ("Bash", {"command": "git log --oneline -3"}, "abc1234 fix: x", False),
        ("Bash", {"command": "gh pr merge 11 --squash"}, "Exit code 1\nnot mergeable", True),
    ]))
    git = _section(receipt, "Git")
    assert "git commit" in git and "git push" in git and "gh pr merge" in git
    assert "git log" not in git
    assert git.count("❌ exit 1") == 2 and "exit 0" in git


def test_no_git_section_without_commit_push_or_merge(tmp_path):
    receipt = _receipt(_session(tmp_path, [("Bash", {"command": "git status && git log -1"}, "clean", False)]))
    assert "## Git" not in receipt


def test_brief_is_one_line_with_the_counts_that_matter():
    from osop.recorder.receipt import render_brief
    line = render_brief(parse_transcript(MINIMAL_SESSION), receipt_path="/r/x.receipt.md")
    assert "\n" not in line and line.startswith("OSOP")
    assert "tests: 1 run: 1 passed" in line
    assert "receipt: /r/x.receipt.md" in line


def test_brief_counts_failures_edits_and_shell_writes(tmp_path):
    from osop.recorder.receipt import render_brief
    path = _session(tmp_path, [
        ("Bash", {"command": "pytest -q"}, "Exit code 1\n1 failed, 2 passed", True),
        ("Edit", {"file_path": "a.py", "old_string": "x", "new_string": "y"}, "ok", False),
        ("Edit", {"file_path": "a.py", "old_string": "y", "new_string": "z"}, "ok", False),
        ("Bash", {"command": "cat a.py > b.py"}, "", False),
    ])
    line = render_brief(parse_transcript(path), receipt_path=Path("r.md"))
    assert "1 run: 1 failed" in line
    assert "files: 1 edited, 1 shell write" in line
    assert "tool calls: 4, 1 failed" in line


def test_git_row_shows_the_git_command_not_the_start_of_the_script(tmp_path):
    receipt = _receipt(_session(tmp_path, [
        ("Bash", {"command": "cd /repo && python -m pytest -q && git commit -m 'fix: y' && git push -u origin fix-y"},
         "3 passed\n[fix-y 1234abc] fix: y\nbranch 'fix-y' set up to track 'origin/fix-y'.", False),
    ]))
    row = _table(_section(receipt, "Git"))[2]
    assert "git commit -m 'fix: y'; git push -u origin fix-y" in row
    assert "python -m pytest" not in row


# --- Codex review of PR #12: Git rows and text outside tables ---------------------------------------


def test_git_global_options_do_not_hide_a_commit(tmp_path):
    receipt = _receipt(_session(tmp_path, [
        ("Bash", {"command": "git -c core.hooksPath=/dev/null commit -m fix"}, "[main abc1234] fix", False),
        ("Bash", {"command": "git --no-pager -C repo push origin main"}, "main -> main", False),
    ]))
    git = _section(receipt, "Git")
    assert "commit -m fix" in git and "push origin main" in git


def test_git_merge_base_is_not_a_merge(tmp_path):
    receipt = _receipt(_session(tmp_path, [
        ("Bash", {"command": "git merge-base HEAD origin/main"}, "abc1234", False),
        ("Bash", {"command": "git tag-list"}, "", False),
    ]))
    assert "## Git" not in receipt


def test_quoted_git_text_is_not_shown_as_a_git_command(tmp_path):
    receipt = _receipt(_session(tmp_path, [
        ("Bash", {"command": 'git commit -m "fix" && echo "git push origin main"'}, "[main abc1234] fix", False),
    ]))
    row = _table(_section(receipt, "Git"))[2]
    assert 'git commit -m "fix"' in row and "git push" not in row


def test_text_outside_a_table_keeps_its_pipes_unescaped(tmp_path):
    receipt = _receipt(_pending_session(tmp_path, "ls | head -n 1"))
    assert "`ls | head -n 1`" in _section(receipt, "No result recorded")
    receipt = _receipt(_session(tmp_path, [
        ("Bash", {"command": "echo a | tee b.log"}, "a", False),
        ("Bash", {"command": "cat x | grep y"}, "Exit code 1\nno y | here", True),
    ]))
    assert "`echo a | tee b.log`" in _section(receipt, "Files changed")
    assert "`cat x | grep y` → no y | here" in _section(receipt, "Errors")


def _git_cell(tmp_path: Path, command: str) -> str:
    receipt = _receipt(_session(tmp_path, [("Bash", {"command": command}, "ok", False)]))
    return _table(_section(receipt, "Git"))[2].split(" | ")[1]


def test_git_cell_shows_commands_not_echoed_words(tmp_path):
    assert _git_cell(tmp_path, "git commit -m fix && echo git push origin main") == "`git commit -m fix`"


def test_git_cell_keeps_a_quoted_option_value(tmp_path):
    assert _git_cell(tmp_path, 'git -C "my repo" push && git tag v1') == '`git -C "my repo" push; git tag v1`'


def test_git_cell_shows_a_push_inside_bash_c(tmp_path):
    assert "git push origin main" in _git_cell(tmp_path, 'git commit -m "fix"; bash -c "git push origin main"')


def test_git_global_option_with_a_separate_value(tmp_path):
    assert _git_cell(tmp_path, "git --git-dir .git push") == "`git --git-dir .git push`"


def test_git_cell_does_not_cut_a_quoted_redirection(tmp_path):
    assert _git_cell(tmp_path, 'git commit -m "fix > docs" && git push') == '`git commit -m "fix > docs"; git push`'


def test_git_cell_stops_before_a_heredoc_body(tmp_path):
    assert _git_cell(tmp_path, "git commit -F - <<EOF\ngit push origin main\nEOF") == "`git commit -F -`"


def test_git_cell_inside_bash_c_keeps_its_quotes(tmp_path):
    cell = _git_cell(tmp_path, """bash -c 'git -C "my repo" push origin main'""")
    assert cell == '`git -C "my repo" push origin main`'


def test_git_cell_inside_a_substitution_has_no_closing_paren(tmp_path):
    assert _git_cell(tmp_path, "echo $(git push origin main)") == "`git push origin main`"


def test_git_status_hidden_by_what_follows_is_not_exit_0(tmp_path):
    def result(command: str) -> str:
        receipt = _receipt(_session(tmp_path, [("Bash", {"command": command}, "rejected", False)]))
        return _table(_section(receipt, "Git"))[2].split(" | ")[2]
    assert result("git push 2>&1 | tail -3") == "⚠️ exit 0, but a pipe after it hides its exit status"
    assert result("git commit -m x; git log -1") == "⚠️ exit 0, but `;` after it hides its exit status"
    assert result("git commit -m x && git push") == "exit 0"
    assert result("set -o pipefail; git push 2>&1 | tail -3") == "exit 0"


# --- independent verification of PR #12 -----------------------------------------------------------


def test_git_behind_timeout_or_an_env_assignment_is_seen(tmp_path):
    assert _git_cell(tmp_path, "timeout 900 git push origin main") == "`git push origin main`"
    assert _git_cell(tmp_path, "GIT_EDITOR=true git cherry-pick abc1234") == "`git cherry-pick abc1234`"
    assert _git_cell(tmp_path, "env GIT_SSH_COMMAND=ssh git push") == "`git push`"
    assert _git_cell(tmp_path, "env -u FORCE_COLOR git push origin main") == "`git push origin main`"


def test_listing_tags_is_not_tagging(tmp_path):
    receipt = _receipt(_session(tmp_path, [("Bash", {"command": "git tag -l 'v*'"}, "v1", False)]))
    assert "## Git" not in receipt
    assert _git_cell(tmp_path, "git tag v2") == "`git tag v2`"


def test_a_quoted_multiline_commit_message_stays_one_segment(tmp_path):
    command = 'git commit -m "$(cat <<\'EOF\'\nfix: x\n\nwhy\nEOF\n)" && git push'
    assert _git_cell(tmp_path, command) == '`git commit -m "$(cat <<\'EOF\' fix: x why EOF )"; git push`'


def test_a_background_start_that_failed_is_an_error_not_an_unfinished_call(tmp_path):
    receipt = _receipt(_session(tmp_path, [
        ("Bash", {"command": "npm run dev", "run_in_background": True}, "Exit code 127\nnpm: not found", True)]))
    assert "## No result recorded" not in receipt and "without result" not in receipt
    assert "npm run dev" in _section(receipt, "Errors")


def test_a_git_command_behind_an_or_may_not_have_run(tmp_path):
    receipt = _receipt(_session(tmp_path, [
        ("Bash", {"command": 'git diff --cached --quiet || git commit -m "fix: docs"'}, "", False)]))
    row = _table(_section(receipt, "Git"))[2].split(" | ")
    assert row[1] == '`git commit -m "fix: docs"`'
    # In a table the pipes of `||` are escaped, as in every other cell.
    escaped_or = "`" + chr(92) + "|" + chr(92) + "|`"
    assert row[2] == f"⚠️ exit 0, but {escaped_or} before it: it ran only if the command before failed"


def test_a_continued_line_or_a_paren_after_the_or_still_counts(tmp_path):
    escaped_or = "`" + chr(92) + "|" + chr(92) + "|`"
    for command in ("git diff --cached --quiet || " + chr(92) + "\n  git commit -m x",
                    "git diff --cached --quiet || (git commit -m x)"):
        receipt = _receipt(_session(tmp_path, [("Bash", {"command": command}, "", False)]))
        row = _table(_section(receipt, "Git"))[2].split(" | ")
        assert row[2] == f"⚠️ exit 0, but {escaped_or} before it: it ran only if the command before failed", command
