import os
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from osop.parser.loader import load_workflow
from osop.validator.schema_validator import validate

console = Console()


def _find_mcp_tools():
    """Find osop-mcp tools directory and add to sys.path. Returns True if found."""
    for search_path in [
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "osop-mcp"),
        os.path.join(os.path.expanduser("~"), "Desktop", "osop", "osop-mcp"),
        os.path.join(os.getcwd(), "..", "osop-mcp"),
        os.path.join(os.getcwd(), "osop-mcp"),
    ]:
        tools_path = os.path.join(search_path, "tools")
        if os.path.isdir(tools_path):
            if search_path not in sys.path:
                sys.path.insert(0, search_path)
            return True
    return False


@click.group()
@click.version_option(package_name="osop")
def cli():
    """OSOP — Record what AI does. Repeat it for free.

    The two pillars (this is the product):
      osop log       RECORD — synthesize .osop + .osoplog from a Claude Code transcript
      osop replay    REPEAT — re-execute a captured .osop with a fresh AI agent

    Supporting commands (make the loop reliable):
      osop init      One-step project setup (sessions/ + CLAUDE.md)
      osop validate  Schema check .osop or .osoplog
      osop record    Full executor (cost limits, risk_assess) — for hand-authored .osop
      osop diff      Side-by-side compare two .osop or two .osoplog files
      osop optimize  Synthesize a better .osop from multiple execution logs
      osop view      Render .sop into standalone HTML for browsing

    The closed loop:
      osop log → osop replay → more .osoplog → osop diff → osop optimize → osop replay …
    """
    pass


# ---------------------------------------------------------------------------
# osop init
# ---------------------------------------------------------------------------

_CLAUDE_MD_OSOP_SECTION = """\
## OSOP — Session Logging

This project uses OSOP (Open Standard Operating Process) to record AI agent workflows.

### Eight CLI commands

- `osop init` — One-step setup: create sessions/ + add this OSOP section to CLAUDE.md
- `osop validate <file>` — Check .osop or .osoplog against schema
- `osop record <file.osop>` — Execute workflow via full executor (write .osoplog at end)
- `osop replay <file.osop>` — Execute workflow with live .osoplog streaming (cli + human in v1)
- `osop log [session-id]` — Synthesize .osop + .osoplog from a Claude Code transcript (real evidence, no LLM self-report)
- `osop diff <a> <b>` — Compare two .osop or .osoplog files
- `osop optimize <logs...>` — Synthesize better .osop from execution logs
- `osop view <file.sop>` — Render .sop into standalone HTML

### When to produce session logs

After completing a multi-step task (3+ distinct steps), invoke `/osop-log` (which calls
`osop log` under the hood). This produces, from the actual transcript:

1. `sessions/YYYY-MM-DD-<short-desc>.osop.yaml` — workflow definition
2. `sessions/YYYY-MM-DD-<short-desc>.osoplog.yaml` — execution record

Optional: enable the SessionEnd hook at `~/.claude/hooks/osop-session-end.sh`
to auto-log every session.

### OSOP Core schema

- **4 node types only:** `agent`, `api`, `cli`, `human`
- **4 edge modes only:** `sequential`, `parallel`, `conditional`, `fallback`

### Node type mapping

| Claude Code action | type |
|---|---|
| Read/explore/edit/write files, analyze, plan, spawn sub-agent | `agent` |
| Shell commands, tests, git, builds | `cli` |
| Web fetch, HTTP requests | `api` |
| Ask user, user reviews | `human` |

### Viewing

Drop .sop + .osop + .osoplog files at https://osop-editor.vercel.app — or run `osop view`.
"""


@cli.command("init")
def init_cmd():
    """Initialize OSOP in the current project.

    Creates sessions/ directory and adds OSOP section to CLAUDE.md.
    Run once per project.
    """
    cwd = Path.cwd()
    sessions_dir = cwd / "sessions"
    claude_md = cwd / "CLAUDE.md"

    # 1. Create sessions/
    if sessions_dir.exists():
        sessions_status = "already exists"
    else:
        sessions_dir.mkdir(exist_ok=True)
        sessions_status = "created"

    # 2. Update or create CLAUDE.md
    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        if "## OSOP" in content or "OSOP — Session Logging" in content:
            claude_md_status = "already has OSOP section (skipped)"
        else:
            claude_md.write_text(content.rstrip() + "\n\n" + _CLAUDE_MD_OSOP_SECTION, encoding="utf-8")
            claude_md_status = "OSOP section appended"
    else:
        claude_md.write_text(_CLAUDE_MD_OSOP_SECTION, encoding="utf-8")
        claude_md_status = "created with OSOP section"

    console.print(Panel(
        f"[green]OSOP initialized[/green] in {cwd}\n"
        f"  sessions/  {sessions_status}\n"
        f"  CLAUDE.md  {claude_md_status}\n\n"
        f"Next: run a task, then use [cyan]/osop-log[/cyan] in Claude Code to record it.",
        title="osop init",
        border_style="green",
    ))


# ---------------------------------------------------------------------------
# osop validate
# ---------------------------------------------------------------------------

@cli.command("validate")
@click.argument("path")
@click.option("--schema", "schema_variant", type=click.Choice(["core", "full"]), default="core",
              help="Schema variant: 'core' (4 node types) or 'full' (all types).")
def validate_cmd(path, schema_variant):
    """Validate an .osop or .osoplog file against schema.

    Examples:
      osop validate my-workflow.osop.yaml
      osop validate session.osoplog.yaml
    """
    is_log = path.endswith(".osoplog.yaml") or path.endswith(".osoplog.yml")

    if is_log:
        _validate_log(path)
    else:
        _validate_workflow(path, schema_variant)


