"""Tests for `osop replay` v2 — agent imitation execution.

Covers:
  - Reference-log discovery (paired stem convention)
  - Preceding-user-prompt walk through the workflow graph
  - Imitation prompt format
  - execute_agent_node behavior with mocked claude -p
  - End-to-end execute_workflow wiring with a captured-style fixture
  - Live `claude -p` smoke test (skipped when binary unavailable)
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from osop import LiveLog
from osop.agent_invoker import AgentInvocationResult, STATUS_COMPLETED
from osop.imitation import (
    build_imitation_prompt,
    find_preceding_user_prompt,
    find_reference_log,
    index_outputs_by_node,
    index_tool_calls_by_node,
    load_reference_log,
)
from osop.replayer import execute_agent_node, execute_workflow


FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# find_reference_log
# ---------------------------------------------------------------------------


def test_find_reference_log_paired(tmp_path):
    osop = tmp_path / "demo.osop.yaml"
    log = tmp_path / "demo.osoplog.yaml"
    osop.write_text("osop_version: '1.0'\nid: x\nname: x\nnodes: []\nedges: []\n")
    log.write_text("osoplog_version: '1.0'\n")
    assert find_reference_log(osop) == log


def test_find_reference_log_missing(tmp_path):
    osop = tmp_path / "alone.osop.yaml"
    osop.write_text("osop_version: '1.0'\nid: x\nname: x\nnodes: []\nedges: []\n")
    assert find_reference_log(osop) is None


def test_find_reference_log_unknown_extension(tmp_path):
    osop = tmp_path / "x.txt"
    osop.write_text("not an osop")
    assert find_reference_log(osop) is None


# ---------------------------------------------------------------------------
# find_preceding_user_prompt
# ---------------------------------------------------------------------------


def _build_session_graph():
    nodes = [
        {"id": "u1", "type": "human"},
        {"id": "a1", "type": "agent"},
        {"id": "u2", "type": "human"},
        {"id": "a2", "type": "agent"},
    ]
    edges = [
        {"from": "u1", "to": "a1", "mode": "sequential"},
        {"from": "a1", "to": "u2", "mode": "sequential"},
        {"from": "u2", "to": "a2", "mode": "sequential"},
    ]
    outputs_by_node = {
        "u1": {"user_prompt": "first request"},
        "u2": {"user_prompt": "second request"},
    }
    return nodes, edges, outputs_by_node


def test_preceding_user_prompt_first_agent():
    nodes, edges, outs = _build_session_graph()
    assert find_preceding_user_prompt(nodes, edges, "a1", outs) == "first request"


def test_preceding_user_prompt_later_agent():
    nodes, edges, outs = _build_session_graph()
    assert find_preceding_user_prompt(nodes, edges, "a2", outs) == "second request"


def test_preceding_user_prompt_walks_through_intermediate_nodes():
    """When a cli node sits between human and agent, walk through it."""
    nodes = [
        {"id": "u1", "type": "human"},
        {"id": "c1", "type": "cli"},
        {"id": "a1", "type": "agent"},
    ]
    edges = [
        {"from": "u1", "to": "c1", "mode": "sequential"},
        {"from": "c1", "to": "a1", "mode": "sequential"},
    ]
    outs = {"u1": {"user_prompt": "do the work"}}
    assert find_preceding_user_prompt(nodes, edges, "a1", outs) == "do the work"


def test_preceding_user_prompt_returns_none_when_no_human():
    nodes = [{"id": "a1", "type": "agent"}]
    assert find_preceding_user_prompt(nodes, [], "a1", {}) is None


# ---------------------------------------------------------------------------
# build_imitation_prompt
# ---------------------------------------------------------------------------


def test_imitation_prompt_includes_user_request_and_actions():
    node = {"id": "a1", "type": "agent", "name": "Do work"}
    prompt = build_imitation_prompt(
        node=node,
        user_prompt="Read the README",
        original_tool_calls=[
            {"tool": "Read", "input": {"file_path": "/tmp/README"}, "output": "hello"},
            {"tool": "Bash", "input": {"command": "ls"}, "output": "a\nb\nc"},
        ],
    )
    assert "Read the README" in prompt
    assert "Read: file_path=/tmp/README" in prompt
    assert "Bash: command=ls" in prompt
    assert "REPLAY CONTEXT" in prompt
    assert "ORIGINAL ACTIONS" in prompt


def test_imitation_prompt_handles_no_tool_calls():
    node = {"id": "a1", "type": "agent"}
    prompt = build_imitation_prompt(
        node=node, user_prompt="just say hi", original_tool_calls=[]
    )
    assert "just say hi" in prompt
    assert "(no recorded tool actions" in prompt


def test_imitation_prompt_handles_no_user_prompt():
    node = {"id": "a1", "type": "agent"}
    prompt = build_imitation_prompt(
        node=node,
        user_prompt=None,
        original_tool_calls=[{"tool": "Read", "input": {"file_path": "x"}, "output": "y"}],
    )
    assert "(no original user prompt" in prompt


def test_imitation_prompt_truncates_huge_action_blocks():
    huge_calls = [
        {"tool": "Bash", "input": {"command": "x" * 5000}, "output": "y" * 5000}
        for _ in range(50)
    ]
    prompt = build_imitation_prompt(
        node={"id": "a"}, user_prompt="x", original_tool_calls=huge_calls
    )
    # Soft-cap at ~12000 plus template overhead — should not be unbounded
    assert len(prompt) < 20000


# ---------------------------------------------------------------------------
# execute_agent_node — mocked invoke_claude_p
# ---------------------------------------------------------------------------


def test_execute_agent_node_skips_when_no_reference():
    out = execute_agent_node(
        {"id": "a1", "type": "agent"},
        user_prompt=None,
        original_tool_calls=[],
        cwd=None,
        max_budget_usd=1.0,
        max_turns=3,
        allowed_tools=["Read"],
    )
    assert out["status"] == "SKIPPED"
    assert "imitate" in out["reason"].lower()


def test_execute_agent_node_records_completed_invocation():
    fake = AgentInvocationResult(
        status=STATUS_COMPLETED,
        cost_usd=0.123456,
        tokens_input=42,
        tokens_output=99,
        model="claude-opus-4-7",
        result_text="all done",
        num_turns=3,
    )
    with patch("osop.replayer.invoke_claude_p", return_value=fake) as mocked:
        out = execute_agent_node(
            {"id": "a1", "type": "agent", "name": "n"},
            user_prompt="please do x",
            original_tool_calls=[
                {"tool": "Bash", "input": {"command": "ls"}, "output": "ok"}
            ],
            cwd="/tmp",
            max_budget_usd=2.0,
            max_turns=5,
            allowed_tools=["Bash"],
        )

    assert mocked.called
    kwargs = mocked.call_args.kwargs
    assert "please do x" in kwargs["prompt"]
    assert kwargs["max_budget_usd"] == 2.0
    assert kwargs["allowed_tools"] == ["Bash"]
    assert out["status"] == "COMPLETED"
    assert out["cost_usd"] == round(0.123456, 6)
    assert out["tokens_input"] == 42
    assert out["model"] == "claude-opus-4-7"
    assert "all done" in out["result_text"]


def test_execute_agent_node_marks_failed_on_budget_exceeded():
    fake = AgentInvocationResult(
        status="BUDGET_EXCEEDED",
        cost_usd=5.01,
        error="max-budget exceeded",
    )
    with patch("osop.replayer.invoke_claude_p", return_value=fake):
        out = execute_agent_node(
            {"id": "a1", "type": "agent"},
            user_prompt="x",
            original_tool_calls=[{"tool": "Bash", "input": {}, "output": ""}],
            cwd=None,
            max_budget_usd=5.0,
            max_turns=10,
            allowed_tools=None,
        )
    assert out["status"] == "FAILED"
    assert out["claude_status"] == "BUDGET_EXCEEDED"
    assert "exceeded" in out["error"]


# ---------------------------------------------------------------------------
# execute_workflow — agent dispatch end-to-end (mocked)
# ---------------------------------------------------------------------------


_CAPTURED_OSOP = """\
osop_version: "1.0"
id: "captured-imitation-test"
name: "Captured imitation fixture"
nodes:
  - {id: "u1", type: "human", name: "User asks"}
  - {id: "a1", type: "agent", name: "Agent works"}
