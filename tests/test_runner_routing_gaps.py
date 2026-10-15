"""Bounded routing regressions backed by separate offline pytest/npm probes.

These tests validate captured evidence; they do not invoke a model or assert
that arbitrary package scripts or inherited environments have been verified.
"""
import unittest

from agent_readiness.report import ReportError, _test_evidence


APPS = {".": {"description": "Single application fixture"}}


def evidence(command, output="1 test collected in 0.01s\n"):
    return {"kind": "command", "command": command, "cwd": ".",
            "exitCode": 0, "timeoutSeconds": 30, "application": ".",
            "output": output}


class RunnerRoutingGapTests(unittest.TestCase):
    def test_explicit_pytest_addopts_cannot_add_a_second_file(self):
        for prefix in (
            "PYTEST_ADDOPTS=tests/test_beta.py",
            "env PYTEST_ADDOPTS=tests/test_beta.py",
            "PYTEST_ADDOPTS='-q tests/test_beta.py'",
            "PYTEST_ADDOPTS=-q PYTEST_ADDOPTS=tests/test_beta.py",
        ):
            with self.subTest(prefix=prefix), self.assertRaises(ReportError):
                _test_evidence([evidence(prefix + " python -m pytest --collect-only tests/test_alpha.py",
                                         "2 tests collected in 0.01s\n")], 1, APPS)

    def test_path_neutral_and_last_effective_addopts_remain_valid(self):
        for prefix in (
            "PYTEST_ADDOPTS=-q",
            "env PYTEST_ADDOPTS='-q -k alpha'",
            "PYTEST_ADDOPTS=tests/test_beta.py PYTEST_ADDOPTS=-q",
            "PYTEST_ADDOPTS=''",
        ):
            with self.subTest(prefix=prefix):
                _test_evidence([evidence(prefix + " python -m pytest --collect-only tests/test_alpha.py")], 1, APPS)

    def test_sole_literal_file_may_come_from_explicit_addopts(self):
        _test_evidence([evidence("PYTEST_ADDOPTS=tests/test_alpha.py python -m pytest --collect-only")], 1, APPS)

    def test_unparseable_explicit_addopts_are_not_verified(self):
        with self.assertRaises(ReportError):
            _test_evidence([evidence("PYTEST_ADDOPTS=\"'\" python -m pytest --collect-only tests/test_alpha.py")], 1, APPS)

    def test_nested_known_package_clients_are_not_test_discovery(self):
        for command in (
            "npm exec --offline -- npm ls --list",
            "npm exec --offline -- npm exec -- npm ls --list",
            "pnpm exec npm ls --listTests",
            "npx --no-install npm ls --list",
            "yarn exec npm ls --listTests",
            "bun x npm ls --list",
        ):
            with self.subTest(command=command), self.assertRaises(ReportError):
                _test_evidence([evidence(command, "fixture@1.0.0 /repo\n└── (empty)\n")], 1, APPS)

    def test_explicit_test_runner_exec_remains_valid(self):
        for command in (
            "npm exec --offline -- jest --listTests",
            "pnpm exec vitest list",
            "npx --no-install jest --listTests",
        ):
            with self.subTest(command=command):
                _test_evidence([evidence(command, "/repo/tests/example.test.js\n")], 1, APPS)

    def test_pytest_config_file_aliases_preserve_single_file_collection(self):
        for option in ("-c empty.ini", "--config-file empty.ini", "--config-file=empty.ini"):
            with self.subTest(option=option):
                _test_evidence([evidence("python -m pytest --collect-only " + option + " tests/test_alpha.py",
                                         "<Module test_alpha.py>\n  <Function test_alpha>\n1 test collected in 0.01s\n")], 1, APPS)

    def test_explicit_other_pytest_module_or_nodeid_contradicts_single_file(self):
        for output in (
            "<Module test_alpha.py>\n<Module test_beta.py>\n2 tests collected in 0.01s\n",
            "tests/test_alpha.py::test_alpha\ntests/test_beta.py::test_beta\n2 tests collected in 0.01s\n",
        ):
            with self.subTest(output=output), self.assertRaises(ReportError):
                _test_evidence([evidence("python -m pytest --collect-only -c broad.ini tests/test_alpha.py", output)], 1, APPS)

    def test_multiple_pytest_cases_in_one_file_remain_valid(self):
        _test_evidence([evidence("python -m pytest --collect-only -q tests/test_alpha.py",
                                 "tests/test_alpha.py::test_one\ntests/test_alpha.py::test_two\n2 tests collected in 0.01s\n")], 1, APPS)

    def test_explicit_other_parent_with_same_basename_is_not_the_selected_file(self):
        with self.assertRaises(ReportError):
            _test_evidence([evidence("python -m pytest --collect-only -q --import-mode=importlib -c broad.ini tests/a/test_same.py",
                                     "tests/b/test_same.py::test_b\ntests/a/test_same.py::test_a\n2 tests collected in 0.01s\n")], 1, APPS)

    def test_matching_explicit_parent_and_normalized_dot_prefix_remain_valid(self):
        for selected in ("tests/a/test_same.py", "./tests/a/test_same.py"):
            with self.subTest(selected=selected):
                _test_evidence([evidence("python -m pytest --collect-only -q " + selected,
                                         "tests/a/test_same.py::test_a\n1 test collected in 0.01s\n")], 1, APPS)

    def test_explicit_maven_running_class_must_match_literal_selector(self):
        summary = "[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 0\n[INFO] BUILD SUCCESS\n"
        for command, actual_class in (
            ("./mvnw -Dtest=ExampleTest test", "org.example.OtherTest"),
            ("./mvnw -Dtest=org.example.ExampleTest test", "org.other.ExampleTest"),
        ):
            with self.subTest(command=command), self.assertRaises(ReportError):
                _test_evidence([evidence(command, "[INFO] Running " + actual_class + "\n" + summary)], 1, APPS)

    def test_maven_simple_or_qualified_match_and_absent_class_output_remain_valid(self):
        summary = "[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 0\n[INFO] BUILD SUCCESS\n"
        for selector, output in (
            ("ExampleTest", "[INFO] Running org.example.ExampleTest\n" + summary),
            ("org.example.ExampleTest", "[INFO] Running org.example.ExampleTest\n" + summary),
            ("org.example.ExampleTest", "[INFO] Running ExampleTest\n" + summary),
            ("ExampleTest", summary),
            ("ExampleTest", ("[INFO] Running org.example.ExampleTest\n" + summary) * 2),
        ):
            with self.subTest(selector=selector, output=output):
                _test_evidence([evidence("./mvnw -Dtest=" + selector + " test", output)], 1, APPS)

    def test_gradle_additional_test_task_output_is_not_the_selected_task(self):
        with self.assertRaises(ReportError):
            _test_evidence([evidence("./gradlew --rerun-tasks test --tests ExampleTest integrationTest",
                                     "> Task :test\n> Task :integrationTest\nBUILD SUCCESSFUL\n")], 1, APPS)

    def test_gradle_compile_and_classes_tasks_do_not_contradict_one_test_task(self):
        _test_evidence([evidence("./gradlew --rerun-tasks :app:test --tests ExampleTest",
                                 "> Task :app:compileTestJava\n> Task :app:testClasses\n> Task :app:test\nBUILD SUCCESSFUL\n")], 1, APPS)

    def test_direct_non_js_cwd_cannot_be_relabelled_as_another_application(self):
        apps = {"apps/api": {"description": "API app"}, "apps/web": {"description": "Web app"}}
        for command, output in (
            ("python -m pytest --collect-only tests/test_example.py", "1 test collected in 0.01s\n"),
            ("./mvnw -Dtest=ExampleTest test", "[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 0\n"),
            ("go test -run ^TestExample$ -count=1", "ok example/api 0.01s\n"),
        ):
            item = {**evidence(command, output), "cwd": "apps/api", "application": "apps/web"}
            with self.subTest(command=command), self.assertRaises(ReportError):
                _test_evidence([item], 1, apps)

    def test_direct_non_js_cwd_may_claim_its_actual_application(self):
        apps = {"apps/api": {"description": "API app"}, "apps/web": {"description": "Web app"}}
        for command, output in (
            ("python -m pytest --collect-only tests/test_example.py", "1 test collected in 0.01s\n"),
            ("./mvnw -Dtest=ExampleTest test", "[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 0\n"),
            ("go test -run ^TestExample$ -count=1", "ok example/api 0.01s\n"),
        ):
            item = {**evidence(command, output), "cwd": "apps/api", "application": "apps/api"}
            with self.subTest(command=command):
                _test_evidence([item], 1, apps)

    def test_non_js_explicit_selector_from_repository_root_remains_supported(self):
        apps = {"apps/api": {"description": "API app"}, "apps/web": {"description": "Web app"}}
        for command, output in (
            ("python -m pytest --collect-only apps/web/tests/test_example.py", "1 test collected in 0.01s\n"),
            ("./mvnw -f apps/web/pom.xml -Dtest=ExampleTest test", "[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 0\n"),
            ("go test ./apps/web/... -run ^TestExample$ -count=1", "ok example/web 0.01s\n"),
        ):
            with self.subTest(command=command):
                _test_evidence([{**evidence(command, output), "application": "apps/web"}], 1, apps)

    def test_root_pytest_explicit_file_owner_cannot_be_relabelled(self):
        apps = {"apps/api": {"description": "API app"}, "apps/web": {"description": "Web app"}}
        item = {**evidence("python -m pytest --collect-only apps/api/tests/test_api.py"),
                "application": "apps/web"}
        with self.assertRaises(ReportError):
            _test_evidence([item], 1, apps)


if __name__ == "__main__":
    unittest.main()
