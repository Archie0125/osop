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
osop log -d fix-auth-bug      # → sessions/<date>-fix-auth-bug.receipt.md
                              #   sessions/<date>-fix-auth-bug.osoplog.yaml
osop log --stdout             # print the .osoplog instead of writing files
```

## The receipt

`receipt.md` is one page per session, sub-agents included:

- **Goal**: the first prompt you typed
- **Tests**: every test run and its result. `✅` / `❌` come from the exit status the tool reported. `⚠️ unclear` means a pipe or `;` after the test command hid that status (`pytest | tail` reports `tail`'s exit code)
- **Commands**, **Files changed**, **Errors**, **Sub-agents**: what actually ran, and who ran it

## Commands

| Command | What |
|---------|------|
| `osop log [session-id \| path]` | Write `receipt.md` + `.osoplog` from a Claude Code transcript (latest session for the current directory by default) |
| `osop init` | One-step project setup |

## What's next

Check the agent's final "done / passed / verified" claims against the
receipt, and flag the ones with no evidence behind them.

## License

Apache License 2.0
