"""Focused deterministic regression fixtures, not new readiness assessments."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from agent_readiness.application_scope import javascript_application_supported as supports
from agent_readiness.report import ReportError, _test_evidence

class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='scope-fixture-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.apps = {'apps/server': {}, 'apps/docs': {}}
        (self.root / 'package.json').write_text(json.dumps({'private': True, 'workspaces': ['apps/*']}))
        self.files = {}
        for app in self.apps:
            directory = self.root / app
            directory.mkdir(parents=True)
            (directory / 'package.json').write_text(json.dumps({'name': '@fixture/' + directory.name}))
            path = directory / 'real.test.ts'
            path.write_text('// Explicit synthetic collection-path fixture, not an executed test.\n')
            self.files[app] = path
        self.server = str(self.files['apps/server'])
        self.both = '\n'.join(str(p) for p in self.files.values())

    def check(self, app, command, output=None, cwd='.', historical=False):
        import shlex
        return supports(app, shlex.split(command), Path(cwd), self.apps, self.root, output or self.server, check_paths=not historical)

    def test_realistic_named_workspace_binds_its_application(self):
        self.assertTrue(self.check('apps/server', 'yarn workspace @fixture/server jest --listTests'))
        self.assertFalse(self.check('apps/docs', 'yarn workspace @fixture/server jest --listTests'))

    def test_forged_output_cannot_override_literal_workspace_manifest(self):
        self.assertFalse(self.check('apps/docs', 'yarn workspace @fixture/server jest --listTests', str(self.files['apps/docs'])))

    def test_prefix_paths_support_expected_application_only(self):
        for command in ('npm --prefix apps/server test -- --listTests', 'yarn --cwd=apps/server test --listTests'):
            with self.subTest(command=command):
                self.assertTrue(self.check('apps/server', command))
                self.assertFalse(self.check('apps/docs', command, self.both))

    def test_direct_application_cwd_is_not_arbitrary_label(self):
        self.assertTrue(self.check('apps/server', 'yarn jest --listTests', cwd='apps/server'))
        self.assertFalse(self.check('apps/docs', 'yarn jest --listTests', cwd='apps/server'))

    def test_root_npm_all_workspaces_preserves_two_distinct_collections(self):
        for app in self.apps:
            self.assertTrue(self.check(app, 'npm test --workspaces -- --listTests', self.both))
        self.assertFalse(self.check('apps/docs', 'npm test --workspaces -- --listTests', self.server))

    def test_root_repeated_literal_workspaces_preserve_two_apps(self):
        command = 'npm -w @fixture/server --workspace=@fixture/docs test -- --listTests'
        for app in self.apps:
            self.assertTrue(self.check(app, command, self.both))

    def test_root_direct_jest_multi_project_collection_preserved(self):
        for app in self.apps:
            self.assertTrue(self.check(app, 'npx jest --listTests', self.both))

    def test_same_output_cannot_count_for_a_second_application(self):
        self.assertTrue(self.check('apps/server', 'npm test --workspaces -- --listTests'))
        self.assertFalse(self.check('apps/docs', 'npm test --workspaces -- --listTests'))

    def test_vitest_relative_paths_and_case_suffix_bind_correct_app(self):
        text = 'apps/server/real.test.ts > suite > case\napps/docs/real.test.ts > suite > case'
        for app in self.apps:
            self.assertTrue(self.check(app, 'npx vitest list', text))

    def test_unknown_workspace_and_outside_prefix_fail_closed(self):
        for command in ('yarn workspace @fixture/unknown jest --listTests', 'npm --prefix /external test -- --listTests'):
            self.assertFalse(self.check('apps/server', command))

    def test_historical_readback_never_reads_current_manifest(self):
        with patch.object(Path, 'read_text', side_effect=AssertionError('No old-checkout reads')):
            self.assertTrue(self.check('apps/server', 'yarn workspace @fixture/server jest --listTests', historical=True))
            self.assertFalse(self.check('apps/docs', 'yarn workspace @fixture/server jest --listTests', historical=True))

    def test_single_nonroot_app_cannot_claim_server_workspace(self):
        self.apps = {'apps/docs': {}}
        self.assertFalse(self.check('apps/docs', 'yarn workspace @fixture/server jest --listTests'))

    def test_root_and_nested_multi_project_collection_both_count(self):
        self.apps = {'.': {}, 'apps/docs': {}}
        output = str(self.root / 'root.test.ts') + '\n' + str(self.files['apps/docs'])
        for app in self.apps:
            self.assertTrue(self.check(app, 'npx jest --listTests', output))

    def test_unique_workspace_and_prefix_resolve_relative_vitest_paths(self):
        for command in ('yarn workspace @fixture/server vitest list', 'npm --prefix apps/server test -- --listTests'):
            self.assertTrue(self.check('apps/server', command, 'real.test.ts > suite > case'))
            self.assertFalse(self.check('apps/docs', command, 'real.test.ts > suite > case'))

    def test_ambiguous_multi_workspace_relative_path_does_not_duplicate(self):
        for app in self.apps:
            self.assertFalse(self.check(app, 'npm test --workspaces -- --listTests', 'real.test.ts > suite > case'))

    def test_root_none_preserves_literal_prefix_and_direct_cwd(self):
        self.assertTrue(supports('apps/server', ['npm', '--prefix', 'apps/server', 'test', '--', '--listTests'], Path('.'), self.apps, None, '/repo/apps/server/real.test.ts'))
        self.assertTrue(supports('apps/server', ['jest', '--listTests'], Path('apps/server'), self.apps, None, '/repo/apps/server/real.test.ts'))
        self.assertFalse(supports('apps/docs', ['jest', '--listTests'], Path('apps/server'), self.apps, None, '/repo/apps/server/real.test.ts'))

    def test_historical_single_workspace_relative_output_is_retained(self):
        with patch.object(Path, 'read_text', side_effect=AssertionError('No old-checkout reads')):
            self.assertTrue(self.check('apps/server', 'yarn workspace @fixture/server vitest list', 'real.test.ts > suite > case', historical=True))

    def use_root_and_docs(self):
        self.apps = {'.': {}, 'apps/docs': {}}
        (self.root / 'package.json').write_text(json.dumps({'name': '@fixture/root', 'private': True, 'workspaces': ['apps/*']}))
        return str(self.root / 'root.test.ts') + '\n' + str(self.files['apps/docs'])

    def test_all_workspaces_excludes_root_by_default(self):
        output = self.use_root_and_docs()
        command = 'npm test --workspaces -- --listTests'
        self.assertFalse(self.check('.', command, output))
        self.assertTrue(self.check('apps/docs', command, output))

    def test_all_workspaces_can_explicitly_include_root(self):
        output = self.use_root_and_docs()
        for flag in ('--include-workspace-root', '--include-workspace-root=true'):
            for app in self.apps:
                self.assertTrue(self.check(app, 'npm test --workspaces ' + flag + ' -- --listTests', output))
        self.assertFalse(self.check('.', 'npm test --workspaces --include-workspace-root=false -- --listTests', output))

    def test_multi_workspace_relative_output_does_not_default_to_root(self):
        self.use_root_and_docs()
        for suffix in ('', ' --include-workspace-root'):
            for app in self.apps:
                self.assertFalse(self.check(app, 'npm test --workspaces' + suffix + ' -- --listTests', 'real.test.ts > suite > case'))

    def test_root_multi_project_without_workspace_selection_stays_supported(self):
        output = self.use_root_and_docs()
        for app in self.apps:
            self.assertTrue(self.check(app, 'npx jest --listTests', output))

    def test_historical_all_workspaces_preserves_root_opt_in_without_manifest_reads(self):
        output = self.use_root_and_docs()
        with patch.object(Path, 'read_text', side_effect=AssertionError('No old-checkout reads')):
            self.assertFalse(self.check('.', 'npm test --workspaces -- --listTests', output, historical=True))
            self.assertTrue(self.check('apps/docs', 'npm test --workspaces -- --listTests', output, historical=True))
            self.assertTrue(self.check('.', 'npm test --workspaces --include-workspace-root -- --listTests', output, historical=True))

    def test_report_rejects_relabelled_server_workspace_record(self):
        record = {'kind': 'command', 'command': 'yarn workspace @fixture/server jest --listTests',
                  'cwd': '.', 'exitCode': 0, 'output': self.server, 'timeoutSeconds': 30,
                  'application': 'apps/server'}
        _test_evidence([record], 1, self.apps, self.root)
        with self.assertRaises(ReportError):
            _test_evidence([dict(record, application='apps/docs')], 1, self.apps, self.root)

    def test_report_rejects_duplicate_server_output_as_two_applications(self):
        record = {'kind': 'command', 'command': 'yarn workspace @fixture/server jest --listTests',
                  'cwd': '.', 'exitCode': 0, 'output': self.server, 'timeoutSeconds': 30,
                  'application': 'apps/server'}
        with self.assertRaises(ReportError):
            _test_evidence([record, dict(record, application='apps/docs')], 2, self.apps, self.root)

if __name__ == '__main__':
    unittest.main()
