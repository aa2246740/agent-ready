"""Recorded-output branch regressions; these tests do not execute JS runners.

The npm dependency-list negative is backed by the separately recorded real
offline public-CLI counterexample. Other syntax examples are mechanical cases.
"""
import unittest
from agent_readiness.report import ReportError, _test_evidence

APPS = {".": {"description": "Single application fixture"}}


def record(command, output):
    return {
        "kind": "command",
        "command": command,
        "cwd": ".",
        "exitCode": 0,
        "output": output,
        "timeoutSeconds": 30,
        "application": ".",
    }


class DependencyListingContractTests(unittest.TestCase):
    def test_package_metadata_builtin_is_not_test_collection(self):
        for command in ("npm root --offline --ignore-scripts --listTests", "npm prefix --listTests"):
            with self.subTest(command=command):
                with self.assertRaises(ReportError):
                    _test_evidence([record(command, "/repo/node_modules\n")], 1, APPS)

    def test_package_dependency_list_is_not_test_collection(self):
        for command, output in [
            ("npm list --depth=0 --offline --ignore-scripts", "readiness-no-tests-fixture@1.0.0 /repo\n└── (empty)\n"),
            ("npm ls --list", "fixture@1.0.0 /repo\n└── (empty)\n"),
            ("pnpm list --depth=0", "Legend: production dependency, optional only, dev only\nfixture /repo\n"),
            ("yarn list", "yarn list v1.22.0\n└─ typescript@5.0.0\nDone\n"),
        ]:
            with self.subTest(command=command):
                with self.assertRaises(ReportError):
                    _test_evidence([record(command, output)], 1, APPS)

    def test_native_jest_vitest_collection_examples_remain_accepted(self):
        for command, output in [
            ("npm test -- --listTests", "/repo/tests/example.test.js\n"),
            ("npx --no-install jest --listTests", "/repo/tests/example.test.js\n"),
            ("yarn jest --listTests", "/repo/tests/example.test.ts\n"),
            ("pnpm exec jest --listTests", "/repo/tests/example.test.ts\n"),
            ("npx --no-install vitest list", "src/example.test.ts > Example > behaves correctly\n"),
            ("vitest list", "src/example.test.ts > Example > behaves correctly\n"),
            ("npm run collect -- --listTests", "/repo/tests/example.test.js\n"),
            ("npm run-script collect -- --listTests", "/repo/tests/example.test.js\n"),
        ]:
            with self.subTest(command=command):
                _test_evidence([record(command, output)], 1, APPS)


if __name__ == "__main__":
    unittest.main()
