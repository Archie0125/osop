"""receipt.md — one page of evidence per session, built from the transcript."""

import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from osop.cli.main import cli
from osop.recorder import parse_transcript
from osop.recorder.receipt import _SECRET, _tail, render_brief, render_receipt

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


def test_php_called_by_its_full_path_is_a_launcher(tmp_path):
    for command in ["C:/xampp/php/php.exe artisan test", "/c/xampp/php/php.exe vendor/bin/phpunit --filter Login",
                    '"C:/Program Files/php/php.exe" artisan test tests/Feature', "/usr/bin/php artisan test",
                    'cd "C:/work/app" && /c/xampp/php/php.exe artisan test 2>&1 | tail -5']:
        assert "No test command ran." not in _verdicts(tmp_path, command), command
    for command in ["C:/xampp/php/php.exe -l app/Models/User.php", "C:/xampp/php/php.exe -v",
                    "echo C:/xampp/php/php.exe artisan test"]:
        assert "No test command ran." in _verdicts(tmp_path, command), command


def test_a_test_in_a_quoted_command_substitution_is_a_test_run(tmp_path):
    # echo prints what the test printed and sets the exit status itself: the test is seen, its status is not.
    for command, output, mark in [
        ('echo "unit: $(python -m pytest -q tests/unit 2>&1 | tail -1)"', "unit: 12 passed in 0.40s", "🟡"),
        # Quotes of its own inside the `$( … )`, with a bracket inside them.
        ('echo "== probe: $(vendor/bin/phpunit --colors=never "$DIR/ProbeTest.php" 2>&1 | grep -F \'OK (\')"',
         "== probe: OK (9 tests, 30 assertions)", "⚠️"),
        ('echo "== probe: $(vendor/bin/phpunit --colors=never 2>&1 | grep -F "OK (")"',
         "== probe: OK (9 tests, 30 assertions)", "⚠️"),
        ('cd app && echo "== probe: $(vendor/bin/phpunit "$DIR/ProbeTest.php" 2>&1 | grep -E \'Tests:\')"',
         "== probe: Tests: 9, Assertions: 30, Failures: 1.", "⚠️"),
        # An escaped quote before the `$( … )` does not end the string; one inside it opens no string.
        ('echo "said \\"go $(vendor/bin/phpunit "$DIR/ProbeTest.php" 2>&1 | tail -1)"',
         'said "go Tests: 9, Assertions: 30, Failures: 1.', "⚠️"),
        ('echo "n: $(python -m pytest -q 2>&1 | grep -c \\\'passed)"', "n: 0", "⚠️"),
        # A bracketed group before the test does not close the `$( … )`.
        ('echo "r: $(cd app && (composer dump-autoload -q) && vendor/bin/phpunit 2>&1 | tail -1)"',
         "r: Tests: 9, Assertions: 30, Failures: 1.", "⚠️"),
    ]:
        row = _verdicts(tmp_path, command, output).splitlines()[2]
        assert f"| {mark} " in row and "the command that uses `$(…)`" in row, (command, row)


def test_a_test_whose_output_a_command_takes_has_its_status_hidden(tmp_path):
    # The command that takes the output sets the exit status: exit 0 is echo's, export's or report.sh's.
    for command in ["echo $(pytest -q)", 'echo "$(pytest -q)"', "export RESULT=$(pytest -q)",
                    "RESULT=$(pytest -q) ./report.sh"]:
        row = _verdicts(tmp_path, command).splitlines()[2]
        assert "🟡 probably passed: the command that uses `$(…)`" in row, (command, row)


def test_a_bare_assignment_takes_the_tests_status(tmp_path):
    for command in ["out=$(pytest -q 2>&1)", 'out="$(pytest -q 2>&1)"', "A=1 out=$(pytest -q 2>&1)"]:
        assert "❌" in _verdicts(tmp_path, command, "Exit code 1\n1 failed, 4 passed", True), command
        assert "✅" in _verdicts(tmp_path, command, ""), command
    # In a loop the assignment is still bare; the `;` after it is what hides the status.
    row = _verdicts(tmp_path, 'for f in a b; do out=$(pytest -q tests/test_$f.py); echo "$out"; done',
                    "3 passed\n2 passed").splitlines()[2]
    assert "🟡 probably passed: `;` after the test" in row


def test_an_unclosed_substitution_is_no_assignment():
    from osop.recorder.receipt import _hidden_status
    code = 'out=$(pytest -q ""'
    assert _hidden_status(code, code.index("-q") + 2) == "the command that uses `$(…)`"


def test_what_a_test_in_a_used_substitution_cannot_show(tmp_path):
    for command, output, is_error in [
        # The fallback inside may have printed the pass line.
        ("echo \"$(pytest -q || printf '5 passed')\"", "5 passed", False),
        # `|| exit 1` follows `test`, which took the output; it is no fallback of pytest's.
        ('test "$(pytest -q >/dev/null 2>&1)" = yes || exit 1', "Exit code 1", True),
        # An escaped bracket does not end the `$( … )`.
        ("echo \"$(printf '%s' \\); pytest -q >/dev/null 2>&1)\"", ")", False),
    ]:
        row = _verdicts(tmp_path, command, output, is_error).splitlines()[2]
        assert "| ⚠️ unclear" in row, (command, row)
    # Nor does one open a bracket: the test after the `$( … )` runs on its own.
    assert "✅ passed" in _verdicts(tmp_path, ": $(printf %s \\(); pytest -q")


def test_nested_quoted_substitutions_are_read_once_each(monkeypatch):
    from osop.recorder import receipt
    original, calls = receipt._shell_code, []

    def counted(command, scripts=()):
        calls.append(command)
        return original(command, scripts)

    monkeypatch.setattr(receipt, "_shell_code", counted)
    assert "$(pytest -q)" in counted("echo " + '"$(echo ' * 12 + '"$(pytest -q)"' + ')"' * 12)
    assert len(calls) == 14, len(calls)


def test_a_quoted_string_whose_substitution_runs_no_test_stays_a_string(tmp_path):
    for command in ["echo '$(pytest -q)'", 'echo "$(cat notes.txt) pytest -q"', '# echo "$(pytest -q)"\nls']:
        assert "No test command ran." in _verdicts(tmp_path, command), command
    # A commit message written by a heredoc stays one string: its lines are no later commands.
    git = _section(_receipt(_session(tmp_path, [(
        "Bash", {"command": "git commit -q -m \"$(cat <<'EOF'\nfix: parser\n\nBody.\nEOF\n)\""}, "", False)])), "Git")
    assert "| exit 0 |" in git, git


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
                    # Listed options with a plain value, also one holding a hyphen.
                    "node --dns-result-order ipv4first tests/http.js",
                    "node --unhandled-rejections warn-with-error-code --test",
                    # `-v` after the script is the script's (verbose), not node's version.
                    "node tests/run.test.js -v", "node --test tests/a.test.js -v"]:
        assert "No test command ran." not in _verdicts(tmp_path, command, "5 passed"), command
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
                    "node --no-warnings app --test",  # a flag without a value: `app` is the script
                    # node's version and help print and exit.
                    "node --version --test", "node --test -v", "node -h tests/x.js",
                    "node --test --test-concurrency 1 -v",
                    # Bundled short options: `-e1` is inline code, `-pe` prints.
                    "node -e1 --test", "node -pe 1 tests/a.js",
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
    for command in ('node --require "./tests/setup.js" tests/run.js 2>&1 | tail -20; '
                    'node tests/helpers/cleanup.js; echo "exit=$?"',
                    # A quoted part glued to a path is one word, as the receipt reads it.
                    'node --import "$PWD"/register.js --test 2>&1 | tail -4; '
                    'node tests/helpers/cleanup.js; echo "exit=$?"'):
        assert "✅" not in _verdicts(tmp_path, command, "AssertionError: x\nexit=0").splitlines()[2], command


