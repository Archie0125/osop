"""Render a one-page receipt from a parsed transcript: what the agent actually ran.

Everything here comes from tool calls and tool results in the transcript.
Nothing is taken from what the agent said about its own work.
"""

from __future__ import annotations

import re
from pathlib import Path

from osop.recorder.transcript import ToolCall, _walk

_TEST_RUNNER = (
    r"pytest|py\.test|unittest|nosetests|phpunit|pest|artisan\s+test|manage\.py\s+test|tox|prove"
    r"|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test|make\s+test|go\s+test|cargo\s+test"
    r"|vitest|jest|rspec|mvn\s+test|gradlew?\s+test|dotnet\s+test"
)
# A runner only counts at the start of a shell command, optionally behind wrappers (sudo, timeout N),
# VAR=x, a launcher (php, npx, python, `<interpreter> -m`) and a path. Run on code with quotes and
# heredocs removed.
_TEST_COMMAND = re.compile(
    r"(?:^|[;&|(\n])\s*"
    r"(?:(?:sudo(?:\s+-\S+)*|time|nice|env|nohup|timeout\s+\S+)\s+)*"
    r"(?:[A-Za-z_]\w*=\S*\s+)*"
    r"(?:(?:php|npx|bunx|uv\s+run|poetry\s+run|python[\d.]*(?:\.exe)?|py)\s+|\S+\s+-m\s+)?"
    r"(?:\S*/)?"
    rf"(?:{_TEST_RUNNER})\b"
)
# The heredoc body is blanked; the rest of its first line stays, so `cat <<EOF > file` keeps its redirect.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1([^\n]*)\n.*?\n\s*\2\b", re.DOTALL)
_QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
# Runners that can be called as a quoted executable path: "/my venv/bin/pytest".
_RUNNER_EXECUTABLE = re.compile(r"[\\/](pytest|py\.test|nosetests|phpunit|pest|tox|prove|vitest|jest|rspec)(?:\.exe)?$")
_FAILURE = re.compile(
    r"(?i:\b[1-9]\d*\s+(?:failed|failures?|errors?)\b|\bfail(?:ed|ures?):\s*[1-9])"
    r"|\bFAILED\b|--- FAIL|(?<![\w\[])FAIL\s+\S*[/\\.]|FAILURES!|BUILD FAILURE|\bnot ok\b"
)
_PIPEFAIL = re.compile(r"\bset\s+([-+])[a-z]*o\s+pipefail\b")
_CONTROL = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PASSED_COUNT = re.compile(r"\b\d+\s+passed\b", re.IGNORECASE)
_ECHOED_EXIT = re.compile(r"\bexit(?:\s+code)?\s*[=:]\s*\d+", re.IGNORECASE)
_EXIT_CODE = re.compile(r"^Exit code (\d+)")
# A lone `&` runs the command in the background: not `&&`, and not the `&` of `2>&1`, `<&0` or `&>`.
_BACKGROUND = re.compile(r"(?<![<>&])&(?![&>])")
# `bash -c '<script>'` (also sh, zsh, dash, ksh), with whatever separates it from the previous command as group 1.
_SHELL_C = re.compile(
    r"(^|[;&|(\n])\s*(?:(?:sudo(?:\s+-\S+)*|env|nohup|time)\s+)*"
    r"(?:ba|z|da|k)?sh(?:\.exe)?\s+(?:-[A-Za-z]*\s+)*-[A-Za-z]*c\s+"
)
_COMMAND_START = r"(?:^|[;&|(\n])\s*(?:sudo\s+)?"
# Shell commands whose text shows a file write. Only the operator or program is recognised, never the file.
_SHELL_WRITES = (
    ("`>`", re.compile(r"(?<![<>])>{1,2}(?!&)\s*(?!/dev/null\b)\S")),
    ("`tee`", re.compile(_COMMAND_START + r"tee\b")),
    ("`sed -i`", re.compile(r"\bsed\b[^|;&\n]*\s-(?:[A-Za-z]*i\S*|-in-place)")),
    ("`mv`/`cp`/`rm`/`touch`", re.compile(_COMMAND_START + r"(?:mv|cp|rm|touch)\b")),
    ("`patch`", re.compile(_COMMAND_START + r"patch\b")),
    ("`git` changing the working tree", re.compile(
        r"\bgit\s+(?:apply|restore|rm|mv|clean|stash(?!\s+list))\b"
        r"|\bgit\s+checkout\b[^|;&\n]*\s--(?:ours|theirs)?(?:\s|$)")),
)
_FILE_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")
_MAIN = "main"


