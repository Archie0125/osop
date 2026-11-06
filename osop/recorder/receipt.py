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
    r"|(?<![\w\[])FAILED\b|--- FAIL|(?<![\w\[])FAIL\s+\S*[/\\.]|FAILURES!|BUILD FAILURE|\bnot ok\b"
)
# Failure language that a "probably passed" output must not contain. Broader than _FAILURE and only used to
# withhold 🟡 when the exit status is hidden; it never makes an exit-0 run ❌ on its own.
_DOUBT = re.compile(
    r"npm ERR!|make: \*\*\*|Traceback \(most recent call last\)|(?<![\w\[])FAIL(?![\w\]])|\bError \d+\b"
    r"|INTERNALERROR|\b[A-Z][A-Za-z]*Error\b|\bException\b|KeyboardInterrupt|Fatal Python error"
    r"|\bpanic\b|\bsegfault\b|Segmentation fault|core dumped|\bfatal:"
    r"|(?i:coverage (?:failure|threshold)|fail-under|not reached)"
)
_PIPEFAIL = re.compile(r"\bset\s+([-+])[a-z]*o\s+pipefail\b")
_CONTROL = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PASSED_COUNT = re.compile(r"\b\d+\s+passed\b", re.IGNORECASE)
_ECHOED_EXIT = re.compile(r"\bexit(?:\s+code)?\s*[=:]\s*\d+", re.IGNORECASE)
_EXIT_CODE = re.compile(r"^Exit code (\d+)")
# A lone `&` runs the command in the background: not `&&`, and not the `&` of `2>&1`, `<&0`, `|&` or `&>`.
_BACKGROUND = re.compile(r"(?<![<>&|])&(?![&>])")
# `bash -c '<script>'` (also sh, zsh, dash, ksh, by name or path): group 1 is what separates it from the
# previous command, group 2 the shell's own options before `-c` (`-euo pipefail`, `-O extglob`, `--norc`, `-l`),
# group 3 the flag cluster holding `c`. Wrappers (`sudo -n`, `env -i`, `nice -n 10`, `timeout -s KILL 60`) come first.
_SHELL_C = re.compile(
    r"(^|[;&|(\n])\s*"
    r"(?:(?:sudo(?:\s+-\S+(?:\s+[^-\s]\S*)?)*|env(?:\s+-\S+)*|nohup|time|nice(?:\s+-n\s+\S+)?"
    r"|timeout(?:\s+-\S+\s+\S+)?\s+\S+)\s+)*"
    r"(?:[A-Za-z_]\w*=\S*\s+)*"
    r"(?:\S*[\\/])?(?:ba|z|da|k)?sh(?:\.exe)?\s+"
    r"((?:[-+][A-Za-z]*[oO]\s+\S+\s+|--(?:rcfile|init-file)\s+\S+\s+|--[a-z-]+\s+|-[A-Za-z]*\s+)*)"
    r"(-[A-Za-z]*c)\s+(?:--\s+)?"
)
_COMMAND_START = r"(?:^|[;&|(\n])\s*(?:sudo\s+)?"
# Shell commands whose text shows a file write. Only the operator or program is recognised, never the file.
_SHELL_WRITES = (
    ("`>`", re.compile(r"(?<![<>])>{1,2}(?!&)\s*(?!/dev/null\b)\S")),
    ("`tee`", re.compile(_COMMAND_START + r"tee\b")),
    ("`sed -i`", re.compile(r"\bsed\b[^|;&\n]*\s-(?:[A-Za-z]*i\S*|-in-place)")),
    ("`perl -i`", re.compile(r"\bperl\b[^|;&\n]*\s-[a-z]*i\S*")),
    ("`mv`/`cp`/`rm`/`touch`", re.compile(_COMMAND_START + r"(?:mv|cp|rm|touch)\b")),
    ("`patch`", re.compile(_COMMAND_START + r"patch\b")),
    ("`git` changing the working tree", re.compile(
        r"\bgit\s+(?:apply|restore|rm|mv|clean|stash(?!\s+list)|reset\s+--hard)\b"
        r"|\bgit\s+checkout\b[^|;&\n]*\s(?:--(?:ours|theirs)?|\.(?:/\S*)?)(?:\s|$)")),
)
# Commands whose result backs a "committed / pushed / merged" claim. Global options (`git -c x=y`, `git -C dir`,
# `git --no-pager`) may sit between `git` and the verb; `merge-base` and `tag-something` are not `merge` / `tag`.
_GIT_VERB = (
    r"(?:git\s+(?:-[cC]\s+\S+\s+|--(?:git-dir|work-tree|namespace|exec-path|config-env)\s+\S+\s+"
    r"|--[\w-]+(?:=\S*)?\s+)*(?:commit|push|merge|rebase|cherry-pick|tag(?!\s+(?:-l|--list|-n\d*)(?!\S)))(?![\w-])"
    r"|gh\s+pr\s+(?:create|merge|close)(?![\w-]))")
