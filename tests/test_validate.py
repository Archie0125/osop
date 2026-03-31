"""Basic tests for osop validate."""
import pytest
from pathlib import Path
from osop.parser.loader import load_workflow
from osop.validator.schema_validator import validate


EXAMPLES_DIR = Path(__file__).parent.parent.parent / "osop-spec" / "examples"


def _minimal_workflow(**overrides):
    base = {
        "osop_version": "1.0",
        "id": "test_wf",
        "name": "Test Workflow",
        "nodes": [
            {"id": "node_a", "type": "agent", "purpose": "Do something"},
            {"id": "node_b", "type": "human", "purpose": "Review it"},
        ],
        "edges": [{"from": "node_a", "to": "node_b"}],
    }
    base.update(overrides)
    return base


def test_valid_minimal_workflow():
    wf = _minimal_workflow()
    errors = validate(wf)
    assert errors == [], f"Expected no errors, got: {errors}"


def test_missing_required_field():
    wf = _minimal_workflow()
    del wf["name"]
    errors = validate(wf)
    assert any("name" in e for e in errors)


def test_unknown_edge_reference():
    wf = _minimal_workflow()
    wf["edges"] = [{"from": "node_a", "to": "nonexistent_node"}]
    errors = validate(wf)
    assert any("nonexistent_node" in e for e in errors)


def test_duplicate_node_id():
    wf = _minimal_workflow()
    wf["nodes"] = [
        {"id": "node_a", "type": "agent", "purpose": "First"},
        {"id": "node_a", "type": "human", "purpose": "Duplicate"},
    ]
    wf["edges"] = [{"from": "node_a", "to": "node_a"}]
    errors = validate(wf)
    assert any("duplicate" in e for e in errors)
