"""`capture` runs real subprocesses; its records must be true and never touch the target."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest

from agent_readiness.report import json_text
from agent_readiness.repository import validate_evidence


CLI = Path(__file__).resolve().parents[1] / "scripts" / "readiness.py"


class CaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="readiness-capture-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "target"
        (self.root / "apps" / "web").mkdir(parents=True)
        (self.root / "README.md").write_text("# Explicitly synthetic capture fixture\n")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, timeout=20)
        self.marker = self.base / "ran"

    def run_cli(self, *arguments, ok=True):
        result = subprocess.run([sys.executable, str(CLI), *map(str, arguments)], cwd=self.base, capture_output=True, text=True, timeout=60)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertNotIn("Traceback", result.stderr)
        return result

    def capture(self, command, *options, timeout=20, ok=True):
        result = self.run_cli("capture", "--target", self.root, "--timeout", timeout, *options, "--", *command, ok=ok)
        return json.loads(result.stdout) if ok else result

    def tree(self, path):
        return {str(p.relative_to(path)): (p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(path.rglob("*")) if ".git" not in p.parts}

    def evaluation(self):
        path = self.base / "task" / "evaluation.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json_text({"schemaVersion": 1, "apps": {".": {"description": "x"}}, "report": {}, "evidence": {"readme": [], "lint_config": []}}))
        return path

    def test_success_record_is_true_and_leaves_the_target_untouched(self):
        before = self.tree(self.root)
        record = self.capture(["printf 'hello\\n'; pwd; echo \"$PYTHONDONTWRITEBYTECODE\"; echo oops >&2"])
        self.assertEqual(list(record), ["kind", "command", "cwd", "exitCode", "output", "timeoutSeconds"])
        self.assertEqual((record["kind"], record["cwd"], record["exitCode"], record["timeoutSeconds"]), ("command", ".", 0, 20))
        self.assertEqual(record["output"], f"hello\n{self.root}\n1\noops\n")
        self.assertEqual(self.tree(self.root), before)
        validate_evidence(self.root, [record])  # a record the validator accepts as-is

    def test_nonzero_exit_status_and_stderr_are_recorded_as_they_happened(self):
        record = self.capture(["echo out; echo err >&2; exit 3"])
        self.assertEqual(record["exitCode"], 3)
        self.assertEqual(record["output"], "out\nerr\n")

    def test_signal_death_uses_the_shell_convention_and_says_so(self):
        record = self.capture(["kill -9 $$"])
        self.assertEqual(record["exitCode"], 137)
        self.assertIn("terminated by signal 9", record["output"])

    def test_timeout_kills_the_process_group_and_records_124(self):
        started = time.monotonic()
        record = self.capture(["echo started; (sleep 30; touch " + str(self.marker) + ") & sleep 30"], timeout=1)
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(record["exitCode"], 124)
        self.assertEqual(record["timeoutSeconds"], 1)
        self.assertTrue(record["output"].startswith("started\n"))
        self.assertTrue(record["output"].endswith("[capture: timed out after 1s; the process group was killed]"))
        time.sleep(0.5)
        self.assertFalse(self.marker.exists(), "background child of the command must be killed too")

    def test_long_output_keeps_head_and_tail_with_an_exact_omission_marker(self):
        program = "import sys; sys.stdout.write('H' * 6000 + 'm' * 11000 + 'T' * 3000)"
        record = self.capture([sys.executable, "-c", program])
        output = record["output"]
        self.assertEqual(output, "H" * 6000 + "\n[... 11000 characters omitted by capture ...]\n" + "T" * 3000)
        exact = self.capture([sys.executable, "-c", "import sys; sys.stdout.write('a' * 9000)"])["output"]
        self.assertEqual(exact, "a" * 9000, "output that fits is never trimmed")
        silent = self.capture(["true"])
        self.assertEqual(silent["output"], "")

    def test_huge_output_is_streamed_not_buffered(self):
        record = self.capture([sys.executable, "-c", "import sys\nfor _ in range(200): sys.stdout.write('z' * 100000)"])
        self.assertIn("[... 19991000 characters omitted by capture ...]", record["output"])
        self.assertLess(len(record["output"]), 9200)

    def test_multiple_words_are_joined_with_shell_quoting_and_cwd_is_target_relative(self):
        record = self.capture(["printf", "%s|", "a b", "c;d"], "--cwd", "apps/web", "--application", "apps/web")
        self.assertEqual(record["command"], "printf '%s|' 'a b' 'c;d'")
        self.assertEqual(record["output"], "a b|c;d|")
        self.assertEqual((record["cwd"], record["application"]), ("apps/web", "apps/web"))
        absolute = self.capture(["pwd"], "--cwd", self.root / "apps")
        self.assertEqual((absolute["cwd"], absolute["output"]), ("apps", f"{self.root}/apps\n"))

    def test_cwd_outside_the_target_is_refused_before_running_anything(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.root / "link").symlink_to(outside)
        for cwd in (outside, "..", "link", "missing"):
            with self.subTest(cwd=str(cwd)):
                result = self.capture([f"touch {self.marker}"], "--cwd", cwd, ok=False)
                self.assertIn("--cwd", result.stderr)
                self.assertFalse(self.marker.exists())

    def test_append_adds_the_record_atomically_and_preserves_formatting(self):
        path = self.evaluation()
        path.chmod(0o640)
        first = self.capture(["echo one"], "--append-to", path, "--criterion", "readme")
        second = self.capture(["echo two"], "--append-to", path, "--criterion", "lint_config", "--application", ".")
        text = path.read_text()
        data = json.loads(text)
        self.assertEqual(data["evidence"]["readme"], [first])
        self.assertEqual(data["evidence"]["lint_config"], [second])
        self.assertEqual(text, json_text(data))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ["evaluation.json"], "no temporary files remain")
        again = self.run_cli("capture", "--target", self.root, "--timeout", 5, "--append-to", path, "--criterion", "readme", "--", "echo", "three")
        self.assertIn("evidence.readme (2 records)", again.stderr)
        self.assertEqual(len(json.loads(path.read_text())["evidence"]["readme"]), 2)

    def test_append_creates_a_missing_evidence_list(self):
        path = self.evaluation()
        data = json.loads(path.read_text())
        del data["evidence"]["readme"]
        path.write_text(json_text(data))
        record = self.capture(["echo hi"], "--append-to", path, "--criterion", "readme")
        self.assertEqual(json.loads(path.read_text())["evidence"]["readme"], [record])

    def test_concurrent_appends_do_not_lose_records(self):
        path = self.evaluation()
        processes = [subprocess.Popen([sys.executable, str(CLI), "capture", "--target", str(self.root), "--timeout", "20",
                                       "--append-to", str(path), "--criterion", "readme", "--", f"echo {index}"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for index in range(6)]
        self.assertEqual([process.wait(timeout=60) for process in processes], [0] * 6)
        outputs = sorted(item["output"] for item in json.loads(path.read_text())["evidence"]["readme"])
        self.assertEqual(outputs, [f"{index}\n" for index in range(6)])

    def test_unknown_criterion_is_refused_before_running_and_the_file_is_unchanged(self):
        path = self.evaluation()
        before = path.read_bytes()
        result = self.capture([f"touch {self.marker}"], "--append-to", path, "--criterion", "not_a_criterion", ok=False)
        self.assertIn("Unknown criterion: not_a_criterion", result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertEqual(path.read_bytes(), before)

    def test_append_path_inside_the_target_is_refused_even_through_a_symlink(self):
        inside = self.root / "evaluation.json"
        inside.write_text(json_text({"evidence": {"readme": []}}))
        outside_link = self.base / "link.json"
        outside_link.symlink_to(inside)
        before = self.tree(self.root)
        for path in (inside, outside_link, self.root / "new.json"):
            with self.subTest(path=path.name):
                result = self.capture([f"touch {self.marker}"], "--append-to", path, "--criterion", "readme", ok=False)
                self.assertIn("outside the repository", result.stderr)
                self.assertFalse(self.marker.exists())
        self.assertEqual(self.tree(self.root), before)

    def test_unreadable_evaluation_is_refused_before_running(self):
        path = self.base / "task.json"
        path.write_text("{not json")
        self.capture([f"touch {self.marker}"], "--append-to", path, "--criterion", "readme", ok=False)
        self.assertFalse(self.marker.exists())

    def test_argument_errors(self):
        self.run_cli("capture", "--target", self.root, "--timeout", 5, ok=False)  # no command
        self.capture(["true"], "--criterion", "readme", ok=False)  # criterion without --append-to
        self.capture(["true"], "--append-to", self.evaluation(), ok=False)  # append without criterion
        self.capture(["true"], timeout=0, ok=False)
        self.capture(["true"], timeout="nan", ok=False)
        not_git = self.base / "plain"
        not_git.mkdir()
        result = self.run_cli("capture", "--target", not_git, "--timeout", 5, "--", "true", ok=False)
        self.assertTrue(result.stderr.strip())

    def test_fractional_timeout_is_kept(self):
        self.assertEqual(self.capture(["true"], timeout=2.5)["timeoutSeconds"], 2.5)

    def test_environment_is_inherited(self):
        environment = dict(os.environ, READINESS_CAPTURE_PROBE="probe-value")
        result = subprocess.run([sys.executable, str(CLI), "capture", "--target", str(self.root), "--timeout", "5", "--", "echo $READINESS_CAPTURE_PROBE"],
                                capture_output=True, text=True, env=environment, timeout=30)
        self.assertEqual(json.loads(result.stdout)["output"], "probe-value\n")


if __name__ == "__main__":
    unittest.main()
