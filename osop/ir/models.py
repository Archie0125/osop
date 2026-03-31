"""OSOP Intermediate Representation models."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NodeSpec:
    id: str
    type: str
    purpose: str
    name: str = ""
    role: str = ""
    runtime: dict = field(default_factory=dict)
    inputs: list = field(default_factory=list)
    outputs: list = field(default_factory=list)
    success_criteria: list = field(default_factory=list)
    failure_modes: list = field(default_factory=list)
    handoff: dict = field(default_factory=dict)
    security: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


@dataclass
class EdgeSpec:
    from_node: str
    to_node: str
    mode: str = "sequential"
    when: str = ""
    label: str = ""


@dataclass
class WorkflowGraph:
    id: str
    name: str
    version: str
    nodes: list[NodeSpec] = field(default_factory=list)
    edges: list[EdgeSpec] = field(default_factory=list)
    schemas: dict = field(default_factory=dict)
    message_contracts: list = field(default_factory=list)
    tests: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def get_node(self, node_id: str) -> NodeSpec | None:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def outgoing_edges(self, node_id: str) -> list[EdgeSpec]:
        return [e for e in self.edges if e.from_node == node_id]


def build_ir(workflow: dict) -> WorkflowGraph:
    """Convert a raw workflow dict into a WorkflowGraph IR."""
    nodes = [
        NodeSpec(
            id=n["id"],
            type=n.get("type", "system"),
            purpose=n.get("purpose", ""),
            name=n.get("name", n["id"]),
            role=n.get("role", ""),
            runtime=n.get("runtime", {}),
            inputs=n.get("inputs", []),
            outputs=n.get("outputs", []),
            success_criteria=n.get("success_criteria", []),
            failure_modes=n.get("failure_modes", []),
            handoff=n.get("handoff", {}),
            security=n.get("security", {}),
            raw=n,
        )
        for n in workflow.get("nodes", [])
    ]
    edges = [
        EdgeSpec(
            from_node=e["from"],
            to_node=e["to"],
            mode=e.get("mode", "sequential"),
            when=e.get("when", ""),
            label=e.get("label", ""),
        )
        for e in workflow.get("edges", [])
    ]
    return WorkflowGraph(
        id=workflow.get("id", ""),
        name=workflow.get("name", ""),
        version=workflow.get("osop_version", "1.0"),
        nodes=nodes,
        edges=edges,
        schemas=workflow.get("schemas", {}),
        message_contracts=workflow.get("message_contracts", []),
        tests=workflow.get("tests", []),
        raw=workflow,
    )
