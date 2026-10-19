"""Round-four protocol fixtures; capture tests separately run real subprocesses."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent_readiness.report import json_text, new_evaluation, store_report, ReportError
from agent_readiness.repository import inspect_repository
try:
    from tests import test_validator_round3 as round3
except ImportError:
    import test_validator_round3 as round3

CLI = Path(__file__).resolve().parents[1] / 'scripts/readiness.py'


class FixStoredEvidenceTests(unittest.TestCase):
    def test_stored_optional_file_line_works_in_every_fix_entry_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / 'repo'
            root.mkdir()
            subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True)
            (root / 'README.md').write_text('Synthetic stored-report fixture.\n')
            data = new_evaluation(inspect_repository(root))
            data['apps'] = {'.': {'description': 'Fixture'}}
            for key, row in data['report'].items():
                row.update(numerator=0, rationale='Synthetic failing criterion.')
                data['evidence'][key] = [{'kind': 'file', 'path': 'README.md', 'note': 'Fixture'}]
            state = base / 'state'
            receipt = store_report(data, root, state)
            original = Path(receipt['report']).read_bytes()
            for options in ([], ['--all'], ['--signals', 'lint_config'], ['--target-level', '4']):
                with self.subTest(options=options):
                    result = subprocess.run([sys.executable, str(CLI), 'fix', str(root), '--state-dir', str(state), *options],
                                            capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('Previous evidence: 1 file, 0 command; cited README.md.', result.stdout)
                    self.assertIn('lint_config', result.stdout)
            self.assertEqual(Path(receipt['report']).read_bytes(), original)
            # A present line still renders and an invalid line is still rejected.
            data['evidence']['lint_config'][0]['line'] = 1
            store_report(data, root, state)
            result = subprocess.run([sys.executable, str(CLI), 'fix', str(root), '--state-dir', str(state), '--signals', 'lint_config'],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('cited README.md:1.', result.stdout)
            data['evidence']['lint_config'][0]['line'] = 50
            with self.assertRaises(ReportError):
                store_report(data, root, state)


class RunnerRound4Tests(unittest.TestCase):
    setUp = round3.RunnerRound3Tests.setUp
    record = round3.RunnerRound3Tests.record
    accept = round3.RunnerRound3Tests.accept
    reject = round3.RunnerRound3Tests.reject

    def script(self, body):
        (self.root / 'apps/web/package.json').write_text(json.dumps({'scripts': {'test': body}}))

    def test_f3_shared_script_and_direct_environment_and_global_options(self):
        for body in ('NODE_ENV=test vitest list', 'env NODE_ENV=test vitest list', 'env vitest list',
                     'vitest --no-color list', 'pnpm exec vitest --no-color list',
                     'env NODE_ENV=test npx --no-install vitest --no-color list',
                     'vitest --config vitest.config.ts --no-color list', 'vitest --root=. list'):
            with self.subTest(body=body):
                self.script(body)
                self.accept('npm test', cwd='apps/web')
                self.accept(body, cwd='apps/web')
                self.reject('npm test', 'PASS tests/example.test.ts\nTests: 1 passed', cwd='apps/web')
                self.reject('npm test', 'No tests found', cwd='apps/web')

    def test_f3_execution_and_option_values_are_not_list_subcommands(self):
        for body in ('NODE_ENV=test vitest run', 'env NODE_ENV=test vitest', 'vitest --config list',
                     'vitest --reporter list', 'pnpm exec vitest --no-color run',
                     'env NODE_ENV=test npm list', 'env NODE_ENV=test echo --listTests'):
            with self.subTest(body=body):
                self.script(body)
                self.reject('npm test', cwd='apps/web')
                self.reject(body, cwd='apps/web')

    def test_f4_runner_items_survive_lifecycle_banners(self):
        for output in ('/repo/tests/example.test.ts\n', 'tests/example.test.ts > adds\n',
                       '  [chromium] › tests/example.spec.ts:3:1 › adds\n',
                       '["/repo/tests/example.test.ts"]\n',
                       '[{"file":"/repo/tests/example.test.ts","name":"adds"}]\n',
                       '[{"file":"/repo/tests/example.test.ts"}]\n'):
            self.accept('npm test', '> pkg@1 test\n> vitest list\n\n' + output, cwd='apps/web')

    def test_f4_empty_list_banners_and_summaries_do_not_count(self):
        for output in ('> pkg@1 test\n> vitest list missing.test.js --passWithNoTests\n',
                       '> vitest list\n', 'Listing tests:\nTotal: 0 tests in 0 files\n',
                       'vitest/3.0.0\nDone in 0.2s\n', '[]\n', '[{"name":"no file"}]\n',
                       '[{"file":"","name":"no file"}]\n'):
            self.reject('npm test', output, cwd='apps/web', reason='enumerate at least one')

    def test_f5_bun_result_text_inside_listed_titles_is_not_execution(self):
        for title in ('formats (pass) correctly', 'formats (fail) correctly', 'prints 1 pass',
                      'prints 1 fail', 'formats Ran 1 tests correctly'):
            self.accept('npm test', f'tests/example.test.ts > {title}\n', cwd='apps/web')

    def test_f5_actual_bun_execution_results_remain_rejected(self):
        for output in ('(pass) adds [0.01ms]\n', '(fail) adds\n', '1 pass\n0 fail\n',
                       'Ran 1 tests across 1 files. [3ms]\n'):
            self.reject('npm test', output, cwd='apps/web', reason='executed')

    def test_f6_cargo_package_id_forms_preserve_app_ownership(self):
        for package in ('path+file:///repo/apps/web#web@0.1.0',
                        'registry+https://github.com/rust-lang/crates.io-index#web@0.1.0',
                        'web@0.1.0', 'path+file:///repo/apps/web#0.1.0', 'web'):
            command = f'cargo test -p {package} -- --list'
            with self.subTest(package=package):
                self.accept(command, 'adds: test\n')
                self.reject(command, 'adds: test\n', application='apps/api', reason='cargo')
        self.reject('cargo test -p path+file:///repo/apps/api#api@0.1.0 -- --list', 'adds: test\n', reason='cargo')
