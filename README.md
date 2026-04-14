# OSOP CLI

**Validate, record, diff, optimize, and view AI agent workflows.**

```bash
pip install osop
```

## Quick Start

```bash
# Validate against OSOP Core schema (4 node types)
osop validate my-workflow.osop.yaml

# Execute workflow, produce .osoplog
osop record my-workflow.osop.yaml

# Compare two execution logs
osop diff run-a.osoplog.yaml run-b.osoplog.yaml

# Synthesize better workflow from logs
osop optimize sessions/*.osoplog.yaml -o optimized.osop.yaml
```

## Commands

| Command | Description |
|---------|-------------|
| `osop validate <file>` | Validate .osop or .osoplog against schema |
| `osop record <file>` | Execute workflow, produce .osoplog |
| `osop diff <a> <b>` | Compare two .osop or .osoplog files |
| `osop optimize <logs...>` | Synthesize better .osop from execution logs |
| `osop view <file.sop>` | Render .sop into standalone HTML |

## `osop record` Options

| Flag | Default | Description |
|------|---------|-------------|
| `--dry-run` | off | Preview without executing |
| `--allow-exec` | off | Allow CLI nodes to run commands |
| `--mock` | off | Simulate execution (no real executor needed) |
| `--max-cost` | $1.00 | Maximum LLM spending |
| `--timeout` | 300s | Maximum execution time |
| `-o <path>` | auto | Write .osoplog.yaml to path |

## OSOP Core

**4 Node Types:** `agent`, `api`, `cli`, `human`
**4 Edge Modes:** `sequential`, `conditional`, `parallel`, `fallback`

## Links

- [Spec](https://github.com/Archie0125/osop-spec)
- [Visual Editor](https://osop-editor.vercel.app)
- [MCP Server](https://github.com/Archie0125/osop-mcp) (4 tools for Claude/Cursor)
- [Examples](https://github.com/Archie0125/osop-examples)

## License

Apache License 2.0