def _validate_workflow(path, schema_variant):
    try:
        workflow = load_workflow(path)
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] File not found: {path}")
        console.print(f"  Fix: Check the path and try again.")
        raise SystemExit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] Cannot parse YAML: {e}")
        console.print(f"  Fix: Check YAML syntax at https://yaml-online-parser.appspot.com/")
        raise SystemExit(1)

    errors = validate(workflow, schema_variant=schema_variant)

    if not errors:
        name = workflow.get("name", path)
        nodes = len(workflow.get("nodes", []))
        edges = len(workflow.get("edges", []))
        console.print(Panel(
            f"[green]Valid[/green]  {name}\n"
            f"[dim]nodes:[/dim] {nodes}  [dim]edges:[/dim] {edges}  [dim]schema:[/dim] {schema_variant}",
            title="osop validate", border_style="green"
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
        console.print(f"  Fix: Edit {path} to fix the errors above.")
        console.print(f"  Docs: https://github.com/Archie0125/osop-spec")
        raise SystemExit(1)


def _validate_log(path):
    from pathlib import Path
    import yaml

    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] File not found: {path}")
        console.print(f"  Fix: Check the path and try again.")
        raise SystemExit(1)

    try:
        data = yaml.safe_load(raw)
    except Exception as e:
        console.print(f"[red]Error:[/red] Cannot parse YAML: {e}")
        console.print(f"  Fix: Check YAML syntax.")
        raise SystemExit(1)

    if not isinstance(data, dict):
        console.print("[red]Error:[/red] osoplog must be a YAML mapping.")
        raise SystemExit(1)

    errors = []
    warnings = []

    for field in ["osoplog_version", "run_id", "workflow_id", "status", "started_at", "ended_at", "duration_ms"]:
        if field not in data:
            errors.append(f"Missing required field: {field}")

    valid_statuses = {"COMPLETED", "FAILED", "TIMEOUT", "COST_LIMIT", "BLOCKED", "DRY_RUN"}
    if data.get("status") and data["status"] not in valid_statuses:
        warnings.append(f"Unexpected status: {data['status']} (expected one of {', '.join(sorted(valid_statuses))})")

    records = data.get("node_records", [])
    if not isinstance(records, list):
        errors.append("node_records must be a list")
    else:
        for i, rec in enumerate(records):
            if isinstance(rec, dict) and "node_id" not in rec:
                errors.append(f"node_records[{i}]: missing node_id")

    dur = data.get("duration_ms")
    if dur is not None and not isinstance(dur, (int, float)):
        errors.append(f"duration_ms must be a number (got {type(dur).__name__})")

    if errors:
        console.print(f"[red]Invalid[/red] osoplog: {path}")
        for e in errors:
            console.print(f"  [red]Error:[/red] {e}")
        for w in warnings:
            console.print(f"  [yellow]Warning:[/yellow] {w}")
        console.print(f"  Fix: Ensure required fields: osoplog_version, run_id, workflow_id, status, started_at, ended_at, duration_ms")
        raise SystemExit(1)
    else:
        wf_id = data.get("workflow_id", "?")
        status = data.get("status", "?")
        dur_ms = data.get("duration_ms", 0)
        console.print(f"[green]Valid[/green] osoplog: {path}")
        console.print(f"  Workflow: {wf_id} | Status: {status} | Nodes: {len(records)} | Duration: {dur_ms}ms")
        for w in warnings:
            console.print(f"  [yellow]Warning:[/yellow] {w}")


# ---------------------------------------------------------------------------
# osop record
# ---------------------------------------------------------------------------

@cli.command("record")
@click.argument("path")
@click.option("--allow-exec", is_flag=True, default=False, help="Allow CLI nodes to execute shell commands")
@click.option("--dry-run", is_flag=True, default=False, help="Simulate without executing")
@click.option("--interactive", is_flag=True, default=False, help="Enable human node input via stdin")
@click.option("--max-cost", type=float, default=1.0, help="Maximum LLM cost in USD (default: $1.00)")
@click.option("--timeout", type=int, default=300, help="Maximum execution time in seconds")
@click.option("--mock", is_flag=True, default=False, help="Run in mock mode (no real execution)")
@click.option("-o", "--output", "log_path", type=str, default=None,
              help="Write .osoplog.yaml to this path (default: <id>.osoplog.yaml)")
def record_cmd(path, allow_exec, dry_run, interactive, max_cost, timeout, mock, log_path):
    """Execute an .osop workflow and produce an .osoplog execution record.

    Examples:
      osop record my-workflow.osop.yaml
      osop record deploy.osop.yaml --allow-exec -o deploy.osoplog.yaml
      osop record review.osop.yaml --dry-run
      osop record test.osop.yaml --mock
    """
    # Validate first
    try:
        workflow = load_workflow(path)
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] File not found: {path}")
        console.print(f"  Fix: Check the path. Use 'osop validate' to check format.")
        raise SystemExit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] Cannot parse YAML: {e}")
        console.print(f"  Fix: Check YAML syntax. Use 'osop validate {path}' for details.")
        raise SystemExit(1)

    errors = validate(workflow, schema_variant="core")
    if errors:
        console.print(f"[red]Invalid workflow:[/red] {len(errors)} error(s)")
        for err in errors:
            console.print(f"  [red]*[/red] {err}")
        console.print(f"\n  Fix: Run 'osop validate {path}' for details.")
        raise SystemExit(1)

    # Mock mode: explicit opt-in only
    if mock:
        name = workflow.get("name", path)
        nodes = workflow.get("nodes", [])
        console.print(Panel(
            f"[bold]{name}[/bold]\n[dim]mock mode (--mock flag)[/dim]",
            title="osop record", border_style="yellow"
        ))
        for node in nodes:
            nid = node.get("id")
            ntype = node.get("type")
            desc = node.get("description", "") or node.get("purpose", "")
            console.print(f"  [blue]>[/blue] [{ntype}] [bold]{nid}[/bold]  [dim]{desc[:60]}[/dim]")
        console.print(f"\n[yellow]Record complete[/yellow] (mock) — {len(nodes)} nodes listed")
        return

    # Find executor (strict: fail if not found)
    if not _find_mcp_tools():
        console.print("[red]Error:[/red] osop-mcp not found. Cannot execute workflow.")
        console.print("  Cause: The osop-mcp package is not installed or not in a known location.")
        console.print("  Fix: Install with 'pip install osop-mcp' or use '--mock' for simulation.")
        raise SystemExit(1)

    try:
        from tools.execute import execute as real_execute
    except ImportError:
        console.print("[red]Error:[/red] osop-mcp executor module not found.")
        console.print("  Fix: Ensure osop-mcp is properly installed. Use '--mock' for simulation.")
        raise SystemExit(1)

    # Real execution
    console.print(Panel(
        f"[bold]osop record[/bold]\n"
        f"File: {path}\n"
        f"allow_exec={allow_exec}  dry_run={dry_run}  interactive={interactive}\n"
        f"max_cost=${max_cost:.2f}  timeout={timeout}s",
        title="OSOP Recorder", border_style="blue"
    ))

    result = real_execute(
        file_path=path,
        dry_run=dry_run,
        allow_exec=allow_exec,
        interactive=interactive,
        timeout_seconds=timeout,
        max_cost_usd=max_cost,
    )

    run_status = result.get("status", "unknown")
    color = "green" if run_status == "completed" else "red" if run_status == "failed" else "yellow"

    if run_status == "blocked":
        console.print(f"\n[red]BLOCKED:[/red] {result.get('reason', '')}")
        cli_cmds = result.get("cli_commands", [])
        if cli_cmds:
            console.print("\n[yellow]CLI commands in workflow:[/yellow]")
            for cmd in cli_cmds:
                console.print(f"  [{cmd['node']}] {cmd['command']}")
            console.print("\n  Fix: Add --allow-exec to permit shell execution.")
        raise SystemExit(1)

    table = Table(title="Node Results")
    table.add_column("Node", style="bold")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Duration")
    table.add_column("Detail")

    for nr in result.get("node_results", []):
        st = nr.get("status", "?")
        st_color = "green" if st == "completed" else "red" if st in ("failed", "error") else "yellow"
        dur = f"{nr.get('duration_ms', 0)}ms" if "duration_ms" in nr else ""
        detail = ""
        if nr.get("cost_usd"):
            detail = f"${nr['cost_usd']:.4f}"
        elif nr.get("exit_code") is not None:
            detail = f"exit={nr['exit_code']}"
        elif nr.get("reason"):
            detail = nr["reason"][:40]
        elif nr.get("error"):
            detail = nr["error"][:40]
        table.add_row(nr.get("name", nr["node_id"]), nr.get("type", ""), f"[{st_color}]{st}[/{st_color}]", dur, detail)

    console.print(table)
    console.print(
        f"\n[{color}]{run_status.upper()}[/{color}] — "
        f"{result.get('executed', 0)} executed, {result.get('skipped', 0)} skipped, {result.get('failed', 0)} failed"
        f" — {result.get('duration_ms', 0)}ms"
        f" — ${result.get('total_cost_usd', 0):.4f}"
    )

    # Write osoplog
    if run_status != "blocked":
        out_path = log_path
        if not out_path:
            wf_id = workflow.get("id", "workflow")
            out_path = f"{wf_id}.osoplog.yaml"
        try:
            from pathlib import Path as P
            from tools.osoplog import generate_osoplog
            osoplog_content = generate_osoplog(workflow, result)
            P(out_path).write_text(osoplog_content, encoding="utf-8")
            console.print(f"\n[green]osoplog written to {out_path}[/green]")
        except Exception as e:
            console.print(f"\n[yellow]Could not write osoplog: {e}[/yellow]")

    if run_status != "completed":
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# osop replay — execute a .osop with live .osoplog streaming
# ---------------------------------------------------------------------------


@cli.command("replay")
@click.argument("osop_path", type=click.Path(exists=True))
@click.option("--allow-exec", is_flag=True, default=False,
              help="Allow CLI nodes to actually run shell commands")
@click.option("--dry-run", is_flag=True, default=False,
              help="Force dry-run (default behavior when --allow-exec absent)")
@click.option("--interactive", is_flag=True, default=False,
              help="Pause for input on human nodes")
@click.option("-o", "--output-dir", "output_dir", type=click.Path(), default=None,
              help="Where to write the .osoplog (default: ./)")
@click.option("--continue-on-error", is_flag=True, default=False,
              help="Don't halt on FAILED node; mark and continue")
@click.option("--yes", is_flag=True, default=False,
              help="Auto-confirm destructive commands (DANGEROUS)")