# `timeout 900 git push`, `GIT_EDITOR=true git cherry-pick`, `env X=1 git push`: wrappers seen in real sessions.
_GIT_START = _COMMAND_START + r"(?:(?:timeout(?:\s+-\S+)*\s+\S+|nohup|env(?:\s+-\S+)*)\s+)*(?:[A-Za-z_]\w*=\S*\s+)*"
_GIT_STATE = re.compile(_GIT_START + _GIT_VERB)
_GIT_SEGMENT = re.compile(_GIT_START + "(" + _GIT_VERB + r"[^;&|\n)]*)")
_FILE_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")
_MAIN = "main"


def render_receipt(parsed: dict, source: Path | str) -> str:
    labels, calls, tests, goal = _collect(parsed)
    unfinished = _unfinished(calls, tests)
    git = _git_table(calls)

    # What a person needs before trusting "done" comes first; the full command table is collapsed at the end.
    sections = [
        _overview(parsed, goal, calls, tests, unfinished),
        "## Goal\n" + _quote(goal),
        "## Tests\n" + _tests_table(tests),
        "## Files changed\n" + _files_table(calls),
        "## Errors\n" + _errors_list(calls),
    ]
    if unfinished:
        sections.append("## No result recorded\n" + _unfinished_list(unfinished))
    if git:
        sections.append("## Git\n" + git)
    if labels:
        sections.append("## Sub-agents\n" + _subagents_table(calls, labels))
    sections.append("## Commands\n" + _commands_details(calls))
    sections.append(
        f"---\nBuilt by `osop log` from `{source}`: read from the transcript, not written by the agent. "
        "Results are the exit status each tool reported."
    )
    return "\n\n".join(sections) + "\n"


def render_brief(parsed: dict, receipt_path: Path | str) -> str:
    """One line for a Stop hook: the counts a person needs before trusting "done", and where the receipt is."""
    _, calls, tests, _ = _collect(parsed)
    files, pending_edits = _edited_files(calls)
    writes = _shell_write_rows(calls)
    unfinished = _unfinished(calls, tests)
    failed = sum(1 for _, tc in calls if tc.is_error)

    files_part = f"files: {len(files)} edited"
    if writes:
        files_part += f", {_count(len(writes), 'shell write')} (not verified)"
    if pending_edits:
        files_part += f", {_count(len(pending_edits), 'edit')} without result"
    calls_part = f"tool calls: {len(calls)}, {failed} failed"
    if unfinished:
        calls_part += f", {len(unfinished)} without result"
    parts = [f"tests: {_tests_summary(tests)}", files_part, calls_part]
    skipped = parsed.get("skipped") or []
    if skipped:
        parts.append(f"⚠️ {_count(len(skipped), 'sub-agent transcript')} could not be read")
    parts.append(f"receipt: {receipt_path}")
    return "OSOP · " + " · ".join(parts)


