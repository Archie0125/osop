"""Role view renderer — show responsibilities by role."""
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from osop.ir.models import build_ir


def render_role(workflow: dict, console: Console) -> None:
    """Render a workflow grouped by role/owner."""
    ir = build_ir(workflow)

    table = Table(title=f"Role View — {ir.name}", show_header=True, header_style="bold")
    table.add_column("Role", style="bold cyan", width=14)
    table.add_column("Node ID", style="dim", width=20)
    table.add_column("Type", width=10)
    table.add_column("Purpose")

    role_map: dict[str, list] = {}
    for node in ir.nodes:
        role = node.role or node.type
        role_map.setdefault(role, []).append(node)

    for role in sorted(role_map):
        nodes = role_map[role]
        for i, node in enumerate(nodes):
            table.add_row(
                role if i == 0 else "",
                node.id,
                node.type,
                node.purpose[:60],
            )

    console.print(table)
