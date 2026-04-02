"""Tests for osop.ir.models — Internal Representation models."""
import pytest
from osop.ir.models import NodeSpec, EdgeSpec, WorkflowGraph, build_ir


# ---------------------------------------------------------------------------
# NodeSpec
# ---------------------------------------------------------------------------

class TestNodeSpec:
    def test_create_minimal_node(self):
        node = NodeSpec(id="n1", type="agent", purpose="Do stuff")
        assert node.id == "n1"
        assert node.type == "agent"
        assert node.purpose == "Do stuff"
        assert node.name == ""
        assert node.role == ""
        assert node.runtime == {}
        assert node.inputs == []
        assert node.outputs == []
        assert node.success_criteria == []
        assert node.failure_modes == []
        assert node.handoff == {}
        assert node.security == {}
        assert node.raw == {}

    def test_create_full_node(self):
        node = NodeSpec(
            id="n2",
            type="human",
            purpose="Review PR",
            name="PR Review",
            role="reviewer",
            runtime={"timeout": 300},
            inputs=[{"name": "pr_url"}],
            outputs=[{"name": "approved"}],
            success_criteria=["PR reviewed"],
            failure_modes=["timeout"],
            handoff={"summary_for_next_node": "Review done"},
            security={"requires_auth": True},
            raw={"id": "n2", "type": "human"},
        )
        assert node.name == "PR Review"
        assert node.role == "reviewer"
        assert node.runtime["timeout"] == 300
        assert len(node.inputs) == 1
        assert node.success_criteria == ["PR reviewed"]


# ---------------------------------------------------------------------------
# EdgeSpec
# ---------------------------------------------------------------------------

class TestEdgeSpec:
    def test_create_minimal_edge(self):
        edge = EdgeSpec(from_node="a", to_node="b")
        assert edge.from_node == "a"
        assert edge.to_node == "b"
        assert edge.mode == "sequential"
        assert edge.when == ""
        assert edge.label == ""

    def test_create_conditional_edge(self):
        edge = EdgeSpec(from_node="a", to_node="b", mode="conditional", when="x > 0")
        assert edge.mode == "conditional"
        assert edge.when == "x > 0"

    def test_create_edge_with_label(self):
        edge = EdgeSpec(from_node="a", to_node="b", mode="fallback", label="Retry")
        assert edge.label == "Retry"


# ---------------------------------------------------------------------------
# WorkflowGraph
# ---------------------------------------------------------------------------

class TestWorkflowGraph:
    def _make_graph(self):
        nodes = [
            NodeSpec(id="a", type="agent", purpose="First"),
            NodeSpec(id="b", type="human", purpose="Second"),
            NodeSpec(id="c", type="cli", purpose="Third"),
        ]
        edges = [
            EdgeSpec(from_node="a", to_node="b"),
            EdgeSpec(from_node="b", to_node="c"),
            EdgeSpec(from_node="a", to_node="c", mode="parallel"),
        ]
        return WorkflowGraph(
            id="test-graph",
            name="Test Graph",
            version="1.0",
            nodes=nodes,
            edges=edges,
        )

    def test_graph_creation(self):
        g = self._make_graph()
        assert g.id == "test-graph"
        assert g.name == "Test Graph"
        assert len(g.nodes) == 3
        assert len(g.edges) == 3

    def test_get_node_found(self):
        g = self._make_graph()
        node = g.get_node("b")
        assert node is not None
        assert node.id == "b"
        assert node.type == "human"

    def test_get_node_not_found(self):
        g = self._make_graph()
        assert g.get_node("nonexistent") is None

    def test_outgoing_edges(self):
        g = self._make_graph()
        edges = g.outgoing_edges("a")
        assert len(edges) == 2
        targets = {e.to_node for e in edges}
        assert targets == {"b", "c"}

    def test_outgoing_edges_none(self):
        g = self._make_graph()
        edges = g.outgoing_edges("c")
        assert edges == []

    def test_outgoing_edges_single(self):
        g = self._make_graph()
        edges = g.outgoing_edges("b")
        assert len(edges) == 1
        assert edges[0].to_node == "c"

    def test_empty_graph(self):
        g = WorkflowGraph(id="empty", name="Empty", version="1.0")
        assert g.nodes == []
        assert g.edges == []
        assert g.get_node("x") is None
        assert g.outgoing_edges("x") == []


