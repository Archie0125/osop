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

- **Claims**: what the agent's final message says it did, checked against the transcript. Only claims the transcript can judge are looked for: tests pass, lint passes, build succeeds, committed, pushed, merged (English and 繁體中文). Each gets `✅ backed`, `❌ contradicted`, `⚠️ cannot tell` or `❓ no evidence`, with the command that decided it. Rules only, no model: a question, an instruction or a negated sentence is not a claim
- **Goal**: the first prompt you typed
- **Tests**: every test run and its result, read from the exit status the tool reported and the whole output. `🟡 probably passed` means the output shows tests passing and no failure, but a pipe, `;` or `&` after the test command hid its exit status (`pytest | tail` reports `tail`'s exit code). `⚠️ unclear` means the status was hidden and the output does not settle it
- **Files changed**: files edited with Edit / MultiEdit / Write, plus shell commands that may have changed files (`>`, `tee`, `sed -i`, `mv`…). An edit with no result recorded is not counted
- **Errors**: every tool call that failed
- **No result recorded**: calls that never got a result, or were started in the background and no completion notice has arrived yet, so there is nothing to judge. When the notice arrives, its exit status counts, and what the command printed is read from the output file Claude Code names in it (the transcript never holds it); the receipt says so, and says when that file could not be read (Claude Code deletes old ones after about a week)
- **Git**: every `git commit` / `push` / `merge` and `gh pr merge`, with its result: what backs a "committed and pushed"
- **Sub-agents**, **Commands**: who ran what; the complete command table sits in a collapsed block

## Stop hook (optional)

`hooks/osop-stop.py` writes the receipt when a Claude Code turn ends and shows the one-line summary, claims first: `OSOP · claims: 1 contradicted ❌ (tests pass) · tests: 3 runs: … · receipt: …`. It checks the final message Claude Code hands the hook (`last_assistant_message`), so a transcript file that has not caught up yet does not matter. It is not installed for you: add it to `settings.json` under `hooks` → `Stop` with a python that has osop installed (see the file's docstring). Transcripts over 200 MB are skipped, and the summary says so (`OSOP_MAX_TRANSCRIPT_MB` changes the limit). Every Stop also appends the summary and the receipt's claim rows to `~/.osop/brief.log` (`OSOP_BRIEF_LOG` changes the path): the receipt is rewritten at each Stop, so this log is where you look back at what earlier turns claimed and how each claim was judged.

## Commands

| Command | What |
|---------|------|
| `osop log [session-id \| path]` | Write `receipt.md` from a Claude Code transcript (latest session for the current directory by default). `--brief` adds the one-line summary; `--last-message FILE` (or `-` for stdin) checks that text instead of the transcript's last assistant text |
| `osop init` | Add an OSOP section to the project's CLAUDE.md |

## What's next

A two-week trial by the owner: does the one-line summary ever change a decision?
Then parsers for other agents' transcripts (Codex CLI first).

## License

Apache License 2.0
