"""A "tests pass" sentence that gives a count the last run did not print is not judged by that run: it is ⚠️.

The runs that did print the count are named, never used: an older pass cannot back the claim (the last run may be
the same tests with a failure in a format read short) and an older failure cannot contradict it (a pre-push hook
may have run the suite again). So the count only ever moves a verdict to ⚠️.
"""

from tests.test_claims import _claims, _session

MAP_TESTS = ("Bash", {"command": "php artisan test tests/Feature/MapTest.php"},
             "  Tests:    18 passed (49 assertions)", False)
FULL_SUITE_FAILED = ("Bash", {"command": "php artisan test"},
                     "Exit code 1\nFAILURES!\nTests: 3477, Assertions: 73477, Failures: 1, Skipped: 19.", True)
SUBSET_PASSED = ("Bash", {"command": "php artisan test tests/Feature/RouteTest.php"},
                 "  Tests:    31 passed (98 assertions)", False)


def test_an_older_run_that_printed_the_count_is_named_but_does_not_back_it(tmp_path):
    claims = _claims(_session(tmp_path, [MAP_TESTS, SUBSET_PASSED], "The 2 map test files: 18 tests all pass."))
    assert "⚠️ cannot tell" in claims and "✅ backed" not in claims
    assert "the last run that printed 18 was `php artisan test tests/Feature/MapTest.php`" in claims
    assert "printed other counts" in claims


def test_a_later_unrelated_failure_makes_it_unclear_not_contradicted(tmp_path):
    claims = _claims(_session(tmp_path, [MAP_TESTS, FULL_SUITE_FAILED], "The 2 map test files: 18 tests all pass."))
    assert "⚠️ cannot tell" in claims and "the last run that printed 18" in claims and "❌ contradicted" not in claims


def test_no_run_printed_the_count(tmp_path):
    """A JS count is not backed or contradicted by a PHP run that printed other numbers."""
    claims = _claims(_session(tmp_path, [FULL_SUITE_FAILED], "JS 測試 13/13 全過"))
    assert "⚠️ cannot tell" in claims and "no test run printed 13" in claims and "❌ contradicted" not in claims


def test_a_count_of_checks_is_not_backed_by_an_unrelated_passing_run(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "php artisan test tests/Unit"}, "  Tests:    52 passed (180 assertions)", False)],
        "程式閘門 48 題全過"))
    assert "⚠️ cannot tell" in claims and "no test run printed 48" in claims


def test_an_earlier_failing_run_with_the_count_does_not_contradict(tmp_path):
    """The passing run may have come from a hook (pre-push) rather than a test command: an older failure is no proof."""
    claims = _claims(_session(tmp_path, [FULL_SUITE_FAILED, SUBSET_PASSED],
                              "Second full run: 3477 tests passed, pushed."))
    assert "⚠️ cannot tell" in claims and "❌ contradicted" not in claims and "the last run that printed 3477" in claims


def test_the_last_run_printing_the_count_is_judged_as_before(tmp_path):
    claims = _claims(_session(tmp_path, [SUBSET_PASSED, FULL_SUITE_FAILED], "全套 3477 條全過"))
    assert "❌ contradicted" in claims


def test_a_sentence_without_a_count_still_uses_the_last_run(tmp_path):
    claims = _claims(_session(tmp_path, [MAP_TESTS, FULL_SUITE_FAILED], "The tests pass."))
    assert "❌ contradicted" in claims


def test_numbers_that_are_not_counts_do_not_count(tmp_path):
    """Dates, times, PR numbers and versions are not test counts."""
    claims = _claims(_session(tmp_path, [SUBSET_PASSED], "osop#19 and v2.3 tests pass at 05:22 tests time."))
    assert "✅ backed" in claims


def test_a_count_split_into_failed_and_passed_is_the_same_run(tmp_path):
    """pytest prints "1 failed, 4 passed", never the 5 the sentence gives; the run still contradicts it."""
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "python -m pytest -q"}, "Exit code 1\n1 failed, 4 passed in 0.31s", True)],
        "All 5 tests pass."))
    assert "❌ contradicted" in claims


