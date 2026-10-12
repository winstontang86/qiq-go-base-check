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
        "schema_version": 1,
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
        "findings": [], "warnings": [],
    }


def confirmation(status="PENDING", kind="JOIN"):
    return {
        "id": "confirm-001", "type": kind, "status": status,
        "scope": "orders query", "reason": "Business necessity reviewed",
        "snapshot_id": "review-001", "approved_by": "reviewer",
        "confirmed_at": "2026-10-12T11:00:00Z", "environment": "production",
        "evidence": ["Scoped approval and verified change record"],
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

    def test_warnings_do_not_block(self):
        self.report["warnings"] = ["Reduce duplicate metrics and verbose success logs"]
        self.assertTrue(gate.evaluate(self.report)["allow_merge"])

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

    def test_production_schema_requires_environment(self):
        item = self.add_confirmation("CONFIRMED", "PRODUCTION_SCHEMA")
        item.pop("environment")
        with self.assertRaises(gate.InvalidReport):
            gate.evaluate(self.report)

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

    def test_mandatory_issues_cannot_be_marked_nonblocking(self):
        for category, severity in (("security", "LOW"), ("logic", "HIGH"),
                                   ("logic", "CRITICAL"), ("performance", "HIGH"),
                                   ("performance", "CRITICAL")):
            with self.subTest(category=category, severity=severity):
                report = copy.deepcopy(self.report)
                report["findings"] = [{
                    "id": "F1", "category": category, "severity": severity,
                    "blocking": False, "rule": "GENERAL", "summary": "Confirmed affected issue",
                    "evidence": ["Source location and affected execution path"],
                }]
                self.assertEqual(gate.evaluate(report)["decision"], "BLOCKED")

    def test_low_risk_logic_finding_does_not_block(self):
        self.report["findings"] = [{
            "id": "F1", "category": "logic", "severity": "LOW", "blocking": False,
            "rule": "GENERAL", "summary": "Low risk issue", "evidence": ["Scoped review evidence"],
        }]
        self.assertTrue(gate.evaluate(self.report)["allow_merge"])

    def test_explicit_hard_rules_cannot_be_marked_nonblocking(self):
        for rule, category in gate.MANDATORY_RULES.items():
            with self.subTest(rule=rule):
                report = sample_report()
                report["findings"] = [{
                    "id": "F1", "category": category, "severity": "LOW",
                    "blocking": False, "rule": rule, "summary": "Confirmed rule violation",
                    "evidence": ["Matched snapshot and source evidence"],
                }]
                self.assertEqual(gate.evaluate(report)["decision"], "BLOCKED")

    def test_wrong_rule_category_is_invalid(self):
        self.report["findings"] = [{
            "id": "F1", "category": "logging", "severity": "LOW", "blocking": False,
            "rule": "NO_USABLE_SQL_INDEX", "summary": "Mismatch", "evidence": ["Evidence"],
        }]
        with self.assertRaises(gate.InvalidReport):
            gate.evaluate(self.report)

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
