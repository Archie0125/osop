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
@click.option("--schema", "schema_variant", type=click.Choice(["full", "core"]), default="full",
              help="Schema variant: 'core' (4 node types, 4 edge modes) or 'full' (all types).")
def validate_cmd(path, schema_variant):
    """Validate an .osop workflow file against schema and contracts."""
    try:
        workflow = load_workflow(path)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)
    except Exception as e:
        console.print(f"[red]Parse error:[/red] {e}")
        raise SystemExit(1)

    errors = validate(workflow, schema_variant=schema_variant)

    if not errors:
        name = workflow.get("name", path)
        wf_id = workflow.get("id", "")
        version = workflow.get("osop_version", "")
        nodes = len(workflow.get("nodes", []))
        edges = len(workflow.get("edges", []))
        schema_label = f"[dim]schema:[/dim] {schema_variant}"
        console.print(Panel(
            f"[green]Valid[/green]  {name}\n"
            f"[dim]id:[/dim] {wf_id}  [dim]version:[/dim] {version}  "
            f"[dim]nodes:[/dim] {nodes}  [dim]edges:[/dim] {edges}  {schema_label}",
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

@cli.command("diff")
@click.argument("file_a", type=click.Path(exists=True))
@click.argument("file_b", type=click.Path(exists=True))
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table", help="Output format")
def diff_cmd(file_a, file_b, fmt):
    """Compare two .osop or .osoplog files side by side.

    Works with both workflow definitions (.osop) and execution logs (.osoplog).
    Detects file type automatically.
    """
    import sys, os, json

    # Detect if these are .osoplog files or .osop files
    is_log = file_a.endswith(".osoplog.yaml") or file_a.endswith(".osoplog.yml")

    diff_fn = None
    for search_path in [
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "osop-mcp"),
        os.path.join(os.path.expanduser("~"), "Desktop", "osop", "osop-mcp"),
        os.path.join(os.getcwd(), "osop-mcp"),
    ]:
        if os.path.isdir(os.path.join(search_path, "tools")):
            sys.path.insert(0, search_path)
            try:
                if is_log:
                    from tools.diff import diff_logs
                    diff_fn = diff_logs
                else:
                    from tools.diff import diff_workflows
                    diff_fn = diff_workflows
                break
            except ImportError:
                sys.path.pop(0)

    if diff_fn is None:
        console.print("[red]Error:[/red] osop-mcp not found.")
        raise SystemExit(1)

    if is_log:
        result = diff_fn(file_path_a=file_a, file_path_b=file_b)
    else:
        result = diff_fn(file_path_a=file_a, file_path_b=file_b)

    if fmt == "json":
        import json as j
        click.echo(j.dumps(result, indent=2, default=str))
        return

    # Table output
    if is_log:
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
        table.add_column("Duration (A → B)")
        table.add_column("Cost (A → B)")
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

                dur_str = f"{d['a_fmt']} → {d['b_fmt']}"
                if d["delta_ms"] != 0:
                    color = "green" if d["delta_ms"] < 0 else "red"
                    dur_str += f" [{color}]({d['delta_pct']})[/{color}]"

                cost_str = ""
                if c["a"] > 0 or c["b"] > 0:
                    cost_str = f"${c['a']:.4f} → ${c['b']:.4f}"
                    if c["delta"] != 0:
                        color = "green" if c["delta"] < 0 else "red"
                        cost_str += f" [{color}]({c['delta_pct']})[/{color}]"

                status_str = s["a"]
                if s["changed"]:
                    status_str = f"[yellow]{s['a']} → {s['b']}[/yellow]"

                table.add_row(nd["node_id"], nd["node_type"], dur_str, cost_str, status_str)

        console.print(table)

        # Summary line
        dur_color = "green" if agg["duration_delta_ms"] < 0 else "red" if agg["duration_delta_ms"] > 0 else "white"
        console.print(
            f"\n[bold]Summary:[/bold] "
            f"[{dur_color}]{agg['duration_delta_pct']} duration[/{dur_color}] | "
            f"{agg['nodes_added']} added, {agg['nodes_removed']} removed, {agg['nodes_modified']} modified"
        )
    else:
        # Workflow diff (existing format)
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
@click.option("--allow-exec", is_flag=True, default=False, help="Allow CLI nodes to execute shell commands")
@click.option("--dry-run", is_flag=True, default=False, help="Simulate without executing")
@click.option("--interactive", is_flag=True, default=False, help="Enable human node input via stdin")
@click.option("--max-cost", type=float, default=1.0, help="Maximum LLM cost in USD (default: $1.00)")
@click.option("--timeout", type=int, default=300, help="Maximum execution time in seconds")
@click.option("--log", "log_path", type=str, default=None, help="Write .osoplog.yaml to this path")
def run_cmd(path, allow_exec, dry_run, interactive, max_cost, timeout, log_path):
    """Execute an .osop workflow with real agent/CLI/API execution."""
    import sys
    import os

    # Try to import the real executor from osop-mcp
    executor = None
    for search_path in [
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "osop-mcp"),
        os.path.join(os.path.expanduser("~"), "Desktop", "osop", "osop-mcp"),
        os.path.join(os.getcwd(), "..", "osop-mcp"),
        os.path.join(os.getcwd(), "osop-mcp"),
    ]:
        tools_path = os.path.join(search_path, "tools")
        if os.path.isdir(tools_path):
            sys.path.insert(0, search_path)
            try:
                from tools.execute import execute as real_execute
                executor = real_execute
                break
            except ImportError:
                sys.path.pop(0)

    if executor is None:
        # Fallback: mock mode
        try:
            workflow = load_workflow(path)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise SystemExit(1)
        name = workflow.get("name", path)
        nodes = workflow.get("nodes", [])
        console.print(Panel(f"[bold]{name}[/bold]\n[dim]mock mode (osop-mcp not found)[/dim]",
                            title="osop run", border_style="yellow"))
        for node in nodes:
            nid = node.get("id")
            ntype = node.get("type")
            purpose = node.get("purpose", "") or node.get("description", "")
            console.print(f"  [blue]→[/blue] [{ntype}] [bold]{nid}[/bold]  [dim]{purpose[:60]}[/dim]")
        console.print(f"\n[yellow]Run complete[/yellow] (mock) — {len(nodes)} nodes listed")
        return

    # Real execution
    console.print(Panel(
        f"[bold]osop run[/bold]\n"
        f"File: {path}\n"
        f"allow_exec={allow_exec}  dry_run={dry_run}  interactive={interactive}\n"
        f"max_cost=${max_cost:.2f}  timeout={timeout}s",
        title="OSOP Executor", border_style="blue"
    ))

    result = executor(
        file_path=path,
        dry_run=dry_run,
        allow_exec=allow_exec,
        interactive=interactive,
        timeout_seconds=timeout,
        max_cost_usd=max_cost,
    )

    # Display results
    run_status = result.get("status", "unknown")
    color = "green" if run_status == "completed" else "red" if run_status == "failed" else "yellow"

    if run_status == "blocked":
        console.print(f"\n[red]BLOCKED:[/red] {result.get('reason', '')}")
        cli_cmds = result.get("cli_commands", [])
        if cli_cmds:
            console.print("\n[yellow]CLI commands in workflow:[/yellow]")
            for cmd in cli_cmds:
                console.print(f"  [{cmd['node']}] {cmd['command']}")
            console.print("\n[dim]Add --allow-exec to permit shell execution.[/dim]")
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
    console.print(f"\n[{color}]{run_status.upper()}[/{color}] — "
                  f"{result.get('executed', 0)} executed, {result.get('skipped', 0)} skipped, {result.get('failed', 0)} failed"
                  f" — {result.get('duration_ms', 0)}ms"
                  f" — ${result.get('total_cost_usd', 0):.4f}")

    # Write osoplog if requested
    if log_path and run_status != "blocked":
        try:
            # Load workflow data for osoplog generation
            workflow_data = load_workflow(path)
            from pathlib import Path as P
            osoplog_mod = None
            for sp in [
                os.path.join(os.path.dirname(__file__), "..", "..", "..", "osop-mcp"),
                os.path.join(os.path.expanduser("~"), "Desktop", "osop", "osop-mcp"),
            ]:
                if os.path.isdir(os.path.join(sp, "tools")):
                    sys.path.insert(0, sp)
                    try:
                        from tools.osoplog import generate_osoplog
                        osoplog_content = generate_osoplog(workflow_data, result)
                        P(log_path).write_text(osoplog_content, encoding="utf-8")
                        console.print(f"\n[green]osoplog written to {log_path}[/green]")
                        break
                    except ImportError:
                        sys.path.pop(0)
        except Exception as e:
            console.print(f"\n[yellow]Could not write osoplog: {e}[/yellow]")

    if run_status != "completed":
        raise SystemExit(1)

