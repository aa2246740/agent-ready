"""Public CLI workflow tests with explicitly synthetic assessment judgments.

These tests exercise schema validation, scoring and task generation. They do not
claim that their temporary fixture satisfies the 84 real readiness criteria.
Actual command evidence retains the child process's exit status and output;
other fixture records are explicitly marked synthetic.
"""

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

from agent_readiness.report import ReportError, latest_report, store_report
from agent_readiness.repository import inspect_repository


PROJECT = Path(__file__).resolve().parents[1]
CLI = PROJECT / "scripts" / "readiness.py"


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(
            [sys.executable, str(CLI), "catalog"],
            cwd=PROJECT.parent, capture_output=True, text=True, timeout=30,
        )
        if result.returncode:
            raise AssertionError(f"catalog failed ({result.returncode}): {result.stderr}")
        cls.catalog = json.loads(result.stdout)
        cls.criteria = {row["id"]: row for row in cls.catalog["criteria"]}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="readiness-workflow-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "synthetic-repository"
        self.root.mkdir()
        self.state = self.base / "external-state"
        self.cwd = self.base / "unrelated-cwd"
        self.cwd.mkdir()
        self.command_results = []
        self.counter = 0
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GIT_CONFIG_NOSYSTEM="1")
        self.git("init", "-b", "main")
        self.git("config", "user.email", "workflow-tests@example.invalid")
        self.git("config", "user.name", "Synthetic Workflow Fixture")
        (self.root / "README.md").write_text(
            "# Synthetic readiness workflow fixture\n"
            "Assessment values are synthetic validation inputs, not real readiness findings.\n",
            encoding="utf-8",
        )
        (self.root / "app.py").write_text(
            "def normalize_name(value):\n    return value.strip().lower()\n", encoding="utf-8",
        )
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_app.py").write_text(
            "import unittest\nfrom app import normalize_name\n\n"
            "class NameTests(unittest.TestCase):\n"
            "    def test_normalization(self):\n"
            "        self.assertEqual(normalize_name('  ALICE  '), 'alice')\n"
            "    def test_empty_input(self):\n"
            "        self.assertEqual(normalize_name('  '), '')\n",
            encoding="utf-8",
        )
        self.git("add", ".")
        self.git("commit", "-m", "Synthetic CLI workflow fixture")

    def process(self, args, *, cwd=None):
        result = subprocess.run(
            [str(arg) for arg in args], cwd=cwd or self.cwd,
            capture_output=True, text=True, timeout=30, env=self.env,
        )
        self.command_results.append(result)
        return result

    def git(self, *args):
        result = self.process(["git", "-C", self.root, *args])
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def cli(self, *args, ok=True):
        result = self.process([sys.executable, CLI, *args])
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout[:2000])
            self.assertEqual(result.stdout, "", "Rejected input must not publish partial stdout")
            self.assertTrue(result.stderr.strip())
            self.assertNotIn("Traceback", result.stderr)
        return result

    @staticmethod
    def tree(path):
        if not path.exists():
            return None
        return {
            str(item.relative_to(path)): item.read_bytes()
            for item in path.rglob("*") if item.is_file() and ".git" not in item.relative_to(path).parts
        }

    def prepare(self):
        self.counter += 1
        output = self.base / f"task-{self.counter}"
        result = self.cli("prepare", self.root, "--output", output, "--state-dir", self.state)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["status"], "awaiting_agent_evaluation")
        self.assertEqual(receipt["criteria"], 84)
        return output, json.loads(Path(receipt["evaluation"]).read_text(encoding="utf-8"))

    def completed(self, data, *, apps=None):
        """Supply legal synthetic failures, never pretend these are real findings."""
        data = deepcopy(data)
        data["apps"] = apps or {".": {"description": "Synthetic command-line fixture application"}}
        for key, criterion in self.criteria.items():
            data["report"][key] = {
                "numerator": 0,
                "denominator": len(data["apps"]) if criterion["scope"] == "application" else 1,
                "rationale": f"Synthetic workflow fixture: {key}; not a real criterion assessment.",
            }
            data["evidence"][key] = [{
                "kind": "file", "path": "README.md", "line": 1,
                "note": "Identifies this fixture and its synthetic assessment scope.",
            }]
        return data

    def save_input(self, data):
        self.counter += 1
        path = self.base / f"evaluation-{self.counter}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def submit(self, data):
        result = self.cli("report", self.root, "--input", self.save_input(data), "--state-dir", self.state, "--json")
        return json.loads(result.stdout)

    def reject_submission(self, data):
        path = self.save_input(data)
        state_before, target_before = self.tree(self.state), self.tree(self.root)
        result = self.cli("report", self.root, "--input", path, "--state-dir", self.state, ok=False)
        self.assertEqual(self.tree(self.state), state_before)
        self.assertEqual(self.tree(self.root), target_before)
        return result

    def test_prepare_assess_report_fix_reassess_and_changes(self):
        before = self.tree(self.root)
        task, template = self.prepare()
        self.assertEqual(set(template["report"]), set(self.criteria))
        self.assertTrue(all(row["numerator"] == "UNASSESSED" for row in template["report"].values()))
        task_text = (task / "assessment.md").read_text(encoding="utf-8")
        for key in self.criteria:
            self.assertIn(key, task_text)
        data = self.completed(template)
        first = self.submit(data)
        self.assertEqual(first["summary"]["scorePercent"], 0)
        markdown = Path(first["markdown"]).read_text(encoding="utf-8")
        for heading in ("# Level", "# Applications", "# Criteria", "# Action Items"):
            self.assertIn(heading, markdown)
        shown = json.loads(self.cli("show", self.root, "--state-dir", self.state, "--json").stdout)
        self.assertEqual(shown["report"], data["report"])
        self.assertEqual(self.tree(self.root), before)
        self.assertEqual(self.git("status", "--porcelain"), "")

        repair = self.cli("fix", self.root, "--state-dir", self.state, "--signals", "readme").stdout
        self.assertIn("(`readme`)", repair)
        self.assertNotIn("(`lint_config`)", repair)
        self.assertIn("re-evaluate", repair.lower())
        self.assertEqual(self.tree(self.root), before, "Generating a fix task must not edit the target")
        old_json = Path(first["report"]).read_bytes()
        (self.root / "README.md").write_text(
            "# Synthetic readiness workflow fixture\n"
            "Run `python -m unittest discover -s tests -v` to test name normalization.\n"
            "Judgments in this test remain synthetic.\n", encoding="utf-8",
        )
        self.reject_submission(data)
        stale_task = self.cli("fix", self.root, "--state-dir", self.state, "--signals", "readme").stdout
        self.assertIn("changed since", stale_task)
        next_task, next_template = self.prepare()
        self.assertTrue((next_task / "previous-report.json").is_file())
        updated = self.completed(next_template)
        updated["report"]["readme"]["numerator"] = 1
        updated["report"]["readme"]["rationale"] = "Synthetic pass for exercising changed-score reporting after README edit."
        second = self.submit(updated)
        self.assertAlmostEqual(second["summary"]["scorePercent"], 100 / 84)
        self.assertNotEqual(first["report"], second["report"])
        self.assertEqual(Path(first["report"]).read_bytes(), old_json)
        changed = Path(second["markdown"]).read_text(encoding="utf-8")
        self.assertIn("# Changes Since Last Report", changed)
        self.assertIn("`readme`: 0 / 1 → 1 / 1", changed)
        self.assertIn(updated["report"]["readme"]["rationale"], changed.split("# Changes Since Last Report")[1])
        shown_markdown = self.cli("show", self.root, "--state-dir", self.state).stdout
        self.assertEqual(shown_markdown, changed, "Viewing a stored report must preserve its changes and prior metadata")
        self.assertEqual(json.loads(self.cli("show", self.root, "--state-dir", self.state, "--json").stdout)["report"], updated["report"])

    def test_report_history_preserves_application_changes_and_ignores_injected_comparison(self):
        _, template = self.prepare()
        data = self.completed(template)
        data["comparison"] = {"criteria": [{"criterionId": "fabricated"}]}
        first = self.submit(data)
        first_document = json.loads(Path(first["report"]).read_text())
        self.assertNotIn("comparison", first_document)
        old_bytes = Path(first["report"]).read_bytes()
        _, template = self.prepare()
        updated = self.completed(template)
        updated["apps"]["."]["description"] = "Revised description of the same fixture application"
        updated["comparison"] = {"criteria": [{"criterionId": "fabricated"}]}
        receipt = self.submit(updated)
        markdown = Path(receipt["markdown"]).read_text()
        self.assertIn("Application updated: `.`", markdown)
        self.assertIn(first_document["id"], markdown)
        self.assertIn(first_document["createdAt"], markdown)
        self.assertNotIn("fabricated", markdown)
        self.assertEqual(self.cli("show", self.root, "--state-dir", self.state).stdout, markdown)
        self.assertEqual(Path(first["report"]).read_bytes(), old_bytes)

    def test_report_preserves_agent_selected_concrete_actions_and_rejects_false_targets(self):
        _, template = self.prepare()
        data = self.completed(template)
        action = {"criterionId": "lint_config", "action": "Configure Ruff for app.py and tests/.",
                  "reason": "Check undefined names before editing this Python application."}
        data["actionItems"] = [action]
        receipt = self.submit(data)
        markdown = Path(receipt["markdown"]).read_text()
        section = markdown.split("# Action Items", 1)[1]
        self.assertIn(action["action"], section)
        self.assertIn(action["reason"], section)
        self.assertEqual(self.cli("show", self.root, "--state-dir", self.state).stdout, markdown)
        for bad_actions in ([action, action], [{**action, "criterionId": "invented"}],
                            [{**action, "action": ""}], [action] * 4, "not-a-list"):
            with self.subTest(actions=bad_actions):
                bad = deepcopy(data)
                bad["actionItems"] = bad_actions
                self.reject_submission(bad)
        for numerator in (1, None):
            bad = deepcopy(data)
            bad["report"]["strict_typing"]["numerator"] = numerator
            bad["actionItems"] = [{**action, "criterionId": "strict_typing"}]
            self.reject_submission(bad)

    def test_multi_application_partial_credit_and_skip_have_equal_signal_weight(self):
        for name in ("one", "two"):
            directory = self.root / "apps" / name
            directory.mkdir(parents=True)
            (directory / "README.md").write_text(f"Synthetic application {name}\n", encoding="utf-8")
        _, template = self.prepare()
        data = self.completed(template, apps={
            "apps/one": {"description": "Synthetic first application"},
            "apps/two": {"description": "Synthetic second application"},
        })
        data["report"]["lint_config"]["numerator"] = 1
        data["report"]["readme"]["numerator"] = 1
        data["report"]["strict_typing"]["numerator"] = None
        receipt = self.submit(data)
        self.assertAlmostEqual(receipt["summary"]["scorePercent"], (0.5 + 1) / 83 * 100)
        self.assertEqual(data["report"]["lint_config"]["denominator"], 2)
        self.assertEqual(data["report"]["readme"]["denominator"], 1)
        prompt = self.cli("fix", self.root, "--state-dir", self.state, "--signals", "lint_config,readme,strict_typing").stdout
        self.assertIn("(`lint_config`) [1/2]", prompt)
        self.assertNotIn("(`readme`)", prompt)
        self.assertNotIn("(`strict_typing`)", prompt)
        bad = deepcopy(data)
        bad["report"]["lint_config"]["denominator"] = 1
        self.reject_submission(bad)
        bad = deepcopy(data)
        bad["report"]["readme"]["denominator"] = 2
        self.reject_submission(bad)

    def test_incomplete_and_invalid_evaluations_never_publish_or_write(self):
        _, template = self.prepare()
        self.reject_submission(template)
        valid = self.completed(template)
        cases = {}
        for section in ("report", "evidence"):
            missing = deepcopy(valid)
            del missing[section]["lint_config"]
            cases[f"missing {section} key"] = missing
            extra = deepcopy(valid)
            extra[section]["invented_criterion"] = deepcopy(extra[section]["lint_config"])
            cases[f"extra {section} key"] = extra
        for field, value in (("numerator", None), ("numerator", "UNASSESSED"), ("numerator", True),
                             ("numerator", 2), ("denominator", 0), ("rationale", ""),
                             ("rationale", "😀" * 251)):
            data = deepcopy(valid)
            data["report"]["lint_config"][field] = value
            cases[f"{field}={value!r}"] = data
        bad = deepcopy(valid)
        bad["evidence"]["lint_config"] = []
        cases["empty evidence"] = bad
        bad = deepcopy(valid)
        bad["schemaVersion"] = True
        cases["boolean schema version"] = bad
        for name, data in cases.items():
            with self.subTest(case=name):
                self.reject_submission(data)

    def test_duplicate_json_key_is_rejected_before_writing_state(self):
        _, template = self.prepare()
        path = self.save_input(self.completed(template))
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace('"numerator": 0', '"numerator": 0, "numerator": 1', 1), encoding="utf-8")
        before = self.tree(self.state)
        result = self.cli("report", self.root, "--input", path, "--state-dir", self.state, ok=False)
        self.assertIn("Duplicate", result.stderr)
        self.assertEqual(self.tree(self.state), before)

    def test_evaluation_cannot_be_submitted_for_a_different_repository(self):
        _, template = self.prepare()
        path = self.save_input(self.completed(template))
        other = self.base / "other-repository"
        clone = self.process(["git", "clone", "--no-hardlinks", self.root, other])
        self.assertEqual(clone.returncode, 0, clone.stderr)
        before = self.tree(other)
        self.cli("report", other, "--input", path, "--state-dir", self.state, ok=False)
        self.assertEqual(self.tree(other), before)
        self.assertFalse(self.state.exists())

    def test_task_and_state_outputs_inside_target_are_rejected(self):
        before = self.tree(self.root)
        self.cli("prepare", self.root, "--output", self.root / "task", "--state-dir", self.state, ok=False)
        self.cli("prepare", self.root, "--output", self.base / "not-created", "--state-dir", self.root / "state", ok=False)
        self.assertFalse((self.base / "not-created").exists())
        _, template = self.prepare()
        path = self.save_input(self.completed(template))
        self.cli("report", self.root, "--input", path, "--state-dir", self.root / "state", ok=False)
        self.assertEqual(self.tree(self.root), before)
        self.assertFalse((self.root / "state").exists())

    def test_prepare_preserves_an_existing_task(self):
        task, _ = self.prepare()
        before = self.tree(task)
        self.cli("prepare", self.root, "--output", task, "--state-dir", self.state, ok=False)
        self.assertEqual(self.tree(task), before)

    def test_show_without_report_fails_without_creating_state(self):
        before = self.tree(self.root)
        self.cli("show", self.root, "--state-dir", self.state, ok=False)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.tree(self.root), before)

    def test_fix_without_report_offers_native_report_or_direct_fix_choice(self):
        before = self.tree(self.root)
        prompt = self.cli("fix", self.root, "--state-dir", self.state).stdout
        for text in ("No previous readiness report", "Generate a full report", "Skip the report"):
            self.assertIn(text, prompt)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.tree(self.root), before)

    def test_fix_without_report_uses_selected_catalog_signals_without_inventing_scores(self):
        before = self.tree(self.root)
        common = ["fix", self.root, "--state-dir", self.state]
        prompt = self.cli(*common, "--signals", "lint_config,readme").stdout
        for key in ("lint_config", "readme"):
            self.assertIn(key, prompt)
            self.assertIn(self.criteria[key]["instructions"], prompt)
        self.assertNotIn("Prior report:", prompt)
        self.assertNotIn("[0/1]", prompt)
        self.cli(*common, "--signals", "invented_criterion", ok=False)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.tree(self.root), before)

    def test_fix_without_report_batches_require_a_full_report_first(self):
        before = self.tree(self.root)
        for options in (("--all",), ("--target-level", "4")):
            with self.subTest(options=options):
                prompt = self.cli("fix", self.root, "--state-dir", self.state, *options).stdout
                self.assertIn("No previous readiness report", prompt)
                self.assertIn("Generate a full report", prompt)
                self.assertIn("first", prompt.lower())
                self.assertNotIn("Prior report:", prompt)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.tree(self.root), before)

    def test_deleted_historical_evidence_does_not_prevent_reassessment(self):
        _, template = self.prepare()
        receipt = self.submit(self.completed(template))
        original = Path(receipt["report"]).read_bytes()
        (self.root / "README.md").unlink()
        shown = json.loads(self.cli("show", self.root, "--state-dir", self.state, "--json").stdout)
        self.assertEqual(shown["report"], self.completed(template)["report"])
        task, fresh = self.prepare()
        self.assertTrue((task / "previous-report.json").is_file())
        self.assertNotEqual(fresh["repository"]["fingerprint"], template["repository"]["fingerprint"])
        repair = self.cli("fix", self.root, "--state-dir", self.state, "--signals", "readme").stdout
        self.assertIn("changed since", repair)
        self.assertEqual(Path(receipt["report"]).read_bytes(), original)

    def test_successful_runnable_test_evidence_is_a_real_bounded_command(self):
        if importlib.util.find_spec("pytest") is None:
            self.skipTest("Install the optional test dependency pytest to verify real collection evidence")
        args = [sys.executable, "-m", "pytest", "--collect-only", "-p", "no:cacheprovider", "tests/test_app.py"]
        result = self.process(args, cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 tests collected", result.stdout)
        _, template = self.prepare()
        data = self.completed(template)
        data["report"]["unit_tests_runnable"]["numerator"] = 1
        data["report"]["unit_tests_runnable"]["rationale"] = "The fixture's two real unit tests were collected from one file; no tests executed."
        data["evidence"]["unit_tests_runnable"] = [{
            "kind": "command", "command": shlex.join(args), "cwd": ".",
            "exitCode": result.returncode, "output": result.stdout + result.stderr, "timeoutSeconds": 30,
        }]
        self.submit(data)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_bad_command_evidence_is_rejected_and_never_executed(self):
        _, template = self.prepare()
        valid = self.completed(template)
        valid["report"]["unit_tests_runnable"]["numerator"] = 1
        marker = self.base / "MUST_NOT_EXECUTE"
        command = shlex.join([sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"])
        base = {"kind": "command", "command": command, "cwd": ".", "exitCode": 0,
                "output": "Synthetic invalid evidence; this command was not run.", "timeoutSeconds": 30}
        cases = [
            {**base, "exitCode": 1}, {**base, "exitCode": True}, {**base, "output": ""},
            {**base, "timeoutSeconds": 0}, {**base, "timeoutSeconds": True},
            {**base, "command": command + " | head -10"},
            {**base, "command": "timeout 30 " + command},
            {**base, "command": command + " 2>&1"}, {**base, "cwd": ".."},
        ]
        for item in cases:
            with self.subTest(evidence=item):
                data = deepcopy(valid)
                data["evidence"]["unit_tests_runnable"] = [item]
                self.reject_submission(data)
                self.assertFalse(marker.exists())

    def test_successful_python_test_execution_is_not_collection_evidence(self):
        args = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
        result = self.process(args, cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 2 tests", result.stderr)
        _, template = self.prepare()
        data = self.completed(template)
        data["report"]["unit_tests_runnable"]["numerator"] = 1
        data["evidence"]["unit_tests_runnable"] = [{
            "kind": "command", "command": shlex.join(args), "cwd": ".",
            "exitCode": result.returncode, "output": result.stdout + result.stderr, "timeoutSeconds": 30,
        }]
        self.reject_submission(data)

    def test_score_rejects_invalid_container_and_numeric_overflow_cleanly(self):
        cases = [None, [], {"report": None}, {"report": []},
                 {"lint_config": None}, {"lint_config": []},
                 {"lint_config": {"numerator": 10 ** 400, "denominator": 1}}]
        for data in cases:
            with self.subTest(data=str(data)[:80]):
                path = self.save_input(data)
                result = self.cli("score", "--input", path, ok=False)
                self.assertEqual(result.returncode, 2)
        self.assertFalse(self.state.exists())

    def test_fix_default_selection_explicit_all_and_target_level(self):
        _, template = self.prepare()
        data = self.completed(template)
        self.submit(data)
        before = self.tree(self.root)
        common = ["fix", self.root, "--state-dir", self.state]
        default = self.cli(*common).stdout
        self.assertIn("Selection (native default)", default)
        self.assertIn("single signal", default)
        explicit = self.cli(*common, "--signals", "lint_config").stdout
        self.assertIn("(`lint_config`)", explicit)
        self.assertNotIn("(`type_check`)", explicit)
        self.assertNotIn("Selection (native default)", explicit)
        all_task = self.cli(*common, "--all").stdout
        for key in self.criteria:
            self.assertIn(f"(`{key}`)", all_task)
        target = self.cli(*common, "--target-level", "2").stdout
        self.assertIn("Requested target: Level 2", target)
        self.assertIn("at least 20%", target)
        for args in (("--signals", "invented_criterion"), ("--signals", ", ,"),
                     ("--target-level", "6"), ("--all", "--signals", "lint_config")):
            with self.subTest(args=args):
                self.cli(*common, *args, ok=False)
        met = self.cli(*common, "--target-level", "1").stdout
        self.assertIn("already meets", met)
        self.assertNotIn("## Failing signals", met)
        self.assertEqual(self.tree(self.root), before)

    def test_stored_score_edit_is_rejected_instead_of_trusted(self):
        _, template = self.prepare()
        receipt = self.submit(self.completed(template))
        path = Path(receipt["report"])
        data = json.loads(path.read_text(encoding="utf-8"))
        data["summary"]["scorePercent"] = 100
        data["summary"]["achievedLevel"] = 5
        path.write_text(json.dumps(data), encoding="utf-8")
        self.cli("show", self.root, "--state-dir", self.state, "--json", ok=False)
        self.cli("fix", self.root, "--state-dir", self.state, "--target-level", "5", ok=False)

    def test_latest_report_edits_cannot_bypass_evaluation_validation(self):
        _, template = self.prepare()
        receipt = self.submit(self.completed(template))
        path = Path(receipt["report"])
        original = json.loads(path.read_text(encoding="utf-8"))
        cases = {}
        for section in ("report", "evidence"):
            data = deepcopy(original)
            del data[section]["lint_config"]
            cases[f"missing {section} key"] = data
        for field, value in (("numerator", None), ("numerator", "UNASSESSED"), ("denominator", 2)):
            data = deepcopy(original)
            data["report"]["lint_config"][field] = value
            cases[f"illegal {field} {value!r}"] = data
        data = deepcopy(original)
        data["report"]["unknown_criterion"] = deepcopy(data["report"]["lint_config"])
        cases["extra criterion"] = data
        data = deepcopy(original)
        data["evidence"]["lint_config"] = []
        cases["empty evidence"] = data
        data = deepcopy(original)
        data["repository"]["root"] = str(self.base / "another-root")
        cases["wrong repository root"] = data
        data = deepcopy(original)
        data["apps"] = {}
        cases["missing apps"] = data
        for name, data in cases.items():
            with self.subTest(case=name):
                path.write_text(json.dumps(data), encoding="utf-8")
                before = self.tree(self.state)
                for command, option in (("show", "--json"), ("fix", "--all")):
                    with self.subTest(command=command):
                        self.cli(command, self.root, "--state-dir", self.state, option, ok=False)
                self.assertEqual(self.tree(self.state), before)
        path.write_text(json.dumps(original), encoding="utf-8")

    def test_lone_surrogate_in_rationale_or_note_leaves_no_empty_file(self):
        _, template = self.prepare()
        base = self.completed(template)
        mutations = {
            "rationale": lambda data: data["report"]["lint_config"].__setitem__("rationale", "bad \ud800 rationale"),
            "note": lambda data: data["evidence"]["lint_config"][0].__setitem__("note", "bad \ud800 note"),
        }
        for label, mutate in mutations.items():
            with self.subTest(field=label):
                data = deepcopy(base)
                mutate(data)
                before = self.tree(self.state)
                with self.assertRaisesRegex(ReportError, "surrogate"):
                    store_report(data, self.root, self.state)
                self.assertEqual(self.tree(self.state), before)
                if self.state.exists():
                    for path in self.state.rglob("*"):
                        if path.is_file():
                            self.assertGreater(path.stat().st_size, 0, path)

    def test_monorepo_tooling_cannot_be_skipped_for_multiple_applications(self):
        for name in ("one", "two"):
            directory = self.root / "apps" / name
            directory.mkdir(parents=True)
            (directory / "README.md").write_text(f"Synthetic application {name}\n", encoding="utf-8")
        _, template = self.prepare()
        multiple = self.completed(template, apps={
            "apps/one": {"description": "Synthetic first application"},
            "apps/two": {"description": "Synthetic second application"},
        })
        multiple["report"]["monorepo_tooling"]["numerator"] = None
        rejected = self.reject_submission(multiple)
        self.assertIn("monorepo_tooling cannot be skipped when multiple applications exist", rejected.stderr)
        single = self.completed(template)
        single["report"]["monorepo_tooling"]["numerator"] = None
        self.submit(single)

    def test_preupgrade_local_report_pointer_and_judgments_remain_readable(self):
        import hashlib
        _, template = self.prepare()
        data = self.completed(template)
        receipt = store_report(data, self.root, self.state)
        report_path = Path(receipt["report"])
        stored = json.loads(report_path.read_text(encoding="utf-8"))
        stored["report"]["agents_md"]["numerator"] = 0
        stored["report"]["agents_md_validation"]["numerator"] = 1
        report_path.write_text(json.dumps(stored), encoding="utf-8")
        pointer = report_path.parent / "latest.json"
        index = json.loads(pointer.read_text(encoding="utf-8"))
        index["sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
        pointer.write_text(json.dumps(index), encoding="utf-8")
        repository = inspect_repository(self.root)
        old_directory = self.state / hashlib.sha256(repository["identity"].encode()).hexdigest()[:24]
        report_path.parent.rename(old_directory)
        loaded = latest_report(repository, self.state)
        self.assertEqual(loaded["report"]["agents_md_validation"]["numerator"], 1)
        with self.assertRaisesRegex(ReportError, "requires agents_md"):
            store_report(stored, self.root, self.state)

    def test_relative_shared_origins_do_not_share_latest_report(self):
        self.git("remote", "add", "origin", "../shared.git")
        other = self.base / "other-checkout"
        other.mkdir()
        for args in (
            ["git", "init", "-q", "-b", "main", str(other)],
            ["git", "-C", str(other), "config", "user.email", "workflow-tests@example.invalid"],
            ["git", "-C", str(other), "config", "user.name", "Synthetic Workflow Fixture"],
        ):
            self.process(args)
            self.assertEqual(self.command_results[-1].returncode, 0, self.command_results[-1].stderr)
        (other / "README.md").write_text("# Synthetic second checkout\n", encoding="utf-8")
        for args in (
            ["git", "-C", str(other), "add", "."],
            ["git", "-C", str(other), "commit", "-m", "Synthetic second checkout"],
            ["git", "-C", str(other), "remote", "add", "origin", "../shared.git"],
        ):
            self.process(args)
            self.assertEqual(self.command_results[-1].returncode, 0, self.command_results[-1].stderr)

        def publish(root, marker):
            output = self.base / f"task-{marker}"
            receipt = json.loads(self.cli("prepare", root, "--output", output, "--state-dir", self.state).stdout)
            data = self.completed(json.loads(Path(receipt["evaluation"]).read_text(encoding="utf-8")))
            data["report"]["readme"]["rationale"] = marker
            self.cli("report", root, "--input", self.save_input(data), "--state-dir", self.state)
            return inspect_repository(root)

        left = publish(self.root, "Left checkout keeps its own latest report.")
        right = publish(other, "Right checkout keeps its own latest report.")
        self.assertEqual(left["origin"], right["origin"])
        self.assertNotEqual(left["identity"], right["identity"])
        self.assertTrue(str(left["origin"]).endswith("/shared"))
        left_report = latest_report(left, self.state)
        right_report = latest_report(right, self.state)
        self.assertEqual(left_report["report"]["readme"]["rationale"], "Left checkout keeps its own latest report.")
        self.assertEqual(right_report["report"]["readme"]["rationale"], "Right checkout keeps its own latest report.")
        self.assertNotEqual(left_report["id"], right_report["id"])


if __name__ == "__main__":
    unittest.main()
