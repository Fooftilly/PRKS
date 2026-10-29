"""Fail-closed contracts for the inactive packaged E2E defect-class audit (#993)."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts.ci import plan_packaged_e2e as staged
from tests.e2e import policy, run


def complete_fixture():
    """Synthetic *complete for its declared modules* suite, no browser needed."""
    ids = set(policy.SMOKE_TEST_IDS) | set(staged.EXTRA_PACKAGED_SMOKE_IDS)
    ids.update(test_id for _, test_id in staged.CRITICAL_BROWSER_OBLIGATIONS)
    all_ids = sorted(ids)
    modules = sorted({test_id.rsplit(".", 2)[0] for test_id in all_ids})
    return all_ids, modules


class PackagedCoverageAuditTests(unittest.TestCase):
    def audit(self, ids=None, **kwargs):
        base, modules = complete_fixture()
        if ids is None:
            ids = base
        return staged.audit_packaged_coverage(
            ids, required_modules=modules, **kwargs
        )

    def test_curated_smoke_is_not_critical_defect_equivalence(self):
        report = self.audit()
        self.assertEqual(report["version"], 1)
        self.assertTrue(report["coverage_complete"])
        self.assertFalse(report["active"])
        self.assertEqual(report["effective_mode"], "full")
        self.assertFalse(report["safe_to_activate_smoke"])
        self.assertEqual(report["packaged_smoke_count"], 14)
        self.assertEqual(report["packaged_full_count"], len(complete_fixture()[0]))
        rows = {r["obligation"]: r for r in report["critical_rows"]}
        self.assertEqual(rows["offline-pdf-reopen"]["status"], "included_in_smoke")
        self.assertEqual(rows["offline-pdf-annotation-tools"]["status"], "included_in_smoke")
        self.assertEqual(rows["main-secondary-divider"]["status"], "included_in_smoke")
        self.assertEqual(rows["offline-annotation-reconnect"]["status"], "excluded_from_smoke")
        self.assertEqual(rows["pdf-annotation-persistence"]["status"], "excluded_from_smoke")
        self.assertEqual(rows["vue-processing-route-ready"]["status"], "excluded_from_smoke")
        self.assertEqual(rows["offline-install-disconnect"]["status"], "excluded_from_smoke")
        self.assertEqual(rows["durable-field-reconciliation"]["status"], "excluded_from_smoke")
        self.assertEqual(rows["service-worker-upgrade-rollback"]["status"], "separate_guard_only")
        self.assertIn("independently", rows["service-worker-upgrade-rollback"]["guard_verification"])
        self.assertEqual(report["missing_required_ids"], [])

    def test_critical_omissions_are_still_listed_when_candidate_grows(self):
        all_ids, modules = complete_fixture()
        extra = next(t for name, t in staged.CRITICAL_BROWSER_OBLIGATIONS
                     if name == "pdf-annotation-persistence")
        candidate = (*policy.SMOKE_TEST_IDS, *staged.EXTRA_PACKAGED_SMOKE_IDS, extra)
        report = staged.audit_packaged_coverage(
            all_ids, candidate_ids=candidate, required_modules=modules,
        )
        rows = {r["obligation"]: r for r in report["critical_rows"]}
        self.assertEqual(rows["pdf-annotation-persistence"]["status"], "included_in_smoke")
        self.assertEqual(rows["offline-annotation-reconnect"]["status"], "excluded_from_smoke")
        self.assertEqual(report["packaged_smoke_count"], 15)
        self.assertFalse(report["safe_to_activate_smoke"])

    def test_removed_or_renamed_required_id_is_not_silently_excluded(self):
        all_ids, _ = complete_fixture()
        victim = next(t for name, t in staged.CRITICAL_BROWSER_OBLIGATIONS
                      if name == "offline-annotation-reconnect")
        for altered in (
            [t for t in all_ids if t != victim],
            [t + "_renamed" if t == victim else t for t in all_ids],
        ):
            with self.subTest(altered=altered != all_ids):
                result = self.audit(altered)
                self.assertFalse(result["coverage_complete"])
                self.assertIn(victim, result["missing_required_ids"])
                row = next(r for r in result["critical_rows"] if r["test_id"] == victim)
                self.assertEqual(row["status"], "missing_from_discovery")
                self.assertIn("fail closed", staged.format_packaged_coverage_audit(result))

    def test_empty_invalid_and_incomplete_module_inventory_fail_closed(self):
        all_ids, modules = complete_fixture()
        for invalid in ([], ["unittest.loader._FailedTest.X"], all_ids + [all_ids[0]],
                        all_ids + ["not-an-e2e-id"], None):
            with self.subTest(invalid=repr(invalid)[:60]):
                with self.assertRaises(ValueError):
                    self.audit(invalid)
        chosen = modules[0]
        partial = [t for t in all_ids if t.rsplit(".", 2)[0] != chosen]
        result = self.audit(partial)
        self.assertFalse(result["coverage_complete"])
        self.assertIn(chosen, result["missing_modules"])

    def test_malformed_feature_mapping_is_rejected(self):
        all_ids, modules = complete_fixture()
        for broken in (
            {},
            {"bad": {"selectors": ()}},
            {"bad": {"selectors": None}},
            {"bad": {"selectors": ("not a selector!",)}},
            {"bad": {"selectors": ("tests.e2e.test_app", "tests.e2e.test_app")}},
        ):
            with self.subTest(mapping=broken):
                with self.assertRaisesRegex(ValueError, "feature"):
                    staged.audit_packaged_coverage(
                        all_ids, required_modules=modules, feature_map=broken
                    )

    def test_candidate_cannot_add_undiscovered_ids_or_drop_smoke_floor(self):
        all_ids, modules = complete_fixture()
        bogus = "tests.e2e.test_offline.OfflineFoundationTests.test_nonexistent"
        candidate = (*policy.SMOKE_TEST_IDS, *staged.EXTRA_PACKAGED_SMOKE_IDS, bogus)
        report = staged.audit_packaged_coverage(
            all_ids, required_modules=modules, candidate_ids=candidate
        )
        self.assertFalse(report["coverage_complete"])
        self.assertEqual(report["unknown_candidate_ids"], [bogus])
        with self.assertRaisesRegex(ValueError, "smoke candidate"):
            self.audit(candidate_ids=(bogus,))

    def test_stable_under_test_order_and_exposes_unmapped_ids(self):
        original, modules = complete_fixture()
        unmatched = "tests.e2e.test_app.UnknownCoverageTests.test_unmapped"
        original.append(unmatched)
        with patch.object(staged.policy, "FEATURES", policy.FEATURES):
            first = staged.audit_packaged_coverage(original, required_modules=modules)
            second = staged.audit_packaged_coverage(
                list(reversed(original)), required_modules=list(reversed(modules))
            )
        self.assertEqual(first["critical_rows"], second["critical_rows"])
        self.assertEqual(first["unmatched_source_ids"], second["unmatched_source_ids"])
        self.assertIn(unmatched, first["unmatched_source_ids"])
        self.assertEqual(
            staged.format_packaged_coverage_audit(first).split("Critical obligation |")[1],
            staged.format_packaged_coverage_audit(second).split("Critical obligation |")[1],
        )

    def test_cli_is_read_only_and_exits_nonzero_on_partial_discovery(self):
        all_ids, modules = complete_fixture()
        with (
            patch.object(run, "DEFAULT_E2E_MODULES", tuple(modules)),
            patch.object(staged, "_discover_complete_e2e_ids", return_value=all_ids),
            patch.object(staged.sys, "argv", ["plan_packaged_e2e", "--coverage-audit"]),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(staged.main(), 0)
        self.assertIn("ADVISORY", output.getvalue())
        self.assertIn("activation: NOT authorized", output.getvalue())
        with (
            patch.object(run, "DEFAULT_E2E_MODULES", tuple(modules)),
            patch.object(staged, "_discover_complete_e2e_ids",
                         return_value=all_ids[:-1]),
            patch.object(staged.sys, "argv", ["plan_packaged_e2e", "--coverage-audit"]),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(staged.main(), 1)
        self.assertIn("fail closed", output.getvalue())


if __name__ == "__main__":
    unittest.main()
