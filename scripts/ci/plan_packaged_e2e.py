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
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    # Support both `python -m scripts.ci.plan_packaged_e2e` and direct CLI.
    sys.path.insert(0, str(ROOT))

from tests.e2e import policy

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


def _view_path_rejection(paths: list[str]) -> str | None:
    """Only explicitly safe view files and harmless companions qualify."""
    views = 0
    for path in paths:
        if _safe_nonproduction_companion(path):
            continue
        if policy.classify_affected_path(path)["skip"]:
            return "unrecognized nonproduction companion: " + path
        if _is_generated_companion(path):
            # Source mapping does not prove generated asset reproducibility.
            return "generated frontend assets require full packaged E2E"
        if path not in SAFE_VUE_VIEWS:
            return "outside narrow feature-owned Vue view allowlist: " + path
        views += 1
    if not views:
        return "no recognized feature-owned view change"
    return None


def _candidate_test_ids(features: tuple[str, ...]) -> tuple[tuple[str, ...], str | None]:
    """Discover the authoritative E2E suite and include every affected feature.

    Never trust a provided inventory or a nonempty smoke-only selection.
    """
    ids = _discover_complete_e2e_ids()
    if not ids or len(set(ids)) != len(ids) or not all(
        isinstance(tid, str) and tid.startswith("tests.e2e.") for tid in ids
    ):
        return (), "invalid or incomplete E2E inventory"

    known = set(ids)
    expected_smoke = (*policy.SMOKE_TEST_IDS, *EXTRA_PACKAGED_SMOKE_IDS)
    if any(tid not in known for tid in expected_smoke):
        return (), "required packaged smoke case missing from E2E inventory"

    # A nonempty combined smoke selection does not prove affected coverage.
    for feature in features:
        if feature != "smoke" and not policy.select_features(ids, (feature,)):
            return (), "no discovered tests for the affected feature: " + feature

    selected = policy.select_features(ids, features)
    union = tuple(dict.fromkeys((*expected_smoke, *selected)))
    if len(union) > MAX_SMOKE_AFFECTED_TESTS:
        return (), "affected selection too broad; keep full packaged shards"
    return union, None


def plan_packaged_e2e(
    changed_paths,
    *,
    path_diffs=None,
    force_full: bool = False,
) -> dict:
    """Stage an inactive packaged E2E proposal, defaulting to full coverage.

    Unknown/unsafe changes, generated assets and missing test discovery
    cannot reduce the six-shard packaged gate.
    """
    if force_full:
        return _result("release, master, manual override or operator requires full")
    try:
        paths = list(changed_paths)
    except (TypeError, ValueError):
        return _result("missing or invalid change list")
    if not paths or not all(_valid_path(p) for p in paths):
        return _result("empty, malformed or unsafe changed path list")

    source = policy.plan_ci_e2e(paths, path_diffs=path_diffs)
    if source.get("mode") != "affected":
        return _result(
            "source planner requires full or skip; packaged defaults full: "
            + str(source.get("reason", "unknown"))
        )

    rejection = _view_path_rejection(paths)
    if rejection:
        return _result(rejection)

    features = tuple(dict.fromkeys(source.get("features") or ()))
    if not features or "smoke" not in features:
        return _result("missing source-affected feature selection")
    try:
        ids, error = _candidate_test_ids(features)
    except (TypeError, ValueError, KeyError, ImportError, OSError, RuntimeError) as exc:
        return _result("test selection failed closed: " + type(exc).__name__)
    if error:
        return _result(error)

    return _result(
        "candidate only: mapped feature-owned views + expanded packaged smoke; "
        "workflow remains full until explicit maintainer approval",
        candidate=True,
        features=features,
        test_ids=ids,
    )


