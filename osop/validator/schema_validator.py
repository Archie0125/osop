"""Validate .osop workflow dicts against the official JSON Schema."""
import json
from pathlib import Path
import jsonschema

# Bundled schema path (relative to this file)
_BUNDLED_SCHEMA = Path(__file__).parent.parent.parent / "schema" / "osop.schema.json"

# Search paths for the schema
_SCHEMA_SEARCH_PATHS = [
    _BUNDLED_SCHEMA,
    Path.home() / "projects" / "osop-spec" / "schema" / "osop.schema.json",
    Path.cwd() / "osop-spec" / "schema" / "osop.schema.json",
]


def load_schema() -> dict:
    for p in _SCHEMA_SEARCH_PATHS:
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


def validate(workflow: dict) -> list[str]:
    """Validate a workflow dict. Returns list of error strings (empty = valid)."""
    schema = load_schema()
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