def test_a_run_that_printed_no_count_is_judged_as_before(tmp_path):
    claims = _claims(_session(tmp_path, [("Bash", {"command": "python -m pytest -q"}, "Exit code 1\n", True)],
                              "All 5 tests pass."))
    assert "❌ contradicted" in claims


def test_a_skipped_count_is_not_a_pass_count(tmp_path):
    """Every full PHPUnit run prints "Skipped: 19"; "19 條跳過" must not pick an older failing one."""
    claims = _claims(_session(tmp_path, [FULL_SUITE_FAILED, SUBSET_PASSED], "全套 3,485 條通過、19 條跳過"))
    assert "no test run printed 3485" in claims


def test_only_numbers_in_a_clause_that_says_pass_are_counts(tmp_path):
    claims = _claims(_session(tmp_path, [SUBSET_PASSED], "內容池從 358 條擴到 2,552 條，全套測試綠"))
    assert "✅ backed" in claims


def test_a_thousands_separator_is_part_of_the_count(tmp_path):
    claims = _claims(_session(tmp_path, [FULL_SUITE_FAILED, SUBSET_PASSED], "全套 3,477 條全過"))
    assert "the last run that printed 3477" in claims


def test_skipped_ran_or_looked_at_is_not_passed(tmp_path):
    """「跳過」「略過」「跑過」「看過」hold 過 but say skipped, ran or looked at, not passed."""
    claims = _claims(_session(tmp_path, [FULL_SUITE_FAILED, SUBSET_PASSED],
                              "整套測試：3,485 個通過，另有 19 個是本來就設定跳過的"))
    assert "no test run printed 3485" in claims


def test_an_assertion_count_is_not_a_test_count(tmp_path):
    """PHPUnit prints "OK (18 tests, 49 assertions)": 49 is not a number of tests."""
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "php vendor/bin/phpunit"}, "OK (18 tests, 49 assertions)", False),
        SUBSET_PASSED], "All 49 tests passed."))
    assert "no test run printed 49" in claims
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "php vendor/bin/phpunit"}, "Tests: 3477, Assertions: 73480, Skipped: 19.", False),
        SUBSET_PASSED], "73480 tests passed."))
    assert "no test run printed 73480" in claims


def test_skipped_tests_are_not_summed_into_a_pass_count(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "python -m pytest -q"}, "4 passed, 1 skipped in 0.31s", False), SUBSET_PASSED],
        "All 5 tests passed."))
    assert "no test run printed 5" in claims


def test_a_progress_percentage_or_a_file_name_is_not_a_count(tmp_path):
    """pytest prints "tests/test_api.py .... [ 18%]" on every run; 18% is no count of 18 tests, nor is test_v2."""
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "python -m pytest -q tests/test_map.py"}, "18 passed in 0.20s", False),
        ("Bash", {"command": "python -m pytest -q"},
         "Exit code 1\ntests/test_api.py .... [ 18%]\ntests/test_v18.py ....F [100%]\n"
         "1 failed, 49 passed in 0.31s", True)],
        "All 18 tests passed."))
    assert "the last run that printed 18" in claims and "❌ contradicted" not in claims


def test_a_fraction_that_is_skipped_or_failed_is_not_a_pass_count(tmp_path):
    claims = _claims(_session(tmp_path, [SUBSET_PASSED, MAP_TESTS], "選用測試 31/31 跳過，其他測試全過"))
    assert "✅ backed" in claims and "printed 31" not in claims


def test_skipped_counts_and_suite_counts_in_runner_output_are_not_test_counts(tmp_path):
    """Only what a runner labels as tests counts: not "18 skipped", not Jest's "Test Suites: 18 total"."""
    earlier = ("Bash", {"command": "python -m pytest -q tests/test_map.py"}, "18 passed in 0.20s", False)
    for output in ("Exit code 1\n1 failed, 49 passed, 18 skipped in 0.31s",
                   "Exit code 1\nTest Suites: 18 failed, 18 total\nTests:       1 failed, 49 passed, 50 total"):
        claims = _claims(_session(tmp_path, [earlier, ("Bash", {"command": "npx jest"}, output, True)],
                                  "All 18 tests passed."))
        assert "the last run that printed 18" in claims and "❌ contradicted" not in claims, output


