"""Tests for osop.replayer — execute_workflow, topo_sort, is_destructive."""

from pathlib import Path

import pytest
import yaml

from osop import LiveLog
from osop.replayer import (
    detect_non_sequential_edges,
    execute_workflow,
    is_destructive,
    topo_sort,
    _extract_command,
)


FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# topo_sort
# ---------------------------------------------------------------------------


def test_topo_sort_chain():
    nodes = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]
    out = topo_sort(nodes, edges)
    assert [n["id"] for n in out] == ["a", "b", "c"]


def test_topo_sort_preserves_independent_root_order():
    nodes = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    edges = [{"from": "a", "to": "c"}]
    out = topo_sort(nodes, edges)
    # b is independent and was second in input → should come second in output
    assert [n["id"] for n in out] == ["a", "b", "c"]


def test_topo_sort_rejects_cycle():
    nodes = [{"id": "a"}, {"id": "b"}]
    edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}]
    with pytest.raises(ValueError, match="cycle"):
        topo_sort(nodes, edges)


def test_topo_sort_rejects_duplicate_id():
    nodes = [{"id": "a"}, {"id": "a"}]
    with pytest.raises(ValueError, match="duplicate"):
        topo_sort(nodes, [])


def test_topo_sort_rejects_unknown_edge():
    nodes = [{"id": "a"}]
    edges = [{"from": "a", "to": "ghost"}]
    with pytest.raises(ValueError, match="unknown node"):
        topo_sort(nodes, edges)


def test_topo_sort_node_missing_id():
    with pytest.raises(ValueError, match="missing id"):
        topo_sort([{"type": "cli"}], [])


# ---------------------------------------------------------------------------
# is_destructive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cmd", [
    "rm -rf /tmp/x",
    "rm -fr /var/log/foo",
    "DROP TABLE users",
    "drop database mydb",
    "DELETE FROM users",
    "git push origin main --force",
    "git push -f",
    "git reset --hard HEAD~3",
    "git branch -D feature/x",
    "git clean -fd",
    "mkfs.ext4 /dev/sda1",
    "dd if=/dev/zero of=/dev/sda",
])
def test_is_destructive_positive(cmd):
    assert is_destructive(cmd), f"should match destructive: {cmd}"


@pytest.mark.parametrize("cmd", [
    "echo hello",
    "ls -la",
    "git status",
    "DELETE FROM users WHERE id=1",
    "git push origin main",
    "git pull",
    "rm /tmp/single-file",  # single file w/o -r is borderline; v1 lets it pass
])
def test_is_destructive_negative(cmd):
    assert not is_destructive(cmd), f"should NOT match destructive: {cmd}"


# ---------------------------------------------------------------------------
# _extract_command
# ---------------------------------------------------------------------------


def test_extract_command_top_level():
    assert _extract_command({"command": "echo x"}) == "echo x"


def test_extract_command_alt_key():
    assert _extract_command({"cmd": "ls"}) == "ls"


def test_extract_command_nested_inputs():
    assert _extract_command({"inputs": {"command": "pwd"}}) == "pwd"


def test_extract_command_io_shape():
    assert _extract_command({"io": {"shell": "whoami"}}) == "whoami"


def test_extract_command_empty_when_missing():
    assert _extract_command({"description": "noop"}) == ""


# ---------------------------------------------------------------------------
# detect_non_sequential_edges
# ---------------------------------------------------------------------------


def test_detect_non_sequential_edges_none():
    assert detect_non_sequential_edges([
        {"from": "a", "to": "b", "mode": "sequential"},
    ]) == []


def test_detect_non_sequential_edges_mixed():
    edges = [
        {"from": "a", "to": "b", "mode": "sequential"},
        {"from": "b", "to": "c", "mode": "fallback"},
        {"from": "b", "to": "d", "mode": "conditional"},
        {"from": "b", "to": "e", "mode": "fallback"},
    ]
    result = detect_non_sequential_edges(edges)
    assert "fallback×2" in result
    assert "conditional×1" in result


