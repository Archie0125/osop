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

    def test_render_help(self, runner):
        result = runner.invoke(cli, ["render", "--help"])
        assert result.exit_code == 0
        assert "Render" in result.output

    def test_run_help(self, runner):
        result = runner.invoke(cli, ["run", "--help"])
        assert result.exit_code == 0

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
# render command
# ---------------------------------------------------------------------------

class TestRenderCommand:
    def test_render_story_default(self, runner):
        result = runner.invoke(cli, ["render", str(FIXTURES / "valid_complex.osop")])
        assert result.exit_code == 0
        assert "Story View" in result.output

    def test_render_story_explicit(self, runner):
        result = runner.invoke(cli, ["render", "--view", "story", str(FIXTURES / "valid_complex.osop")])
        assert result.exit_code == 0
        assert "Story View" in result.output

    def test_render_role_view(self, runner):
        result = runner.invoke(cli, ["render", "--view", "role", str(FIXTURES / "valid_complex.osop")])
        assert result.exit_code == 0
        assert "Role View" in result.output

    def test_render_unimplemented_view(self, runner):
        result = runner.invoke(cli, ["render", "--view", "graph", str(FIXTURES / "valid_complex.osop")])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output

    def test_render_nonexistent_file(self, runner):
        result = runner.invoke(cli, ["render", "/no/such/file.osop"])
        assert result.exit_code == 1

    def test_render_invalid_view_choice(self, runner):
        result = runner.invoke(cli, ["render", "--view", "invalid_view", str(FIXTURES / "valid_complex.osop")])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# run command
# ---------------------------------------------------------------------------

class TestRunCommand:
    def test_run_mock_default(self, runner):
        result = runner.invoke(cli, ["run", str(FIXTURES / "valid_minimal.osop")])
        assert result.exit_code == 0
        assert "Run complete" in result.output

    def test_run_shows_nodes(self, runner):
        result = runner.invoke(cli, ["run", str(FIXTURES / "valid_minimal.osop")])
        assert "step_a" in result.output
        assert "step_b" in result.output

    def test_run_nonexistent_file(self, runner):
        result = runner.invoke(cli, ["run", "/no/such/file.osop"])
        assert result.exit_code == 1

    def test_run_complex_workflow(self, runner):
        result = runner.invoke(cli, ["run", str(FIXTURES / "valid_complex.osop")])
        assert result.exit_code == 0
        assert "4 nodes executed" in result.output


# ---------------------------------------------------------------------------
# test command
# ---------------------------------------------------------------------------

class TestTestCommand:
    def test_test_no_tests_defined(self, runner):
        result = runner.invoke(cli, ["test", str(FIXTURES / "valid_minimal.osop")])
        assert result.exit_code == 0
        assert "No tests" in result.output

    def test_test_with_tests(self, runner, tmp_path):
        import yaml
        wf = {
            "osop_version": "1.0",
            "id": "with-tests",
            "name": "Workflow With Tests",
            "nodes": [{"id": "a", "type": "agent", "purpose": "x"}],
            "edges": [{"from": "a", "to": "a"}],
            "tests": [
                {"id": "test_1", "type": "unit"},
                {"id": "test_2", "type": "integration"},
            ],
        }
        f = tmp_path / "with_tests.yaml"
        f.write_text(yaml.dump(wf), encoding="utf-8")
        result = runner.invoke(cli, ["test", str(f)])
        assert result.exit_code == 0
        assert "2 passed" in result.output

    def test_test_nonexistent_file(self, runner):
        result = runner.invoke(cli, ["test", "/no/such/file.osop"])
        assert result.exit_code == 1
