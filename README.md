# OSOP — Open Standard Operating Procedures

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![PyPI](https://img.shields.io/pypi/v/osop)](https://pypi.org/project/osop/)

**OSOP** is a universal protocol for defining, validating, and executing process definitions. It provides a structured YAML-based schema for workflows that can be parsed, rendered, tested, and run across any toolchain.

Website: [osop.ai](https://osop.ai) | GitHub: [github.com/osop](https://github.com/osop)

## Features

- **CLI tools** — `osop validate`, `osop render`, `osop run`, `osop test` for the full workflow lifecycle
- **12 node types** — start, end, step, decision, fork, join, loop, retry, approval, webhook, timer, subprocess
- **Parser** — reads `.osop.yaml` files and produces a validated AST
- **Executor** — runs workflows with pluggable adapters for each node type
- **Adapters** — extensible adapter system for custom node behavior
- **Schema validation** — JSON Schema-based validation with clear error reporting

## Installation

```bash
pip install osop
```

Requires Python 3.11 or later.

## Quick Start

```bash
# Validate a workflow definition
osop validate workflow.osop.yaml

# Render a workflow as a diagram
osop render workflow.osop.yaml --format mermaid

# Dry-run a workflow
osop run workflow.osop.yaml --dry-run

# Run tests defined in a workflow
osop test workflow.osop.yaml
```

## Workflow Example

```yaml
osop: "0.1"
name: deploy-service
description: Deploy a containerized service to production

nodes:
  - id: start
    type: start

  - id: build
    type: step
    action: docker build -t $IMAGE .

  - id: test
    type: step
    action: pytest tests/

  - id: approve
    type: approval
    approvers: [platform-team]

  - id: deploy
    type: step
    action: kubectl apply -f k8s/

  - id: end
    type: end

edges:
  - from: start
    to: build
  - from: build
    to: test
  - from: test
    to: approve
  - from: approve
    to: deploy
  - from: deploy
    to: end
```

## Node Types

| Type | Description |
|------|-------------|
| `start` | Entry point of the workflow |
| `end` | Terminal node |
| `step` | Single action or command |
| `decision` | Conditional branch based on expression |
| `fork` | Parallel split into multiple branches |
| `join` | Synchronization barrier for parallel branches |
| `loop` | Iterate over a collection or until a condition |
| `retry` | Retry a step with backoff strategy |
| `approval` | Human or automated approval gate |
| `webhook` | Wait for or send an HTTP callback |
| `timer` | Delay or cron-scheduled trigger |
| `subprocess` | Invoke another OSOP workflow |

## Architecture

```
osop/
  cli.py          # Click CLI entry points
  parser.py       # YAML parser and AST builder
  schema.py       # JSON Schema definitions and validation
  executor.py     # Workflow execution engine
  adapters/       # Node type adapters (one per type)
  models.py       # Data models for workflows, nodes, edges
  errors.py       # Custom exception hierarchy
```

## Development

```bash
git clone https://github.com/osop/osop.git
cd osop
pip install -e ".[dev]"
pytest
```

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.
