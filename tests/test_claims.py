"""Claims: what the final message says, checked against what the transcript shows."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from osop.cli.main import cli
from osop.recorder import parse_transcript
from osop.recorder.claims import find_claims, sentences
from osop.recorder.receipt import render_brief, render_receipt

FIXTURES = Path(__file__).parent / "fixtures"
CONTRADICTED = FIXTURES / "claims_contradicted.jsonl"


def _session(tmp_path: Path, calls: list, final: str | None, cwd: str = "/work/demo") -> Path:
    """One prompt, then (tool, input, output, is_error) per call a minute apart, then the agent's final text."""
    events = [{"type": "user", "timestamp": "2026-11-06T10:00:00Z", "sessionId": "s", "cwd": cwd,
               "message": {"role": "user", "content": "do the task"}}]
    for i, (tool, inp, output, is_error) in enumerate(calls, 1):
        events.append({"type": "assistant", "timestamp": f"2026-11-06T10:{i:02d}:00Z", "message": {
            "model": "m", "content": [{"type": "tool_use", "id": f"t{i}", "name": tool, "input": inp}]}})
        events.append({"type": "user", "timestamp": f"2026-11-06T10:{i:02d}:30Z",
                       "message": {"role": "user", "content": [
                           {"type": "tool_result", "tool_use_id": f"t{i}", "content": output, "is_error": is_error}]}})
    if final is not None:
        events.append({"type": "assistant", "timestamp": "2026-11-06T10:59:00Z",
                       "message": {"model": "m", "content": [{"type": "text", "text": final}]}})
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")
    return path


def _claims(path: Path) -> str:
    receipt = render_receipt(parse_transcript(path), source=path)
    return receipt.split("## Claims\n", 1)[1].split("\n## ", 1)[0]


def _kinds(text: str) -> list[str]:
    return [c.kind for c in find_claims(text)]


# --- finding claims in text --------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "All 3 tests pass.", "151 passed, exit 0.", "the suite is green: they all pass",
    "全套測試綠：2,783 通過、0 失敗、19 skipped", "相關測試 113 個全部通過，截圖也確認過了",
    "JS 224、PHP 63 passed", "測試 15 條綠", "單獨重跑那個檔是 45/45 全過",
])
def test_tests_pass_claims(text):
    assert _kinds(text) == ["tests"]


@pytest.mark.parametrize("text, kind", [
    ("`tsc` 通過，build 成功", "lint"), ("型別檢查和 lint 都沒問題", "lint"), ("`npx tsc --noEmit` 0 錯", "lint"),
    ("npm run build OK", "build"), ("已經重新 build", "build"), ("build 和 typecheck 都過", "build"),
    ("已 commit 成 `5d74ce3d`，還沒 push", "commit"), ("都 commit 好了", "commit"),
    ("The fix is committed as abc1234.", "commit"),
    ("已 push 到 `origin/main`（8aca7df9）", "push"), ("push 完成，上線指令也準備好了", "push"),
    ("已經推上去了", "push"), ("Pushed to origin/main.", "push"), ("都推了，剛查過", "push"),
    ("PR #10 已經 merge", "merge"), ("#6–#10 全部 merged，沒有 open PR", "merge"),
    ("feat/x is merged into main", "merge"),
])
def test_other_claims(text, kind):
    assert kind in _kinds(text), text


def test_one_sentence_can_hold_several_claims():
    assert _kinds("相關的兩個測試檔 37 題都通過，已經 build，已 commit、已 push 到 origin/main") == [
        "tests", "build", "commit", "push"]


