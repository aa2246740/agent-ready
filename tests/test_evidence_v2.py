"""Contract fixtures, not model assessments or authenticated remote captures."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import unittest

from agent_readiness.catalog import criteria_by_id
from agent_readiness.report import (
    ReportError, _code_span, _historical_evidence, _test_evidence, latest_report,
    new_evaluation, render_markdown, store_report, validate_evaluation,
)
from agent_readiness.repository import RepositoryError, inspect_repository, validate_evidence
from agent_readiness.scoring import score_report
try:
    from tests import test_empty_command_output as empty_fixture
    from tests import test_blind_submit_mcp as blind_fixture
except ImportError:
    import test_empty_command_output as empty_fixture
    import test_blind_submit_mcp as blind_fixture


CLI = empty_fixture.CLI


class EvidenceV2Tests(unittest.TestCase):
    setUp = empty_fixture.EmptyCommandOutputTests.setUp

    def record(self, command="python3 -m pytest --collect-only tests/test_arguments.py", output="206 tests collected in 0.02s", **values):
        return {"kind": "command", "command": command, "output": output, "cwd": ".",
                "exitCode": 0, "timeoutSeconds": 60, **values}

    def evaluation(self, apps=None):
        tests = self.root / "tests"
        tests.mkdir(exist_ok=True)
        (tests / "test_arguments.py").write_text("def test_arguments():\n    assert ['x'][0] == 'x'\n")
        (self.root / "src").mkdir(exist_ok=True)
        data = new_evaluation(inspect_repository(self.root))
        data["apps"] = apps or {".": {"description": "Synthetic v2 contract fixture"}}
        for key, row in data["report"].items():
            row.update(numerator=0, rationale="Synthetic contract fixture, not a readiness assessment.")
            data["evidence"][key] = [{"kind": "file", "path": "README.md", "line": 1, "note": "Synthetic fixture identity"}]
        return data

    def accept(self, row, apps=None):
        _test_evidence([row], 1, apps or {".": {}}, self.root)

    def reject(self, row, reason):
        with self.assertRaises(ReportError) as caught:
            self.accept(row)
        self.assertIn(reason, str(caught.exception))
        self.assertIn("evidence[0]", str(caught.exception))

    def test_absolute_cwd_inside_is_normalized_outside_is_preserved_and_history_reads_both(self):
        data = self.evaluation()
        inside = self.record(cwd=str(self.root / "tests"))
        outside = self.record("git log -1 | head -1", "captured metadata", cwd=str(self.base))
        data["evidence"]["lint_config"] = [inside, outside]
        receipt = store_report(data, self.root, self.state)
        saved = latest_report(data["repository"], self.state)
        self.assertEqual(saved["evidence"]["lint_config"][0]["cwd"], "tests")
        self.assertEqual(saved["evidence"]["lint_config"][1]["cwd"], str(self.base))
        self.assertTrue(Path(receipt["report"]).is_file())
        _historical_evidence([self.record(cwd="/unavailable/old/shell"), self.record(cwd=str(self.root))], self.root)

    def test_absolute_outside_cwd_is_allowed_for_other_criteria_without_runner_bans(self):
        for command in ("git log | head -1", "gh pr list 2>&1", "git status; git log -1"):
            with self.subTest(command=command):
                row = self.record(command, "actual captured excerpt", cwd="/old/host/directory")
                validate_evidence(self.root, row)
                self.assertEqual(row["cwd"], "/old/host/directory")

    def test_runner_from_outside_can_cd_absolute_or_relative_into_target(self):
        self.evaluation()
        for destination in (str(self.root), "./target", "target"):
            with self.subTest(destination=destination):
                self.accept(self.record("cd " + shlex.quote(destination) + " && python3 -m pytest --collect-only tests/test_arguments.py", cwd=str(self.base)))
        self.reject(self.record(cwd=str(self.base)), "outside the target")
        self.accept(self.record(cwd=str(self.root)))

    def test_root_selectors_and_app_attribution_work_after_cd_from_host_shell(self):
        self.evaluation()
        apps = {"apps/web": {"description": "Synthetic web"}, "apps/api": {"description": "Synthetic api"}}
        for app in apps:
            (self.root / app / "tests").mkdir(parents=True)
            (self.root / app / "tests/test_arguments.py").write_text("def test_args():\n    assert 1 + 1 == 2\n")
        for command, output in (
            ("go test -run TestExample ./apps/web/...", "ok fixture/apps/web 0.01s"),
            ("npm --prefix apps/web test -- --listTests", str(self.root / "apps/web/tests/page.test.js")),
            ("python3 -m pytest --collect-only apps/web/tests/test_arguments.py", "206 tests collected"),
        ):
            with self.subTest(command=command):
                row = self.record("cd " + shlex.quote(str(self.root)) + " && " + command, output, cwd=str(self.base), application="apps/web")
                self.accept(row, apps)
                row["application"] = "apps/api"
                with self.assertRaises(ReportError):
                    self.accept(row, apps)

    def test_normalized_relative_cd_still_checks_every_in_target_symlink(self):
        self.evaluation()
        (self.base / "outside").mkdir()
        (self.root / "alias").symlink_to(self.root / "tests", target_is_directory=True)
        self.reject(self.record("cd ../target/alias && go test -list .", "TestExample", cwd=str(self.base / "outside")), "symlink")
        self.accept(self.record("cd ../target && python3 -m pytest --collect-only tests/test_arguments.py", cwd=str(self.base / "outside")))

    def test_direct_cwd_below_root_symlink_is_rejected(self):
        self.evaluation()
        (self.root / "alias").symlink_to(self.root / "tests", target_is_directory=True)
        for cwd in ("alias", str(self.root / "alias")):
            with self.subTest(cwd=cwd):
                self.reject(self.record(cwd=cwd), "symlink")

    def test_exact_measured_python_and_cargo_list_commands_are_accepted(self):
        self.evaluation()
        self.accept(self.record("PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -p no:cacheprovider --collect-only tests/test_arguments.py"))
        self.accept(self.record("CARGO_TARGET_DIR=/abs/outside/scratch/cargo-target cargo test --ignore-rust-version -p grep-cli --lib -- --list",
                                "tests::test_quote: test\ntests::test_escape: test\n\n22 tests, 0 benchmarks"))

    def test_general_assignments_before_runner_and_after_cd_preserve_cache_redirection(self):
        self.evaluation()
        assignments = "env PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX=/outside/cache NO_COLOR=1 CUSTOM_FLAG=value "
        for prefix in (assignments, "cd " + shlex.quote(str(self.root)) + " && " + assignments):
            with self.subTest(prefix=prefix):
                self.accept(self.record(prefix + "python3 -m pytest --collect-only tests/test_arguments.py"))
        self.accept(self.record("GOCACHE=/outside/cache GOFLAGS='-mod=vendor -tags=unit' go test -list . ./pkg", "TestAlpha\nok fixture/pkg 0.01s"))
        self.accept(self.record("PYTHONPATH=" + shlex.quote(str(self.root / 'src')) + " python3 -m pytest --collect-only tests/test_arguments.py"))

    def test_discovery_retargeting_environment_is_still_rejected(self):
        self.evaluation()
        for assignment, reason in (("PYTHONPATH=/outside", "PYTHONPATH"), ("PYTHONPATH=src:../outside", "PYTHONPATH"),
                                   ("PYTHONPATH=src/../src", "PYTHONPATH"), ("GOFLAGS=-modfile=/outside/go.mod", "GOFLAGS"),
                                   ("GOFLAGS='-C /outside'", "GOFLAGS"), ("PYTEST_ADDOPTS=../other.py", "PYTEST_ADDOPTS")):
            with self.subTest(assignment=assignment):
                self.reject(self.record(assignment + " python3 -m pytest --collect-only tests/test_arguments.py"), reason)

    def test_nonfinite_and_overflowing_timeouts_are_specific_record_problems(self):
        self.evaluation()
        for timeout in (float("inf"), float("nan"), 10**400):
            with self.subTest(timeout_type=type(timeout).__name__):
                self.reject(self.record(timeoutSeconds=timeout), "timeoutSeconds must be finite and positive")

    def test_native_other_ecosystem_listings_need_at_least_one_test(self):
        self.evaluation()
        samples = [
            ("cargo test -- --list", "module::example: test\n1 test, 0 benchmarks"),
            ("go test -list . ./pkg", "TestExample\nExampleGreeting\nok fixture/pkg 0.01s"),
            ("ctest -N", "Test project fixture\n  Test #1: example\nTotal Tests: 1"),
            ("ctest --show-only=json-v1", '{"tests":[{"name":"example"}]}'),
            ("dotnet test --list-tests", "The following Tests are available:\n    Fixture.Tests.Example"),
            ("phpunit --list-tests", "Available test(s):\n - FixtureTest::testExample"),
            ("swift test --list-tests", "FixtureTests.Example/testExample"),
        ]
        for command, output in samples:
            with self.subTest(command=command):
                self.accept(self.record(command, output))
                self.reject(self.record(command, "0 tests listed"), "zero tests")
                self.reject(self.record(command, "Build succeeded"), "at least one test")

    def test_other_ecosystem_bounded_execution_and_compile_only_negatives(self):
        self.evaluation()
        for command, output in [
            ("cargo test -p grep-cli --lib", "test result: ok. 22 passed; 0 failed"),
            ("cargo test example", "test result: ok. 1 passed; 0 failed"),
            ("ctest -R example", "100% tests passed, 0 tests failed out of 1"),
            ("dotnet test --filter Name=Example", "Passed! - Failed: 0, Passed: 1, Total: 1"),
            ("bundle exec rspec spec/example_spec.rb", "1 example, 0 failures"),
            ("ruby -Itest test/example_test.rb", "1 runs, 2 assertions, 0 failures, 0 errors"),
            ("mix test test/example_test.exs", "1 test, 0 failures"),
            ("phpunit tests/ExampleTest.php", "OK (1 test, 1 assertion)"),
            ("dart test test/example_test.dart", "00:01 +1: All tests passed!"),
            ("deno test tests/example_test.ts", "ok | 1 passed | 0 failed"),
            ("zig test tests/example.zig", "All 1 tests passed."),
            ("swift test --filter Example", "Executed 1 test, with 0 failures"),
        ]:
            with self.subTest(command=command):
                self.accept(self.record(command, output))
        for command in ("go test -c", "cargo test --no-run", "cargo test --no-run=true", "cargo test --help", "cargo --version", "ctest --help", "rspec --dry-run", "make -n test", "git log -1 --format=test"):
            with self.subTest(command=command):
                self.reject(self.record(command, "Build succeeded"), "unit_tests_runnable")
        self.reject(self.record("cargo test", "test result: ok. 200 passed; 0 failed"), "full suite")

    def test_shell_bans_apply_to_unit_tests_only_with_specific_reason(self):
        self.evaluation()
        for suffix, reason in ((" | head -5", "pipe `|`"), (" 2>&1", "2>&1"), (" > out", "`>`"),
                               (" < in", "`<`"), ("; echo x", "`;`"), (" `pwd`", "backtick"), (" $(pwd)", "$(")):
            with self.subTest(suffix=suffix):
                expected = "shell operator" if reason == "backtick" else reason
                self.reject(self.record(command="go test -list . ./pkg" + suffix), expected)
        for command in ("head -1", "tail -1", "tee out", "timeout 20 go test -list ."):
            self.reject(self.record(command), "head/tail/tee/timeout")

    def test_template_and_all_filled_denominators_in_stored_reports(self):
        for name in ("one", "two"):
            (self.root / name).mkdir()
        data = self.evaluation({"one": {"description": "Synthetic app one"}, "two": {"description": "Synthetic app two"}})
        for index, (key, criterion) in enumerate(criteria_by_id().items()):
            self.assertEqual(data["report"][key]["denominator"], None if criterion["scope"] == "application" else 1)
            if index % 2:
                del data["report"][key]["denominator"]
        data["report"]["strict_typing"]["numerator"] = None
        receipt = store_report(data, self.root, self.state)
        saved = json.loads(Path(receipt["report"]).read_text())
        for key, criterion in criteria_by_id().items():
            self.assertIs(type(saved["report"][key]["denominator"]), int)
            self.assertEqual(saved["report"][key]["denominator"], 2 if criterion["scope"] == "application" else 1)

    def test_all_structure_evidence_runner_and_condition_problems_are_collected(self):
        data = self.evaluation()
        data["report"]["lint_config"].update(denominator=2, rationale="")
        data["evidence"]["lint_config"] = [{"kind": "file", "path": "missing", "note": "", "line": 0}]
        data["report"]["unit_tests_runnable"]["numerator"] = 1
        data["evidence"]["unit_tests_runnable"] = [self.record("go test -list . | head -1"), self.record(cwd=str(self.base))]
        data["report"]["dependency_update_automation"]["numerator"] = 1
        with self.assertRaises(ReportError) as caught:
            validate_evaluation(data, self.root)
        problems = caught.exception.problems
        for fragment in ("lint_config.denominator", "lint_config.rationale", "evidence[0].path", "evidence[0].note", "evidence[0].line", "unit_tests_runnable evidence[0]: pipe", "unit_tests_runnable evidence[1]: runner directory", "dependency_configuration", "dependency_pull_request"):
            self.assertTrue(any(fragment in problem for problem in problems), (fragment, problems))
        source = self.base / "broken.json"
        source.write_text(json.dumps(data))
        result = subprocess.run([sys.executable, str(CLI), "report", str(self.root), "--input", str(source), "--state-dir", str(self.state)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr.splitlines()[:-1], ["readiness: " + p for p in problems])
        self.assertEqual(result.stderr.splitlines()[-1], f"{len(problems)} problems; fix all and re-run report")
        self.assertFalse(self.state.exists())

    def test_problem_list_is_bounded_with_exact_total(self):
        data = self.evaluation()
        for row in data["report"].values():
            row["rationale"] = ""
        with self.assertRaises(ReportError) as caught:
            validate_evaluation(data, self.root)
        self.assertEqual(len(caught.exception.problems), 50)
        self.assertEqual(caught.exception.problem_count, 84)
        self.assertEqual(caught.exception.omitted_count, 34)

    def test_markdown_code_spans_preserve_literal_ampersands_angle_brackets_and_backticks(self):
        data = self.evaluation()
        validate_evaluation(data, self.root)
        app = "app&<`name``>"
        data["apps"] = {app: {"description": "description & <text>"}}
        data["evidence"]["lint_config"] = [self.record('tool --flag "a&<b>" `one``two`', "output & <text>", cwd=app)]
        rendered = render_markdown(data, {**data, "apps": {"old": {"description": "old"}}})
        self.assertIn("```" + app + "```", rendered)
        self.assertIn('``` tool --flag "a&<b>" `one``two` ```', rendered)
        self.assertIn("description &amp; &lt;text&gt;", rendered)
        self.assertIn("output &amp; &lt;text&gt;", rendered)
        self.assertEqual(_code_span("`x``"), "``` `x`` ```")
        self.assertEqual(_code_span(" a&<b> "), "`  a&<b>  `")

    def test_score_cli_preserves_native_nonpositive_denominators_and_rejects_nonfinite(self):
        for denominator in (0, -1):
            data = {"lint_config": {"numerator": 0, "denominator": denominator}, "type_check": {"numerator": 1, "denominator": 1}}
            source = self.base / "score.json"
            source.write_text(json.dumps(data))
            result = subprocess.run([sys.executable, str(CLI), "score", "--input", str(source)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), score_report(data))
        for number in (float("inf"), float("-inf"), float("nan"), 10**400):
            source.write_text(json.dumps({"lint_config": {"numerator": 0, "denominator": number}}))
            result = subprocess.run([sys.executable, str(CLI), "score", "--input", str(source)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)

    def test_script_disables_bytecode_before_package_import(self):
        env = dict(os.environ)
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        program = "import runpy, sys; runpy.run_path(sys.argv[1], run_name='script_probe'); assert sys.dont_write_bytecode; assert 'agent_readiness.cli' in sys.modules"
        result = subprocess.run([sys.executable, "-c", program, str(CLI)], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class MCPCollectedProblemsTests(unittest.TestCase):
    setUp = blind_fixture.SyntheticBlindSubmitTests.setUp
    write_trace = blind_fixture.SyntheticBlindSubmitTests.write_trace

    def test_mcp_session_restrictions_and_validator_errors_are_collected_together(self):
        data = deepcopy(self.evaluation)
        data["evidence"]["lint_config"] = [{"kind": "command", "command": "git log -1", "cwd": ".", "output": "fixture", "exitCode": 0}]
        data["report"]["type_check"].update(denominator=8, rationale="")
        response = self.gate.submit({"evaluation_json": json.dumps(data)})
        self.assertFalse(response["accepted"])
        problems = response["diagnostics"]["problems"]
        for fragment in ("lint_config evidence[0].kind", "type_check.denominator", "type_check.rationale"):
            self.assertTrue(any(fragment in message for message in problems), problems)

    def test_mcp_and_full_validator_return_the_same_collected_list(self):
        data = deepcopy(self.evaluation)
        data["report"]["lint_config"].update(denominator=8, rationale="")
        data["evidence"]["type_check"][0]["note"] = ""
        with self.assertRaises(ReportError) as caught:
            validate_evaluation(deepcopy(data), self.target)
        response = self.gate.submit({"evaluation_json": json.dumps(data)})
        self.assertFalse(response["accepted"])
        self.assertEqual(response["diagnostics"]["problems"], caught.exception.problems)
        self.assertEqual(response["diagnostics"]["problemCount"], caught.exception.problem_count)


if __name__ == "__main__":
    unittest.main()