@cli.command("init")
@click.option("--name", prompt="Workflow name", help="Name of the workflow")
@click.option("--id", "wf_id", default=None, help="Workflow ID (auto-generated from name if omitted)")
@click.option("--type", "wf_type", type=click.Choice(["agent", "devops", "business", "data", "custom"]),
              prompt="Workflow type", help="Type of workflow to scaffold")
@click.option("-o", "--output", default=None, help="Output file path (default: <id>.osop.yaml)")
def init_cmd(name, wf_id, wf_type, output):
    """Scaffold a new .osop workflow file interactively."""
    import re
    from pathlib import Path

    if not wf_id:
        wf_id = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')

    if not output:
        output = f"{wf_id}.osop.yaml"

    templates = {
        "agent": {
            "nodes": [
                {"id": "planner", "type": "agent", "name": "Planner",
                 "purpose": "Plan the approach and break down the task.",
                 "runtime": {"provider": "anthropic", "model": "claude-sonnet-4-20250514",
                             "system_prompt": "You are a planning agent. Break down tasks into clear steps."},
                 "outputs": ["plan"]},
                {"id": "executor", "type": "agent", "name": "Executor",
                 "purpose": "Execute the plan step by step.",
                 "inputs": ["plan"],
                 "runtime": {"provider": "anthropic", "model": "claude-sonnet-4-20250514",
                             "system_prompt": "You are an execution agent. Follow the plan precisely."},
                 "outputs": ["result"]},
                {"id": "reviewer", "type": "human", "name": "Human Review",
                 "purpose": "Review and approve the final result.",
                 "inputs": ["result"]},
            ],
            "edges": [
                {"from": "planner", "to": "executor", "mode": "sequential"},
                {"from": "executor", "to": "reviewer", "mode": "sequential"},
            ],
            "tags": ["agent", "ai", "multi-step"],
        },
        "devops": {
            "nodes": [
                {"id": "build", "type": "cli", "name": "Build",
                 "purpose": "Build the project.",
                 "runtime": {"command": "npm run build"}},
                {"id": "test", "type": "cli", "name": "Test",
                 "purpose": "Run the test suite.",
                 "runtime": {"command": "npm test"}},
                {"id": "deploy", "type": "cli", "name": "Deploy",
                 "purpose": "Deploy to production.",
                 "runtime": {"command": "echo 'deploy command here'"},
                 "security": {"risk_level": "high"},
                 "approval_gate": {"required": True, "approver_role": "admin"}},
            ],
            "edges": [
                {"from": "build", "to": "test", "mode": "sequential"},
                {"from": "test", "to": "deploy", "mode": "sequential"},
            ],
            "tags": ["devops", "cicd", "deployment"],
        },
        "business": {
            "nodes": [
                {"id": "request", "type": "human", "name": "Submit Request",
                 "purpose": "User submits a request for processing."},
                {"id": "review", "type": "human", "name": "Manager Review",
                 "purpose": "Manager reviews and approves the request.",
                 "approval_gate": {"required": True}},
                {"id": "process", "type": "agent", "name": "Process Request",
                 "purpose": "AI processes the approved request.",
                 "runtime": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"}},
                {"id": "notify", "type": "api", "name": "Send Notification",
                 "purpose": "Notify stakeholders of completion.",
                 "runtime": {"url": "https://hooks.slack.com/services/...", "method": "POST"}},
            ],
            "edges": [
                {"from": "request", "to": "review", "mode": "sequential"},
                {"from": "review", "to": "process", "mode": "sequential"},
                {"from": "process", "to": "notify", "mode": "sequential"},
            ],
            "tags": ["business", "approval", "notification"],
        },
        "data": {
            "nodes": [
                {"id": "extract", "type": "api", "name": "Extract Data",
                 "purpose": "Fetch data from the source API.",
                 "runtime": {"url": "https://api.example.com/data", "method": "GET"}},
                {"id": "transform", "type": "agent", "name": "Transform",
                 "purpose": "Clean and transform the raw data.",
                 "runtime": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"}},
                {"id": "load", "type": "db", "name": "Load",
                 "purpose": "Write transformed data to the database."},
            ],
            "edges": [
                {"from": "extract", "to": "transform", "mode": "sequential"},
                {"from": "transform", "to": "load", "mode": "sequential"},
            ],
            "tags": ["data", "etl", "pipeline"],
        },
        "custom": {
            "nodes": [
                {"id": "step_1", "type": "agent", "name": "Step 1",
                 "purpose": "Describe what this step does."},
                {"id": "step_2", "type": "cli", "name": "Step 2",
                 "purpose": "Describe what this step does.",
                 "runtime": {"command": "echo 'your command here'"}},
            ],
            "edges": [
                {"from": "step_1", "to": "step_2", "mode": "sequential"},
            ],
            "tags": ["custom"],
        },
    }

    template = templates[wf_type]
    import yaml
    workflow = {
        "osop_version": "2.0",
        "id": wf_id,
        "name": name,
        "description": f"TODO: Describe your {wf_type} workflow.",
        "tags": template["tags"],
        "nodes": template["nodes"],
        "edges": template["edges"],
    }

    yaml_content = yaml.dump(workflow, default_flow_style=False, allow_unicode=True, sort_keys=False)
    # Add header comment
    header = f"# {name}\n# Generated by: osop init\n# Type: {wf_type}\n# Edit this file to customize your workflow.\n\n"
    full_content = header + yaml_content

    Path(output).write_text(full_content, encoding="utf-8")
    console.print(f"\n[green]Created {output}[/green]")
    console.print(f"  Nodes: {len(template['nodes'])}")
    console.print(f"  Edges: {len(template['edges'])}")
    console.print(f"\nNext steps:")
    console.print(f"  osop validate {output}")
    console.print(f"  osop run {output} --dry-run")
    console.print(f"  osop render {output} --format mermaid")


@cli.command("validate-log")
@click.argument("path")
def validate_log_cmd(path):
    """Validate an .osoplog.yaml execution log file."""
    import json
    from pathlib import Path

    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] File not found: {path}")
        raise SystemExit(1)

    import yaml
    try:
        data = yaml.safe_load(raw)
    except Exception as e:
        console.print(f"[red]YAML parse error:[/red] {e}")
        raise SystemExit(1)

    if not isinstance(data, dict):
        console.print("[red]Error:[/red] osoplog must be a YAML mapping")
        raise SystemExit(1)

    errors = []
    warnings = []

    # Check required fields
    for field in ["osoplog_version", "run_id", "workflow_id", "status"]:
        if field not in data:
            errors.append(f"Missing required field: {field}")

    # Check status enum
    valid_statuses = {"COMPLETED", "FAILED", "TIMEOUT", "COST_LIMIT", "BLOCKED", "DRY_RUN"}
    if data.get("status") and data["status"] not in valid_statuses:
        warnings.append(f"Unexpected status: {data['status']} (expected one of {valid_statuses})")

    # Check node_records
    records = data.get("node_records", [])
    if not isinstance(records, list):
        errors.append("node_records must be a list")
    else:
        for i, rec in enumerate(records):
            if not isinstance(rec, dict):
                errors.append(f"node_records[{i}]: must be a mapping")
                continue
            if "node_id" not in rec:
                errors.append(f"node_records[{i}]: missing node_id")
            rec_status = rec.get("status", "")
            valid_node_statuses = {"COMPLETED", "FAILED", "SKIPPED", "DRY_RUN", "TIMEOUT", "ERROR"}
            if rec_status and rec_status not in valid_node_statuses:
                warnings.append(f"node_records[{i}] ({rec.get('node_id', '?')}): unexpected status '{rec_status}'")

    # Check timestamps
    for field in ["started_at", "ended_at"]:
        val = data.get(field, "")
        if val and isinstance(val, str) and "T" not in val:
            warnings.append(f"{field}: should be ISO 8601 format (got '{val}')")

    # Check duration
    dur = data.get("duration_ms")
    if dur is not None and not isinstance(dur, (int, float)):
        errors.append(f"duration_ms must be a number (got {type(dur).__name__})")

    # Try JSON schema validation if schema exists
    schema_found = False
    import os, sys
    for search_path in [
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "osop-spec", "schema", "osoplog.schema.json"),
        os.path.join(os.path.expanduser("~"), "Desktop", "osop", "osop-spec", "schema", "osoplog.schema.json"),
    ]:
        if os.path.isfile(search_path):
            try:
                import jsonschema
                with open(search_path, encoding="utf-8") as f:
                    schema = json.load(f)
                validator = jsonschema.Draft202012Validator(schema)
                for err in validator.iter_errors(data):
                    path = " > ".join(str(p) for p in err.absolute_path) or "(root)"
                    errors.append(f"Schema: {path}: {err.message}")
                schema_found = True
            except ImportError:
                warnings.append("jsonschema not installed; skipping schema validation")
            except Exception as e:
                warnings.append(f"Schema validation error: {e}")
            break

    # Report
    if errors:
        console.print(f"[red]INVALID[/red] osoplog: {path}")
        for e in errors:
            console.print(f"  [red]Error:[/red] {e}")
        for w in warnings:
            console.print(f"  [yellow]Warning:[/yellow] {w}")
        raise SystemExit(1)
    else:
        node_count = len(records)
        wf_id = data.get("workflow_id", "?")
        status = data.get("status", "?")
        dur_ms = data.get("duration_ms", 0)
        console.print(f"[green]Valid[/green] osoplog: {path}")
        console.print(f"  Workflow: {wf_id} | Status: {status} | Nodes: {node_count} | Duration: {dur_ms}ms")
        if not schema_found:
            console.print(f"  [dim](structural check only; osoplog.schema.json not found)[/dim]")
        if warnings:
            for w in warnings:
                console.print(f"  [yellow]Warning:[/yellow] {w}")


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


