#!/usr/bin/env python3
"""Bounded, privacy-safe triage of a built runtime image's Syft/Grype reports.

This is advisory: advisory IDs are not confirmed exploitable findings, and
reported fixed versions still require compatibility and reachability review.
Only whitelisted metadata and vetted public package/advisory labels are emitted.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

MAX_INPUT_BYTES = 24 * 1024 * 1024
TOP_LIMIT = 12
SEVERITIES = ("Critical", "High", "Medium", "Low", "Negligible", "Unknown")
FIX_STATES = ("fix-listed", "wont-fix", "not-fixed", "unknown")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
ADVISORY_ID = re.compile(r"(?:CVE-[0-9]{4}-[0-9]{4,}|GHSA-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4})\Z")
PACKAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.-]{0,79}\Z")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.:~+-]{0,49}\Z")
TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z\Z")


def read_json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("scanner input exceeds the bounded size limit")
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("scanner input must be a JSON object")
    return result


def safe(value: Any, pattern: re.Pattern[str], fallback: str = "redacted") -> str:
    return value if isinstance(value, str) and pattern.fullmatch(value) else fallback


def ecosystem(kind: Any) -> str:
    # Names come from Grype's package types, not from package/file paths.
    return {
        "deb": "OS: deb",
        "rpm": "OS: rpm",
        "apk": "OS: apk",
        "python": "Python",
        "npm": "JavaScript",
        "binary": "Binary/other",
        "UnknownPackage": "Binary/other",
    }.get(kind, "Other")


def iso(value: Any) -> str:
    return safe(value, TIMESTAMP, "unknown")


def age_hours(scan_time: str, db_time: str) -> float | None:
    if scan_time == "unknown" or db_time == "unknown":
        return None
    try:
        scan = datetime.fromisoformat(scan_time.replace("Z", "+00:00"))
        db = datetime.fromisoformat(db_time.replace("Z", "+00:00"))
    except ValueError:
        return None
    delta = (scan - db).total_seconds() / 3600
    return round(delta, 2) if delta >= 0 else None


def project_match(row: Any) -> dict[str, Any]:
    """Validate one Grype match and project only bounded, public labels."""
    if not isinstance(row, dict):
        raise ValueError("malformed Grype match row")
    vuln, art = row.get("vulnerability"), row.get("artifact")
    if not isinstance(vuln, dict) or not isinstance(art, dict):
        raise ValueError("malformed Grype vulnerability or artifact")
    advisory, package_id = vuln.get("id"), art.get("id")
    if not isinstance(advisory, str) or not advisory:
        raise ValueError("match has no advisory identifier")
    if not isinstance(package_id, str) or not package_id:
        raise ValueError("match has no package identity")
    severity = vuln.get("severity", "Unknown")
    if severity not in SEVERITIES:
        severity = "Unknown"
    fix = vuln.get("fix") or {}
    if not isinstance(fix, dict):
        fix = {}
    versions = fix.get("versions")
    valid_versions = [v for v in versions if safe(v, VERSION) != "redacted"] if isinstance(versions, list) else []
    state = fix.get("state")
    state_bucket = "fix-listed" if state == "fixed" and valid_versions else (
        state if state in ("wont-fix", "not-fixed") else "unknown"
    )
    group = ecosystem(art.get("type"))
    name = safe(art.get("name"), PACKAGE)
    example = None
    if ADVISORY_ID.fullmatch(advisory) and name != "redacted":
        example = {
            "advisory": advisory,
            "package": name,
            "installed": safe(art.get("version"), VERSION),
            "ecosystem": group,
            "severity": severity,
            "fix_state": state_bucket,
            "fixed_versions": ", ".join(valid_versions[:2]) if valid_versions else "none listed",
        }
    return {
        "id": advisory, "package_id": package_id, "severity": severity,
        "fix_state": state_bucket, "ecosystem": group, "example": example,
    }


def tally_matches(matches: list[Any]) -> dict[str, Any]:
    """Count rows, distinct identities, ecosystems and prioritized examples."""
    severity_counts: collections.Counter[str] = collections.Counter()
    fix_counts: collections.Counter[str] = collections.Counter()
    ecosystems: dict[str, dict[str, Any]] = {}
    advisory_ids: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    candidates: list[tuple[int, int, str, str, str, dict[str, Any]]] = []
    for row in matches:
        info = project_match(row)
        advisory = info["id"]
        package_id = info["package_id"]
        group = info["ecosystem"]
        state = info["fix_state"]
        severity = info["severity"]
        severity_counts[severity] += 1
        fix_counts[state] += 1
        advisory_ids.add(advisory)
        pairs.add((package_id, advisory))
        bucket = ecosystems.setdefault(group, {"matches": 0, "advisories": set(), "packages": set(), "fix_listed": 0})
        bucket["matches"] += 1
        bucket["advisories"].add(advisory)
        bucket["packages"].add(package_id)
        bucket["fix_listed"] += state == "fix-listed"
        example = info["example"]
        if example is not None:
            candidates.append((
                0 if state == "fix-listed" else 1, SEVERITIES.index(severity),
                group, example["package"], advisory, example,
            ))
    return {
        "match_count": len(matches),
        "unique_advisory_ids": len(advisory_ids),
        "unique_package_advisory_pairs": len(pairs),
        "by_severity": {key: severity_counts[key] for key in SEVERITIES},
        "by_fix_state": {key: fix_counts[key] for key in FIX_STATES},
        "by_ecosystem": {
            group: {
                "matches": bucket["matches"],
                "unique_advisory_ids": len(bucket["advisories"]),
                "affected_packages": len(bucket["packages"]),
                "fix_listed": bucket["fix_listed"],
            }
            for group, bucket in sorted(ecosystems.items())
        },
        "triage_examples": [
            entry[-1] for entry in sorted(
                candidates,
                key=lambda entry: (*entry[:-1], entry[-1]["installed"], entry[-1]["fixed_versions"]),
            )[:TOP_LIMIT]
        ],
    }


def summarize(sbom: dict[str, Any], scan: dict[str, Any], image_id: str) -> dict[str, Any]:
    if not str(sbom.get("spdxVersion", "")).startswith("SPDX-"):
        raise ValueError("expected a Syft SPDX JSON SBOM")
    packages = sbom.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ValueError("built runtime image SBOM has no packages")
    matches = scan.get("matches")
    if not isinstance(matches, list):
        raise ValueError("missing Grype matches list")
    if not IMAGE_ID.fullmatch(image_id):
        raise ValueError("invalid built image SHA256 identity")
    descriptor = scan.get("descriptor") or {}
    if not isinstance(descriptor, dict) or descriptor.get("name") != "grype":
        raise ValueError("expected Grype JSON descriptor")

    db = descriptor.get("db") or {}
    if not isinstance(db, dict):
        db = {}
    status = db.get("status") or {}
    if not isinstance(status, dict):
        status = {}
    distro = scan.get("distro") or {}
    if not isinstance(distro, dict):
        distro = {}
    distro_part = re.compile(r"[A-Za-z0-9._+-]{1,24}\Z")
    scan_time, db_time = iso(descriptor.get("timestamp")), iso(status.get("built"))
    return {
        "format": "prks-runtime-image-triage-v1",
        "image_id": image_id,
        "spdx_packages": len(packages),
        "grype_version": safe(descriptor.get("version"), VERSION, "unknown"),
        "grype_scan_at": scan_time,
        "grype_db_built_at": db_time,
        "grype_db_age_hours": age_hours(scan_time, db_time),
        "distro": safe(distro.get("name"), distro_part, "unknown"),
        "distro_version": safe(distro.get("version"), distro_part, "unknown"),
        **tally_matches(matches),
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "## Advisory built-runtime SBOM triage",
        "",
        f"- Image SHA256: {report['image_id']}",
        f"- SPDX package entries: {report['spdx_packages']}",
        f"- Grype version: {report['grype_version']}; scan: {report['grype_scan_at']}",
        f"- Grype database built: {report['grype_db_built_at']}; age (hours): {report['grype_db_age_hours']}",
        f"- Target distro: {report['distro']} {report['distro_version']}",
        f"- Match rows: {report['match_count']}; unique advisory IDs: {report['unique_advisory_ids']}; unique package/advisory pairs: {report['unique_package_advisory_pairs']}",
        "- Severities (matches): " + ", ".join(f"{k}={v}" for k, v in report["by_severity"].items()),
        "- Fix states (matches): " + ", ".join(f"{k}={v}" for k, v in report["by_fix_state"].items()),
        "",
        "### Affected package ecosystems",
        "| Package ecosystem | Matches | Advisory IDs | Packages | Fix version listed |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for group, row in report["by_ecosystem"].items():
        lines.append(f"| {group} | {row['matches']} | {row['unique_advisory_ids']} | {row['affected_packages']} | {row['fix_listed']} |")
    lines += [
        "",
        "### Prioritized examples (up to 12, fix-listed first)",
        "| Severity | Advisory | Package | Installed | Type | Fix state | Listed fixed versions |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for v in report["triage_examples"]:
        lines.append("| " + " | ".join(v[k] for k in (
            "severity", "advisory", "package", "installed", "ecosystem", "fix_state", "fixed_versions"
        )) + " |")
    lines += [
        "",
        "**Interpretation:** Counts are scanner matches, not confirmed exploitable issues. An advisory ID can",
        "appear against multiple packages; aliases may refer to the same underlying vulnerability.",
        "Listed fixes may target incompatible Python/OS major versions and require verification.",
        "OS/native dependencies in the shipped image add coverage beyond GitHub Dependency Review,",
        "which primarily reviews changes in supported package manifests. Python package alerts can overlap.",
        "Do not sum findings from these scanners or claim unique added coverage without matching identities.",
        "No issues are auto-waived, suppressed, or promoted to blocking status by this advisory pilot.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--grype", type=Path, required=True)
    parser.add_argument("--image-id-file", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    opts = parser.parse_args()
    start = time.perf_counter()
    report = summarize(
        read_json(opts.sbom), read_json(opts.grype),
        opts.image_id_file.read_text(encoding="utf-8").strip(),
    )
    opts.json_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    opts.markdown_output.write_text(markdown(report), encoding="utf-8")
    print(f"Runtime SBOM triage: {report['match_count']} scanner matches; "
          f"{report['unique_advisory_ids']} unique IDs; "
          f"summary time {time.perf_counter() - start:.3f}s")


if __name__ == "__main__":
    main()
