# OSOP CLI

**Record what AI does. Repeat it for free.**

```bash
pip install osop
```

OSOP turns any Claude Code session into a re-runnable workflow. The two pillars:

- **`osop log`** — capture a real session (transcript-derived, not LLM self-report) into `.osop` + `.osoplog`
- **`osop replay`** — re-execute that captured `.osop` with a fresh AI agent, producing more `.osoplog`s for diff/optimize

Everything else exists to make this loop reliable.

## Quick Start — the record / repeat loop

```bash
osop init                                                   # 1. set up project

# 2. run a real Claude Code session, then:
osop log -d "fix-auth-bug"                                  # capture → sessions/

# 3. replay the captured workflow with a fresh AI run
osop replay sessions/<...>.osop.yaml \
  --max-budget-per-node 1.00 \
  --allowed-tools "Read,Edit,Write,Bash,Grep,Glob"

# 4. compare the runs
osop diff sessions/<...>.osoplog.yaml <new-run>.osoplog.yaml

# 5. (after several runs) synthesize a better workflow
osop optimize sessions/*.osoplog.yaml -o better.osop.yaml
```

## Commands

**Core loop (this is the product):**

| Command | What |
|---------|------|
| `osop log [session-id]` | **Record** — synthesize `.osop` + `.osoplog` from a Claude Code transcript |
| `osop replay <file.osop>` | **Repeat** — re-execute via `claude -p` per agent node, stream `.osoplog` |

**Supporting infrastructure:**

| Command | What |
|---------|------|
| `osop init` | One-step project setup (sessions/ + CLAUDE.md) |
| `osop validate <file>` | Schema check `.osop` or `.osoplog` |
| `osop record <file.osop>` | Full executor (cost limits / risk_assess / sub-agents) — for hand-authored workflows |
| `osop diff <a> <b>` | Side-by-side diff of two `.osop` or two `.osoplog` files |
| `osop optimize <logs...>` | Synthesize a better `.osop` from execution logs |
| `osop view <file.sop>` | Render `.sop` to standalone HTML |

## `osop replay` v2 — agent imitation flags

| Flag | Default | What |
|------|---------|------|
| `--max-budget-per-node` | $5.00 | USD ceiling per agent node (enforced by `claude -p`) |
| `--agent-max-turns` | 10 | Tool-call ceiling per agent node |
| `--allowed-tools` | `Read,Edit,Write,Bash,Grep,Glob,WebFetch` | Comma-separated allowlist |
| `--reference-log` | auto | Source-of-truth `.osoplog`; auto-discovered from same stem if omitted |
| `--no-agent` | off | Skip agent nodes (back to v1 cli+human-only behavior) |
| `--allow-exec` | off | Allow cli nodes to actually run shell commands |
| `--interactive` | off | Pause for input on human nodes |
| `--continue-on-error` | off | Don't halt on FAILED node |

## `osop record` Options (full executor — distinct from `replay`)

| Flag | Default | Description |
|------|---------|-------------|
| `--dry-run` | off | Preview without executing |
| `--allow-exec` | off | Allow CLI nodes to run commands |
| `--mock` | off | Simulate (no real executor) |
| `--max-cost` | $1.00 | Total LLM ceiling for the run |
| `--timeout` | 300s | Maximum execution time |
| `-o <path>` | auto | Write `.osoplog.yaml` to path |

## OSOP Core

**4 Node Types:** `agent`, `api`, `cli`, `human`
**4 Edge Modes:** `sequential`, `conditional`, `parallel`, `fallback`

## Links

- [Spec](https://github.com/Archie0125/osop-spec)
- [Visual Editor](https://osop-editor.vercel.app)
- [MCP Server](https://github.com/Archie0125/osop-mcp) (8 tools for Claude / Cursor / OpenClaw)
- [Examples](https://github.com/Archie0125/osop-examples)
- [Agent Rules](https://github.com/Archie0125/osop-agent-rules) (drop-in for 13+ agents)

## License

Apache License 2.0
