"""Wrapper options after `run` and value-taking Go flags must not hide the real command.

Synthetic command records; the Go cases also run the real toolchain when one is
available so the flag table is checked against actual `go test` behavior.
"""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

from agent_readiness.report import ReportError, _go_packages, _test_evidence, _unwrap_wrapper

COLLECTED = "tests/test_a.py::test_one\n\n1 test collected in 0.01s\n"


class Fixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="readiness-wrapper-go-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "repo"
        (self.root / "apps" / "web").mkdir(parents=True)
        (self.root / "apps" / "api").mkdir(parents=True)
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_a.py").write_text("def test_one():\n    assert 1\n")
        self.single = {".": {"description": "Synthetic single application"}}
        self.multi = {"apps/web": {"description": "Synthetic web"}, "apps/api": {"description": "Synthetic api"}}

    def evidence(self, command, output, application=None):
        item = {"kind": "command", "command": command, "cwd": ".", "exitCode": 0, "output": output, "timeoutSeconds": 30}
        if application is not None:
            item["application"] = application
        return item

    def accepted(self, command, output, apps=None, application=None):
        _test_evidence([self.evidence(command, output, application)], 1, apps or self.single, self.root)

    def rejected(self, command, output, apps=None, application=None):
        with self.assertRaises(ReportError, msg=command):
            _test_evidence([self.evidence(command, output, application)], 1, apps or self.single, self.root)


class WrapperRunOptionTests(Fixture):
    def test_wrapper_options_after_run_are_skipped(self):
        for command in (
            "uv run --frozen pytest --collect-only tests/test_a.py",
            "uv run --no-sync pytest --collect-only tests/test_a.py",
            "uv run --locked --no-dev pytest --collect-only tests/test_a.py",
            "uv run --with pytest-xdist pytest --collect-only tests/test_a.py",
            "uv run --with=pytest-xdist pytest --collect-only tests/test_a.py",
            "uv run --with pytest-xdist --with pytest-cov --frozen pytest --collect-only tests/test_a.py",
            "uv run --python 3.12 pytest --collect-only tests/test_a.py",
            "uv run -p 3.12 pytest --collect-only tests/test_a.py",
            "uv run --extra dev --group test --package web pytest --collect-only tests/test_a.py",
            "uv run --with-requirements requirements-dev.txt pytest --collect-only tests/test_a.py",
            "uv run --index-url https://pypi.example/simple --find-links wheels pytest --collect-only tests/test_a.py",
            "uv run --env-file .env pytest --collect-only tests/test_a.py",
            "uv run --frozen -- pytest --collect-only tests/test_a.py",
            "uv run --frozen python -m pytest --collect-only tests/test_a.py",
            "uv run --frozen -m pytest --collect-only tests/test_a.py",
            "poetry run pytest --collect-only tests/test_a.py",
            "poetry run -q pytest --collect-only tests/test_a.py",
            "pdm run pytest --collect-only tests/test_a.py",
            "pdm run -s pytest --collect-only tests/test_a.py",
            "hatch run pytest --collect-only tests/test_a.py",
            "hatch run test:pytest --collect-only tests/test_a.py",
            "hatch run -e test pytest --collect-only tests/test_a.py",
            "pipenv run pytest --collect-only tests/test_a.py",
        ):
            with self.subTest(command=command):
                self.accepted(command, COLLECTED)

    def test_unwrapped_command_gets_the_direct_command_checks(self):
        for command in (
            "uv run --frozen pytest tests",                                  # not a single file
            "uv run --frozen pytest --collect-only tests/missing.py",         # file does not exist
            "uv run --frozen pytest --collect-only -c ../x.ini tests/test_a.py",
            "uv run --frozen git log --grep test",                            # not a test runner
            "uv run --frozen sh -c 'pytest --collect-only tests/test_a.py'",
        ):
            with self.subTest(command=command):
                self.rejected(command, COLLECTED)
        self.rejected("uv run --frozen pytest --collect-only tests/test_a.py", "1 passed in 0.01s\n")
        self.rejected("uv run --frozen pytest --collect-only tests/test_a.py", "collected 0 items\n")

    def test_directory_retargeting_still_rejected(self):
        for command in (
            "uv --project apps/web run pytest --collect-only tests/test_a.py",
            "uv --directory apps/web run pytest --collect-only tests/test_a.py",
            "uv run --project apps/web pytest --collect-only tests/test_a.py",
            "uv run --project=apps/web pytest --collect-only tests/test_a.py",
            "uv run --directory apps/web pytest --collect-only tests/test_a.py",
            "uv run --directory=apps/web pytest --collect-only tests/test_a.py",
            "poetry run --directory apps/web pytest --collect-only tests/test_a.py",
            "poetry --directory apps/web run pytest --collect-only tests/test_a.py",
            "pdm run --project apps/web pytest --collect-only tests/test_a.py",
        ):
            with self.subTest(command=command):
                self.rejected(command, COLLECTED)

    def test_option_values_cannot_leave_the_repository(self):
        for command in (
            "uv run --with-requirements ../requirements.txt pytest --collect-only tests/test_a.py",
            "uv run --env-file /etc/hosts pytest --collect-only tests/test_a.py",
            "uv run --python",  # option missing its value
        ):
            with self.subTest(command=command):
                self.rejected(command, COLLECTED)
        self.accepted(f"uv run --env-file {self.root}/.env pytest --collect-only tests/test_a.py", COLLECTED)

    def test_unwrap_returns_inner_runner(self):
        self.assertEqual(_unwrap_wrapper(["uv", "run", "--frozen", "--with", "x", "pytest", "-q"]), ["pytest", "-q"])
        self.assertEqual(_unwrap_wrapper(["uv", "run", "-m", "pytest", "t.py"]), ["python", "-m", "pytest", "t.py"])
        self.assertEqual(_unwrap_wrapper(["hatch", "run", "test:pytest"]), ["pytest"])
        self.assertEqual(_unwrap_wrapper(["pytest", "--frozen"]), ["pytest", "--frozen"])
        self.assertIsNone(_unwrap_wrapper(["uv", "run", "--frozen"]))
        self.assertIsNone(_unwrap_wrapper(["uv", "sync"]))
        self.assertIsNone(_unwrap_wrapper(["uv", "run", "--project", "apps/web", "pytest"]))