@cli.command("synthesize")
@click.argument("log_files", nargs=-1, required=True)
@click.option("--base", type=click.Path(exists=True), default=None, help="Base .osop file to optimize (optional)")
@click.option("--goal", type=str, default="", help="Optimization goal (e.g., 'reduce cost', 'speed up step 3')")
@click.option("--provider", type=str, default="anthropic", help="LLM provider (anthropic or openai)")
@click.option("--model", type=str, default="", help="LLM model to use")
@click.option("-o", "--output", type=click.Path(), default=None, help="Write optimized .osop to file")
@click.option("--prompt-only", is_flag=True, default=False, help="Output the synthesis prompt without calling LLM (use with any AI)")
def synthesize_cmd(log_files, base, goal, provider, model, output, prompt_only):
    """Synthesize an optimized .osop from multiple execution logs.

    Feed your .osoplog files to AI. Get back a better workflow.

    Examples:
      osop synthesize run1.osoplog.yaml run2.osoplog.yaml run3.osoplog.yaml
      osop synthesize sessions/*.osoplog.yaml --base my-workflow.osop.yaml
      osop synthesize *.osoplog.yaml --goal "reduce LLM cost" -o optimized.osop.yaml
    """
    import sys
    import os
    from pathlib import Path

    # Find synthesize module
    synthesizer = None
    for search_path in [
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "osop-mcp"),
        os.path.join(os.path.expanduser("~"), "Desktop", "osop", "osop-mcp"),
        os.path.join(os.getcwd(), "osop-mcp"),
    ]:
        tools_path = os.path.join(search_path, "tools")
        if os.path.isdir(tools_path):
            sys.path.insert(0, search_path)
            try:
                from tools.synthesize import synthesize as synth_fn
                synthesizer = synth_fn
                break
            except ImportError:
                sys.path.pop(0)

    if synthesizer is None:
        console.print("[red]Error:[/red] osop-mcp not found. Cannot run synthesize without the MCP tools.")
        raise SystemExit(1)

    # Expand glob patterns (Click gives us the resolved paths)
    paths = list(log_files)
    console.print(Panel(
        f"[bold]osop synthesize[/bold]\n"
        f"Logs: {len(paths)} file(s)\n"
        f"Base: {base or '(none, generating from scratch)'}\n"
        f"Goal: {goal or '(general optimization)'}\n"
        f"Provider: {provider}",
        title="OSOP Workflow Synthesizer", border_style="purple"
    ))

    console.print(f"\nAnalyzing {len(paths)} execution log(s)...")

    result = synthesizer(
        log_paths=paths,
        base_osop_path=base,
        goal=goal,
        provider=provider,
        model=model,
        prompt_only=prompt_only,
    )

    # Prompt-only mode: just output the prompt
    if prompt_only and result.get("status") == "prompt_ready":
        stats = result.get("stats", {})
        console.print(f"\n[bold]Stats:[/bold] {stats.get('total_runs', 0)} runs, {len(stats.get('node_summaries', {}))} unique nodes")
        console.print(f"\n[bold]Synthesis Prompt:[/bold] (paste this to any AI)\n")
        click.echo(result["prompt"])
        if output:
            from pathlib import Path as P
            P(output).write_text(result["prompt"], encoding="utf-8")
            console.print(f"\n[green]Prompt saved to {output}[/green]")
        return

    if result["status"] == "failed":
        console.print(f"\n[red]FAILED:[/red] {result.get('error', 'Unknown error')}")
        # Still show stats if available
        stats = result.get("stats", {})
        if stats:
            console.print(f"\n[dim]Stats collected before failure:[/dim]")
            console.print(f"  Runs: {stats.get('total_runs', 0)} | Avg duration: {stats.get('avg_duration_ms', 0)}ms")
        raise SystemExit(1)

    # Show stats
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

    # Show insights
    insights = result.get("insights", "")
    if insights:
        console.print(f"\n[bold]AI Insights:[/bold]")
        console.print(f"  {insights[:500]}")

    # Show optimized YAML
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
    console.print(f"\n[green]DONE[/green] — synthesis cost: ${cost:.4f}")