@pytest.mark.parametrize("text", [
    # questions and instructions
    "要 commit → push 嗎", "要我現在把 bbd9bc4a push 上去嗎？", "Say \"commit\" when you want it committed.",
    "要先把內容另外 commit 到一個分支，然後由你部署", "CI 綠了就合進 main 並 push", "等兩個背景任務：測試要全綠",
    "push 到 main，pre-push hook 會跑全套測試，約 2.5 分鐘", "I will push once the suite is green.",
    # negated
    "未 commit", "還沒 push，也沒有上線", "Nothing is committed or pushed yet.", "本機沒有「已 commit 未 push」的東西",
    "這次我沒有跑測試，這批改動目前是不是全綠還不知道", "測試沒通過", "1 failed, 2 passed",
    # the word, not the deed
    "比已 push 的多 2 個 commit", "少一個已經推上去的 commit", "已經 build 好的前台 bundle 裡同樣是這三個名稱",
    "從 commit 到開 10% 一路做完", "sitemap 也已提交", "IndexNow 45 個網址 push 成功（status 200）",
    "4 篇合併完成：舊檔已刪", "伺服器把它們合併進原本的資料", "How the PDF is merged", "W2 建置完成：七項全過審",
    "付款檢查全部通過", "通過 Google 驗證", "", "Running the tests.",
])
def test_not_claims(text):
    assert _kinds(text) == [], text


def test_code_blocks_are_not_claims_but_inline_code_is_read():
    assert _kinds("Run it:\n```\npytest -q\n3 passed\n```\nthen decide.") == []
    assert _kinds("`php artisan test` 201 passed") == ["tests"]


def test_sentences_split_on_stops_and_lines_but_keep_table_rows():
    assert sentences("測試全過；全套還沒跑，等你決定再跑\n| 程式＋seeder | 已 commit、已 push |") == [
        "測試全過", "全套還沒跑，等你決定再跑", "程式＋seeder 已 commit、已 push"]
    assert _kinds("測試全過；全套還沒跑，等你決定再跑") == ["tests"]


# --- verdicts from the transcript --------------------------------------------------------------------


def test_fixture_says_tests_pass_but_the_last_run_failed():
    claims = _claims(CONTRADICTED)
    assert "tests pass" in claims and "❌ contradicted" in claims and "exit 1" in claims
    assert "committed" in claims and "❓ no evidence" in claims and "no `git commit` command ran" in claims
    brief = render_brief(parse_transcript(CONTRADICTED), "r.md")
    assert "claims: 1 contradicted ❌ (tests pass), 1 without evidence ❓ (committed)" in brief


def test_backed_tests_and_push(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "python -m pytest -q"}, "3 passed in 0.1s", False),
        ("Bash", {"command": "git commit -m x && git push origin main"}, "[main abc1234] x\nTo github.com", False),
    ], "3 passed. Committed and pushed to origin/main."))
    rows = [line for line in claims.splitlines() if line.startswith("| ")][1:]
    assert all("✅ backed" in r for r in rows) and len(rows) == 3, claims
    assert "last test run `python -m pytest -q` at 10:01 → ✅ passed" in claims


def test_tests_claim_is_unclear_when_the_status_is_hidden_or_the_project_changed_after(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "python -m pytest -q | tail -3"}, "collected 5 items", False)], "tests pass"))
    assert "⚠️ cannot tell" in claims and "hides its exit status" in claims
    # A pipe with a clean pass summary in the output is "probably passed": the evidence column says so.
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "python -m pytest -q | tail -3"}, "3 passed in 0.1s", False)], "tests pass"))
    assert "✅ backed" in claims and "🟡 probably passed" in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "python -m pytest -q"}, "3 passed", False),
        ("Edit", {"file_path": "/work/demo/src/app.py", "old_string": "a", "new_string": "b"}, "ok", False),
    ], "tests pass"))
    assert "⚠️ cannot tell" in claims and "the project changed after it: 1 source file edited (app.py)" in claims


def test_docs_screenshots_and_edits_outside_the_project_do_not_make_a_passing_run_stale(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "python -m pytest -q"}, "3 passed", False),
        ("Write", {"file_path": "/home/me/.claude/memory/notes.py", "content": "x"}, "ok", False),
        ("Edit", {"file_path": "/work/demo/CLAUDE.md", "old_string": "a", "new_string": "b"}, "ok", False),
        ("Write", {"file_path": "/work/demo/shots/home.png", "content": "x"}, "ok", False),
        ("Bash", {"command": "sed -i 's/a/b/' /work/demo/x.py"}, "", False),
    ], "tests pass"))
    assert "✅ backed" in claims