def test_zero_failures_says_the_count_passed(tmp_path):
    """「3,477 個 0 失敗」 and "3477 tests, 0 failures" give the count as passing."""
    for sentence in ("全套 3,477 個 0 失敗", "Full suite: 3477 tests, 0 failures"):
        claims = _claims(_session(tmp_path, [FULL_SUITE_FAILED, SUBSET_PASSED], sentence))
        assert "the last run that printed 3477" in claims, sentence


def test_a_runner_format_read_short_cannot_back_a_claim_the_last_run_failed(tmp_path):
    """Mocha prints "4 passing" and "1 failing" on two lines; read short, the last run looks like other tests."""
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "npx mocha"}, "  5 passing (10ms)", False),
        ("Bash", {"command": "npx mocha"}, "Exit code 1\n  4 passing (10ms)\n  1 failing", True)],
        "All 5 tests passed."))
    assert "✅ backed" not in claims


def test_a_count_written_against_its_word_is_a_count(tmp_path):
    claims = _claims(_session(tmp_path, [SUBSET_PASSED], "測試 18個全過"))
    assert "no test run printed 18" in claims


def test_tests_followed_by_a_skipped_status_is_not_a_count(tmp_path):
    """Pest prints "Tests:    19 skipped, 3466 passed": the 19 after "Tests:" is the skipped ones."""
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "php artisan test"}, "  Tests:    19 skipped, 3466 passed (73477 assertions)", False),
        SUBSET_PASSED], "新增的 19 個測試全過"))
    assert "no test run printed 19" in claims


def test_jest_snapshot_counts_are_not_tests(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "npx jest"}, "Snapshots:   6 passed, 6 total\nTests:       40 passed, 40 total", False),
        SUBSET_PASSED], "6 個新測試全過"))
    assert "no test run printed 6" in claims


def test_each_count_the_last_run_did_not_print_is_named_with_its_run(tmp_path):
    backend_failed = ("Bash", {"command": "python -m pytest -q backend"}, "Exit code 1\n1 failed, 22 passed", True)
    frontend = ("Bash", {"command": "npx vitest run"}, "      Tests  38 passed (38)", False)
    claims = _claims(_session(tmp_path, [backend_failed, frontend, SUBSET_PASSED], "前端 38 個全過、後端 23 個全過"))
    assert "⚠️ cannot tell" in claims and "printed 23 was" in claims and "printed 38 was" in claims
    claims = _claims(_session(tmp_path, [frontend, SUBSET_PASSED], "前端 38 個全過、後端 23 個全過"))
    assert "⚠️ cannot tell" in claims and "no test run printed 23" in claims and "printed 38 was" in claims


def test_a_count_right_after_the_clause_that_says_pass_is_a_count(tmp_path):
    """「整套測試通過，4120 個」: a passing JS run that printed other counts does not back the PHP suite's 4120."""
    js = ("Bash", {"command": "node tests/js/run.mjs"}, "34 passed, 0 failed", False)
    for sentence in ("推送前整套測試通過，4120 個、12 個 skipped", "All tests passed, 4120 tests."):
        claims = _claims(_session(tmp_path, [FULL_SUITE_FAILED, js], sentence))
        assert "⚠️ cannot tell" in claims and "printed 4120" in claims and "✅ backed" not in claims, sentence
    # Only a clause that is the count alone ("2 個檔案改了" is not a number of tests), and only right after the pass.
    for sentence in ("測試全過，2 個檔案改了", "圖檔轉完，358 個，測試全過"):
        assert "✅ backed" in _claims(_session(tmp_path, [js], sentence)), sentence


