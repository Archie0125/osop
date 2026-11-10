"""hooks/osop-stop.py — a Claude Code Stop hook that writes the receipt and shows its one-line summary.

The package does not install it: wiring it into settings.json is the owner's decision.
"""

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import time
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
    result = _run_hook(
        {"hook_event_name": "Stop", "stop_hook_active": False, "transcript_path": str(CONTRADICTED)}, tmp_path)
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


def test_stop_hook_survives_a_lone_surrogate_in_the_message_and_a_bad_size_limit(tmp_path):
    result = _run_hook({"hook_event_name": "Stop", "transcript_path": str(MINIMAL_SESSION),
                        "last_assistant_message": "All tests pass \ud83d"}, tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.decode("utf-8"))["systemMessage"].startswith("OSOP · claims:")
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path), OSOP_MAX_TRANSCRIPT_MB="abc")
    payload = json.dumps({"hook_event_name": "Stop", "transcript_path": str(MINIMAL_SESSION)}).encode("utf-8")
    result = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, env=env, timeout=120)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.decode("utf-8"))["systemMessage"].startswith("OSOP ·")


# --- brief.log: what every Stop leaves behind, since the receipt itself is rewritten each time -------------


def test_stop_hook_appends_the_brief_and_the_claim_rows_to_brief_log(tmp_path):
    payload = {"hook_event_name": "Stop", "session_id": "sess-1", "transcript_path": str(CONTRADICTED),
               "cwd": "/work/demo", "last_assistant_message": "全套測試綠，已 push 到 origin/main。"}
    for _ in range(2):
        result = _run_hook(payload, tmp_path)
        assert result.returncode == 0, result.stderr
    log = (tmp_path / ".osop" / "brief.log").read_text(encoding="utf-8")
    entries = [line for line in log.splitlines() if not line.startswith("\t")]
    rows = [line for line in log.splitlines() if line.startswith("\t")]
    assert len(entries) == 2 and len(rows) == 4, log  # two Stops, two claim rows each, appended
    stamp, project, session, brief = entries[0].split("\t")
    assert len(stamp) == 25 and stamp[10] == "T" and stamp[19] in "+-", stamp  # local time with its UTC offset
    assert project == "demo" and session == "sess-1"
    assert brief.startswith("OSOP · claims: 1 contradicted ❌ (tests pass), 1 without evidence ❓ (pushed)")
    assert rows[0].startswith("\t| tests pass: ") and "❌ contradicted" in rows[0], rows
    assert rows[1].startswith("\t| pushed: ") and "❓ no evidence" in rows[1], rows


def test_brief_log_holds_only_the_brief_when_there_are_no_claims(tmp_path):
    result = _run_hook({"hook_event_name": "Stop", "transcript_path": str(MINIMAL_SESSION),
                        "last_assistant_message": "Listed the files."}, tmp_path)
    assert result.returncode == 0, result.stderr
    lines = (tmp_path / ".osop" / "brief.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, lines
    assert lines[0].split("\t")[2] == "minimal_session"  # no session_id in the input: the transcript's name
    assert "\tOSOP · claims: none found" in lines[0]


def test_brief_log_path_can_be_set_and_a_write_failure_is_said_not_hidden(tmp_path):
    custom = tmp_path / "logs" / "osop-brief.tsv"
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path), OSOP_BRIEF_LOG=str(custom))
    payload = json.dumps({"hook_event_name": "Stop", "transcript_path": str(MINIMAL_SESSION),
                          "last_assistant_message": "Listed the files."}).encode("utf-8")
    result = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, env=env, timeout=120)
    assert result.returncode == 0, result.stderr
    assert custom.read_text(encoding="utf-8").count("\n") == 1
    assert not (tmp_path / ".osop" / "brief.log").exists()
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    env["OSOP_BRIEF_LOG"] = str(blocker / "brief.log")
    result = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, env=env, timeout=120)
    assert result.returncode == 0, result.stderr
    message = json.loads(result.stdout.decode("utf-8"))["systemMessage"]
    assert message.startswith("OSOP · claims:") and "brief.log not written" in message, message


def _hook_module():
    spec = importlib.util.spec_from_file_location("osop_stop_hook", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_brief_log_entry_keeps_four_fields_and_one_line_per_row_whatever_the_text_holds(tmp_path, monkeypatch):
    # A later reader splits entry lines on tabs and treats a leading tab as a claim row: no other tab or
    # line break may come from the brief or a row (Codex review of PR #16).
    monkeypatch.setenv("OSOP_BRIEF_LOG", str(tmp_path / "brief.log"))
    receipt = tmp_path / "demo" / "x.receipt.md"
    receipt.parent.mkdir()
    receipt.write_text("# Receipt\n\n## Claims\n| Claim | Verdict | Evidence |\n|---|---|---|\n"
                       "| tests pass: “a\tb” | ❓ no evidence | none |\n\n## Goal\n", encoding="utf-8")
    out = _hook_module()._logged(f"OSOP · claims:\tnone\nfound · receipt: {receipt}", {"session_id": "s"}, "t.jsonl")
    assert "not written" not in out
    lines = (tmp_path / "brief.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2, lines
    assert lines[0].count("\t") == 3 and lines[0].split("\t")[1:3] == ["demo", "s"], lines[0]
    assert lines[1].startswith("\t| tests pass:") and lines[1].count("\t") == 1, lines[1]


def test_brief_log_append_waits_for_another_writer_holding_the_lock(tmp_path):
    # Windows' append mode seeks and writes in two steps, so two Stops at once could overwrite each other
    # (CPython issue 86772). The append takes a lock; a holder that writes late must still come first.
    log, ready = tmp_path / "brief.log", tmp_path / "ready"
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import importlib.util, time
        from pathlib import Path
        spec = importlib.util.spec_from_file_location("h", r"{HOOK}")
        h = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(h)
        with Path(r"{log}").open("a", encoding="utf-8") as fh:
            h._lock(fh)
            Path(r"{ready}").write_text("1")
            time.sleep(1.5)
            fh.seek(0, 2)
            fh.write("first\\n")
            fh.flush()
            h._unlock(fh)
        """)])
    for _ in range(200):
        if ready.exists():
            break
        time.sleep(0.05)
    assert ready.exists(), "the lock holder did not start"
    _hook_module()._append(log, "second\n")
    assert holder.wait(timeout=30) == 0
    assert log.read_text(encoding="utf-8") == "first\nsecond\n"
