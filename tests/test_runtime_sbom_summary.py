"""Synthetic regression fixtures for the bounded, advisory runtime SBOM report."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ci.summarize_runtime_sbom import (
    TOP_LIMIT,
    age_hours,
    markdown,
    read_json,
    safe,
    summarize,
    PACKAGE,
)

IMAGE = "sha256:" + "b" * 64


def row(
    advisory: str,
    pkg: str,
    kind: str = "deb",
    severity: str = "High",
    fix_state: str = "fixed",
    versions: list[str] | None = None,
    installed: str = "1.0",
) -> dict:
    return {
        "vulnerability": {
            "id": advisory,
            "severity": severity,
            "fix": {"state": fix_state, "versions": versions if versions is not None else ["2.0"]},
        },
        "artifact": {
            "id": "Package-" + pkg,
            "name": pkg,
            "version": installed,
            "type": kind,
            "locations": [{"path": "/never/publish/a/private/filename.pdf"}],
        },
    }


def fixture(*rows: dict) -> tuple[dict, dict]:
    return (
        {"spdxVersion": "SPDX-2.3", "packages": [{"name": "synthetic"}]},
        {
            "descriptor": {
                "name": "grype",
                "version": "0.121.0",
                "timestamp": "2026-10-25T14:46:00Z",
                "db": {"status": {"built": "2026-10-25T06:27:34Z"}},
                "configuration": {"secret": "never publish this"},
            },
            "distro": {"name": "debian", "version": "13"},
            "matches": list(rows),
        },
    )


class RuntimeSbomSummaryTests(unittest.TestCase):
    def test_unique_ids_pairs_and_separate_ecosystems(self) -> None:
        one = row("CVE-2026-12345", "libalpha")
        two = row("CVE-2026-12345", "libbeta", fix_state="wont-fix", versions=[])
        three = row("GHSA-abcd-1234-efgh", "pip", "python", "Medium")
        sbom, scan = fixture(one, one, two, three)
        output = summarize(sbom, scan, IMAGE)
        self.assertEqual(output["match_count"], 4)
        self.assertEqual(output["unique_advisory_ids"], 2)
        self.assertEqual(output["unique_package_advisory_pairs"], 3)
        self.assertEqual(output["by_ecosystem"]["OS: deb"]["matches"], 3)
        self.assertEqual(output["by_ecosystem"]["OS: deb"]["affected_packages"], 2)
        self.assertEqual(output["by_ecosystem"]["Python"]["matches"], 1)
        self.assertEqual(output["by_fix_state"]["fix-listed"], 3)
        self.assertEqual(output["by_fix_state"]["wont-fix"], 1)
        self.assertEqual(output["grype_db_age_hours"], 8.31)

    def test_same_package_advisory_with_two_versions_has_deterministic_order(self) -> None:
        old = row("CVE-2026-12345", "libalpha", installed="1.0")
        new = row("CVE-2026-12345", "libalpha", installed="1.1")
        old["artifact"]["id"] = "Package-libalpha-1.0"
        new["artifact"]["id"] = "Package-libalpha-1.1"
        sbom, scan = fixture(new, old)
        output = summarize(sbom, scan, IMAGE)
        self.assertEqual(output["match_count"], 2)
        self.assertEqual(output["unique_advisory_ids"], 1)
        self.assertEqual(output["unique_package_advisory_pairs"], 2)
        self.assertEqual(
            [entry["installed"] for entry in output["triage_examples"]],
            ["1.0", "1.1"],
        )

    def test_fix_listed_requires_usable_versions(self) -> None:
        a = row("CVE-2026-12345", "library", versions=["bad\nvalue"])
        sbom, scan = fixture(a)
        output = summarize(sbom, scan, IMAGE)
        self.assertEqual(output["by_fix_state"]["unknown"], 1)
        self.assertEqual(output["triage_examples"][0]["fixed_versions"], "none listed")

    def test_top_prioritizes_fix_listed_then_severity_and_is_bounded(self) -> None:
        sample = [row(f"CVE-2026-{20000 + i}", f"package{i}", severity="Low") for i in range(20)]
        sample += [
            row("CVE-2026-99001", "unfixed", severity="Critical", fix_state="not-fixed", versions=[]),
            row("CVE-2026-99002", "fix-critical", severity="Critical"),
            row("CVE-2026-99003", "fix-high", severity="High"),
        ]
        sbom, scan = fixture(*sample)
        output = summarize(sbom, scan, IMAGE)
        self.assertEqual(len(output["triage_examples"]), TOP_LIMIT)
        self.assertEqual(
            [v["severity"] for v in output["triage_examples"][:2]],
            ["Critical", "High"],
        )
        self.assertTrue(all(v["fix_state"] == "fix-listed" for v in output["triage_examples"]))

    def test_whitelisted_labels_no_private_paths_or_descriptions(self) -> None:
        secret = "private-document-name"
        sample = row("CVE-2026-12345", f"bad|{secret}", installed=f"1.0\n{secret}")
        sbom, scan = fixture(sample)
        scan["matches"][0]["vulnerability"]["description"] = secret
        scan["matches"][0]["artifact"]["locations"] = [{"path": "/home/user/" + secret}]
        output = summarize(sbom, scan, IMAGE)
        text = markdown(output) + json.dumps(output)
        self.assertNotIn(secret, text)
        self.assertNotIn("locations", text)
        self.assertNotIn("description", text)
        self.assertEqual(output["match_count"], 1)
        self.assertEqual(output["triage_examples"], [])

    def test_expected_grype_v6_database_metadata(self) -> None:
        sbom, scan = fixture()
        output = summarize(sbom, scan, IMAGE)
        self.assertEqual(output["grype_db_built_at"], "2026-10-25T06:27:34Z")
        self.assertEqual(output["grype_scan_at"], "2026-10-25T14:46:00Z")
        self.assertEqual(output["by_severity"]["Critical"], 0)

    def test_literal_redacted_is_a_valid_public_package_and_version(self) -> None:
        item = row("CVE-2026-12345", "redacted", installed="redacted", versions=["redacted"])
        sbom, scan = fixture(item)
        output = summarize(sbom, scan, IMAGE)
        self.assertEqual(output["triage_examples"][0]["package"], "redacted")
        self.assertEqual(output["triage_examples"][0]["installed"], "redacted")
        self.assertEqual(output["by_fix_state"]["fix-listed"], 1)

    def test_malformed_spdx_package_entry_is_rejected(self) -> None:
        sbom, scan = fixture()
        with self.assertRaisesRegex(ValueError, "malformed SPDX package"):
            summarize({"spdxVersion": "SPDX-2.3", "packages": [None]}, scan, IMAGE)

    def test_impossible_grype_timestamps_are_not_printed_as_valid(self) -> None:
        sbom, scan = fixture()
        scan["descriptor"]["timestamp"] = "2026-99-99T45:80:00Z"
        scan["descriptor"]["db"]["status"]["built"] = "2026-02-30T13:00:00Z"
        output = summarize(sbom, scan, IMAGE)
        self.assertEqual(output["grype_scan_at"], "unknown")
        self.assertEqual(output["grype_db_built_at"], "unknown")
        self.assertIsNone(output["grype_db_age_hours"])

    def test_bad_scanner_shapes_fail_closed(self) -> None:
        sbom, scan = fixture()
        with self.assertRaisesRegex(ValueError, "invalid built image"):
            summarize(sbom, scan, "invalid")
        with self.assertRaisesRegex(ValueError, "no packages"):
            summarize({"spdxVersion": "SPDX-2.3", "packages": []}, scan, IMAGE)
        with self.assertRaisesRegex(ValueError, "matches list"):
            summarize(sbom, {"descriptor": {"name": "grype"}}, IMAGE)
        with self.assertRaisesRegex(ValueError, "match row"):
            summarize(sbom, {**scan, "matches": [3]}, IMAGE)
        with self.assertRaisesRegex(ValueError, "descriptor"):
            summarize(sbom, {**scan, "descriptor": {"name": "another"}}, IMAGE)

    def test_missing_and_invalid_database_freshness(self) -> None:
        self.assertIsNone(age_hours("unknown", "2026-10-25T00:00:00Z"))
        self.assertIsNone(age_hours("2026-10-24T00:00:00Z", "2026-10-25T00:00:00Z"))
        self.assertEqual(age_hours("2026-10-25T08:00:00Z", "2026-10-25T07:00:00Z"), 1.0)
        sbom, scan = fixture()
        del scan["descriptor"]["db"]
        self.assertEqual(summarize(sbom, scan, IMAGE)["grype_db_built_at"], "unknown")

    def test_unusual_values_are_not_echoed_into_markdown(self) -> None:
        self.assertEqual(safe("ok-valid", PACKAGE), "ok-valid")
        self.assertEqual(safe("| malicious markdown", PACKAGE), "redacted")
        sbom, scan = fixture(row("PRIVATE-{do-not-leak}", "libalpha"))
        result = summarize(sbom, scan, IMAGE)
        self.assertEqual(result["triage_examples"], [])
        self.assertEqual(result["unique_advisory_ids"], 1)

    def test_bounded_file_reader(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.json"
            path.write_text(json.dumps({"matches": []}), encoding="utf-8")
            self.assertEqual(read_json(path), {"matches": []})
            path.write_text("[1, 2]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON object"):
                read_json(path)
            with patch("scripts.ci.summarize_runtime_sbom.MAX_INPUT_BYTES", 2):
                with self.assertRaisesRegex(ValueError, "bounded size limit"):
                    read_json(path)


if __name__ == "__main__":
    unittest.main()
