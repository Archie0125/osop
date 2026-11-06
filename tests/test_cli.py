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
    def test_adds_claude_md_section_without_a_sessions_dir(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert not (tmp_path / "sessions").exists()  # receipts live under ~/.osop, not in the repo
        text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "## OSOP" in text and "~/.osop/receipts/" in text and "sessions/" not in text

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


class TestReceiptLocation:
    def test_default_is_home_osop_receipts_by_project(self, runner, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["log", str(MINIMAL_SESSION), "-d", "demo"])
        assert result.exit_code == 0, result.output
        # the fixture's cwd is /work/demo → project "demo"
        assert (tmp_path / "home" / ".osop" / "receipts" / "demo" / "2026-10-23-demo.receipt.md").is_file()
        assert not (tmp_path / "sessions").exists()

    def test_out_dir_still_overrides(self, runner, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        result = runner.invoke(cli, ["log", str(MINIMAL_SESSION), "-o", str(tmp_path / "out"), "-d", "demo"])
        assert result.exit_code == 0
        assert (tmp_path / "out" / "2026-10-23-demo.receipt.md").is_file()
        assert not (tmp_path / "home").exists()


class TestBrief:
    def test_brief_prints_one_line_and_writes_the_receipt(self, runner, tmp_path):
        result = runner.invoke(cli, ["log", str(MINIMAL_SESSION), "-o", str(tmp_path), "-d", "demo", "--brief"])
        assert result.exit_code == 0, result.output
        line = result.stdout_bytes.decode("utf-8")
        assert line.count("\n") == 1 and line.startswith("OSOP")
        assert "1 passed" in line
        assert str(tmp_path / "2026-10-23-demo.receipt.md") in line
        assert (tmp_path / "2026-10-23-demo.receipt.md").is_file()

    def test_brief_and_stdout_are_exclusive(self, runner):
        result = runner.invoke(cli, ["log", str(MINIMAL_SESSION), "--stdout", "--brief"])
        assert result.exit_code != 0


class TestReceiptsDir:
    def test_project_is_the_last_segment_of_a_windows_or_posix_cwd(self, tmp_path, monkeypatch):
        from osop.cli.main import _receipts_dir
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        for cwd in (r"C:\work\demo", "C:\\work\\demo\\", "/work/demo", "/work/demo/"):
            assert _receipts_dir({"cwd": cwd}, Path("x/s.jsonl")).name == "demo", cwd
        assert _receipts_dir({"cwd": ""}, Path("slug-dir/s.jsonl")).name == "slug-dir"
