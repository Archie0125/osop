# OSOP CLI

**Validate, render, and run AI agent workflows.**

```bash
pip install osop
```

## Quick Start

```bash
# Validate against OSOP Core schema (4 node types)
osop validate --schema core my-workflow.osop.yaml

# Validate against full schema (12 node types)
osop validate my-workflow.osop.yaml

# Render as Mermaid diagram
osop render my-workflow.osop.yaml

# Execute (agent nodes call LLMs, cli nodes run commands)
osop run my-workflow.osop.yaml --dry-run

# Compare two workflows or execution logs
osop diff v1.osop.yaml v2.osop.yaml
```

## Commands

| Command | Description |
|---------|-------------|
| `osop validate <file>` | Validate against JSON Schema + contract checks |
| `osop validate --schema core <file>` | Validate against Core schema only |
| `osop render <file>` | Render as Mermaid diagram |
| `osop run <file>` | Execute the workflow |
| `osop diff <a> <b>` | Compare two .osop or .osoplog files |
| `osop init` | Scaffold a new workflow |
| `osop report <file> [log]` | Generate HTML/text report |

## OSOP Core Types

The `--schema core` option validates against the minimal schema:

**4 Node Types:** `agent`, `api`, `cli`, `human`
**4 Edge Modes:** `sequential`, `conditional`, `parallel`, `fallback`

## Example

```yaml
osop_version: "1.0"
id: "debug-session"
name: "AI Debugging Session"

nodes:
  - id: explore
    type: agent
    name: "Explore Codebase"
  - id: fix
    type: agent
    name: "Write Fix"
  - id: test
    type: cli
    name: "Run Tests"
    runtime:
      command: "npm test"
  - id: review
    type: human
    name: "User Reviews"

edges:
  - from: explore
    to: fix
  - from: fix
    to: test
  - from: test
    to: review
  - from: test
    to: fix
    mode: fallback
    label: "Tests failed"
```

## `osop run` Options

| Flag | Default | Description |
|------|---------|-------------|
| `--dry-run` | off | Preview without executing |
| `--allow-exec` | off | Allow CLI nodes to run commands |
| `--max-cost` | $1.00 | Maximum LLM spending |
| `--timeout` | 300s | Maximum execution time |
| `--log <path>` | none | Write .osoplog.yaml execution record |

## Links

- [Spec](https://github.com/Archie0125/osop-spec)
- [Visual Editor](https://osop-editor.vercel.app)
- [MCP Server](https://github.com/Archie0125/osop-mcp) (5 tools for Claude/Cursor)
- [Examples](https://github.com/Archie0125/osop-examples)

## License

Apache License 2.0
