"""Review-r4 protocol fixtures: honest JS list forms and zero-test banners."""
import json
import unittest

try:
    from tests import test_validator_round3 as round3
except ImportError:
    import test_validator_round3 as round3


class RunnerRound5Tests(unittest.TestCase):
    setUp = round3.RunnerRound3Tests.setUp
    record = round3.RunnerRound3Tests.record
    accept = round3.RunnerRound3Tests.accept
    reject = round3.RunnerRound3Tests.reject

    def script(self, body):
        (self.root / 'apps/web/package.json').write_text(json.dumps({'scripts': {'test': body}}))

    def test_script_arguments_reach_the_runner(self):
        self.script('vitest')
        for command in ('npm test -- list', 'npm run test -- list', 'npm test list',
                        'yarn test list', 'pnpm test list', 'bun run test list'):
            with self.subTest(command=command):
                self.accept(command, cwd='apps/web')

    def test_forwarded_arguments_that_do_not_list_are_rejected(self):
        self.script('vitest')
        # pnpm and yarn forward `--` literally, so `vitest -- list` runs tests.
        for command in ('pnpm test -- list', 'yarn test -- list', 'npm test -- run', 'npm test'):
            with self.subTest(command=command):
                self.reject(command, cwd='apps/web')
        self.script('vitest run')
        self.reject('npm test -- list', cwd='apps/web')
        self.script('echo')
        self.reject('yarn test list', cwd='apps/web')

    def test_yarn_and_bun_script_echo_is_not_a_listed_test(self):
        for output in ('$ vitest list --passWithNoTests test/missing.test.js\n',
                       'yarn run v1.22.22\n$ vitest list --passWithNoTests test/missing.test.js\nDone in 0.31s.\n'):
            with self.subTest(output=output):
                self.reject('yarn test', output, cwd='apps/web', reason='enumerate at least one')
        self.accept('yarn test', '$ vitest list\ntests/example.test.ts > adds\n', cwd='apps/web')

    def test_result_words_inside_listed_titles_are_not_execution(self):
        for title in ('summarises 2 passed checks', 'reports 3 tests failed', 'shows 1 failed login'):
            with self.subTest(title=title):
                self.accept('npm test', f'tests/example.test.ts > {title}\n', cwd='apps/web')

    def test_runner_result_summaries_remain_execution(self):
        listed = 'tests/example.test.ts > adds\n'
        for summary in (' Test Files  1 passed (1)\n      Tests  1 passed (1)\n',
                        'Tests:       1 passed, 1 total\n', 'Test Suites: 1 failed, 1 total\n',
                        '  2 passed (1.2s)\n', '\x1b[32m1 passed\x1b[39m\n'):
            with self.subTest(summary=summary):
                self.reject('npm test', listed + summary, cwd='apps/web', reason='executed')

    def test_cross_env_prefix_matches_env(self):
        self.script('cross-env NODE_ENV=test vitest list')
        self.accept('npm test', cwd='apps/web')
        self.accept('cross-env NODE_ENV=test vitest list', cwd='apps/web')
        self.script('cross-env NODE_ENV=test vitest run')
        self.reject('npm test', cwd='apps/web')
        self.reject('cross-env NODE_ENV=test vitest run', cwd='apps/web')

    def test_vitest_value_options_before_list(self):
        for command in ('vitest --environment node list', 'vitest --dir src list', 'vitest -t adds list',
                        'pnpm exec vitest --environment node list'):
            with self.subTest(command=command):
                self.accept(command, cwd='apps/web')
        for command in ('vitest --environment list', 'vitest --dir list', 'vitest --environment node run'):
            with self.subTest(command=command):
                self.reject(command, cwd='apps/web')


if __name__ == '__main__':
    unittest.main()
