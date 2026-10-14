import contextlib
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_gate.py"
SPEC = importlib.util.spec_from_file_location("evaluate_gate", SCRIPT)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def sample_report():
    return {
        "schema_version": 2,
        "scope": {
            "target_ref": "origin/main", "target_sha": "a" * 40,
            "head_sha": "b" * 40, "merge_base_sha": "c" * 40,
            "reviewed_at": "2026-10-12T10:00:00Z", "snapshot_id": "review-001",
            "snapshot_kind": "committed", "target_verified": True,
            "integration_risk": False, "merge_result_verified": False,
        },
        "checks": [
            {"id": category, "status": "PASS", "reason": "Verified applicable scope",
             "evidence": ["Snapshot-matched review evidence"]}
            for category in gate.CATEGORIES
        ],
        "required_confirmation_ids": [], "confirmations": [],
        "findings": [], "recommendations": [],
    }


def confirmation(status="PENDING", kind="JOIN"):
    return {
        "id": "confirm-001", "type": kind, "status": status,
        "scope": "orders query", "reason": "Business necessity reviewed",
        "snapshot_id": "review-001", "approved_by": "reviewer",
        "confirmed_at": "2026-10-12T11:00:00Z", "environment": "production",
        "evidence": ["Scoped approval and verified change record"],
    }


def finding(rule="SECURITY_VULNERABILITY", category="security", identifier="F1"):
    return {
        "id": identifier, "category": category, "severity": "HIGH", "rule": rule,
        "summary": "Confirmed rule violation",
        "evidence": ["Matched snapshot and source evidence"],
    }


def recommendation(rule="METRIC_REDUNDANCY", category="observability", identifier="R1"):
    return {
        "id": identifier, "category": category, "rule": rule,
        "summary": "Redundant metric definition",
        "evidence": ["metrics/order_metrics.go:18"],
        "suggestion": "Merge duplicate counters into one",
    }