def render_receipt(parsed: dict, source: Path | str) -> str:
    labels = _subagent_labels(parsed["nodes"])
    calls = [(_owner(node.node_id, labels), tc) for node in _walk(parsed["nodes"]) for tc in node.tool_calls]
    tests = [
        (by, tc, code, m) for by, tc in calls if tc.tool == "Bash"
        and (m := _TEST_COMMAND.search(code := _shell_code(_unwrap_shell_c(_command(tc)))))
    ]
    goal = next((n.user_prompt for n in parsed["nodes"] if n.user_prompt), "") or ""

    sections = [
        _overview(parsed, goal, calls, tests),
        "## Goal\n" + _quote(goal),
        "## Tests\n" + _tests_table(tests),
        "## Commands\n" + _commands_table(calls),
        "## Files changed\n" + _files_table(calls),
        "## Errors\n" + _errors_list(calls),
    ]
    if labels:
        sections.append("## Sub-agents\n" + _subagents_table(calls, labels))
    sections.append(
        f"---\nBuilt by `osop log` from `{source}`: read from the transcript, not written by the agent. "
        "Results are the exit status each tool reported."
    )
    return "\n\n".join(sections) + "\n"


def _overview(parsed: dict, goal: str, calls: list, tests: list) -> str:
    failed = sum(1 for _, tc in calls if tc.is_error)
    by_subagents = sum(1 for by, _ in calls if by != _MAIN)
    title = _cell(goal.splitlines()[0] if goal else "session", 70)
    rows = [
        ("Session", f"`{parsed.get('session_id') or '?'}`"),
        ("Model", parsed.get("model") or "unknown"),
        ("When", f"{parsed['started_at']} → {parsed['ended_at']} ({_duration(parsed['duration_ms'])})"),
        ("Tool calls", f"{len(calls)} total, {failed} failed, {by_subagents} by sub-agents"),
        ("Tests", _tests_summary(tests)),
    ]
    skipped = parsed.get("skipped") or []
    if skipped:
        # Missing evidence must be visible: "not read" is not "nothing happened".
        names = "; ".join(f"{_code(name)} ({_cell(why, 60)})" for name, why in skipped)
        rows.append(("Sub-agents not read", f"⚠️ {len(skipped)} could not be read, so their work is missing: {names}"))
    return f"# Receipt: {title}\n\n| | |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in rows)


def _tests_summary(tests: list) -> str:
    if not tests:
        return "none ran"
    verdicts = [_test_verdict(tc, code, m) for _, tc, code, m in tests]
    counts = [
        (sum(1 for v in verdicts if v.startswith(mark)), label)
        for mark, label in (("✅", "passed"), ("🟡", "probably passed"), ("❌", "failed"),
                            ("⚠️", "unclear ⚠️"), ("⏳", "no result"))
    ]
    return f"{len(verdicts)} runs: " + ", ".join(f"{n} {label}" for n, label in counts if n)


def _tests_table(tests: list) -> str:
    if not tests:
        return "**No test command ran.**"
    rows = [
        f"| {_cell(by)} | {_code(_command(tc))} | {_test_verdict(tc, code, m)} | {_cell(_tail(tc.output_summary), 90)} |"
        for by, tc, code, m in tests
    ]
    return "| By | Command | Result | Output (end) |\n|---|---|---|---|\n" + "\n".join(rows)


