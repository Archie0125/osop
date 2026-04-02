"""Tests for osop.renderers — story and role view rendering."""
import pytest
from io import StringIO
from rich.console import Console
from osop.renderers.story import render_story
from osop.renderers.role import render_role


def _make_console() -> tuple[Console, StringIO]:
    """Create a Console that writes to a StringIO buffer."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=120)
    return console, buf


def _minimal_workflow(**overrides):
    base = {
        "osop_version": "1.0",
        "id": "test_wf",
        "name": "Test Workflow",
        "nodes": [
            {"id": "node_a", "type": "agent", "purpose": "Do something"},
            {"id": "node_b", "type": "human", "purpose": "Review it"},
        ],
        "edges": [{"from": "node_a", "to": "node_b"}],
    }
    base.update(overrides)
    return base


def _complex_workflow():
    return {
        "osop_version": "1.0",
        "id": "complex",
        "name": "Complex Workflow",
        "description": "Multi-role workflow with conditional edges.",
        "nodes": [
            {"id": "plan", "type": "agent", "purpose": "Plan work", "role": "planner",
             "success_criteria": ["Plan created"],
             "handoff": {"summary_for_next_node": "Plan is ready for execution"}},
            {"id": "review", "type": "human", "purpose": "Review plan", "role": "reviewer"},
            {"id": "exec", "type": "cli", "purpose": "Execute plan", "role": "executor"},
        ],
        "edges": [
            {"from": "plan", "to": "review", "mode": "sequential"},
            {"from": "review", "to": "exec", "mode": "conditional", "when": "approved"},
        ],
    }


# ---------------------------------------------------------------------------
# Story view
# ---------------------------------------------------------------------------

class TestStoryRenderer:
    def test_story_renders_without_error(self):
        console, buf = _make_console()
        render_story(_minimal_workflow(), console)
        output = buf.getvalue()
        assert len(output) > 0

    def test_story_shows_workflow_name(self):
        console, buf = _make_console()
        render_story(_minimal_workflow(), console)
        output = buf.getvalue()
        assert "Test Workflow" in output

    def test_story_shows_node_ids(self):
        console, buf = _make_console()
        render_story(_minimal_workflow(), console)
        output = buf.getvalue()
        assert "node_a" in output
        assert "node_b" in output

    def test_story_shows_description(self):
        console, buf = _make_console()
        wf = _minimal_workflow(description="A test description")
        render_story(wf, console)
        output = buf.getvalue()
        assert "test description" in output

    def test_story_shows_success_criteria(self):
        console, buf = _make_console()
        render_story(_complex_workflow(), console)
        output = buf.getvalue()
        assert "Plan created" in output

    def test_story_shows_handoff(self):
        console, buf = _make_console()
        render_story(_complex_workflow(), console)
        output = buf.getvalue()
        assert "Handoff" in output

    def test_story_shows_conditional_edge(self):
        console, buf = _make_console()
        render_story(_complex_workflow(), console)
        output = buf.getvalue()
        assert "approved" in output

    def test_story_shows_roles(self):
        console, buf = _make_console()
        render_story(_complex_workflow(), console)
        output = buf.getvalue()
        assert "Roles involved" in output

    def test_story_single_node(self):
        console, buf = _make_console()
        wf = _minimal_workflow(
            nodes=[{"id": "solo", "type": "agent", "purpose": "Only node"}],
            edges=[{"from": "solo", "to": "solo"}],
        )
        render_story(wf, console)
        output = buf.getvalue()
        assert "solo" in output

    def test_story_node_type_labels(self):
        """Agent and Human type labels should appear."""
        console, buf = _make_console()
        render_story(_minimal_workflow(), console)
        output = buf.getvalue()
        assert "Agent" in output
        assert "Human" in output


# ---------------------------------------------------------------------------
# Role view
# ---------------------------------------------------------------------------

class TestRoleRenderer:
    def test_role_renders_without_error(self):
        console, buf = _make_console()
        render_role(_minimal_workflow(), console)
        output = buf.getvalue()
        assert len(output) > 0

    def test_role_shows_workflow_name(self):
        console, buf = _make_console()
        render_role(_minimal_workflow(), console)
        output = buf.getvalue()
        assert "Test Workflow" in output

    def test_role_shows_node_ids(self):
        console, buf = _make_console()
        render_role(_minimal_workflow(), console)
        output = buf.getvalue()
        assert "node_a" in output
        assert "node_b" in output

    def test_role_groups_by_role(self):
        console, buf = _make_console()
        render_role(_complex_workflow(), console)
        output = buf.getvalue()
        assert "planner" in output
        assert "reviewer" in output
        assert "executor" in output

    def test_role_shows_purpose(self):
        console, buf = _make_console()
        render_role(_complex_workflow(), console)
        output = buf.getvalue()
        assert "Plan work" in output

    def test_role_falls_back_to_type_when_no_role(self):
        """When nodes have no role field, the type is used as the grouping key."""
        console, buf = _make_console()
        render_role(_minimal_workflow(), console)
        output = buf.getvalue()
        assert "agent" in output
        assert "human" in output