def _discover_complete_e2e_ids(*, default_modules: bool = False) -> list[str]:
    """Use live unittest modules; audit can pin the ordinary full gate.

    Imports may require pinned development dependencies. If discovery fails
    (including unittest's synthetic _FailedTest entries), choose full.
    """
    from tests.e2e import run

    # The installed classic-transition/accessibility opt-ins alter
    # run.E2E_MODULES at import time. Only the advisory coverage audit
    # explicitly pins the ordinary default packaged/source full module set.
    # The dormant candidate planner retains its existing discovery behavior.
    ids = (run.discover_test_ids(modules=run.DEFAULT_E2E_MODULES)
           if default_modules else run.discover_test_ids())
    if (
        not ids
        or run.invalid_e2e_discovery_ids(ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("incomplete E2E unittest discovery")
    return ids



# Versioned advisory obligations: these are *specific browser behaviors*, not
# substring-matched classes or a statement of full defect equivalence.
# Keep the real policy's nine smoke IDs and five packaged extras as the only
# smoke-selector source of truth; this table does not select tests.
COVERAGE_OBLIGATION_VERSION = 1
CRITICAL_BROWSER_OBLIGATIONS = (
    ("app-shell-navigation",
     "tests.e2e.test_app.AppShellAndNavigationTests.test_app_loads_and_real_navigation"),
    ("main-secondary-divider",
     "tests.e2e.test_app.MainSecondaryDividerTests.test_divider_drag_causes_no_additional_route_or_pdf_requests"),
    ("offline-cached-reload",
     "tests.e2e.test_offline.OfflineFoundationTests.test_cached_work_renders_offline_after_reload"),
    ("offline-install-disconnect",
     "tests.e2e.test_offline.OfflineFoundationTests.test_persistence_setup_abandons_on_disconnect_before_worker_install"),
    ("offline-default-navigation",
     "tests.e2e.test_folders_offline.FoldersOfflineTests.test_default_offline_launch_lands_on_the_cached_hierarchy"),
    ("durable-operation-reload",
     "tests.e2e.test_local_store_durability.LocalStoreDurabilityTests.test_a_pending_operation_survives_a_page_reload"),
    ("durable-field-reconciliation",
     "tests.e2e.test_work_metadata_offline.OfflineWorkMetadataTests.test_offline_field_edit_survives_reload_and_synchronizes"),
    ("offline-pdf-reopen",
     "tests.e2e.test_offline.OfflineFoundationTests.test_previously_opened_pdf_reopens_offline"),
    ("offline-pdf-annotation-tools",
     "tests.e2e.test_offline.OfflineFoundationTests.test_cached_pdf_reopened_offline_keeps_annotation_tools"),
    ("offline-annotation-reconnect",
     "tests.e2e.test_offline.OfflineFoundationTests.test_pending_annotation_survives_disconnect_and_resumes_on_reconnect"),
    ("pdf-annotation-persistence",
     "tests.e2e.test_app.PdfPersistenceTests.test_highlight_persists_through_handshake_reload_and_delete"),
    ("graph-offline-navigation",
     "tests.e2e.test_research_graph_offline.ResearchGraphOfflineTests.test_record_graph_navigation_both_directions"),
    ("vue-processing-route-ready",
     "tests.e2e.test_processing_route_surface.ProcessingRouteSurfaceTests.test_processing_files_page_mounts_from_the_route"),
    ("research-notes-draft-recovery",
     "tests.e2e.test_work_notes_offline.ResearchNotesRecoveryTests.test_reload_within_500_ms_restores_the_exact_newest_text_and_saves_it"),
)
# These are named independent PR-required job obligations, not E2E IDs.
# Their success must be checked separately in GitHub Actions; merely listing
# them here NEVER asserts that a particular workflow run passed.
SEPARATE_GUARD_OBLIGATIONS = (
    ("distribution-docker-build", "Docker classic bundle and rollback build smoke"),
    ("service-worker-upgrade-rollback",
     "Classic bundle SW upgrade and rollback (real Chromium)"),
)
_E2E_ID = re.compile(
    r"^tests\.e2e\.(?:[A-Za-z_]\w*\.){2}test_[A-Za-z0-9_]+$"
)
_SELECTOR_ID = re.compile(r"^tests\.e2e\.(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*$")


def _validated_coverage_inventory(all_ids, required_modules, feature_map) -> tuple[list[str], list[str]]:
    """Validate a complete suite and policy shape before deriving any coverage.

    Module membership is checked against the runner's declared full suite, not
    against the curated smoke selection. Missing individual critical IDs are
    *reported* as missing rather than accidentally treated as excluded.
    """
    if not isinstance(all_ids, (tuple, list)) or not all_ids:
        raise ValueError("missing or empty E2E discovery")
    if (any(not isinstance(t, str) or not _E2E_ID.fullmatch(t) for t in all_ids)
            or len(set(all_ids)) != len(all_ids)):
        raise ValueError("malformed or duplicated E2E discovery IDs")
    if (not isinstance(required_modules, (tuple, list))
            or not required_modules
            or any(not isinstance(m, str) or not _SELECTOR_ID.fullmatch(m)
                   for m in required_modules)
            or len(set(required_modules)) != len(required_modules)):
        raise ValueError("missing or malformed expected module inventory")
    if not isinstance(feature_map, dict) or not feature_map:
        raise ValueError("missing or malformed feature mapping")
    for name, definition in feature_map.items():
        if (not isinstance(name, str) or not name
                or not isinstance(definition, dict)):
            raise ValueError("malformed E2E feature mapping")
        selectors = definition.get("selectors")
        if (not isinstance(selectors, (tuple, list)) or not selectors
                or any(not isinstance(s, str) or not _SELECTOR_ID.fullmatch(s)
                       for s in selectors)
                or len(set(selectors)) != len(selectors)):
            raise ValueError("malformed E2E feature selectors: " + name)
    modules_present = {tid.rsplit(".", 2)[0] for tid in all_ids}
    missing = sorted(set(required_modules) - modules_present)
    # A wholly disconnected feature map is not usable. Some individual
    # selectors may intentionally match zero tests (e.g. optional classes),
    # so list those as advisory diagnostics instead of rejecting the suite.
    prefixes = [
        prefix for definition in feature_map.values()
        for prefix in definition["selectors"]
    ]
    if not any(
        tid == prefix or tid.startswith(prefix + ".")
        for prefix in prefixes for tid in all_ids
    ):
        raise ValueError("feature mapping has no discovered E2E coverage")
    return sorted(all_ids), missing


def audit_packaged_coverage(
    all_ids,
    *,
    candidate_ids=None,
    required_modules=None,
    feature_map=None,
) -> dict:
    """Build a deterministic, always-advisory full-vs-smoke risk report.

    The supplied inventory must come from complete source unittest discovery.
    Tests may supply synthetic IDs and a matching expected module list.
    Missing critical obligations are surfaced, never counted as covered.
    """
    from tests.e2e import run

    if required_modules is None:
        required_modules = run.DEFAULT_E2E_MODULES
    if feature_map is None:
        feature_map = policy.FEATURES
    full, missing_modules = _validated_coverage_inventory(
        all_ids, required_modules, feature_map
    )
    full_set = set(full)
    smoke_floor = (*policy.SMOKE_TEST_IDS, *EXTRA_PACKAGED_SMOKE_IDS)
    if len(smoke_floor) != 14 or len(set(smoke_floor)) != 14:
        raise ValueError("packaged smoke floor changed unexpectedly")
    candidate = smoke_floor if candidate_ids is None else candidate_ids
    if (not isinstance(candidate, (list, tuple)) or not candidate
            or any(not isinstance(t, str) or not _E2E_ID.fullmatch(t) for t in candidate)
            or len(set(candidate)) != len(candidate)
            or not set(smoke_floor).issubset(candidate)):
        raise ValueError("malformed or incomplete packaged smoke candidate")
    candidate_set = set(candidate)
    unknown_candidate = sorted(candidate_set - full_set)
    rows = []
    for obligation, tid in CRITICAL_BROWSER_OBLIGATIONS:
        if tid not in full_set:
            state = "missing_from_discovery"
        elif tid in candidate_set:
            state = "included_in_smoke"
        else:
            state = "excluded_from_smoke"
        rows.append({
            "obligation": obligation, "test_id": tid,
            "source_full": tid in full_set,
            "packaged_full": tid in full_set,
            "packaged_smoke": tid in candidate_set and tid in full_set,
            "status": state,
        })
    for obligation, guard in SEPARATE_GUARD_OBLIGATIONS:
        rows.append({
            "obligation": obligation,
            "test_id": None, "guard_job": guard,
            "source_full": False, "packaged_full": False,
            "packaged_smoke": False, "status": "separate_guard_only",
            "guard_verification": "requires an independently passing Actions guard job",
        })
    mapped = set()
    unmatched_selectors = set()
    for definition in feature_map.values():
        for prefix in definition["selectors"]:
            matches = {
                tid for tid in full if tid == prefix or tid.startswith(prefix + ".")
            }
            if not matches:
                unmatched_selectors.add(prefix)
            mapped.update(matches)
    missing_required = sorted(
        (set(smoke_floor) | {tid for _, tid in CRITICAL_BROWSER_OBLIGATIONS})
        - full_set
    )
    valid_inventory = not (missing_modules or missing_required or unknown_candidate)
    return {
        "version": COVERAGE_OBLIGATION_VERSION,
        "active": False,
        "effective_mode": "full",
        "proposed_mode": "advisory_smoke_comparison",
        "coverage_complete": valid_inventory,
        "safe_to_activate_smoke": False,
        "source_discovered_count": len(full),
        "packaged_full_count": len(full),
        "packaged_smoke_count": len(candidate_set & full_set),
        "expected_full_modules": len(required_modules),
        "missing_modules": missing_modules,
        "missing_required_ids": missing_required,
        "unknown_candidate_ids": unknown_candidate,
        "unmatched_source_ids": sorted(full_set - mapped),
        "unmatched_feature_selectors": sorted(unmatched_selectors),
        "critical_rows": rows,
        "limitation": (
            "Class/ID membership cannot prove defect equivalence; separate CI "
            "guards must be independently verified and no policy is activated."
        ),
    }


def format_packaged_coverage_audit(report: dict) -> str:
    """Deterministic, human-readable evidence; no silent missing-ID success."""
    lines = [
        f"Packaged full vs dormant smoke: critical coverage v{report['version']} (ADVISORY)",
        f"Active policy: {report['effective_mode']} (unchanged); activation: NOT authorized",
        f"Inventory complete: {'yes' if report['coverage_complete'] else 'NO — fail closed'}",
        (f"Discovered source full / packaged full / packaged smoke tests: "
         f"{report['source_discovered_count']} / {report['packaged_full_count']} / "
         f"{report['packaged_smoke_count']}"),
        "",
        "Critical obligation | Full | Dormant smoke | Status | Exact ID or independent guard",
    ]
    for row in report["critical_rows"]:
        lines.append(
            f"{row['obligation']} | "
            f"{'yes' if row['packaged_full'] else 'no'} | "
            f"{'yes' if row['packaged_smoke'] else 'no'} | {row['status']} | "
            f"{row.get('test_id') or row['guard_job']}"
        )
    for key in ("missing_modules", "missing_required_ids", "unknown_candidate_ids",
                "unmatched_source_ids", "unmatched_feature_selectors"):
        values = report[key]
        lines.append("")
        lines.append(f"{key}: {len(values)}")
        lines.extend("  " + value for value in values)
    lines.append("")
    lines.append(report["limitation"])
    return "\n".join(lines)


def _untracked_frontend_app_paths(repo: Path) -> list[str]:
    """Include new Vue files absent from shared E2E untracked prefixes.

    Reuse the E2E policy's existing read-only, fail-closed Git runner rather
    than introducing another subprocess implementation.
    """
    output = policy._git_stdout(
        repo,
        ["ls-files", "--others", "--exclude-standard", "-z", "--", "frontend-app/"],
        "untracked Vue discovery",
    )
    return [path for path in output.split("\0") if path]

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", help="Existing single Git base revision (e.g. origin/master)"
    )
    parser.add_argument(
        "--coverage-audit", action="store_true",
        help="Read-only versioned critical coverage report (no selection/CI changes)"
    )
    parser.add_argument(
        "--force-full", action="store_true", help="Release/high-risk/manual full override"
    )
    args = parser.parse_args()
    if args.coverage_audit:
        try:
            report = audit_packaged_coverage(
                _discover_complete_e2e_ids(default_modules=True)
            )
            print(format_packaged_coverage_audit(report))
            return 0 if report["coverage_complete"] else 1
        except (OSError, ValueError, TypeError, ImportError, RuntimeError) as exc:
            print("Critical coverage audit refused: " + type(exc).__name__
                  + ": " + str(exc), file=sys.stderr)
            return 1
    if not args.base:
        parser.error("--base is required unless --coverage-audit is specified")
    try:
        paths = policy.list_changed_paths(ROOT, args.base, include_untracked=True)
        for path in _untracked_frontend_app_paths(ROOT):
            if path not in paths:
                paths.append(path)
        diffs = {}
        if "frontend/sw.js" in paths:
            diffs["frontend/sw.js"] = policy.service_worker_diff_for_plan(ROOT, args.base)
        plan = plan_packaged_e2e(
            paths, path_diffs=diffs, force_full=args.force_full
        )
    except (OSError, ValueError, policy.ChangeDiscoveryError) as exc:
        plan = _result("change discovery failed closed: " + type(exc).__name__)
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
