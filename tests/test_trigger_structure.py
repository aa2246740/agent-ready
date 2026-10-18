"""Automatic-trigger validation: comments follow the file type, manual means structure.

Synthetic excerpts only. Positive cases are correct, honest registrations that
earlier heuristics rejected; negative cases keep the genuine misuse rejected.
"""

import unittest

from agent_readiness.condition_evidence import _automatic_trigger_error as check, _comment_mask

WORKFLOW = 'name: ci\non:\n  push:\n    paths: ["src/**"]\n  schedule:\n    - cron: "0 0 * * *"\n'


class CommentFollowsFileType(unittest.TestCase):
    def accepted(self, quote, registration, path=None):
        self.assertIsNone(check(quote, registration, path), (quote, registration, path))

    def rejected(self, quote, registration, path=None, text="only in a comment"):
        error = check(quote, registration, path)
        self.assertIsNotNone(error, (quote, registration, path))
        self.assertIn(text, error)

    def test_crontab_line_starting_with_star_is_a_registration(self):
        for path in (None, "crontab", "deploy/app.cron", "etc/crontab"):
            with self.subTest(path=path):
                self.accepted("* * * * * /usr/bin/run-job", "* * * * * /usr/bin/run-job", path)
        self.accepted("# m h dom mon dow command\n*/5 * * * * /usr/bin/run-job\n", "*/5 * * * *", "crontab")
        self.rejected("# */5 * * * * /usr/bin/run-job\n", "*/5 * * * *", "crontab")

    def test_glob_in_workflow_does_not_open_a_block_comment(self):
        self.accepted(WORKFLOW, "schedule", ".github/workflows/ci.yml")
        self.accepted(WORKFLOW, "- cron:", ".github/workflows/ci.yaml")
        self.accepted(WORKFLOW, "schedule")  # command output / unknown type: nothing is a comment

    def test_free_words_do_not_mark_a_trigger_manual(self):
        for word in ("manual-release", "planned", "TODO", "disabled-legacy", "manual_approval"):
            with self.subTest(word=word):
                quote = f"on:\n  schedule:\n    - cron: '0 0 * * *'\n  push:\n    branches: [{word}]\n"
                self.accepted(quote, "schedule", "ci.yml")
        self.accepted("# TODO: tune cadence\nschedule: every 5 minutes\nname: manual check helper\n", "schedule", "alert.yml")
        self.accepted("schedule: every 5 minutes\ndescription: planned for disabled hosts\n", "schedule: every 5", "alert.txt")

    def test_comment_syntax_per_file_type(self):
        cases = {
            "a.yml": "# schedule: x\n", "a.toml": "# schedule = 1\n", "a.ini": "# schedule\n", "run.sh": "# schedule\n",
            "job.py": "# schedule\n", "job.rb": "# schedule\n", "Dockerfile": "# schedule\n", "Makefile": "# schedule\n",
            ".env": "# schedule\n", "x.env": "# schedule\n", "job.js": "// schedule\n", "job.ts": "/* schedule */\n",
            "job.go": "// schedule\n", "Job.java": "/*\n * schedule\n */\n", "page.html": "<!-- schedule -->\n",
            "doc.md": "<!--\nschedule\n-->\n", "App.vue": "<!-- schedule -->\n", "view.svelte": "<!-- schedule -->\n",
            "build.gradle": "// schedule\n",
        }
        for path, quote in cases.items():
            with self.subTest(path=path):
                self.rejected(quote + "handler\n", "schedule", path)

    def test_wrong_syntax_for_the_type_is_not_a_comment(self):
        for path, quote in (("job.go", "# schedule\n"), ("a.yml", "// schedule\n"), ("a.yml", "/* schedule */\n"),
                            ("page.html", "# schedule\n"), ("doc.md", "# schedule\n"), ("job.js", "<!-- schedule -->\n"),
                            ("notes.txt", "# schedule\n"), ("data.json", "// schedule\n"), (None, "# schedule\n"),
                            ("job.py", "// schedule\n")):
            with self.subTest(path=path, quote=quote):
                self.accepted(quote, "schedule", path)

    def test_markers_inside_strings_are_not_comments(self):
        self.accepted('cron: "0 0 * * *"  # nightly\nlabel: "# not a comment"\n', 'label: "# not a comment"', "a.yml")
        self.accepted("const url = 'http://example.test/x'; schedule()\n", "schedule()", "job.js")
        self.accepted('const note = "/* x"; schedule();\n', "schedule()", "job.js")
        self.accepted("echo '# hi'; run-check\n", "run-check", "run.sh")
        self.accepted("name: Bob's job\nschedule: hourly\n", "schedule: hourly", "a.yml")
        self.rejected('x = "a"  # schedule: hourly\n', "schedule: hourly", "job.py")

    def test_registration_after_trailing_comment_marker_is_still_a_comment(self):
        self.rejected("run_check  # schedule: hourly\n", "schedule: hourly", "a.yml")
        self.rejected("run_check(); // schedule()\n", "schedule()", "job.js")
        self.accepted("schedule: hourly  # keep\n", "schedule: hourly", "a.yml")

    def test_unterminated_block_comment_runs_to_the_end_and_closed_one_ends(self):
        self.rejected("/* schedule\nstill comment\n", "schedule", "job.js")
        self.accepted("/* note */\nschedule()\n", "schedule()", "job.js")
        mask = _comment_mask("a /* b */ c", "x.js")
        self.assertEqual("".join("C" if m else "." for m in mask), "..CCCCCCC..")

    def test_one_live_occurrence_is_enough(self):
        self.accepted("# schedule: old\nschedule: new\n", "schedule", "a.yml")


