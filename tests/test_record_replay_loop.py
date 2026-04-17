"""End-to-end test for the record / repeat loop.

This is the test that proves OSOP's headline claim works as one
connected story:

    captured .osop
        → osop replay  →  fresh .osoplog #1
        → osop replay  →  fresh .osoplog #2
        →  diff(#1, #2) shows comparable structure

If this test ever fails, the loop is broken regardless of what unit
tests say. Keep it simple and assertive.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from osop import LiveLog
from osop.agent_invoker import AgentInvocationResult, STATUS_COMPLETED
from osop.replayer import execute_workflow


# A captured-style .osop with one human prompt + one agent phase.
# Modeled on what `osop log` actually emits today.
_CAPTURED_OSOP = """\
osop_version: "1.0"
id: "loop-fixture"
name: "Record/replay loop fixture"
nodes:
  - {id: "u1", type: "human", name: "User asks"}
  - {id: "a1", type: "agent", name: "Agent works"}
edges:
  - {from: "u1", to: "a1", mode: "sequential"}
"""

_CAPTURED_OSOPLOG = """\
osoplog_version: "1.0"
run_id: "fixture-original"
workflow_id: "loop-fixture"
status: "COMPLETED"
started_at: "2026-04-17T00:00:00Z"
ended_at: "2026-04-17T00:00:30Z"
duration_ms: 30000
runtime: {agent: "claude-code", model: "x", source: "transcript-parser"}
node_records:
  - node_id: "u1"
    node_type: "human"
    status: "COMPLETED"
    started_at: "2026-04-17T00:00:00Z"
    ended_at: "2026-04-17T00:00:00Z"
    duration_ms: 0
    outputs: {user_prompt: "list 2 files"}
  - node_id: "a1"
    node_type: "agent"
    status: "COMPLETED"
    started_at: "2026-04-17T00:00:00Z"
    ended_at: "2026-04-17T00:00:30Z"
    duration_ms: 30000
    tool_calls:
      - {tool: "Glob", input: {pattern: "*"}, output: "a.txt\\nb.txt"}
"""


def _replay_once(osop_path: Path, output_dir: Path, fake_result: AgentInvocationResult) -> Path:
    """Run one replay invocation. Returns path to the produced .osoplog."""
    workflow = yaml.safe_load(osop_path.read_text(encoding="utf-8"))
    log = LiveLog.start(osop_path, output_dir=output_dir)
    with patch("osop.replayer.invoke_claude_p", return_value=fake_result):
        summary = execute_workflow(
            workflow,
            log,
            allow_exec=False,
            interactive=False,
            continue_on_error=False,
            confirm_destructive=lambda c: False,
            osop_path=osop_path,
        )
    return log.finish(summary["status"])


def test_record_replay_loop_produces_comparable_runs(tmp_path):
    """Two replays of the same captured .osop produce two valid .osoplog
    files that share workflow_id and node structure but have distinct run_ids
    — exactly what `osop diff` and `osop optimize` need to do their work.
    """
    # 1. RECORD step — represented by these on-disk fixtures, as if `osop log`
    #    had just finished against a real Claude Code transcript.
    osop_path = tmp_path / "loop-fixture.osop.yaml"
    osop_path.write_text(_CAPTURED_OSOP, encoding="utf-8")
    log_ref = tmp_path / "loop-fixture.osoplog.yaml"
    log_ref.write_text(_CAPTURED_OSOPLOG, encoding="utf-8")

    # Two distinct fake claude -p results — different costs/tokens to mimic
    # natural run-to-run variance.
    result_1 = AgentInvocationResult(
        status=STATUS_COMPLETED,
        cost_usd=0.21, tokens_input=120, tokens_output=85,
        model="claude-opus-4-7", result_text="found a.txt and b.txt",
        num_turns=2,
    )
    result_2 = AgentInvocationResult(
        status=STATUS_COMPLETED,
        cost_usd=0.27, tokens_input=125, tokens_output=110,
        model="claude-opus-4-7", result_text="found a.txt and b.txt",
        num_turns=3,
    )

    # 2 + 3. REPEAT × 2 — each call writes a fresh .osoplog into output_dir.
    out1_dir = tmp_path / "run1"
    out2_dir = tmp_path / "run2"
    out1 = _replay_once(osop_path, out1_dir, result_1)
    out2 = _replay_once(osop_path, out2_dir, result_2)

    # 4. The two outputs are real, distinct files.
    assert out1.exists() and out2.exists()
    assert out1 != out2

    # 5. Both validate against canonical schema constraints.
    log1 = yaml.safe_load(out1.read_text(encoding="utf-8"))
    log2 = yaml.safe_load(out2.read_text(encoding="utf-8"))
    for log in (log1, log2):
        for required in ("osoplog_version", "run_id", "workflow_id",
                         "status", "started_at", "ended_at",
                         "duration_ms", "node_records"):
            assert required in log, f"missing {required}"

    # 6. Same workflow_id (so diff/optimize will pair them up).
    assert log1["workflow_id"] == log2["workflow_id"] == "loop-fixture"

    # 7. Distinct run_ids (so diff can tell them apart).
    assert log1["run_id"] != log2["run_id"]

    # 8. Same node-id structure (the "path" was followed by both).
    ids_1 = [r["node_id"] for r in log1["node_records"]]
    ids_2 = [r["node_id"] for r in log2["node_records"]]
    assert ids_1 == ids_2 == ["u1", "a1"]

    # 9. The agent node carries the divergent execution metadata that diff
    #    will surface to the human reader.
    a1_run1 = next(r for r in log1["node_records"] if r["node_id"] == "a1")
    a1_run2 = next(r for r in log2["node_records"] if r["node_id"] == "a1")
    assert a1_run1["outputs"]["cost_usd"] == 0.21
    assert a1_run2["outputs"]["cost_usd"] == 0.27
    assert a1_run1["outputs"]["num_turns"] != a1_run2["outputs"]["num_turns"]

    # 10. Both runs tagged with the canonical writer source so consumers
    #     can attribute the log lineage.
    assert log1["runtime"]["source"] == "live-log"
    assert log2["runtime"]["source"] == "live-log"


def test_record_replay_loop_handles_missing_reference_log(tmp_path):
    """If `osop log` produced an .osop but the paired .osoplog is missing
    (deleted, never copied, etc.), `osop replay` should not crash — it
    should SKIP every agent node loudly and produce a still-valid .osoplog
    so the user sees the gap.
    """
    osop_path = tmp_path / "lonely.osop.yaml"
    osop_path.write_text(_CAPTURED_OSOP, encoding="utf-8")
    # Deliberately don't create the paired .osoplog.

    workflow = yaml.safe_load(osop_path.read_text(encoding="utf-8"))
    log = LiveLog.start(osop_path, output_dir=tmp_path / "out")

    with patch("osop.replayer.invoke_claude_p") as mocked:
        summary = execute_workflow(
            workflow, log,
            allow_exec=False, interactive=False, continue_on_error=False,
            confirm_destructive=lambda c: False,
            osop_path=osop_path,
        )
    out_path = log.finish(summary["status"])

    assert not mocked.called, "replay must not invoke claude -p without reference"

    log_doc = yaml.safe_load(out_path.read_text(encoding="utf-8"))
    a1_rec = next(r for r in log_doc["node_records"] if r["node_id"] == "a1")
    assert a1_rec["status"] == "SKIPPED"
    # The skip reason must mention 'reference' so the user knows why.
    skip_reason = (a1_rec.get("outputs") or {}).get("skip_reason", "")
    assert "reference" in skip_reason.lower(), \
        f"unhelpful skip reason: {skip_reason!r}"
