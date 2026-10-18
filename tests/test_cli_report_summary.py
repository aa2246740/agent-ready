"""`report` prints a short summary by default and the full receipt with --json."""

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from agent_readiness.scoring import score_report


CLI = Path(__file__).resolve().parents[1] / "scripts" / "readiness.py"


class ReportSummaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="readiness-summary-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "target"
        self.root.mkdir()
        self.state = self.base / "state"
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, timeout=20)
        (self.root / "README.md").write_text("# Explicitly synthetic summary fixture\n")
        self.counter = 0

    def run_cli(self, *arguments):
        return subprocess.run([sys.executable, str(CLI), *map(str, arguments)], cwd=self.base, capture_output=True, text=True, timeout=60)

    def evaluation(self, passed=("readme",), skipped=("backlog_health",)):
        self.counter += 1
        task = self.base / f"task-{self.counter}"
        receipt = json.loads(self.run_cli("prepare", self.root, "--output", task, "--state-dir", self.state).stdout)
        data = json.loads(Path(receipt["evaluation"]).read_text())
        data["apps"] = {".": {"description": "Synthetic schema fixture"}}
        for key, value in data["report"].items():
            data["report"][key] = {"numerator": 1 if key in passed else None if key in skipped else 0, "denominator": 1,
                                   "rationale": "Synthetic judgment for output formatting; not a real audit."}
            data["evidence"][key] = [{"kind": "file", "path": "README.md", "line": 1, "note": "Identifies the synthetic fixture."}]
        path = task / "evaluation-filled.json"
        path.write_text(json.dumps(data))
        return path

    def report(self, path, *options):
        return self.run_cli("report", self.root, "--input", path, "--state-dir", self.state, *options)

    def test_default_output_is_a_short_summary_with_counts_and_paths(self):
        result = self.report(self.evaluation())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        self.assertLessEqual(len(lines), 8)
        self.assertLess(len(result.stdout), 1200)
        with self.assertRaises(ValueError):
            json.loads(result.stdout)
        self.assertRegex(lines[0], r"^Level 1 - \d+\.\d%$")
        self.assertIn("Criteria: 1 passed, 82 failed, 1 skipped (of 84)", lines)
        self.assertIn("Applications (1): .", lines)
        json_path = Path(re.search(r"^JSON: (.+)$", result.stdout, re.M).group(1))
        markdown_path = Path(re.search(r"^Markdown: (.+)$", result.stdout, re.M).group(1))
        self.assertTrue(json_path.is_file() and markdown_path.is_file())
        self.assertTrue(json_path.is_relative_to(self.state) and markdown_path.is_relative_to(self.state))
        stored = json.loads(json_path.read_text())
        self.assertAlmostEqual(float(lines[0].split(" - ")[1].rstrip("%")), stored["summary"]["scorePercent"], places=1)
        self.assertIn("--json", result.stdout)

    def test_json_flag_prints_the_full_receipt_with_the_same_stored_files(self):
        path = self.evaluation()
        result = self.report(path, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(set(receipt), {"report", "markdown", "summary"})
        self.assertEqual(len(receipt["summary"]["criterionResults"]), 84)
        stored = json.loads(Path(receipt["report"]).read_text())
        self.assertEqual(receipt["summary"], score_report(stored["report"]))
        self.assertTrue(Path(receipt["markdown"]).is_file())

    def test_second_report_summary_mentions_changes_since_the_previous_one(self):
        self.report(self.evaluation())
        result = self.report(self.evaluation(passed=("readme", "lint_config")))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Criteria: 2 passed, 81 failed, 1 skipped (of 84)", result.stdout)
        self.assertIn("Changed since previous report: 1 criteria, 0 applications", result.stdout)

    def test_rejection_keeps_exit_code_two_empty_stdout_and_the_problem_list(self):
        path = self.evaluation()
        data = json.loads(path.read_text())
        data["report"]["lint_config"]["numerator"] = None  # not skippable
        path.write_text(json.dumps(data))
        for options in ((), ("--json",)):
            result = self.report(path, *options)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("lint_config", result.stderr)
            self.assertIn("fix all and re-run report", result.stderr)
        self.assertFalse(self.state.exists() and any(self.state.rglob("*.json")))


if __name__ == "__main__":
    unittest.main()
