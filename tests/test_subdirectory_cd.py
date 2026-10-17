"""Captured-record safety tests; no model or application runner is executed."""
from copy import deepcopy
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import unittest

from agent_readiness.report import ReportError, _runner_context, _test_evidence
try:
    from tests import test_empty_command_output as fixture
except ImportError:
    import test_empty_command_output as fixture


class SubdirectoryCdTests(unittest.TestCase):
    setUp = fixture.EmptyCommandOutputTests.setUp
    cli = fixture.EmptyCommandOutputTests.cli

    def apps(self):
        for name in ("server", "web"):
            (self.root / "apps" / name).mkdir(parents=True, exist_ok=True)
            tests = self.root / "apps" / name / "tests"
            tests.mkdir(exist_ok=True)
            (tests / "test_one.py").write_text("def test_one(): pass\n", encoding="utf-8")
        return {"apps/server": {"description": "Synthetic server app"},
                "apps/web": {"description": "Synthetic web app"}}

    def captured(self, command, *, cwd=".", application=None):
        row = {"kind": "command", "command": command, "cwd": cwd, "exitCode": 0,
               "output": str(self.root / "apps/server/example.test.ts") + "\n", "timeoutSeconds": 120}
        if application is not None:
            row["application"] = application
        return row

    def reject(self, row, apps):
        with self.assertRaises(ReportError):
            _test_evidence([row], 1, apps, self.root)

    def test_literal_absolute_relative_and_initial_subdirectory_are_preserved(self):
        apps = self.apps()
        commands = [("cd ./apps/server && npm test -- --listTests", "."),
                    ("cd -- " + shlex.quote(str(self.root / "apps/server")) + " && yarn test --listTests", "."),
                    ("cd ./server && npm test -- --listTests", "apps")]
        for command, cwd in commands:
            with self.subTest(command=command):
                row = self.captured(command, cwd=cwd)
                before = deepcopy(row)
                _test_evidence([row], 1, apps, self.root)
                self.assertEqual(_runner_context(command, cwd, self.root, check_paths=True)[1], Path("apps/server"))
                self.assertEqual(row, before)

    def test_single_root_app_can_run_in_child_but_one_app_cannot_count_twice(self):
        apps = self.apps()
        row = self.captured("cd ./apps/server && npm test -- --listTests")
        _test_evidence([row], 1, {".": {"description": "Synthetic single root app"}}, self.root)
        with self.assertRaises(ReportError):
            _test_evidence([row, dict(row, application="apps/server")], 2, apps, self.root)

    def test_explicit_application_cannot_relabel_another_runner_directory(self):
        apps = self.apps()
        row = self.captured("cd ./apps/server && npm test -- --listTests", application="apps/web")
        self.reject(row, apps)
        self.reject(dict(row, application="."), {**apps, ".": {"description": "Root app"}})
        (self.root / "shared").mkdir()
        self.reject(self.captured("cd ./shared && npm test -- --listTests", application="apps/server"), apps)

    def test_unsafe_paths_shell_forms_and_nonexistent_destination_are_rejected(self):
        apps = self.apps()
        prefixes = ["cd /tmp", "cd -", "cd -P apps/server", "cd apps/server", "cd ../outside", "cd ./apps/server/../web", "cd .git", "cd missing",
                    "cd $PWD/apps/server", "cd ~/apps/server", 'cd "$(pwd)/apps/server"', "cd ./apps/*"]
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                self.reject(self.captured(prefix + " && npm test -- --listTests"), apps)
        for suffix in ("; echo done", " | head -2", " > result.txt", " && echo done", " && cd ."):
            self.reject(self.captured("cd ./apps/server && npm test -- --listTests" + suffix), apps)
        for runner in ("! npm test -- --listTests", "bash -c 'npm test -- --listTests'", "env -C ../web npm test -- --listTests"):
            self.reject(self.captured("cd ./apps/server && " + runner), apps)

    def test_current_initial_and_destination_symlink_segments_are_rejected(self):
        apps = self.apps()
        outside = self.base / "outside"
        outside.mkdir()
        (self.root / "apps/link-out").symlink_to(outside, target_is_directory=True)
        (self.root / "apps/link-in").symlink_to(self.root / "apps/server", target_is_directory=True)
        for name in ("link-out", "link-in"):
            self.reject(self.captured(f"cd ./apps/{name} && npm test -- --listTests"), apps)
            command = "cd " + shlex.quote(str(self.root / "apps/server")) + " && npm test -- --listTests"
            self.reject(self.captured(command, cwd="apps/" + name), apps)

    def test_known_runner_scope_switches_and_cross_app_paths_are_rejected(self):
        apps = self.apps()
        for runner in ("npm --prefix ../web test -- --listTests", "npm --workspace=web test -- --listTests",
                       "pnpm -C../web test -- --listTests", "pnpm --filter web test -- --listTests",
                       "yarn workspace web test --listTests", "yarn --cwd ../web test --listTests",
                       "npm test -- --listTests ../web", "npm test -- --listTests " + str(self.root / "apps/web/test.ts")):
            with self.subTest(runner=runner):
                self.reject(self.captured("cd ./apps/server && " + runner), apps)
        python = self.captured("cd ./apps/server && env PYTHONDONTWRITEBYTECODE=1 python -m pytest --collect-only -p no:cacheprovider tests/test_one.py")
        python["output"] = "collected 1 item\n1 test collected in 0.01s\n"
        _test_evidence([python], 1, apps, self.root)

    def test_outer_environment_cannot_redirect_a_changed_directory_runner(self):
        apps = self.apps()
        for prefix in ("env npm_config_prefix=/other", "npm_config_prefix=/other",
                       "env NODE_OPTIONS=--require=/other/preload.cjs", "PYTHONPATH=/other",
                       "PWD=/other", "CDPATH=/other", "env PYTHONDONTWRITEBYTECODE=0"):
            with self.subTest(prefix=prefix):
                self.reject(self.captured("cd ./apps/server && " + prefix + " npm test -- --listTests"), apps)

    def test_compact_java_and_python_directory_selectors_are_rejected(self):
        apps = self.apps()
        for runner in ("mvn -f../web/pom.xml -Dtest=One test", "mvn -pl../web -Dtest=One test",
                       "gradle -p../web test --tests One", "gradle -b../web/build.gradle test --tests One",
                       "gradle -c../web/settings.gradle test --tests One"):
            with self.subTest(runner=runner):
                row = self.captured("cd ./apps/server && " + runner)
                row["output"] = "Tests run: 1, Failures: 0, Errors: 0, Skipped: 0\nBUILD SUCCESS\n1 test completed\n"
                self.reject(row, apps)
        row = self.captured("cd ./apps/server && python -m pytest -c../web/pytest.ini --collect-only")
        row["output"] = "collected 1 item\n1 test collected in 0.01s\n"
        self.reject(row, apps)

    def test_maven_fail_controls_do_not_become_compact_file_selectors(self):
        apps = self.apps()
        for flag in ("-ff", "-fae", "-fn"):
            row = self.captured("cd ./apps/server && mvn " + flag + " -Dtest=One test")
            row["output"] = "Tests run: 1, Failures: 0, Errors: 0, Skipped: 0\nBUILD SUCCESS\n"
            _test_evidence([row], 1, apps, self.root)

    def test_current_report_and_history_after_original_root_deleted_keep_exact_evidence(self):
        apps = self.apps()
        subprocess.run(["git", "-C", str(self.root), "remote", "add", "origin", "https://example.invalid/synthetic-subdir"], check=True)
        item = self.captured("cd -- " + shlex.quote(str(self.root / "apps/server")) + " && npm test -- --listTests")
        task = self.base / "task"
        prepared = self.cli("prepare", self.root, "--output", task, "--state-dir", self.state)
        evaluation = json.loads(Path(prepared["evaluation"]).read_text())
        evaluation["apps"] = apps
        for criterion in self.cli("catalog")["criteria"]:
            key = criterion["id"]
            evaluation["report"][key] = {"numerator": 0, "denominator": len(apps) if criterion["scope"] == "application" else 1,
                                          "rationale": "Synthetic validation judgment; not a model assessment."}
            evaluation["evidence"][key] = [{"kind": "file", "path": "README.md", "line": 1, "note": "Synthetic fixture."}]
        evaluation["report"]["unit_tests_runnable"]["numerator"] = 1
        evaluation["evidence"]["unit_tests_runnable"] = [item]
        source = self.base / "evaluation.json"
        source.write_text(json.dumps(evaluation))
        self.cli("report", self.root, "--input", source, "--state-dir", self.state)
        before = self.cli("show", self.root, "--state-dir", self.state, "--json")
        moved = self.base / "relocated"
        shutil.copytree(self.root, moved)
        shutil.rmtree(self.root)
        after = self.cli("show", moved, "--state-dir", self.state, "--json")
        self.assertEqual(before["evidence"]["unit_tests_runnable"], [item])
        self.assertEqual(after["evidence"]["unit_tests_runnable"], [item])
        self.assertEqual(after["repository"]["root"], str(self.root))
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
