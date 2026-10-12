#!/usr/bin/env python3
"""Aggregate declared review evidence; never execute repository commands."""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

CATEGORIES = (
    "build", "tests", "logic", "security", "performance",
    "observability", "logging", "business_rules",
)
CHECK_STATUSES = {"PASS", "FAIL", "UNKNOWN", "NA"}
CONFIRMATION_TYPES = {"DATABASE_REUSE", "PRODUCTION_SCHEMA", "JOIN"}
MANDATORY_RULES = {
    "BUILD_FAILURE": "build", "TEST_FAILURE": "tests",
    "MISSING_UNIT_COVERAGE": "tests", "SEVERE_LOGIC_BUG": "logic",
    "SECURITY_VULNERABILITY": "security", "PERFORMANCE_REGRESSION": "performance",
    "NO_USABLE_SQL_INDEX": "performance", "SLOW_QUERY": "performance",
    "MISSING_CRITICAL_METRIC": "observability", "MISSING_ERROR_LOG": "logging",
    "MISSING_LOG_CONTEXT": "logging", "BUSINESS_RULE_VIOLATION": "business_rules",
}
EXIT_CODES = {
    "PASS": 0, "BLOCKED": 1, "NEEDS_CONFIRMATION": 2,
    "UNKNOWN": 3, "INVALID_REPORT": 4,
}
MAX_REPORT_BYTES = 1024 * 1024


class InvalidReport(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidReport(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def evidence(value, required=False):
    return (
        isinstance(value, list)
        and all(text(item) for item in value)
        and (not required or bool(value))
    )


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value
    ):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        return False


def validate_report(report):
    require(isinstance(report, dict), "report must be an object")
    require(type(report.get("schema_version")) is int
            and report["schema_version"] == 1, "schema_version must be 1")
    scope = report.get("scope")
    require(isinstance(scope, dict), "scope is required")
    for field in ("target_ref", "reviewed_at", "snapshot_id"):
        require(text(scope.get(field)), f"scope.{field} is required")
    require(timestamp(scope["reviewed_at"]), "scope.reviewed_at must be a zoned ISO timestamp")
    for field in ("target_sha", "head_sha", "merge_base_sha"):
        value = scope.get(field)
        require(isinstance(value, str)
                and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value),
                f"scope.{field} must be a full lowercase commit hash")
    require(scope.get("snapshot_kind") in ("committed", "worktree", "merge_result"),
            "scope.snapshot_kind is invalid")
    for field in ("target_verified", "integration_risk", "merge_result_verified"):
        require(type(scope.get(field)) is bool, f"scope.{field} must be boolean")
    if scope["snapshot_kind"] == "merge_result":
        require(scope["merge_result_verified"], "merge-result snapshot must be verified")

    checks = report.get("checks")
    require(isinstance(checks, list), "checks must be an array")
    found = set()
    for check in checks:
        require(isinstance(check, dict), "each check must be an object")
        category = check.get("id")
        require(isinstance(category, str) and category in CATEGORIES,
                "check id is invalid")
        require(category not in found, "duplicate check id")
        found.add(category)
        status = check.get("status")
        require(isinstance(status, str) and status in CHECK_STATUSES,
                f"{category}: invalid status")
        require(text(check.get("reason")), f"{category}: reason is required")
        require(evidence(check.get("evidence"), status != "UNKNOWN"),
                f"{category}: evidence is missing or invalid")
    require(found == set(CATEGORIES), "all eight check categories are required")

    required_ids = report.get("required_confirmation_ids")
    require(isinstance(required_ids, list) and all(text(item) for item in required_ids),
            "required_confirmation_ids must be a string array")
    require(len(required_ids) == len(set(required_ids)), "duplicate required confirmation id")
    confirmations = report.get("confirmations")
    require(isinstance(confirmations, list), "confirmations must be an array")
    confirmation_ids = set()
    for confirmation in confirmations:
        require(isinstance(confirmation, dict), "each confirmation must be an object")
        identifier = confirmation.get("id")
        require(text(identifier) and identifier not in confirmation_ids,
                "confirmation id is missing or duplicated")
        confirmation_ids.add(identifier)
        require(confirmation.get("type") in CONFIRMATION_TYPES,
                "confirmation type is invalid")
        require(confirmation.get("status") in ("PENDING", "CONFIRMED", "REJECTED"),
                "confirmation status is invalid")
        for field in ("scope", "reason", "snapshot_id"):
            require(text(confirmation.get(field)), f"confirmation.{field} is required")
        confirmed = confirmation["status"] == "CONFIRMED"
        require(evidence(confirmation.get("evidence"), confirmed),
                "confirmation evidence is missing or invalid")
        if confirmed:
            for field in ("approved_by", "confirmed_at"):
                require(text(confirmation.get(field)), f"confirmation.{field} is required")
            require(timestamp(confirmation["confirmed_at"]),
                    "confirmation.confirmed_at must be a zoned ISO timestamp")
            require(confirmation["snapshot_id"] == scope["snapshot_id"],
                    "confirmation snapshot does not match review snapshot")
            if confirmation["type"] == "PRODUCTION_SCHEMA":
                require(text(confirmation.get("environment")),
                        "production schema confirmation requires environment")
    require(confirmation_ids == set(required_ids),
            "confirmations must exactly match required_confirmation_ids")

    findings = report.get("findings")
    require(isinstance(findings, list), "findings must be an array")
    finding_ids = set()
    for finding in findings:
        require(isinstance(finding, dict), "each finding must be an object")
        identifier = finding.get("id")
        require(text(identifier) and identifier not in finding_ids,
                "finding id is missing or duplicated")
        finding_ids.add(identifier)
        require(finding.get("category") in CATEGORIES, "finding category is invalid")
        require(finding.get("severity") in ("CRITICAL", "HIGH", "MEDIUM", "LOW"),
                "finding severity is invalid")
        require(type(finding.get("blocking")) is bool, "finding.blocking must be boolean")
        rule = finding.get("rule")
        require(isinstance(rule, str) and (rule == "GENERAL" or rule in MANDATORY_RULES),
                "finding rule is missing or invalid")
        if rule != "GENERAL":
            require(MANDATORY_RULES[rule] == finding["category"],
                    "finding rule does not match category")
        require(text(finding.get("summary")), "finding summary is required")
        require(evidence(finding.get("evidence"), True), "finding evidence is required")
    require(evidence(report.get("warnings")), "warnings must be a string array")
    return report


