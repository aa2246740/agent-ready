"""Real bounded Go executions plus explicit same-cwd evidence compatibility.

Full report judgments are synthetic schema fixtures. Go runner output is captured
from real child processes; no model API or Factory backend is used here.
"""

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

from agent_readiness.report import ReportError, _test_evidence
try:
    from tests import test_empty_command_output as fixture
except ImportError:
    import test_empty_command_output as fixture


@unittest.skipUnless(os.environ.get("GO_TEST_BINARY") or shutil.which("go"), "go is required")
class RunnerContextTests(unittest.TestCase):
    cli = fixture.EmptyCommandOutputTests.cli

    @classmethod
    def setUpClass(cls):
        explicit_go = os.environ.get("GO_TEST_BINARY")
        cls.go = explicit_go or shutil.which("go")
        cls.go_skip = None
        if not cls.go:
            cls.go_skip = "go is required"
            return
        try:
            version = subprocess.run([cls.go, "version"], capture_output=True, text=True, timeout=15)
            valid = version.returncode == 0 and re.match(r"^go version go\d+\.", version.stdout)
        except (OSError, subprocess.TimeoutExpired):
            valid = False
        if not valid:
            if explicit_go:
                raise AssertionError("Explicit GO_TEST_BINARY is not a working Go runtime")
            cls.go_skip = "Automatically discovered go command is not a working Go runtime"
            return
        cache = tempfile.TemporaryDirectory(prefix="readiness-go-evidence-cache-")
        cls.addClassCleanup(cache.cleanup)
        cls.env = dict(os.environ, GOCACHE=cache.name, GOTOOLCHAIN="local", GOPROXY="off", GOSUMDB="off")

    def setUp(self):
        if self.go_skip:
            self.skipTest(self.go_skip)
        fixture.EmptyCommandOutputTests.setUp(self)

    def go_fixture(self, subdirectory="."):
        path = self.root / subdirectory
        path.mkdir(exist_ok=True)
        (path / "go.mod").write_text("module example.invalid/readiness-execution\n\ngo 1.18\n")
        (path / "actual_test.go").write_text(
            'package execution\nimport "testing"\n'
            'func TestActual(t *testing.T) { if 2 + 2 != 4 { t.Fatal("arithmetic") } }\n'
        )
        empty = path / "empty"
        empty.mkdir()
        (empty / "empty.go").write_text("package empty\nfunc Answer() int { return 42 }\n")
        return path

    def execute(self, arguments, *, cwd=".", prefix=None):
        command = shlex.join([self.go, "test", "-count=1", *arguments])
        if prefix is not None:
            command = prefix + " && " + command
        result = subprocess.run(
            ["bash", "-c", command], cwd=self.root / cwd, env=self.env,
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return {"kind": "command", "command": command, "cwd": cwd,
                "exitCode": result.returncode, "output": result.stdout + result.stderr,
                "timeoutSeconds": 120}

    def assert_runnable(self, evidence, *, passing=True, apps=None):
        apps = apps or {".": {"description": "Synthetic Go fixture"}}
        if passing:
            _test_evidence([evidence], 1, apps, self.root)
        else:
            with self.assertRaises(ReportError):
                _test_evidence([evidence], 1, apps, self.root)

    def test_real_go_unmatched_selection_and_testless_package_do_not_pass(self):
        self.go_fixture()
        for arguments, marker in ((["-run", "^DoesNotExist$", "."], "[no tests to run]"), (["./empty"], "[no test files]")):
            with self.subTest(arguments=arguments):
                evidence = self.execute(arguments)
                self.assertIn(marker, evidence["output"])
                self.assert_runnable(evidence, passing=False)

    def test_real_go_list_mode_lists_at_least_one_test(self):
        self.go_fixture()
        item = self.execute(["-list", ".", "."])
        self.assertIn("TestActual", item["output"])
        self.assertNotIn("=== RUN", item["output"])
        self.assert_runnable(item)
        item = self.execute(["-list", "DoesNotExist", "."])
        self.assertNotIn("TestActual", item["output"])
        self.assert_runnable(item, passing=False)

    def test_real_go_import_path_resolves_nested_local_module_owner(self):
        self.go_fixture('app')
        (self.root / 'other').mkdir()
        (self.root / 'go.work').write_text('go 1.18\n\nuse ./app\n')
        item = self.execute(['-list', '.', 'example.invalid/readiness-execution'])
        self.assertIn('TestActual', item['output'])
        apps = {'app': {'description': 'Actual Go test module'}, 'other': {'description': 'Separate app'}}
        item['application'] = 'app'
        self.assert_runnable(item, apps=apps)
        item['application'] = 'other'
        self.assert_runnable(item, apps=apps, passing=False)

    def test_real_go_positive_and_mixed_packages_do_pass(self):
        self.go_fixture()
        for arguments in (["-run", "^TestActual$", "."], ["-run", "^TestActual$", "./..."]):
            with self.subTest(arguments=arguments):
                evidence = self.execute(arguments)
                self.assertIn("ok", evidence["output"])
                if arguments[-1] == "./...":
                    self.assertIn("[no test files]", evidence["output"])
                self.assert_runnable(evidence)

    def test_real_go_json_mixed_packages_preserve_positive_test_event(self):
        self.go_fixture()
        evidence = self.execute(["-json", "-run", "^TestActual$", "./..."])
        self.assertIn('"Test":"TestActual"', evidence["output"])
        self.assertIn("[no test files]", evidence["output"])
        self.assert_runnable(evidence)

    def test_actual_noop_cd_prefix_saved_verbatim_and_history_survives_deleted_cwd(self):
        target = self.go_fixture("app")
        evidence = self.execute(["-run", "^TestActual$", "."], cwd="app", prefix="cd -- " + shlex.quote(str(target)))
        task = self.base / "task"
        receipt = self.cli("prepare", self.root, "--output", task, "--state-dir", self.state)
        evaluation = json.loads(Path(receipt["evaluation"]).read_text())
        evaluation["apps"] = {"app": {"description": "Synthetic Go application"}}
        for criterion in self.cli("catalog")["criteria"]:
            key = criterion["id"]
            evaluation["report"][key] = {"numerator": 0, "denominator": 1,
                                          "rationale": "Synthetic validation judgment, not a real assessment."}
            evaluation["evidence"][key] = [{"kind": "file", "path": "README.md", "line": 1,
                                            "note": "Synthetic fixture identification."}]
        evaluation["report"]["unit_tests_runnable"]["numerator"] = 1
        evaluation["evidence"]["unit_tests_runnable"] = [evidence]
        source = self.base / "evaluation.json"
        source.write_text(json.dumps(evaluation))
        self.cli("report", self.root, "--input", source, "--state-dir", self.state, "--json")
        before = self.cli("show", self.root, "--state-dir", self.state, "--json")
        self.assertEqual(before["evidence"]["unit_tests_runnable"], [evidence])
        shutil.rmtree(target)
        after = self.cli("show", self.root, "--state-dir", self.state, "--json")
        self.assertEqual(after["evidence"]["unit_tests_runnable"], [evidence])

    def test_literal_dot_prefix_does_not_require_history_filesystem_resolution(self):
        item = {"kind": "command", "command": "cd . && ./mvnw -Dtest=ExampleTest test", "cwd": "app",
                "exitCode": 0, "output": "Tests run: 1, Failures: 0, Errors: 0, Skipped: 0\n", "timeoutSeconds": 60}
        self.assertFalse((self.root / "app").exists())
        with self.assertRaises(ReportError):
            self.assert_runnable(item, apps={"app": {"description": "Historical fixture"}})
        _test_evidence([item], 1, {"app": {"description": "Historical fixture"}}, self.root, check_paths=False)

    def test_unsafe_or_non_noop_prefixes_remain_rejected(self):
        runner = "./mvnw -Dtest=ExampleTest test"
        prefixes = [
            "cd /tmp && ", "cd app && ", "cd ../outside && ", "cd .; ", "cd . || ",
            "cd . && cd . && ", "cd $PWD && ", "cd ~ && ", "cd \"$(pwd)\" && ",
            "cd . && printf ok && ", "cd . && env X=$(pwd) ", "cd . & ",
            "cd . && "+runner+" > /tmp/runner-output && ",
        ]
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                item = {"kind": "command", "command": prefix + runner, "cwd": ".", "exitCode": 0,
                        "output": "Tests run: 1, Failures: 0, Errors: 0, Skipped: 0\n", "timeoutSeconds": 60}
                self.assert_runnable(item, passing=False)

    def test_noop_prefix_does_not_relax_java_or_empty_output_contracts(self):
        for command, output in (
            ("./mvnw -Dtest=ExampleTest -DskipTests test", "BUILD SUCCESS"),
            ("./mvnw -Dtest=ExampleTest test", "Tests run: 0, Failures: 0, Errors: 0, Skipped: 0"),
            ("./gradlew test --tests ExampleTest --dry-run", "Task :test SKIPPED"),
            ("npm test -- --listTests", ""),
            ("go test -run Example .", " \t\n"),
        ):
            with self.subTest(command=command):
                item = {"kind": "command", "command": "cd . && " + command, "cwd": ".",
                        "exitCode": 0, "output": output, "timeoutSeconds": 30}
                self.assert_runnable(item, passing=False)


class RootCwdContractTests(unittest.TestCase):
    """Captured records only. These checks do not execute the recorded runners."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="readiness-root-cwd-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "repo"
        (self.root / "apps" / "a").mkdir(parents=True)
        (self.root / "apps" / "b").mkdir(parents=True)
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_a.py").write_text("def test_one():\n    assert 1\n\ndef test_two():\n    assert 2\n")
        self.single = {".": {"description": "Synthetic single application"}}
        self.multi = {
            "apps/a": {"description": "Synthetic application a"},
            "apps/b": {"description": "Synthetic application b"},
        }

    def row(self, command, output, application=None):
        item = {
            "kind": "command", "command": command, "cwd": ".", "exitCode": 0,
            "output": output, "timeoutSeconds": 30,
        }
        if application is not None:
            item["application"] = application
        return item

    def assert_rejected(self, command, output, apps=None, application=None):
        with self.assertRaises(ReportError):
            _test_evidence([self.row(command, output, application)], 1, apps or self.single, self.root)

    def assert_accepted(self, command, output, apps=None, application=None):
        _test_evidence([self.row(command, output, application)], 1, apps or self.single, self.root)

    def test_plain_cwd_rejects_retarget_shell_and_non_runners(self):
        collected = "collected 1 item\n"
        for command in (
            "env -C /other/repo pytest --collect-only tests/test_a.py",
            'sh -c "echo test"',
            "python3 -m pytest --collect-only --rootdir=/elsewhere tests/test_a.py",
            "test -d tests",
            "git log --grep test",
        ):
            with self.subTest(command=command):
                self.assert_rejected(command, collected)

    def test_root_scope_checks_include_environment_and_config_options(self):
        for command in (
            "pytest --collect-only -c ../outside/config.ini tests/test_a.py",
            "PYTEST_ADDOPTS='--rootdir=/outside' pytest --collect-only tests/test_a.py",
            "PYTEST_ADDOPTS='-c ../outside/config.ini' pytest --collect-only tests/test_a.py",
        ):
            with self.subTest(command=command):
                self.assert_rejected(command, "collected 1 item\n")

    def test_local_pytest_config_remains_valid_but_other_app_config_is_rejected(self):
        self.assert_accepted("pytest --collect-only -c tests/config.ini tests/test_a.py", "collected 2 items\n")
        self.assert_rejected("pytest --collect-only -c apps/b/pytest.ini apps/a/tests/test_a.py",
                             "collected 2 items\n", self.multi, "apps/a")

    def test_scope_selectors_cannot_follow_symlinks_to_other_apps(self):
        (self.root / "alias").symlink_to(self.root / "apps" / "b", target_is_directory=True)
        self.assert_rejected("go test ./alias/...", "ok example.invalid/b 0.01s\n", self.multi, "apps/a")
        self.assert_rejected("npm --prefix alias test -- --listTests", "tests/example.test.ts\n",
                             self.multi, "apps/a")

    def test_every_wrapper_validates_the_inner_runner(self):
        for wrapper in ("uv", "poetry", "pdm", "pipenv", "hatch"):
            with self.subTest(wrapper=wrapper):
                self.assert_accepted(f"{wrapper} run pytest --collect-only tests/test_a.py", "collected 2 items\n")
                self.assert_rejected(f"{wrapper} run git log --grep test", "test\n")
                self.assert_rejected(f"{wrapper} --project apps/b run pytest --collect-only tests/test_a.py",
                                     "collected 2 items\n")

    def test_missing_test_file_does_not_count_even_when_parent_is_missing(self):
        self.assert_rejected("pytest --collect-only missing/test_a.py", "collected 2 items\n")

    def test_nonexecuting_runner_modes_do_not_count(self):
        for command in (
            "go version test", "go help test", "go test -c", "go test -list Test",
            "cargo test --no-run=true", "cargo test -- --list", "ctest -N",
            "ctest --show-only=json-v1", "dotnet test --list-tests",
            "rspec --dry-run", "phpunit --list-tests", "mix help test", "dart help test",
            "deno info test", "bundle exec rake -T", "ctest --help-command test", "go test -h", "go test -n", "bundle exec rake -T test",
        ):
            with self.subTest(command=command):
                self.assert_rejected(command, "test fixture\n")

    def test_uv_run_accepts_one_file_collection_and_rejects_execution(self):
        accepted = "uv run pytest --collect-only tests/test_a.py"
        self.assert_accepted(accepted, "collected 1 item\n")
        self.assert_rejected("uv run pytest tests", "collected 1 item\n")
        self.assert_rejected("uv run pytest tests", "3 passed\n")
        self.assert_rejected(accepted, "3 passed\n")

    def test_quiet_pytest_count_and_timeout_keyword_are_accepted(self):
        self.assert_accepted("pytest --collect-only -qq tests/test_a.py", "tests/test_a.py: 2\n")
        self.assert_accepted("pytest --collect-only -k timeout tests/test_a.py", "collected 1 item\n")

    def test_root_command_labeled_as_another_application_is_rejected(self):
        cases = (
            ("go test ./apps/a/...", "ok example.invalid/apps/a 0.012s\n"),
            (
                "mvn -f apps/a/pom.xml -Dtest=FooTest test",
                "Running FooTest\nTests run: 1, Failures: 0, Errors: 0, Skipped: 0\n",
            ),
            (
                "gradle :apps:a:test --rerun-tasks --tests FooTest",
                "> Task :apps:a:test\n",
            ),
        )
        for command, output in cases:
            with self.subTest(command=command):
                self.assert_rejected(command, output, self.multi, "apps/b")
                self.assert_accepted(command, output, self.multi, "apps/a")


if __name__ == "__main__":
    unittest.main()
