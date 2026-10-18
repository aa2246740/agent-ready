"""Synthetic positive/negative contracts for the round-three review findings.

Captured outputs here are protocol fixtures, not claims of runtime acceptance.
Real Go execution remains in test_runner_context and test_wrapper_and_go_flags.
"""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from agent_readiness.condition_evidence import _automatic_trigger_error, condition_errors
from agent_readiness.report import ReportError, _test_evidence, validate_evaluation
from agent_readiness.repository import inspect_repository
try:
    from tests import test_condition_evidence as conditions
except ImportError:
    import test_condition_evidence as conditions


class RunnerRound3Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='validator-round3-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.apps = {'apps/web': {}, 'apps/api': {}}
        for app in self.apps:
            directory = self.root / app
            (directory / 'tests').mkdir(parents=True)
            (directory / 'tests/test_one.py').write_text('def test_one():\n    assert 2 + 2 == 4\n')
            (directory / 'pytest.ini').write_text('[pytest]\n')
            (directory / 'package.json').write_text(json.dumps({
                'name': '@fixture/' + directory.name,
                'scripts': {'test': 'vitest list', 'inventory': 'jest --listTests',
                            'only': 'node --test --test-only', 'deps': 'npm list'},
            }))
            (directory / 'Cargo.toml').write_text('[package]\nname = "' + directory.name + '"\nversion = "0.1.0"\n')
        (self.root / 'go.mod').write_text('module example.com/m\n\ngo 1.18\n')

    def record(self, command, output='tests/example.test.ts > suite > adds\n', **values):
        return {'kind': 'command', 'command': command, 'cwd': '.', 'exitCode': 0,
                'output': output, 'timeoutSeconds': 30, 'application': 'apps/web', **values}

    def accept(self, command, output='tests/example.test.ts > suite > adds\n', **values):
        _test_evidence([self.record(command, output, **values)], 1, self.apps, self.root)

    def reject(self, command, output='tests/example.test.ts > suite > adds\n', reason=None, **values):
        with self.assertRaises(ReportError) as caught:
            self.accept(command, output, **values)
        if reason:
            self.assertIn(reason, str(caught.exception))

    def test_d1_exact_directory_and_package_selectors(self):
        for client in ('pnpm', 'npm', 'yarn'):
            for selector in ('--filter ./apps/web', '-F ./apps/web', '--filter=./apps/web',
                             '-F@fixture/web', '--filter @fixture/web', '--dir apps/web',
                             '-C apps/web', '--prefix apps/web', '--workspace apps/web',
                             '--workspace @fixture/web'):
                with self.subTest(client=client, selector=selector):
                    self.accept(f'{client} {selector} test -- --listTests')
                    self.reject(f'{client} {selector} test -- --listTests', application='apps/api')

    def test_d1_ambiguous_glob_unknown_and_external_selectors(self):
        for selector in ('--filter "./apps/*"', '--filter "@fixture/*"', '--filter "web..."',
                         '--workspace missing', '--dir /outside', '--filter ../web'):
            with self.subTest(selector=selector):
                self.reject('pnpm ' + selector + ' test -- --listTests')
        manifest = self.root / 'apps/api/package.json'
        manifest.write_text(json.dumps({'name': '@fixture/web'}))
        self.reject('pnpm --filter @fixture/web test -- --listTests', reason='ambiguous')

    def test_d2_configured_list_scripts_at_the_selected_app(self):
        for command in ('npm test', 'npm run inventory', 'pnpm test', 'yarn inventory', 'bun run inventory', 'bun run test'):
            with self.subTest(command=command):
                self.accept(command, cwd='apps/web')
                self.accept('cd apps/web && ' + command)
        self.accept('pnpm --filter @fixture/web test')
        self.accept('npm --workspace apps/web run inventory')
        self.accept('yarn workspace @fixture/web inventory')

    def test_d2_scripts_still_reject_execution_dependency_listing_and_zero_tests(self):
        self.reject('npm test', '1 passed', cwd='apps/web', reason='executed')
        self.reject('npm test', '0 tests listed', cwd='apps/web', reason='zero tests')
        for command in ('npm run only', 'npm run deps', 'npm run absent'):
            self.reject(command, cwd='apps/web', reason='collection/list')
        self.reject('pnpm --filter @fixture/api test', reason='application')

    def test_d3_own_config_is_relative_to_effective_cd_directory(self):
        for option in ('-c pytest.ini', '-cpytest.ini', '--config-file=pytest.ini',
                       '--rootdir .', '--confcutdir .'):
            command = f'pytest --collect-only {option} tests/test_one.py'
            with self.subTest(option=option):
                self.accept('cd apps/web && ' + command, 'collected 1 item\n')
                self.accept(command, 'collected 1 item\n', cwd='apps/web')

    def test_d3_config_still_cannot_escape_or_select_another_app(self):
        for option in ('-c ../api/pytest.ini', '--rootdir /outside', '--confcutdir ../api'):
            self.reject(f'cd apps/web && pytest --collect-only {option} tests/test_one.py', 'collected 1 item\n')
        self.reject('pytest --collect-only -c apps/api/pytest.ini apps/web/tests/test_one.py', 'collected 1 item\n', reason='another application')

    def test_d4_interpreter_dependency_and_index_options_are_not_discovery(self):
        for options in ('--python /opt/host/python3', '-p /opt/host/python3', '-p/opt/host/python3',
                        '--python=/opt/host/python3', '--with /opt/requirements/pkg.whl',
                        '--index-url https://packages.example/simple', '--index https://packages.example/simple',
                        '--cache-dir /opt/cache', '--with ../local-package'):
            for command in (f'uv run {options} pytest', f'uv {options} run pytest'):
                with self.subTest(command=command):
                    self.accept(command + ' --collect-only tests/test_one.py', 'collected 1 item\n', cwd='apps/web')

    def test_d4_project_selectors_and_inner_runner_escapes_stay_rejected(self):
        for options in ('--project apps/api', '--directory apps/api', '-C apps/api', '-Capps/api'):
            for command in (f'uv {options} run pytest', f'uv run {options} pytest'):
                self.reject(command + ' --collect-only tests/test_one.py', 'collected 1 item\n', cwd='apps/web')
        self.reject('poetry run -p apps/api pytest --collect-only tests/test_one.py', 'collected 1 item\n', cwd='apps/web')
        self.reject('uv run --python /opt/python pytest --collect-only /outside/test_one.py', 'collected 1 item\n', cwd='apps/web')

    def test_d8_go_imports_use_root_and_nested_module_prefixes(self):
        for selector in ('./apps/web/...', 'example.com/m/apps/web/...', 'example.com/m/apps/web'):
            self.accept('go test -run TestOne ' + selector, 'ok example.com/m/apps/web 0.01s\n')
            self.reject('go test -run TestOne ' + selector, 'ok example.com/m/apps/web 0.01s\n', application='apps/api')
        (self.root / 'apps/web/go.mod').write_text('module "example.net/nested"\n')
        self.accept('go test -list . example.net/nested/...', 'TestOne\nok example.net/nested 0.01s\n')
        for selector in ('example.net/nested-other/...', 'example.com/other/apps/web/...'):
            self.reject('go test -list . ' + selector, 'TestOne\nok example.net/nested 0.01s\n')

    def test_d9_pest_native_results_and_listings(self):
        for runner in ('pest', 'vendor/bin/pest', './vendor/bin/pest', 'php artisan test'):
            for result in ('PASS Tests\\ExampleTest\nTests: 1 passed (1 assertion)', 'OK (1 test, 1 assertion)', '\x1b[32mTests:\x1b[0m 1 \x1b[32mpassed\x1b[0m'):
                self.accept(runner + ' tests/ExampleTest.php', result, cwd='apps/web')
            self.accept(runner + ' --list-tests', 'Available tests:\n - ExampleTest::adds\n', cwd='apps/web')

    def test_d9_pest_and_unknown_runner_diagnostics(self):
        for runner in ('pest', 'vendor/bin/pest', 'php artisan test'):
            self.reject(runner + ' tests/ExampleTest.php', 'Tests: 0 passed', cwd='apps/web')
            self.reject(runner + ' tests/ExampleTest.php', 'Tests: 1 passed, 1 failed', cwd='apps/web')
            self.reject(runner + ' --list-tests', 'Available tests:\n', cwd='apps/web')
            self.reject(runner + ' --help', 'Tests: 1 passed', cwd='apps/web')
        self.reject('rspec --dry-run spec/one_spec.rb', '1 example, 0 failures', cwd='apps/web', reason='dry-run')
        self.accept('npx playwright test --list', 'tests/example.test.ts:3:1 > adds\n', cwd='apps/web')
        self.reject('unfamiliar tests/one.test', 'SUCCESS', cwd='apps/web', reason="unrecognized test runner unfamiliar; cite its native test-list command")

    def test_d10_bun_execution_is_not_list_output(self):
        self.accept('bun run inventory', cwd='apps/web')
        for output in ('(pass) adds', '(fail) adds', '1 pass\n0 fail', 'Ran 1 tests across 1 files.'):
            self.reject('bun test --list-tests tests/example.test.ts', output, cwd='apps/web', reason='executed')
            self.reject('npm test -- --listTests', output, cwd='apps/web', reason='executed')
        self.reject('bun test --list-tests tests/example.test.ts', 'tests/example.test.ts', cwd='apps/web', reason='collection/list')

    def test_d11_go_empty_warning_does_not_count_plain_or_json(self):
        command = 'go test -run TestMissing ./apps/web'
        self.reject(command, 'testing: warning: no tests to run\nPASS\nok example.com/m/apps/web 0.01s\n')
        self.reject(command, 'ok example.com/m/apps/web 0.01s [no tests to run]\n')
        output = '\n'.join(json.dumps(event) for event in (
            {'Action': 'output', 'Package': 'web', 'Output': 'testing: warning: no tests to run\n'},
            {'Action': 'pass', 'Package': 'web'},
        ))
        self.reject(command, output)

    def test_d11_independent_real_test_lines_survive_empty_packages(self):
        command = 'go test -run TestOne ./apps/web/...'
        empty = 'testing: warning: no tests to run\nPASS\nok example.com/m/apps/web/empty 0.01s\n'
        actual = '=== RUN   TestOne\n--- PASS: TestOne (0.00s)\nok example.com/m/apps/web 0.01s\n'
        for output in (actual, empty + actual, actual + empty, 'TestOne\n' + empty):
            self.accept(command, output)
        self.accept(command, json.dumps({'Action': 'pass', 'Package': 'web', 'Test': 'TestOne'}))

    def test_d12_cargo_package_manifest_and_bin_ownership(self):
        for selector in ('-p web', '-pweb', '--package=web', '--manifest-path apps/web/Cargo.toml', '--bin web'):
            command = 'cargo test ' + selector + ' -- --list'
            self.accept(command, 'adds: test\n')
            self.reject(command, 'adds: test\n', application='apps/api', reason='cargo')
        manifest = self.root / 'apps/web/Cargo.toml'
        manifest.write_text(manifest.read_text() + '\n[[bin]]\nname = "web-cli"\npath = "src/cli.rs"\n')
        self.accept('cargo test -p web --bin web-cli -- --list', 'adds: test\n')
        self.reject('cargo test -p web --bin api -- --list', 'adds: test\n')

    def test_d12_capture_is_not_relabelled_across_apps(self):
        first = self.record('cargo test -- --list', 'adds: test\n')
        _test_evidence([first, deepcopy(first)], 1, self.apps, self.root)
        with self.assertRaisesRegex(ReportError, 'same command, effective runner directory and output'):
            _test_evidence([first, dict(first, application='apps/api')], 2, self.apps, self.root)
        second = self.record('cargo test -p api -- --list', 'adds: test\n', application='apps/api')
        _test_evidence([self.record('cargo test -p web -- --list', 'adds: test\n'), second], 2, self.apps, self.root)
        # A spelling of cwd does not create a second execution.
        with self.assertRaises(ReportError):
            _test_evidence([first, dict(first, cwd=str(self.root), application='apps/api')], 2, self.apps, self.root)

    def test_d12_root_app_includes_local_library_crates_with_distinct_nested_owners(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        (self.root / 'Cargo.toml').write_text('[package]\nname = "root-cli"\nversion = "0.1.0"\n')
        nested = self.root / 'crates/shared'
        nested.mkdir(parents=True)
        (nested / 'Cargo.toml').write_text('[package]\nname = "shared"\nversion = "0.1.0"\n')
        root_apps = {'.': {}}
        item = self.record('cargo test -p shared --lib -- --list', 'adds: test\n', application='.')
        _test_evidence([item], 1, root_apps, self.root)
        root_apps['apps/api'] = {}
        _test_evidence([item], 1, root_apps, self.root)
        with self.assertRaises(ReportError):
            _test_evidence([dict(item, application='apps/api')], 1, root_apps, self.root)
        root_apps['crates/shared'] = {}
        with self.assertRaises(ReportError):
            _test_evidence([item], 1, root_apps, self.root)
        _test_evidence([dict(item, application='crates/shared')], 1, root_apps, self.root)


class ConditionRound3Tests(unittest.TestCase):
    setUp = conditions.ConditionEvidenceTests.setUp
    record = conditions.ConditionEvidenceTests.record
    alert = conditions.ConditionEvidenceTests.alert
    dependency = conditions.ConditionEvidenceTests.dependency
    write_trace = conditions.ConditionEvidenceTests.write_trace

    def test_d5_normalized_handler_notifier_paths_form_a_chain(self):
        for extension in ('.py', '.js', '.ts', '.mjs', '.cjs', '.go', '.rb', '.java', '.kt', '.sh'):
            for separator in ('.', '/', '::'):
                records = self.alert()
                handler = 'app/monitor' + extension
                sender = 'app/notifier' + extension
                (self.target / 'app').mkdir(exist_ok=True)
                self.sources[handler] = 'from app.notifier import send\ndef check_health(): send("bad")\n'
                self.sources[sender] = 'def send(message): deliver(message)\n'
                for path in (handler, sender):
                    (self.target / path).write_text(self.sources[path])
                records[0] = self.record(handler, 'alert_rule', handler='APP' + separator + 'MONITOR', notifier='app/notifier')
                records[2] = self.record(sender, 'alert_delivery', notifier='app.notifier' + extension)
                self.sources['registration.txt'] = 'schedule: hourly python -m app.monitor\n'
                (self.target / 'registration.txt').write_text(self.sources['registration.txt'])
                records[1] = self.record('registration.txt', 'automatic_trigger', kind='schedule', registration='schedule: hourly', handler='app/monitor' + extension)
                with self.subTest(extension=extension, separator=separator):
                    self.assertEqual(condition_errors(self.data, self.target), [])

    def test_d5_unrelated_identities_fail_with_normalized_chain_diagnostic(self):
        records = self.alert()
        records[1] = self.record('other-registration.txt', 'automatic_trigger', kind='schedule', registration='enabled schedule', handler='unrelated_check')
        errors = str(condition_errors(self.data, self.target))
        for message in ('identities must connect across alert_rule/automatic_trigger/alert_delivery', 'normalized identities found', 'check_health', 'unrelated_check'):
            self.assertIn(message, errors)
        records[0]['support']['facts']['handler'] = 'invented.monitor.py'
        self.assertIn('support.facts.handler', str(condition_errors(self.data, self.target)))

    def test_d6_scheduler_can_run_once_but_a_service_or_process_cannot(self):
        for quote, registration, path in (
            ('*/5 * * * * python app/monitor.py --once\n', '*/5 * * * *', 'crontab'),
            ('schedule:\n  cron: "*/5 * * * *"\n  command: app/monitor.py --once\n', 'schedule', 'alert.yml'),
            ('[Timer]\nOnCalendar=hourly\nExecStart=monitor --once\n', 'OnCalendar=hourly', 'alert.timer'),
            ('event: check_health --once\n', 'event', 'alert.yml'),
            ('manual alerts queue: check_health --once\n', 'manual alerts queue', 'events.yml'),
        ):
            self.assertIsNone(_automatic_trigger_error(quote, registration, path, 'schedule'))
        for kind in ('process', 'service'):
            self.assertIsNotNone(_automatic_trigger_error('ExecStart=monitor --once\n', 'ExecStart', 'alert.service', kind))
        self.assertIsNotNone(_automatic_trigger_error('manual --once check_health', 'manual --once', None, 'schedule'))

    def test_d7_file_and_command_quotes_normalize_only_newlines(self):
        for newline in ('\r\n', '\r', '\n'):
            records = self.dependency()
            text = '{\n"extends":["config:recommended"]\n}\n'
            self.sources['renovate.json'] = text.replace('\n', newline)
            (self.target / 'renovate.json').write_bytes(self.sources['renovate.json'].encode())
            records[0] = self.record('renovate.json', 'dependency_configuration', updater='renovate')
            records[0]['support']['quote'] = text.replace('\n', newline)
            self.assertEqual(condition_errors(self.data, self.target), [])
            records[0]['support']['quote'] = text
            self.assertEqual(condition_errors(self.data, self.target), [])
            records[0]['line'] = 2
            self.assertIn('support.quote', str(condition_errors(self.data, self.target)))
        row = self.dependency()[1]
        row.update(kind='command', command='gh pr view 7', cwd='.', exitCode=0, output='\r\n' + row['support']['quote'] + '\r\n')
        for field in ('path', 'line', 'note'):
            row.pop(field)
        self.assertEqual(condition_errors(self.data, self.target), [])
        row['support']['quote'] += '\nfabricated'
        self.assertIn('support.quote', str(condition_errors(self.data, self.target)))

    def test_d7_mcp_readback_matches_newlines_but_not_unread_text(self):
        records = self.dependency()
        self.sources['renovate.json'] = '{\r\n"extends":["config:recommended"]\r\n}\r\n'
        (self.target / 'renovate.json').write_bytes(self.sources['renovate.json'].encode())
        records[0] = self.record('renovate.json', 'dependency_configuration', updater='renovate')
        records[0]['support']['quote'] = self.sources['renovate.json'].replace('\r\n', '\n')
        self.data['repository'] = inspect_repository(self.target)
        Path(self.config['template']).write_text(json.dumps(self.data))
        self.write_trace()
        gate = conditions.ADAPTER.Gate(self.config)
        self.assertEqual(gate.missing_quotes(self.data), [])
        self.assertTrue(gate.submit({'evaluation_json': json.dumps(self.data)})['accepted'])
        self.config.update(output=str(self.base / 'second-attempts'), state_dir=str(self.base / 'second-state'))
        self.write_trace(truncate='renovate.json')
        gate = conditions.ADAPTER.Gate(self.config)
        self.assertEqual(gate.missing_quotes(self.data)[0]['path'], 'renovate.json')
        self.assertFalse(gate.submit({'evaluation_json': json.dumps(self.data)})['accepted'])

    def test_d13_cronjob_suspend_is_local_to_its_spec_and_document(self):
        template = 'kind: CronJob\nmetadata:\n  name: monitor\nspec:\n  schedule: "*/5 * * * *"\n  suspend: %s\n  jobTemplate:\n    spec:\n      suspend: true\n'
        for value in ('false', 'true'):
            result = _automatic_trigger_error(template % value, 'schedule', 'cronjob.yml', 'schedule')
            self.assertEqual(result is None, value == 'false')
        disabled = template % 'true'
        enabled = (template % 'false').replace('monitor', 'other')
        self.assertIsNone(_automatic_trigger_error(disabled + '---\n' + enabled, 'other', 'cronjob.yml', 'schedule'))
        self.assertIsNone(_automatic_trigger_error('schedule:\n  suspend: true\n  cron: hourly\n', 'schedule', 'custom.yml', 'schedule'))
        self.assertIsNotNone(_automatic_trigger_error((template % 'true') + '  command: check --once\n', 'schedule', 'cronjob.yml', 'schedule'))


if __name__ == '__main__':
    unittest.main()
