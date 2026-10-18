"""`cd` may spell the snapshot root through a symlinked ancestor (macOS /tmp), nothing else.

Mirrors /private/tmp/<repo> versus /tmp/<repo> with a symlink created inside the
test's own temporary directory, so the result does not depend on the host OS.
"""

from pathlib import Path
import tempfile
import unittest

from agent_readiness.report import ReportError, _runner_context, _test_evidence

RUN = " && pytest --collect-only tests/test_x.py"


class RunnerSymlinkAliasTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="readiness-symlink-alias-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "real" / "repo"            # recorded, fully resolved root
        for app in ("web", "api"):
            (self.root / "apps" / app / "tests").mkdir(parents=True)
            (self.root / "apps" / app / "tests" / "test_x.py").write_text("def test_x():\n    assert 1\n")
        (self.base / "alias").symlink_to(self.base / "real", target_is_directory=True)  # like /tmp -> /private/tmp
        (self.base / "outside").mkdir()
        (self.base / "outside" / "link").symlink_to(self.root / "apps" / "web", target_is_directory=True)
        (self.base / "outside" / "root-link").symlink_to(self.root, target_is_directory=True)

    def context(self, destination, *, check_paths=True, root=None):
        return _runner_context(f"cd {destination}{RUN}", ".", root or self.root, check_paths=check_paths)

    def test_symlinked_ancestor_of_the_root_is_accepted(self):
        for check_paths in (True, False):
            with self.subTest(check_paths=check_paths):
                words, runner_cwd = self.context(self.base / "alias" / "repo" / "apps" / "web", check_paths=check_paths)
                self.assertEqual(words[0], "pytest")
                self.assertEqual(runner_cwd, Path("apps/web"))
        words, runner_cwd = self.context(self.base / "alias" / "repo")
        self.assertEqual((words[0], runner_cwd), ("pytest", Path(".")))

    def test_root_recorded_through_the_alias_accepts_the_resolved_spelling(self):
        aliased_root = self.base / "alias" / "repo"
        words, runner_cwd = self.context(self.root / "apps" / "api", root=aliased_root)
        self.assertEqual((words[0], runner_cwd), ("pytest", Path("apps/api")))

    def test_symlink_outside_the_repository_pointing_into_it_is_rejected(self):
        for destination in (self.base / "outside" / "link", self.base / "outside" / "root-link",
                            self.base / "outside" / "root-link" / "apps" / "web"):
            for check_paths in (True, False):
                with self.subTest(destination=destination.name, check_paths=check_paths):
                    self.assertEqual(self.context(destination, check_paths=check_paths), ([], None))

    def test_symlink_below_the_root_is_still_rejected_through_the_alias(self):
        (self.root / "apps" / "shortcut").symlink_to(self.root / "apps" / "api", target_is_directory=True)
        self.assertEqual(self.context(self.base / "alias" / "repo" / "apps" / "shortcut"), ([], None))
        self.assertEqual(self.context(self.root / "apps" / "shortcut"), ([], None))

    def test_unrelated_absolute_directories_stay_rejected(self):
        for destination in (self.base, self.base / "real", self.base / "alias" / "other" / "apps" / "web"):
            with self.subTest(destination=str(destination)):
                self.assertEqual(self.context(destination), ([], None))

    def test_evidence_with_outside_symlink_is_not_accepted_as_a_test_run(self):
        apps = {"apps/web": {"description": "Synthetic web"}, "apps/api": {"description": "Synthetic api"}}

        def row(destination):
            return {"kind": "command", "command": f"cd {destination}{RUN}", "cwd": ".", "exitCode": 0,
                    "output": "tests/test_x.py::test_x\n\n1 test collected in 0.01s\n", "timeoutSeconds": 30,
                    "application": "apps/web"}

        _test_evidence([row(self.base / "alias" / "repo" / "apps" / "web")], 1, apps, self.root)
        with self.assertRaises(ReportError):
            _test_evidence([row(self.base / "outside" / "link")], 1, apps, self.root)


if __name__ == "__main__":
    unittest.main()
