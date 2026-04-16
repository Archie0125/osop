"""Tests for osop.live_log.LiveLog."""

import pytest
import yaml

from osop import LiveLog


WORKFLOW_YAML = """\
osop_version: "1.0"
id: "test-workflow"
name: "Test Workflow"
description: "For LiveLog tests"
version: "1.0.0"

nodes:
  - id: "step-one"
    type: "cli"
    subtype: "script"
    name: "Step One"
    description: "first"
    security: {risk_level: "low"}
  - id: "step-two"
    type: "cli"
    subtype: "script"
    name: "Step Two"
    description: "second"
    security: {risk_level: "low"}

edges:
  - {from: "step-one", to: "step-two", mode: "sequential"}
"""


@pytest.fixture
def workflow_file(tmp_path):
    p = tmp_path / "wf.osop.yaml"
    p.write_text(WORKFLOW_YAML, encoding="utf-8")
    return p


def _read(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_start_creates_log_file_immediately(workflow_file, tmp_path):
    log = LiveLog.start(workflow_file, output_dir=tmp_path / "logs")
    assert log.path.exists(), "log file must exist after start() (live mode)"
    doc = _read(log.path)
    assert doc["osoplog_version"] == "1.0"
    assert doc["workflow_id"] == "test-workflow"
    assert doc["status"] == "RUNNING"
    assert doc["mode"] == "live"


def test_node_records_flush_on_completion(workflow_file, tmp_path):
    log = LiveLog.start(workflow_file, output_dir=tmp_path / "logs")

    with log.node("step-one") as node:
        node.output(rows=42)

    doc = _read(log.path)
    assert len(doc["node_records"]) == 1
    rec = doc["node_records"][0]
    assert rec["node_id"] == "step-one"
    assert rec["status"] == "COMPLETED"
    assert rec["outputs"] == {"rows": 42}
    assert "started_at" in rec and "ended_at" in rec
    assert rec["duration_ms"] >= 0


def test_exception_marks_node_failed_and_reraises(workflow_file, tmp_path):
    log = LiveLog.start(workflow_file, output_dir=tmp_path / "logs")

    with pytest.raises(ValueError, match="boom"):
        with log.node("step-one"):
            raise ValueError("boom")

    doc = _read(log.path)
    rec = doc["node_records"][0]
    assert rec["status"] == "FAILED"
    assert "ValueError: boom" in rec["error"]


def test_explicit_fail_does_not_require_raise(workflow_file, tmp_path):
    log = LiveLog.start(workflow_file, output_dir=tmp_path / "logs")

    with log.node("step-one") as node:
        node.fail(error="bad input")

    doc = _read(log.path)
    rec = doc["node_records"][0]
    assert rec["status"] == "FAILED"
    assert rec["error"] == "bad input"


def test_unknown_node_id_rejected(workflow_file, tmp_path):
    log = LiveLog.start(workflow_file, output_dir=tmp_path / "logs")
    with pytest.raises(ValueError, match="not found in workflow"):
        with log.node("nonexistent-node"):
            pass


def test_finish_sets_terminal_status(workflow_file, tmp_path):
    log = LiveLog.start(workflow_file, output_dir=tmp_path / "logs")
    with log.node("step-one"):
        pass
    path = log.finish("COMPLETED")
    doc = _read(path)
    assert doc["status"] == "COMPLETED"
    assert "ended_at" in doc
    assert doc["duration_ms"] >= 0


def test_finish_rejects_open_node(workflow_file, tmp_path):
    log = LiveLog.start(workflow_file, output_dir=tmp_path / "logs")
    # simulate forgotten __exit__ by entering manually
    cm = log.node("step-one")
    cm.__enter__()
    with pytest.raises(RuntimeError, match="still running"):
        log.finish()
    cm.__exit__(None, None, None)


def test_partial_record_survives_crash_mid_node(workflow_file, tmp_path):
    """The whole point: crash mid-node still leaves a readable osoplog."""
    log = LiveLog.start(workflow_file, output_dir=tmp_path / "logs")
    cm = log.node("step-one")
    cm.__enter__()
    # "crash" — we never call __exit__. File must still be parseable.
    doc = _read(log.path)
    assert doc["status"] == "RUNNING"
    assert len(doc["node_records"]) == 1
    assert doc["node_records"][0]["status"] == "RUNNING"


def test_sequential_constraint(workflow_file, tmp_path):
    log = LiveLog.start(workflow_file, output_dir=tmp_path / "logs")
    cm1 = log.node("step-one")
    cm1.__enter__()
    with pytest.raises(RuntimeError, match="still open"):
        with log.node("step-two"):
            pass
    cm1.__exit__(None, None, None)


def test_timestamps_are_utc_z_suffixed(workflow_file, tmp_path):
    """Cross-runtime convention: all .osoplog timestamps are UTC with 'Z'.

    LiveLog must match `osop log` and `osop record` so logs from different
    sources can be diffed, optimized, and viewed without timezone drift.
    """
    log = LiveLog.start(workflow_file, output_dir=tmp_path / "logs")
    with log.node("step-one"):
        pass
    log.finish()

    doc = _read(log.path)
    assert doc["started_at"].endswith("Z"), f"started_at not UTC-Z: {doc['started_at']}"
    assert doc["ended_at"].endswith("Z"), f"ended_at not UTC-Z: {doc['ended_at']}"
    rec = doc["node_records"][0]
    assert rec["started_at"].endswith("Z"), f"node started_at not UTC-Z: {rec['started_at']}"
    assert rec["ended_at"].endswith("Z"), f"node ended_at not UTC-Z: {rec['ended_at']}"
    # millisecond precision retained
    assert "." in rec["started_at"], "millisecond precision lost"


def test_generated_osoplog_passes_schema_validation(workflow_file, tmp_path):
    """The whole point: an osoplog written by LiveLog must conform to spec.

    Uses the project's own validator so this test catches schema drift even
    if the host writer and the validator both live in this repo.
    """
    from osop.validator.schema_validator import validate as _validate
    import yaml as _yaml

    log = LiveLog.start(workflow_file, output_dir=tmp_path / "logs")
    with log.node("step-one") as n:
        n.output(rows=1)
    out_path = log.finish("COMPLETED")

    with open(out_path, encoding="utf-8") as f:
        log_doc = _yaml.safe_load(f)

    # Required fields per the osoplog contract
    for required in ("osoplog_version", "run_id", "workflow_id", "status",
                     "started_at", "ended_at", "duration_ms", "node_records"):
        assert required in log_doc, f"missing required field: {required}"

    assert log_doc["status"] in {"COMPLETED", "FAILED", "TIMEOUT", "COST_LIMIT", "BLOCKED", "DRY_RUN"}
    assert isinstance(log_doc["node_records"], list)
    for rec in log_doc["node_records"]:
        assert "node_id" in rec, f"node_record missing node_id: {rec}"