# ---------------------------------------------------------------------------
# build_ir
# ---------------------------------------------------------------------------

class TestBuildIR:
    def test_build_from_minimal_dict(self):
        wf = {
            "osop_version": "1.0",
            "id": "ir-test",
            "name": "IR Test",
            "nodes": [
                {"id": "a", "type": "agent", "purpose": "Do stuff"},
            ],
            "edges": [{"from": "a", "to": "a"}],
        }
        ir = build_ir(wf)
        assert ir.id == "ir-test"
        assert ir.name == "IR Test"
        assert ir.version == "1.0"
        assert len(ir.nodes) == 1
        assert len(ir.edges) == 1

    def test_build_preserves_raw(self):
        wf = {
            "osop_version": "1.0",
            "id": "raw-test",
            "name": "Raw Test",
            "nodes": [{"id": "n", "type": "cli", "purpose": "test", "custom": "value"}],
            "edges": [],
        }
        ir = build_ir(wf)
        assert ir.raw == wf
        assert ir.nodes[0].raw["custom"] == "value"

    def test_build_edge_modes(self):
        wf = {
            "osop_version": "1.0",
            "id": "edge-modes",
            "name": "Edge Modes",
            "nodes": [
                {"id": "a", "type": "agent", "purpose": "x"},
                {"id": "b", "type": "agent", "purpose": "y"},
            ],
            "edges": [
                {"from": "a", "to": "b", "mode": "conditional", "when": "flag == true", "label": "Conditional"},
            ],
        }
        ir = build_ir(wf)
        edge = ir.edges[0]
        assert edge.mode == "conditional"
        assert edge.when == "flag == true"
        assert edge.label == "Conditional"

    def test_build_defaults_for_missing_fields(self):
        wf = {
            "osop_version": "1.0",
            "id": "defaults",
            "name": "Defaults",
            "nodes": [{"id": "a"}],  # missing type, purpose
            "edges": [{"from": "a", "to": "a"}],  # missing mode
        }
        ir = build_ir(wf)
        node = ir.nodes[0]
        assert node.type == "system"  # default
        assert node.purpose == ""
        assert node.name == "a"  # falls back to id
        edge = ir.edges[0]
        assert edge.mode == "sequential"  # default

    def test_build_with_schemas_and_tests(self):
        wf = {
            "osop_version": "1.0",
            "id": "extras",
            "name": "Extras",
            "nodes": [],
            "edges": [],
            "schemas": {"input": {"type": "object"}},
            "message_contracts": [{"from": "a", "to": "b", "schema": "input"}],
            "tests": [{"id": "t1", "type": "unit"}],
        }
        ir = build_ir(wf)
        assert ir.schemas == {"input": {"type": "object"}}
        assert len(ir.message_contracts) == 1
        assert len(ir.tests) == 1

    def test_build_node_with_all_fields(self):
        wf = {
            "osop_version": "1.0",
            "id": "full-node",
            "name": "Full Node",
            "nodes": [
                {
                    "id": "n1",
                    "type": "agent",
                    "purpose": "Full test",
                    "name": "Full Node",
                    "role": "tester",
                    "runtime": {"model": "gpt-4"},
                    "inputs": [{"name": "query"}],
                    "outputs": [{"name": "result"}],
                    "success_criteria": ["Output produced"],
                    "failure_modes": ["timeout", "error"],
                    "handoff": {"summary_for_next_node": "Done"},
                    "security": {"pii": False},
                }
            ],
            "edges": [],
        }
        ir = build_ir(wf)
        node = ir.nodes[0]
        assert node.name == "Full Node"
        assert node.role == "tester"
        assert node.runtime["model"] == "gpt-4"
        assert len(node.inputs) == 1
        assert len(node.outputs) == 1
        assert node.success_criteria == ["Output produced"]
        assert len(node.failure_modes) == 2
        assert node.handoff["summary_for_next_node"] == "Done"
        assert node.security["pii"] is False

    def test_build_empty_workflow(self):
        wf = {}
        ir = build_ir(wf)
        assert ir.id == ""
        assert ir.name == ""
        assert ir.version == "1.0"
        assert ir.nodes == []
        assert ir.edges == []
