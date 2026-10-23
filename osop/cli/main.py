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

      osop log    Write a receipt of what the agent ran, from a session transcript
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

- `osop log [session-id]` — write `sessions/<date>-<desc>.receipt.md` from a session transcript
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
              help="Print the receipt to stdout instead of writing a file")
def log_cmd(source, short_desc, out_dir, stdout):
    """Write a receipt of what the agent actually did in a Claude Code session.

    Reads the canonical JSONL transcript, sub-agents included, and builds the
    receipt from real tool calls and their results: no LLM self-report.

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

    from osop.recorder.receipt import render_receipt
    from osop.recorder.transcript import parse_transcript, resolve_transcript_path

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

    receipt = render_receipt(parsed, source=path)

    if stdout:
        # Bytes, not text: a cp950/cp1252 console cannot encode every character a transcript holds.
        sys.stdout.buffer.write(receipt.encode("utf-8"))
        sys.stdout.buffer.flush()
        return

    target_dir = Path(out_dir) if out_dir else (Path.cwd() / "sessions")
    target_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = target_dir / f"{short_desc}.receipt.md"
    receipt_path.write_text(receipt, encoding="utf-8", newline="\n")

    console.print(Panel(
        f"[dim]source:[/dim] {path}\n[green]{receipt_path}[/green]",
        title="osop log",
        border_style="green",
    ))
