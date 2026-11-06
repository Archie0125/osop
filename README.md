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
# ...run a Claude Code session, then:
osop log                      # → ~/.osop/receipts/<project>/<date>-session-<id>.receipt.md
osop log -d fix-auth-bug      # name the receipt
osop log --brief              # the same, plus a one-line summary (what a Stop hook shows)
osop log --stdout             # print the receipt instead of writing a file
```

Receipts live under `~/.osop/receipts/`, never in the repo the agent worked on.

## The receipt

`receipt.md` is one page per session, sub-agents included. Facts first, the full command table collapsed at the end:

- **Goal**: the first prompt you typed
- **Tests**: every test run and its result, read from the exit status the tool reported and the whole output. `🟡 probably passed` means the output shows tests passing and no failure, but a pipe, `;` or `&` after the test command hid its exit status (`pytest | tail` reports `tail`'s exit code). `⚠️ unclear` means the status was hidden and the output does not settle it
- **Files changed**: files edited with Edit / MultiEdit / Write, plus shell commands that may have changed files (`>`, `tee`, `sed -i`, `mv`…). An edit with no result recorded is not counted
- **Errors**: every tool call that failed
- **No result recorded**: calls that never got a result or were started in the background, so there is nothing to judge
- **Git**: every `git commit` / `push` / `merge` and `gh pr merge`, with its result: what backs a "committed and pushed"
- **Sub-agents**, **Commands**: who ran what; the complete command table sits in a collapsed block

## Stop hook (optional)

`hooks/osop-stop.py` writes the receipt when a Claude Code turn ends and shows the one-line summary. It is not installed for you: add it to `settings.json` under `hooks` → `Stop` with a python that has osop installed (see the file's docstring).

## Commands

| Command | What |
|---------|------|
| `osop log [session-id \| path]` | Write `receipt.md` from a Claude Code transcript (latest session for the current directory by default) |
| `osop init` | Add an OSOP section to the project's CLAUDE.md |

## What's next

Check the agent's final "done / passed / verified" claims against the
receipt, and flag the ones with no evidence behind them.

## License

Apache License 2.0
