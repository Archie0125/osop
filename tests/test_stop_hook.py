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
CONTRADICTED = Path(__file__).parent / "fixtures" / "claims_contradicted.jsonl"


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
    missing = tmp_path / ("a-directory-name-long-enough-to-wrap-an-80-column-console" * 2) / "missing.jsonl"
    result = _run_hook({"hook_event_name": "Stop", "stop_hook_active": False,
                        "transcript_path": str(missing)}, tmp_path)
    assert result.returncode == 0
    message = json.loads(result.stdout.decode("utf-8"))["systemMessage"]
    assert message.startswith("OSOP") and "no receipt" in message
    assert "missing.jsonl" in message  # the path is not lost to console wrapping


def test_stop_hook_checks_the_claims_in_the_message_claude_code_hands_it(tmp_path):
    # The transcript's own final text says the fix is done; the hook's copy is what the user actually saw.
    payload = {"hook_event_name": "Stop", "stop_hook_active": False, "transcript_path": str(CONTRADICTED),
               "last_assistant_message": "全套測試綠，已 push 到 origin/main。"}
    result = _run_hook(payload, tmp_path)
    assert result.returncode == 0, result.stderr
    message = json.loads(result.stdout.decode("utf-8"))["systemMessage"]
    assert "claims: 1 contradicted ❌ (tests pass), 1 without evidence ❓ (pushed)" in message
    receipt = next((tmp_path / ".osop" / "receipts" / "demo").glob("*.receipt.md")).read_text(encoding="utf-8")
    assert "全套測試綠，已 push 到 origin/main" in receipt and "committed" not in receipt.split("## Goal")[0]


def test_stop_hook_without_the_message_falls_back_to_the_transcript(tmp_path):
    result = _run_hook({"hook_event_name": "Stop", "stop_hook_active": False, "transcript_path": str(CONTRADICTED)}, tmp_path)
    message = json.loads(result.stdout.decode("utf-8"))["systemMessage"]
    assert "claims: 1 contradicted ❌ (tests pass), 1 without evidence ❓ (committed)" in message


def test_stop_hook_skips_a_transcript_over_the_size_limit(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path), OSOP_MAX_TRANSCRIPT_MB="0.0001")
    payload = json.dumps({"hook_event_name": "Stop", "transcript_path": str(MINIMAL_SESSION)}).encode("utf-8")
    result = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, env=env, timeout=120)
    assert result.returncode == 0
    message = json.loads(result.stdout.decode("utf-8"))["systemMessage"]
    assert message.startswith("OSOP · receipt skipped") and "osop log" in message and "minimal_session.jsonl" in message
    assert not (tmp_path / ".osop").exists()