# ---------------------------------------------------------------------------
# execute_workflow — integration
# ---------------------------------------------------------------------------


def _load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _read(p):
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_replay_dry_run_skips_all_cli_nodes(tmp_path):
    workflow = _load(FIXTURES / "cli_chain.osop.yaml")
    log = LiveLog.start(FIXTURES / "cli_chain.osop.yaml", output_dir=tmp_path)

    summary = execute_workflow(
        workflow,
        log,
        allow_exec=False,
        interactive=False,
        continue_on_error=False,
        confirm_destructive=lambda c: False,
    )
    log.finish(summary["status"])

    assert summary["status"] == "COMPLETED"
    assert summary["counts"]["SKIPPED"] == 3
    assert summary["counts"]["COMPLETED"] == 0
    assert summary["counts"]["FAILED"] == 0

    doc = _read(log.path)
    assert len(doc["node_records"]) == 3
    for rec in doc["node_records"]:
        assert rec["status"] == "SKIPPED"
        assert rec["outputs"]["dry_run"] is True


def test_replay_with_allow_exec_actually_runs(tmp_path):
    workflow = _load(FIXTURES / "cli_chain.osop.yaml")
    log = LiveLog.start(FIXTURES / "cli_chain.osop.yaml", output_dir=tmp_path)

    summary = execute_workflow(
        workflow,
        log,
        allow_exec=True,
        interactive=False,
        continue_on_error=False,
        confirm_destructive=lambda c: False,
    )
    log.finish(summary["status"])

    assert summary["status"] == "COMPLETED"
    assert summary["counts"]["COMPLETED"] == 3

    doc = _read(log.path)
    for rec, expected_out in zip(doc["node_records"], ["step-a-out", "step-b-out", "step-c-out"]):
        assert rec["status"] == "COMPLETED"
        assert expected_out in rec["outputs"]["stdout"]


def test_replay_emits_blocked_for_unreached_nodes_on_halt(tmp_path):
    workflow = _load(FIXTURES / "cli_chain_with_failure.osop.yaml")
    log = LiveLog.start(FIXTURES / "cli_chain_with_failure.osop.yaml", output_dir=tmp_path)

    summary = execute_workflow(
        workflow,
        log,
        allow_exec=True,
        interactive=False,
        continue_on_error=False,
        confirm_destructive=lambda c: False,
    )
    log.finish(summary["status"])

    assert summary["status"] == "HALTED"
    assert summary["halted_on"] == "step-b"
    assert summary["counts"]["COMPLETED"] == 1
    assert summary["counts"]["FAILED"] == 1
    assert summary["counts"]["BLOCKED"] == 1

    doc = _read(log.path)
    assert len(doc["node_records"]) == 3, "every node should appear in the log"

    by_id = {r["node_id"]: r for r in doc["node_records"]}
    assert by_id["step-a"]["status"] == "COMPLETED"
    assert by_id["step-b"]["status"] == "FAILED"
    assert by_id["step-c"]["status"] == "SKIPPED"
    assert "BLOCKED" in by_id["step-c"]["outputs"].get("skip_reason", "") or \
           "BLOCKED" in by_id["step-c"].get("outputs", {}).get("skip_reason", "")


def test_replay_continue_on_error_runs_independent_branches(tmp_path):
    """With --continue-on-error a FAILED node doesn't halt the whole run."""
    workflow = _load(FIXTURES / "cli_chain_with_failure.osop.yaml")
    log = LiveLog.start(FIXTURES / "cli_chain_with_failure.osop.yaml", output_dir=tmp_path)

    summary = execute_workflow(
        workflow,
        log,
        allow_exec=True,
        interactive=False,
        continue_on_error=True,
        confirm_destructive=lambda c: False,
    )
    log.finish(summary["status"])

    # step-c is downstream of step-b; v1 still runs it (no transitive blocking)
    # since continue_on_error is true. Document via test rather than enforce.
    assert summary["status"] == "FAILED"
    assert summary["halted_on"] is None
    assert summary["counts"]["COMPLETED"] >= 2  # a + c
    assert summary["counts"]["FAILED"] == 1