@pytest.mark.parametrize("output", [
    "Time: 01:42\n\nOK (2764 tests, 50431 assertions)",
    "OK, but some tests were skipped!\nTests: 2764, Assertions: 50431, Skipped: 19.",
    "test result: ok. 12 passed; 0 failed", "5 passing (2s)", "ok  \tgithub.com/x/pkg\t0.5s", "12 examples, 0 failures",
])
def test_pass_summaries_of_other_runners_back_a_piped_run(tmp_path, output):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "php artisan test 2>&1 | tail -4"}, output, False)], "全套測試通過"))
    assert "✅ backed" in claims and "🟡 probably passed" in claims, claims


def test_an_echoed_exit_status_right_after_the_test_is_the_tests(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "php artisan test > /tmp/full.log 2>&1; echo exit=$?; tail -3 /tmp/full.log"},
         "exit=0\nOK (5 tests, 9 assertions)", False)], "tests pass"))
    assert "✅ backed" in claims and "`echo $?` right after the test printed exit=0" in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "pytest -q; echo \"exit=$?\""}, "1 failed, 2 passed\nexit=1", False)], "tests pass"))
    assert "❌ contradicted" in claims and "printed exit=1" in claims


def test_the_last_test_run_decides(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "python -m pytest tests/test_a.py"}, "Exit code 1\n1 failed", True),
        ("Bash", {"command": "python -m pytest"}, "9 passed", False),
    ], "all tests pass"))
    assert "✅ backed" in claims and "at 10:02" in claims


def test_push_claim_against_a_failed_or_hidden_push(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "git push"}, "Exit code 1\n! [rejected] main -> main (fetch first)", True)], "已 push"))
    assert "pushed" in claims and "❌ contradicted" in claims and "exit 1" in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "git push; echo done"}, "done", False)], "已 push"))
    assert "⚠️ cannot tell" in claims and "`;` after it hides its exit status" in claims


def test_lint_and_build_evidence(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "npx tsc --noEmit"}, "", False),
        ("Bash", {"command": "npm run build"}, "Exit code 1\nerror TS2322", True),
    ], "tsc 通過，build 成功"))
    assert "lint passes" in claims and "✅ backed" in claims
    assert "build succeeds" in claims and "❌ contradicted" in claims


def test_merge_evidence_accepts_gh_pr_merge(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "gh pr merge 12 --squash"}, "✓ Merged", False)], "PR #12 已 merge"))
    assert "merged" in claims and "✅ backed" in claims


def test_no_final_message_and_no_claims(tmp_path):
    claims = _claims(_session(tmp_path, [("Bash", {"command": "ls"}, "a", False)], None))
    assert "No final message to check" in claims
    claims = _claims(_session(tmp_path, [("Bash", {"command": "ls"}, "a", False)], "Listed the files."))
    assert "No checkable claim" in claims


def test_one_row_per_kind(tmp_path):
    claims = _claims(_session(tmp_path, [("Bash", {"command": "pytest"}, "3 passed", False)],
                              "tests pass.\nAll 3 tests pass again."))
    assert claims.count("\n| tests pass: ") == 1


def test_api_error_text_is_not_the_final_message(tmp_path):
    path = _session(tmp_path, [("Bash", {"command": "pytest"}, "Exit code 1\n1 failed", True)], "測試全過")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "assistant", "timestamp": "2026-11-06T10:59:30Z", "isApiErrorMessage": True,
                             "message": {"content": [{"type": "text", "text": "API Error: overloaded"}]}}) + "\n")
    parsed = parse_transcript(path)
    assert parsed["last_assistant_message"] == "測試全過"
    assert "❌ contradicted" in _claims(path)