def evaluate(report):
    validate_report(report)
    failed = [item["id"] for item in report["checks"] if item["status"] == "FAIL"]
    unknown = [item["id"] for item in report["checks"] if item["status"] == "UNKNOWN"]
    blocking_findings = []
    for item in report["findings"]:
        mandatory = (
            item["rule"] in MANDATORY_RULES
            or item["category"] == "security"
            or (item["category"] in ("logic", "performance")
                and item["severity"] in ("CRITICAL", "HIGH"))
        )
        if item["blocking"] or mandatory:
            blocking_findings.append(item["id"])
    scope = report["scope"]
    scope_issues = []
    if not scope["target_verified"]:
        scope_issues.append("target_not_verified")
    if scope["snapshot_kind"] == "worktree":
        scope_issues.append("worktree_pre_review_requires_committed_recheck")
    if scope["integration_risk"] and not scope["merge_result_verified"]:
        scope_issues.append("merge_result_not_verified")
    pending = [item["id"] for item in report["confirmations"]
               if item["status"] == "PENDING"]
    rejected = [item["id"] for item in report["confirmations"]
                if item["status"] == "REJECTED"]
    if failed or blocking_findings or rejected:
        decision = "BLOCKED"
    elif unknown or scope_issues:
        decision = "UNKNOWN"
    elif pending:
        decision = "NEEDS_CONFIRMATION"
    else:
        decision = "PASS"
    return {
        "decision": decision,
        "allow_merge": decision == "PASS",
        "failed_checks": failed,
        "unknown_checks": unknown,
        "scope_issues": scope_issues,
        "blocking_findings": blocking_findings,
        "pending_confirmations": pending,
        "rejected_confirmations": rejected,
        "warnings": report["warnings"],
        "snapshot_id": scope["snapshot_id"],
    }


def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON object key")
        result[key] = value
    return result


def reject_constant(value):
    raise InvalidReport("non-finite JSON values are not allowed")


def load_report(path):
    with Path(path).open("rb") as handle:
        data = handle.read(MAX_REPORT_BYTES + 1)
    require(len(data) <= MAX_REPORT_BYTES, "report exceeds size limit")
    return json.loads(data.decode("utf-8"), object_pairs_hook=reject_duplicates,
                      parse_constant=reject_constant)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", help="path to a review JSON report")
    args = parser.parse_args(argv)
    try:
        result = evaluate(load_report(args.report))
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        result = {
            "decision": "INVALID_REPORT", "allow_merge": False,
            "error": "Report cannot be read or violates the review schema.",
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return EXIT_CODES[result["decision"]]


if __name__ == "__main__":
    sys.exit(main())