edges:
  - {from: "u1", to: "a1", mode: "sequential"}
"""


_CAPTURED_OSOPLOG = """\
osoplog_version: "1.0"
run_id: "fake-run-1"
workflow_id: "captured-imitation-test"
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
    outputs: {user_prompt: "echo hello and read the readme"}
  - node_id: "a1"
    node_type: "agent"
    status: "COMPLETED"
    started_at: "2026-04-17T00:00:00Z"
    ended_at: "2026-04-17T00:00:30Z"
    duration_ms: 30000
    tool_calls:
      - {tool: "Bash", started_at: "2026-04-17T00:00:01Z", input: {command: "echo hello"}, output: "hello"}
      - {tool: "Read", started_at: "2026-04-17T00:00:02Z", input: {file_path: "README.md"}, output: "# project"}
"""


def test_execute_workflow_dispatches_agent_with_reference_log(tmp_path):
    osop = tmp_path / "captured.osop.yaml"
    osop.write_text(_CAPTURED_OSOP)
    log_ref = tmp_path / "captured.osoplog.yaml"
    log_ref.write_text(_CAPTURED_OSOPLOG)

    fake = AgentInvocationResult(
        status=STATUS_COMPLETED,
        cost_usd=0.01,
        tokens_input=100,
        tokens_output=50,
        model="opus-4-7",
        result_text="echoed hello and read readme",
        num_turns=2,
    )

    workflow = yaml.safe_load(osop.read_text())
    live_log = LiveLog.start(osop, output_dir=tmp_path / "out")

    with patch("osop.replayer.invoke_claude_p", return_value=fake) as mocked:
        summary = execute_workflow(
            workflow,
            live_log,
            allow_exec=False,
            interactive=False,
            continue_on_error=False,
            confirm_destructive=lambda c: False,
            osop_path=osop,
        )
    live_log.finish(summary["status"])

    assert mocked.called, "agent node should have invoked claude -p"
    sent_prompt = mocked.call_args.kwargs["prompt"]
    assert "echo hello and read the readme" in sent_prompt
    assert "Bash: command=echo hello" in sent_prompt

    assert summary["status"] == "COMPLETED"
    # u1 is human in non-interactive mode → SKIPPED; a1 mocked → COMPLETED
    assert summary["counts"]["COMPLETED"] == 1, f"counts: {summary['counts']}"
    assert summary["counts"]["SKIPPED"] == 1, f"counts: {summary['counts']}"


def test_execute_workflow_skips_agent_without_reference_log(tmp_path):
    osop = tmp_path / "lonely.osop.yaml"
    osop.write_text(_CAPTURED_OSOP)
    # No paired .osoplog.yaml on disk

    workflow = yaml.safe_load(osop.read_text())
    live_log = LiveLog.start(osop, output_dir=tmp_path / "out")

    with patch("osop.replayer.invoke_claude_p") as mocked:
        summary = execute_workflow(
            workflow,
            live_log,
            allow_exec=False,
            interactive=False,
            continue_on_error=False,
            confirm_destructive=lambda c: False,
            osop_path=osop,
        )
    live_log.finish(summary["status"])

    assert not mocked.called, "should not invoke claude -p without reference log"
    assert summary["counts"]["SKIPPED"] >= 1


def test_execute_workflow_no_agent_flag_skips_agents(tmp_path):
    osop = tmp_path / "skip.osop.yaml"
    osop.write_text(_CAPTURED_OSOP)
    log_ref = tmp_path / "skip.osoplog.yaml"
    log_ref.write_text(_CAPTURED_OSOPLOG)

    workflow = yaml.safe_load(osop.read_text())
    live_log = LiveLog.start(osop, output_dir=tmp_path / "out")

    with patch("osop.replayer.invoke_claude_p") as mocked:
        summary = execute_workflow(
            workflow,
            live_log,
            allow_exec=False,
            interactive=False,
            continue_on_error=False,
            confirm_destructive=lambda c: False,
            osop_path=osop,
            skip_agents=True,
        )
    live_log.finish(summary["status"])

    assert not mocked.called
    assert summary["counts"]["SKIPPED"] >= 1


# ---------------------------------------------------------------------------
# Live claude -p smoke test (skipped when not installed)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="claude CLI not on PATH; skipping live agent invocation smoke test",
)
def test_live_claude_p_smoke(tmp_path):
    """Real claude -p invocation. Even a trivial prompt costs ~$0.15-$0.30
    because Claude Code loads its full system context every call. We verify
    the wrapper produces a structured result; both COMPLETED and the
    expected error states (BUDGET_EXCEEDED, AUTH_FAILED) prove the parser
    works and the subprocess plumbing is healthy.
    """
    from osop.agent_invoker import invoke_claude_p

    res = invoke_claude_p(
        prompt="Reply with the single word: PONG",
        max_budget_usd=1.00,
        max_turns=1,
        allowed_tools=[],
        timeout_seconds=120,
    )
    assert res.status in ("COMPLETED", "BUDGET_EXCEEDED", "AUTH_FAILED"), \
        f"unexpected status {res.status}: {res.error}"
    # Whatever happened, we got a parsed JSON back and structured fields populated
    assert res.raw_json is not None or res.error
    if res.status == "COMPLETED":
        assert res.cost_usd >= 0
        assert res.num_turns >= 1