class EvaluateGateTests(unittest.TestCase):
    def setUp(self):
        self.report = sample_report()

    def add_confirmation(self, status="PENDING", kind="JOIN"):
        item = confirmation(status, kind)
        self.report["required_confirmation_ids"].append(item["id"])
        self.report["confirmations"].append(item)
        return item

    def test_all_pass(self):
        self.assertTrue(gate.evaluate(self.report)["allow_merge"])

    def test_each_failed_check_blocks(self):
        for index, category in enumerate(gate.CATEGORIES):
            with self.subTest(category=category):
                report = copy.deepcopy(self.report)
                report["checks"][index]["status"] = "FAIL"
                result = gate.evaluate(report)
                self.assertEqual(result["decision"], "BLOCKED")
                self.assertFalse(result["allow_merge"])

    def test_unknown_blocks(self):
        self.report["checks"][0].update(status="UNKNOWN", evidence=[])
        self.assertEqual(gate.evaluate(self.report)["decision"], "UNKNOWN")

    def test_pending_confirmation_blocks(self):
        self.add_confirmation()
        self.assertEqual(gate.evaluate(self.report)["decision"], "NEEDS_CONFIRMATION")

    def test_all_confirmation_types_can_be_confirmed(self):
        for kind in gate.CONFIRMATION_TYPES:
            with self.subTest(kind=kind):
                report = sample_report()
                report["required_confirmation_ids"] = ["confirm-001"]
                report["confirmations"] = [confirmation("CONFIRMED", kind)]
                self.assertEqual(gate.evaluate(report)["decision"], "PASS")

    def test_rejected_confirmation_blocks(self):
        self.add_confirmation("REJECTED")
        self.assertEqual(gate.evaluate(self.report)["decision"], "BLOCKED")

    def test_failure_precedes_unknown_and_confirmation(self):
        self.add_confirmation()
        self.report["checks"][0]["status"] = "FAIL"
        self.report["checks"][1]["status"] = "UNKNOWN"
        self.assertEqual(gate.evaluate(self.report)["decision"], "BLOCKED")

    def test_unknown_precedes_confirmation(self):
        self.add_confirmation()
        self.report["checks"][0]["status"] = "UNKNOWN"
        self.assertEqual(gate.evaluate(self.report)["decision"], "UNKNOWN")

    def test_missing_or_duplicate_category_invalid(self):
        for checks in (self.report["checks"][:-1], self.report["checks"] * 2):
            with self.subTest(length=len(checks)):
                report = copy.deepcopy(self.report)
                report["checks"] = checks
                with self.assertRaises(gate.InvalidReport):
                    gate.evaluate(report)

    def test_pass_and_na_require_evidence_and_reason(self):
        for status in ("PASS", "NA", "FAIL"):
            for field, value in (("evidence", []), ("reason", " ")):
                with self.subTest(status=status, field=field):
                    report = copy.deepcopy(self.report)
                    report["checks"][0]["status"] = status
                    report["checks"][0][field] = value
                    with self.assertRaises(gate.InvalidReport):
                        gate.evaluate(report)

    def test_justified_na_is_allowed(self):
        self.report["checks"][0].update(status="NA", reason="Documentation-only change")
        self.assertTrue(gate.evaluate(self.report)["allow_merge"])

    def test_recommendations_are_listed_without_blocking(self):
        self.report["recommendations"] = [
            recommendation(),
            recommendation("LOG_REDUNDANCY", "logging", "R2"),
        ]
        result = gate.evaluate(self.report)
        self.assertEqual(result["decision"], "PASS")
        self.assertTrue(result["allow_merge"])
        self.assertEqual(result["recommendations"], ["R1", "R2"])
        self.assertEqual(result["blocking_findings"], [])

    def test_recommendation_rules_are_restricted(self):
        for rule, category in (("GENERAL", "logic"), ("BUILD_FAILURE", "build"),
                               ("NO_USABLE_SQL_INDEX", "performance")):
            with self.subTest(rule=rule):
                report = copy.deepcopy(self.report)
                report["recommendations"] = [recommendation(rule, category)]
                with self.assertRaises(gate.InvalidReport):
                    gate.evaluate(report)

    def test_recommendation_rule_must_match_category(self):
        self.report["recommendations"] = [recommendation("LOG_REDUNDANCY", "logic")]
        with self.assertRaises(gate.InvalidReport):
            gate.evaluate(self.report)

    def test_recommendation_requires_summary_evidence_and_suggestion(self):
        for field in ("summary", "evidence", "suggestion"):
            with self.subTest(field=field):
                report = copy.deepcopy(self.report)
                item = recommendation()
                del item[field]
                report["recommendations"] = [item]
                with self.assertRaises(gate.InvalidReport):
                    gate.evaluate(report)

    def test_issue_ids_are_unique_across_levels(self):
        self.report["findings"] = [finding(identifier="X1")]
        self.report["recommendations"] = [recommendation(identifier="X1")]
        with self.assertRaises(gate.InvalidReport):
            gate.evaluate(self.report)

    def test_confirmed_requires_approver_time_and_evidence(self):
        for field in ("approved_by", "confirmed_at", "evidence"):
            with self.subTest(field=field):
                report = sample_report()
                item = confirmation("CONFIRMED")
                del item[field]
                report["required_confirmation_ids"] = [item["id"]]
                report["confirmations"] = [item]
                with self.assertRaises(gate.InvalidReport):
                    gate.evaluate(report)

    def test_production_confirmation_requires_environment(self):
        for kind in ("PRODUCTION_SCHEMA", "PRODUCTION_DATA_WRITE"):
            with self.subTest(kind=kind):
                item = self.add_confirmation("CONFIRMED", kind)
                item.pop("environment")
                with self.assertRaises(gate.InvalidReport):
                    gate.evaluate(self.report)

    def test_data_write_confirmation_blocks_until_confirmed(self):
        item = self.add_confirmation("PENDING", "PRODUCTION_DATA_WRITE")
        self.assertEqual(gate.evaluate(self.report)["decision"], "NEEDS_CONFIRMATION")
        item["status"] = "CONFIRMED"
        self.assertEqual(gate.evaluate(self.report)["decision"], "PASS")

    def test_stale_confirmation_invalid(self):
        self.add_confirmation("CONFIRMED")["snapshot_id"] = "older-review"
        with self.assertRaises(gate.InvalidReport):
            gate.evaluate(self.report)

    def test_required_confirmation_cannot_be_omitted(self):
        self.report["required_confirmation_ids"] = ["missing"]
        with self.assertRaises(gate.InvalidReport):
            gate.evaluate(self.report)

    def test_scope_unknown_prevents_pass(self):
        for field, value in (("target_verified", False), ("snapshot_kind", "worktree"),
                             ("integration_risk", True)):
            with self.subTest(field=field):
                report = copy.deepcopy(self.report)
                report["scope"][field] = value
                self.assertEqual(gate.evaluate(report)["decision"], "UNKNOWN")

    def test_verified_merge_result_passes(self):
        self.report["scope"].update(snapshot_kind="merge_result", integration_risk=True,
                                      merge_result_verified=True)
        self.assertTrue(gate.evaluate(self.report)["allow_merge"])

    def test_every_mandatory_rule_blocks(self):
        for rule, category in gate.MANDATORY_RULES.items():
            with self.subTest(rule=rule):
                report = sample_report()
                report["findings"] = [finding(rule, category)]
                result = gate.evaluate(report)
                self.assertEqual(result["decision"], "BLOCKED")
                self.assertEqual(result["blocking_findings"], ["F1"])

    def test_medium_severity_finding_still_blocks(self):
        item = finding()
        item["severity"] = "MEDIUM"
        self.report["findings"] = [item]
        self.assertEqual(gate.evaluate(self.report)["decision"], "BLOCKED")

    def test_findings_require_mandatory_rule(self):
        for rule in ("GENERAL", "METRIC_REDUNDANCY", "UNKNOWN_RULE"):
            with self.subTest(rule=rule):
                report = copy.deepcopy(self.report)
                report["findings"] = [finding(rule, "observability")]
                with self.assertRaises(gate.InvalidReport):
                    gate.evaluate(report)

    def test_finding_rule_must_match_category(self):
        self.report["findings"] = [finding("NO_USABLE_SQL_INDEX", "logging")]
        with self.assertRaises(gate.InvalidReport):
            gate.evaluate(self.report)

    def test_low_risk_severity_is_not_reportable(self):
        item = finding()
        item["severity"] = "LOW"
        self.report["findings"] = [item]
        with self.assertRaises(gate.InvalidReport):
            gate.evaluate(self.report)

    def test_finding_requires_summary_and_evidence(self):
        for field in ("summary", "evidence"):
            with self.subTest(field=field):
                report = copy.deepcopy(self.report)
                item = finding()
                del item[field]
                report["findings"] = [item]
                with self.assertRaises(gate.InvalidReport):
                    gate.evaluate(report)

    def test_invalid_or_unzoned_timestamps_are_rejected(self):
        for value in ("not-a-time", "2026-02-30T10:00:00Z", "2026-10-12T10:00:00"):
            for field in ("reviewed_at", "confirmed_at"):
                with self.subTest(value=value, field=field):
                    report = sample_report()
                    if field == "reviewed_at":
                        report["scope"][field] = value
                    else:
                        item = confirmation("CONFIRMED")
                        item[field] = value
                        report["required_confirmation_ids"] = [item["id"]]
                        report["confirmations"] = [item]
                    with self.assertRaises(gate.InvalidReport):
                        gate.evaluate(report)

    def test_cli_exit_codes(self):
        for decision, code in gate.EXIT_CODES.items():
            with self.subTest(decision=decision), tempfile.TemporaryDirectory() as directory:
                report = sample_report()
                if decision == "BLOCKED":
                    report["checks"][0]["status"] = "FAIL"
                elif decision == "UNKNOWN":
                    report["checks"][0]["status"] = "UNKNOWN"
                elif decision == "NEEDS_CONFIRMATION":
                    report["required_confirmation_ids"] = ["confirm-001"]
                    report["confirmations"] = [confirmation()]
                elif decision == "INVALID_REPORT":
                    report = {}
                path = Path(directory) / "report.json"
                path.write_text(json.dumps(report), encoding="utf-8")
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(gate.main([str(path)]), code)
                self.assertEqual(json.loads(output.getvalue())["decision"], decision)

    def test_cli_rejects_bad_json_without_leaking_content(self):
        for data in ('{"a":1,"a":2}', '{"a":NaN}', 'secret-invalid-content', '[' * 2000):
            with self.subTest(data_length=len(data)), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "invalid.json"
                path.write_text(data, encoding="utf-8")
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(gate.main([str(path)]), 4)
                self.assertNotIn("secret-invalid-content", output.getvalue())

    def test_report_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.json"
            path.write_bytes(b" " * (gate.MAX_REPORT_BYTES + 1))
            with self.assertRaises(gate.InvalidReport):
                gate.load_report(path)

    def test_missing_file_returns_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = gate.main([str(Path(directory) / "missing.json")])
            self.assertEqual(code, 4)

    def test_non_boolean_scope_and_short_hash_invalid(self):
        for field, value in (("target_verified", "true"), ("head_sha", "abcdef")):
            with self.subTest(field=field):
                report = copy.deepcopy(self.report)
                report["scope"][field] = value
                with self.assertRaises(gate.InvalidReport):
                    gate.evaluate(report)


if __name__ == "__main__":
    unittest.main()
