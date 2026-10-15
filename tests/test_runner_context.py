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
import test_empty_command_output as fixture


class RunnerContextTests(unittest.TestCase):
    setUp = fixture.EmptyCommandOutputTests.setUp
    cli = fixture.EmptyCommandOutputTests.cli

    @classmethod
    def setUpClass(cls):
        explicit_go = os.environ.get("GO_TEST_BINARY")
        cls.go = explicit_go or shutil.which("go")
        if not cls.go:
            raise unittest.SkipTest("Go runtime unavailable for actual subprocess regression")
        try:
            version = subprocess.run([cls.go, "version"], capture_output=True, text=True, timeout=15)
            valid = version.returncode == 0 and re.match(r"^go version go\d+\.", version.stdout)
        except (OSError, subprocess.TimeoutExpired):
            valid = False
        if not valid:
            if explicit_go:
                raise AssertionError("Explicit GO_TEST_BINARY is not a working Go runtime")
            raise unittest.SkipTest("Automatically discovered go command is not a working Go runtime")
        cache = tempfile.TemporaryDirectory(prefix="readiness-go-evidence-cache-")
        cls.addClassCleanup(cache.cleanup)
        cls.env = dict(os.environ, GOCACHE=cache.name, GOTOOLCHAIN="local", GOPROXY="off", GOSUMDB="off")

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
        self.cli("report", self.root, "--input", source, "--state-dir", self.state)
        before = self.cli("show", self.root, "--state-dir", self.state, "--json")
        self.assertEqual(before["evidence"]["unit_tests_runnable"], [evidence])
        shutil.rmtree(target)
        after = self.cli("show", self.root, "--state-dir", self.state, "--json")
        self.assertEqual(after["evidence"]["unit_tests_runnable"], [evidence])

    def test_literal_dot_prefix_does_not_require_history_filesystem_resolution(self):
        item = {"kind": "command", "command": "cd . && ./mvnw -Dtest=ExampleTest test", "cwd": "app",
                "exitCode": 0, "output": "Tests run: 1, Failures: 0, Errors: 0, Skipped: 0\n", "timeoutSeconds": 60}
        self.assertFalse((self.root / "app").exists())
        self.assert_runnable(item, apps={"app": {"description": "Historical fixture"}})

    def test_unsafe_or_non_noop_prefixes_remain_rejected(self):
        runner = "./mvnw -Dtest=ExampleTest test"
        prefixes = [
            "cd /tmp && ", "cd app && ", "cd ../target && ", "cd .; ", "cd . || ",
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
