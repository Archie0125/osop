import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

console = Console()


@click.group()
@click.version_option(package_name="osop")
def cli():
    """OSOP — what an AI agent actually did, from its transcript.

    The record comes from the Claude Code session transcript, not from the
    agent's own report.

      osop log    Build an .osoplog evidence record from a session transcript
      osop init   One-step project setup (sessions/ + CLAUDE.md)
    """
    pass


# ---------------------------------------------------------------------------
# osop init
# ---------------------------------------------------------------------------

_CLAUDE_MD_OSOP_SECTION = """\
## OSOP — Session Records

This project uses OSOP to record what AI agent sessions actually did, read
straight from the Claude Code transcript (not the agent's own report).

- `osop log [session-id]` — write `sessions/<date>-<desc>.osoplog.yaml` from a session transcript
- `osop init` — this setup

Run `osop log` after a multi-step task.
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

    if sessions_dir.exists():
        sessions_status = "already exists"
    else:
        sessions_dir.mkdir(exist_ok=True)
        sessions_status = "created"

    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        if "## OSOP" in content:
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
        f"Next: run a task, then [cyan]osop log[/cyan] to record it.",
        title="osop init",
        border_style="green",
    ))


# ---------------------------------------------------------------------------
# osop log — build an evidence record from a Claude Code transcript
# ---------------------------------------------------------------------------


@cli.command("log")
@click.argument("source", required=False)
@click.option("-d", "--desc", "short_desc", default=None,
              help="Short description for filenames (default: derived from date+session)")
@click.option("-o", "--out-dir", "out_dir", type=click.Path(), default=None,
              help="Output directory (default: ./sessions)")
@click.option("--stdout", is_flag=True, default=False,
              help="Print YAML to stdout instead of writing files")
def log_cmd(source, short_desc, out_dir, stdout):
    """Build an .osoplog evidence record from a Claude Code session transcript.

    Reads the canonical JSONL transcript and records every tool call with
    its real input, output, timing, and error flag — no LLM self-report.
    Each user prompt becomes a `human` node; the agent work that follows
    becomes one node per phase, with full per-call detail in `tool_calls[]`.

    SOURCE may be:
      - A path to a transcript .jsonl
      - A session id (looked up under ~/.claude/projects/*/<id>.jsonl)
      - omitted → most recent transcript for cwd

    Examples:
      osop log
      osop log 130fbd5c-b7d8-47bb-88b3-3eb9f91fba27
      osop log -d fix-auth-bug
      osop log /path/to/session.jsonl --stdout
    """
    from datetime import datetime as _dt

    from osop.recorder.transcript import (
        parse_transcript,
        resolve_transcript_path,
        synthesize,
        to_yaml,
    )

    try:
        path = resolve_transcript_path(source, cwd=Path.cwd())
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

    date_str = (parsed["started_at"] or _dt.utcnow().isoformat())[:10]
    if not short_desc:
        sid = (parsed.get("session_id") or path.stem)[:8]
        short_desc = f"{date_str}-session-{sid}"
    elif not short_desc.startswith(date_str):
        short_desc = f"{date_str}-{short_desc}"

    osoplog_doc = synthesize(parsed, short_desc=short_desc)
    osoplog_yaml = to_yaml(osoplog_doc)

    if stdout:
        # Bytes, not text: a cp950/cp1252 console cannot encode every character a transcript holds.
        sys.stdout.buffer.write(osoplog_yaml.encode("utf-8"))
        sys.stdout.buffer.flush()
        return

    target_dir = Path(out_dir) if out_dir else (Path.cwd() / "sessions")
    target_dir.mkdir(parents=True, exist_ok=True)
    log_path = target_dir / f"{short_desc}.osoplog.yaml"
    log_path.write_text(osoplog_yaml, encoding="utf-8", newline="\n")

    total_calls = sum(
        len(rec.get("tool_calls", [])) for rec in osoplog_doc["node_records"]
    )

    console.print(Panel(
        f"[bold]{osoplog_doc['workflow_id']}[/bold]\n"
        f"[dim]source:[/dim] {path}\n"
        f"[dim]nodes:[/dim] {len(osoplog_doc['node_records'])}  "
        f"[dim]tool calls:[/dim] {total_calls}  "
        f"[dim]duration:[/dim] {osoplog_doc['duration_ms']}ms\n"
        f"[green]{log_path}[/green]",
        title="osop log",
        border_style="green",
    ))