def test_runner_failure_markers_beat_exit_zero(tmp_path):
    for output in ["FAILURES!\nTests: 5, Failures: 2.", "--- FAIL: TestLogin (0.01s)", "ok 1 - a\nnot ok 2 - b"]:
        row = _verdicts(tmp_path, "npm test", output).splitlines()[2]
        assert "❌" in row, output


def test_failed_chain_before_the_test_is_not_blamed_on_the_test(tmp_path):
    # The step before `&&` failed, so the test never ran: nothing in the output says it failed.
    row = _verdicts(tmp_path, "mklink /J vendor ../vendor && php artisan test", "Exit code 1\nAccess denied",
                    True).splitlines()[2]
    assert "⚠️ unclear" in row and "earlier" in row and "no test failure" in row
    row = _verdicts(tmp_path, "python patch.py && pytest -q", "Exit code 1\nAssertionError: old text not found",
                    True).splitlines()[2]
    assert "⚠️ unclear" in row and "❌" not in row
    # The output reports a test failure: that is the test's.
    row = _verdicts(tmp_path, "python patch.py && pytest -q", "Exit code 1\n1 failed, 4 passed", True).splitlines()[2]
    assert "❌" in row and "earlier" in row


def test_a_later_step_that_owns_the_exit_status_does_not_fail_the_test(tmp_path):
    # Output sent to a file, then a later step failed: the exit status is that step's.
    for command, output in [
            ('(pytest -q > "$S/test.log" 2>&1; echo "TEST_EXIT=$?" >> "$S/test.log"); tail -3 "$S/test.log" "$S/b.log"',
             "Exit code 1\ntail: option used in invalid context -- 3"),
            ("pytest -q > /tmp/t.log 2>&1; cut -c1-80 /tmp/missing.log",
             "Exit code 1\ncut: /tmp/missing.log: No such file"),
            ("npm test > /tmp/t.log 2>&1 && git status --short | grep -q .", "Exit code 1")]:
        row = _verdicts(tmp_path, command, output, True).splitlines()[2]
        assert "⚠️ unclear" in row and "❌" not in row and "no test failure" in row, command
    # Claude Code's "Exit code 1" and a step's "Error:" on the next line are no "1 error" summary.
    for output in ("Exit code 1\nError: missing configuration", "Exit code 1\npatch failed:\n2 hunks rejected"):
        row = _verdicts(tmp_path, "./scripts/bootstrap.sh && pytest -q", output, True).splitlines()[2]
        assert "⚠️ unclear" in row, output
    # A single fallback after `||` ran only because the test failed.
    for command, output in [("pytest -q > /tmp/t.log 2>&1 || cat /tmp/missing.log",
                              "Exit code 1\ncat: /tmp/missing.log: No such file or directory"),
                             ("pytest -q || exit 1", "Exit code 1")]:
        assert "❌" in _verdicts(tmp_path, command, output, True).splitlines()[2], command
    # mocha's summary, on a line of its own, is a test failure; the same words inside other text are not.
    row = _verdicts(tmp_path, "npx mocha && npm run lint",
                    "Exit code 1\n  3 passing (5ms)\n  1 failing\n\n  1) smoke\n     AssertionError: 41 !== 42",
                    True).splitlines()[2]
    assert "❌" in row
    row = _verdicts(tmp_path, 'pytest -q -s; echo "exit=$?"', "assert report == '1 failing'\n.\n5 passed\nexit=0"
                    ).splitlines()[2]
    assert "✅" in row
    # PHPUnit's error summary is a test failure.
    row = _verdicts(tmp_path, "php vendor/bin/phpunit tests/Feature 2>&1; git status --short",
                    "Exit code 1\nERRORS!\nTests: 7, Assertions: 31, Errors: 1.", True).splitlines()[2]
    assert "❌" in row
    # With a test failure in the output, it is still the test's.
    row = _verdicts(tmp_path, "pytest -q 2>&1 | tail -3; cut -c1-80 /tmp/missing.log",
                    "Exit code 1\n2 failed, 3 passed\ncut: /tmp/missing.log: No such file", True).splitlines()[2]
    assert "❌" in row


def test_the_status_echoed_right_after_the_test_beats_a_later_steps_exit(tmp_path):
    command = 'pytest -q > /tmp/t.log 2>&1; echo "test exit=$?"; grep -E "passed|failed" /tmp/t.log; grep -q TODO a.py'
    row = _verdicts(tmp_path, command, "Exit code 1\ntest exit=0\n5 passed", True).splitlines()[2]
    assert "✅ passed" in row and "exit=0" in row and "a later step returned exit 1" in row
    row = _verdicts(tmp_path, command, "Exit code 1\ntest exit=1\n1 failed, 4 passed", True).splitlines()[2]
    assert "❌ failed" in row and "exit=1" in row


