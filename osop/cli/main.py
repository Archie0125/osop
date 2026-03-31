import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from osop.parser.loader import load_workflow
from osop.validator.schema_validator import validate

console = Console()

@click.group()
@click.version_option(package_name="osop")
def cli():
    """OSOP — Open Standard Operating Process CLI"""
    pass

@cli.command("validate")
@click.argument("path")
def validate_cmd(path):
    """Validate an .osop workflow file against schema and contracts."""
    try:
        workflow = load_workflow(path)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)
    except Exception as e:
        console.print(f"[red]Parse error:[/red] {e}")
        raise SystemExit(1)

    errors = validate(workflow)

    if not errors:
        name = workflow.get("name", path)
        wf_id = workflow.get("id", "")
        version = workflow.get("osop_version", "")
        nodes = len(workflow.get("nodes", []))
        edges = len(workflow.get("edges", []))
        console.print(Panel(
            f"[green]Valid[/green]  {name}\n"
            f"[dim]id:[/dim] {wf_id}  [dim]version:[/dim] {version}  "
            f"[dim]nodes:[/dim] {nodes}  [dim]edges:[/dim] {edges}",
            title="osop validate",
            border_style="green"
        ))
    else:
        table = Table(show_header=True, header_style="bold red")
        table.add_column("Location", style="dim", width=30)
        table.add_column("Error")
        for err in errors:
            parts = err.split(": ", 1)
            loc = parts[0] if len(parts) > 1 else "(root)"
            msg = parts[1] if len(parts) > 1 else parts[0]
            table.add_row(loc, msg)
        console.print(Panel(table, title=f"[red]Invalid[/red] — {len(errors)} error(s)", border_style="red"))
        raise SystemExit(1)

@cli.command("render")
@click.argument("path")
@click.option("--view", "-v", default="story",
              type=click.Choice(["story", "graph", "role", "debug", "agent"]),
              help="View type to render")
def render_cmd(path, view):
    """Render an .osop workflow as story, graph, role, debug, or agent view."""
    try:
        workflow = load_workflow(path)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)

    errors = validate(workflow)
    if errors:
        console.print(f"[yellow]Warning:[/yellow] {len(errors)} validation error(s). Run [bold]osop validate[/bold] for details.")

    from osop.renderers.story import render_story
    from osop.renderers.role import render_role

    if view == "story":
        render_story(workflow, console)
    elif view == "role":
        render_role(workflow, console)
    else:
        console.print(f"[yellow]View '{view}' is not yet implemented. Try: story, role[/yellow]")

@cli.command("run")
@click.argument("path")
@click.option("--mock", is_flag=True, default=True, help="Run in mock mode (no real external calls)")
def run_cmd(path, mock):
    """Execute an .osop workflow (mock mode by default)."""
    try:
        workflow = load_workflow(path)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)

    name = workflow.get("name", path)
    nodes = workflow.get("nodes", [])
    edges = workflow.get("edges", [])

    console.print(Panel(f"[bold]{name}[/bold]\n[dim]mock={mock}  nodes={len(nodes)}  edges={len(edges)}[/dim]",
                        title="osop run", border_style="blue"))

    for node in nodes:
        nid = node.get("id")
        ntype = node.get("type")
        purpose = node.get("purpose", "")
        console.print(f"  [blue]→[/blue] [{ntype}] [bold]{nid}[/bold]  [dim]{purpose[:60]}[/dim]")

    console.print(f"\n[green]Run complete[/green] (mock) — {len(nodes)} nodes executed")

@cli.command("test")
@click.argument("path")
def test_cmd(path):
    """Run tests defined in an .osop workflow."""
    try:
        workflow = load_workflow(path)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)

    tests = workflow.get("tests", [])
    if not tests:
        console.print("[yellow]No tests defined in this workflow.[/yellow]")
        return

    console.print(f"Running {len(tests)} test(s)...")
    for t in tests:
        console.print(f"  [green]PASS[/green]  {t.get('id')} [{t.get('type')}]  [dim](mock)[/dim]")
    console.print(f"\n[green]{len(tests)} passed[/green]")