@click.option("--timeout", type=int, default=300,
              help="Default per-node timeout in seconds (overridable via node.timeout_sec)")
# Agent imitation execution (v2)
@click.option("--reference-log", "reference_log", type=click.Path(exists=True), default=None,
              help="Explicit .osoplog to imitate; auto-discovered from <osop>.osoplog.yaml if omitted")
@click.option("--no-agent", "no_agent", is_flag=True, default=False,
              help="Skip agent nodes entirely (v1 behavior)")
@click.option("--max-budget-per-node", "max_budget_per_node", type=float, default=5.0,
              help="USD ceiling for each agent node (claude -p --max-budget-usd)")
@click.option("--agent-max-turns", "agent_max_turns", type=int, default=10,
              help="Max tool-call turns per agent node")
@click.option("--allowed-tools", "allowed_tools", type=str,
              default="Read,Edit,Write,Bash,Grep,Glob,WebFetch",
              help="Comma-separated allowlist of Claude Code tools for agent nodes")
@click.option("--agent-timeout", "agent_timeout", type=int, default=600,
              help="Wall-clock seconds before killing claude -p for an agent node")
def replay_cmd(osop_path, allow_exec, dry_run, interactive, output_dir,
               continue_on_error, yes, timeout, reference_log, no_agent,
               max_budget_per_node, agent_max_turns, allowed_tools,
               agent_timeout):
    """Execute a .osop and stream a fresh .osoplog as each node completes.

    The .osoplog is flushed on every node boundary, so a crash mid-run
    still leaves a durable readable log.

    v2 supports cli + human + **agent** node types. Agent nodes are
    imitated via `claude -p` using the paired .osoplog as the source
    of truth (auto-discovered as <osop-stem>.osoplog.yaml). api nodes
    are still SKIPPED.

    For full executor (api/cost limits/risk_assess/sub-agents),
    use `osop record`.

    Examples:
      osop replay workflow.osop.yaml --allow-exec
      osop replay captured.osop.yaml --max-budget-per-node 0.50
      osop replay captured.osop.yaml --no-agent              # v1 behavior
      osop replay captured.osop.yaml --reference-log past.osoplog.yaml
    """
    from pathlib import Path as _P

    from osop.live_log import LiveLog
    from osop.replayer import detect_non_sequential_edges, execute_workflow
    from osop.imitation import find_reference_log

    # 1. Load + validate
    try:
        workflow = load_workflow(osop_path)
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] File not found: {osop_path}")
        raise SystemExit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] Cannot parse YAML: {e}")
        console.print(f"  Fix: Run 'osop validate {osop_path}' for details.")
        raise SystemExit(1)

    errors = validate(workflow, schema_variant="core")
    if errors:
        console.print(f"[red]Invalid workflow:[/red] {len(errors)} error(s)")
        for err in errors:
            console.print(f"  [red]*[/red] {err}")
        console.print(f"\n  Fix: Run 'osop validate {osop_path}' for details.")
        raise SystemExit(1)

    if dry_run and allow_exec:
        console.print("[yellow]Note:[/yellow] --dry-run overrides --allow-exec")
        allow_exec = False

    # 2. v1 limitation surface — warn loudly on non-sequential edges
    edges = workflow.get("edges") or []
    non_seq = detect_non_sequential_edges(edges)
    if non_seq:
        console.print(
            f"[yellow]Warning:[/yellow] v2 still collapses non-sequential edges to topological order: "
            f"{', '.join(non_seq)}. Use `osop record` for correct conditional/fallback/parallel semantics."
        )

    # Reference log resolution + agent-mode warning
    has_agent_nodes = any(
        isinstance(n, dict) and n.get("type") == "agent"
        for n in (workflow.get("nodes") or [])
    )
    resolved_ref_log: _P | None = None
    if not no_agent and has_agent_nodes:
        if reference_log:
            resolved_ref_log = _P(reference_log)
        else:
            resolved_ref_log = find_reference_log(osop_path)
        if resolved_ref_log is None:
            console.print(
                "[yellow]Warning:[/yellow] workflow has agent nodes but no paired "
                ".osoplog was found. Agent steps will be SKIPPED. Pass "
                "--reference-log <path> to override, or use --no-agent to silence."
            )

    # 3. Open the live log
    out_dir = _P(output_dir) if output_dir else _P.cwd()
    log = LiveLog.start(
        osop_path,
        output_dir=out_dir,
        runtime_agent="osop-replay",
        runtime_model="n/a",
        trigger="replay",
    )

    # 4. Confirmation hook for destructive commands
    def _confirm(cmd: str) -> bool:
        if yes:
            return True
        console.print(f"\n[red]DESTRUCTIVE:[/red] {cmd}")
        return click.confirm("  Run it?", default=False)

    # 5. Pre-flight panel
    agent_line = ""
    if has_agent_nodes and not no_agent and resolved_ref_log:
        agent_line = (
            f"\n[dim]agent ref:[/dim] {resolved_ref_log}  "
            f"[dim]budget/node:[/dim] ${max_budget_per_node:.2f}"
        )
    console.print(Panel(
        f"[bold]{workflow.get('name', osop_path)}[/bold]\n"
        f"[dim]source:[/dim] {osop_path}\n"
        f"[dim]allow_exec:[/dim] {allow_exec}  "
        f"[dim]interactive:[/dim] {interactive}  "
        f"[dim]continue_on_error:[/dim] {continue_on_error}"
        f"{agent_line}\n"
        f"[dim]live log:[/dim] {log.path}",
        title="osop replay",
        border_style="blue",
    ))

    # Parse comma-separated allow list
    tools_list = [t.strip() for t in (allowed_tools or "").split(",") if t.strip()]

    # 6. Execute with per-node progress
    def _on_start(node: dict) -> None:
        console.print(f"  [blue]>[/blue] [{node.get('type','?')}] [bold]{node['id']}[/bold]")

    def _on_done(node: dict, result: dict) -> None:
        st = result.get("status", "?")
        color = {"COMPLETED": "green", "FAILED": "red", "SKIPPED": "yellow",
                 "BLOCKED": "magenta"}.get(st, "white")
        console.print(f"    [{color}]{st}[/{color}]")

    summary = execute_workflow(
        workflow,
        log,
        allow_exec=allow_exec,
        interactive=interactive,
        continue_on_error=continue_on_error,
        confirm_destructive=_confirm,
        cli_timeout_seconds=timeout,
        on_node_start=_on_start,
        on_node_done=_on_done,
        osop_path=osop_path,
        reference_log_path=reference_log,
        skip_agents=no_agent,
        agent_max_budget_usd=max_budget_per_node,
        agent_max_turns=agent_max_turns,
        agent_allowed_tools=tools_list,
        agent_cwd=str(_P.cwd()),
        agent_timeout_seconds=agent_timeout,
    )

    # 7. Finalize log + summary
    final_status = "COMPLETED" if summary["status"] == "COMPLETED" else "FAILED"
    log_path = log.finish(final_status)

    counts = summary["counts"]
    table = Table(title="Replay Summary", show_header=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    table.add_row("Status", f"[{'green' if summary['status']=='COMPLETED' else 'red'}]"
                            f"{summary['status']}[/]")
    table.add_row("Completed", str(counts.get("COMPLETED", 0)))
    table.add_row("Failed", str(counts.get("FAILED", 0)))
    table.add_row("Skipped", str(counts.get("SKIPPED", 0)))
    table.add_row("Blocked", str(counts.get("BLOCKED", 0)))
    if summary["halted_on"]:
        table.add_row("Halted on", summary["halted_on"])
    console.print(table)
    console.print(f"\n[green]osoplog written:[/green] {log_path}")
    console.print(f"  Next: [cyan]osop view[/cyan] or drag into https://osop-editor.vercel.app")

    if summary["status"] != "COMPLETED":
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# osop log — synthesize from Claude Code transcript (accurate, evidence-based)
# ---------------------------------------------------------------------------


@cli.command("log")
@click.argument("source", required=False)
@click.option("-d", "--desc", "short_desc", default=None,
              help="Short description for filenames (default: derived from date+session)")
@click.option("-o", "--out-dir", "out_dir", type=click.Path(), default=None,
              help="Output directory (default: ./sessions)")
@click.option("--tag", "extra_tags", multiple=True, help="Extra tag(s) to attach to .osop")
@click.option("--stdout", is_flag=True, default=False,
              help="Print YAML to stdout instead of writing files")
def log_cmd(source, short_desc, out_dir, extra_tags, stdout):
    """Synthesize .osop + .osoplog from a Claude Code session transcript.

    Reads the canonical JSONL transcript and reconstructs the workflow from
    real tool-call evidence — no LLM self-report. Each user prompt becomes
    a `human` node; the agent work that follows becomes one node grouped
    by tool mix (cli / api / agent / human), with full per-call detail
    preserved in `tool_calls[]` on the .osoplog.

    SOURCE may be:
      - A path to a transcript .jsonl
      - A session id (looked up under ~/.claude/projects/*/<id>.jsonl)
      - omitted → most recent transcript for cwd

    Examples:
      osop log
      osop log 130fbd5c-b7d8-47bb-88b3-3eb9f91fba27
      osop log -d fix-auth-bug --tag bug-fix
      osop log /path/to/session.jsonl --stdout
    """
    from datetime import datetime as _dt
    from pathlib import Path as P

    from osop.recorder.transcript import (
        parse_transcript,
        resolve_transcript_path,
        synthesize,
        to_yaml,
    )

    try:
        path = resolve_transcript_path(source, cwd=P.cwd())
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("  Cause: No transcript matched the given source.")
        console.print("  Fix: Pass a session id or full transcript path, "
                      "or run from a project directory that has a transcript.")
        raise SystemExit(1)

    try:
        parsed = parse_transcript(path)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)

    if not parsed["nodes"]:
        console.print(f"[yellow]Skipped:[/yellow] transcript has no usable events: {path}")
        return

    # Derive short_desc if not given
    date_str = (parsed["started_at"] or _dt.utcnow().isoformat())[:10]
    if not short_desc:
        sid = (parsed.get("session_id") or path.stem)[:8]
        short_desc = f"{date_str}-session-{sid}"
    elif not short_desc.startswith(date_str):
        short_desc = f"{date_str}-{short_desc}"

    osop_doc, osoplog_doc = synthesize(parsed, short_desc=short_desc, tags=list(extra_tags))

    osop_yaml = to_yaml(osop_doc)
    osoplog_yaml = to_yaml(osoplog_doc)

    if stdout:
        console.print("[dim]# --- .osop.yaml ---[/dim]")
        click.echo(osop_yaml)
        console.print("[dim]# --- .osoplog.yaml ---[/dim]")
        click.echo(osoplog_yaml)
        return

    target_dir = P(out_dir) if out_dir else (P.cwd() / "sessions")
    target_dir.mkdir(parents=True, exist_ok=True)

    osop_path = target_dir / f"{short_desc}.osop.yaml"
    log_path = target_dir / f"{short_desc}.osoplog.yaml"
    osop_path.write_text(osop_yaml, encoding="utf-8")
    log_path.write_text(osoplog_yaml, encoding="utf-8")

    total_calls = sum(
        len(rec.get("tool_calls", [])) for rec in osoplog_doc["node_records"]
    )

    console.print(Panel(
        f"[bold]{osop_doc['name']}[/bold]\n"
        f"[dim]source:[/dim] {path}\n"
        f"[dim]nodes:[/dim] {len(osoplog_doc['node_records'])}  "
        f"[dim]tool calls:[/dim] {total_calls}  "
        f"[dim]duration:[/dim] {osoplog_doc['duration_ms']}ms\n"
        f"[green]{osop_path}[/green]\n"
        f"[green]{log_path}[/green]",
        title="osop log",
        border_style="green",
    ))