def test_the_tests_own_echo_line_is_read_and_no_other(tmp_path):
    def row(command, output, is_error=False):
        return _verdicts(tmp_path, command, output, is_error).splitlines()[2]
    # An earlier step printed an `exit=1` of its own; the test's echo says 0.
    assert "✅ passed" in row('php gen.php 2>&1 | tail -3; php artisan test; echo "test exit $?"',
                              "dry run, exit=1 when real\ntest exit 0\nTests: 50 passed")
    # Labels without the word "exit" on its own, and a status kept in a variable.
    assert "✅ passed" in row("npm test > /tmp/t.log 2>&1; echo npm_test_exit=$?", "npm_test_exit=0")
    assert "❌ failed" in row('pytest -q > /tmp/t.log 2>&1; rc=$?; echo "rc=$rc"', "rc=1")
    assert "✅ passed" in row('pytest -q > /tmp/t.log 2>&1; rc=$?; echo "pytest rc=${rc}"', "pytest rc=0")
    # An earlier step's echo with another label does not get in the way.
    assert "✅ passed" in row('npx tsc > /tmp/c 2>&1; echo "tsc exit $?"; npm test > /tmp/t 2>&1; echo "test exit $?"',
                              "tsc exit 2\ntest exit 0\nℹ pass 5\nℹ fail 0")
    # Two tests in one call make one row: every test needs its echo, and one failure is enough.
    command = 'pytest tests/a > /tmp/a 2>&1; echo "a=$?"; pytest tests/b > /tmp/b 2>&1; echo "b=$?"'
    assert "❌ failed" in row(command, "a=0\nb=1") and "b=1" in row(command, "a=0\nb=1")
    assert "✅ passed" in row(command, "a=0\nb=0") and "a=0, b=0" in row(command, "a=0\nb=0")
    assert "✅" not in row('pytest tests/a > /tmp/a 2>&1; echo "a=$?"; pytest tests/b > /tmp/b 2>&1', "a=0")
    # Text inside a line is not the echo's line; a line the test printed with the same label makes it unreadable.
    assert "❌ failed" in row('npx mocha; echo "exit=$?"; true', "  ✓ prints exit=0 for --help\n  1 failing\nexit=1")
    assert "❌ failed" in row('npx mocha; echo "exit=$?"; true', "  ✓ prints exit=0\n  1 failing\nexit=1")
    # The echo's whole line, text after the status included.
    assert "✅ passed" in row('pytest -q -s; echo "exit=$? (pytest)"; true',
                              "exit=1 means a failure\n5 passed\nexit=0 (pytest)")
    assert "❌" not in row('pytest -q > /dev/null 2>&1; rc=$?; echo -n "test rc=$rc"; echo 1', "test rc=01")
    assert "✅" not in row('pytest -q -s; echo "exit=$?"; true', "exit=0\n.\nF\n1 failed, 1 passed\nexit=1")
    assert "❌" not in row('pytest -q -s; echo "exit=$?"; true', "exit=1\n.\n1 passed\nexit=0")
    # A later step's Traceback does not unsay the test's own 0.
    assert "✅ passed" in row('pytest -q > /tmp/t 2>&1; echo "exit=$?"; python check.py',
                              "Exit code 1\nexit=0\nTraceback (most recent call last):\nKeyError: 'x'", True)
    # Echoes that print another status, a bare number, or into a file.
    assert "✅" not in row('pytest -q | tail -3; echo "exit=$?"', "3 passed\nexit=0")
    assert "✅" not in row('pytest -q | tail -3; rc=$?; echo "rc=$rc"', "3 passed\nrc=0")
    assert "✅" not in row("pytest -q > /tmp/t 2>&1; echo $?; true", "0")
    # An echo into the log reaches the output when the log is shown; not shown, or shown with an older run's line, no.
    log = 'pytest -q > /tmp/t.log 2>&1; echo "exit=$?" >> /tmp/t.log; tail -n 2 /tmp/t.log'
    assert "✅ passed" in row(log, "5 passed in 0.31s\nexit=0")
    assert "✅" not in row('pytest -q > /tmp/t.log 2>&1; echo "exit=$?" >> /tmp/t.log; ls', "t.log")
    assert "✅" not in row('pytest -q >> /tmp/t.log 2>&1; echo "exit=$?" >> /tmp/t.log; cat /tmp/t.log',
                           "1 failed\nexit=1\n5 passed\nexit=0")
    # Another log shown instead of the one the echo went into.
    other_log = 'pytest -q > /tmp/run.log 2>&1; rc=$?; echo "exit=$rc" >> /tmp/run.log; tail -n 1 /tmp/old.log'
    assert "✅" not in row(other_log, "exit=0")
    # Same-label echoes into logs come out in the order the logs are shown, not the order they ran.
    both = ('ruff check . > /tmp/l.log 2>&1; echo "exit=$?" >> /tmp/l.log; pytest -q > /tmp/t.log 2>&1; '
            'echo "exit=$?" >> /tmp/t.log; tail -qn 1 /tmp/t.log /tmp/l.log')
    for output in ("exit=0\nexit=1", "exit=1\nexit=0"):
        assert "✅" not in row(both, output) and "❌" not in row(both, output), output
    # Logs shown in any order, every line showing the same status: that status is each test's.
    two = ('pytest tests/a > /tmp/a.log 2>&1; echo "exit=$?" >> /tmp/a.log; pytest tests/b > /tmp/b.log 2>&1; '
           'echo "exit=$?" >> /tmp/b.log; tail -qn 1 /tmp/b.log /tmp/a.log')
    assert "✅ passed" in row(two, "exit=0\nexit=0")
    # A log read through a loop variable, and an echo inside a group that closes right after it.
    loop = ('pytest -q > "$N/final-sim.log" 2>&1; echo "sim_exit=$?" >> "$N/final-sim.log"; '
            'for f in final-sim; do tail -n 2 "$N/$f.log"; done')
    assert "❌ failed" in row(loop, "1 failed, 4 passed\nsim_exit=1")
    assert "✅ passed" in row("(pytest -q > ../t.log 2>&1; echo exit=$? >> ../t.log); tail -2 ../t.log",
                              "5 passed\nexit=0")
    # Parentheses in a quoted pattern are no group; a group's status is not the test's, pipefail or not.
    assert "✅ passed" in row('pytest -q 2>&1 | grep -E "(passed|failed)" | head -3; echo "exit=${PIPESTATUS[0]}"',
                              "5 passed\nexit=0")
    assert "✅" not in row('set -o pipefail; (pytest -q 2>&1 | tail -5); echo "exit=${PIPESTATUS[0]}"',
                           "5 passed\nexit=0")
    # Same-text echoes whose lines come out in another order than written (a log shown later, `&`, a function):
    # different statuses cannot be told apart.
    for command, output in [
            ('ruff check . > /tmp/r 2>&1; echo "exit=$?" >> /tmp/r; pytest -q > /tmp/p 2>&1; echo "exit=$?"; '
             'cat /tmp/r', "exit=1\nAll checks passed!\nexit=0"),
            ('(ruff check .; echo "exit=$?") & (pytest -q; echo "exit=$?") & wait', "exit=0\n3 passed\nexit=1"),
            ('show() { echo "exit=$?"; }; ruff check .; show; pytest -q > /tmp/t 2>&1; echo "exit=$?"',
             "exit=1\nexit=0")]:
        assert "✅" not in row(command, output) and "❌" not in row(command, output), command
    # Two runs of the test: the last decides a failure; a pass needs both.
    red_green = ('pytest -q -k new > /tmp/a 2>&1; echo "before fix exit=$?"; git stash pop -q; '
                 'pytest -q > /tmp/b 2>&1; echo "after fix exit=$?"')
    assert "❌" not in row(red_green, "before fix exit=1\nafter fix exit=0")
    assert "❌ failed" in row(red_green, "before fix exit=0\nafter fix exit=1")
    # `bash -c "…$?"`: the calling shell put its own status in before bash ran.
    assert "✅" not in row('bash -c "pytest -q > /tmp/l 2>&1; echo test exit $?"', "test exit 0")
    # Claude Code's own "Exit code 1" line, and a runner's "Tests: 5 passed", are no echo's line.
    grep_after = 'pytest -q > /tmp/t.log 2>&1; echo "Exit code $?" >> /tmp/t.log; grep -q FAILED /tmp/t.log'
    assert "❌ failed" not in row(grep_after, "Exit code 1", True)
    assert "❌" not in row('php artisan test 2>&1 | tail -3; echo "Tests: ${PIPESTATUS[0]}"',
                           "Tests:    5 passed (12 assertions)\nTests: 0")
    # Under pipefail a group's status is its last failing stage's: `grep -q` finding nothing, not the test.
    assert "❌" not in row('set -o pipefail; (pytest -q 2>&1 | grep -q "^FAILED"); echo "test rc=${PIPESTATUS[0]}"',
                           "test rc=1")
    # Another log whose name ends like the echo's log; a second echo argument; an echo sent to /dev/null.
    old = "test rc=1"
    assert "❌" not in row('pytest -q > /dev/null 2>&1; echo "test rc=$?" >> test.log; tail -n 1 old-test.log', old)
    assert "❌" not in row('pytest -q > /dev/null 2>&1; echo "test rc=$?" "(pytest)"; tail -n 1 old-test.log',
                           "test rc=0 (pytest)\ntest rc=1")
    assert "✅ passed" in row('pytest -q > /dev/null 2>&1; echo "test rc=$?" "(pytest)"', "test rc=0 (pytest)")
    assert "❌" not in row('pytest -q > /dev/null 2>&1; echo "test rc=$?" 1>/dev/null; tail -n 1 old-test.log', old)
    # `bash -o pipefail -c "…$?"`: a double-quoted script, whatever options come before `-c`.
    for shell in ("bash -o pipefail -c", "bash -O extglob -c", "timeout 600 bash -o pipefail -c"):
        assert "✅" not in row(f'{shell} "pytest -q > /tmp/l 2>&1; echo test exit $?"', "test exit 0"), shell
    # In a loop the test runs several times; one echo line is one of the runs.
    loop = 'for f in a b; do\n  pytest -q $f > /tmp/t 2>&1; echo "rc=$?" > /tmp/rc\ndone; tail -1 /tmp/rc'
    assert "✅" not in row(loop, "rc=0")
    # A log appended to and not emptied in this call may show an older run's line first.
    assert "✅" not in row('pytest -q > /tmp/t 2>&1; echo "rc=$?" >> /tmp/runs.log; head -2 /tmp/runs.log',
                           "earlier\nrc=0")
    assert "✅ passed" in row('pytest -q > /tmp/t.log 2>&1; echo "rc=$?" >> /tmp/t.log; tail -1 /tmp/t.log', "rc=0")
    # The same name in another directory is another log.
    assert "✅" not in row('pytest -q > /tmp/new/t.log 2>&1; echo "rc=$?" >> /tmp/new/t.log; tail -1 /tmp/old/t.log',
                           "rc=0")
    # An echo inside a group that closes right after it.
    assert "✅ passed" in row('(pytest -q > /tmp/t 2>&1; echo "exit=$?"); tail -1 /tmp/t', "exit=0\n5 passed")
    # A pipe inside a group: the status after it is the group's, which is tail's.
    assert "✅" not in row('(pytest -q 2>&1 | tail -n 20) > /tmp/t.log; rc=${PIPESTATUS[0]}; echo "test rc=$rc"',
                           "test rc=0")
    # `echo -n` leaves the line open for the next output.
    assert "❌" not in row('pytest -q > /dev/null 2>&1; rc=$?; echo -n "test rc=$rc"; printf "1 warning\\n"',
                           "test rc=01 warning")