class ManualMeansStructure(unittest.TestCase):
    def rejected(self, quote, registration, path=None):
        error = check(quote, registration, path)
        self.assertIsNotNone(error, (quote, registration))
        self.assertIn("manual, test-only, planned or disabled", error)

    def test_workflow_dispatch_only_is_manual(self):
        quote = "on:\n  workflow_dispatch:\n    inputs:\n      reason: {}\n"
        self.rejected(quote, "workflow_dispatch", "ci.yml")
        self.rejected(quote, "on:", "ci.yml")
        self.rejected("on: workflow_dispatch\n", "workflow_dispatch", "ci.yml")
        self.rejected("on: [workflow_dispatch]\n", "on:", "ci.yml")
        self.rejected("workflow_dispatch:\n", "workflow_dispatch", "ci.yml")

    def test_workflow_dispatch_next_to_an_automatic_event_is_fine(self):
        quote = "on:\n  workflow_dispatch:\n  schedule:\n    - cron: '0 0 * * *'\n"
        for registration in ("schedule", "- cron: '0 0 * * *'", "workflow_dispatch", "on:"):
            with self.subTest(registration=registration):
                self.assertIsNone(check(quote, registration, "ci.yml"))
        self.assertIsNone(check("on: [schedule, workflow_dispatch]\n", "schedule"))
        self.assertIsNone(check("on:\n  workflow_dispatch:\n  push:\n    branches: [main]\n", "push"))
        self.assertIsNone(check("on:\n  workflow_dispatch:\n  release:\n    types: [published]\n", "release"))

    def test_other_workflows_in_the_excerpt_do_not_decide(self):
        quote = ("on:\n  workflow_dispatch:\njobs:\n  a:\n    runs-on: x\n")
        self.rejected(quote, "workflow_dispatch", "ci.yml")
        quote = "on:\n  schedule:\n    - cron: '0 0 * * *'\njobs:\n  a:\n    steps:\n      - run: echo workflow_dispatch\n"
        self.assertIsNone(check(quote, "schedule", "ci.yml"))

    def test_once_flag_is_manual(self):
        self.rejected("0 * * * * check --once\n", "0 * * * * check --once", "crontab")
        self.rejected("0 * * * * check --once\n", "0 * * * *")
        self.rejected("ExecStart=/usr/bin/check --once\nOnCalendar=hourly\n", "ExecStart=/usr/bin/check --once")
        self.assertIsNone(check("0 * * * * check --once-upon-a-time\n", "0 * * * *"))
        self.assertIsNone(check("0 * * * * check --no-once\n", "0 * * * *"))

    def test_enabled_false_in_the_registration_block_is_disabled(self):
        for quote, registration, path in (
            ("schedule:\n  enabled: false\n  cron: '0 * * * *'\n", "schedule", "a.yml"),
            ("schedule:\n  cron: '0 * * * *'\n  enabled: false\n", "cron: '0 * * * *'", "a.yml"),
            ("schedule:\n  - cron: '0 * * * *'\n    enabled: false\n", "- cron: '0 * * * *'", "a.yml"),
            ("[schedule]\ncron = '0 * * * *'\nenabled = false\n", "[schedule]", "a.toml"),
            ("[schedule]\nenabled=false\ncron = '0 * * * *'\n", "cron = '0 * * * *'", "a.toml"),
            ('{"schedule": {"enabled": false, "cron": "0 * * * *"}}', '"schedule"', "a.json"),
            ("schedule: {enabled: False}\n", "schedule", "a.yml"),
        ):
            with self.subTest(quote=quote):
                self.rejected(quote, registration, path)

    def test_enabled_false_elsewhere_does_not_disable_the_registration(self):
        for quote, registration, path in (
            ("triggers:\n  schedule:\n    cron: '0 * * * *'\n  webhook:\n    enabled: false\n", "schedule", "a.yml"),
            ("jobs:\n  a:\n    enabled: false\n  b:\n    schedule: hourly\n", "schedule: hourly", "a.yml"),
            ("[old]\nenabled = false\n[schedule]\ncron = '0 * * * *'\n", "[schedule]", "a.toml"),
            ("[old]\nenabled = false\n[schedule]\ncron = '0 * * * *'\n", "cron = '0 * * * *'", "a.toml"),
            ("schedule:\n  cron: '0 * * * *'\n  auto_enabled: false\n", "schedule", "a.yml"),
            ("schedule:\n  cron: '0 * * * *'\n  enabled: falsey\n", "schedule", "a.yml"),
        ):
            with self.subTest(quote=quote):
                self.assertIsNone(check(quote, registration, path))

    def test_enabled_false_in_a_comment_is_ignored(self):
        self.assertIsNone(check("schedule:  # enabled: false\n  cron: '0 * * * *'\n", "schedule", "a.yml"))
        self.assertIsNone(check("schedule:\n  # enabled: false\n  cron: '0 * * * *'\n", "schedule", "a.yml"))

    def test_other_live_occurrence_rescues_a_manual_one(self):
        quote = "legacy:\n  schedule:\n    enabled: false\ncurrent:\n  schedule:\n    cron: '0 * * * *'\n"
        self.assertIsNone(check(quote, "schedule", "a.yml"))
        self.rejected("legacy:\n  schedule:\n    enabled: false\n", "schedule", "a.yml")


if __name__ == "__main__":
    unittest.main()
