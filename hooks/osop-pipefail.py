#!/usr/bin/env python3
"""Claude Code PreToolUse hook: `set -o pipefail;` in front of a Bash command that pipes a test, lint or build runner.

`pytest 2>&1 | tail -20` exits with tail's status, so a failing run looks like a success to the agent and the
transcript keeps no exit status for the receipt. With pipefail the pipeline fails when the runner fails.
A runner piped into a reader that stops early (head, grep -q/-m/-l, sed … q, awk … exit) is left alone:
it would die of a broken pipe, and pipefail would turn a passing run into a failure.

The package does not install this. Wiring it in changes settings.json, so that is the owner's call. To use
it, add under "hooks" → "PreToolUse" (with a python that has osop installed):

    {"matcher": "Bash", "hooks": [{"type": "command", "command": "python /path/to/hooks/osop-pipefail.py",
                                   "timeout": 10}]}

Input (stdin JSON, per the Claude Code hooks reference): tool_name, tool_input, permission_mode, …
Output: hookSpecificOutput.updatedInput holding the whole tool_input (Claude Code replaces the input object,
so the other fields are copied over), or nothing when the command runs as written. No permissionDecision:
the normal permission flow still applies, to the rewritten command. In auto mode a command hook from
settings may change a Bash call without the "changed after review" denial; when that denial does appear,
Claude repeats the call and the repeat runs as reviewed.

Any failure is silent and exits 0: the command then runs as written. A non-zero exit would be reported as a
hook failure on every Bash call.
"""

import json
import sys


def main() -> int:
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
        if not isinstance(data, dict) or data.get("tool_name") != "Bash":
            return 0
        tool_input = data.get("tool_input")
        if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
            return 0
        from osop.recorder.pipefail import with_pipefail

        command = with_pipefail(tool_input["command"])
        if command is None:
            return 0
        output = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                         "updatedInput": {**tool_input, "command": command}}}
        # ASCII-only JSON: a cp950 console cannot print the description's Chinese; Claude Code decodes the escapes.
        sys.stdout.write(json.dumps(output) + "\n")
        sys.stdout.flush()
    except Exception:  # noqa: BLE001 — a broken hook must not break Bash
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
