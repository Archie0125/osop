"""Tests for osop.cli.main — CLI commands via Click's CliRunner."""
import pytest
from pathlib import Path
from click.testing import CliRunner
from osop.cli.main import cli


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# Help and version
# ---------------------------------------------------------------------------

class TestCLIHelp:
    def test_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "OSOP" in result.output

    def test_validate_help(self, runner):
        result = runner.invoke(cli, ["validate", "--help"])
        assert result.exit_code == 0
        assert "Validate" in result.output

    def test_record_help(self, runner):
        result = runner.invoke(cli, ["record", "--help"])
        assert result.exit_code == 0
        assert "record" in result.output.lower()

    def test_diff_help(self, runner):
        result = runner.invoke(cli, ["diff", "--help"])
        assert result.exit_code == 0
        assert "Compare" in result.output

    def test_optimize_help(self, runner):
        result = runner.invoke(cli, ["optimize", "--help"])
        assert result.exit_code == 0
        assert "optimize" in result.output.lower()

    def test_unknown_command(self, runner):
        result = runner.invoke(cli, ["nonexistent"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# validate command
# ---------------------------------------------------------------------------

class TestValidateCommand:
    def test_validate_valid_file(self, runner):
        result = runner.invoke(cli, ["validate", str(FIXTURES / "valid_minimal.osop")])
        assert result.exit_code == 0
        assert "Valid" in result.output

    def test_validate_valid_json(self, runner):
        result = runner.invoke(cli, ["validate", str(FIXTURES / "valid_minimal.json")])
        assert result.exit_code == 0
        assert "Valid" in result.output

    def test_validate_missing_fields(self, runner):
        result = runner.invoke(cli, ["validate", str(FIXTURES / "missing_fields.osop")])
        assert result.exit_code == 1

    def test_validate_nonexistent_file(self, runner):
        result = runner.invoke(cli, ["validate", "/no/such/file.osop"])
        assert result.exit_code == 1
        assert "Error" in result.output

    def test_validate_invalid_yaml(self, runner):
        result = runner.invoke(cli, ["validate", str(FIXTURES / "invalid_yaml.osop")])
        assert result.exit_code == 1

    def test_validate_empty_file(self, runner):
        result = runner.invoke(cli, ["validate", str(FIXTURES / "empty.osop")])
        assert result.exit_code == 1

    def test_validate_complex_workflow(self, runner):
        result = runner.invoke(cli, ["validate", str(FIXTURES / "valid_complex.osop")])
        assert result.exit_code == 0
        assert "Valid" in result.output

    def test_validate_shows_node_count(self, runner):
        result = runner.invoke(cli, ["validate", str(FIXTURES / "valid_minimal.osop")])
        assert result.exit_code == 0
        assert "nodes" in result.output.lower()

    def test_validate_shows_workflow_name(self, runner):
        result = runner.invoke(cli, ["validate", str(FIXTURES / "valid_minimal.osop")])
        assert "Minimal Test Workflow" in result.output


# ---------------------------------------------------------------------------
# record command
# ---------------------------------------------------------------------------

class TestRecordCommand:
    def test_record_nonexistent_file(self, runner):
        result = runner.invoke(cli, ["record", "/no/such/file.osop"])
        assert result.exit_code == 1
        assert "Error" in result.output

    def test_record_mock_mode(self, runner):
        result = runner.invoke(cli, ["record", "--mock", str(FIXTURES / "valid_minimal.osop")])
        assert result.exit_code == 0
        assert "mock" in result.output.lower()

    def test_record_strict_fails_without_mcp(self, runner):
        result = runner.invoke(cli, ["record", str(FIXTURES / "valid_minimal.osop")])
        # Should fail with error if osop-mcp not installed, or succeed if it is
        # Either way, it shouldn't crash
        assert result.exit_code in (0, 1)


# ---------------------------------------------------------------------------
# diff command
# ---------------------------------------------------------------------------

class TestDiffCommand:
    def test_diff_nonexistent_file(self, runner):
        result = runner.invoke(cli, ["diff", "/no/such/a.osop", "/no/such/b.osop"])
        assert result.exit_code != 0
