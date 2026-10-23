"""Tests for osop.cli.main — CLI commands via Click's CliRunner."""
import re

import pytest
import yaml
from pathlib import Path
from click.testing import CliRunner
from osop.cli.main import cli


FIXTURES = Path(__file__).parent / "fixtures"
MINIMAL_SESSION = FIXTURES / "minimal_session.jsonl"


@pytest.fixture
def runner():
    return CliRunner()


class TestCLIHelp:
    def test_help_lists_log_and_init(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "log" in result.output
        assert "init" in result.output

    @pytest.mark.parametrize("removed", ["replay", "record", "validate", "diff", "optimize", "view"])
    def test_removed_commands_are_gone(self, runner, removed):
        result = runner.invoke(cli, [removed])
        assert result.exit_code != 0


class TestInitCommand:
    def test_creates_sessions_and_claude_md(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert (tmp_path / "sessions").is_dir()
        assert "## OSOP" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

    def test_second_run_does_not_duplicate_section(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"])
        runner.invoke(cli, ["init"])
        assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8").count("## OSOP") == 1


class TestLogCommand:
    def test_stdout_contains_real_tool_call(self, runner):
        result = runner.invoke(cli, ["log", str(MINIMAL_SESSION), "--stdout"])
        assert result.exit_code == 0
        doc = yaml.safe_load(result.output)
        calls = [tc for rec in doc["node_records"] for tc in rec.get("tool_calls", [])]
        assert [tc["input"]["command"] for tc in calls] == ["python -m pytest -q"]
        assert calls[0]["output"] == "3 passed in 0.12s"
        assert calls[0]["is_error"] is False

    def test_writes_no_osop_workflow_file(self, runner, tmp_path):
        result = runner.invoke(cli, ["log", str(MINIMAL_SESSION), "-o", str(tmp_path), "-d", "demo"])
        assert result.exit_code == 0
        assert not list(tmp_path.glob("*.osop.yaml"))

    def test_stdout_survives_non_utf8_console(self, tmp_path):
        # Windows consoles in zh-TW default to cp950, which cannot encode ✓.
        session = tmp_path / "session.jsonl"
        session.write_text(MINIMAL_SESSION.read_text(encoding="utf-8").replace(
            "3 passed in 0.12s", "✓ 3 passed，全部通過"), encoding="utf-8")
        result = CliRunner(charset="cp950").invoke(cli, ["log", str(session), "--stdout"])
        assert result.exit_code == 0, result.exception
        doc = yaml.safe_load(result.stdout_bytes.decode("utf-8"))
        calls = [tc for rec in doc["node_records"] for tc in rec.get("tool_calls", [])]
        assert calls[0]["output"] == "✓ 3 passed，全部通過"

    def test_stdout_bytes_match_file_bytes(self, runner, tmp_path):
        def without_run_id(data: bytes) -> bytes:
            return re.sub(rb"run_id: \S+", b"run_id: X", data)

        printed = runner.invoke(cli, ["log", str(MINIMAL_SESSION), "--stdout", "-d", "same"]).stdout_bytes
        runner.invoke(cli, ["log", str(MINIMAL_SESSION), "-o", str(tmp_path), "-d", "same"])
        written = (tmp_path / "2026-10-23-same.osoplog.yaml").read_bytes()
        assert without_run_id(printed) == without_run_id(written)

    def test_missing_transcript_fails(self, runner):
        result = runner.invoke(cli, ["log", "no-such-session-id"])
        assert result.exit_code != 0
