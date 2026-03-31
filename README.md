# osop

The official CLI runtime for [OSOP](https://github.com/Archie0125/osop-spec) — Open Standard Operating Process.

## Install

```bash
pip install osop
```

## Commands

| Command | Description |
|---------|-------------|
| `osop validate <file>` | Validate an .osop workflow file |
| `osop run <file>` | Execute a workflow |
| `osop render <file> --view story` | Render human-readable story view |
| `osop test <file>` | Run workflow tests |

## Quick Start

```bash
# Clone the spec examples
git clone https://github.com/Archie0125/osop-spec

# Validate a workflow
osop validate osop-spec/examples/pdf-ai-db.osop.yaml

# Render story view
osop render osop-spec/examples/pdf-ai-db.osop.yaml --view story
```

## Architecture

```
osop/
├── cli/          # CLI entry points
├── parser/       # YAML/JSON → IR
├── validator/    # Schema + graph + contract validation
├── ir/           # Intermediate Representation models
├── compiler/     # IR → Execution Plan
├── executor/     # Node lifecycle + retry/timeout
├── adapters/     # agent / api / cli / db / git / docker / cicd / mcp
├── renderers/    # story / graph / role / debug / agent views
├── ledger/       # Immutable run records
└── test_runner/  # unit / integration / e2e / simulation
```

## Related Repos

- [osop-spec](https://github.com/Archie0125/osop-spec) — Schema & specification
- [osop-mcp](https://github.com/Archie0125/osop-mcp) — MCP server _(coming soon)_
- [osop-openclaw-skill](https://github.com/Archie0125/osop-openclaw-skill) — OpenClaw skill _(coming soon)_

## License

MIT
