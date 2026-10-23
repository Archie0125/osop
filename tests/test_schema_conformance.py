""".osoplog schema conformance for the transcript recorder.

Pins the contract: `osop log` MUST emit .osoplog files that satisfy the spec
at osop-spec/schema/osoplog.schema.json AND set ``runtime.source`` so
consumers can attribute each log to its origin.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from osop.recorder import parse_transcript, synthesize


SPEC_SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "osop-spec" / "schema" / "osoplog.schema.json"
)
MINIMAL_SESSION = Path(__file__).parent / "fixtures" / "minimal_session.jsonl"


def _load_schema():
    if not SPEC_SCHEMA.exists():
        pytest.skip(f"osop-spec schema not reachable at {SPEC_SCHEMA}")
    return json.loads(SPEC_SCHEMA.read_text(encoding="utf-8"))


def _validate_against_schema(doc: dict) -> list[str]:
    """Lightweight contract check focused on the canonical required fields.

    We avoid pulling in jsonschema for this test (the project doesn't depend
    on it). Field presence + types catches the drifts we care about.
    """
    errors: list[str] = []
    schema = _load_schema()
    for key in schema["required"]:
        if key not in doc:
            errors.append(f"missing required: {key}")

    status_enum = set(schema["properties"]["status"]["enum"])
    if doc.get("status") not in status_enum:
        errors.append(f"status not in enum: {doc.get('status')!r}")

    node_status_enum = set(
        schema["$defs"]["nodeRecord"]["properties"]["status"]["enum"]
    )
    for i, rec in enumerate(doc.get("node_records", [])):
        if not isinstance(rec, dict):
            errors.append(f"node_records[{i}] not a dict")
            continue
        if "node_id" not in rec:
            errors.append(f"node_records[{i}] missing node_id")
        if rec.get("status") and rec["status"] not in node_status_enum:
            errors.append(
                f"node_records[{i}] status {rec['status']!r} not in enum"
            )
    return errors


def test_transcript_parser_output_matches_canonical_schema():
    log_doc = synthesize(parse_transcript(MINIMAL_SESSION), short_desc="conformance-test")
    errs = _validate_against_schema(log_doc)
    assert not errs, f"transcript-parser output failed schema: {errs}"


def test_transcript_parser_sets_canonical_runtime_source():
    log_doc = synthesize(parse_transcript(MINIMAL_SESSION), short_desc="conformance-test")
    assert log_doc["runtime"]["source"] == "transcript-parser"


def test_tool_calls_field_defined():
    """Schema must define tool_calls[] for transcript-parser per-call detail."""
    schema = _load_schema()
    node_props = schema["$defs"]["nodeRecord"]["properties"]
    assert "tool_calls" in node_props, "tool_calls field missing from nodeRecord"
    assert "toolCall" in schema["$defs"], "toolCall $def missing"
