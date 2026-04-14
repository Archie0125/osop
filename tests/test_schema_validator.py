"""Comprehensive tests for osop.validator.schema_validator."""
import pytest
from osop.validator.schema_validator import validate, load_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_workflow(**overrides):
    """Return a valid minimal workflow dict, with optional overrides."""
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


# ---------------------------------------------------------------------------
# Schema loading
# ---------------------------------------------------------------------------

class TestLoadSchema:
    def test_load_schema_returns_dict(self):
        schema = load_schema()
        assert isinstance(schema, dict)

    def test_schema_has_required_fields(self):
        schema = load_schema()
        assert "required" in schema or "properties" in schema

    def test_fallback_schema_has_required_list(self):
        """The fallback inline schema should require the five core fields."""
        schema = load_schema()
        # Whether from file or fallback, the required fields should include these:
        required = schema.get("required", [])
        for field in ["osop_version", "id", "name", "nodes", "edges"]:
            assert field in required, f"'{field}' missing from schema required list"


# ---------------------------------------------------------------------------
# Valid workflows
# ---------------------------------------------------------------------------

class TestValidWorkflows:
    def test_valid_minimal_workflow(self):
        errors = validate(_minimal_workflow())
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_valid_workflow_with_description(self):
        wf = _minimal_workflow(description="A test workflow")
        errors = validate(wf)
        assert errors == []

    def test_valid_workflow_with_extension_fields(self):
        """Extension fields (x-*) beyond the spec should not cause validation errors."""
        wf = _minimal_workflow(tags=["test"])
        wf["x-custom"] = "hello"
        errors = validate(wf)
        assert errors == []

    def test_valid_single_node_self_edge(self):
        wf = _minimal_workflow(
            nodes=[{"id": "only", "type": "agent", "purpose": "Self-loop"}],
            edges=[{"from": "only", "to": "only"}],
        )
        errors = validate(wf)
        assert errors == []

    def test_valid_workflow_with_edge_modes(self):
        wf = _minimal_workflow()
        wf["edges"] = [
            {"from": "node_a", "to": "node_b", "mode": "conditional", "when": "x > 1"},
        ]
        errors = validate(wf)
        assert errors == []


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------

class TestMissingRequiredFields:
    @pytest.mark.parametrize("field", ["osop_version", "id", "name", "nodes", "edges"])
    def test_missing_required_field(self, field):
        wf = _minimal_workflow()
        del wf[field]
        errors = validate(wf)
        assert len(errors) > 0, f"Expected errors when '{field}' is missing"
        assert any(field in e for e in errors), (
            f"Expected error mentioning '{field}', got: {errors}"
        )


# ---------------------------------------------------------------------------
# Duplicate node IDs
# ---------------------------------------------------------------------------

class TestDuplicateNodeIds:
    def test_duplicate_node_id(self):
        wf = _minimal_workflow(
            nodes=[
                {"id": "dup", "type": "agent", "purpose": "First"},
                {"id": "dup", "type": "human", "purpose": "Second"},
            ],
            edges=[{"from": "dup", "to": "dup"}],
        )
        errors = validate(wf)
        assert any("duplicate" in e.lower() for e in errors)

    def test_no_duplicate_with_unique_ids(self):
        errors = validate(_minimal_workflow())
        assert not any("duplicate" in e.lower() for e in errors)

    def test_triple_duplicate(self):
        wf = _minimal_workflow(
            nodes=[
                {"id": "x", "type": "agent", "purpose": "A"},
                {"id": "x", "type": "agent", "purpose": "B"},
                {"id": "x", "type": "agent", "purpose": "C"},
            ],
            edges=[{"from": "x", "to": "x"}],
        )
        errors = validate(wf)
        dup_errors = [e for e in errors if "duplicate" in e.lower()]
        # At least 2 duplicates flagged (second and third occurrence)
        assert len(dup_errors) >= 2


# ---------------------------------------------------------------------------
# Edge references to non-existent nodes
# ---------------------------------------------------------------------------

class TestEdgeReferences:
    def test_edge_from_unknown_node(self):
        wf = _minimal_workflow()
        wf["edges"] = [{"from": "ghost", "to": "node_b"}]
        errors = validate(wf)
        assert any("ghost" in e for e in errors)

    def test_edge_to_unknown_node(self):
        wf = _minimal_workflow()
        wf["edges"] = [{"from": "node_a", "to": "nonexistent"}]
        errors = validate(wf)
        assert any("nonexistent" in e for e in errors)

    def test_both_edge_endpoints_unknown(self):
        wf = _minimal_workflow()
        wf["edges"] = [{"from": "bad_src", "to": "bad_dst"}]
        errors = validate(wf)
        assert any("bad_src" in e for e in errors)
        assert any("bad_dst" in e for e in errors)


# ---------------------------------------------------------------------------
# Empty arrays
# ---------------------------------------------------------------------------

class TestEmptyArrays:
    def test_empty_nodes_array(self):
        wf = _minimal_workflow(nodes=[])
        errors = validate(wf)
        # The fallback schema requires minItems: 1 for nodes
        assert len(errors) > 0, "Empty nodes array should produce errors"

    def test_empty_edges_array(self):
        wf = _minimal_workflow(edges=[])
        errors = validate(wf)
        # The fallback schema requires minItems: 1 for edges
        assert len(errors) > 0, "Empty edges array should produce errors"


# ---------------------------------------------------------------------------
# Type validation
# ---------------------------------------------------------------------------

class TestTypeValidation:
    def test_osop_version_must_be_string(self):
        wf = _minimal_workflow(osop_version=1.0)
        errors = validate(wf)
        assert len(errors) > 0

    def test_id_must_be_string(self):
        wf = _minimal_workflow(id=123)
        errors = validate(wf)
        assert len(errors) > 0

    def test_name_must_be_string(self):
        wf = _minimal_workflow(name=42)
        errors = validate(wf)
        assert len(errors) > 0

    def test_nodes_must_be_array(self):
        wf = _minimal_workflow(nodes="not-an-array")
        errors = validate(wf)
        assert len(errors) > 0

    def test_edges_must_be_array(self):
        wf = _minimal_workflow(edges="not-an-array")
        errors = validate(wf)
        assert len(errors) > 0


# ---------------------------------------------------------------------------
# Multiple errors at once
# ---------------------------------------------------------------------------

class TestMultipleErrors:
    def test_multiple_issues_reported(self):
        wf = {
            "osop_version": "1.0",
            "id": "multi-err",
            "name": "Multi Error",
            "nodes": [
                {"id": "a", "type": "agent", "purpose": "X"},
                {"id": "a", "type": "agent", "purpose": "Y"},  # duplicate
            ],
            "edges": [
                {"from": "a", "to": "missing"},  # unknown ref
            ],
        }
        errors = validate(wf)
        assert len(errors) >= 2, f"Expected 2+ errors, got {len(errors)}: {errors}"