class GoFlagTests(Fixture):
    OK_WEB = "ok  \texample.com/apps/web\t0.012s\n"

    def test_value_taking_build_flags_do_not_become_packages(self):
        for command in (
            "go test -tags integration ./apps/web/...",
            "go test --tags integration ./apps/web/...",
            "go test -tags=integration ./apps/web/...",
            "go test -tags 'integration e2e' ./apps/web/...",
            "go test -ldflags '-X main.version=1' ./apps/web/...",
            "go test -gcflags all=-N ./apps/web/...",
            "go test -asmflags -trimpath ./apps/web/...",
            "go test -mod vendor ./apps/web/...",
            "go test -modfile alt.mod ./apps/web/...",
            "go test -pkgdir pkg ./apps/web/...",
            "go test -toolexec time ./apps/web/...",
            "go test -overlay overlay.json ./apps/web/...",
            "go test -pgo auto ./apps/web/...",
            "go test -compiler gc -installsuffix race -buildmode default ./apps/web/...",
            "go test -covermode atomic -coverpkg ./apps/web/... ./apps/web/...",
            "go test -race -v -short -cover -json -failfast -count=1 ./apps/web/...",
            "go test -buildvcs=false ./apps/web/...",
            "go test -test.run TestX ./apps/web/...",
            "go test ./apps/web/... -args -test.v",
            "go test ./apps/web/... -tags integration -run TestX",
        ):
            with self.subTest(command=command):
                self.accepted(command, self.OK_WEB, self.multi, "apps/web")

    def test_ownership_is_still_enforced_with_value_flags(self):
        for command in (
            "go test -tags integration ./apps/api/...",
            "go test -tags integration ./...",
            "go test -tags integration",
            "go test -tags integration ./apps/web/... ./apps/api/...",
            "go test -mod vendor ./apps/api/...",
        ):
            with self.subTest(command=command):
                self.rejected(command, self.OK_WEB, self.multi, "apps/web")

    def test_boolean_flags_take_no_value_and_args_end_package_parsing(self):
        self.assertEqual(_go_packages(["go", "test", "-race", "-v", "./apps/web"]), [Path("apps/web")])
        self.assertEqual(_go_packages(["go", "test", "-tags", "x", "./apps/web"]), [Path("apps/web")])
        self.assertEqual(_go_packages(["go", "test", "-buildvcs", "false"]), [Path("false")])  # go treats it as a package too
        self.assertEqual(_go_packages(["go", "test", "./apps/web", "-args", "../api", "--flag"]), [Path("apps/web")])

    @unittest.skipUnless(os.environ.get("GO_TEST_BINARY") or shutil.which("go"), "go is required")
    def test_real_go_accepts_the_flag_forms_the_table_assumes(self):
        go = os.environ.get("GO_TEST_BINARY") or shutil.which("go")
        web = self.root / "apps" / "web"
        (web / "go.mod").write_text("module example.invalid/web\n\ngo 1.18\n")
        (web / "web_test.go").write_text('//go:build integration\n\npackage web\n\nimport "testing"\n\nfunc TestWeb(t *testing.T) {}\n')
        cache = tempfile.TemporaryDirectory(prefix="readiness-go-flags-cache-")
        self.addCleanup(cache.cleanup)
        env = dict(os.environ, GOCACHE=cache.name, GOTOOLCHAIN="local", GOPROXY="off", GOSUMDB="off", GOFLAGS="")
        command = shlex.join([go, "test", "-count=1", "-tags", "integration", "-run", "^TestWeb$", "-ldflags", "-s", "./..."])
        result = subprocess.run(["bash", "-c", command], cwd=web, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"^ok\s+example\.invalid/web")
        item = {"kind": "command", "command": command, "cwd": "apps/web", "exitCode": 0,
                "output": result.stdout + result.stderr, "timeoutSeconds": 120}
        _test_evidence([item], 1, self.multi, self.root)


if __name__ == "__main__":
    unittest.main()