def test_a_linters_error_count_before_the_test_is_no_test_failure(tmp_path):
    def row(command, output, is_error=False):
        return _verdicts(tmp_path, command, output, is_error).splitlines()[2]
    # ruff failed, so pytest never ran: the status and "Found 2 errors" are ruff's.
    echo = 'ruff check . && python -m pytest -q > /tmp/p.log 2>&1; echo "pytest exit=$?"'
    assert "❌" not in row(echo, "E501 line too long\nFound 2 errors.\npytest exit=1")
    assert "❌" not in row("ruff check . && pytest -q > /tmp/p.log 2>&1", "Exit code 1\nFound 2 errors.", True)
    tsc_first = "npx tsc --noEmit && npm test > /tmp/t 2>&1"
    assert "❌" not in row(tsc_first, "Exit code 2\nFound 3 errors in 2 files.", True)
    assert "❌" not in row("npx eslint . && npm test > /tmp/t 2>&1", "Exit code 1\n✖ 3 problems (3 errors, 0 warnings)",
                           True)
    # The test runner's own error counts stay the test's, a linter in the call or not.
    collection = "!!!!!!!! Interrupted: 2 errors during collection !!!!!!!!"
    assert "❌" in row("pytest -q --tb=no && ruff check .", f"Exit code 2\n{collection}", True)
    assert "❌ failed" in row('ruff check . && pytest -q --tb=no; echo "pytest exit=$?"',
                              f"All checks passed!\n{collection}\npytest exit=2")
    assert "❌" in row("mvn test && npm run build", "Exit code 1\n[INFO] BUILD FAILURE", True)
    # A version query is no lint run: the count is read as without a linter.
    assert "❌" in row("ruff --version && python gen.py && pytest -q", "Exit code 1\nruff 0.8.0\nFound 2 errors.", True)
    # A linter after a passing test prints its count too.
    assert "❌" not in row("pytest -q && ruff check .", "Exit code 1\n5 passed in 0.31s\nFound 2 errors.", True)
    # A test runner's own failure after the linter is still the test's.
    assert "❌ failed" in row(echo, "All checks passed!\n1 failed, 4 passed\npytest exit=1")
    # With no linter or build before it, an error count is the test runner's (pytest's collection errors).
    assert "❌" in row("python gen.py && pytest -q", "Exit code 2\n2 errors in 0.51s", True)


def test_an_echoed_status_counts_only_when_the_test_ran(tmp_path):
    # After `a &&`, a non-zero `$?` may be a's: the test never ran. A zero means both ran and passed.
    for is_error, end in ((True, "; false"), (False, "")):
        command = f'python patch.py && pytest -q > /tmp/t.log 2>&1; echo "exit=$?"{end}'
        output = ("Exit code 1\n" if is_error else "") + "AssertionError: old text not found\nexit=1"
        row = _verdicts(tmp_path, command, output, is_error).splitlines()[2]
        assert "⚠️ unclear" in row and "❌" not in row, command
    row = _verdicts(tmp_path, 'python gen.py && pytest -q > /tmp/t.log 2>&1; echo "exit=$?"; false',
                    "Exit code 1\nexit=0", True).splitlines()[2]
    assert "✅ passed" in row
    # A test failure in the output: the gate passed and the test ran.
    for is_error, end in ((True, "; false"), (False, "")):
        command = f'git worktree add -q ../wt main && cd ../wt && pytest -q > /tmp/t.log 2>&1; echo "exit=$?"{end}'
        output = ("Exit code 1\n" if is_error else "") + "exit=1\nFAILED tests/test_a.py::test_x\n1 failed, 4 passed"
        assert "❌ failed" in _verdicts(tmp_path, command, output, is_error).splitlines()[2], command
    # After `a ||`, a zero `$?` may be a's: the test ran only if a failed.
    row = _verdicts(tmp_path, '[ -f .skip ] || pytest -q > /tmp/t.log 2>&1; echo "exit=$?"; false',
                    "Exit code 1\nexit=0", True).splitlines()[2]
    assert "✅" not in row
    # The call failed and the output has failure language: an `exit=0` in it may be the test's own text.
    for output in ("Exit code 1\n  ✓ prints exit=0 for --help\n  1 failing\n  AssertionError: expected 2\nexit=1",
                   "Exit code 1\n  ✓ prints exit=0 for --help\n  TypeError: run is not a function\nexit=1"):
        row = _verdicts(tmp_path, 'npx mocha; echo "exit=$?"; false', output, True).splitlines()[2]
        assert "✅" not in row, output
    # A gate continued on the next line is still a gate.
    row = _verdicts(tmp_path, "python patch.py && \\\n  pytest -q > /tmp/t.log 2>&1",
                    "Exit code 1\nAssertionError: old text not found", True).splitlines()[2]
    assert "⚠️ unclear" in row