def _collect(parsed: dict) -> tuple[dict[str, str], list, list, str]:
    """Every tool call with who made it, the test runs among them, and the first prompt."""
    labels = _subagent_labels(parsed["nodes"])
    calls = [(_owner(node.node_id, labels), tc) for node in _walk(parsed["nodes"]) for tc in node.tool_calls]
    tests = [
        (by, tc, code, m) for by, tc in calls if tc.tool == "Bash"
        and (m := _TEST_COMMAND.search(code := _shell_code(_unwrap_shell_c(_command(tc)))))
    ]
    goal = next((n.user_prompt for n in parsed["nodes"] if n.user_prompt), "") or ""
    return labels, calls, tests, goal


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _overview(parsed: dict, goal: str, calls: list, tests: list, unfinished: list) -> str:
    failed = sum(1 for _, tc in calls if tc.is_error)
    by_subagents = sum(1 for by, _ in calls if by != _MAIN)
    title = _cell(goal.splitlines()[0] if goal else "session", 70, in_table=False)
    tool_calls = f"{len(calls)} total, {failed} failed, {by_subagents} by sub-agents"
    if unfinished:
        tool_calls += f", {len(unfinished)} without result"
    rows = [
        ("Session", f"`{parsed.get('session_id') or '?'}`"),
        ("Model", parsed.get("model") or "unknown"),
        ("When", f"{parsed['started_at']} → {parsed['ended_at']} ({_duration(parsed['duration_ms'])})"),
        ("Tool calls", tool_calls),
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
        f"| {_cell(by)} | {_code(_command(tc))} | {_cell(_test_verdict(tc, code, m), 200)} "
        f"| {_cell(_tail(tc.output_summary), 90)} |"
        for by, tc, code, m in tests
    ]
    return "| By | Command | Result | Output (end) |\n|---|---|---|---|\n" + "\n".join(rows)


def _test_verdict(tc: ToolCall, code: str, match: re.Match) -> str:
    out = tc.output  # the whole output: a failure in the middle of a long run must not be lost
    if tc.status == "PENDING":
        return "⏳ no result recorded"
    mentions_failure = bool(_FAILURE.search(out) or _DOUBT.search(out))
    clean_pass = bool(_PASSED_COUNT.search(out)) and not mentions_failure
    if tc.is_error:
        # `set -o pipefail`, separators and group brackets are not steps that can fail.
        before = bool(re.sub(rf"[\s;&|(){_NEW_SHELL}]+", "", _PIPEFAIL.sub("", code[:match.start()])))
        after = _hidden_status(code, match.end()) is not None or "&&" in code[match.end():]
        which = "an earlier or later" if before and after else "an earlier" if before else "a later"
        if after and clean_pass:
            return f"⚠️ unclear: {_exit_text(tc)}, but output shows tests passing; {which} step in the chain may have failed"
        if before or after:
            return f"{_failed(tc)}: this test or {which} step in the chain failed"
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
    runs = sum(1 for _ in _TEST_COMMAND.finditer(code))
    if runs > 1:
        # "3 passed" may belong to any of them; the others may have failed silently.
        return f"⚠️ unclear: {hidden} after the test hides its exit status; {runs} test commands in one call"
    # A `||` fallback may have run and printed, and its output cannot be told apart from the test's.
    echoed_failure = any(int(re.search(r"\d+$", e).group()) for e in echoed)
    if clean_pass and hidden != "`||`" and not echoed_failure:
        return (f"🟡 probably passed: {hidden} after the test hid its exit status; "
                "output shows tests passing, no failure")
    hint = (f"; output prints {echoed[-1]}" if echoed
            else "; output mentions failures" if mentions_failure
            else "; output shows tests passing" if clean_pass else "")
    return f"⚠️ unclear: {hidden} after the test hides its exit status{hint}"