def test_final_message_resets_at_each_user_event(tmp_path):
    path = _session(tmp_path, [("Bash", {"command": "pytest"}, "3 passed", False)], "tests pass")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "timestamp": "2026-11-06T11:00:00Z", "origin": {"kind": "human"},
                             "message": {"role": "user", "content": "now push"}}) + "\n")
    assert parse_transcript(path)["last_assistant_message"] == ""
    assert "No final message to check" in _claims(path)


# --- the CLI takes the Stop hook's copy of the message ---------------------------------------------


def test_last_message_from_stdin_overrides_the_transcript(tmp_path):
    path = _session(tmp_path, [("Bash", {"command": "pytest"}, "Exit code 1\n1 failed", True)], "still working on it")
    runner = CliRunner()
    args = ["log", str(path), "-o", str(tmp_path / "out"), "-d", "d", "--brief", "--last-message", "-"]
    result = runner.invoke(cli, args, input="全套測試綠，已 push")
    assert result.exit_code == 0, result.output
    line = result.stdout_bytes.decode("utf-8")
    assert "claims: 1 contradicted ❌ (tests pass), 1 without evidence ❓ (pushed)" in line
    receipt = (tmp_path / "out" / "2026-11-06-d.receipt.md").read_text(encoding="utf-8")
    assert "全套測試綠，已 push" in receipt


def test_last_message_from_a_file_and_an_empty_one(tmp_path):
    path = _session(tmp_path, [("Bash", {"command": "pytest"}, "3 passed", False)], "tests pass")
    (tmp_path / "m.txt").write_text("", encoding="utf-8")
    result = CliRunner().invoke(cli, ["log", str(path), "-o", str(tmp_path / "out"), "-d", "d", "--brief",
                                      "--last-message", str(tmp_path / "m.txt")])
    assert result.exit_code == 0, result.output
    assert "claims: no final message" in result.stdout_bytes.decode("utf-8")


# --- found by the independent verifier of PR #13 ----------------------------------------------------


def _tests_row(tmp_path, command, output):
    path = _session(tmp_path, [("Bash", {"command": command}, output, False)], None)
    receipt = render_receipt(parse_transcript(path), source="s")
    return receipt.split("## Tests\n", 1)[1].split("\n## ", 1)[0].splitlines()[2]


def test_an_earlier_echoed_status_in_the_same_call_is_not_the_tests(tmp_path):
    command = 'ruff check .; echo "exit=$?"; python -m pytest -q | tail -3; echo "exit=${PIPESTATUS[0]}"'
    row = _tests_row(tmp_path, command, "E501 line too long\nexit=1\n3 passed\nexit=0")
    assert "⚠️" in row and "❌" not in row


def test_an_echo_between_the_test_and_the_status_echo_breaks_the_link(tmp_path):
    row = _tests_row(tmp_path, 'python -m pytest -q; echo "--- lint ---"; ruff check .; echo "exit=$?"',
                     "3 passed\n--- lint ---\nE501\nexit=1")
    assert "⚠️" in row and "❌" not in row
    row = _tests_row(tmp_path, 'python -m pytest -q; echo "done"; echo "exit=$?"',
                     "no tests ran in 0.01s\ndone\nexit=0")
    assert "✅" not in row


def test_a_dry_run_push_and_an_auto_merge_are_not_evidence(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "git push --dry-run origin main"}, "To github.com\n * [new branch] main -> main", False),
        ("Bash", {"command": "git push -n origin main"}, "Everything up-to-date", False),
    ], "已 push"))
    assert "❓ no evidence" in claims and "✅" not in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "gh pr merge 13 --auto --squash"}, "✓ Pull request #13 will be automatically merged",
         False),
    ], "PR #13 已 merge"))
    assert "❓ no evidence" in claims and "✅" not in claims