def _test_verdict(tc: ToolCall, code: str, match: re.Match) -> str:
    out = tc.output  # the whole output: a failure in the middle of a long run must not be lost
    if tc.status == "PENDING":
        return "⏳ no result recorded"
    if tc.is_error:
        if code[:match.start()].strip():
            return _failed(tc) + ": this test or an earlier step in the chain failed"
        return _failed(tc)
    if tc.input.get("run_in_background"):
        return "⏳ started in background; no exit status in the transcript"
    hidden = _hidden_status(code, match.end())
    if hidden is None:
        if _FAILURE.search(out):
            return "❌ exit 0 but output reports failures"
        return "✅ passed" if _PASSED_COUNT.search(out) else "✅ exit 0, no pass/fail summary seen"
    # The reported exit status belongs to whatever ran last, so only the output is left to read.
    echoed = _ECHOED_EXIT.findall(out)
    failing = bool(_FAILURE.search(out)) or any(int(re.search(r"\d+$", e).group()) for e in echoed)
    if _PASSED_COUNT.search(out) and not failing:
        return (f"🟡 probably passed: {hidden} after the test hid its exit status; "
                "output shows tests passing, no failure")
    hint = (f"; output prints {echoed[-1]}" if echoed
            else "; output mentions failures" if _FAILURE.search(out) else "")
    return f"⚠️ unclear: {hidden} after the test hides its exit status{hint}"


def _hidden_status(code: str, test_end: int) -> str | None:
    """What follows the test command and can hide its exit status, or None when nothing does.

    The result is shown inside a markdown table cell, so `|` is escaped.
    """
    rest = code[test_end:].rstrip()
    # `|| anything` runs the fallback when the test fails, and its exit status wins.
    if "||" in rest:
        return "`\\|\\|`"
    if ";" in rest:
        return "`;`"
    if "\n" in rest:
        return "a later line"
    if _BACKGROUND.search(rest):
        return "`&` (run in the background)"
    # Only the last `set -o pipefail` / `set +o pipefail` before the test applies.
    switches = _PIPEFAIL.findall(code[:test_end])
    if re.search(r"(?<!\|)\|(?!\|)", rest) and not (switches and switches[-1] == "-"):
        return "a pipe"
    return None


def _unwrap_shell_c(command: str) -> str:
    """`bash -c '<script>'` is analysed as the script it runs, so a test inside it is seen."""
    for _ in range(3):
        m = _SHELL_C.search(command)
        quoted = _QUOTED.match(command, m.end()) if m else None
        if not quoted:
            return command
        inner = quoted.group(0)[1:-1]
        if quoted.group(0)[0] == '"':
            inner = re.sub(r'\\(["\\$`])', r"\1", inner)
        command = command[:m.end(1)] + " " + inner + command[quoted.end():]
    return command


def _shell_code(command: str) -> str:
    """The command with heredoc bodies, quoted strings and comments blanked out."""
    def blank(m: re.Match) -> str:
        runner = _RUNNER_EXECUTABLE.search(m.group(0)[1:-1])
        return f"/{runner.group(1)}" if runner else '""'

    code = _QUOTED.sub(blank, _HEREDOC.sub(lambda m: "<<heredoc" + m.group(3), command))
    return re.sub(r"(?m)(^|[\s;&|(])#[^\n]*", r"\1", code)


def _commands_table(calls: list) -> str:
    shell = [(by, tc) for by, tc in calls if tc.tool == "Bash"]
    if not shell:
        return "**No shell commands ran.**"
    rows = [
        f"| {i} | {_cell(by)} | {_code(_command(tc))} | {_status(tc)} |"
        for i, (by, tc) in enumerate(shell, 1)
    ]
    return "| # | By | Command | Result |\n|---|---|---|---|\n" + "\n".join(rows)


def _files_table(calls: list) -> str:
    files: dict[str, dict[str, set[str] | dict[str, int]]] = {}
    pending: list[tuple[str, ToolCall, str]] = []
    for by, tc in calls:
        path = tc.input.get("file_path") or tc.input.get("notebook_path")
        if tc.tool not in _FILE_TOOLS or tc.is_error or not isinstance(path, str) or not path:
            continue
        if tc.status == "PENDING":
            pending.append((by, tc, path))  # no result: whether the edit was applied is unknown
            continue
        entry = files.setdefault(path, {"tools": {}, "by": set()})
        entry["tools"][tc.tool] = entry["tools"].get(tc.tool, 0) + 1
        entry["by"].add(by)
    parts = []
    if files:
        rows = [
            f"| {_code(_tail(path, 120))} | {', '.join(f'{t} ×{n}' for t, n in e['tools'].items())} | {_cell(', '.join(sorted(e['by'])))} |"
            for path, e in files.items()
        ]
        parts.append("| File | How | By |\n|---|---|---|\n" + "\n".join(rows))
    else:
        parts.append("**No files edited with Edit / Write.**")
    if pending:
        parts.append("⏳ Not counted, no result recorded: " + ", ".join(
            f"{_code(_tail(path, 120))} ({tc.tool}, {_cell(by)})" for by, tc, path in pending))
    parts.append(_shell_writes(calls))
    return "\n\n".join(parts)