def test_a_helper_script_in_tests_is_not_a_test_run(tmp_path):
    """A fixture server or a seed script lives in tests/ too: its exit 0 does not back "tests pass" after a failure."""
    failed = ("Bash", {"command": "python -m pytest -q"}, "Exit code 1\n1 failed, 4 passed in 0.31s", True)
    for helper in [("Bash", {"command": "node tests/fixtures/server.mjs --once"}, "listening on 3000", False),
                   ("Bash", {"command": "node test/helpers/seed-db.js"}, "seeded 41 rows", False)]:
        claims = _claims(_session(tmp_path, [failed, helper], "The tests pass."))
        assert "❌ contradicted" in claims and "pytest" in claims, helper[1]
    # A server that timed out is no failing test either.
    passed = ("Bash", {"command": "python -m pytest -q"}, "5 passed in 0.31s", False)
    timed_out = ("Bash", {"command": "timeout 5 node tests/e2e/server.mjs"}, "Exit code 124\nlistening on 3000", True)
    assert "✅ backed" in _claims(_session(tmp_path, [passed, timed_out], "The tests pass."))


def test_a_script_known_only_by_its_folder_needs_a_test_result(tmp_path):
    """A helper the names miss (`mockServer.js`, `shot.js`) makes the last run unclear, never backed or contradicted."""
    failed = ("Bash", {"command": "python -m pytest -q"}, "Exit code 1\n1 failed, 4 passed in 0.31s", True)
    passed = ("Bash", {"command": "python -m pytest -q"}, "5 passed in 0.31s", False)
    helper = ("Bash", {"command": "node tests/mockServer.js --once"}, "listening on 3000", False)
    timed_out = ("Bash", {"command": "timeout 5 node test/shot.js"}, "Exit code 124\nsaved shot.png", True)
    for calls in ([failed, helper], [passed, timed_out]):
        claims = _claims(_session(tmp_path, calls, "The tests pass."))
        assert "⚠️ cannot tell" in claims and "printed no test result" in claims, calls[1]
    # A test script there that prints its result backs or contradicts as any runner does.
    for output, is_error, verdict in [("34 passed, 0 failed", False, "✅ backed"),
                                      ("Exit code 1\n33 passed, 1 failed", True, "❌ contradicted")]:
        run = ("Bash", {"command": "node tests/js/cart.mjs"}, output, is_error)
        assert verdict in _claims(_session(tmp_path, [passed, run], "The tests pass.")), output


def test_a_bare_ok_line_is_not_a_pass_summary(tmp_path):
    """go's "ok  pkg" and TAP's "ok 1 - …" are on one line; a lone `ok` from a step before is not a test result."""
    failed = ("Bash", {"command": "python -m pytest -q"}, "Exit code 1\n1 failed, 4 passed in 0.31s", True)
    for command in ("python scripts/prep.py && node tests/js/cart.mjs 2>&1 | grep -A2 '✗' | cut -c1-400",
                    "python scripts/prep.py && npx vitest run 2>&1 | grep -A2 '×'"):
        run = ("Bash", {"command": command}, "ok\n✗ the total includes tax\n  expected 108, got 100", False)
        claims = _claims(_session(tmp_path, [failed, run], "The tests pass."))
        assert "✅ backed" not in claims, command


def test_a_rerun_behind_env_is_the_last_run(tmp_path):
    """A run that failed and passed again with `env -u FORCE_COLOR` is judged by the second one."""
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "php artisan test tests/Feature/PdfTest.php 2>&1 | tail -25"},
         "Exit code 1\n  Tests:    1 failed, 5 passed", True),
        ("Bash", {"command": "env -u FORCE_COLOR php artisan test tests/Feature/PdfTest.php 2>&1 | tail -4"},
         "  Tests:    6 passed (12 assertions)", False)], "The PDF tests pass."))
    assert "✅ backed" in claims and "❌ contradicted" not in claims


def test_a_fraction_needs_a_pass_word_too(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "python -m pytest -q tests/test_review.py"}, "3 passed in 0.1s", False), SUBSET_PASSED],
        "Addressed 3/3 review comments, and all tests pass."))
    assert "✅ backed" in claims and "printed 3" not in claims