def test_a_windows_path_outside_the_project_is_not_a_project_edit(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "python -m pytest -q"}, "3 passed", False),
        ("Edit", {"file_path": r"C:\other\x.py", "old_string": "a", "new_string": "b"}, "ok", False),
    ], "tests pass", cwd=r"C:\work\demo"))
    assert "✅ backed" in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "python -m pytest -q"}, "3 passed", False),
        ("Edit", {"file_path": "/etc/hosts.py", "old_string": "a", "new_string": "b"}, "ok", False),
    ], "tests pass", cwd=""))
    assert "⚠️ cannot tell" in claims and "files changed after it" in claims and "the project" not in claims


def test_an_empty_hook_message_is_not_blamed_on_the_transcript(tmp_path):
    parsed = parse_transcript(_session(tmp_path, [("Bash", {"command": "ls"}, "a", False)], "done"))
    parsed["last_assistant_message"] = ""
    section = render_receipt(parsed, source="s").split("## Claims\n", 1)[1].split("\n## ", 1)[0]
    assert "No final message to check" in section and "transcript ends" not in section


def test_english_negation_only_counts_before_the_claim():
    assert _kinds("All tests pass, no regressions.") == ["tests"]
    assert _kinds("Nothing is committed or pushed yet.") == []
    assert _kinds("The branch has not been pushed.") == []


def test_markdown_list_labels_are_quoted_cleanly():
    assert [c.quote for c in find_claims("- **Tests**: all tests pass")] == ["Tests: all tests pass"]


# --- after Codex's review of 707ac287 ---------------------------------------------------------------


def test_negation_inside_the_claim_is_a_negation():
    assert _kinds("Tests did not pass.") == []
    assert _kinds("The tests don't pass yet.") == []
    assert _kinds("ruff did not pass, 3 errors remain") == []
    assert _kinds("tests no longer pass") == []
    assert _kinds("Tests pass now.") == ["tests"]


def test_a_question_mark_ends_a_question_but_a_query_string_does_not():
    assert _kinds("Did the tests pass?") == []
    assert _kinds("Tests pass? Not sure.") == []
    assert _kinds("Tests pass? (checked twice)") == []
    assert _kinds("全套 3,330 綠、prod 上 contact-page.js 已是新版（?v= 已換）、/contact 200") == ["tests"]


def test_after_or_before_ahead_of_the_claim_is_a_plan_but_behind_it_is_the_story():
    assert _kinds("All tests passed after fixing the bug.") == ["tests"]
    assert _kinds("I pushed after rebasing on main.") == ["push"]
    assert _kinds("After the tests pass, push it.") == []
    assert _kinds("Before pushing, make sure the tests pass.") == []


def test_a_test_failure_later_in_the_chain_is_not_the_lints(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "ruff check . && npm test"},
         "Exit code 1\nAll checks passed!\nFAIL src/app.test.ts\nTests: 1 failed", True)], "Lint passes."))
    assert "⚠️ cannot tell" in claims and "a later step in the chain failed" in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "ruff check . && npm test"}, "Exit code 1\nFound 2 errors.", True)], "Lint passes."))
    assert "❌ contradicted" in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "npm run lint"}, "Exit code 1\nFAIL src/app.ts", True)], "Lint passes."))
    assert "❌ contradicted" in claims


def test_the_last_push_in_a_call_is_the_one_judged(tmp_path):
    out = "! [rejected] main -> main (fetch first)\nSuccessfully rebased\n   abc1234..def5678  main -> main"
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "git push origin main; git pull --rebase; git push origin main"}, out, False)],
        "Pushed to origin/main."))
    assert "✅ backed" in claims and "exit 0" in claims and "rebase" not in claims.split("→")[-1]


def test_aborting_a_merge_is_not_a_merge(tmp_path):
    claims = _claims(_session(tmp_path, [("Bash", {"command": "git merge --abort"}, "", False)], "PR #13 is merged."))
    assert "❓ no evidence" in claims


def test_a_version_query_is_neither_a_lint_nor_a_test_run(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "ruff check ."}, "Exit code 1\nFound 1 error.", True),
        ("Bash", {"command": "ruff --version"}, "ruff 0.8.0", False),
    ], "Lint passes."))
    assert "❌ contradicted" in claims and "--version" not in claims
    path = _session(tmp_path, [("Bash", {"command": "php vendor/bin/phpunit --version"}, "PHPUnit 11.0", False)], "ok")
    assert "tests: none ran" in render_brief(parse_transcript(path), "r.md")


