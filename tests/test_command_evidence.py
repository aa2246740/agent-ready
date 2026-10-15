"""Captured-record unit tests for the native runnable-test evidence contract.

These fixtures are synthetic command/output records. No test runner command in
this file is executed, and these tests do not establish runner integration.
"""

import unittest

from agent_readiness.report import ReportError, _test_evidence


SINGLE_APP = {".": {"description": "Fixture application"}}


def captured(command, output, *, cwd=".", exit_code=0, timeout=30, application=None):
    item = {
        "kind": "command",
        "command": command,
        "cwd": cwd,
        "exitCode": exit_code,
        "output": output,
        "timeoutSeconds": timeout,
    }
    if application is not None:
        item["application"] = application
    return item


class CommandEvidenceTests(unittest.TestCase):
    def assert_rejected(self, record):
        with self.assertRaises(ReportError):
            _test_evidence([record], 1, SINGLE_APP)

    def test_native_negative_runner_examples(self):
        examples = [
            ("Gradle dry run", "./gradlew test --tests ExampleTest --dry-run", ":test SKIPPED\nBUILD SUCCESSFUL"),
            ("Maven explicit skip", "./mvnw -Dtest=ExampleTest -DskipTests test", "Tests are skipped.\nBUILD SUCCESS"),
            ("Maven skip compile and run", "./mvnw -Dtest=ExampleTest -Dmaven.test.skip=true test", "Not compiling test sources\nBUILD SUCCESS"),
            ("Maven wrong Gradle flag", "./mvnw --rerun-tasks -Dtest=ExampleTest test", "BUILD SUCCESS"),
            ("Java task listing", "./gradlew tasks", "Verification tasks\ntest - Runs the unit tests"),
            ("Java compile only", "./mvnw test-compile", "BUILD SUCCESS"),
            ("JS executed suite", "npm test", "PASS tests/example.test.js\nTest Suites: 30 passed\nTests: 350 passed"),
            ("Python unittest execution", "python -m unittest discover -s tests", "Ran 10 tests in 0.05s\nOK"),
            ("Python pytest execution", "python -m pytest tests/test_example.py", "1 passed in 0.01s"),
            ("Python unbounded collection", "python -m pytest --collect-only", "collected 350 items"),
            ("Arbitrary echo", "echo ok", "ok"),
        ]
        for name, command, output in examples:
            with self.subTest(name=name):
                self.assert_rejected(captured(command, output))

    def test_gradle_nonexecuted_test_tasks_are_not_success(self):
        for marker in ("UP-TO-DATE", "FROM-CACHE", "SKIPPED", "NO-SOURCE"):
            with self.subTest(marker=marker):
                self.assert_rejected(captured(
                    "./gradlew --rerun-tasks :server:test --tests ExampleTest",
                    f"> Task :server:test {marker}\nBUILD SUCCESSFUL in 1s",
                ))

    def test_native_positive_collection_modes(self):
        examples = [
            ("Jest", "npm test -- --listTests", "/repo/tests/example.test.js\n"),
            ("Vitest", "npx --no-install vitest list", "src/example.test.ts > Example > produces expected output\n"),
            ("Python", "python -m pytest --collect-only tests/test_example.py", "collected 1 item\n<Module tests/test_example.py>\n<Function test_example>\n1 test collected in 0.01s"),
        ]
        for name, command, output in examples:
            with self.subTest(name=name):
                _test_evidence([captured(command, output)], 1, SINGLE_APP)

    def test_listing_filenames_with_pass_or_fail_is_not_test_execution(self):
        _test_evidence([captured(
            "npm test -- --listTests",
            "/repo/tests/pass.test.js\n/repo/tests/fail.test.js\n",
        )], 1, SINGLE_APP)

    def test_native_positive_selected_java_execution(self):
        examples = [
            ("Gradle", "./gradlew --rerun-tasks test --tests ExampleTest", "> Task :compileJava\n> Task :test\nBUILD SUCCESSFUL in 1s\n3 actionable tasks: 3 executed"),
            ("Gradle module", "./gradlew --rerun-tasks :server:test --tests ExampleTest", "> Task :server:test\nBUILD SUCCESSFUL in 1s\n1 actionable task: 1 executed"),
            ("Maven", "./mvnw -Dtest=ExampleTest test", "[INFO] Running ExampleTest\n[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 0\n[INFO] BUILD SUCCESS"),
            ("Maven module", "./mvnw -f server/pom.xml -Dtest=ExampleTest test", "[INFO] Running ExampleTest\n[INFO] Tests run: 2, Failures: 0, Errors: 0, Skipped: 0\n[INFO] BUILD SUCCESS"),
        ]
        for name, command, output in examples:
            with self.subTest(name=name):
                _test_evidence([captured(command, output)], 1, SINGLE_APP)

    def test_java_success_banner_without_test_execution_is_not_enough(self):
        examples = [
            ("Gradle no task evidence", "./gradlew --rerun-tasks test --tests ExampleTest", "BUILD SUCCESSFUL in 1s"),
            ("Maven no execution evidence", "./mvnw -Dtest=ExampleTest test", "[INFO] BUILD SUCCESS"),
            ("Maven skips from build configuration", "./mvnw -Dtest=ExampleTest test", "[INFO] Tests are skipped.\n[INFO] BUILD SUCCESS"),
            ("Maven reports run but skipped", "./mvnw -Dtest=ExampleTest test", "[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 1\n[INFO] Tests were skipped\n[INFO] BUILD SUCCESS"),
            ("Maven all skipped summary", "./mvnw -Dtest=ExampleTest test", "[INFO] Tests run: 3, Failures: 0, Errors: 0, Skipped: 3\n[INFO] BUILD SUCCESS"),
        ]
        for name, command, output in examples:
            with self.subTest(name=name):
                self.assert_rejected(captured(command, output))

    def test_python_collection_requires_collected_tests_and_no_execution(self):
        command = "python -m pytest --collect-only tests/test_example.py"
        for output in ("Everything is fine", "pytest 8.4.2", "collected 1 item\n1 passed in 0.01s"):
            with self.subTest(output=output):
                self.assert_rejected(captured(command, output))

    def test_collection_supports_readonly_environment_assignments(self):
        for prefix in ("PYTHONDONTWRITEBYTECODE=1 ", "env PYTHONDONTWRITEBYTECODE=1 "):
            with self.subTest(prefix=prefix):
                _test_evidence([captured(
                    prefix + "python -m pytest --collect-only -p no:cacheprovider tests/test_example.py",
                    "collected 1 item\n1 test collected in 0.01s",
                )], 1, SINGLE_APP)

    def test_runner_exit_status_is_required(self):
        self.assert_rejected(captured(
            "python -m pytest --collect-only tests/test_example.py",
            "ERROR collecting tests/test_example.py\nModuleNotFoundError: No module named 'missing_dependency'",
            exit_code=2,
        ))

    def test_zero_collected_or_executed_tests_are_not_success(self):
        examples = [
            ("python -m pytest --collect-only tests/test_example.py", "collected 0 items"),
            ("npm test -- --listTests", "No tests found"),
            ("./mvnw -Dtest=ExampleTest test", "Tests run: 0, Failures: 0, Errors: 0, Skipped: 0\nBUILD SUCCESS"),
        ]
        for command, output in examples:
            with self.subTest(command=command):
                self.assert_rejected(captured(command, output))

    def test_collection_command_must_not_report_test_execution(self):
        for output in (
            "PASS tests/example.test.js\nTests: 1 passed",
            "FAIL tests/example.test.js\nTests: 1 failed",
            "Test Files  1 passed (1)\nTests  2 passed (2)",
        ):
            with self.subTest(output=output):
                self.assert_rejected(captured("npm test -- --listTests", output))

    def test_direct_bounded_command_requirement(self):
        for timeout in (None, 0, -1, True):
            with self.subTest(timeout=timeout):
                self.assert_rejected(captured("npm test -- --listTests", "/repo/tests/example.test.js", timeout=timeout))
        for suffix in (" 2>&1", " | head -20", " | tail -20", " | tee output.txt", "; echo ok", " && echo ok"):
            with self.subTest(suffix=suffix):
                self.assert_rejected(captured("npm test -- --listTests" + suffix, "/repo/tests/example.test.js"))
        self.assert_rejected(captured("timeout 10 npm test -- --listTests", "/repo/tests/example.test.js"))

    def test_multiple_passing_apps_require_distinct_application_evidence(self):
        apps = {
            "apps/api": {"description": "API service"},
            "apps/web": {"description": "Web application"},
        }
        api = captured("python -m pytest --collect-only tests/test_api.py", "collected 1 item\n1 test collected", cwd="apps/api")
        web = captured("npm test -- --listTests", "/repo/apps/web/tests/page.test.js", cwd="apps/web")
        _test_evidence([api, web], 2, apps)
        with self.assertRaises(ReportError):
            _test_evidence([api, dict(api)], 2, apps)
        _test_evidence([api], 1, apps)

    def test_root_invocations_can_identify_the_application_explicitly(self):
        apps = {
            "apps/api": {"description": "API service"},
            "apps/web": {"description": "Web application"},
        }
        api = captured("python -m pytest --collect-only apps/api/tests/test_api.py", "collected 1 item\n1 test collected", application="apps/api")
        web = captured("npm --prefix apps/web test -- --listTests", "/repo/apps/web/tests/page.test.js", application="apps/web")
        _test_evidence([api, web], 2, apps)
        invalid = dict(web, application="apps/unknown")
        with self.assertRaises(ReportError):
            _test_evidence([api, invalid], 2, apps)


if __name__ == "__main__":
    unittest.main()