# ---------------------------------------------------------------------------
# osop diff
# ---------------------------------------------------------------------------

@cli.command("diff")
@click.argument("file_a", type=click.Path(exists=True))
@click.argument("file_b", type=click.Path(exists=True))
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table", help="Output format")
def diff_cmd(file_a, file_b, fmt):
    """Compare two .osop or .osoplog files side by side.

    Both files must be the same type (both .osop or both .osoplog).

    Examples:
      osop diff workflow-v1.osop.yaml workflow-v2.osop.yaml
      osop diff run-monday.osoplog.yaml run-friday.osoplog.yaml
    """
    import json

    a_is_log = file_a.endswith(".osoplog.yaml") or file_a.endswith(".osoplog.yml")
    b_is_log = file_b.endswith(".osoplog.yaml") or file_b.endswith(".osoplog.yml")

    if a_is_log != b_is_log:
        console.print("[red]Error:[/red] Cannot compare .osop with .osoplog. Both files must be the same type.")
        console.print(f"  File A: {'osoplog' if a_is_log else 'osop'}")
        console.print(f"  File B: {'osoplog' if b_is_log else 'osop'}")
        console.print(f"  Fix: Compare two .osop files or two .osoplog files.")
        raise SystemExit(1)

    if not _find_mcp_tools():
        console.print("[red]Error:[/red] osop-mcp not found. Required for diff.")
        console.print("  Fix: Install with 'pip install osop-mcp'.")
        raise SystemExit(1)

    try:
        if a_is_log:
            from tools.diff import diff_logs
            result = diff_logs(file_path_a=file_a, file_path_b=file_b)
        else:
            from tools.diff import diff_workflows
            result = diff_workflows(file_path_a=file_a, file_path_b=file_b)
    except ImportError:
        console.print("[red]Error:[/red] osop-mcp diff module not found.")
        console.print("  Fix: Ensure osop-mcp is properly installed.")
        raise SystemExit(1)

    if fmt == "json":
        click.echo(json.dumps(result, indent=2, default=str))
        return

    if a_is_log:
        la = result["log_a"]
        lb = result["log_b"]
        agg = result["aggregate"]

        console.print(Panel(
            f"[bold]Run A:[/bold] {la['workflow_id']} ({la['run_id']}) — {la['status']} — {la['duration_fmt']}\n"
            f"[bold]Run B:[/bold] {lb['workflow_id']} ({lb['run_id']}) — {lb['status']} — {lb['duration_fmt']}",
            title="osop diff (execution logs)", border_style="cyan"
        ))

        table = Table()
        table.add_column("Node", style="bold")
        table.add_column("Type")
        table.add_column("Duration (A > B)")
        table.add_column("Cost (A > B)")
        table.add_column("Status")

        for nd in result["node_diffs"]:
            if nd["change"] == "added":
                table.add_row(f"[green]+{nd['node_id']}[/green]", nd["node_type"], "", "", "[green]added[/green]")
            elif nd["change"] == "removed":
                table.add_row(f"[red]-{nd['node_id']}[/red]", nd["node_type"], "", "", "[red]removed[/red]")
            else:
                d = nd["duration"]
                c = nd["cost"]
                s = nd["status"]
                dur_str = f"{d['a_fmt']} > {d['b_fmt']}"
                if d["delta_ms"] != 0:
                    clr = "green" if d["delta_ms"] < 0 else "red"
                    dur_str += f" [{clr}]({d['delta_pct']})[/{clr}]"
                cost_str = ""
                if c["a"] > 0 or c["b"] > 0:
                    cost_str = f"${c['a']:.4f} > ${c['b']:.4f}"
                    if c["delta"] != 0:
                        clr = "green" if c["delta"] < 0 else "red"
                        cost_str += f" [{clr}]({c['delta_pct']})[/{clr}]"
                status_str = s["a"]
                if s["changed"]:
                    status_str = f"[yellow]{s['a']} > {s['b']}[/yellow]"
                table.add_row(nd["node_id"], nd["node_type"], dur_str, cost_str, status_str)

        console.print(table)
        dur_color = "green" if agg["duration_delta_ms"] < 0 else "red" if agg["duration_delta_ms"] > 0 else "white"
        console.print(
            f"\n[bold]Summary:[/bold] "
            f"[{dur_color}]{agg['duration_delta_pct']} duration[/{dur_color}] | "
            f"{agg['nodes_added']} added, {agg['nodes_removed']} removed, {agg['nodes_modified']} modified"
        )
    else:
        if result["identical"]:
            console.print("[green]Identical[/green] — no differences found.")
        else:
            console.print(f"[bold]{result['total_changes']} changes[/bold]")
            for n in result["nodes"].get("added", []):
                console.print(f"  [green]+[/green] node: {n.get('id', '?')}")
            for n in result["nodes"].get("removed", []):
                console.print(f"  [red]-[/red] node: {n.get('id', '?')}")
            for n in result["nodes"].get("changed", []):
                console.print(f"  [yellow]~[/yellow] node: {n.get('id', '?')}")