def _hidden_status(code: str, test_end: int) -> str | None:
    """What follows the test command and can hide its exit status, or None when nothing does."""
    rest = code[test_end:].rstrip()
    # `|| anything` runs the fallback when the test fails, and its exit status wins.
    if "||" in rest:
        return "`||`"
    if ";" in rest:
        return "`;`"
    if "\n" in rest:
        return "a later line"
    if _BACKGROUND.search(rest):
        return "`&` (run in the background)"
    # Every pipe after the test, inner or outer, needs a `set -o pipefail` that still governs it; the last one wins.
    for pipe in re.finditer(r"(?<!\|)\|(?!\|)", rest):
        pipe_pos = test_end + pipe.start()
        switches = [m.group(1) for m in _PIPEFAIL.finditer(code[:test_end]) if _governs(code, m.start(), pipe_pos)]
        if not (switches and switches[-1] == "-"):
            return "a pipe"
    return None


_NEW_SHELL = "\x01"  # placed before the `(` that stands for an unwrapped `bash -c '…'`


def _governs(code: str, switch_pos: int, pipe_pos: int) -> bool:
    """Whether `set ±o pipefail` at switch_pos still applies at pipe_pos.

    A subshell `( … )` inherits the option, but it ends with the group that set it, and a new shell
    (`bash -c`, marked by _NEW_SHELL) starts without it.
    """
    open_groups: list[bool] = []
    for i in range(switch_pos, pipe_pos):
        if code[i] == "(":
            open_groups.append(i > 0 and code[i - 1] == _NEW_SHELL)
        elif code[i] == ")":
            if not open_groups:
                return False
            open_groups.pop()
    return not any(open_groups)


def _unwrap_shell_c(command: str, blank: bool = True) -> str:
    """`bash -c '<script>'` becomes a marked group `( <script> )`: the test inside is seen, an operator after the
    closing quote applies to the whole script, and the mark records where a new shell started (see _governs).

    With `blank`, the script's quotes and comments are blanked and its `-o pipefail` is written in, for analysis.
    Without, the script is spliced in as written, for display.
    """
    command = command.replace(_NEW_SHELL, "")
    pos = 0
    for _ in range(100):  # each pass unwraps one `bash -c`; the bound only stops a pathological command
        m = _SHELL_C.search(command, pos)
        if not m:
            break
        options = f"{m.group(2)} {m.group(3)}"
        if re.search(r"(?:^|\s)-[A-Za-z]*n[A-Za-z]*(?:\s|$)", options):
            pos = m.end()  # `-n` checks the script's syntax and runs nothing: there is no test run to see
            continue
        quoted = _QUOTED.match(command, m.end())
        if quoted:
            inner = quoted.group(0)[1:-1]
            if quoted.group(0)[0] == '"':
                inner = re.sub(r'\\(["\\$`])', r"\1", inner)
            end = quoted.end()
        else:
            word = re.match(r"[^\s;&|()]+", command[m.end():])
            if not word:
                break
            inner, end = word.group(0), m.end() + word.end()
        # Nested shells are unwrapped first; then the script's own quotes and comments end with the script.
        inner = _unwrap_shell_c(inner, blank)
        pipefail = re.findall(r"(?:^|\s)([-+])[A-Za-z]*o\s+pipefail\b", options)
        if blank and pipefail and pipefail[-1] == "-":  # a later `+o pipefail` turns it off again
            inner = "set -o pipefail; " + inner
        head = f"{command[:m.end(1)]} {_NEW_SHELL}( {_shell_code(inner) if blank else inner} ) "
        command = head + command[end:]
        pos = len(head)
    return command


def _shell_code(command: str) -> str:
    """The command with heredoc bodies, quoted strings and comments blanked out."""
    def blank(m: re.Match) -> str:
        runner = _RUNNER_EXECUTABLE.search(m.group(0)[1:-1])
        return f"/{runner.group(1)}" if runner else '""'

    code = _QUOTED.sub(blank, _HEREDOC.sub(lambda m: "<<heredoc" + m.group(3), command))
    return re.sub(r"(?m)(^|[\s;&|(])#[^\n]*", r"\1", code)


