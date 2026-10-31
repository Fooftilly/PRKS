#!/usr/bin/env python3
"""Generate one E2E test-oracle mutation to stdout for isolated acceptance CI.

This helper never edits the checkout. The caller redirects the mutated module
into RUNNER_TEMP, installs it only in that job, and restores source in an EXIT
trap. Direct invocation merely emits source text; it does not run E2E tests.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PDF_ANCHOR = """        _wait_pdf_annotation_durable(page)
        _save_annotation_comment(page, ann_id, "recovered highlight")
        _wait_server_comment(page, work_id, ann_id, "recovered highlight")
"""
PDF_MUTATION = """        _wait_pdf_annotation_durable(page)
        # Deliberately omit the user's recovery write. The canonical server
        # must NOT contain a comment that was never saved.
        print("PRKS_NC_PDF_RECOVERY_OMITTED", flush=True)
        _wait_server_comment(page, work_id, ann_id, "recovered highlight", timeout=1200)
"""
PERSON_ANCHOR = """        self.assertFalse(page.evaluate(delete_invalidated, before))
        page.evaluate('() => { void deletePerson(); }')
        page.locator('#prks-modal-confirm-ok').click()
        page.wait_for_function(delete_invalidated, arg=before)
"""
PERSON_MUTATION = """        self.assertFalse(page.evaluate(delete_invalidated, before))
        # Freeze only the observer for Person Groups before the real delete.
        # The original deletion and People invalidation must still complete.
        page.evaluate(\"\"\"(baseline) => {
            const realGeneration = window.prksOfflineDomainGeneration;
            window.prksOfflineDomainGeneration = (domain) =>
                domain === 'person-groups'
                    ? baseline['person-groups']
                    : realGeneration(domain);
        }\"\"\", before)
        print("PRKS_NC_PERSON_GROUP_INVALIDATION_SUPPRESSED", flush=True)
        page.evaluate('() => { void deletePerson(); }')
        page.locator('#prks-modal-confirm-ok').click()
        page.wait_for_function(
            "baseline => prksOfflineDomainGeneration('people') > baseline.people",
            arg=before, timeout=15000)
        print("PRKS_NC_PERSON_PEOPLE_ADVANCED", flush=True)
        # The actual original readiness predicate must reject a missing
        # Person Group generation, not a later assertion of a stubbed value.
        page.wait_for_function(delete_invalidated, arg=before, timeout=2500)
"""

CONTROLS = {
    "pdf": ("tests/e2e/test_app.py", PDF_ANCHOR, PDF_MUTATION),
    "person": ("tests/e2e/test_person_groups_offline.py", PERSON_ANCHOR, PERSON_MUTATION),
}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in CONTROLS:
        raise SystemExit("usage: e2e_flake_negative_controls.py pdf|person")
    case = sys.argv[1]
    relative_file, anchor, mutation = CONTROLS[case]
    original = (ROOT / relative_file).read_text(encoding="utf-8")
    if original.count(anchor) != 1:
        raise SystemExit(f"{case}: source anchor changed; refusing an unverified control")
    sys.stdout.write(original.replace(anchor, mutation, 1))


if __name__ == "__main__":
    main()