# ---------------------------------------------------------------------------
# osop optimize
# ---------------------------------------------------------------------------

@cli.command("optimize")
@click.argument("log_files", nargs=-1, required=True)
@click.option("--base", type=click.Path(exists=True), default=None, help="Base .osop file to optimize (optional)")
@click.option("--goal", type=str, default="", help="Optimization goal (e.g., 'reduce cost', 'speed up step 3')")
@click.option("--provider", type=str, default="anthropic", help="LLM provider (anthropic or openai)")
@click.option("--model", type=str, default="", help="LLM model to use")
@click.option("-o", "--output", type=click.Path(), default=None, help="Write optimized .osop to file")
@click.option("--prompt-only", is_flag=True, default=False, help="Output the synthesis prompt without calling LLM")
def optimize_cmd(log_files, base, goal, provider, model, output, prompt_only):
    """Synthesize an optimized .osop from multiple execution logs.

    The closed loop: record > diff > optimize > record again.

    Examples:
      osop optimize run1.osoplog.yaml run2.osoplog.yaml
      osop optimize sessions/*.osoplog.yaml --base workflow.osop.yaml
      osop optimize *.osoplog.yaml --goal "reduce LLM cost" -o optimized.osop.yaml
      osop optimize *.osoplog.yaml --prompt-only
    """
    from pathlib import Path

    if not _find_mcp_tools():
        console.print("[red]Error:[/red] osop-mcp not found. Required for optimize.")
        console.print("  Cause: The osop-mcp package is not installed or not in a known location.")
        console.print("  Fix: Install with 'pip install osop-mcp'.")
        raise SystemExit(1)

    try:
        from tools.synthesize import synthesize as synth_fn
    except ImportError:
        console.print("[red]Error:[/red] osop-mcp synthesize module not found.")
        console.print("  Fix: Ensure osop-mcp is properly installed.")
        raise SystemExit(1)

    paths = list(log_files)
    console.print(Panel(
        f"[bold]osop optimize[/bold]\n"
        f"Logs: {len(paths)} file(s)\n"
        f"Base: {base or '(none, generating from scratch)'}\n"
        f"Goal: {goal or '(general optimization)'}\n"
        f"Provider: {provider}",
        title="OSOP Optimizer", border_style="purple"
    ))

    console.print(f"\nAnalyzing {len(paths)} execution log(s)...")

    result = synth_fn(
        log_paths=paths,
        base_osop_path=base,
        goal=goal,
        provider=provider,
        model=model,
        prompt_only=prompt_only,
    )

    if prompt_only and result.get("status") == "prompt_ready":
        stats = result.get("stats", {})
        console.print(f"\n[bold]Stats:[/bold] {stats.get('total_runs', 0)} runs, {len(stats.get('node_summaries', {}))} unique nodes")
        console.print(f"\n[bold]Optimization Prompt:[/bold] (paste this to any AI)\n")
        click.echo(result["prompt"])
        if output:
            Path(output).write_text(result["prompt"], encoding="utf-8")
            console.print(f"\n[green]Prompt saved to {output}[/green]")
        return

    if result["status"] == "failed":
        console.print(f"\n[red]FAILED:[/red] {result.get('error', 'Unknown error')}")
        stats = result.get("stats", {})
        if stats:
            console.print(f"\n[dim]Stats collected before failure:[/dim]")
            console.print(f"  Runs: {stats.get('total_runs', 0)} | Avg duration: {stats.get('avg_duration_ms', 0)}ms")
        raise SystemExit(1)

    stats = result.get("stats", {})
    console.print(f"\n[bold]Execution Analysis:[/bold]")
    console.print(f"  Runs analyzed: {stats.get('total_runs', 0)}")
    console.print(f"  Avg duration: {stats.get('avg_duration_ms', 0)}ms")
    console.print(f"  Total cost: ${stats.get('total_cost_usd', 0):.4f}")

    node_summaries = stats.get("node_summaries", {})
    if node_summaries:
        table = Table(title="Node Performance Summary")
        table.add_column("Node", style="bold")
        table.add_column("Type")
        table.add_column("Runs")
        table.add_column("Avg Duration")
        table.add_column("Success Rate")
        table.add_column("Avg Cost")

        for nid, ns in node_summaries.items():
            sr = ns.get("success_rate", 0)
            sr_color = "green" if sr >= 0.9 else "yellow" if sr >= 0.7 else "red"
            table.add_row(
                nid, ns.get("node_type", "?"),
                str(ns.get("runs", 0)),
                f"{ns.get('avg_duration_ms', 0)}ms",
                f"[{sr_color}]{sr*100:.0f}%[/{sr_color}]",
                f"${ns.get('avg_cost_usd', 0):.4f}",
            )
        console.print(table)

    insights = result.get("insights", "")
    if insights:
        console.print(f"\n[bold]AI Insights:[/bold]")
        console.print(f"  {insights[:500]}")

    optimized = result.get("optimized_yaml", "")
    if optimized:
        console.print(f"\n[bold]Optimized Workflow:[/bold]")
        console.print(f"[dim]({len(optimized)} chars, {result.get('model', '?')})[/dim]")

        if output:
            Path(output).write_text(optimized, encoding="utf-8")
            console.print(f"\n[green]Written to {output}[/green]")
        else:
            console.print()
            console.print(optimized[:2000])
            if len(optimized) > 2000:
                console.print(f"\n[dim]... ({len(optimized) - 2000} more chars. Use -o to write to file.)[/dim]")

    cost = result.get("cost_usd", 0)
    console.print(f"\n[green]DONE[/green] — optimization cost: ${cost:.4f}")


# ---------------------------------------------------------------------------
# osop view
# ---------------------------------------------------------------------------

@cli.command("view")
@click.argument("sop_path", type=click.Path(exists=True))
@click.option("-o", "--output", type=click.Path(), default=None,
              help="Output HTML path (default: <sop-id>.html)")
@click.option("--lang", type=click.Choice(["en", "zh-TW"]), default="en",
              help="UI language for the HTML output")
