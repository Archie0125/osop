"""Tests for osop.parser.loader — file loading and parsing."""
import pytest
import json
import yaml
from pathlib import Path
from osop.parser.loader import load_workflow


FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Valid file loading
# ---------------------------------------------------------------------------

class TestLoadValidFiles:
    def test_load_valid_yaml(self):
        data = load_workflow(str(FIXTURES / "valid_minimal.osop"))
        assert isinstance(data, dict)
        assert data["osop_version"] == "1.0"
        assert data["id"] == "test-minimal"
        assert data["name"] == "Minimal Test Workflow"
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1

    def test_load_valid_json(self):
        data = load_workflow(str(FIXTURES / "valid_minimal.json"))
        assert isinstance(data, dict)
        assert data["id"] == "test-minimal-json"
        assert data["name"] == "Minimal JSON Workflow"

    def test_load_complex_yaml(self):
        data = load_workflow(str(FIXTURES / "valid_complex.osop"))
        assert len(data["nodes"]) == 4
        assert len(data["edges"]) == 4
        assert data["edges"][1]["mode"] == "conditional"

    def test_load_yaml_with_yml_extension(self, tmp_path):
        content = {
            "osop_version": "1.0",
            "id": "yml-test",
            "name": "YML Test",
            "nodes": [{"id": "a", "type": "agent", "purpose": "test"}],
            "edges": [{"from": "a", "to": "a"}],
        }
        f = tmp_path / "test.yml"
        f.write_text(yaml.dump(content), encoding="utf-8")
        data = load_workflow(str(f))
        assert data["id"] == "yml-test"


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

class TestLoadErrors:
    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError, match="not found"):
            load_workflow("/nonexistent/path/workflow.osop")

    def test_invalid_yaml(self):
        with pytest.raises(Exception):
            load_workflow(str(FIXTURES / "invalid_yaml.osop"))

    def test_unsupported_extension(self, tmp_path):
        f = tmp_path / "workflow.txt"
        f.write_text("some content", encoding="utf-8")
        with pytest.raises(ValueError, match="Unsupported file format"):
            load_workflow(str(f))

    def test_empty_file(self):
        """Empty YAML file parses to None, which should raise ValueError."""
        with pytest.raises(ValueError, match="must be a YAML/JSON object"):
            load_workflow(str(FIXTURES / "empty.osop"))

    def test_yaml_array_not_dict(self, tmp_path):
        """A YAML file containing a list instead of a dict should fail."""
        f = tmp_path / "array.yaml"
        f.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(ValueError, match="must be a YAML/JSON object"):
            load_workflow(str(f))

    def test_json_array_not_dict(self, tmp_path):
        f = tmp_path / "array.json"
        f.write_text('[1, 2, 3]', encoding="utf-8")
        with pytest.raises(ValueError, match="must be a YAML/JSON object"):
            load_workflow(str(f))

    def test_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text('{"broken": json', encoding="utf-8")
        with pytest.raises(Exception):
            load_workflow(str(f))


# ---------------------------------------------------------------------------
# Tmp_path based tests
# ---------------------------------------------------------------------------

class TestLoadWithTmpPath:
    def test_round_trip_yaml(self, tmp_path):
        """Write a workflow dict as YAML, load it back, verify round-trip."""
        original = {
            "osop_version": "1.0",
            "id": "round-trip",
            "name": "Round Trip Test",
            "nodes": [
                {"id": "n1", "type": "cli", "purpose": "Run script"},
            ],
            "edges": [{"from": "n1", "to": "n1"}],
        }
        f = tmp_path / "rt.yaml"
        f.write_text(yaml.dump(original), encoding="utf-8")
        loaded = load_workflow(str(f))
        assert loaded["id"] == original["id"]
        assert loaded["name"] == original["name"]
        assert len(loaded["nodes"]) == len(original["nodes"])

    def test_round_trip_json(self, tmp_path):
        original = {
            "osop_version": "1.0",
            "id": "json-rt",
            "name": "JSON Round Trip",
            "nodes": [{"id": "j1", "type": "api", "purpose": "Call API"}],
            "edges": [{"from": "j1", "to": "j1"}],
        }
        f = tmp_path / "rt.json"
        f.write_text(json.dumps(original), encoding="utf-8")
        loaded = load_workflow(str(f))
        assert loaded == original
