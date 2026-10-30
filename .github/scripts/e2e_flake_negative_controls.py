#!/usr/bin/env python3
"""Prepare isolated E2E oracle mutations for the acceptance workflow.

This script temporarily changes a tracked test source file on disk in the
dedicated CI checkout. The invoking shell restores the original file via an
EXIT trap after each control, including failures. No production files change.
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
PERSON_ANCHOR = """        o._wait_list_uncached(page, 'person-groups:index')
        self.changed(page, before, {'people','person-groups'})

    def test_work_role_changes_and_metadata_exclusion(self):
"""
PERSON_MUTATION = """        o._wait_list_uncached(page, 'person-groups:index')
        # The real delete and cache eviction already completed. Simulate
        # the lost Person Group generation notification at the test boundary.
        page.evaluate(\"\"\"(baseline) => {
            const realGeneration = window.prksOfflineDomainGeneration;
            window.prksOfflineDomainGeneration = (domain) =>
                domain === 'person-groups'
                    ? baseline['person-groups']
                    : realGeneration(domain);
        }\"\"\", before)
        print("PRKS_NC_PERSON_GROUP_INVALIDATION_SUPPRESSED", flush=True)
        self.changed(page, before, {'people','person-groups'})

    def test_work_role_changes_and_metadata_exclusion(self):
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
    path = ROOT / relative_file
    original = path.read_text(encoding="utf-8")
    if original.count(anchor) != 1:
        raise SystemExit(f"{case}: source anchor changed; refusing an unverified control")
    path.write_text(original.replace(anchor, mutation, 1), encoding="utf-8")
    print(f"Applied isolated {case} E2E negative control", flush=True)


if __name__ == "__main__":
    main()
