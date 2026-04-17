"""Cross-writer .osoplog schema conformance.

Three writers produce .osoplog files in this codebase:

1. LiveLog SDK (osop.live_log) — used directly by host apps and by `osop replay`.
2. Transcript parser (osop.recorder.transcript) — used by `osop log` to
   synthesize .osoplog from Claude Code session transcripts.
3. Full executor (osop-mcp/tools/osoplog) — used by `osop record`. Not tested
   here because it lives in a sibling package; it is covered by osop-mcp's
   own test suite.

This module pins the canonical contract: every writer in this repo MUST emit
.osoplog files that satisfy the spec at osop-spec/schema/osoplog.schema.json
AND set ``runtime.source`` to a canonical value so consumers can attribute
each log to its origin.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from osop import LiveLog
from osop.recorder import parse_transcript, synthesize


SPEC_SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "osop-spec" / "schema" / "osoplog.schema.json"
)


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
    required = schema["required"]
    for key in required:
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


# ---------------------------------------------------------------------------
# LiveLog (used by `osop replay` + direct host apps)
# ---------------------------------------------------------------------------

WORKFLOW_YAML = """\
osop_version: "1.0"
id: "schema-conformance-test"
name: "Conformance test workflow"
description: "Used by test_schema_conformance to drive LiveLog."
nodes:
  - id: "step-a"
    type: "cli"
    name: "A"
    description: "first"
edges: []
"""


@pytest.fixture
def workflow_file(tmp_path):
    p = tmp_path / "wf.osop.yaml"
    p.write_text(WORKFLOW_YAML, encoding="utf-8")
    return p


def test_live_log_output_matches_canonical_schema(workflow_file, tmp_path):
    log = LiveLog.start(workflow_file, output_dir=tmp_path / "logs")
    with log.node("step-a") as ctx:
        ctx.output(rows=1)
    out_path = log.finish("COMPLETED")

    doc = yaml.safe_load(out_path.read_text(encoding="utf-8"))
    errs = _validate_against_schema(doc)
    assert not errs, f"LiveLog output failed schema: {errs}"


def test_live_log_sets_canonical_runtime_source(workflow_file, tmp_path):
    log = LiveLog.start(workflow_file, output_dir=tmp_path / "logs")
    with log.node("step-a"):
        pass
    out_path = log.finish("COMPLETED")
    doc = yaml.safe_load(out_path.read_text(encoding="utf-8"))
    assert doc["runtime"]["source"] == "live-log", \
        f"LiveLog must set runtime.source='live-log', got {doc['runtime'].get('source')!r}"


# ---------------------------------------------------------------------------
# Transcript parser (used by `osop log`)
# ---------------------------------------------------------------------------


# Pick a real transcript checked in via the user's Claude Code project.
# Skip if the user doesn't have one (e.g. fresh CI).
_PROJECT_TRANSCRIPTS = (
    Path.home() / ".claude" / "projects" / "C--Users-A7-Desktop-osop"
)


def _has_real_transcript() -> bool:
    return _PROJECT_TRANSCRIPTS.exists() and any(
        _PROJECT_TRANSCRIPTS.glob("*.jsonl")
    )


@pytest.mark.skipif(
    not _has_real_transcript(),
    reason="No Claude Code transcript available for transcript-parser conformance test",
)
def test_transcript_parser_output_matches_canonical_schema():
    transcript = sorted(_PROJECT_TRANSCRIPTS.glob("*.jsonl"))[0]
    parsed = parse_transcript(transcript)
    if not parsed["nodes"]:
        pytest.skip("transcript has no usable events")
    osop_doc, log_doc = synthesize(parsed, short_desc="conformance-test")

    errs = _validate_against_schema(log_doc)
    assert not errs, f"transcript-parser output failed schema: {errs}"


@pytest.mark.skipif(
    not _has_real_transcript(),
    reason="No Claude Code transcript available",
)
def test_transcript_parser_sets_canonical_runtime_source():
    transcript = sorted(_PROJECT_TRANSCRIPTS.glob("*.jsonl"))[0]
    parsed = parse_transcript(transcript)
    if not parsed["nodes"]:
        pytest.skip("transcript has no usable events")
    _, log_doc = synthesize(parsed, short_desc="conformance-test")

    assert log_doc["runtime"]["source"] == "transcript-parser", \
        f"transcript parser must set runtime.source='transcript-parser', got {log_doc['runtime'].get('source')!r}"


# ---------------------------------------------------------------------------
# BLOCKED status (new in canonical v1.0)
# ---------------------------------------------------------------------------


def test_node_status_enum_includes_blocked():
    """Schema must allow BLOCKED as a node status — emitted by osop replay."""
    schema = _load_schema()
    statuses = schema["$defs"]["nodeRecord"]["properties"]["status"]["enum"]
    assert "BLOCKED" in statuses, \
        f"BLOCKED missing from node status enum; have {statuses}"


def test_tool_calls_field_defined():
    """Schema must define tool_calls[] for transcript-parser per-call detail."""
    schema = _load_schema()
    node_props = schema["$defs"]["nodeRecord"]["properties"]
    assert "tool_calls" in node_props, "tool_calls field missing from nodeRecord"
    assert "toolCall" in schema["$defs"], "toolCall $def missing"
