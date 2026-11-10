#!/usr/bin/env python3
"""Claude Code Stop hook: write the session's receipt and show its one-line summary.

The package does not install this. Wiring it in changes settings.json, so that is the
owner's call. To use it, add under "hooks" → "Stop" (with a python that has osop installed):

    {"hooks": [{"type": "command", "command": "python /path/to/hooks/osop-stop.py", "timeout": 180}]}

Input (stdin JSON, per the Claude Code hooks reference): transcript_path, stop_hook_active, cwd, …
Output: {"systemMessage": "OSOP · tests: … · receipt: …"}, or "OSOP · no receipt: …" when the
receipt could not be written. Silence would hide the failure.

Every Stop also appends the brief and the receipt's claim rows to ~/.osop/brief.log (OSOP_BRIEF_LOG
overrides the path): the receipt file is rewritten at each Stop, so this log is the only record of
what earlier turns claimed and how each claim was judged. A failed append is said in the brief.

Claude Code writes the transcript file asynchronously, so the turn that just ended may not be in
it yet when the hook runs. The receipt is still built from the file on disk, never from memory.

It runs on every Stop, including one with stop_hook_active (Claude continued because another
hook blocked an earlier stop): that continuation is work the receipt must cover. This hook never
blocks, so there is no loop to guard against.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def main() -> int:
    try:
        # Claude Code sends UTF-8; the console codec (cp950 on a zh-TW Windows) may not decode it.
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}

    transcript = data.get("transcript_path")
    if not transcript:
        _say("OSOP · no receipt: the Stop hook received no transcript_path")
        return 0

    # Parsing runs at about 12 MB/s and every Stop waits for it: a giant transcript is skipped, and said so.
    try:
        limit_mb = float(os.environ.get("OSOP_MAX_TRANSCRIPT_MB") or 200)
    except ValueError:
        limit_mb = 200.0
    try:
        size_mb = os.path.getsize(transcript) / 1e6
    except OSError:
        size_mb = 0.0  # a missing file: let osop log report it
    if size_mb > limit_mb:
        _say(f"OSOP · receipt skipped: transcript is {size_mb:.0f} MB, over OSOP_MAX_TRANSCRIPT_MB={limit_mb:.0f}; "
             f"run `osop log {transcript}` to build it")
        return 0

    args = [sys.executable, "-m", "osop", "log", str(transcript), "--brief"]
    stdin = None
    message = data.get("last_assistant_message")
    if isinstance(message, str):
        # The hook's copy of the final message is complete; the transcript file may still lag behind it.
        args += ["--last-message", "-"]
        stdin = message.encode("utf-8", "surrogatepass")  # a lone surrogate must not crash the hook
    try:
        # A wide console: an error message that wraps at 80 columns would lose the path it names.
        run = subprocess.run(args, input=stdin, capture_output=True, timeout=150,
                             env={**os.environ, "COLUMNS": "4000"})
    except subprocess.TimeoutExpired:
        _say("OSOP · no receipt: osop log timed out")
        return 0

    line = run.stdout.decode("utf-8", "replace").strip()
    if run.returncode == 0 and line.startswith("OSOP"):
        _say(_logged(line, data, str(transcript)))
        return 0
    reason = next((ln.strip() for ln in (run.stdout + run.stderr).decode("utf-8", "replace").splitlines()
                   if ln.strip()), f"osop log exited {run.returncode}")
    if len(reason) > 300:
        reason = reason[:100] + " … " + reason[-180:]  # keep the end: that is where a path's file name is
    _say(f"OSOP · no receipt: {reason}")
    return 0


def _logged(brief: str, data: dict, transcript: str) -> str:
    """Append the brief and the receipt's claim rows to the brief log; the brief back, with a note if that failed.

    One entry per Stop: `<local time with offset>\\t<project>\\t<session id>\\t<brief>`, then one tab-indented
    line per claim row of the receipt just written. A reader splits entry lines on tabs and knows a row by its
    leading tab, so no other tab or line break gets in. The write holds a lock: Stops from several windows
    must not overwrite each other.
    """
    path = Path(os.environ.get("OSOP_BRIEF_LOG") or Path.home() / ".osop" / "brief.log")
    try:
        receipt = Path(brief.rsplit(" · receipt: ", 1)[1]) if " · receipt: " in brief else None
        rows = _claim_rows(receipt) if receipt else []
        session = data.get("session_id")
        if not isinstance(session, str) or not session:
            session = Path(transcript).stem
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        entry = f"{stamp}\t{receipt.parent.name if receipt else ''}\t{session}\t{_one_line(brief)}\n"
        entry += "".join(f"\t{_one_line(row)}\n" for row in rows)
        _append(path, entry)
    except (OSError, ValueError) as e:
        return f"{brief} · brief.log not written: {e}"
    return brief


def _one_line(text: str) -> str:
    return text.replace("\t", " ").replace("\r", " ").replace("\n", " ")


def _append(path: Path, entry: str) -> None:
    """Append under a lock. Windows' append mode seeks to the end and writes in two steps (CPython issue 86772),
    so two processes appending at once could overwrite each other; the lock serialises them."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        _lock(fh)
        try:
            fh.seek(0, os.SEEK_END)
            fh.write(entry)
            fh.flush()
        finally:
            _unlock(fh)


if sys.platform == "win32":
    import msvcrt

    def _lock(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)  # byte 0 of the file; retries for about 10 s, then OSError

    def _unlock(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

    def _unlock(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _claim_rows(receipt: Path) -> list[str]:
    """The data rows of the receipt's Claims table (no header), or none."""
    text = receipt.read_text(encoding="utf-8")
    if "## Claims\n" not in text:
        return []
    section = text.split("## Claims\n", 1)[1].split("\n## ", 1)[0]
    return [ln for ln in section.splitlines() if ln.startswith("| ") and not ln.startswith("| Claim ")]


def _say(message: str) -> None:
    # ASCII-only JSON: a cp950 console cannot print "·" or "⚠️", and Claude Code decodes the escapes.
    sys.stdout.write(json.dumps({"systemMessage": message}) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
