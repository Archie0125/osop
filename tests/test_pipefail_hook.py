"""hooks/osop-pipefail.py — a PreToolUse hook that puts `set -o pipefail;` in front of a Bash command that pipes a
test, lint or build runner, so the pipe cannot hide the runner's exit status.

The package does not install it: wiring it into settings.json is the owner's decision.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from osop.recorder.pipefail import with_pipefail

HOOK = Path(__file__).parent.parent / "hooks" / "osop-pipefail.py"


@pytest.mark.parametrize(
    "command",
    [
        "pytest tests/ 2>&1 | tail -20",
        "npm test 2>&1 | grep -E 'passed|failed'",
        "cd app && npm run build 2>&1 | tail -5",
        "ruff check . | tee lint.log",
        "python -m pytest -q |& tail -3",
        "pytest tests/ |\n  tail -5",  # a pipe continued on the next line
        "pytest tests/ \\\n  | tail -5",
        "cd x\npytest | tail -5",  # the runner is not on the first line
        "go test ./... 2>&1 | sed -n '1,40p'",  # sed without q reads everything
        "timeout 300 pytest | tail -20",
        "pytest 2>&1 | tee out.log | grep -c passed",
        "cargo test 2>&1 | grep -E 'test result' | tail -1",
        "npx vitest run 2>&1 | tail -15; echo done",  # the pipe is in the runner's own command
    ],
)
def test_a_piped_runner_gets_pipefail(command):
    assert with_pipefail(command) == "set -o pipefail; " + command


@pytest.mark.parametrize(
    "command",
    [
        "pytest tests/",  # no pipe
        "pytest tests/ && echo ok",
        "ls -la | head -3",  # no runner
        "pytest tests/ 2>&1 | head -50",  # head stops early: the runner would die of a broken pipe
        "pytest | tee log | head -3",
        "pytest |& head -3",
        "npm test 2>&1 | grep -q passed",
        "npm test 2>&1 | grep -m1 passed",
        "npm test 2>&1 | grep -il passed",
        "npm test 2>&1 | rg --max-count=1 passed",
        "npm test 2>&1 | sed -n '1,/summary/{p;q}'",
        "npm test 2>&1 | sed 20q",
        "npm test 2>&1 | awk '/FAIL/{print; exit}'",
        "set -o pipefail; pytest | tail -3",  # already set
        "set -eo pipefail\npytest | tail -3",
        "set +o pipefail; pytest | tail -3",  # turned off on purpose
        "bash -c 'pytest | tail -3'",  # a new shell does not inherit the option
        "echo 'pytest | tail' > run.sh",  # the pipe is inside a string
        "cat <<'EOF' > t.sh\npytest | tail -3\nEOF",  # or a heredoc
        "pytest tests/; ls | head -3",  # the pipe is on another command
        "pytest tests/ || ls | head -3",
        "pytest tests/ # see | head",  # a comment
        "echo 'running pytest' | cat",  # the runner is inside a string
        "",
    ],
)
def test_left_alone(command):
    assert with_pipefail(command) is None


@pytest.mark.parametrize(
    "command",
    [
        "pytest | tail -3\nnpm run build | head -5",
        "pytest | tail -3\ngit log --oneline | head -3",  # not a runner, but the prefix would govern it too
        "pytest | tail -3 && ls | head",
        "echo $(pytest | head -1)",
    ],
)
def test_a_pipe_into_head_anywhere_leaves_the_whole_command_alone(command):
    # One prefix governs every pipeline in the command; a reader that stops early would fail it.
    assert with_pipefail(command) is None


def test_other_pipes_that_read_everything_are_fine():
    command = "git status | cat; pytest | tail -3"
    assert with_pipefail(command) == "set -o pipefail; " + command


def _run_hook(stdin: bytes) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True, timeout=60)


def _payload(command: str, **fields) -> bytes:
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "permission_mode": "auto",
                       "tool_input": {"command": command, **fields}}).encode("utf-8")


def test_hook_rewrites_the_command_and_keeps_the_other_fields():
    result = _run_hook(_payload("pytest | tail -3", description="run the tests", timeout=60))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "updatedInput": {"command": "set -o pipefail; pytest | tail -3", "description": "run the tests", "timeout": 60},
    }}


def test_hook_keeps_non_ascii_fields_and_prints_ascii():
    result = _run_hook(_payload("npm test 2>&1 | tail -5", description="跑測試"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.isascii()  # a cp950 console cannot print the description; Claude Code decodes \u escapes
    assert json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]["description"] == "跑測試"


@pytest.mark.parametrize(
    "stdin",
    [
        _payload("pytest tests/"),  # nothing to change
        _payload("pytest | head -5"),
        json.dumps({"tool_name": "PowerShell", "tool_input": {"command": "pytest | Select-Object -Last 5"}}).encode(),
        json.dumps({"tool_name": "Bash", "tool_input": {}}).encode(),
        json.dumps({"tool_name": "Bash", "tool_input": {"command": 5}}).encode(),
        json.dumps(["not", "a", "dict"]).encode(),
        b"not json",
        b"",
        b"\xff\xfe\x00",
    ],
)
def test_hook_is_silent_and_exits_zero_when_it_has_nothing_to_say(stdin):
    # Silence leaves the command as written; a non-zero exit would be reported as a hook failure on every Bash call.
    result = _run_hook(stdin)
    assert result.returncode == 0, result.stderr
    assert result.stdout == b""