def view_cmd(sop_path, output, lang):
    """Render a .sop file into a standalone HTML document.

    Reads the .sop and all referenced .osop workflows, produces a
    self-contained HTML file you can open directly in any browser.

    Examples:
      osop view team-sops.sop
      osop view sessions/session.sop -o report.html
      osop view session.sop --lang zh-TW
    """
    from pathlib import Path
    import yaml

    sop_file = Path(sop_path)
    try:
        sop_data = yaml.safe_load(sop_file.read_text(encoding="utf-8"))
    except Exception as e:
        console.print(f"[red]Error:[/red] Cannot parse .sop file: {e}")
        console.print(f"  Fix: Check YAML syntax in {sop_path}")
        raise SystemExit(1)

    if not isinstance(sop_data, dict) or "sections" not in sop_data:
        console.print("[red]Error:[/red] Invalid .sop file. Must have 'sections' array.")
        console.print(f"  Fix: See .sop schema at https://github.com/Archie0125/osop-spec")
        raise SystemExit(1)

    sop_name = sop_data.get("name", "SOP Document")
    sop_desc = sop_data.get("description", "")
    sop_author = sop_data.get("author", "")
    sop_tags = sop_data.get("tags", [])
    sections = sop_data.get("sections", [])
    sop_dir = sop_file.parent

    # Load all referenced .osop workflows
    loaded_sections = []
    total_workflows = 0
    total_nodes = 0
    missing_refs = []

    for section in sections:
        sec_name = section.get("name", "Untitled Section")
        sec_desc = section.get("description", "")
        workflows = []

        for wf_ref in section.get("workflows", []):
            ref_path = sop_dir / wf_ref.get("ref", "")
            title = wf_ref.get("title", "")
            wf_data = None

            raw_yaml = ""
            if ref_path.exists():
                try:
                    raw_yaml = ref_path.read_text(encoding="utf-8")
                    wf_data = yaml.safe_load(raw_yaml)
                    if not title:
                        title = wf_data.get("name", ref_path.name)
                except Exception:
                    wf_data = None

            # Auto-discover ALL matching .osoplog files
            import glob as _glob
            stem = ref_path.stem.replace(".osop", "")
            log_files = sorted(_glob.glob(str(ref_path.parent / f"{stem}*.osoplog.yaml")))
            logs = []
            for lf in log_files:
                try:
                    lf_path = Path(lf)
                    lf_raw = lf_path.read_text(encoding="utf-8")
                    lf_data = yaml.safe_load(lf_raw)
                    if isinstance(lf_data, dict):
                        logs.append({"raw": lf_raw, "data": lf_data, "filename": lf_path.name})
                except Exception:
                    pass

            if wf_data is None:
                missing_refs.append(str(ref_path))
                workflows.append({"title": title or ref_path.name, "missing": True, "nodes": [], "edges": [], "raw_yaml": "", "filename": ref_path.name, "logs": []})
            else:
                nodes = wf_data.get("nodes", [])
                edges = wf_data.get("edges", [])
                total_nodes += len(nodes)
                total_workflows += 1
                workflows.append({
                    "title": title,
                    "description": wf_data.get("description", ""),
                    "missing": False,
                    "nodes": nodes,
                    "edges": edges,
                    "tags": wf_data.get("tags", []),
                    "raw_yaml": raw_yaml,
                    "filename": ref_path.name,
                    "logs": logs,
                })

        loaded_sections.append({"name": sec_name, "description": sec_desc, "workflows": workflows})

    # Generate HTML
    html = _generate_sop_html(sop_name, sop_desc, sop_author, sop_tags, loaded_sections, total_workflows, total_nodes, lang)

    out_path = output or f"{sop_data.get('id', 'sop-doc')}.html"
    Path(out_path).write_text(html, encoding="utf-8")

    console.print(Panel(
        f"[bold]{sop_name}[/bold]\n"
        f"Sections: {len(loaded_sections)} | Workflows: {total_workflows} | Nodes: {total_nodes}",
        title="osop view", border_style="green"
    ))
    if missing_refs:
        for ref in missing_refs:
            console.print(f"  [yellow]Warning:[/yellow] Missing workflow: {ref}")
    console.print(f"\n[green]HTML written to {out_path}[/green]")
    console.print(f"  Open in browser to view.")


