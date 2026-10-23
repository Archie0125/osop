# OSOP CLI

**What an AI agent actually did — read from its transcript, not its own report.**

```bash
pip install git+https://github.com/Archie0125/osop
```

> Not on PyPI yet. `pip install osop` installs an unrelated package with the same name.

Agents say "done" and "tests pass". OSOP checks the Claude Code session
transcript instead: every command the agent ran, what came back, and
whether it failed. No cooperation from the agent, no LLM, zero tokens.

## Quick start

```bash
osop init                     # once per project: sessions/ + a CLAUDE.md section
# ...run a Claude Code session, then:
osop log -d fix-auth-bug      # → sessions/<date>-fix-auth-bug.osoplog.yaml
osop log --stdout             # print instead of writing
```

## Commands

| Command | What |
|---------|------|
| `osop log [session-id \| path]` | Build an `.osoplog` evidence record from a Claude Code transcript (latest session for the current directory by default) |
| `osop init` | One-step project setup |

## What's next

A one-page receipt per session that flags claims made without evidence
("tests pass" with no passing test run). Not built yet.

## License

Apache License 2.0
