"""Story view renderer — human-readable narrative of a workflow."""
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from osop.ir.models import build_ir

NODE_EMOJI = {
    "human": "[bold cyan]Human[/bold cyan]",
    "agent": "[bold magenta]Agent[/bold magenta]",
    "api": "[bold blue]API[/bold blue]",
    "cli": "[bold yellow]CLI[/bold yellow]",
    "db": "[bold green]DB[/bold green]",
    "git": "[bold white]Git[/bold white]",
    "docker": "[bold blue]Docker[/bold blue]",
    "cicd": "[bold red]CI/CD[/bold red]",
    "mcp": "[bold purple]MCP[/bold purple]",
    "system": "[dim]System[/dim]",
}


def render_story(workflow: dict, console: Console) -> None:
    """Render a workflow as a human-readable story."""
    ir = build_ir(workflow)

    header = Text()
    header.append(ir.name, style="bold")
    if workflow.get("description"):
        header.append(f"\n{workflow['description']}", style="dim")
    header.append(f"\n\nosop_version: {ir.version}  |  id: {ir.id}  |  nodes: {len(ir.nodes)}", style="dim")

    console.print(Panel(header, title="Story View", border_style="cyan"))

    console.print()
    console.print("[bold]Workflow Steps[/bold]")
    console.print()

    # Simple topological walk (sequential for now)
    visited = set()
    def walk(node_id: str, depth: int = 0):
        if node_id in visited:
            return
        visited.add(node_id)
        node = ir.get_node(node_id)
        if not node:
            return
        indent = "  " * depth
        type_label = NODE_EMOJI.get(node.type, node.type)
        console.print(f"{indent}[dim]{len(visited):02d}.[/dim] {type_label}  [bold]{node.name or node.id}[/bold]")
        console.print(f"{indent}    [dim]{node.purpose}[/dim]")
        if node.success_criteria:
            console.print(f"{indent}    [green]Success:[/green] {node.success_criteria[0]}")
        if node.handoff.get("summary_for_next_node"):
            summary = str(node.handoff["summary_for_next_node"]).strip()[:80]
            console.print(f"{indent}    [dim]Handoff:[/dim] {summary}...")
        console.print()
        for edge in ir.outgoing_edges(node_id):
            label = ""
            if edge.mode == "conditional":
                label = f" [yellow](if {edge.when})[/yellow]"
            elif edge.mode != "sequential":
                label = f" [dim]({edge.mode})[/dim]"
            console.print(f"{indent}    [dim]→{label}[/dim] {edge.to_node}")
            walk(edge.to_node, depth + 1)

    # Start from nodes with no incoming edges
    all_targets = {e.to_node for e in ir.edges}
    starts = [n.id for n in ir.nodes if n.id not in all_targets]
    if not starts:
        starts = [ir.nodes[0].id] if ir.nodes else []

    for start in starts:
        walk(start)

    # Summary
    roles = set(n.role for n in ir.nodes if n.role)
    if roles:
        console.print(f"[dim]Roles involved: {', '.join(sorted(roles))}[/dim]")
    views = workflow.get("views", [])
    if views:
        console.print(f"[dim]Available views: {', '.join(views)}[/dim]")