def test_a_step_that_cannot_own_the_exit_status_leaves_the_test_failed(tmp_path):
    # A step before the test joined by `;` ran first: the test's status replaced its own.
    row = _verdicts(tmp_path, "cd app; php artisan test", "Exit code 255\nPHP Fatal error:  Cannot redeclare f()",
                    True).splitlines()[2]
    assert "❌" in row
    # Under pipefail the pipe returns the test's failure, even with no summary printed.
    fatal = "Exit code 255\nPHP Fatal error:  Cannot redeclare f()"
    for command in ("set -o pipefail; php artisan test 2>&1 | tail -5",
                    "set -o pipefail; cd app && php artisan test 2>&1 | tail -5"):
        assert "❌" in _verdicts(tmp_path, command, fatal, True).splitlines()[2], command
    row = _verdicts(tmp_path, "set -o pipefail; python gen.py && php artisan test 2>&1 | tail -5",
                    "Exit code 255\nPHP Fatal error:  Cannot redeclare f()", True).splitlines()[2]
    assert "⚠️ unclear" in row  # gen.py may have failed, and a PHP fatal is no test summary
    # A failed `cd` prints its error: without one, the test after `cd … &&` ran and the status is its own.
    row = _verdicts(tmp_path, "cd app && pytest -q > /tmp/t.log 2>&1", "Exit code 1", True).splitlines()[2]
    assert "❌" in row
    row = _verdicts(tmp_path, "cd app && pytest -q > /tmp/t.log 2>&1",
                    "Exit code 1\nbash: cd: app: No such file or directory", True).splitlines()[2]
    assert "⚠️ unclear" in row
    row = _verdicts(tmp_path, "cd app && make gen && pytest -q > /tmp/t.log 2>&1", "Exit code 1", True).splitlines()[2]
    assert "⚠️ unclear" in row
    # node --test's own summary is a test failure, whatever ran after it.
    row = _verdicts(tmp_path, "node --test test/ > /tmp/t.log 2>&1; grep -E 'ℹ (pass|fail)' /tmp/t.log; grep -q x a.js",
                    "Exit code 1\nℹ pass 34\nℹ fail 1", True).splitlines()[2]
    assert "❌" in row
    # Under `set -e` any step before can end the call with its own status; `set` and `cd` themselves do not.
    row = _verdicts(tmp_path, "set -euo pipefail; python gen.py; pytest -q", "Exit code 1\nKeyError: 'x'",
                    True).splitlines()[2]
    assert "⚠️ unclear" in row
    row = _verdicts(tmp_path, "set -euo pipefail; cd app; php artisan test 2>&1 | tail -5", fatal, True).splitlines()[2]
    assert "❌" in row


def test_a_command_the_shell_could_not_parse_is_no_failed_test(tmp_path):
    for command, output in [
            ("python - <<'EOF'\nprint('x)\nEOF\npytest -q 2>&1 | tail -3",
             "Exit code 2\n/usr/bin/bash: -c: line 9: unexpected EOF while looking for matching `''"),
            ("pytest -q 2>&1 | tail -3", "Exit code 2\nbash: line 1: syntax error near unexpected token `)'")]:
        row = _verdicts(tmp_path, command, output, True).splitlines()[2]
        assert "⚠️ unclear" in row and "could not parse" in row, command
    # A script the test runs printed it, after the test started: the exit status is the test's.
    row = _verdicts(tmp_path, "npm test", "Exit code 2\n\n> app@1.0.0 test\n> bash -c 'echo )'\n\n"
                    "bash: -c: line 1: syntax error near unexpected token `)'", True).splitlines()[2]
    assert "❌" in row and "could not parse" not in row


def test_an_assignment_from_a_command_can_fail_before_the_test(tmp_path):
    for command in ("TEST_ROOT=$(mktemp) && pytest -q", "TEST_ROOT=`mktemp` && pytest -q"):
        row = _verdicts(tmp_path, command, "Exit code 1\nmktemp: failed to create file: No space left on device",
                        True).splitlines()[2]
        assert "⚠️ unclear" in row, command
    row = _verdicts(tmp_path, "TEST_ROOT=/tmp/t && pytest -q > /tmp/t.log 2>&1", "Exit code 1", True).splitlines()[2]
    assert "❌" in row


def test_a_tap_line_a_test_prints_is_no_runner_summary(tmp_path):
    row = _verdicts(tmp_path, "pytest -q -s tests/test_tap.py", "TAP version 13\n# fail 1\n.\n1 passed in 0.01s"
                    ).splitlines()[2]
    assert "✅" in row


@pytest.mark.parametrize("output", [
    "PreToolUse:Bash hook error: [python guard.py]: blocked: recursive delete outside the scratchpad",
    "The server-side auto mode classifier gave no verdict for Bash: a hook changed this call's input.",
    "This agent is isolated in the worktree /w/wt-1, but this command names git in a form too complex to verify "
    "that it stays inside the worktree. Refusing to run it.",
    "ENAMETOOLONG: name too long, uv_spawn",
    "The user doesn't want to take this action right now. STOP what you are doing.",
])
def test_a_call_that_never_ran_is_no_test_run(tmp_path, output):
    assert "No test command ran." in _verdicts(tmp_path, "pytest -q && rm -rf build", output, True)


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
    # "Found 1 error." is ruff's count, not the test runner's: no ❌ for the test that printed "5 passed".
    row = _verdicts(tmp_path, "pytest -q && ruff check .",
                    "Exit code 1\n5 passed in 0.01s\nF401 unused import\nFound 1 error.", True).splitlines()[2]
    assert "⚠️" in row and "❌" not in row and "later" in row
    row = _verdicts(tmp_path, "pytest -q && ruff check .", "Exit code 1\n1 failed, 4 passed", True).splitlines()[2]
    assert "❌" in row and "later" in row


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
    assert "tests: last ✅ passed (1 run)" in line
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
    assert "tests: last ❌ failed (1 run, 1 failed)" in line
    assert "files: 1 edited, 1 shell write" in line
    assert "tool calls: 4, 1 failed" in line


def test_brief_leads_with_the_last_test_run_and_counts_earlier_failures(tmp_path):
    from osop.recorder.receipt import render_brief
    fixed = _session(tmp_path, [
        ("Bash", {"command": "pytest -q"}, "Exit code 1\n1 failed, 2 passed", True),
        ("Bash", {"command": "pytest -q"}, "3 passed in 0.1s", False)])
    assert "tests: last ✅ passed (2 runs, 1 failed) ·" in render_brief(parse_transcript(fixed), "r.md")
    piped = _session(tmp_path, [
        ("Bash", {"command": "pytest -q"}, "3 passed in 0.1s", False),
        ("Bash", {"command": "pytest -q | tail -1"}, "3 passed in 0.1s", False)])
    assert "tests: last 🟡 probably passed (2 runs) ·" in render_brief(parse_transcript(piped), "r.md")
    # Failed is the receipt's ❌, not is_error: these two exit 0 with failures in the output.
    swallowed = _session(tmp_path, [
        ("Bash", {"command": "npm test"}, "Tests: 2 failed, 10 passed", False),
        ("Bash", {"command": "npm test"}, "Tests: 1 failed, 11 passed", False),
        ("Bash", {"command": "npm test"}, "Tests: 12 passed", False)])
    assert "tests: last ✅ passed (3 runs, 2 failed) ·" in render_brief(parse_transcript(swallowed), "r.md")
    # An unclear last run is never shown as passed, and an unclear earlier run is not counted as failed.
    unclear = _session(tmp_path, [
        ("Bash", {"command": "pytest -q; echo done"}, "collected 3 items\ndone", False),
        ("Bash", {"command": "pytest -q | tail -1"}, "collected 3 items", False)])
    assert "tests: last ⚠️ unclear (2 runs) ·" in render_brief(parse_transcript(unclear), "r.md")