def _commands_details(calls: list) -> str:
    """The full command table, collapsed: every test, file, failure and unfinished call is already shown above."""
    shell = [(by, tc) for by, tc in calls if tc.tool == "Bash"]
    if not shell:
        return "**No shell commands ran.**"
    failed = sum(1 for _, tc in shell if tc.is_error)
    rows = [
        f"| {i} | {_cell(by)} | {_code(_command(tc))} | {_status(tc)} |"
        for i, (by, tc) in enumerate(shell, 1)
    ]
    table = "| # | By | Command | Result |\n|---|---|---|---|\n" + "\n".join(rows)
    summary = f"{_count(len(shell), 'shell command')}, {failed} failed (expand for the full table)"
    return f"<details>\n<summary>{summary}</summary>\n\n{table}\n\n</details>"


def _unfinished(calls: list, tests: list) -> list:
    """Calls with nothing to judge: no result arrived, or they were started in the background.

    Test runs and file edits already show their ⏳ in their own sections; a call that failed to start has a
    result (its failure) and is listed under Errors.
    """
    test_calls = {id(tc) for _, tc, _, _ in tests}
    return [
        (by, tc) for by, tc in calls
        if (tc.status == "PENDING" or (tc.input.get("run_in_background") and not tc.is_error))
        and id(tc) not in test_calls and tc.tool not in _FILE_TOOLS
    ]


def _unfinished_list(unfinished: list) -> str:
    return "\n".join(
        f"- **{_cell(by, in_table=False)}** · {tc.tool} {_code(_subject(tc), 120, in_table=False)} → {_status(tc)}"
        for by, tc in unfinished
    )


def _git_table(calls: list) -> str:
    """Commands whose result backs or contradicts a "committed / pushed / merged" claim."""
    rows = []
    for by, tc in calls:
        if tc.tool != "Bash":
            continue
        code = _shell_code(_unwrap_shell_c(_command(tc)))
        m = _GIT_STATE.search(code)
        if not m:
            continue
        # Show the git part, not the start of a long script. Unwrapped as written, so a push inside
        # `bash -c '…'` is shown with its quotes (the marker is a control char, stripped by _cell).
        segments = _git_segments(_unwrap_shell_c(_command(tc), blank=False))
        # The call's exit status is only this command's when nothing after it can replace it (same rule as tests).
        judged = tc.status == "COMPLETED" and not tc.is_error and not tc.input.get("run_in_background")
        hidden = _hidden_status(code, m.end()) if judged else None
        # `git diff --quiet || git commit`: exit 0 may be the first command's, with the commit never run.
        # A `\`-continued line and an opening `(` between the `||` and the command do not change that.
        before = re.sub(r"[\s(]+$", "", re.sub(r"\\\n", " ", code[:m.start() + 1]))
        if judged and before.endswith("||"):
            result = "⚠️ exit 0, but `||` before it: it ran only if the command before failed"
        elif hidden:
            result = f"⚠️ exit 0, but {hidden} after it hides its exit status"
        else:
            result = _status(tc)
        rows.append(f"| {_cell(by)} | {_code('; '.join(segments) or _command(tc))} | {_cell(result, 200)} "
                    f"| {_cell(_tail(_EXIT_CODE.sub('', tc.output_summary).strip(), 90), 90)} |")
    if not rows:
        return ""
    return "| By | Command | Result | Output (end) |\n|---|---|---|---|\n" + "\n".join(rows)


def _git_segments(command: str) -> list[str]:
    """The git commands as written, each cut at the next separator or redirection, found where a command starts
    and outside quotes and heredoc bodies.

    Quotes and heredoc bodies are blanked with same-length filler (a heredoc keeps its `<<`, where the segment is
    cut), so every position still holds and the text shown is sliced from the command itself. A quoted string that
    spans lines, like `-m "$(cat <<'EOF' … EOF)"`, stays one segment.
    """
    fill = lambda m, keep=0: m.group(0)[:keep] + "x" * (len(m.group(0)) - keep)  # noqa: E731
    blanked = _QUOTED.sub(fill, _HEREDOC.sub(lambda m: fill(m, 2), command))
    segments = []
    for m in _GIT_SEGMENT.finditer(blanked):
        start = m.start(1)
        cut = re.search(r"\s+\d?[<>]", blanked[start:m.end()])
        end = start + cut.start() if cut else m.end()
        segments.append(command[start:end].strip())
    return segments


