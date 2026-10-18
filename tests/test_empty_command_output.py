"""Real empty child-process output must survive synthetic report validation.

The surrounding 84 judgments are explicitly synthetic schema inputs. These
checks are not real model assessments or proof of the fixture's readiness.
"""

from copy import deepcopy
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

from agent_readiness.report import ReportError, _historical_evidence, _test_evidence
from agent_readiness.repository import RepositoryError, validate_evidence


CLI = Path(__file__).resolve().parents[1] / "scripts" / "readiness.py"


class EmptyCommandOutputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="readiness-empty-output-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "target"
        self.root.mkdir()
        self.state = self.base / "state"
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, timeout=20)
        (self.root / "README.md").write_text("# Explicitly synthetic validation fixture\n")

    def cli(self, *arguments):
        result = subprocess.run(
            [sys.executable, str(CLI), *map(str, arguments)],
            cwd=self.base, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def actual_command(self, program):
        argv = [sys.executable, "-c", program]
        result = subprocess.run(argv, cwd=self.root, capture_output=True, text=True, timeout=5)
        return {
            "kind": "command", "command": shlex.join(argv), "cwd": ".",
            "exitCode": result.returncode, "output": result.stdout + result.stderr,
            "timeoutSeconds": 5,
        }

    def save_and_read(self, evidence):
        task = self.base / "task"
        receipt = self.cli("prepare", self.root, "--output", task, "--state-dir", self.state)
        evaluation = json.loads(Path(receipt["evaluation"]).read_text())
        catalog = self.cli("catalog")
        evaluation["apps"] = {".": {"description": "Synthetic schema fixture"}}
        for criterion in catalog["criteria"]:
            key = criterion["id"]
            evaluation["report"][key] = {
                "numerator": 0, "denominator": 1,
                "rationale": "Synthetic failed judgment for validation; not a real audit.",
            }
            evaluation["evidence"][key] = [{
                "kind": "file", "path": "README.md", "line": 1,
                "note": "Identifies synthetic validation fixture.",
            }]
        evaluation["evidence"]["lint_config"] = [evidence]
        source = self.base / "evaluation.json"
        source.write_text(json.dumps(evaluation))
        self.cli("report", self.root, "--input", source, "--state-dir", self.state, "--json")
        shown = self.cli("show", self.root, "--state-dir", self.state, "--json")
        self.assertEqual(shown["evidence"]["lint_config"], [evidence])

    def test_silent_failed_subprocess_saved_and_read_back_without_invented_output(self):
        evidence = self.actual_command("import sys; sys.exit(1)")
        self.assertEqual((evidence["exitCode"], evidence["output"]), (1, ""))
        self.save_and_read(evidence)

    def test_whitespace_subprocess_output_survives_verbatim(self):
        evidence = self.actual_command("import sys; sys.stdout.write(' \\t\\n')")
        self.assertEqual((evidence["exitCode"], evidence["output"]), (0, " \t\n"))
        self.save_and_read(evidence)

    def test_current_and_historical_command_output_reject_non_strings(self):
        evidence = self.actual_command("import sys; sys.exit(1)")
        for invalid in (None, False, 0, [], {}):
            with self.subTest(output=invalid):
                item = dict(evidence, output=invalid)
                with self.assertRaises(RepositoryError):
                    validate_evidence(self.root, [item])
                with self.assertRaises(ReportError):
                    _historical_evidence([item])
        missing = deepcopy(evidence)
        del missing["output"]
        with self.assertRaises(RepositoryError):
            validate_evidence(self.root, [missing])
        with self.assertRaises(ReportError):
            _historical_evidence([missing])

    def test_silent_output_does_not_prove_unit_tests_runnable_in_any_language(self):
        for command in (
            "go test ./...", "npm test -- --listTests", "npx --no-install vitest list",
            "python -m pytest --collect-only tests/test_app.py",
            "./mvnw -Dtest=ExampleTest test", "./gradlew --rerun-tasks test --tests ExampleTest",
        ):
            for output in ("", " \t\n"):
                with self.subTest(command=command, output=output):
                    item = {"kind": "command", "command": command, "cwd": ".",
                            "exitCode": 0, "output": output, "timeoutSeconds": 30}
                    with self.assertRaises(ReportError):
                        _test_evidence([item], 1, {".": {"description": "Synthetic fixture"}})

    def test_known_runner_output_retains_language_contract(self):
        for command, output in (
            ("go test ./...", "ok example.invalid/project 0.020s\n"),
            ("npm test -- --listTests", "/fixture/tests/app.test.js\n"),
            ("python -m pytest --collect-only tests/test_app.py", "2 tests collected in 0.01s\n"),
            ("./mvnw -Dtest=ExampleTest test", "Tests run: 1, Failures: 0, Errors: 0, Skipped: 0\n"),
            ("./gradlew --rerun-tasks test --tests ExampleTest", "> Task :test\nBUILD SUCCESSFUL\n"),
        ):
            with self.subTest(command=command):
                item = {"kind": "command", "command": command, "cwd": ".",
                        "exitCode": 0, "output": output, "timeoutSeconds": 30}
                _test_evidence([item], 1, {".": {"description": "Synthetic fixture"}})