def _shell_writes(calls: list) -> str:
    """Shell commands whose text shows a file write. What they changed is not known, only that they could."""
    rows = []
    for by, tc in calls:
        if tc.tool != "Bash" or tc.status == "PENDING":
            continue
        code = _shell_code(_unwrap_shell_c(_command(tc)))
        reasons = [name for name, pattern in _SHELL_WRITES if pattern.search(code)]
        if reasons:
            rows.append(f"- **{_cell(by)}** · {_code(_command(tc), 160)} ({', '.join(reasons)})"
                        + (" ❌ failed" if tc.is_error else ""))
    if not rows:
        return "No shell command that writes files (`>`, `tee`, `sed -i`, `mv`, `cp`, `rm`…) was seen."
    return "Shell commands that may have changed files (not verified):\n" + "\n".join(rows)


def _errors_list(calls: list) -> str:
    errors = [(by, tc) for by, tc in calls if tc.is_error]
    if not errors:
        return "**No tool call failed.**"
    return "\n".join(
        f"- **{_cell(by)}** · {tc.tool} {_code(_subject(tc), 100)} → {_cell(_EXIT_CODE.sub('', tc.output_summary), 160)}"
        for by, tc in errors
    )


def _subagents_table(calls: list, labels: dict[str, str]) -> str:
    rows = []
    for label in dict.fromkeys(labels.values()):
        mine = [tc for by, tc in calls if by == label]
        rows.append(f"| {_cell(label)} | {len(mine)} | {sum(1 for tc in mine if tc.is_error)} |")
    return "| Sub-agent | Tool calls | Failed |\n|---|---|---|\n" + "\n".join(rows)


def _subagent_labels(nodes: list) -> dict[str, str]:
    """Sub-agent id → the description of the Agent call that spawned it."""
    return {
        tc.sub_transcript_id: tc.input.get("description") or tc.sub_transcript_id
        for node in _walk(nodes) for tc in node.tool_calls if tc.sub_transcript_id
    }


def _owner(node_id: str, labels: dict[str, str]) -> str:
    """Sub-agent node ids are prefixed with their sub-agent id: '<id>.agent_1'."""
    if "." not in node_id:
        return _MAIN
    sub_id = node_id.rsplit(".", 1)[0].split(".")[-1]
    return labels.get(sub_id, sub_id)


def _status(tc: ToolCall) -> str:
    if tc.status == "PENDING":
        return "⏳ no result recorded"
    if tc.is_error:
        return _failed(tc)
    if tc.input.get("run_in_background"):
        return "⏳ started in background"
    # No ✅ here: exit 0 is only what the tool reported; the Tests section judges test runs.
    return "exit 0"


def _failed(tc: ToolCall) -> str:
    m = _EXIT_CODE.match(tc.output_summary)
    return f"❌ exit {m.group(1)}" if m else "❌ failed"


def _command(tc: ToolCall) -> str:
    return str(tc.input.get("command", ""))


def _subject(tc: ToolCall) -> str:
    for key in ("command", "file_path", "notebook_path", "url", "pattern", "description"):
        if tc.input.get(key):
            return str(tc.input[key])
    return ""


def _tail(text: str, limit: int = 90) -> str:
    return text if len(text) <= limit else "…" + text[-(limit - 1):]


def _cell(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", _CONTROL.sub("", str(text))).strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text.replace("|", "\\|")


def _code(text: str, limit: int = 120) -> str:
    cell = _cell(text, limit)
    if not cell:
        return cell
    # A code span needs a fence longer than any backtick run inside it.
    fence = "`" * (max((len(run) for run in re.findall(r"`+", cell)), default=0) + 1)
    return f"{fence} {cell} {fence}" if len(fence) > 1 else f"`{cell}`"


def _quote(text: str) -> str:
    text = _CONTROL.sub("", text).strip() or "(no prompt recorded)"
    if len(text) > 600:
        text = text[:599] + "…"
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def _duration(ms: int) -> str:
    seconds = max(ms, 0) // 1000
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
