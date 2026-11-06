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
"""

import json
import subprocess
import sys


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if data.get("stop_hook_active"):
        return 0  # already continuing because of a Stop hook: do not report twice

    transcript = data.get("transcript_path")
    if not transcript:
        _say("OSOP · no receipt: the Stop hook received no transcript_path")
        return 0

    try:
        run = subprocess.run([sys.executable, "-m", "osop", "log", str(transcript), "--brief"],
                             capture_output=True, timeout=150)
    except subprocess.TimeoutExpired:
        _say("OSOP · no receipt: osop log timed out")
        return 0

    line = run.stdout.decode("utf-8", "replace").strip()
    if run.returncode == 0 and line.startswith("OSOP"):
        _say(line)
        return 0
    reason = next((ln.strip() for ln in (run.stdout + run.stderr).decode("utf-8", "replace").splitlines()
                   if ln.strip()), f"osop log exited {run.returncode}")
    _say(f"OSOP · no receipt: {reason[:200]}")
    return 0


def _say(message: str) -> None:
    # ASCII-only JSON: a cp950 console cannot print "·" or "⚠️", and Claude Code decodes the escapes.
    sys.stdout.write(json.dumps({"systemMessage": message}) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