def test_ruff_fix_with_nothing_remaining_is_not_a_failure(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "ruff check --fix .; echo done"}, "Found 1 error (1 fixed, 0 remaining).\ndone", False)],
        "Lint passes."))
    assert "✅ backed" in claims and "0 remaining" in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "ruff check --fix .; echo done"}, "Found 3 errors (1 fixed, 2 remaining).\ndone", False)],
        "Lint passes."))
    assert "❌ contradicted" in claims


def test_a_shell_script_under_the_project_is_source_too(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "bats test.bats"}, "1..1\nok 1 script works", False),
        ("Edit", {"file_path": "/work/demo/bin/run.sh", "old_string": "a", "new_string": "b"}, "ok", False),
    ], "Tests pass."))
    assert "⚠️ cannot tell" in claims and "run.sh" in claims


def test_a_question_mark_glued_to_the_next_sentence_still_ends_the_question():
    assert _kinds("測試過了？沒有。") == []
    assert _kinds("Did the tests pass?No.") == []
    assert _kinds("Tests pass?Not yet, 2 failed.") == []
    assert _kinds("測試全過？還沒跑。") == []


# --- after Codex's second round (head 1b4af067) ------------------------------------------------------


def test_cannot_inside_the_claim_is_a_negation():
    assert _kinds("Tests cannot pass.") == []
    assert _kinds("ruff cannot pass with these errors") == []


def test_plural_ruff_fix_summary_is_clean_too(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "ruff check --fix .; echo done"}, "Found 2 errors (2 fixed, 0 remaining).\ndone", False)],
        "Lint passes."))
    assert "✅ backed" in claims


def test_help_after_a_subcommand_and_a_version_query_before_the_real_run(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "ruff check --help"}, "Usage: ruff check [OPTIONS] [FILES]...", False)], "Lint passes."))
    assert "❓ no evidence" in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "ruff check ."}, "All checks passed!", False),
        ("Bash", {"command": "ruff --version && ruff check ."}, "Exit code 1\nruff 0.8.0\nFound 1 error.", True),
    ], "Lint passes."))
    assert "❌ contradicted" in claims
    path = _session(tmp_path, [
        ("Bash", {"command": "pytest -q --help"}, "usage: pytest", False),
        ("Bash", {"command": "pytest --version && pytest -q"}, "pytest 8.0\n3 passed", False)], "tests pass")
    assert "tests: 1 run: 1 passed" in render_brief(parse_transcript(path), "r.md")


def test_ts_diagnostics_and_pass_lines_of_a_test_runner_in_the_chain_are_not_the_lints(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "npm run lint && npm test"},
         "Exit code 1\nAll checks passed!\nFAIL src/app.test.ts\n  src/app.test.ts:3:7 - error TS2322: Type 'string'",
         True)], "Lint passes."))
    assert "⚠️ cannot tell" in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "npx tsc --noEmit"}, "Exit code 1\nsrc/app.ts:3:7 - error TS2322: Type 'string'", True)],
        "Lint passes."))
    assert "❌ contradicted" in claims and "error TS2322" in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "npx tsc --noEmit; npx jest"}, "PASS src/app.test.ts\nTests: 1 passed", False)],
        "Lint passes."))
    assert "⚠️ cannot tell" in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "vendor/bin/pint --test; echo done"}, "  PASS   .......... 12 files\ndone", False)],
        "Lint passes."))
    assert "✅ backed" in claims