def test_brief_without_a_timestamp_on_a_run_still_renders(tmp_path):
    path = _session(tmp_path, [("Bash", {"command": "pytest -q"}, "3 passed in 0.1s", False)])
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    events.append({"type": "assistant", "timestamp": None, "message": {"model": "m", "content": [
        {"type": "tool_use", "id": "t9", "name": "Bash", "input": {"command": "pytest -q"}}]}})
    events.append({"type": "assistant", "timestamp": "2026-10-23T10:01:00Z",
                   "message": {"model": "m", "content": [{"type": "text", "text": "All tests pass."}]}})
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    assert "tests: last ✅ passed (2 runs) ·" in render_brief(parse_transcript(path), "r.md")
    # The only run has no timestamp and no result; then a result arrives with one.
    only = [events[0], events[-2], events[-1]]
    path.write_text("\n".join(json.dumps(e) for e in only) + "\n", encoding="utf-8")
    brief = render_brief(parse_transcript(path), "r.md")
    assert "claims: 1 unclear ⚠️ (tests pass)" in brief and "tests: last ⏳ no result yet (1 run) ·" in brief
    result = {"type": "user", "timestamp": "2026-10-23T10:00:30Z", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t9", "content": "3 passed in 0.1s", "is_error": False}]}}
    answered = [*only[:2], result, only[2]]
    path.write_text("\n".join(json.dumps(e) for e in answered) + "\n", encoding="utf-8")
    assert "tests: last ✅ passed (1 run) ·" in render_brief(parse_transcript(path), "r.md")
    assert "✅ passed" in _section(_receipt(path), "Tests")


def test_brief_takes_the_last_test_run_by_time_not_by_who_ran_it(tmp_path):
    # A sub-agent's failing run came first; the main agent's passing run after it is the last one.
    from osop.recorder.receipt import render_brief
    session = _session(tmp_path, [("Agent", {"description": "Check"}, "done", False),
                                  ("Bash", {"command": "pytest -q"}, "3 passed in 0.1s", False)])
    subagents = tmp_path / "s" / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-x1.meta.json").write_text(json.dumps({"toolUseId": "t0"}), encoding="utf-8")
    (subagents / "agent-x1.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"type": "user", "timestamp": "2026-10-23T10:00:01Z", "message": {"role": "user", "content": "check"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:00:02Z", "message": {"content": [
            {"type": "tool_use", "id": "s1", "name": "Bash", "input": {"command": "pytest -q"}}]}},
        {"type": "user", "timestamp": "2026-10-23T10:00:02Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "s1", "content": "Exit code 1\n1 failed", "is_error": True}]}},
    ]) + "\n", encoding="utf-8")
    assert "tests: last ✅ passed (2 runs, 1 failed) ·" in render_brief(parse_transcript(session), "r.md")


def test_brief_takes_the_run_that_started_last_even_if_another_ended_later(tmp_path):
    # As the claim check: the main run started at :03 and ended at :40; the sub-agent's started at :10.
    from osop.recorder.receipt import render_brief
    events = [{"type": "user", "timestamp": "2026-10-23T10:00:00Z", "sessionId": "s",
               "message": {"role": "user", "content": "do the task"}}]
    for tid, tool, inp, out, err, start, end in [
            ("t0", "Agent", {"description": "Check"}, "done", False, "01", "02"),
            ("t1", "Bash", {"command": "pytest -q"}, "3 passed in 30s", False, "03", "40")]:
        events.append({"type": "assistant", "timestamp": f"2026-10-23T10:00:{start}Z", "message": {
            "model": "m", "content": [{"type": "tool_use", "id": tid, "name": tool, "input": inp}]}})
        events.append({"type": "user", "timestamp": f"2026-10-23T10:00:{end}Z", "message": {
            "role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": out, "is_error": err}]}})
    session = tmp_path / "s.jsonl"
    session.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    subagents = tmp_path / "s" / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-x1.meta.json").write_text(json.dumps({"toolUseId": "t0"}), encoding="utf-8")
    (subagents / "agent-x1.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"type": "user", "timestamp": "2026-10-23T10:00:09Z", "message": {"role": "user", "content": "check"}},
        {"type": "assistant", "timestamp": "2026-10-23T10:00:10Z", "message": {"content": [
            {"type": "tool_use", "id": "s1", "name": "Bash", "input": {"command": "pytest -q"}}]}},
        {"type": "user", "timestamp": "2026-10-23T10:00:11Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "s1", "content": "Exit code 1\n1 failed", "is_error": True}]}},
    ]) + "\n", encoding="utf-8")
    assert "tests: last ❌ failed (2 runs, 1 failed) ·" in render_brief(parse_transcript(session), "r.md")


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


# --- colour codes, refused commands, secrets --------------------------------------------------------


def test_a_refused_command_is_not_a_test_run(tmp_path):
    """Refused by the permission system or by the user: nothing ran, so it neither fails nor replaces the run before."""
    passed = ("Bash", {"command": "python -m pytest -q"}, "5 passed in 0.31s", False)
    for refusal in ("Permission for this tool use was denied. It requires approval, and this session has none.",
                    "The user doesn't want to proceed with this tool use. The tool use was rejected."):
        refused = ("Bash", {"command": "python -m pytest -q tests/test_slow.py"}, refusal, True)
        path = _session(tmp_path, [passed, refused])
        tests = _section(_receipt(path), "Tests")
        assert "❌" not in tests and tests.count("pytest") == 1, refusal


def test_secrets_in_commands_and_output_are_masked(tmp_path):
    key = "sk-ant-" + "a1B2" * 12
    token = "ghp_" + "Z9y8" * 10
    path = _session(tmp_path, [
        ("Bash", {"command": f"printf '%s' '{key}' > key.txt; python -m pytest -q"}, "5 passed", False),
        ("Bash", {"command": "env"}, f"GITHUB_TOKEN={token}\nExit code 1", True)])
    receipt = _receipt(path)
    assert key not in receipt and token not in receipt and key[:20] not in receipt
    assert "[secret]" in receipt


def test_a_private_key_is_masked_with_its_body_and_a_file_name_is_left_alone(tmp_path):
    body = "MC4CAQAwBQYDK2VwBCIEIA" + "EB" * 20
    pem = f"-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----"
    gitlab = "glpat-" + "aB3d" * 5
    name = "task-550e8400-e29b-41d4-a716-446655440000.json"
    path = _session(tmp_path, [
        ("Bash", {"command": f"git clone https://oauth2:{gitlab}@example.com/a.git"}, "done", False),
        ("Bash", {"command": "cat key.pem"}, pem, False), ("Bash", {"command": f"ls {name}"}, name, False)])
    receipt = _receipt(path)
    assert body not in receipt and "END PRIVATE KEY" not in receipt and gitlab not in receipt
    assert name in receipt


def test_a_token_at_the_end_of_long_output_is_masked_before_the_cut(tmp_path):
    """The Git table shows the end of the output: the tail of a token must not survive there."""
    token = "ghp_" + "Z9y8" * 9
    url = f"https://x-access-token:{token}@example.com/" + "r" * 62 + ".git"
    output = "Exit code 128\n" + "remote: " + "x" * 200 + f" fatal: unable to access {url}"
    receipt = _receipt(_session(tmp_path, [("Bash", {"command": "git push origin main"}, output, True)]))
    assert token[-12:] not in receipt and "[secret]@example.com" in receipt


