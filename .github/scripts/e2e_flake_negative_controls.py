#!/usr/bin/env python3
"""Prove the current PDF-recovery and Person-coherence E2E oracles fail closed.

Only run in the isolated acceptance-stress job. A temporary in-memory test
mutation deliberately removes one required recovery/invalidation observation.
The original test file is always restored, even on timeout or interruption.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tests/e2e/run.py"
PDF_TEST = (
    "tests.e2e.test_app.PdfAnnotationCommentEditTests."
    "test_a_failed_highlight_is_settled_by_saving_it_again"
)
PERSON_TEST = (
    "tests.e2e.test_person_groups_offline.PersonGroupsOfflineTests."
    "test_person_create_exclusion_and_delete_membership_coherence"
)

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


def run_control(name, relative_file, test_id, anchor, mutation, evidence):
    path = ROOT / relative_file
    original = path.read_text(encoding="utf-8")
    if original.count(anchor) != 1:
        raise RuntimeError(f"{name}: source anchor changed; refusing an unverified control")
    try:
        path.write_text(original.replace(anchor, mutation, 1), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--jobs", "1", "--no-pointer-capture", test_id],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
    finally:
        path.write_text(original, encoding="utf-8")

    output = result.stdout + "\n" + result.stderr
    if result.returncode == 0 or not all(token in output for token in evidence):
        print(f"FAIL: {name} mutation was not rejected for the required reason", flush=True)
        print(output[-12000:], flush=True)
        raise SystemExit(1)
    print(f"PASS (expected rejection): {name}; E2E returned {result.returncode}", flush=True)


def main():
    run_control(
        "PDF recovery omitted",
        "tests/e2e/test_app.py",
        PDF_TEST,
        PDF_ANCHOR,
        PDF_MUTATION,
        ("PRKS_NC_PDF_RECOVERY_OMITTED", "server never stored comment 'recovered highlight'"),
    )
    run_control(
        "Person Group generation suppressed",
        "tests/e2e/test_person_groups_offline.py",
        PERSON_TEST,
        PERSON_ANCHOR,
        PERSON_MUTATION,
        ("PRKS_NC_PERSON_GROUP_INVALIDATION_SUPPRESSED", "person-groups", "not greater than"),
    )
    print("PASS: both independent data-safety oracle negative controls rejected defects")


if __name__ == "__main__":
    main()
