"""Tests for osop.cli.main — CLI commands via Click's CliRunner."""
import pytest
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
    def test_stdout_prints_the_receipt(self, runner):
        result = runner.invoke(cli, ["log", str(MINIMAL_SESSION), "--stdout"])
        assert result.exit_code == 0
        assert result.output.startswith("# Receipt: run the tests")
        assert "python -m pytest -q" in result.output

    def test_writes_only_the_receipt(self, runner, tmp_path):
        result = runner.invoke(cli, ["log", str(MINIMAL_SESSION), "-o", str(tmp_path), "-d", "demo"])
        assert result.exit_code == 0
        assert [p.name for p in tmp_path.iterdir()] == ["2026-10-23-demo.receipt.md"]

    def test_stdout_survives_non_utf8_console(self, tmp_path):
        # Windows consoles in zh-TW default to cp950, which cannot encode ✓.
        session = tmp_path / "session.jsonl"
        session.write_text(MINIMAL_SESSION.read_text(encoding="utf-8").replace(
            "3 passed in 0.12s", "✓ 3 passed，全部通過"), encoding="utf-8")
        result = CliRunner(charset="cp950").invoke(cli, ["log", str(session), "--stdout"])
        assert result.exit_code == 0, result.exception
        assert "✓ 3 passed，全部通過" in result.stdout_bytes.decode("utf-8")

    def test_stdout_bytes_match_file_bytes(self, runner, tmp_path):
        printed = runner.invoke(cli, ["log", str(MINIMAL_SESSION), "--stdout", "-d", "same"]).stdout_bytes
        runner.invoke(cli, ["log", str(MINIMAL_SESSION), "-o", str(tmp_path), "-d", "same"])
        assert printed == (tmp_path / "2026-10-23-same.receipt.md").read_bytes()

    def test_missing_transcript_fails(self, runner):
        result = runner.invoke(cli, ["log", "no-such-session-id"])
        assert result.exit_code != 0