def test_the_receipt_path_in_the_brief_is_never_masked(tmp_path):
    path = _session(tmp_path, [("Bash", {"command": "python -m pytest -q"}, "5 passed", False)])
    where = "C:/receipts/sk-" + "a" * 30 + "/2026-11-06-session.receipt.md"
    assert render_brief(parse_transcript(path), where).endswith(f"receipt: {where}")


def test_text_glued_to_a_token_stays_and_other_refusal_wordings_count(tmp_path):
    key = "sk-ant-" + "a1B2" * 12
    path = _session(tmp_path, [("Bash", {"command": f"echo {key}測試通過"}, "ok", False)])
    assert "[secret]測試通過" in _receipt(path)
    passed = ("Bash", {"command": "ruff check ."}, "All checks passed!", False)
    denied = "Permission for this command was denied by a built-in Claude Code safety check."
    tests = ("Bash", {"command": "python -m pytest -q"}, "5 passed", False)
    path = _session(tmp_path, [passed, tests, ("Bash", {"command": "ruff check . --fix"}, denied, True),
                               ("Bash", {"command": "python -m pytest -q -x"}, denied, True)])
    assert "❌" not in _section(_receipt(path), "Tests")


def test_tokens_after_a_written_newline_in_a_cut_goal_and_encrypted_keys(tmp_path):
    first, second = "ghp_" + "A" * 40, "ghp_" + "B" * 40
    body = "Zm9v" * 16
    pem = ("-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\nDEK-Info: AES-256-CBC,0123456789ABCDEF\n\n"
           + body + "\n-----END RSA PRIVATE KEY-----\nthen the tests ran")
    cut = "-----BEGIN PRIVATE KEY-----\n" + body + "\nand after that the build passed without the key"
    path = _session(tmp_path, [
        ("Bash", {"command": "printf '" + first + r"\n" + second + r"\n' > tokens.txt"}, "ok", False),
        ("Bash", {"command": "cat key.pem"}, pem, False), ("Bash", {"command": "cat cut.pem"}, cut, False)])
    receipt = _receipt(path)
    assert first not in receipt and second not in receipt and body not in receipt
    # The words after a key are kept, with or without its END line.
    assert _SECRET.sub("[secret]", pem).endswith("then the tests ran")
    assert _SECRET.sub("[secret]", cut).endswith("and after that the build passed without the key")
    goal = "x" * 580 + " " + first
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    events[0]["message"]["content"] = goal
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    assert "ghp_AAAA" not in _receipt(path)


def test_a_cut_encrypted_key_on_one_line_leaves_what_follows():
    body = "Zm9v" * 16
    flat = ("-----BEGIN RSA PRIVATE KEY----- Proc-Type: 4,ENCRYPTED DEK-Info: AES-256-CBC,0123456789ABCDEF "
            + body + " " + body + " 3 failed, 2 passed in 0.3s")
    assert _SECRET.sub("[secret]", flat) == "[secret]3 failed, 2 passed in 0.3s"


def test_a_key_without_its_end_line_is_masked_as_far_as_its_lines_go():
    head, line, bs = "-----BEGIN PRIVATE KEY-----", "Zm9v" * 16, chr(92)
    error = "FileNotFoundError: key.pem"
    assert _SECRET.sub("[secret]", f"{head}\n{line}\n{error}") == "[secret]" + error
    assert _SECRET.sub("[secret]", f"{head}{bs}r{bs}n{line}{bs}r{bs}n") == "[secret]"
    assert _SECRET.sub("[secret]", f"{head}\n{line}\nEXIT=1 after") == "[secret]EXIT=1 after"
    assert _SECRET.sub("[secret]", f"{head}\n{line}\nSTATUS= \n") == "[secret]STATUS= \n"


def test_a_token_split_by_colour_codes_is_masked_before_the_cut(tmp_path):
    token = "ghp_" + "Q7w3" * 9
    coloured = chr(27) + "[01;31mghp_" + chr(27) + "[m" + token[4:]
    output = f"Exit code 128\nfatal: unable to access https://u:{coloured}@example.com/" + "r" * 62 + ".git"
    receipt = _receipt(_session(tmp_path, [("Bash", {"command": "git push origin main"}, output, True)]))
    assert token[-10:] not in receipt and token[-10:] not in _tail(output)


def test_a_token_in_the_middle_of_long_output_is_masked_before_the_summary_is_cut(tmp_path):
    """A long output is shown by its two ends: a token across the cut must not leave its tail there."""
    key = "sk-proj-" + "Ab3d" * 39
    for is_error in (False, True):
        output = ("Exit code 1\n" if is_error else "") + "x" * 300 + " " + key + " " + "z" * 50
        receipt = _receipt(_session(tmp_path, [("Bash", {"command": "python -m pytest -q"}, output, is_error)]))
        assert "Ab3dAb3d" not in receipt, is_error


def test_a_token_across_the_cut_of_a_table_cell_leaves_no_head(tmp_path):
    token = "ghp_" + "Zq" * 20
    command = "echo " + "w" * 100 + f" {token} && python -m pytest -q"
    receipt = _receipt(_session(tmp_path, [("Bash", {"command": command}, "5 passed", False)]))
    assert "ghp_Zq" not in receipt


def test_many_key_headers_without_an_end_line_are_masked_in_linear_time():
    import time

    text = "-----BEGIN PRIVATE KEY-----\nshort\n" * 20000
    started = time.perf_counter()
    masked = _SECRET.sub("[secret]", text)
    assert time.perf_counter() - started < 1.0
    assert "BEGIN" not in masked and masked.count("short") == 20000


