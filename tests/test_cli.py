"""Tests for osop.cli.main — CLI commands via Click's CliRunner."""
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

    def test_writes_only_osoplog(self, runner, tmp_path):
        result = runner.invoke(cli, ["log", str(MINIMAL_SESSION), "-o", str(tmp_path), "-d", "demo"])
        assert result.exit_code == 0
        assert [p.name for p in tmp_path.iterdir()] == ["2026-10-23-demo.osoplog.yaml"]

    def test_missing_transcript_fails(self, runner):
        result = runner.invoke(cli, ["log", "no-such-session-id"])
        assert result.exit_code != 0
