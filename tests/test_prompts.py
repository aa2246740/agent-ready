"""Regressions for decisions recovered from Droid 0.240.0's lN/C/oZ/y.

Source: the original chunk at byte offset 186035557 (SHA-256
7b46b0fc3864aa48ac66f426d5a8d90403fbd95c95b3e5f81d92fa8a295aedae).
These are prompt-contract tests, not claims of a real model assessment.
"""

from copy import deepcopy
from pathlib import Path
import re
import unittest

from agent_readiness.catalog import load_catalog
from agent_readiness.condition_evidence import INSTRUCTIONS as CONDITION_INSTRUCTIONS
from agent_readiness.prompts import (
    LOCAL_ADAPTER_HEADING, assessment_prompt, fix_prompt, fix_without_report_prompt,
)
from agent_readiness.report import ReportError


SKILLS = Path(__file__).resolve().parents[1] / "skills"


class PromptContractTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog()
        self.root = "/synthetic/repository"
        self.repository = {
            "root": self.root, "identity": "local:" + self.root,
            "head": "a" * 40, "fingerprint": "b" * 64, "dirty": False,
        }
        self.previous = {
            "id": "synthetic-previous-report",
            "createdAt": "2026-01-02T03:04:05Z",
            "repository": deepcopy(self.repository),
            "apps": {".": {"description": "Synthetic prompt-contract fixture"}},
            "report": {
                row["id"]: {"numerator": 1, "denominator": 1,
                            "rationale": "Synthetic prior judgment, not an actual readiness result."}
                for row in self.catalog["criteria"]
            },
            "evidence": {row["id"]: [] for row in self.catalog["criteria"]},
        }
        self.previous["report"]["lint_config"]["numerator"] = 0
        self.previous["report"]["type_check"]["numerator"] = 0
        self.previous["report"]["strict_typing"]["numerator"] = None

    def assessment(self, previous=None):
        return assessment_prompt(self.repository, Path("/synthetic/outside-task"), previous)

    def adapter(self, prompt=None):
        prompt = prompt or self.assessment()
        return prompt[prompt.index(LOCAL_ADAPTER_HEADING):]

    def test_assessment_preserves_scan_and_application_discovery_decisions(self):
        # Original lines 84–183: bounded source discovery and applications first.
        prompt = self.assessment()
        for expected in (
            "Detect the repository language", "Ruby", "Gemfile", "Cargo.toml", "go.mod",
            "below 200", "BEFORE focused source searches", "unbounded **/*.java",
            "EVERY criterion", "end users or other systems", "moved to its own repository",
            "independent run OR deployment", "alternate root build frontends",
            "count '.' as one application", "APPLICATIONS_IDENTIFIED: N",
            "denominator N", "denominator 1",
        ):
            with self.subTest(requirement=expected):
                self.assertIn(expected, prompt)
        self.assertLess(prompt.index("APPLICATIONS_IDENTIFIED: N"), prompt.index("## Phase 3 - Criterion Evaluation"))
        self.assertIn("CURRENT working tree", prompt)  # Explicit local adaptation, no checkout.

    def test_assessment_follows_native_phase_order_with_one_local_adapter_section(self):
        prompt = self.assessment()
        headings = [
            "## Phase 1 - Repository Scan", "## Phase 2 - Application Discovery",
            "## Phase 3 - Criterion Evaluation", "## Phase 4 - Report Validation",
            "## Phase 5 - Scoring & Report Generation", "## Behavioral Guidelines", LOCAL_ADAPTER_HEADING,
        ]
        positions = [prompt.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(prompt.count(LOCAL_ADAPTER_HEADING), 1)
        self.assertIn("Local adapter", prompt[:positions[0]])  # Pointer before the phases.
        self.assertEqual(prompt.count(CONDITION_INSTRUCTIONS), 1)
        self.assertIn(CONDITION_INSTRUCTIONS, self.adapter())

    def test_all_84_criteria_are_emitted_verbatim_grouped_by_scope(self):
        prompt = self.assessment()
        rows = re.findall(r"^- \*\*([a-z_]+)\*\* \(Level (\d)\)( \[Skippable\])?: ", prompt, re.MULTILINE)
        self.assertEqual(len(rows), 84)
        self.assertEqual({row[0] for row in rows}, {row["id"] for row in self.catalog["criteria"]})
        repository_ids = [row["id"] for row in self.catalog["criteria"] if row["scope"] == "repository"]
        application_ids = [row["id"] for row in self.catalog["criteria"] if row["scope"] == "application"]
        self.assertEqual((len(repository_ids), len(application_ids)), (44, 40))
        listed = [row[0] for row in rows]
        self.assertEqual(sorted(listed[:44]), sorted(repository_ids))
        self.assertEqual(sorted(listed[44:]), sorted(application_ids))
        by_id = {row["id"]: row for row in self.catalog["criteria"]}
        for key, level, skippable in rows:
            self.assertEqual(level, str(by_id[key]["level"]), key)
            self.assertEqual(bool(skippable), bool(by_id[key].get("isSkippable")), key)
        for criterion in self.catalog["criteria"]:
            with self.subTest(criterion=criterion["id"]):
                self.assertIn(f"**{criterion['id']}** (Level {criterion['level']})", prompt)
                self.assertIn(criterion["instructions"], prompt)

    def test_repository_and_application_counts_come_from_the_catalog(self):
        prompt = self.assessment()
        for expected in (
            "Repository Scope (44 criteria)", "Application Scope (40 criteria)",
            "### Repository Scope Criteria (44)", "### Application Scope Criteria (40)",
            "every one of the 40 Application Scope criteria uses denominator N",
            "every one of the 44 Repository Scope criteria uses denominator 1",
            "Fill all 84 report keys", "exactly 84 criterion keys",
        ):
            self.assertIn(expected, prompt)

    def test_skip_wording_follows_native_semantics_without_extra_discouragement(self):
        prompt = self.assessment()
        for expected in (
            "numerator null is allowed only for criteria marked [Skippable]",
            "its stated skip condition, its PREREQUISITE failing, or its stated N/A cases",
            "say which in the rationale",
            "Do not convert an applicable failure, missing access that the criterion does not list as a skip reason, or an unevaluated item into a skip",
            "There is no per-application N/A",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, prompt)
        for discouragement in ("UNASSESSED is a deliberate invalid sentinel", "rarely", "avoid skipping", "prefer failing"):
            self.assertNotIn(discouragement, prompt)

    def test_command_bans_apply_only_to_unit_tests_runnable_evidence(self):
        adapter = self.adapter()
        paragraph = next(line for line in adapter.splitlines() if line.startswith("unit_tests_runnable local checks"))
        self.assertIn("ONLY for that criterion's positive evidence", paragraph)
        self.assertIn("other criteria keep their native commands, pipes included", paragraph)
        for expected in ("no pipe", "`2>&1`", "head", "tail", "tee", "timeoutSeconds > 0", "exitCode 0",
                         "`cargo test -p x --lib -- --list`", "`go test -list . ./pkg`", "`ctest -N`",
                         "Compile-only", "PYTHONPATH must stay inside the target", "`uv run`"):
            self.assertIn(expected, paragraph)
        # The bans are not repeated as a global rule anywhere else in the local adapter.
        rest = adapter.replace(paragraph, "")
        for banned in ("`2>&1`", "head", "tee"):
            self.assertNotIn(banned, rest)

    def test_v2_cwd_rule_accepts_real_directories_and_keeps_the_cd(self):
        adapter = self.adapter()
        for expected in (
            "cwd is the directory the command really ran in",
            "target-relative ('.' = target root) or absolute",
            "record that absolute start directory and keep `cd /abs/target && ...` in `command`",
            "never invent a directory or drop the cd",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, adapter)
        prompt = self.assessment()
        for stale in ("Never relabel", "initial working directory", "cannot represent", "schema limitation"):
            self.assertNotIn(stale, prompt)

    def test_local_adapter_covers_template_denominators_hygiene_and_report(self):
        adapter = self.adapter()
        for expected in (
            "evidence[id] = [records]", "at least one record per criterion", "UNASSESSED",
            "`report` fills null with N = len(apps)", "a non-null value that is wrong is an error",
            "A skipped criterion keeps its denominator",
            '"application": "<app dir>"',
            "PYTHONDONTWRITEBYTECODE=1", "-p no:cacheprovider", "CARGO_TARGET_DIR", "mktemp -d",
            "never record tokens or env dumps", "--jq", "NO_COLOR=1 CLICOLOR_FORCE=0", "Quote globs",
            "TARGET's own entry point", "It lists ALL problems at once", "fix them all and re-run",
            "--state-dir", "outside the target", "no dashboard URL, no Level 6", "prepare again",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, adapter)
        self.assertNotIn("echo =====", self.assessment())

    def test_size_budgets_keep_instructions_short(self):
        prompt = self.assessment()
        adapter = self.adapter(prompt)
        own_local_text = adapter.replace(CONDITION_INSTRUCTIONS, "")
        self.assertLessEqual(len(own_local_text.encode()), 3800, "local adapter prose outside the condition block")
        self.assertLessEqual(len(adapter.encode()), 3800 + len(CONDITION_INSTRUCTIONS.encode()))
        self.assertLessEqual(len(prompt.encode()), 64 * 1024)
        for name, limit in (("agent-readiness", 2560), ("readiness-report", 4096), ("readiness-fix", 4096)):
            with self.subTest(skill=name):
                self.assertLessEqual(len((SKILLS / name / "SKILL.md").read_bytes()), limit)

    def test_skills_are_short_routers_pointing_to_the_generated_assessment(self):
        for name in ("agent-readiness", "readiness-report", "readiness-fix"):
            text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=name):
                self.assertTrue(text.startswith(f"---\nname: {name}\ndescription: "))
                self.assertIn("READINESS_CLI", text)
                self.assertNotIn("echo =====", text)
                for stale in ("never relabel", "Never relabel", "不能把字段改成 `.`"):
                    self.assertNotIn(stale, text)
        report = (SKILLS / "readiness-report" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("assessment.md", report)
        self.assertIn("Local adapter", report)
        self.assertIn("一次列出全部问题", report)
        self.assertNotIn("timeout", report)  # The unit-test command bans live in assessment.md only.

    def test_assessment_contains_every_original_rule_without_replacing_its_contract(self):
        prompt = self.assessment()
        for expected in (
            "Prefer existence checks over deep semantic analysis",
            "bounded list, collection, or test command required by each criterion",
            "re-read each unit_tests_runnable command and its output",
            "STOP and revise before publishing", "exact signal-weighted mean",
            "# Level", "# Applications", "# Criteria", "# Action Items",
            "There is no Level 6", "replacing Factory's cloud store",
            "Level 1: 0–20%, Level 2: 20–40%, Level 3: 40–60%, Level 4: 60–80%, Level 5: 80–100%",
        ):
            self.assertIn(expected, prompt)

    def test_previous_report_preserves_context_and_requires_explained_changes(self):
        # Original C(), lines 9–35: prior state is evidence, never a copied answer.
        self.previous["repository"]["dirty"] = True
        original = deepcopy(self.previous)
        prompt = self.assessment(self.previous)
        for expected in (
            self.previous["createdAt"], "a" * 40, "had uncommitted local changes",
            "Previous applications:", "Synthetic prompt-contract fixture",
            "Previous criterion results:", "Synthetic prior judgment",
            "Only add/remove applications when repository structure clearly changed",
            "Re-verify EVERY signal", "WHERE to look, not WHAT to conclude",
            "preserve the same score", "affected criterion's rationale",
            "Unchanged files/configuration must produce the same conclusion",
            "list only criteria or applications that changed; omit unchanged items",
        ):
            self.assertIn(expected, prompt)
        self.assertLess(prompt.index("## Previous Report Reference"), prompt.index("## Phase 1 - Repository Scan"))
        self.assertEqual(self.previous, original)
        self.assertEqual(prompt, self.assessment(self.previous))
        self.assertNotIn("Previous Report Reference", self.assessment())

    def test_agent_action_items_are_specific_evidence_grounded_local_extensions(self):
        # Native lN lines 292–305 requires 2–3 specific, achievable next steps.
        adapter = self.adapter()
        self.assertIn("actionItems (optional)", adapter)
        self.assertIn("2–3 high-impact, specific, achievable", adapter)
        for field in ('"criterionId"', '"action"', '"reason"'):
            self.assertIn(field, adapter)
        self.assertIn("only for failing criteria (partial application failures included)", adapter)
        self.assertIn("never passing or skipped ones", adapter)
        self.assertIn("Fewer than two failures: only that many; none: leave empty", adapter)

    def test_existing_report_default_selection_has_names_scores_and_only_failing_categories(self):
        # Original oZ report/no-userArgs branch, lines 504–521.
        prompt = fix_prompt(self.repository, self.previous)
        for expected in (
            "Selection (native default)", "ONLY categories with at least one failing signal",
            "single question", "name and current numerator/denominator score",
            "single signal", "never say 'select all that apply'",
            "Respect an existing user selection or authorization",
        ):
            self.assertIn(expected, prompt)
        self.assertIn("(`lint_config`) [0/1]", prompt)
        self.assertIn("(`type_check`) [0/1]", prompt)
        self.assertNotIn("(`readme`)", prompt)
        for key in ("lint_config", "type_check"):
            row = next(row for row in self.catalog["criteria"] if row["id"] == key)
            self.assertIn(row["description"], prompt)
            self.assertIn(row["instructions"], prompt)

    def test_mixed_explicit_requests_explain_passing_and_skipped_items(self):
        # Original lines 497–501 require mentioning already-passing requests.
        prompt = fix_prompt(self.repository, self.previous, signals=["lint_config", "readme", "strict_typing"])
        self.assertIn("(`lint_config`) [0/1]", prompt)
        self.assertIn("`readme` already passes", prompt)
        self.assertIn("`strict_typing` was skipped", prompt)
        self.assertNotIn("(`type_check`)", prompt)
        self.assertNotIn("Selection (native default)", prompt)
        only_passed = fix_prompt(self.repository, self.previous, signals=["readme"])
        self.assertIn("`readme` already passes", only_passed)
        self.assertIn("No selected evaluated signal is failing", only_passed)
        with self.assertRaises(ReportError):
            fix_prompt(self.repository, self.previous, signals=["not_a_criterion"])

    def test_natural_language_scope_keeps_constraints_and_skips_repeated_selection(self):
        request = "Fix the cyclomatic complexity criteria, but leave CI unchanged."
        prompt = fix_prompt(self.repository, self.previous, instructions=request)
        self.assertIn(request, prompt)
        self.assertIn("IDs, names and meanings", prompt)
        self.assertIn("Fix each matching failure in sequence", prompt)
        self.assertIn("skip already-passing or unknown requests", prompt)
        self.assertNotIn("Selection (native default)", prompt)
        # Constraints remain visible even when no requested signal needs edits.
        no_op = fix_prompt(self.repository, self.previous, signals=["readme"], instructions=request)
        self.assertIn(request, no_op)

    def test_no_report_default_preserves_both_native_paths_and_embeds_full_assessment(self):
        # Original lines 522–572 embed lN and differ after report/skip selection.
        prompt = fix_without_report_prompt(self.repository)
        self.assertIn("Generate a full report first", prompt)
        self.assertIn("Skip the report and fix signals directly", prompt)
        generation_branch = prompt.split("If generating first:", 1)[1].split("If skipping the report:", 1)[0]
        self.assertIn("present the failing signals", generation_branch)
        self.assertIn("does not require another category-selection step", generation_branch)
        skip_branch = prompt.split("If skipping the report:", 1)[1].split("## Native signal catalog", 1)[0]
        self.assertIn("all categories", skip_branch)
        self.assertIn("ONE AskUser question", skip_branch)
        self.assertIn("most impactful/common ones, up to 10", skip_branch)
        self.assertIn("host tool has a smaller limit", skip_branch)
        report_workflow = prompt.split("## Readiness Report Generation Instructions", 1)[1]
        self.assertIn("First run the local prepare command", report_workflow)
        self.assertIn("APPLICATIONS_IDENTIFIED: N", report_workflow)
        for row in self.catalog["criteria"]:
            self.assertIn(row["instructions"], report_workflow)
        self.assertNotIn("readiness-report/SKILL.md", prompt)
        self.assertNotIn("Prior report:", prompt)

    def test_no_report_user_scope_fixes_directly_only_after_the_initial_path_choice(self):
        request = "Improve type safety without modifying CI."
        prompt = fix_without_report_prompt(self.repository, instructions=request)
        self.assertIn("Generate a full report first", prompt)
        self.assertIn("Skip the report and fix signals directly", prompt)
        self.assertIn(request, prompt)
        self.assertIn("semantically match the original requested IDs, names or meanings", prompt)
        self.assertIn("Do not ask for a category or signal again", prompt)
        self.assertNotIn("Ask which category of signals", prompt)
        selected = fix_without_report_prompt(self.repository, signals=["type_check"])
        catalog_section = selected.split("## Native signal catalog", 1)[1].split("## Repair quality", 1)[0]
        self.assertIn("`type_check`", catalog_section)
        self.assertNotIn("`lint_config`", catalog_section)
        with self.assertRaises(ReportError):
            fix_without_report_prompt(self.repository, signals=["not_a_criterion"])

    def test_repair_quality_applies_to_both_existing_and_absent_report(self):
        # Native y() is shared by every non-no-op fix branch, lines 454–483.
        for prompt in (fix_prompt(self.repository, self.previous), fix_without_report_prompt(self.repository)):
            with self.subTest(report_exists="Prior report:" in prompt):
                for expected in (
                    "do not refactor unrelated code", "technically pass but provide no real value",
                    "empty test.js", "ESLint configuration disabling all rules", "@ts-nocheck",
                    "meaningful unit tests", "proper TypeScript types", "verify the actual improvement",
                ):
                    self.assertIn(expected, prompt)

    def test_batch_and_target_are_explicit_adaptations_and_stale_score_is_not_success(self):
        for kwargs in ({"all_signals": True}, {"target_level": 4}):
            with self.subTest(kwargs=kwargs):
                prompt = fix_without_report_prompt(self.repository, **kwargs)
                self.assertIn("Generate a full report first", prompt)
                self.assertIn("local extension", prompt)
                self.assertNotIn("## Choose how to proceed", prompt)
        already_met = fix_prompt(self.repository, self.previous, target_level=4)
        self.assertIn("already meets", already_met)
        changed = dict(self.repository, fingerprint="c" * 64)
        stale = fix_prompt(changed, self.previous, target_level=4)
        self.assertIn("changed since the report", stale)
        self.assertNotIn("already meets", stale)
        self.assertIn("## Failing signals", stale)


if __name__ == "__main__":
    unittest.main()
