"""Debug view renderer — show run state and node details."""
from rich.console import Console
from rich.table import Table
from osop.ir.models import build_ir


def render_debug(workflow: dict, console: Console, run_id: str = "—") -> None:
    ir = build_ir(workflow)
    table = Table(title=f"Debug View — {ir.name}  run: {run_id}", header_style="bold")
    table.add_column("Node", style="bold", width=20)
    table.add_column("Type", width=10)
    table.add_column("Status", width=10)
    table.add_column("Inputs")
    table.add_column("Outputs")
    for node in ir.nodes:
        ins = ", ".join(i["name"] for i in node.inputs) if node.inputs else "—"
        outs = ", ".join(o["name"] for o in node.outputs) if node.outputs else "—"
        table.add_row(node.id, node.type, "[dim]pending[/dim]", ins, outs)
    console.print(table)
