"""hooks/osop-stop.py — a Claude Code Stop hook that writes the receipt and shows its one-line summary.

The package does not install it: wiring it into settings.json is the owner's decision.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).parent.parent / "hooks" / "osop-stop.py"
MINIMAL_SESSION = Path(__file__).parent / "fixtures" / "minimal_session.jsonl"


def _run_hook(payload: dict, home: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))  # Path.home() on POSIX and on Windows
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload).encode("utf-8"),
                          capture_output=True, env=env, timeout=120)


def test_stop_hook_shows_the_brief_and_writes_the_receipt(tmp_path):
    result = _run_hook({"hook_event_name": "Stop", "stop_hook_active": False,
                        "transcript_path": str(MINIMAL_SESSION), "cwd": "/work/demo"}, tmp_path)
    assert result.returncode == 0, result.stderr
    message = json.loads(result.stdout.decode("utf-8"))["systemMessage"]
    assert message.startswith("OSOP") and "1 passed" in message
    assert list((tmp_path / ".osop" / "receipts" / "demo").glob("*.receipt.md"))


def test_stop_hook_still_reports_after_another_hook_made_claude_continue(tmp_path):
    # stop_hook_active means Claude kept working after an earlier Stop: that work must reach the receipt.
    result = _run_hook({"hook_event_name": "Stop", "stop_hook_active": True,
                        "transcript_path": str(MINIMAL_SESSION)}, tmp_path)
    assert result.returncode == 0
    assert json.loads(result.stdout.decode("utf-8"))["systemMessage"].startswith("OSOP ·")
    assert list((tmp_path / ".osop" / "receipts" / "demo").glob("*.receipt.md"))


def test_stop_hook_reads_utf8_input_whatever_the_console_codec(tmp_path):
    session = tmp_path / "專案" / "s.jsonl"
    session.parent.mkdir()
    session.write_bytes(MINIMAL_SESSION.read_bytes())
    payload = json.dumps({"hook_event_name": "Stop", "stop_hook_active": False,
                          "transcript_path": str(session)}, ensure_ascii=False).encode("utf-8")
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path), PYTHONIOENCODING="cp950")
    result = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, env=env, timeout=120)
    assert result.returncode == 0, result.stderr
    message = json.loads(result.stdout.decode("utf-8"))["systemMessage"]
    assert message.startswith("OSOP ·") and "no receipt" not in message


def test_stop_hook_reports_a_failed_receipt_instead_of_staying_silent(tmp_path):
    result = _run_hook({"hook_event_name": "Stop", "stop_hook_active": False,
                        "transcript_path": str(tmp_path / "missing.jsonl")}, tmp_path)
    assert result.returncode == 0
    message = json.loads(result.stdout.decode("utf-8"))["systemMessage"]
    assert message.startswith("OSOP") and "no receipt" in message
