"""One-file collection regressions; runner output here is a mechanical fixture.

Actual offline pytest and public CLI evidence is separately retained in
python-multifile-runner-v1/ and python-nodeid-runner-v1/.
"""
import unittest
from agent_readiness.report import ReportError, _test_evidence

APPS = {'.': {'description': 'Single application fixture'}}


def record(command, count=1):
    return {'kind': 'command', 'command': command, 'cwd': '.',
            'exitCode': 0, 'timeoutSeconds': 30, 'application': '.',
            'output': f'collected {count} items\n\n================ {count} tests collected in 0.01s ================\n'}


class PythonCollectionContractTests(unittest.TestCase):
    def test_one_existing_file_and_narrower_node_remain_valid(self):
        for command in (
            'python -m pytest --collect-only tests/test_alpha.py',
            'python3 -m pytest -p no:cacheprovider --collect-only tests/test_alpha.py',
            'python3 -m pytest --collect-only tests/test_alpha.py::test_alpha',
            'pytest --collect-only tests/test_alpha.py::TestAlpha::test_alpha',
        ):
            with self.subTest(command=command):
                _test_evidence([record(command)], 1, APPS)

    def test_multiple_file_targets_are_not_one_file_collection(self):
        for command in (
            'python -m pytest --collect-only tests/test_alpha.py tests/test_beta.py',
            'pytest --collect-only tests/test_alpha.py tests/test_beta.py::test_beta',
            'python3 -m pytest --collect-only tests/test_alpha.py tests/',
        ):
            with self.subTest(command=command):
                with self.assertRaises(ReportError):
                    _test_evidence([record(command, 2)], 1, APPS)


if __name__ == '__main__':
    unittest.main()
