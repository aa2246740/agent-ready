"""Rule integrity and deterministic scoring regressions.

Native-source differential tests are maintained outside the public distribution.
"""

from collections import Counter
import hashlib
import json
import math
import unittest

from agent_readiness.catalog import criteria_by_id, load_catalog
from agent_readiness.scoring import score_report, status


def evaluation(numerator, denominator=1):
    return {"numerator": numerator, "denominator": denominator, "rationale": "Fixture evidence"}


class ScoringTests(unittest.TestCase):
    def test_versioned_rubric_integrity_and_copy_isolation(self):
        catalog = load_catalog()
        criteria = catalog["criteria"]
        self.assertEqual(catalog["version"], "0.240.0")
        self.assertEqual(len(criteria), 84)
        self.assertEqual(len(criteria_by_id()), 84)
        self.assertEqual(len(catalog["categories"]), 9)
        self.assertEqual(Counter(c["level"] for c in criteria), {1: 7, 2: 26, 3: 29, 4: 20, 5: 2})
        self.assertEqual(sum(c.get("isSkippable", False) for c in criteria), 30)
        self.assertEqual(Counter(c["scope"] for c in criteria), {"repository": 44, "application": 40})
        rubric = {key: catalog[key] for key in ("criteria", "categories", "levels")}
        canonical = json.dumps(rubric, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), "69ebd723b00820e227d87b46b746f4059d9a3f13d4df5913531d54528c75b49a")
        category_ids = {c["id"] for c in catalog["categories"]}
        for criterion in criteria:
            self.assertIn(criterion["category"], category_ids)
            self.assertTrue(criterion["instructions"])
        criteria[0]["id"] = "mutated"
        self.assertEqual(load_catalog()["criteria"][0]["id"], "lint_config")

    def test_score_is_equal_weight_per_criterion_not_per_application(self):
        report = {"lint_config": evaluation(1, 2), "type_check": evaluation(9, 9)}
        score = score_report(report)
        self.assertEqual(score["scorePercent"], 75)
        self.assertEqual(score["overallProgress"]["checksPassed"], 150)
        self.assertEqual(score["overallProgress"]["checksTotal"], 200)
        self.assertEqual(status(report["lint_config"]), "failed")
        self.assertEqual(score["achievedLevel"], 4)
        self.assertEqual(score["checksNeededForNextLevel"], 1)

    def test_grade_boundaries(self):
        for percent, expected in ((0, 1), (19.999, 1), (20, 2), (39.999, 2), (40, 3), (59.999, 3), (60, 4), (79.999, 4), (80, 5), (100, 5)):
            with self.subTest(percent=percent):
                result = score_report({"lint_config": evaluation(percent, 100)})
                self.assertEqual(result["achievedLevel"], expected)

    def test_empty_skipped_missing_and_unknown_reports(self):
        for report in ({}, {"lint_config": evaluation(None)}, {"unknown_id": evaluation(1)}):
            result = score_report(report)
            self.assertEqual(result["achievedLevel"], 1)
            self.assertEqual(result["scorePercent"], 0)
            self.assertIsNone(result["checksNeededForNextLevel"])
            self.assertEqual(result["overallProgress"]["checksTotal"], 0)
        result = score_report({"lint_config": evaluation(None), "type_check": evaluation(1)})
        self.assertEqual(result["scorePercent"], 100)
        self.assertTrue(result["criterionResults"][0]["isSkipped"])
        self.assertFalse(result["criterionResults"][2]["isSkipped"])

    def test_no_level_gate_and_native_rounding(self):
        high_level = next(c["id"] for c in load_catalog()["criteria"] if c["level"] == 5)
        score = score_report({high_level: evaluation(1)})
        self.assertEqual(score["achievedLevel"], 5)
        self.assertTrue(all(level["isUnlocked"] for level in score["levelBreakdowns"]))
        self.assertEqual(score_report({"lint_config": evaluation(0.005)})["overallProgress"]["checksPassed"], 1)

    def test_native_nonpositive_denominator_edge_is_preserved(self):
        report = {"lint_config": evaluation(0, 0), "type_check": evaluation(1)}
        result = score_report(report)
        self.assertEqual(result["scorePercent"], 100)
        # The upstream scorer itself has this inconsistency. Submission validation
        # rejects zero denominators, but the compatibility core preserves it.
        self.assertTrue(result["criterionResults"][0]["isComplete"])
        self.assertEqual(status(report["lint_config"]), "failed")

    def test_json_numbers_use_javascript_binary64_semantics(self):
        result = score_report({"lint_config": evaluation(9007199254740993, 9007199254740994)})
        self.assertEqual(result["criterionResults"][0]["passed"], 9007199254740992)
        self.assertEqual(result["overallProgress"]["percentComplete"], 99.99999999999997)
        # Both JSON numbers become the same Number in JavaScript before ===.
        self.assertEqual(status(evaluation(9007199254740993, 9007199254740992)), "passed")

    def test_intermediate_overflow_and_nan_follow_json_stringify(self):
        result = score_report({"lint_config": evaluation(1e308)})
        self.assertIsNone(result["overallProgress"]["checksPassed"])
        self.assertIsNone(result["overallProgress"]["percentComplete"])
        self.assertEqual(result["achievedLevel"], 5)
        cancelled = score_report({
            "lint_config": evaluation(1, math.ulp(0.0)),
            "type_check": evaluation(-1, math.ulp(0.0)),
        })
        self.assertIsNone(cancelled["overallProgress"]["percentComplete"])
        self.assertIsNone(cancelled["checksNeededForNextLevel"])
        self.assertEqual(cancelled["achievedLevel"], 1)
        # Every externally returned field, including category extensions, is
        # valid strict JSON even when internal Number arithmetic is non-finite.
        json.dumps(result, allow_nan=False)
        json.dumps(cancelled, allow_nan=False)

    def test_invalid_numeric_inputs_are_explicitly_rejected(self):
        for number in (True, False, "1", math.nan, math.inf, -math.inf, 10 ** 400):
            for field in ("numerator", "denominator"):
                with self.subTest(field=field, number_type=type(number).__name__):
                    item = evaluation(1)
                    item[field] = number
                    with self.assertRaises(ValueError):
                        score_report({"lint_config": item})
                    with self.assertRaises(ValueError):
                        status(item)
        with self.assertRaisesRegex(ValueError, "requires numerator and denominator"):
            score_report({"lint_config": {"denominator": 1}})




if __name__ == "__main__":
    unittest.main()