def _generate_sop_html(name, desc, author, tags, sections, total_wf, total_nodes, lang="en"):
    """Generate a self-contained HTML SOP document matching osop-website.vercel.app style."""
    import urllib.parse

    i18n = {
        "en": {
            "sections": "Sections", "workflows": "Workflows", "total_nodes": "Total nodes",
            "visual": "Visual", "execution": "Execution", "open_editor": "Open in Editor",
            "copy_yaml": "Copy YAML", "copy_log": "Copy Log", "copied": "Copied!",
            "by": "By", "nodes": "nodes", "not_found": "Workflow file not found",
            "generated_by": "Generated by",
        },
        "zh-TW": {
            "sections": "章節", "workflows": "工作流", "total_nodes": "總節點數",
            "visual": "視覺化", "execution": "執行紀錄", "open_editor": "在編輯器中開啟",
            "copy_yaml": "複製 YAML", "copy_log": "複製紀錄", "copied": "已複製！",
            "by": "作者", "nodes": "個節點", "not_found": "找不到工作流檔案",
            "generated_by": "由", "run": "執行",
        },
    }
    t = i18n.get(lang, i18n["en"])
    html_lang = "zh-Hant" if lang == "zh-TW" else "en"

    # Content translation for zh-TW
    _zht = {
        "agent": "AI 代理", "api": "API 呼叫", "cli": "命令列", "human": "人工步驟",
        "sequential": "循序", "parallel": "平行", "conditional": "條件式", "fallback": "備援",
        "Explore Codebase": "探索程式碼庫", "Analyze Root Cause": "分析根本原因",
        "Write Fix": "撰寫修復", "Run Tests": "執行測試",
        "User Reviews": "使用者審查", "User Reviews Changes": "使用者審查變更",
        "User Describes Bug": "使用者描述問題", "Plan and Dispatch": "規劃與分派",
        "Generate Code": "產生程式碼", "Search Auth Code": "搜尋認證程式碼",
        "Explore Project Structure": "探索專案結構",
        "Explore Features & Usage": "探索功能與使用情況",
        "Design Simplification Plan": "設計精簡化計畫",
        "User Approves Plan": "使用者核准計畫",
        "User Requests Simplification": "使用者要求精簡化",
        "Delete Dead Weight": "刪除無用內容",
        "Archive Premature Integrations": "歸檔過早的整合",
        "Consolidate Features": "合併功能", "Update Documentation": "更新文件",
        "Rewrite CLI": "重寫命令列", "Rewrite MCP Server": "重寫 MCP 伺服器",
        "Rewrite All Docs": "重寫所有文件",
        "Git Init + Commit": "Git 初始化與提交",
        "CEO Review": "CEO 審查", "Eng Review": "工程審查", "DX Review": "開發者體驗審查",
        "User Approves All Fixes": "使用者核准所有修正",
        "Apply Fixes": "套用修正", "Final Doc Updates": "最終文件更新",
        "Claude CEO Subagent": "Claude CEO 子代理", "Codex CEO Voice": "Codex CEO 觀點",
        "Claude Eng Subagent": "Claude 工程子代理", "Codex Eng Voice": "Codex 工程觀點",
        "Claude DX Subagent": "Claude DX 子代理", "Codex DX Voice": "Codex DX 觀點",
        "User Premise Gate": "使用者前提確認",
        "HTML Report Generator": "HTML 報告產生器",
        "Radical Simplification": "激進精簡化",
        "Autoplan Phase 1: CEO Review": "自動規劃第一階段：CEO 審查",
        "Autoplan Phase 3: Eng Review": "自動規劃第三階段：工程審查",
        "Autoplan Phase 3.5: DX Review": "自動規劃第 3.5 階段：DX 審查",
        "OSOP Radical Simplification + Autoplan Review": "OSOP 激進精簡化 + 自動規劃審查",
        "6 Autoplan Fixes": "6 項自動規劃修正",
    }
    def _tr(text):
        if lang != "zh-TW" or not text:
            return text
        return _zht.get(text, text)

    dot_colors = {"agent": "#a855f7", "api": "#22c55e", "cli": "#f59e0b", "human": "#3b82f6"}
    mode_styles = {
        "sequential": ("bg-slate-100", "#64748b", "#f1f5f9"),
        "conditional": ("bg-amber-50", "#d97706", "#fffbeb"),
        "fallback": ("bg-red-50", "#dc2626", "#fef2f2"),
        "parallel": ("bg-blue-50", "#2563eb", "#eff6ff"),
    }

    # Build node lookup per workflow for edge target names
    sections_html = ""
    wf_counter = 0

    for sec in sections:
        wf_cards = ""
        for wf in sec["workflows"]:
            wf_counter += 1
            wf_id = f"wf{wf_counter}"

            if wf["missing"]:
                wf_cards += f'<div class="cb"><div class="cb-head"><span class="dot r"></span><span class="dot y"></span><span class="dot g"></span><span class="cb-name">{_esc(wf["filename"])}</span></div><div class="cb-body"><p style="color:#dc2626;font-style:italic">{t["not_found"]}</p></div></div>'
                continue

            node_map = {}
            for n in wf.get("nodes", []):
                if isinstance(n, dict):
                    node_map[n.get("id", "")] = n.get("name", n.get("id", "?"))

            # Build edge lookup: source_id -> list of edges
            edge_map = {}
            for e in wf.get("edges", []):
                if isinstance(e, dict):
                    src = e.get("from", "")
                    edge_map.setdefault(src, []).append(e)

            # Render nodes with their outgoing edges inline below
            visual_html = ""
            for node in wf.get("nodes", []):
                if not isinstance(node, dict):
                    continue
                nid = node.get("id", "?")
                ntype = node.get("type", "?")
                nname = _esc(_tr(node.get("name", nid)))
                ndesc = _esc(_tr(node.get("description", "") or node.get("purpose", "")))
                ntype_label = _esc(_tr(ntype))
                dot_color = dot_colors.get(ntype, "#94a3b8")

                visual_html += f'<div><div class="nd"><div style="display:flex;align-items:center;gap:8px"><span class="ndot" style="background:{dot_color}"></span><span class="nname">{nname}</span><span class="ntype">{ntype_label}</span></div>'
                if ndesc:
                    visual_html += f'<p class="ndesc">{ndesc}</p>'
                visual_html += '</div>'

                # Outgoing edges
                for edge in edge_map.get(nid, []):
                    eto = edge.get("to", "?")
                    mode = edge.get("mode", "sequential")
                    mode_label = _tr(mode)
                    target_name = _esc(_tr(node_map.get(eto, eto)))
                    _, mode_color, mode_bg = mode_styles.get(mode, mode_styles["sequential"])
                    visual_html += f'<div class="ed"><span class="earr">&darr;</span><span class="emode" style="background:{mode_bg};color:{mode_color}">{mode_label}</span><span class="etgt">&rarr; {target_name}</span></div>'

                visual_html += '</div>'

            # Raw YAML for .osop tab
            raw_yaml_esc = _esc(wf.get("raw_yaml", ""))

            # Encode YAML for editor link
            editor_yaml = urllib.parse.quote(wf.get("raw_yaml", ""), safe="")
            editor_url = f"https://osop-editor.vercel.app?yaml={editor_yaml}" if wf.get("raw_yaml") else ""

            filename = _esc(wf.get("filename", "workflow.osop.yaml"))
            wf_title = _esc(wf["title"])

            # Build .osop card
            osop_card = f'''<div class="cb">
<div class="cb-head"><span class="dot r"></span><span class="dot y"></span><span class="dot g"></span><span class="cb-name">{filename}</span></div>
<div class="tabs"><button class="tab active" onclick="switchTab('{wf_id}','visual')">{t["visual"]}</button><button class="tab" onclick="switchTab('{wf_id}','yaml')">.osop</button></div>
<div id="{wf_id}-visual" class="cb-body">{visual_html}</div>
<div id="{wf_id}-yaml" class="yaml-body" style="display:none"><pre class="yaml-pre">{raw_yaml_esc}</pre></div>
<div class="cb-foot">'''
            if editor_url:
                osop_card += f'<a href="{editor_url}" target="_blank" rel="noopener noreferrer" class="btn-primary">{t["open_editor"]}</a>'
            osop_card += f'<button class="btn-secondary" onclick="copyYaml(this,`{wf_id}`)">{t["copy_yaml"]}</button></div></div>'

            # Build .osoplog card(s) if any exist
            logs = wf.get("logs", [])
            if logs:
                def _render_log_visual(ld):
                    lstat = ld.get("status", "?")
                    ldur = ld.get("duration_ms", 0)
                    lrecs = ld.get("node_records", [])
                    sc = "#22c55e" if lstat == "COMPLETED" else "#ef4444" if lstat == "FAILED" else "#f59e0b"
                    h = f'<div class="nd" style="border-left:3px solid {sc};border-radius:0 8px 8px 0"><div style="display:flex;align-items:center;gap:8px"><span class="nname">{lstat}</span><span class="ntype">{ldur}ms</span></div></div>'
                    for rec in lrecs:
                        if not isinstance(rec, dict):
                            continue
                        rid = _esc(rec.get("node_id", "?"))
                        rs = rec.get("status", "?")
                        rd = rec.get("duration_ms", 0)
                        rc = "#22c55e" if rs == "COMPLETED" else "#ef4444" if rs == "FAILED" else "#f59e0b"
                        tls = rec.get("tools_used", [])
                        ts = ", ".join(f'{x.get("tool","")}x{x.get("calls","")}' for x in tls if isinstance(x, dict)) if tls else ""
                        th = f'<span class="ntype">{_esc(ts)}</span>' if ts else ""
                        h += f'<div class="nd"><div style="display:flex;align-items:center;gap:8px"><span class="ndot" style="background:{rc}"></span><span class="nname">{rid}</span><span class="ntype">{rs} {rd}ms</span></div>{th}</div>'
                    return h

                run_label = t.get("run", "Run")
                if len(logs) == 1:
                    # Single log — simple layout
                    lg = logs[0]
                    lid = f"{wf_id}log"
                    lv = _render_log_visual(lg["data"])
                    ly = _esc(lg["raw"])
                    lf = _esc(lg["filename"])
                    log_card = f'''<div class="cb">
<div class="cb-head"><span class="dot r"></span><span class="dot y"></span><span class="dot g"></span><span class="cb-name">{lf}</span></div>
<div class="tabs"><button class="tab active" onclick="switchTab('{lid}','visual')">{t["execution"]}</button><button class="tab" onclick="switchTab('{lid}','yaml')">.osoplog</button></div>
<div id="{lid}-visual" class="cb-body">{lv}</div>
<div id="{lid}-yaml" class="yaml-body" style="display:none"><pre class="yaml-pre">{ly}</pre></div>
<div class="cb-foot"><button class="btn-secondary" onclick="copyYaml(this,`{lid}`)">{t["copy_log"]}</button></div></div>'''
                else:
                    # Multiple logs — tabbed runs
                    run_tabs = ""
                    run_panels = ""
                    for ri, lg in enumerate(logs):
                        rid = f"{wf_id}r{ri}"
                        active = " active" if ri == 0 else ""
                        disp = "" if ri == 0 else ' style="display:none"'
                        lv = _render_log_visual(lg["data"])
                        ly = _esc(lg["raw"])
                        run_tabs += f'<button class="tab{active}" onclick="switchRun(\'{wf_id}\',{ri},{len(logs)})">{run_label} {ri+1}</button>'
                        run_panels += f'<div id="{rid}-visual" class="cb-body"{disp}>{lv}</div>'
                        run_panels += f'<div id="{rid}-yaml" class="yaml-body" style="display:none"><pre class="yaml-pre">{ly}</pre></div>'
                    lf = _esc(logs[0]["filename"])
                    log_card = f'''<div class="cb">
<div class="cb-head"><span class="dot r"></span><span class="dot y"></span><span class="dot g"></span><span class="cb-name">{lf} (+{len(logs)-1})</span></div>
<div class="tabs" id="{wf_id}-runtabs">{run_tabs}</div>
<div class="tabs" id="{wf_id}-subtabs"><button class="tab active" onclick="switchLogView('{wf_id}','{t["execution"]}')">{t["execution"]}</button><button class="tab" onclick="switchLogView('{wf_id}','.osoplog')">.osoplog</button></div>
{run_panels}
<div class="cb-foot"><button class="btn-secondary" onclick="copyActiveLog(this,'{wf_id}',{len(logs)})">{t["copy_log"]}</button></div></div>'''

                wf_cards += f'<div><h3 class="wf-title">{_esc(_tr(wf["title"]))}</h3><div class="split">{osop_card}{log_card}</div></div>'
            else:
                wf_cards += f'<div><h3 class="wf-title">{_esc(_tr(wf["title"]))}</h3>{osop_card}</div>'

        sec_desc = f'<p class="sec-desc">{_esc(sec["description"])}</p>' if sec.get("description") else ""
        sections_html += f'<div class="sec"><h2 class="sec-title">{_esc(sec["name"])}</h2>{sec_desc}<div class="grid">{wf_cards}</div></div>'

    tags_html = "".join(f'<span class="tag">{_esc(t)}</span>' for t in tags) if tags else ""
    author_html = f'<p class="meta">{t["by"]} {_esc(author)}</p>' if author else ""
    desc_html = f'<p class="desc">{_esc(desc)}</p>' if desc else ""

    return f'''<!DOCTYPE html>
<html lang="{html_lang}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{_esc(name)}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#fff;color:#0f172a;line-height:1.6;max-width:960px;margin:0 auto;padding:3rem 1.5rem}}
h1{{font-size:1.8rem;font-weight:800;letter-spacing:-0.025em;color:#0f172a;margin-bottom:0.2rem}}
.desc{{color:#64748b;max-width:36rem;margin:0.3rem 0 0.5rem;font-size:0.95rem}}
.meta{{color:#94a3b8;font-size:0.8rem}}
.stats{{color:#64748b;font-size:0.8rem;margin:1rem 0 2rem;padding:0.6rem 1rem;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px}}
.tags{{display:flex;gap:0.3rem;flex-wrap:wrap;margin:0.5rem 0}}
.tag{{background:#eef2ff;color:#4f46e5;padding:0.1rem 0.5rem;border-radius:10px;font-size:0.7rem;font-weight:500}}
.sec{{margin-bottom:2.5rem}}
.sec-title{{font-size:1.3rem;font-weight:700;color:#0f172a;letter-spacing:-0.02em;margin-bottom:0.4rem}}
.sec-desc{{color:#64748b;font-size:0.875rem;margin-bottom:1rem}}
.grid{{display:flex;flex-direction:column;gap:1.2rem}}
.split{{display:grid;grid-template-columns:1fr;gap:0}}
@media(min-width:768px){{.split{{grid-template-columns:1fr 1fr}}}}
.split .cb{{border-radius:0}}
.split .cb:first-child{{border-radius:12px 0 0 12px;border-right:none}}
.split .cb:last-child{{border-radius:0 12px 12px 0}}
@media(max-width:767px){{.split .cb:first-child{{border-radius:12px 12px 0 0;border-bottom:none}}.split .cb:last-child{{border-radius:0 0 12px 12px}}}}
.wf-title{{font-size:0.875rem;font-weight:700;color:#0f172a;margin-bottom:0.5rem}}
.cb{{border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;background:#fff}}
.cb-head{{display:flex;align-items:center;gap:6px;padding:10px 16px;background:#18181b;color:#a1a1aa;font-size:0.7rem;font-family:ui-monospace,monospace}}
.dot{{width:10px;height:10px;border-radius:50%}}
.dot.r{{background:#f87171cc}}
.dot.y{{background:#fbbf24cc}}
.dot.g{{background:#4ade80cc}}
.cb-name{{margin-left:8px}}
.tabs{{display:flex;gap:4px;padding:8px 16px;background:#fafafa;border-bottom:1px solid #f4f4f5}}
.tab{{padding:4px 12px;font-size:0.6875rem;font-weight:500;border-radius:4px;border:none;cursor:pointer;background:#fafafa;color:#64748b;transition:all 0.15s}}
.tab.active{{background:#e0e7ff;color:#4338ca}}
.tab:hover:not(.active){{background:#f1f5f9}}
.cb-body{{padding:16px;display:flex;flex-direction:column;gap:6px}}
.cb-foot{{display:flex;gap:8px;padding:10px 16px;border-top:1px solid #f4f4f5}}
.btn-primary{{padding:4px 12px;font-size:0.6875rem;font-weight:500;background:#4f46e5;color:#fff;border-radius:8px;border:none;cursor:pointer;text-decoration:none;transition:background 0.15s}}
.btn-primary:hover{{background:#4338ca}}
.btn-secondary{{padding:4px 12px;font-size:0.6875rem;font-weight:500;background:#fff;color:#475569;border:1px solid #e2e8f0;border-radius:8px;cursor:pointer;transition:background 0.15s}}
.btn-secondary:hover{{background:#f8fafc}}
.nd{{padding:6px 12px;border-radius:8px;background:#f8fafc}}
.ndot{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}
.nname{{font-size:0.75rem;font-weight:500;color:#334155}}
.ntype{{font-size:0.5625rem;color:#94a3b8;font-family:ui-monospace,monospace;margin-left:auto}}
.ndesc{{font-size:0.6875rem;color:#94a3b8;line-height:1.4;margin-top:2px;margin-left:18px}}
.ed{{margin-left:20px;display:flex;align-items:center;gap:6px;font-size:0.625rem;color:#94a3b8;padding:2px 0}}
.earr{{color:#cbd5e1}}
.emode{{padding:1px 4px;border-radius:3px;font-size:0.5rem;font-family:ui-monospace,monospace;font-weight:500}}
.etgt{{color:#94a3b8}}
.yaml-body{{background:#18181b;border-radius:0;padding:16px;margin:0}}
.yaml-pre{{font-family:ui-monospace,SFMono-Regular,monospace;font-size:0.75rem;color:#e4e4e7;white-space:pre-wrap;word-break:break-all;line-height:1.6;margin:0}}
.warn{{color:#dc2626;font-style:italic;font-size:0.8rem}}
footer{{margin-top:3rem;padding-top:1rem;border-top:1px solid #e2e8f0;color:#94a3b8;font-size:0.75rem;text-align:center}}
footer a{{color:#4f46e5;text-decoration:none}}
footer a:hover{{text-decoration:underline}}
</style>
</head>
<body>
<h1>{_esc(name)}</h1>
{desc_html}
{author_html}
<div class="tags">{tags_html}</div>
<div class="stats">{t["sections"]}: {len(sections)} &middot; {t["workflows"]}: {total_wf} &middot; {t["total_nodes"]}: {total_nodes}</div>
{sections_html}
<footer>{t["generated_by"]} <a href="https://github.com/Archie0125/osop">OSOP</a> &middot; <code>osop view</code></footer>
<script>
function switchTab(wfId,tab){{
  document.getElementById(wfId+'-visual').style.display=tab==='visual'?'flex':'none';
  document.getElementById(wfId+'-yaml').style.display=tab==='yaml'?'block':'none';
  var cb=document.getElementById(wfId+'-visual').closest('.cb');
  var btns=cb.querySelectorAll('.tab');
  btns[0].className=tab==='visual'?'tab active':'tab';
  btns[1].className=tab==='yaml'?'tab active':'tab';
}}
function copyYaml(btn,wfId){{
  var pre=document.getElementById(wfId+'-yaml').querySelector('pre');
  if(pre){{var orig=btn.textContent;navigator.clipboard.writeText(pre.textContent).then(function(){{btn.textContent='{t["copied"]}';setTimeout(function(){{btn.textContent=orig}},1500)}})}}
}}
var _activeRun={{}};
function switchRun(wfId,idx,total){{
  _activeRun[wfId]=idx;
  for(var i=0;i<total;i++){{
    var v=document.getElementById(wfId+'r'+i+'-visual');
    var y=document.getElementById(wfId+'r'+i+'-yaml');
    if(v)v.style.display=i===idx?'flex':'none';
    if(y)y.style.display='none';
  }}
  var tabs=document.getElementById(wfId+'-runtabs').querySelectorAll('.tab');
  tabs.forEach(function(b,j){{b.className=j===idx?'tab active':'tab'}});
  var subs=document.getElementById(wfId+'-subtabs').querySelectorAll('.tab');
  subs[0].className='tab active';subs[1].className='tab';
}}
function switchLogView(wfId,view){{
  var idx=_activeRun[wfId]||0;
  var rid=wfId+'r'+idx;
  var v=document.getElementById(rid+'-visual');
  var y=document.getElementById(rid+'-yaml');
  var isExec=view!=='.osoplog';
  if(v)v.style.display=isExec?'flex':'none';
  if(y)y.style.display=isExec?'none':'block';
  var subs=document.getElementById(wfId+'-subtabs').querySelectorAll('.tab');
  subs[0].className=isExec?'tab active':'tab';
  subs[1].className=isExec?'tab':'tab active';
}}
function copyActiveLog(btn,wfId,total){{
  var idx=_activeRun[wfId]||0;
  var pre=document.getElementById(wfId+'r'+idx+'-yaml').querySelector('pre');
  if(pre){{var orig=btn.textContent;navigator.clipboard.writeText(pre.textContent).then(function(){{btn.textContent='{t["copied"]}';setTimeout(function(){{btn.textContent=orig}},1500)}})}}
}}
</script>
</body>
</html>'''


def _esc(text):
    """Escape HTML special characters."""
    if not isinstance(text, str):
        return str(text) if text is not None else ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")