def test_the_last_marker_in_the_output_decides_a_hidden_status(tmp_path):
    out = "! [rejected] main -> main (fetch first)\nSuccessfully rebased\n   abc1234..def5678  main -> main\ndone"
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "git push origin main; git pull --rebase; git push origin main; echo done"}, out, False)],
        "Pushed to origin/main."))
    assert "✅ backed" in claims and "🟡 probably pushed" in claims
    out = "   abc1234..def5678  main -> main\n ! [rejected] dev -> dev (non-fast-forward)"
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "git push origin main dev; echo done"}, out, False)], "已 push"))
    assert "❌ contradicted" in claims


# --- after Codex's third round and the verifier's fourth (head ff6f7b31) ----------------------------


def test_a_failing_eslint_summary_is_not_zero_problems(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "npx eslint .; echo done"}, "✖ 10 problems (10 errors, 0 warnings)\ndone", False)],
        "Lint passes."))
    assert "❌ contradicted" in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "npx eslint .; echo done"}, "✔ 0 problems\ndone", False)], "Lint passes."))
    assert "✅ backed" in claims


def test_the_last_markers_are_compared_not_the_first(tmp_path):
    out = ("! [rejected] main -> main (fetch first)\n   abc1234..def5678  main -> main\n"
           "fatal: Could not resolve host: mirror\ndone")
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "git push origin main; git push backup main; git push mirror main; echo done"}, out,
         False)], "Pushed to mirror/main."))
    assert "❌ contradicted" in claims and "fatal:" in claims


def test_a_version_query_echoed_before_the_test_is_not_the_tests_status(tmp_path):
    out = "pytest 8.0\nexit=0\nERROR: file or directory not found: tests/test_api.py\n\nno tests ran in 0.00s\nexit=4"
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "pytest --version; echo exit=$?; pytest tests/test_api.py; echo exit=$?"}, out, False)],
        "Tests pass."))
    assert "⚠️ cannot tell" in claims


def test_a_lowercase_answer_glued_to_a_question_mark_is_still_an_answer():
    assert _kinds("Did the tests pass?no, still broken.") == []
    assert _kinds("Tests pass?yes") == []
    assert _kinds("已是新版（?v= 已換）、全套 3,330 綠") == ["tests"]
    assert _kinds("see /docs?page=2&lang=en, all 12 tests pass") == ["tests"]


def test_help_inside_a_group_is_not_a_run_but_ctest_verbose_is(tmp_path):
    path = _session(tmp_path, [("Bash", {"command": "(pytest --help)"}, "usage: pytest [options]", False)], "ok")
    assert "tests: none ran" in render_brief(parse_transcript(path), "r.md")
    path = _session(tmp_path, [("Bash", {"command": "ctest -V"}, "100% tests passed, 0 tests failed out of 3", False)],
                    "ok")
    assert "tests: 1 run" in render_brief(parse_transcript(path), "r.md")


def test_the_evidence_names_the_real_run_not_the_version_query(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "ruff --version && ruff check ."}, "Exit code 1\nruff 0.8.0\nFound 1 error.", True)],
        "Lint passes."))
    assert "last `ruff check .`" in claims


# --- after Codex's fourth round (head 1a1b87fe) -----------------------------------------------------


def test_dash_v_is_a_version_flag_except_for_ctest(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "ruff check ."}, "Exit code 1\nFound 1 error.", True),
        ("Bash", {"command": "ruff -V"}, "ruff 0.8.0", False),
    ], "Lint passes."))
    assert "❌ contradicted" in claims
    path = _session(tmp_path, [("Bash", {"command": "ctest -V"}, "100% tests passed, 0 tests failed out of 3", False)],
                    "ok")
    assert "tests: 1 run" in render_brief(parse_transcript(path), "r.md")


def test_a_runner_name_in_a_quoted_string_does_not_shift_the_echoed_status(tmp_path):
    out = ("pytest --version\npytest 8.0\nexit=0\nERROR: file or directory not found: tests/test_api.py\n"
           "no tests ran in 0.00s\nexit=4")
    command = 'echo "pytest --version"; pytest --version; echo exit=$?; pytest tests/test_api.py; echo exit=$?'
    claims = _claims(_session(tmp_path, [("Bash", {"command": command}, out, False)], "Tests pass."))
    assert "⚠️ cannot tell" in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": 'echo "Running pytest..."; pytest -q; echo exit=$?'},
         "Running pytest...\n3 passed\nexit=0", False)], "Tests pass."))
    assert "✅ backed" in claims


