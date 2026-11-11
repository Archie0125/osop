"""A "tests pass" sentence that gives a count is judged by the run that printed that count, not by whatever ran last."""

from tests.test_claims import _claims, _session

MAP_TESTS = ("Bash", {"command": "php artisan test tests/Feature/MapTest.php"},
             "  Tests:    18 passed (49 assertions)", False)
FULL_SUITE_FAILED = ("Bash", {"command": "php artisan test"},
                     "Exit code 1\nFAILURES!\nTests: 3477, Assertions: 73477, Failures: 1, Skipped: 19.", True)
SUBSET_PASSED = ("Bash", {"command": "php artisan test tests/Feature/RouteTest.php"},
                 "  Tests:    31 passed (98 assertions)", False)


def test_a_later_unrelated_failure_does_not_contradict_the_count_the_sentence_gives(tmp_path):
    claims = _claims(_session(tmp_path, [MAP_TESTS, FULL_SUITE_FAILED], "The 2 map test files: 18 tests all pass."))
    assert "✅ backed" in claims and "MapTest" in claims and "the last run that printed 18" in claims


def test_no_run_printed_the_count(tmp_path):
    """A JS count is not backed or contradicted by a PHP run that printed other numbers."""
    claims = _claims(_session(tmp_path, [FULL_SUITE_FAILED], "JS 測試 13/13 全過"))
    assert "⚠️ cannot tell" in claims and "no test run printed 13" in claims and "❌ contradicted" not in claims


def test_a_count_of_checks_is_not_backed_by_an_unrelated_passing_run(tmp_path):
    claims = _claims(_session(tmp_path, [
        ("Bash", {"command": "php artisan test tests/Unit"}, "  Tests:    52 passed (180 assertions)", False)],
        "程式閘門 48 題全過"))
    assert "⚠️ cannot tell" in claims and "no test run printed 48" in claims


def test_an_earlier_run_with_the_count_backs_but_never_contradicts(tmp_path):
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
    assert "✅ backed" in claims and "the last run that printed 18" in claims


def test_a_fraction_that_is_skipped_or_failed_is_not_a_pass_count(tmp_path):
    claims = _claims(_session(tmp_path, [SUBSET_PASSED, MAP_TESTS], "選用測試 31/31 跳過，其他測試全過"))
    assert "✅ backed" in claims and "printed 31" not in claims
