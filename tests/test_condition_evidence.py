"""Synthetic counterexamples, not claims about a real PR or deployed alert."""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent_readiness.condition_evidence import condition_errors
from agent_readiness.prompts import assessment_prompt
from agent_readiness.report import ReportError, latest_report, new_evaluation, store_report, validate_evaluation
from agent_readiness.repository import inspect_repository
from test_blind_submit_mcp import ADAPTER


class ConditionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="condition-proof-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.target = self.base / "repository"
        self.target.mkdir()
        subprocess.run(["git", "init", "-q", str(self.target)], check=True, capture_output=True)
        self.sources = {
            "README.md": "Synthetic protocol fixture; not a real project assessment.\n",
            "renovate.json": '{"extends":["config:recommended"],"updater":"renovate"}\n',
            "pr.json": '{"url":"https://forge.example/team/project/pull/7","author":"renovate[bot]","title":"Update widget dependency to 2.0"}\n',
            "monitor.py": 'def check_health():\n    if unhealthy(): notify_ops("outage")\n',
            "registration.txt": 'enabled schedule: check_health every five minutes\n',
            "sender.py": 'def notify_ops(message): send("operations", message)\n',
            "manual.txt": 'manual --once check_health: notify_ops delivered successfully\n',
            "other-registration.txt": 'enabled schedule: unrelated_check every five minutes\n',
        }
        for name, text in self.sources.items():
            (self.target / name).write_text(text)
        self.data = new_evaluation(inspect_repository(self.target))
        self.data["apps"] = {".": {"description": "Synthetic condition contract fixture"}}
        for key, row in self.data["report"].items():
            row.update(numerator=0, rationale="Synthetic schema fixture; not a real assessment.")
            self.data["evidence"][key] = [self.record("README.md")]
        self.trace = self.base / "trace.jsonl"
        self.write_trace()
        template, budget = self.base / "template.json", self.base / "budget.json"
        template.write_text(json.dumps(self.data))
        budget.write_text('{"deadlineEpoch":null,"maxSubmissions":null}')
        self.config = dict(target=str(self.target), template=str(template), budget=str(budget),
                           output=str(self.base / "attempts"), state_dir=str(self.base / "state"), trace=str(self.trace))

    def record(self, path, condition=None, **facts):
        item = {"kind": "file", "path": path, "line": 1, "note": "Synthetic test evidence"}
        if condition:
            item["support"] = {"condition": condition, "quote": self.sources[path].rstrip("\n"), "facts": facts}
        return item

    def dependency(self):
        key = "dependency_update_automation"
        self.data["report"][key]["numerator"] = 1
        self.data["evidence"][key] = [
            self.record("renovate.json", "dependency_configuration", updater="renovate"),
            self.record("pr.json", "dependency_pull_request", updater="renovate", url="https://forge.example/team/project/pull/7", author="renovate[bot]", dependency="widget"),
        ]
        return self.data["evidence"][key]

    def alert(self):
        key = "alerting_configured"
        self.data["report"][key]["numerator"] = 1
        self.data["evidence"][key] = [
            self.record("monitor.py", "alert_rule", handler="check_health", notifier="notify_ops"),
            self.record("registration.txt", "automatic_trigger", kind="schedule", registration="enabled schedule", handler="check_health"),
            self.record("sender.py", "alert_delivery", notifier="notify_ops"),
        ]
        return self.data["evidence"][key]

    def write_trace(self, *, truncate=None):
        rows = []
        for index, (path, source) in enumerate(self.sources.items()):
            rows += [{"type": "tool_call", "id": str(index), "toolName": "blind___read_text_file", "parameters": {"path": "/input/" + path}},
                     {"type": "tool_result", "id": str(index), "value": "header only\n" if path == truncate else source}]
        self.trace.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_configuration_alone_cannot_prove_dependency_pr(self):
        self.dependency().pop()
        errors = condition_errors(self.data, self.target)
        self.assertEqual([e["condition"] for e in errors], ["dependency_pull_request"])
        with self.assertRaisesRegex(ReportError, "dependency_pull_request"):
            validate_evaluation(self.data, self.target)

    def test_manual_delivery_does_not_supply_automatic_trigger(self):
        records = self.alert()
        records[1] = self.record("manual.txt", "automatic_trigger", kind="manual", registration="manual --once", handler="check_health")
        errors = condition_errors(self.data, self.target)
        self.assertIn("requires an enabled automatic trigger", str(errors))

    def test_missing_trigger_and_pr_reported_together_without_mutation(self):
        self.dependency().pop()
        self.alert().pop(1)
        before = copy.deepcopy(self.data)
        errors = condition_errors(self.data, self.target)
        self.assertEqual({e["criterionId"] for e in errors}, {"dependency_update_automation", "alerting_configured"})
        self.assertEqual(self.data, before)

    def test_manual_trigger_cannot_be_relabelled_as_schedule(self):
        records = self.alert()
        records[1] = self.record("manual.txt", "automatic_trigger", kind="schedule", registration="manual --once", handler="check_health")
        self.assertIn("registration describes a manual", str(condition_errors(self.data, self.target)))

    def test_unquoted_facts_and_invented_excerpt_rejected(self):
        records = self.dependency()
        records[1]["support"]["facts"]["author"] = "not-in-source"
        self.assertIn("literally", str(condition_errors(self.data, self.target)))
        records[1]["support"]["quote"] = "fabricated PR record"
        self.assertIn("does not match", str(condition_errors(self.data, self.target)))

    def test_quote_wrong_line_rejected(self):
        records = self.alert()
        records[0]["line"] = 2
        self.assertIn("does not match", str(condition_errors(self.data, self.target)))

    def test_disconnected_alert_chain_rejected(self):
        records = self.alert()
        records[1] = self.record("other-registration.txt", "automatic_trigger", kind="schedule", registration="enabled schedule", handler="unrelated_check")
        self.assertIn("connection", str(condition_errors(self.data, self.target)))

    def test_same_updater_must_connect_pr_to_configuration(self):
        records = self.dependency()
        records[0]["support"]["facts"]["updater"] = "recommended"
        self.assertIn("connection", str(condition_errors(self.data, self.target)))

    def test_valid_positive_proofs_saved_and_read_back(self):
        self.dependency()
        self.alert()
        receipt = store_report(self.data, self.target, self.base / "state")
        self.assertTrue(Path(receipt["report"]).exists())
        saved = latest_report(self.data["repository"], self.base / "state")
        self.assertEqual(saved["report"], self.data["report"])
        self.assertEqual(saved["evidence"], self.data["evidence"])

    def test_command_capture_supported_without_execution(self):
        records = self.dependency()
        file = records[1]
        records[1] = {"kind": "command", "command": "synthetic-pr-record", "cwd": ".", "exitCode": 0,
                      "output": self.sources["pr.json"], "support": file["support"]}
        self.assertEqual(condition_errors(self.data, self.target), [])
        records[1]["exitCode"] = 1
        self.assertIn("does not match", str(condition_errors(self.data, self.target)))

    def test_unread_quote_cannot_be_satisfied_by_reading_only_file_header(self):
        self.dependency()
        self.write_trace(truncate="pr.json")
        gate = ADAPTER.Gate(self.config)
        reply = gate.submit({"evaluation_json": json.dumps(self.data)})
        self.assertFalse(reply["accepted"])
        self.assertEqual(reply["diagnostics"]["unreadConditionQuotes"][0]["path"], "pr.json")
        self.assertFalse((self.base / "state").exists())

    def test_immediate_feedback_then_corrected_submission_and_stop(self):
        records = self.dependency()
        pr = records.pop()
        trigger = self.alert().pop(1)
        gate = ADAPTER.Gate(self.config)
        first = gate.submit({"evaluation_json": json.dumps(self.data)})
        self.assertFalse(first["accepted"])
        self.assertFalse(first["stop"])
        self.assertEqual(len(first["diagnostics"]["conditionEvidence"]), 2)
        self.assertFalse((self.base / "state").exists())
        records.append(pr)
        self.data["evidence"]["alerting_configured"].append(trigger)
        self.assertTrue(gate.submit({"evaluation_json": json.dumps(self.data)})["accepted"])
        self.assertEqual(gate.submit({"evaluation_json": "invalid"})["reason"], "already_accepted")
        self.assertEqual(json.loads((self.base / "attempts/attempt-01/evaluation.json").read_text())["report"], self.data["report"])

    def test_failures_need_no_invented_positive_proofs(self):
        self.assertEqual(condition_errors(self.data, self.target), [])
        validate_evaluation(self.data, self.target)

    def test_other_four_native_rules_are_not_given_extra_runtime_requirements(self):
        for key in ("feature_flag_infrastructure", "dead_feature_flag_detection", "release_automation", "product_analytics_instrumentation"):
            self.data["report"][key]["numerator"] = 1
        validate_evaluation(self.data, self.target)
        prompt = assessment_prompt(self.data["repository"], self.base / "task")
        for text in ("permanent kill switch", "documented flag lifecycle/cleanup", "does NOT require a historical", "page analytics"):
            self.assertIn(text, prompt)

    def test_cli_uses_same_condition_guard(self):
        self.dependency().pop()
        path = self.base / "input.json"
        path.write_text(json.dumps(self.data))
        result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/readiness.py"),
                                 "report", str(self.target), "--input", str(path), "--state-dir", str(self.base / "state")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("dependency_pull_request", result.stderr)
        self.assertFalse((self.base / "state").exists())

    def test_old_report_keeps_original_history_contract_but_fresh_import_is_strict(self):
        receipt = store_report(self.data, self.target, self.base / "state")
        path = Path(receipt["report"])
        # Build an explicitly synthetic pre-upgrade stored report, not edit any
        # historical user evidence. Its old positive judgment has no new support.
        old = json.loads(path.read_text())
        old["report"]["dependency_update_automation"]["numerator"] = 1
        path.write_text(json.dumps(old))
        pointer = path.parent / "latest.json"
        index = json.loads(pointer.read_text())
        index["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        pointer.write_text(json.dumps(index))
        self.assertEqual(latest_report(self.data["repository"], self.base / "state")["report"]["dependency_update_automation"]["numerator"], 1)
        with self.assertRaisesRegex(ReportError, "Required-condition"):
            validate_evaluation(old, self.target)

    def test_malformed_support_cannot_crash_diagnostics(self):
        records = self.dependency()
        for value in (None, [], {"condition": [], "quote": "x", "facts": {}}, {"condition": "invented", "quote": "x", "facts": {}}):
            with self.subTest(value=value):
                records[1]["support"] = value
                self.assertTrue(condition_errors(self.data, self.target))

    def two_apps(self):
        from agent_readiness.catalog import criteria_by_id
        for app in ("a", "b"):
            (self.target / app).mkdir()
        self.data["apps"] = {app: {"description": "Synthetic " + app} for app in ("a", "b")}
        for key, rule in criteria_by_id().items():
            if rule["scope"] == "application":
                self.data["report"][key]["denominator"] = 2

    def test_one_alert_chain_cannot_count_for_two_applications(self):
        records = self.alert()
        self.two_apps()
        self.data["report"]["alerting_configured"]["numerator"] = 2
        for record in records:
            record["support"]["application"] = "a"
        self.assertIn("found 1", str(condition_errors(self.data, self.target)))
        self.data["report"]["alerting_configured"]["numerator"] = 1
        self.assertEqual(condition_errors(self.data, self.target), [])
        self.data["report"]["alerting_configured"]["numerator"] = 2
        other = copy.deepcopy(records)
        for record in other:
            record["support"]["application"] = "b"
        records.extend(other)  # Explicit shared synthetic service for both apps.
        self.assertEqual(condition_errors(self.data, self.target), [])

    def test_cross_application_chain_cannot_be_spliced_or_left_unassigned(self):
        records = self.alert()
        self.two_apps()
        self.assertIn("identify an existing application", str(condition_errors(self.data, self.target)))
        for record, app in zip(records, ("a", "b", "a")):
            record["support"]["application"] = app
        self.assertIn("connection", str(condition_errors(self.data, self.target)))

    def test_all_invalid_rationales_returned_in_one_feedback(self):
        ids = ["alerting_configured", "dependency_update_automation", "dead_feature_flag_detection"]
        for index, key in enumerate(ids):
            self.data["report"][key]["rationale"] = "x" * (501 + index)
        reply = ADAPTER.Gate(self.config).submit({"evaluation_json": json.dumps(self.data)})
        self.assertFalse(reply["accepted"])
        self.assertEqual({e["criterionId"] for e in reply["diagnostics"]["invalidRationales"]}, set(ids))
        for key in ids:
            self.data["report"][key]["rationale"] = "Synthetic failed criterion."
        self.assertTrue(ADAPTER.Gate(self.config).submit({"evaluation_json": json.dumps(self.data)})["accepted"])

    def test_invalid_unicode_rationale_does_not_crash_feedback(self):
        key = "alerting_configured"
        self.data["report"][key]["rationale"] = "\ud800"
        reply = ADAPTER.Gate(self.config).submit({"evaluation_json": json.dumps(self.data)})
        self.assertFalse(reply["accepted"])
        self.assertFalse(reply["stop"])
        self.assertEqual(reply["diagnostics"]["invalidRationales"][0]["criterionId"], key)


if __name__ == "__main__":
    unittest.main()