# --- after Codex's fifth round (head 8973971e) ------------------------------------------------------


def test_maven_dash_v_prints_the_version_and_runs_on(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "mvn test"}, "[INFO] BUILD SUCCESS\nTests run: 3, Failures: 0", False),
        ("Bash", {"command": "mvn test -V"}, "Exit code 1\n[INFO] BUILD FAILURE", True),
    ], "Tests pass."))
    assert "❌ contradicted" in claims


def test_two_linters_in_one_call_cannot_lend_each_other_their_markers(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "ruff check .; npx tsc --noEmit; echo done"},
         "All checks passed!\nsrc/app.ts(3,7): error TS2322: Type 'string' is not assignable to type 'number'.\ndone",
         False)], "Typecheck passes."))
    assert "⚠️ cannot tell" in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "ruff check . && npx tsc --noEmit"}, "All checks passed!", False)], "Lint passes."))
    assert "✅ backed" in claims


def test_eslint_warnings_do_not_fail_lint(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "npx eslint .; echo done"}, "✖ 1 problem (0 errors, 1 warning)\ndone", False)],
        "Lint passes."))
    assert "✅ backed" in claims and "0 errors" in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "npx eslint .; echo done"}, "✖ 2 problems (1 error, 1 warning)\ndone", False)],
        "Lint passes."))
    assert "❌ contradicted" in claims


def test_a_past_tense_claim_behind_an_after_clause_is_a_claim():
    assert _kinds("After fixing the bug, all tests passed.") == ["tests"]
    assert _kinds("After the rebase, I pushed to main.") == ["push"]
    assert _kinds("After the tests pass, push it.") == []


def test_the_same_linter_twice_in_one_call_still_reads_its_own_markers(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "ruff check --fix .; ruff check src/; echo done"},
         "Found 2 errors (2 fixed, 0 remaining).\nFound 1 error.\ndone", False)], "Lint passes."))
    assert "❌ contradicted" in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "ruff check src/; ruff check --fix .; echo done"},
         "Found 1 error.\nFound 1 error (1 fixed, 0 remaining).\ndone", False)], "Lint passes."))
    assert "✅ backed" in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "npm run lint; npm run typecheck; echo done"},
         "✔ 0 problems\nsrc/app.ts(3,7): error TS2322: Type 'string'\ndone", False)], "Typecheck passes."))
    assert "⚠️ cannot tell" in claims


# --- after Codex's sixth round (head bdee22f9) ------------------------------------------------------


def test_eslints_warning_limit_is_a_failure(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "npx eslint . --max-warnings 0; echo done"},
         "✖ 1 problem (0 errors, 1 warning)\nESLint found too many warnings (maximum: 0).\ndone", False)],
        "Lint passes."))
    assert "❌ contradicted" in claims and "too many warnings" in claims


def test_a_rust_build_error_is_not_clippys(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "cargo clippy && cargo build --release"},
         "Exit code 1\nFinished `dev` profile [unoptimized + debuginfo] target(s)\nCompiling demo v0.1.0\n"
         "error[E0425]: cannot find value `DEBUG_ONLY` in this scope", True)], "Clippy passes."))
    assert "⚠️ cannot tell" in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "cargo clippy"}, "Exit code 1\nerror[E0425]: cannot find value `X`", True)],
        "Clippy passes."))
    assert "❌ contradicted" in claims and "error[E0425]" in claims


def test_a_present_passive_behind_after_is_still_a_plan():
    assert _kinds("After the changes are committed, push to main.") == []
    assert _kinds("After the PR is merged, delete the branch.") == []
    assert _kinds("The changes are committed.") == ["commit"]
    assert _kinds("After the tests passed, I pushed to main.") == ["tests", "push"]
