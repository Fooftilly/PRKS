"""Dormant, fail-closed candidate policy for packaged PR E2E (#740).

This module is advisory ONLY. No GitHub Actions job imports it or consumes its
output. Existing packaged PRs continue running six full shards until a separate
maintainer-approved workflow change. Reuse the source E2E path/feature mapping,
and apply a much narrower allowlist before considering smoke + affected tests.

Manual read-only invocation (from the repository root):
  python -m scripts.ci.plan_packaged_e2e --base origin/master
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from tests.e2e import policy

ROOT = Path(__file__).resolve().parents[2]

# Only isolated, feature-owned Vue view files are potential reduced-coverage
# candidates. Do not allow feature-wide wildcards: new files, shared helpers,
# TS state/routes, backend changes and cross-feature dependencies fail full.
SAFE_VUE_VIEWS = frozenset(
    {
        "frontend-app/src/features/processing/ProcessingFileCard.vue",
        "frontend-app/src/features/processing/ProcessingFilesRoute.vue",
        "frontend-app/src/features/progress/ProgressView.vue",
        "frontend-app/src/features/publishers/PublishersRoute.vue",
        "frontend-app/src/features/types/TypeDetailRoute.vue",
        "frontend-app/src/features/types/TypesIndexRoute.vue",
    }
)

# Keep the existing full packaged smoke floor: nine curated policy IDs and
# five explicit offline/PDF/Graph cases from the manual packaged workflow.
# The contract test checks the manual workflow's executed IDs against this.
EXTRA_PACKAGED_SMOKE_IDS = (
    "tests.e2e.test_app.MainSecondaryDividerTests.test_divider_drag_causes_no_additional_route_or_pdf_requests",
    "tests.e2e.test_app.PdfPersistenceTests.test_annotation_copy_link_reports_clipboard_success_and_failure",
    "tests.e2e.test_offline.OfflineFoundationTests.test_previously_opened_pdf_reopens_offline",
    "tests.e2e.test_offline.OfflineFoundationTests.test_cached_pdf_reopened_offline_keeps_annotation_tools",
    "tests.e2e.test_research_graph_offline.ResearchGraphOfflineTests.test_record_graph_navigation_both_directions",
)

# A large affected selection loses the intended cost advantage and should
# require the full suite rather than quietly weakening regression detection.
MAX_SMOKE_AFFECTED_TESTS = 100


def _is_generated_companion(path: str) -> bool:
    return (
        path.startswith("frontend/vue/")
        or path == "frontend/vendor/DEPENDENCY-MANIFEST.json"
        or path == "frontend/sw.js"
    )


def _safe_nonproduction_companion(path: str) -> bool:
    """Only allow known documentation and feature-local unit tests as extras."""
    if path in {"README.md", "AGENTS.md"}:
        return True
    if path.startswith("docs/") and path.endswith(".md"):
        return True
    if path.endswith(".test.ts"):
        return any(
            path.startswith(view.rsplit("/", 1)[0] + "/")
            for view in SAFE_VUE_VIEWS
        )
    return False


def _valid_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\x00" in value:
        return False
    normalized = value.replace("\\", "/")
    return (
        normalized == value
        and not value.startswith("/")
        and not any(piece in ("", ".", "..") for piece in value.split("/"))
    )


def _result(
    reason: str,
    *,
    candidate: bool = False,
    features: tuple[str, ...] = (),
    test_ids: tuple[str, ...] = (),
) -> dict:
    return {
        "active": False,
        "effective_mode": "full",
        "proposed_mode": "smoke_affected" if candidate and test_ids else "full",
        "candidate": candidate,
        "reason": reason,
        "features": list(features),
        "test_ids": list(test_ids),
        "test_count": len(test_ids),
        "full_shards_if_executed": 6,
        "required_independent_guards": [
            "docker-build-modes",
            "worker-upgrade-rollback",
        ],
    }


def plan_packaged_e2e(
    changed_paths,
    *,
    known_test_ids=None,
    path_diffs=None,
    force_full: bool = False,
) -> dict:
    """Propose a reduced packaged leg, but ALWAYS return effective_mode=full.

    Missing changes, incomplete test discovery, unknown paths, unverified
    generated Vue companions, or ambiguous safety rules fail closed. In the
    eventual activation PR, the real E2E test inventory must be discovered
    *before* using selected IDs; selection must still fail full on errors.
    """
    if force_full:
        return _result("release, master, manual override or operator requires full")
    try:
        paths = list(changed_paths)
    except (TypeError, ValueError):
        return _result("missing or invalid change list")
    if not paths or not all(_valid_path(p) for p in paths):
        return _result("empty, malformed or unsafe changed path list")

    # Authoritative CI mapping accounts for Vue-generated bundle provenance,
    # service-worker revision-only diffs, and shared/unmapped production paths.
    source = policy.plan_ci_e2e(paths, path_diffs=path_diffs)
    if source.get("mode") != "affected":
        return _result(
            "source planner requires full or skip; packaged defaults full: "
            + str(source.get("reason", "unknown"))
        )

    view_paths = []
    for path in paths:
        classified = policy.classify_affected_path(path)
        if classified["skip"]:
            # The source planner legitimately skips documentation and unit
            # tests, but may also skip unfamiliar support paths. Do not let
            # unknown or CI-relevant changes hide behind a safe view diff.
            if _safe_nonproduction_companion(path):
                continue
            return _result("unrecognized nonproduction companion: " + path)
        if _is_generated_companion(path):
            # Already checked by the source planner: a generated companion
            # without mapped Vue source and verified SW stamp requires full.
            continue
        if path not in SAFE_VUE_VIEWS:
            return _result("outside narrow feature-owned Vue view allowlist: " + path)
        view_paths.append(path)
    if not view_paths:
        return _result("no recognized feature-owned view change")

    features = tuple(dict.fromkeys(source.get("features") or ()))
    if not features or "smoke" not in features:
        return _result("missing source-affected feature selection")

    if known_test_ids is None:
        return _result(
            "eligible view paths, but exact E2E discovery is required before reduction",
            candidate=True,
            features=features,
        )
    try:
        ids = list(known_test_ids)
        if not ids or len(set(ids)) != len(ids) or not all(
            isinstance(tid, str) and tid.startswith("tests.e2e.") for tid in ids
        ):
            return _result("invalid or incomplete E2E inventory")
        known = set(ids)
        expected_smoke = (*policy.SMOKE_TEST_IDS, *EXTRA_PACKAGED_SMOKE_IDS)
        if any(tid not in known for tid in expected_smoke):
            return _result("required packaged smoke case missing from E2E inventory")
        # 'smoke' is always present in the source affected plan, so testing
        # the combined set alone cannot prove a feature-owned case exists.
        affected = policy.select_features(
            ids, tuple(feature for feature in features if feature != "smoke")
        )
        if not affected:
            return _result("no discovered tests for the affected feature")
        selected = policy.select_features(ids, features)
        union = tuple(dict.fromkeys((*expected_smoke, *selected)))
        if len(union) > MAX_SMOKE_AFFECTED_TESTS:
            return _result("affected selection too broad; keep full packaged shards")
    except (TypeError, ValueError, KeyError) as exc:
        return _result("test selection failed closed: " + type(exc).__name__)

    return _result(
        "candidate only: mapped feature-owned views + expanded packaged smoke; "
        "workflow remains full until explicit maintainer approval",
        candidate=True,
        features=features,
        test_ids=union,
    )


def _untracked_frontend_app_paths(repo: Path) -> list[str]:
    """Include new Vue files absent from the shared E2E git-discovery prefixes.

    The shared source planner discovers untracked frontend/ paths, but its
    untracked prefix set does not yet include frontend-app/. Do not change the
    live source E2E policy for an inactive packaged experiment.
    """
    command = [
        "git", "-C", str(repo), "ls-files", "--others",
        "--exclude-standard", "-z", "--", "frontend-app/",
    ]
    try:
        proc = subprocess.run(command, capture_output=True, check=False)
    except OSError as exc:
        raise policy.ChangeDiscoveryError("untracked Vue discovery unavailable") from exc
    if proc.returncode:
        raise policy.ChangeDiscoveryError("untracked Vue discovery failed")
    return [path for path in proc.stdout.decode("utf-8").split("\0") if path]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", required=True, help="Existing single Git base revision (e.g. origin/master)"
    )
    parser.add_argument(
        "--test-ids-json",
        type=Path,
        help="Optional JSON array from complete current E2E discovery; absent = full",
    )
    parser.add_argument(
        "--force-full", action="store_true", help="Release/high-risk/manual full override"
    )
    args = parser.parse_args()
    try:
        paths = policy.list_changed_paths(ROOT, args.base, include_untracked=True)
        for path in _untracked_frontend_app_paths(ROOT):
            if path not in paths:
                paths.append(path)
        diffs = {}
        if "frontend/sw.js" in paths:
            diffs["frontend/sw.js"] = policy.service_worker_diff_for_plan(ROOT, args.base)
        ids = None
        if args.test_ids_json:
            ids = json.loads(args.test_ids_json.read_text(encoding="utf-8"))
        plan = plan_packaged_e2e(
            paths, known_test_ids=ids, path_diffs=diffs, force_full=args.force_full
        )
    except (OSError, ValueError, policy.ChangeDiscoveryError) as exc:
        plan = _result("change discovery failed closed: " + type(exc).__name__)
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
