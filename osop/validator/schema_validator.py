"""Validate .osop workflow dicts against the official JSON Schema."""
import json
from pathlib import Path
import jsonschema

# Schema file names by variant
_SCHEMA_FILES = {
    "full": "osop.schema.json",
    "core": "osop-core.schema.json",
}

# Base search directories for schema files
_SCHEMA_DIRS = [
    Path(__file__).parent.parent.parent / "schema",
    Path(__file__).parent.parent.parent.parent / "osop-spec" / "schema",
    Path.home() / "Desktop" / "osop" / "osop-spec" / "schema",
    Path.home() / "projects" / "osop-spec" / "schema",
    Path.cwd() / "osop-spec" / "schema",
    Path.cwd().parent / "osop-spec" / "schema",
]


def load_schema(variant: str = "full") -> dict:
    """Load JSON Schema for validation.

    Args:
        variant: "full" for the complete schema, "core" for the minimal subset.
    """
    filename = _SCHEMA_FILES.get(variant, _SCHEMA_FILES["full"])
    for d in _SCHEMA_DIRS:
        p = d / filename
        if p.exists():
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    # Fallback: minimal inline schema for bootstrapping
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["osop_version", "id", "name", "nodes", "edges"],
        "properties": {
            "osop_version": {"type": "string"},
            "id": {"type": "string"},
            "name": {"type": "string"},
            "nodes": {"type": "array", "minItems": 1},
            "edges": {"type": "array", "minItems": 1},
        }
    }


def validate(workflow: dict, schema_variant: str = "full") -> list[str]:
    """Validate a workflow dict. Returns list of error strings (empty = valid).

    Args:
        workflow: Parsed workflow dict.
        schema_variant: "full" or "core".
    """
    schema = load_schema(schema_variant)
    validator_cls = jsonschema.Draft202012Validator if "$schema" in schema and "2020-12" in schema["$schema"] else jsonschema.Draft7Validator
    validator = validator_cls(schema)
    errors = []
    for err in sorted(validator.iter_errors(workflow), key=lambda e: list(e.absolute_path)):
        path = " > ".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"{path}: {err.message}")
    
    # Additional contract checks
    nodes = workflow.get("nodes", [])
    node_ids = set()
    for node in nodes:
        nid = node.get("id", "")
        if nid in node_ids:
            errors.append(f"nodes: duplicate node id '{nid}'")
        node_ids.add(nid)

    for edge in workflow.get("edges", []):
        frm = edge.get("from", "")
        to = edge.get("to", "")
        if frm and frm not in node_ids:
            errors.append(f"edges: 'from' references unknown node '{frm}'")
        if to and to not in node_ids:
            errors.append(f"edges: 'to' references unknown node '{to}'")

    return errors
