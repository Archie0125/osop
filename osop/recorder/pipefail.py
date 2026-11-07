"""`set -o pipefail;` in front of a command that pipes a test, lint or build runner.

`pytest 2>&1 | tail -20` exits with tail's status: a failing run looks like a success to the agent, and the
transcript keeps no exit status for the receipt. With pipefail the pipeline fails when the runner fails.
hooks/osop-pipefail.py applies this as a Claude Code PreToolUse hook.
"""

import re

from osop.recorder.receipt import _BUILD_RUNNER, _LINT_RUNNER, _PIPEFAIL, _RUN_PREFIX, _TEST_RUNNER, _mask

_RUNNER = re.compile(_RUN_PREFIX + rf"(?:{_TEST_RUNNER}|{_LINT_RUNNER}|{_BUILD_RUNNER})\b")
_PIPE = re.compile(r"(?<!\|)\|(?!\|)")
# Where the runner's own command ends: `;`, `&&`, `||`, a lone `&`, or a newline that does not continue a pipe.
_END = re.compile(r";|&&|\|\||(?<![&>|])&(?![&>])|\n")
_LEADING = re.compile(r"^[\s&]*(?:\d*>&?\d*\s*)*")  # `|& tail`
_GREP_STOPS = re.compile(r"(?:^|\s)-[A-Za-z]*[qlLm]|(?:^|\s)--(?:quiet|silent|max-count|files-with(?:out)?-match)")
_SED_QUITS = re.compile(r"(?<![A-Za-z_-])[qQ](?![A-Za-z_])")
_SCRIPT_EXITS = re.compile(r"\b(?:exit|last|break)\b")


def with_pipefail(command: str) -> str | None:
    """The command behind `set -o pipefail; `, or None when it should run as written.

    None when no runner is piped, when the command already sets or unsets pipefail (the agent is handling it),
    and when a runner is piped into a reader that stops before the end of its input (head, read, a shell
    loop, grep -q/-m/-l, sed … q, awk … exit): the runner then dies of a broken pipe (Python exits 120 on
    Windows, node with EPIPE), and pipefail would turn a passing run into a failed one.
    """
    masked = _mask(command)
    if _PIPEFAIL.search(masked):
        return None
    if not any(_PIPE.search(masked, r.end(), _statement_end(masked, r.end())) for r in _RUNNER.finditer(masked)):
        return None
    # The prefix governs every pipeline in the command, not only the runner's, so every one must be safe.
    for start, end in _statements(masked):
        cuts = [pipe.end() for pipe in _PIPE.finditer(masked, start, end)]
        for a, b in zip(cuts, cuts[1:] + [end], strict=False):
            if _stops_early(masked[a:b], command[a:b]):
                return None
    return f"set -o pipefail; {command}"


def _statement_end(masked: str, pos: int) -> int:
    for m in _END.finditer(masked, pos):
        if m.group(0) == "\n" and masked[pos:m.start()].rstrip(" \t").endswith(("|", "\\")):
            continue  # `pytest |` + newline + `tail`, or a `\` continuation
        return m.start()
    return len(masked)


def _statements(masked: str) -> list[tuple[int, int]]:
    """(start, end) of each statement: text between separators, a continued pipe kept whole."""
    spans = []
    pos = 0
    while True:
        end = _statement_end(masked, pos)
        spans.append((pos, end))
        separator = _END.match(masked, end)
        if separator is None:
            return spans
        pos = separator.end()


def _stops_early(masked_segment: str, raw_segment: str) -> bool:
    """Whether this pipe segment may close its input before the runner is done writing."""
    body = _LEADING.sub("", masked_segment, count=1)
    words = body.split()
    tool = re.sub(r".*/", "", words[0]) if words else ""
    if tool in ("head", "read", "while", "until"):
        return True  # a loop's body is cut off at its `;`, so its `break` cannot be seen: every loop counts
    if tool in ("grep", "egrep", "fgrep", "rg", "ag"):
        return bool(_GREP_STOPS.search(body))
    if tool in ("sed", "gsed"):
        return bool(_SED_QUITS.search(raw_segment))  # the script is quoted, so the raw text is needed
    if tool in ("awk", "gawk", "mawk", "nawk", "perl"):
        return bool(_SCRIPT_EXITS.search(raw_segment))
    return False