def _edited_files(calls: list) -> tuple[dict[str, dict], list[tuple[str, ToolCall, str]]]:
    """Files edited with Edit / Write tools, and edits whose result never arrived."""
    files: dict[str, dict] = {}
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
    return files, pending


def _files_table(calls: list) -> str:
    files, pending = _edited_files(calls)
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
            f"{_code(_tail(path, 120), in_table=False)} ({tc.tool}, {_cell(by, in_table=False)})"
            for by, tc, path in pending))
    parts.append(_shell_writes(calls))
    return "\n\n".join(parts)


def _shell_write_rows(calls: list) -> list[tuple[str, ToolCall, list[str]]]:
    """Shell commands whose text shows a file write. What they changed is not known, only that they could."""
    rows = []
    for by, tc in calls:
        if tc.tool != "Bash" or tc.status == "PENDING":
            continue
        code = _shell_code(_unwrap_shell_c(_command(tc)))
        reasons = [name for name, pattern in _SHELL_WRITES if pattern.search(code)]
        if reasons:
            rows.append((by, tc, reasons))
    return rows


def _shell_writes(calls: list) -> str:
    rows = [
        f"- **{_cell(by, in_table=False)}** · {_code(_command(tc), 160, in_table=False)} ({', '.join(reasons)})"
        + (" ❌ failed" if tc.is_error else "")
        for by, tc, reasons in _shell_write_rows(calls)
    ]
    if not rows:
        return "No shell command that writes files (`>`, `tee`, `sed -i`, `mv`, `cp`, `rm`…) was seen."
    # Collapsed: the count is the fact; the list is long in big sessions and only says "could have".
    summary = f"{_count(len(rows), 'shell command')} may have changed files (not verified)"
    return f"<details>\n<summary>{summary}</summary>\n\n" + "\n".join(rows) + "\n\n</details>"


def _errors_list(calls: list) -> str:
    errors = [(by, tc) for by, tc in calls if tc.is_error]
    if not errors:
        return "**No tool call failed.**"
    return "\n".join(
        f"- **{_cell(by, in_table=False)}** · {tc.tool} {_code(_subject(tc), 100, in_table=False)} "
        f"→ {_cell(_EXIT_CODE.sub('', tc.output_summary), 160, in_table=False)}"
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
    return f"❌ {_exit_text(tc)}"


def _exit_text(tc: ToolCall) -> str:
    m = _EXIT_CODE.match(tc.output_summary)
    return f"exit {m.group(1)}" if m else "failed"


def _command(tc: ToolCall) -> str:
    return str(tc.input.get("command", ""))


def _subject(tc: ToolCall) -> str:
    for key in ("command", "file_path", "notebook_path", "url", "pattern", "description"):
        if tc.input.get(key):
            return str(tc.input[key])
    return ""


def _tail(text: str, limit: int = 90) -> str:
    return text if len(text) <= limit else "…" + text[-(limit - 1):]


def _cell(text: str, limit: int = 120, in_table: bool = True) -> str:
    text = re.sub(r"\s+", " ", _CONTROL.sub("", str(text))).strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    if not in_table:
        return text  # outside a table `\|` is not an escape: the backslash would show
    # Every pipe, even one already behind a backslash: cmark-gfm reads `\\|` as an escaped pipe too, and it is
    # the only way `\|` in a command renders as it ran.
    return text.replace("|", "\\|")


def _code(text: str, limit: int = 120, in_table: bool = True) -> str:
    cell = _cell(text, limit, in_table)
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
