"""Synthetic-only regression tests for the configured blind submission adapter.

Every repository, trace and evaluation below is authored by this test. No model,
network service, real assessment, source export or historical report is read.
The intentionally uniform judgments are protocol fixtures, not readiness claims.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from agent_readiness.report import latest_report, new_evaluation, read_json
from agent_readiness.repository import inspect_repository


SERVER = Path(__file__).resolve().parents[1] / "tools" / "blind_submit_mcp.py"
SPEC = importlib.util.spec_from_file_location("synthetic_blind_submit_mcp", SERVER)
ADAPTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTER)


class SyntheticBlindSubmitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="synthetic-blind-submit-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.target = self.root / "synthetic-repository"
        self.target.mkdir()
        self.readme = self.target / "README.md"
        self.readme.write_text("# Synthetic submission fixture\nNo real assessment.\n", encoding="utf-8")
        self.outside = self.root / "synthetic-outside.txt"
        self.outside.write_text("Synthetic path escape fixture only.\n", encoding="utf-8")
        (self.target / "escape").symlink_to(self.outside)
        subprocess.run(["git", "init", "-q", str(self.target)], check=True, capture_output=True)
        self.template = new_evaluation(inspect_repository(self.target))
        self.evaluation = copy.deepcopy(self.template)
        self.evaluation["apps"] = {".": {"description": "Synthetic adapter test fixture"}}
        for key, value in self.evaluation["report"].items():
            value.update(numerator=0, denominator=1, rationale="Synthetic interface fixture; not a project assessment.")
            self.evaluation["evidence"][key] = [{"kind": "file", "path": "README.md", "line": 1,
                                                "note": "Synthetic README fixture authored for this test."}]
        self.ids = list(self.evaluation["report"])
        self.assertEqual(len(self.ids), 84)
        self.template_path = self.root / "synthetic-template.json"
        self.template_path.write_text(json.dumps(self.template), encoding="utf-8")
        self.trace = self.root / "synthetic-trace.jsonl"
        self.write_trace()
        self.budget = self.root / "synthetic-budget.json"
        self.budget.write_text(json.dumps({"deadlineEpoch": time.time() + 600}), encoding="utf-8")
        self.config = {"target": str(self.target), "template": str(self.template_path),
                       "output": str(self.root / "synthetic-submissions"),
                       "state_dir": str(self.root / "synthetic-report-state"),
                       "trace": str(self.trace), "budget": str(self.budget)}
        self.gate = ADAPTER.Gate(self.config)

    def write_trace(self, *, path="README.md", is_error=False):
        records = [
            {"type": "tool_call", "id": "synthetic-read-1", "toolName": "blind___read_text_file",
             "parameters": {"path": "/input/" + path}},
            {"type": "tool_result", "id": "synthetic-read-1", "isError": is_error,
             "content": [{"type": "text", "text": "Synthetic README fixture."}]},
        ]
        self.trace.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")

    def submit(self, data=None, *, gate=None):
        return (gate or self.gate).submit({"evaluation_json": json.dumps(self.evaluation if data is None else data)})

    def assert_rejected(self, response, message=None):
        self.assertFalse(response["accepted"], response)
        if message:
            self.assertIn(message, response.get("error", ""), response)
        self.assertFalse((Path(self.config["output"]) / "accepted.json").exists())
        self.assertFalse(Path(self.config["state_dir"]).exists())

    def test_missing_79_criteria_returns_complete_diagnostics(self):
        data = copy.deepcopy(self.evaluation)
        for field in ("report", "evidence"):
            data[field] = {key: data[field][key] for key in self.ids[:5]}
        answer = self.submit(data)
        self.assert_rejected(answer, "exactly 84 criteria")
        self.assertEqual(answer["diagnostics"]["report"]["count"], 5)
        self.assertEqual(len(answer["diagnostics"]["report"]["missing"]), 79)
        self.assertEqual(len(answer["diagnostics"]["evidence"]["missing"]), 79)
        self.assertEqual(answer["remainingSubmissions"], 2)
        self.assertFalse(answer["stop"])
        self.assertEqual(read_json(Path(self.config["output"]) / "attempt-01" / "response.json"), answer)

    def test_placeholder_key_cannot_stand_for_remaining_criteria(self):
        data = copy.deepcopy(self.evaluation)
        data["report"]["remaining_criteria"] = data["report"].pop(self.ids[0])
        answer = self.submit(data)
        self.assert_rejected(answer)
        self.assertEqual(answer["diagnostics"]["report"]["extra"], ["remaining_criteria"])
        self.assertEqual(answer["diagnostics"]["report"]["missing"], [self.ids[0]])

    def test_empty_evidence_is_rejected(self):
        data = copy.deepcopy(self.evaluation)
        data["evidence"][self.ids[0]] = []
        answer = self.submit(data)
        self.assert_rejected(answer, "at least one evidence record")
        self.assertIn(self.ids[0], answer["diagnostics"]["emptyEvidence"])

    def test_complete_synthetic_84_stores_and_reads_back_with_original_validator(self):
        answer = self.submit()
        self.assertTrue(answer["accepted"], answer)
        self.assertTrue(answer["stop"])
        saved = latest_report(inspect_repository(self.target), self.config["state_dir"])
        self.assertEqual(saved["report"], self.evaluation["report"])
        self.assertEqual(saved["evidence"], self.evaluation["evidence"])
        self.assertEqual(saved["repository"], self.evaluation["repository"])
        self.assertNotIn("comparison", saved)
        self.assertEqual(len(saved["report"]), 84)
        self.assertEqual(len(saved["evidence"]), 84)
        self.assertEqual(saved["summary"]["scorePercent"], 0)
        self.assertTrue(Path(answer["report"]).is_relative_to(Path(self.config["state_dir"])))
        self.assertIn("Synthetic interface fixture", Path(answer["markdown"]).read_text())
        self.assertEqual(inspect_repository(self.target), self.template["repository"])

    def test_failed_submission_can_be_corrected_in_same_gate(self):
        bad = copy.deepcopy(self.evaluation)
        bad["evidence"][self.ids[0]] = []
        self.assert_rejected(self.submit(bad))
        answer = self.submit()
        self.assertTrue(answer["accepted"], answer)
        self.assertEqual(answer["attempt"], 2)
        self.assertEqual(answer["remainingSubmissions"], 1)

    def test_third_failure_stops_and_fourth_does_not_create_attempt(self):
        for number in range(1, 4):
            answer = self.gate.submit({"evaluation_json": "{}"})
            self.assertEqual(answer["attempt"], number)
            self.assertEqual(answer["stop"], number == 3)
        fourth = self.submit()
        self.assert_rejected(fourth)
        self.assertTrue(fourth["stop"])
        self.assertEqual(fourth["reason"], "submission_budget_exhausted")
        self.assertEqual(fourth["attemptsUsed"], 3)
        self.assertFalse((Path(self.config["output"]) / "attempt-04").exists())

    def test_attempt_budget_survives_gate_reconstruction(self):
        for number in range(1, 4):
            answer = ADAPTER.Gate(self.config).submit({"evaluation_json": "{}"})
            self.assertEqual(answer["attempt"], number)
        self.assertEqual(ADAPTER.Gate(self.config).submit({"evaluation_json": "{}"})["reason"],
                         "submission_budget_exhausted")

    def test_null_budget_allows_more_than_three_failures_then_valid_submission(self):
        self.budget.write_text(json.dumps({"deadlineEpoch": None, "maxSubmissions": None}), encoding="utf-8")
        for number in range(1, 5):
            answer = ADAPTER.Gate(self.config).submit({"evaluation_json": "{}"})
            self.assert_rejected(answer, "top-level")
            self.assertEqual(answer["attempt"], number)
            self.assertIsNone(answer["remainingSubmissions"])
            self.assertFalse(answer["stop"])
        accepted = self.submit(gate=ADAPTER.Gate(self.config))
        self.assertTrue(accepted["accepted"], accepted)
        self.assertEqual(accepted["attempt"], 5)
        self.assertIsNone(accepted["remainingSubmissions"])
        self.assertTrue(accepted["stop"])
        self.assertEqual(len(latest_report(inspect_repository(self.target), self.config["state_dir"])["report"]), 84)

    def test_configured_submission_limit_replaces_legacy_three(self):
        self.budget.write_text(json.dumps({"deadlineEpoch": None, "maxSubmissions": 5}), encoding="utf-8")
        for number in range(1, 6):
            answer = ADAPTER.Gate(self.config).submit({"evaluation_json": "{}"})
            self.assertEqual(answer["attempt"], number)
            self.assertEqual(answer["remainingSubmissions"], 5 - number)
            self.assertEqual(answer["stop"], number == 5)
        rejected = self.submit()
        self.assert_rejected(rejected)
        self.assertEqual(rejected["reason"], "submission_budget_exhausted")
        self.assertEqual(rejected["attemptsUsed"], 5)
        self.assertFalse((Path(self.config["output"]) / "attempt-06").exists())

    def test_expired_deadline_still_applies_without_submission_cap(self):
        self.budget.write_text(json.dumps({"deadlineEpoch": time.time() - 1, "maxSubmissions": None}), encoding="utf-8")
        answer = self.submit()
        self.assert_rejected(answer)
        self.assertTrue(answer["stop"])
        self.assertEqual(answer["reason"], "deadline_exceeded")
        self.assertIsNone(answer["remainingSubmissions"])
        self.assertEqual(list(Path(self.config["output"]).glob("attempt-*")), [])

    def test_attempt_numbers_continue_after_99_without_overwriting_existing_attempts(self):
        self.budget.write_text(json.dumps({"deadlineEpoch": None, "maxSubmissions": None}), encoding="utf-8")
        output = Path(self.config["output"])
        for number in (99, 100):
            attempt = output / f"attempt-{number}"
            attempt.mkdir()
            (attempt / "preserve.txt").write_text(f"Synthetic existing attempt {number}\n", encoding="utf-8")
        answer = self.gate.submit({"evaluation_json": "{}"})
        self.assertEqual(answer["attempt"], 101)
        self.assertFalse(answer["stop"])
        self.assertTrue((output / "attempt-101" / "response.json").is_file())
        for number in (99, 100):
            self.assertEqual((output / f"attempt-{number}" / "preserve.txt").read_text(),
                             f"Synthetic existing attempt {number}\n")

    def test_invalid_budget_values_reject_without_reserving_attempt(self):
        invalid = [{"maxSubmissions": value} for value in (0, -1, 1.5, True, "3")]
        invalid += [{"deadlineEpoch": value} for value in (True, "future", float("inf"), float("nan"))]
        for value in invalid:
            with self.subTest(configured=value):
                self.budget.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.submit()
                self.assertEqual(list(Path(self.config["output"]).glob("attempt-*")), [])
                self.assertFalse(Path(self.config["state_dir"]).exists())

    def test_nonobject_budget_is_a_validation_error_not_a_server_crash(self):
        for value in ([], None, 3, "invalid"):
            with self.subTest(configured=value):
                self.budget.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.submit()
                self.assertEqual(list(Path(self.config["output"]).glob("attempt-*")), [])

    def test_successful_report_cannot_be_overwritten_by_later_submission(self):
        first = self.submit()
        self.assertTrue(first["accepted"], first)
        state = Path(self.config["state_dir"])
        before = {str(path.relative_to(state)): path.read_bytes() for path in state.rglob("*") if path.is_file()}
        again = ADAPTER.Gate(self.config).submit({"evaluation_json": "{}"})
        after = {str(path.relative_to(state)): path.read_bytes() for path in state.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertTrue(again["stop"])
        self.assertEqual(again["reason"], "already_accepted")
        self.assertEqual(again["attemptsUsed"], 1)

    def test_expired_budget_rejects_before_reserving_or_storing(self):
        self.budget.write_text(json.dumps({"deadlineEpoch": time.time() - 1}), encoding="utf-8")
        answer = self.submit()
        self.assert_rejected(answer)
        self.assertEqual(answer["reason"], "deadline_exceeded")
        self.assertEqual(answer["attemptsUsed"], 0)
        self.assertEqual(list(Path(self.config["output"]).glob("attempt-*")), [])

    def test_parallel_calls_cannot_exceed_three_attempts(self):
        with ThreadPoolExecutor(max_workers=5) as executor:
            answers = list(executor.map(lambda _: ADAPTER.Gate(self.config).submit({"evaluation_json": "{}"}), range(5)))
        self.assertEqual(sorted(answer["attempt"] for answer in answers if "attempt" in answer), [1, 2, 3])
        self.assertEqual(sum(answer.get("reason") == "submission_budget_exhausted" for answer in answers), 2)
        self.assertEqual(len(list(Path(self.config["output"]).glob("attempt-*"))), 3)

    def test_argument_path_and_command_injection_is_rejected(self):
        answer = self.gate.submit({"evaluation_json": json.dumps(self.evaluation), "target": "/tmp/other",
                                   "state_dir": "/tmp/other-state", "command": "touch /tmp/forbidden"})
        self.assert_rejected(answer, "Only evaluation_json")

    def test_top_level_output_path_injection_is_rejected(self):
        data = copy.deepcopy(self.evaluation)
        data["state_dir"] = str(self.root / "injected-state")
        self.assert_rejected(self.submit(data), "top-level")
        self.assertFalse((self.root / "injected-state").exists())

    def test_repository_root_metadata_cannot_be_replaced(self):
        data = copy.deepcopy(self.evaluation)
        data["repository"]["root"] = str(self.root)
        self.assert_rejected(self.submit(data), "repository must equal")

    def test_repository_extra_metadata_cannot_be_injected(self):
        data = copy.deepcopy(self.evaluation)
        data["repository"]["command"] = "synthetic-do-not-execute"
        self.assert_rejected(self.submit(data), "repository must equal")

    def test_unknown_evidence_command_field_is_rejected(self):
        data = copy.deepcopy(self.evaluation)
        data["evidence"][self.ids[0]][0]["command"] = "synthetic-do-not-execute"
        self.assert_rejected(self.submit(data))

    def test_unknown_application_field_is_rejected(self):
        data = copy.deepcopy(self.evaluation)
        data["apps"]["."]["state_dir"] = str(self.root / "injected-state")
        self.assert_rejected(self.submit(data))

    def test_command_evidence_is_rejected_without_execution(self):
        marker = self.root / "command-must-not-run"
        data = copy.deepcopy(self.evaluation)
        data["evidence"][self.ids[0]] = [{"kind": "command", "command": "touch " + str(marker),
                                        "cwd": ".", "exitCode": 0, "output": "Synthetic fixture only"}]
        self.assert_rejected(self.submit(data), "command evidence")
        self.assertFalse(marker.exists())

    def test_absolute_parent_git_windows_and_symlink_paths_are_rejected(self):
        for index, path in enumerate((str(self.outside), "../synthetic-outside.txt", ".git/config",
                                      "C:\\synthetic\\outside.txt", "escape")):
            with self.subTest(path=path):
                config = dict(self.config, output=str(self.root / f"synthetic-path-attempt-{index}"))
                data = copy.deepcopy(self.evaluation)
                data["evidence"][self.ids[0]][0]["path"] = path
                self.write_trace(path=path)
                self.assert_rejected(self.submit(data, gate=ADAPTER.Gate(config)))

    def test_unread_file_evidence_is_rejected(self):
        self.trace.write_text("", encoding="utf-8")
        self.assert_rejected(self.submit(), "not read successfully")

    def test_all_unread_criterion_paths_are_returned_in_one_feedback(self):
        extra = self.target / "synthetic-extra.md"
        extra.write_text("Synthetic additional evidence only.\n", encoding="utf-8")
        self.template = new_evaluation(inspect_repository(self.target))
        self.template_path.write_text(json.dumps(self.template), encoding="utf-8")
        self.evaluation["repository"] = copy.deepcopy(self.template["repository"])
        self.evaluation["evidence"][self.ids[0]].append(
            {"kind": "file", "path": "synthetic-extra.md", "line": 1,
             "note": "Synthetic second evidence file authored for this test."})
        self.trace.write_text("", encoding="utf-8")
        answer = self.submit(gate=ADAPTER.Gate(self.config))
        self.assert_rejected(answer, "not read successfully")
        missing = answer["diagnostics"]["unreadFileEvidence"]
        expected = [{"criterionId": key, "path": item["path"]}
                    for key, records in self.evaluation["evidence"].items() for item in records]
        self.assertEqual(missing, expected)
        self.assertEqual(len(missing), 85)
        self.assertEqual({entry["criterionId"] for entry in missing}, set(self.ids))
        self.assertIn(f"{self.ids[0]}: synthetic-extra.md", answer["error"])
        self.assertIn(f"{self.ids[-1]}: README.md", answer["error"])
        self.assertFalse(answer["stop"])

    def test_successfully_read_files_are_excluded_from_all_unread_feedback(self):
        extra = self.target / "synthetic-unread.md"
        extra.write_text("Synthetic unread evidence only.\n", encoding="utf-8")
        self.template = new_evaluation(inspect_repository(self.target))
        self.template_path.write_text(json.dumps(self.template), encoding="utf-8")
        self.evaluation["repository"] = copy.deepcopy(self.template["repository"])
        for key in (self.ids[0], self.ids[-1]):
            self.evaluation["evidence"][key].append(
                {"kind": "file", "path": "synthetic-unread.md", "line": 1,
                 "note": "Synthetic unread fixture authored for this test."})
        answer = self.submit(gate=ADAPTER.Gate(self.config))
        self.assert_rejected(answer, "not read successfully")
        self.assertEqual(answer["diagnostics"]["unreadFileEvidence"],
                         [{"criterionId": key, "path": "synthetic-unread.md"}
                          for key in (self.ids[0], self.ids[-1])])

    def test_failed_read_does_not_establish_evidence_provenance(self):
        self.write_trace(is_error=True)
        self.assert_rejected(self.submit(), "not read successfully")

    def test_multiple_read_success_header_establishes_file_provenance(self):
        records = [
            {"type": "tool_call", "id": "synthetic-multi-1", "toolName": "blind___read_multiple_files",
             "parameters": {"paths": ["/input/README.md"]}},
            {"type": "tool_result", "id": "synthetic-multi-1", "isError": False,
             "value": "/input/README.md:\nSynthetic README fixture.\n"},
        ]
        self.trace.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
        self.assertEqual(self.gate.read_paths(), {"README.md"})
        answer = self.submit()
        self.assertTrue(answer["accepted"], answer)

    def test_multiple_read_per_file_error_or_truncated_header_cannot_establish_provenance(self):
        values = (
            "/input/README.md: Error - synthetic permission denied\n",
            "Synthetic earlier file content... [4096 bytes cut]",
        )
        for value in values:
            with self.subTest(value=value):
                records = [
                    {"type": "tool_call", "id": "synthetic-multi-1", "toolName": "blind___read_multiple_files",
                     "parameters": {"paths": ["/input/README.md"]}},
                    {"type": "tool_result", "id": "synthetic-multi-1", "isError": False, "value": value},
                ]
                self.trace.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
                self.assertEqual(self.gate.read_paths(), set())
                self.assert_rejected(self.submit(), "not read successfully")

    def test_repository_mutation_after_prepare_is_rejected(self):
        self.readme.write_text("Synthetic changed after snapshot.\n", encoding="utf-8")
        self.assert_rejected(self.submit(), "changed since prepare")

    def test_duplicate_json_key_is_rejected_by_existing_parser(self):
        text = json.dumps(self.evaluation).replace('"numerator": 0', '"numerator": 0, "numerator": 1', 1)
        answer = self.gate.submit({"evaluation_json": text})
        self.assert_rejected(answer, "Duplicate JSON key")

    def test_nonfinite_json_number_is_rejected_by_existing_parser(self):
        text = json.dumps(self.evaluation).replace('"numerator": 0', '"numerator": NaN', 1)
        answer = self.gate.submit({"evaluation_json": text})
        self.assert_rejected(answer, "Non-finite JSON number")

    def test_real_stdio_protocol_exposes_feedback_and_bounded_tool_schema(self):
        config_path = self.root / "synthetic-mcp-config.json"
        config_path.write_text(json.dumps(self.config), encoding="utf-8")
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "submit_evaluation", "arguments": {"evaluation_json": "{}"}}},
        ]
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("READINESS_MODEL_", "FACTORY_"))}
        process = subprocess.run([sys.executable, str(SERVER), "--config", str(config_path)],
                                 input="".join(json.dumps(row) + "\n" for row in requests), text=True,
                                 capture_output=True, timeout=30, env=environment)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stderr, "")
        responses = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual([row["id"] for row in responses], [1, 2, 3])
        tools = responses[1]["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["submit_evaluation"])
        self.assertEqual(set(tools[0]["inputSchema"]["properties"]), {"evaluation_json"})
        self.assertFalse(tools[0]["inputSchema"]["additionalProperties"])
        feedback = responses[2]["result"]
        self.assertTrue(feedback["isError"])
        answer = json.loads(feedback["content"][0]["text"])
        self.assert_rejected(answer, "top-level")
        self.assertEqual(answer["attempt"], 1)
        self.assertEqual(answer["remainingSubmissions"], 2)
        self.assertFalse(answer["stop"])

    def test_stdio_invalid_budget_returns_addressed_error_and_keeps_protocol_alive(self):
        self.budget.write_text("[]\n", encoding="utf-8")
        config_path = self.root / "synthetic-invalid-budget-config.json"
        config_path.write_text(json.dumps(self.config), encoding="utf-8")
        requests = [
            {"jsonrpc": "2.0", "id": 17, "method": "tools/call",
             "params": {"name": "submit_evaluation", "arguments": {"evaluation_json": "{}"}}},
            {"jsonrpc": "2.0", "id": 18, "method": "ping"},
        ]
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("READINESS_MODEL_", "FACTORY_"))}
        process = subprocess.run([sys.executable, str(SERVER), "--config", str(config_path)],
                                 input="".join(json.dumps(row) + "\n" for row in requests), text=True,
                                 capture_output=True, timeout=30, env=environment)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stderr, "")
        responses = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual([row["id"] for row in responses], [17, 18])
        self.assertTrue(responses[0]["result"]["isError"])
        feedback = json.loads(responses[0]["result"]["content"][0]["text"])
        self.assertFalse(feedback["accepted"])
        self.assertTrue(feedback["stop"])
        self.assertEqual(feedback["reason"], "controller_configuration_error")
        self.assertIn("JSON object", feedback["error"])
        self.assertEqual(responses[1]["result"], {})
        self.assertEqual(list(Path(self.config["output"]).glob("attempt-*")), [])

    def test_output_inside_target_is_rejected_without_creating_it(self):
        before = {path.relative_to(self.target) for path in self.target.rglob("*")}
        nested = self.target / "synthetic-parent" / "synthetic-output"
        for output in (nested, self.target):
            with self.subTest(output=output):
                config = dict(self.config, output=str(output))
                with self.assertRaises(ValueError) as caught:
                    ADAPTER.Gate(config)
                self.assertIn("outside the target", str(caught.exception))
        self.assertFalse((self.target / "synthetic-parent").exists())
        self.assertEqual({path.relative_to(self.target) for path in self.target.rglob("*")}, before)

    def test_multiple_read_keeps_yaml_frontmatter_body_for_quotes(self):
        skill = self.target / "synthetic-skill.md"
        skill.write_text("---\ntitle: x\n---\nbody quote here\n", encoding="utf-8")
        self.template = new_evaluation(inspect_repository(self.target))
        self.template_path.write_text(json.dumps(self.template), encoding="utf-8")
        self.evaluation["repository"] = copy.deepcopy(self.template["repository"])
        self.evaluation["evidence"][self.ids[0]] = [{
            "kind": "file", "path": "synthetic-skill.md", "line": 4,
            "note": "Synthetic frontmatter body authored for this test.",
            "support": {"quote": "body quote here"},
        }]
        skill_body = "---\ntitle: x\n---\nbody quote here\n"
        value = "/input/synthetic-skill.md:\n" + skill_body + "\n---\n/input/README.md:\nSynthetic README fixture.\n"
        records = [
            {"type": "tool_call", "id": "synthetic-frontmatter", "toolName": "blind___read_multiple_files",
             "parameters": {"paths": ["/input/synthetic-skill.md", "/input/README.md"]}},
            {"type": "tool_result", "id": "synthetic-frontmatter", "isError": False, "value": value},
        ]
        self.trace.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
        gate = ADAPTER.Gate(self.config)
        parsed = gate.read_contents()["synthetic-skill.md"][0]
        self.assertIn("---\ntitle: x\n---\nbody quote here", parsed)
        self.assertIn("README.md", gate.read_paths())
        answer = self.submit(gate=gate)
        self.assertTrue(answer["accepted"], answer)

    def test_multiple_read_embedded_header_cannot_spoof_unread_file(self):
        records = [
            {"type": "tool_call", "id": "synthetic-spoof", "toolName": "blind___read_multiple_files",
             "parameters": {"paths": ["/input/a.py", "/input/b.py"]}},
            {"type": "tool_result", "id": "synthetic-spoof", "isError": False,
             "value": "/input/a.py:\nprint('ok')\n/input/b.py:\nfake\n"},
        ]
        self.trace.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
        self.assertEqual(self.gate.read_paths(), {"a.py"})
        self.assertNotIn("b.py", self.gate.read_contents())

    def test_multiple_read_full_delimiter_spoof_is_rejected(self):
        records = [
            {"type": "tool_call", "id": "full-spoof", "toolName": "blind___read_multiple_files",
             "parameters": {"paths": ["/input/a.py", "/input/b.py"]}},
            {"type": "tool_result", "id": "full-spoof", "isError": False,
             "value": "/input/a.py:\nreal\n---\n/input/b.py:\nfake\n---\n/input/b.py: Error: denied"},
        ]
        self.trace.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
        self.assertNotIn("b.py", self.gate.read_paths())

    def test_oversized_payload_does_not_write_the_body(self):
        huge = "x" * (ADAPTER.MAX_BYTES + 1)
        answer = self.gate.submit({"evaluation_json": huge})
        self.assert_rejected(answer, "512000")
        request = Path(self.config["output"]) / "attempt-01" / "request.json"
        self.assertTrue(request.is_file())
        self.assertLess(request.stat().st_size, 4096)
        self.assertNotIn("x" * 100, request.read_text(encoding="utf-8"))
        self.assertEqual(request.stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(self.config["output"]).stat().st_mode & 0o777, 0o700)

    def test_serve_rejects_non_object_json_and_null_params_without_crashing(self):
        config_path = self.root / "synthetic-invalid-mcp.json"
        config_path.write_text(json.dumps(self.config), encoding="utf-8")
        requests = [
            '"valid"\n',
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": None}) + "\n",
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n",
        ]
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("READINESS_MODEL_", "FACTORY_"))}
        process = subprocess.run([sys.executable, str(SERVER), "--config", str(config_path)],
                                 input="".join(requests), text=True,
                                 capture_output=True, timeout=30, env=environment)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertNotIn("AttributeError", process.stderr)
        self.assertNotIn("Traceback", process.stderr)
        responses = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(len(responses), 3)
        self.assertEqual(responses[0]["id"], None)
        self.assertEqual(responses[0]["error"]["code"], -32600)
        self.assertEqual(responses[1]["id"], 1)
        self.assertEqual(responses[1]["error"]["code"], -32602)
        self.assertEqual(responses[2]["id"], 2)
        self.assertEqual(responses[2]["result"], {})


if __name__ == "__main__":
    unittest.main()