@cli.command("report")
@click.argument("osop_file", type=click.Path(exists=True))
@click.argument("log_file", type=click.Path(exists=True), required=False, default=None)
@click.option("--format", "fmt", type=click.Choice(["html", "text", "ansi"]), default=None,
              help="Output format (auto-detected if omitted)")
@click.option("-o", "--output", type=click.Path(), default=None,
              help="Write report to file instead of stdout")
def report_cmd(osop_file, log_file, fmt, output):
    """Generate an HTML or text report from an .osop file and optional .osoplog."""
    import sys
    from pathlib import Path
    from osop.reporters.html import generate_html_report
    from osop.reporters.text import generate_text_report

    # Auto-detect format
    if fmt is None:
        if output and output.endswith(".html"):
            fmt = "html"
        elif sys.stdout.isatty():
            fmt = "ansi"
        else:
            fmt = "text"

    # Read input files
    try:
        osop_yaml = Path(osop_file).read_text(encoding="utf-8")
    except Exception as e:
        console.print(f"[red]Error reading {osop_file}:[/red] {e}")
        raise SystemExit(1)

    log_yaml: str | None = None
    if log_file:
        try:
            log_yaml = Path(log_file).read_text(encoding="utf-8")
        except Exception as e:
            console.print(f"[red]Error reading {log_file}:[/red] {e}")
            raise SystemExit(1)

    # Generate report
    if fmt == "html":
        result = generate_html_report(osop_yaml, log_yaml)
    elif fmt == "ansi":
        result = generate_text_report(osop_yaml, log_yaml, ansi=True)
    else:
        result = generate_text_report(osop_yaml, log_yaml, ansi=False)

    # Output
    if output:
        try:
            Path(output).write_text(result, encoding="utf-8")
            console.print(f"[green]Report written to {output}[/green] ({len(result):,} bytes)")
        except Exception as e:
            console.print(f"[red]Error writing {output}:[/red] {e}")
            raise SystemExit(1)
    else:
        click.echo(result)
