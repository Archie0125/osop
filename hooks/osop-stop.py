#!/usr/bin/env python3
"""Claude Code Stop hook: write the session's receipt and show its one-line summary.

The package does not install this. Wiring it in changes settings.json, so that is the
owner's call. To use it, add under "hooks" → "Stop" (with a python that has osop installed):

    {"hooks": [{"type": "command", "command": "python /path/to/hooks/osop-stop.py", "timeout": 180}]}

Input (stdin JSON, per the Claude Code hooks reference): transcript_path, stop_hook_active, cwd, …
Output: {"systemMessage": "OSOP · tests: … · receipt: …"}, or "OSOP · no receipt: …" when the
receipt could not be written. Silence would hide the failure.

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
    limit_mb = float(os.environ.get("OSOP_MAX_TRANSCRIPT_MB") or 200)
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
        stdin = message.encode("utf-8")
    try:
        # A wide console: an error message that wraps at 80 columns would lose the path it names.
        run = subprocess.run(args, input=stdin, capture_output=True, timeout=150,
                             env={**os.environ, "COLUMNS": "4000"})
    except subprocess.TimeoutExpired:
        _say("OSOP · no receipt: osop log timed out")
        return 0

    line = run.stdout.decode("utf-8", "replace").strip()
    if run.returncode == 0 and line.startswith("OSOP"):
        _say(line)
        return 0
    reason = next((ln.strip() for ln in (run.stdout + run.stderr).decode("utf-8", "replace").splitlines()
                   if ln.strip()), f"osop log exited {run.returncode}")
    if len(reason) > 300:
        reason = reason[:100] + " … " + reason[-180:]  # keep the end: that is where a path's file name is
    _say(f"OSOP · no receipt: {reason}")
    return 0


def _say(message: str) -> None:
    # ASCII-only JSON: a cp950 console cannot print "·" or "⚠️", and Claude Code decodes the escapes.
    sys.stdout.write(json.dumps({"systemMessage": message}) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