def _hooked(tmp_path, command, output, *, rewrites=None, note=None, is_error=False, final=None,
            event="PreToolUse", hook_id="t0", stdout=None) -> Path:
    """One Bash call as Claude Code records it when PreToolUse hooks replace its command: a hook_success
    attachment per hook, and Claude Code's reading of a non-zero exit it does not flag (returnCodeInterpretation)."""
    rewrites = ["set -o pipefail; " + command] if rewrites is None else rewrites
    events = [{"type": "user", "timestamp": "2026-11-26T10:00:00Z", "sessionId": "s",
               "message": {"role": "user", "content": "do the task"}},
              {"type": "assistant", "timestamp": "2026-11-26T10:00:01Z", "message": {"model": "m", "content": [
                  {"type": "tool_use", "id": "t0", "name": "Bash", "input": {"command": command}}]}}]
    for rewrite in rewrites:
        hook_out = stdout if stdout is not None else json.dumps(
            {"hookSpecificOutput": {"hookEventName": event, "updatedInput": {"command": rewrite}}})
        events.append({"type": "attachment", "timestamp": "2026-11-26T10:00:01Z", "attachment": {
            "type": "hook_success", "hookName": f"{event}:Bash", "hookEvent": event,
            "toolUseID": hook_id, "stdout": hook_out, "exitCode": 0}})
    result = {"type": "user", "timestamp": "2026-11-26T10:00:02Z", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t0", "content": output, "is_error": is_error}]}}
    if note:
        result["toolUseResult"] = {"stdout": output, "stderr": "", "returnCodeInterpretation": note}
    events.append(result)
    if final:
        events.append({"type": "assistant", "timestamp": "2026-11-26T10:00:03Z",
                       "message": {"model": "m", "content": [{"type": "text", "text": final}]}})
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


def test_a_pipe_the_pipefail_hook_rewrote_reports_the_tests_own_status(tmp_path):
    command = "python -m pytest -q | tail -3"
    hooked = _section(_receipt(_hooked(tmp_path, command, "3 passed in 0.1s")), "Tests")
    assert "✅ passed" in hooked and "set -o pipefail; python -m pytest" in hooked
    written = _section(_receipt(_hooked(tmp_path, command, "3 passed in 0.1s", rewrites=[])), "Tests")
    assert "🟡" in written and "✅" not in written


def test_a_tests_pass_claim_is_backed_by_a_run_the_hook_rewrote(tmp_path):
    path = _hooked(tmp_path, "python -m pytest -q | tail -3", "3 passed in 0.1s", final="All 3 tests pass.")
    brief = render_brief(parse_transcript(path), "r.md")
    assert "claims: 1 backed ·" in brief and "tests: last ✅ passed (1 run) ·" in brief


def test_a_failing_run_under_pipefail_that_ends_in_grep_is_a_failure(tmp_path):
    # pytest exits 1 and pipefail reports it; Claude Code reads exit 1 of a grep as "No matches found", not an error.
    tests = _section(_receipt(_hooked(tmp_path, 'python -m pytest -q 2>&1 | grep -E "passed|failed"',
                                      "1 failed, 2 passed in 0.2s", note="No matches found")), "Tests")
    assert "❌" in tests and "No matches found" in tests and "✅" not in tests


def test_a_non_zero_exit_claude_code_did_not_flag_is_never_a_pass(tmp_path):
    # Written by the agent, no hook: the grep found nothing, so the tests may have passed or failed.
    tests = _section(_receipt(_hooked(tmp_path, "set -o pipefail; python -m pytest -q | grep FAILED", "",
                                      rewrites=[], note="No matches found")), "Tests")
    assert "⚠️ unclear: a non-zero exit" in tests and "✅" not in tests
    commands = _section(_receipt(_hooked(tmp_path, "grep -n x f.py", "", rewrites=[], note="No matches found")),
                        "Commands")
    assert "non-zero exit, not flagged (Claude Code: No matches found)" in commands and "exit 0" not in commands


def test_a_build_claim_is_not_backed_by_a_non_zero_exit_claude_code_did_not_flag(tmp_path):
    # The agent's own pipefail: the build's failure or grep finding no "error" both give exit 1.
    path = _hooked(tmp_path, "set -o pipefail; npm run build 2>&1 | grep -i error", "", rewrites=[],
                   note="No matches found", final="The build succeeded.")
    brief = render_brief(parse_transcript(path), "r.md")
    assert "claims: 1 unclear ⚠️ (build succeeds)" in brief


def test_a_flag_on_a_status_that_something_else_hides_is_not_read(tmp_path):
    # `;` gives the call the grep's status, not the test's: the test's status stays hidden.
    tests = _section(_receipt(_hooked(tmp_path, "python -m pytest -q; grep -c x f.py", "3 passed in 0.1s",
                                      rewrites=[], note="No matches found")), "Tests")
    assert "🟡 probably passed: `;`" in tests


def test_a_flag_on_an_event_with_two_results_is_not_given_to_either(tmp_path):
    events = [{"type": "user", "timestamp": "2026-11-26T10:00:00Z", "sessionId": "s",
               "message": {"role": "user", "content": "do the task"}},
              {"type": "assistant", "timestamp": "2026-11-26T10:00:01Z", "message": {"model": "m", "content": [
                  {"type": "tool_use", "id": "t0", "name": "Bash", "input": {"command": "python -m pytest -q"}},
                  {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "grep -c x f.py"}}]}},
              {"type": "user", "timestamp": "2026-11-26T10:00:02Z", "message": {"role": "user", "content": [
                  {"type": "tool_result", "tool_use_id": "t0", "content": "3 passed in 0.1s", "is_error": False},
                  {"type": "tool_result", "tool_use_id": "t1", "content": "0", "is_error": False}]},
               "toolUseResult": {"stdout": "0", "stderr": "", "returnCodeInterpretation": "No matches found"}}]
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    assert "✅ passed" in _section(_receipt(path), "Tests")


def test_hooks_that_disagree_leave_the_command_as_written(tmp_path):
    command = "python -m pytest -q | tail -3"
    # Either would give the pipe the test's status, but which one ran is not recorded.
    tests = _section(_receipt(_hooked(tmp_path, command, "3 passed in 0.1s",
                                      rewrites=["set -o pipefail; " + command, "set -o pipefail;  " + command])),
                     "Tests")
    assert "🟡" in tests and "✅" not in tests


def test_a_separator_after_the_test_still_hides_it_under_the_hook(tmp_path):
    tests = _section(_receipt(_hooked(tmp_path, "python -m pytest -q | tail -3; echo done", "3 passed\ndone")),
                     "Tests")
    assert "set -o pipefail; python -m pytest" in tests  # the hook's command is the one judged
    assert "🟡 probably passed: `;`" in tests and "✅" not in tests


def test_only_a_pretooluse_hook_for_this_call_replaces_its_command(tmp_path):
    command = "python -m pytest -q | tail -3"
    for kwargs in ({"event": "PostToolUse"}, {"hook_id": "another-call"},
                   {"stdout": "[" * 10000 + "0" + "]" * 10000}):  # nested too deep for json: skipped, not a crash
        tests = _section(_receipt(_hooked(tmp_path, command, "3 passed in 0.1s", **kwargs)), "Tests")
        assert "🟡" in tests and "✅" not in tests, kwargs


def test_a_test_after_or_that_printed_nothing_may_not_have_run(tmp_path):
    tests = _section(_receipt(_hooked(tmp_path, "false || python -m pytest -q", "", rewrites=[])), "Tests")
    assert "it ran only if the command before failed, and no test result was printed" in tests and "✅" not in tests
    ran = _section(_receipt(_hooked(tmp_path, "false || python -m pytest -q", "3 passed in 0.1s", rewrites=[])),
                   "Tests")
    assert "✅ passed" in ran


def test_a_failing_step_after_the_test_in_a_hooked_pipe_is_not_blamed_on_the_test(tmp_path):
    tests = _section(_receipt(_hooked(tmp_path, "python -m pytest -q | tee out.log",
                                      "Exit code 1\n3 passed in 0.1s\ntee: out.log: No space left on device",
                                      is_error=True)), "Tests")
    assert "⚠️ unclear: exit 1, but output shows tests passing" in tests and "❌" not in tests


def test_failures_passed_on_by_tail_under_the_hook_are_the_tests(tmp_path):
    # npm's script swallowed pytest's exit status; tail only passes the summary on.
    tests = _section(_receipt(_hooked(tmp_path, "npm test 2>&1 | tail -3", "1 failed, 4 passed in 0.2s")), "Tests")
    assert "❌ exit 0 but output reports failures" in tests


def test_exit_zero_with_failures_another_command_printed_is_unclear(tmp_path):
    # Under pipefail exit 0 is the test's; the failures come from the old log the same call printed.
    old, new = "2 failed, 3 passed", "5 passed in 0.1s"
    for command, output in (("tail -2 old.log && python -m pytest -q | tail -1", f"{old}\n{new}"),
                            ("python -m pytest -q | tail -1 && tail -2 old.log", f"{new}\n{old}")):
        tests = _section(_receipt(_hooked(tmp_path, command, output)), "Tests")
        assert "⚠️ unclear: exit 0, but output reports failures" in tests and "❌" not in tests, command
