"""Declarative E2E selection: tiers, feature groups, affected-file mapping.

Pure logic — no Playwright, no subprocesses. Covered by
`tests/test_e2e_policy.py`. The runner (`tests/e2e/run.py`) is the only
entry point that applies these selections to a real Chromium run.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

LAST_FAILED_PATH = Path(".tests") / "e2e-last-failed.json"

# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------

TIERS = ("targeted", "feature", "smoke", "full", "dev", "last-failed", "affected")

TIER_LABELS = {
    "targeted": "targeted E2E (explicit test/module selection)",
    "feature": "feature/domain E2E",
    "smoke": "smoke E2E (small essential shell + critical workflows)",
    "full": "full E2E regression gate",
    "dev": "dev/agent E2E (fail-fast, no pointer-capture)",
    "last-failed": "last-failed E2E rerun",
    "affected": "affected E2E (git-diff → feature groups)",
}

# ---------------------------------------------------------------------------
# Smoke — explicit existing tests; never duplicate copies
# ---------------------------------------------------------------------------

SMOKE_TEST_IDS = (
    # App shell: Home → Work → Concepts → Graph
    "tests.e2e.test_app.AppShellAndNavigationTests.test_app_loads_and_real_navigation",
    # Command palette navigation
    "tests.e2e.test_app.AppShellAndNavigationTests.test_command_palette_opens_seeded_work",
    # Workspace tabs close/focus
    "tests.e2e.test_app.WorkspaceTabsTests.test_close_selects_right_neighbor_then_home",
    # New File / create control surface
    "tests.e2e.test_app.WorkCreateWorkflowTests.test_unified_create_control_and_menu",
    # Settings shell categories
    "tests.e2e.test_app.SettingsCategoryWorkflowTests.test_categories_present_general_default_and_calm",
    # Offline read cache: Work detail
    "tests.e2e.test_offline.OfflineFoundationTests.test_cached_work_renders_offline_after_reload",
    # Offline folders / default route
    "tests.e2e.test_folders_offline.FoldersOfflineTests.test_default_offline_launch_lands_on_the_cached_hierarchy",
    # Research Graph offline snapshot
    "tests.e2e.test_research_graph_offline.ResearchGraphOfflineTests.test_core_cache_and_local_interactions",
    # Durable local-store survives reload
    "tests.e2e.test_local_store_durability.LocalStoreDurabilityTests.test_a_pending_operation_survives_a_page_reload",
)

# Optional stress/stability scripts — never part of normal iteration or full gate.
STRESS_SCRIPTS = (
    "tests/e2e/stress_cache_offline.py",
)

# ---------------------------------------------------------------------------
# Feature groups — selectors match unittest IDs by prefix
# ---------------------------------------------------------------------------
# A selector is a dotted prefix of a test id (module, class, or full method).
# Prefer module-level groups; split test_app.py by class because it mixes domains.

FEATURES = {
    "shell": {
        "description": "App shell, navigation, command palette, request coordinator, design gallery",
        "selectors": (
            "tests.e2e.test_app.AppShellAndNavigationTests",
            "tests.e2e.test_app.RequestCoordinatorTests",
            "tests.e2e.test_app.MarkdownFixtureTests",
            "tests.e2e.test_app.DesignSystemGalleryTests",
        ),
    },
    "tabs": {
        "description": "Workspace tabs, warm parking, TabContext host roots",
        "selectors": (
            "tests.e2e.test_app.WorkspaceTabsTests",
            "tests.e2e.test_app.TabContextHostRootTests",
        ),
    },
    "tiling": {
        "description": "Split view / tiling, Main–Secondary divider",
        "selectors": (
            "tests.e2e.test_app.WorkspaceTilingTests",
            "tests.e2e.test_app.MainSecondaryDividerTests",
        ),
    },
    "workspace-drag": {
        "description": "Workspace pane/tab drag and drop",
        "selectors": ("tests.e2e.test_app.WorkspaceDragDropTests",),
    },
    "workspace-persistence": {
        "description": "Workspace localStorage persistence",
        "selectors": ("tests.e2e.test_app.WorkspacePersistenceTests",),
    },
    "graph": {
        "description": "Research Graph UI + offline projection cache",
        "selectors": (
            "tests.e2e.test_app.ResearchNoteConceptGraphTests",
            "tests.e2e.test_app.PersonGraphFocusTests",
            "tests.e2e.test_app.ResearchGraphContextTests",
            "tests.e2e.test_app.ResearchGraphChromeTests",
            "tests.e2e.test_app.ResearchGraphSplitWorkspaceTests",
            "tests.e2e.test_research_graph_offline",
        ),
    },
    "concepts": {
        "description": "Concepts index/detail polish + durable Concept ops + offline Concepts",
        "selectors": (
            "tests.e2e.test_app.ResearchIndexAndDetailPolishTests",
            "tests.e2e.test_app.ResearchPickerTests",
            "tests.e2e.test_concepts_durable",
            "tests.e2e.test_offline.OfflineConceptTests",
        ),
    },
    "positions": {
        "description": "Positions durable ops + offline Positions",
        "selectors": (
            "tests.e2e.test_positions_durable",
            "tests.e2e.test_offline.OfflinePositionTests",
        ),
    },
    "arguments": {
        "description": "Arguments/Stances durable ops + offline Arguments",
        "selectors": (
            "tests.e2e.test_arguments_durable",
            "tests.e2e.test_offline.OfflineArgumentTests",
            "tests.e2e.test_offline.OfflineArgumentCoherenceTests",
        ),
    },
    "people": {
        "description": "People UI, drafts, offline People, create/edit, Work↔Person roles",
        "selectors": (
            "tests.e2e.test_app.PersonProfileDraftOwnershipTests",
            "tests.e2e.test_app.DirtyNavigationGuardTests",
            "tests.e2e.test_app.PeopleSearchEmptyRoleTests",
            "tests.e2e.test_app.PersonGroupPolishTests",
            "tests.e2e.test_offline.OfflinePeopleTests",
            "tests.e2e.test_offline.OfflinePeopleMutationTests",
            "tests.e2e.test_offline.OfflinePeopleCoherenceTests",
            "tests.e2e.test_person_create_offline",
            "tests.e2e.test_person_edit_offline",
            "tests.e2e.test_work_people_offline",
        ),
    },
    "person-groups": {
        "description": "Person Groups hierarchy offline + durable",
        "selectors": (
            "tests.e2e.test_person_groups_offline",
            "tests.e2e.test_person_groups_durable",
        ),
    },
    "notes": {
        "description": "Research Notes / Reminders offline + related Work detail polish",
        "selectors": (
            "tests.e2e.test_work_notes_offline",
            "tests.e2e.test_app.WorkDetailsPolishTests",
            "tests.e2e.test_app.RightPanelNavigationOwnershipTests",
            "tests.e2e.test_app.PdfPersistenceTests",
        ),
    },
    "work-create": {
        "description": "New File / Work creation workflows",
        "selectors": ("tests.e2e.test_app.WorkCreateWorkflowTests",),
    },
    "settings": {
        "description": "Settings category navigation and persistence",
        "selectors": ("tests.e2e.test_app.SettingsCategoryWorkflowTests",),
    },
    "folders": {
        "description": "Folders/Home offline + durable Folder ops",
        "selectors": (
            "tests.e2e.test_folders_offline",
            "tests.e2e.test_folders_durable",
        ),
    },
    "playlists": {
        "description": "Playlists offline + durable",
        "selectors": (
            "tests.e2e.test_playlists_offline",
            "tests.e2e.test_playlists_durable",
        ),
    },
    "browse": {
        "description": "Progress/Types/Recent browse offline catalogs",
        "selectors": ("tests.e2e.test_browse_offline",),
    },
    "offline": {
        "description": "Offline foundation + broad offline coherence (Work cache)",
        "selectors": (
            "tests.e2e.test_offline.OfflineFoundationTests",
            "tests.e2e.test_local_store_durability",
        ),
    },
    "sync": {
        "description": "Local-first sync families (tags, opens, metadata, source)",
        "selectors": (
            "tests.e2e.test_work_tags_offline",
            "tests.e2e.test_work_opens_offline",
            "tests.e2e.test_work_metadata_offline",
            "tests.e2e.test_work_source_offline",
        ),
    },
}

# ---------------------------------------------------------------------------
# Affected-file mapping
# ---------------------------------------------------------------------------
# Patterns are matched with Path.match against repo-relative POSIX paths.
# First matching rule wins per path; features from all changed paths are
# unioned into the final selection (not first-match across the whole diff).

AFFECTED_RULES = (
    # E2E framework itself → smoke + runner unit coverage signal
    {
        "name": "e2e-framework",
        "paths": (
            "tests/e2e/run.py",
            "tests/e2e/harness.py",
            "tests/e2e/sharding.py",
            "tests/e2e/policy.py",
            "tests/e2e/install_browser.py",
            "tests/e2e/fixtures.py",
            "tests/e2e/__init__.py",
            "tests/browser/pointer_capture.py",
            "scripts/e2e",
        ),
        "features": ("smoke",),
        "fallback": "smoke",
        "note": "E2E runner/harness change → smoke suite (not silent skip)",
    },
    # Central shared infrastructure → broader than one domain
    {
        "name": "shared-frontend-core",
        "paths": (
            "frontend/js/app.js",
            "frontend/js/api.js",
            "frontend/js/ui.js",
            "frontend/js/request-coordinator.js",
            "frontend/js/tab-context.js",
            "frontend/js/command-palette.js",
            "frontend/index.html",
            "frontend/sw.js",
            "backend/server.py",
            "backend/db_manager.py",
            "backend/db_migrations.py",
            "backend/db_schema.sql",
            "prks_app.py",
        ),
        "features": ("smoke", "shell", "tabs", "offline", "sync"),
        "note": "Shared core → smoke + shell/tabs/offline/sync",
    },
    {
        "name": "graph",
        "paths": (
            "frontend/js/components/research-graph.js",
            "backend/research_graph.py",
            "tools/research-graph/**",
        ),
        "features": ("graph",),
    },
    {
        "name": "concepts",
        "paths": (
            "frontend/js/components/concepts.js",
            "frontend/js/concept-state.js",
            "backend/concept_sync.py",
            "backend/research_network.py",
            "backend/research_markup.py",
            "backend/research_index.py",
        ),
        "features": ("concepts", "graph", "notes"),
    },
    {
        "name": "positions",
        "paths": (
            "frontend/js/components/positions.js",
            "frontend/js/position-state.js",
            "backend/position_sync.py",
        ),
        "features": ("positions", "graph"),
    },
    {
        "name": "arguments",
        "paths": (
            "frontend/js/components/arguments.js",
            "frontend/js/argument-state.js",
            "backend/argument_sync.py",
        ),
        "features": ("arguments", "graph"),
    },
    {
        "name": "person-groups",
        "paths": (
            "frontend/js/components/person-groups.js",
            "frontend/js/person-group-state.js",
            "backend/person_group_sync.py",
        ),
        "features": ("person-groups", "people"),
    },
    {
        "name": "people",
        "paths": (
            "frontend/js/components/people.js",
            "frontend/js/work-role-editor.js",
            "frontend/js/work-role-state.js",
            "frontend/js/person-state.js",
            "frontend/js/person-metadata-state.js",
            "frontend/js/person-*.js",
            "backend/person_sync.py",
            "backend/person_metadata_sync.py",
            "backend/work_role_sync.py",
        ),
        "features": ("people", "person-groups"),
    },
    {
        "name": "notes",
        "paths": (
            "frontend/js/work-notes-state.js",
            "frontend/js/components/works.js",
            "frontend/js/works-pdf.js",
            "backend/work_note_sync.py",
            "backend/pdf_annotations.py",
        ),
        "features": ("notes",),
    },
    {
        "name": "workspace-tabs",
        "paths": (
            "frontend/js/workspace-tabs.js",
            "frontend/js/workspace-persistence.js",
            "frontend/js/workspace-tab-menu.js",
        ),
        "features": ("tabs", "workspace-persistence"),
    },
    {
        "name": "workspace-tiling",
        "paths": (
            "frontend/js/workspace-tiling.js",
            "frontend/js/workspace-tree.js",
            "frontend/js/workspace-split.js",
        ),
        "features": ("tiling", "tabs"),
    },
    {
        "name": "workspace-overview",
        "paths": (
            "frontend/js/workspace-overview.js",
            "frontend/js/overview-primitives.js",
            "frontend/js/work-selection.js",
        ),
        "features": ("tiling", "tabs"),
        "note": "Workspace overview / selection (PR #5) → tiling + tabs",
    },
    {
        "name": "workspace-drag",
        "paths": ("frontend/js/workspace-drag.js",),
        "features": ("workspace-drag", "tiling", "tabs"),
    },
    {
        "name": "folders",
        "paths": (
            "frontend/js/components/folders.js",
            "frontend/js/folder-state.js",
            "frontend/js/folder-tag-state.js",
            "frontend/js/folder-*.js",
            "backend/folder_sync.py",
            "backend/folder_tag_sync.py",
        ),
        "features": ("folders", "browse"),
    },
    {
        "name": "playlists",
        "paths": (
            "frontend/js/components/playlists.js",
            "frontend/js/playlist-state.js",
            "backend/playlist_sync.py",
        ),
        "features": ("playlists",),
    },
    {
        "name": "browse",
        "paths": (
            "frontend/js/components/progress.js",
            "frontend/js/components/types.js",
            "frontend/js/components/recent.js",
            "frontend/js/components/search.js",
        ),
        "features": ("browse",),
    },
    {
        "name": "settings",
        "paths": (
            "frontend/js/components/settings.js",
            "frontend/js/sync-diagnostics.js",
        ),
        "features": ("settings",),
    },
    {
        "name": "offline-runtime",
        "paths": (
            "frontend/js/offline-runtime.js",
            "frontend/js/offline-store.js",
            "frontend/js/local-store.js",
        ),
        "features": ("offline", "sync", "smoke"),
    },
    {
        "name": "work-lifecycle",
        "paths": (
            "frontend/js/work-lifecycle-state.js",
            "frontend/js/work-lifecycle-*.js",
            "backend/work_lifecycle_sync.py",
        ),
        "features": ("offline", "folders", "sync"),
        "note": "CREATE/DELETE_WORK lifecycle → offline + folders + sync",
    },
    {
        "name": "sync-families",
        "paths": (
            "frontend/js/sync-runtime.js",
            "frontend/js/work-tag-*.js",
            "frontend/js/work-open-*.js",
            "frontend/js/work-metadata-*.js",
            "frontend/js/work-source-*.js",
            "frontend/js/tag-vocabulary-state.js",
            "backend/sync_protocol.py",
            "backend/work_tag_sync.py",
            "backend/work_open_sync.py",
            "backend/work_metadata_sync.py",
            "backend/work_source_sync.py",
            "backend/tag_sync.py",
        ),
        "features": ("sync", "offline"),
    },
    {
        "name": "work-create",
        "paths": (
            "frontend/js/ribbon-create.js",
            "frontend/js/components/work-cards.js",
        ),
        "features": ("work-create", "shell"),
    },
    {
        "name": "e2e-module",
        "paths": ("tests/e2e/test_*.py",),
        "features": (),  # resolved specially from the filename
        "resolve_e2e_module": True,
        "note": "Changed E2E module → that module's feature group(s)",
    },
    {
        "name": "frontend-css-html",
        "paths": (
            "frontend/css/**",
            "frontend/*.html",
        ),
        "features": ("smoke", "shell"),
        "note": "Presentation-only → smoke + shell",
    },
    {
        "name": "docs-agents",
        "paths": (
            "AGENTS.md",
            "README.md",
            "docs/**",
            ".cursor/**",
            "DESIGN.md",
        ),
        "features": (),
        "skip": True,
        "note": "Docs/guidance only → no E2E selection",
    },
    {
        "name": "unit-tests-only",
        "paths": (
            "tests/test_*.py",
            "tests/browser/**",
            "tests/ux_tour/**",
        ),
        "features": (),
        "skip": True,
        "note": "Non-E2E tests → no browser E2E selection",
    },
)

# Unknown production paths under these prefixes fall back to smoke (not full).
CONSERVATIVE_SMOKE_PREFIXES = (
    "frontend/",
    "backend/",
    "tools/",
    "prks_app.py",
    "requirements",
)


def feature_names():
    return tuple(FEATURES.keys())


def _id_matches_selector(test_id: str, selector: str) -> bool:
    return test_id == selector or test_id.startswith(selector + ".")


def select_by_selectors(all_ids, selectors):
    """Return test IDs matching any dotted prefix selector, preserving order."""
    selected = []
    seen = set()
    for test_id in all_ids:
        for selector in selectors:
            if _id_matches_selector(test_id, selector):
                if test_id not in seen:
                    selected.append(test_id)
                    seen.add(test_id)
                break
    return selected


def select_smoke(all_ids):
    """Smoke suite: curated IDs must all still exist (never silently shrink)."""
    known = set(all_ids)
    missing = [tid for tid in SMOKE_TEST_IDS if tid not in known]
    if missing:
        raise ValueError(
            "curated smoke suite references missing test id(s): %s"
            % ", ".join(missing)
        )
    return list(SMOKE_TEST_IDS)


def select_features(all_ids, names):
    """Union of feature groups. Raises ValueError on unknown names."""
    unknown = [n for n in names if n != "smoke" and n not in FEATURES]
    if unknown:
        raise ValueError(
            "unknown E2E feature group(s): %s (known: %s)"
            % (", ".join(unknown), ", ".join(feature_names()))
        )
    selectors = []
    for name in names:
        if name == "smoke":
            continue
        selectors.extend(FEATURES[name]["selectors"])
    selected = select_by_selectors(all_ids, selectors)
    if "smoke" in names:
        for tid in select_smoke(all_ids):
            if tid not in selected:
                selected.append(tid)
    return selected


def format_feature_catalog():
    lines = ["E2E feature groups:", ""]
    for name, meta in FEATURES.items():
        lines.append("  %-22s %s" % (name, meta["description"]))
        lines.append("    selectors: %s" % ", ".join(meta["selectors"]))
    lines.append("")
    lines.append("  %-22s %s" % ("smoke", "Curated essential suite (see SMOKE_TEST_IDS)"))
    lines.append("")
    lines.append("Stress/stability (opt-in, not in full gate):")
    for script in STRESS_SCRIPTS:
        lines.append("  %s" % script)
    return "\n".join(lines)


def _posix(path: str) -> str:
    return path.replace("\\", "/")


def _path_matches(rel: str, pattern: str) -> bool:
    rel = _posix(rel)
    pattern = _posix(pattern)
    if "**" in pattern:
        # Path.match supports ** from Python 3.12; also allow prefix forms.
        try:
            if Path(rel).match(pattern):
                return True
        except ValueError:
            pass
        # Fallback: simple ** → substring directory match
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            return rel == prefix or rel.startswith(prefix + "/")
        if "/**/" in pattern:
            left, right = pattern.split("/**/", 1)
            return rel.startswith(left + "/") and Path(rel).match("*/" + right)
        return Path(rel).match(pattern)
    if "*" in pattern:
        return Path(rel).match(pattern)
    return rel == pattern


def features_for_e2e_module_path(rel: str):
    """Map tests/e2e/test_foo.py → feature names whose selectors cover that module."""
    rel = _posix(rel)
    if not rel.startswith("tests/e2e/test_") or not rel.endswith(".py"):
        return []
    stem = Path(rel).stem  # test_app
    # test_app.py mixes many domains. A whole-file change must not silently
    # collapse to smoke — union every feature group that owns a test_app class.
    if stem == "test_app":
        hits = []
        for name, meta in FEATURES.items():
            for selector in meta["selectors"]:
                if selector.startswith("tests.e2e.test_app."):
                    hits.append(name)
                    break
        return hits or ["smoke"]
    module = "tests.e2e.%s" % stem
    hits = []
    for name, meta in FEATURES.items():
        for selector in meta["selectors"]:
            if selector == module or selector.startswith(module + "."):
                hits.append(name)
                break
    return hits


def match_affected_path(rel: str):
    """Return (rule_name, features, skip, note) for one changed path."""
    rel = _posix(rel)
    for rule in AFFECTED_RULES:
        for pattern in rule["paths"]:
            if not _path_matches(rel, pattern):
                continue
            if rule.get("skip"):
                return rule["name"], (), True, rule.get("note") or ""
            if rule.get("resolve_e2e_module"):
                feats = tuple(features_for_e2e_module_path(rel))
                if not feats:
                    # Unknown E2E module file → smoke
                    return rule["name"], ("smoke",), False, "unmapped E2E module → smoke"
                return rule["name"], feats, False, rule.get("note") or ""
            return (
                rule["name"],
                tuple(rule.get("features") or ()),
                False,
                rule.get("note") or "",
            )
    # Conservative fallback
    for prefix in CONSERVATIVE_SMOKE_PREFIXES:
        if rel == prefix or rel.startswith(prefix):
            return (
                "unmapped-production",
                ("smoke",),
                False,
                "unmapped production path → smoke (not full suite)",
            )
    return "ignored", (), True, "outside production/E2E tree → skip"


def select_affected(all_ids, changed_paths):
    """Map changed paths → feature union + explanation records.

    Returns dict:
      features, test_ids, decisions (per path), empty_reason, noop_ok

    `noop_ok` is True when the diff is only docs/unit/ignored paths (successful
    no-op for --affected). It is False when features were selected but yielded
    no tests, or when a production path mapped to an empty selection.
    """
    features = []
    seen_f = set()
    decisions = []
    for raw in changed_paths:
        rel = _posix(raw)
        rule_name, feats, skip, note = match_affected_path(rel)
        decisions.append(
            {
                "path": rel,
                "rule": rule_name,
                "features": list(feats),
                "skip": skip,
                "note": note,
            }
        )
        if skip:
            continue
        for f in feats:
            if f not in seen_f:
                seen_f.add(f)
                features.append(f)
    if not features:
        # Docs/unit/ignored-only (or empty changed list) → successful no-op.
        return {
            "features": [],
            "test_ids": [],
            "decisions": decisions,
            "empty_reason": "no E2E-relevant changes mapped",
            "noop_ok": True,
        }
    test_ids = select_features(all_ids, features)
    if not test_ids:
        return {
            "features": features,
            "test_ids": [],
            "decisions": decisions,
            "empty_reason": "mapped features selected zero tests",
            "noop_ok": False,
        }
    return {
        "features": features,
        "test_ids": test_ids,
        "decisions": decisions,
        "empty_reason": None,
        "noop_ok": False,
    }


# Untracked paths under these prefixes are included in --affected discovery.
# scripts/ covers scripts/e2e; tests/e2e/ covers policy/runner additions.
UNTRACKED_AFFECTED_PREFIXES = (
    "frontend/",
    "backend/",
    "tests/e2e/",
    "tools/",
    "scripts/",
    "prks_app.py",
)


def list_changed_paths(repo: Path, base: str | None = None, include_untracked=True):
    """Working-tree changes vs base (default: HEAD). Explicit --base overrides.

    Default comparison is the agent-normal case: dirty working tree + index
    against HEAD (or against `base` when provided). Includes Added/Copied/
    Modified/Renamed/Deleted (D). Also includes untracked files under
    frontend/, backend/, tests/e2e/, tools/, scripts/ when include_untracked.
    """
    repo = Path(repo)
    ref = base or "HEAD"
    paths = []
    # Staged + unstaged vs ref — include deletes so removed production/E2E
    # files still drive feature selection.
    cmd = ["git", "-C", str(repo), "diff", "--name-only", "--diff-filter=ACMRD", ref]
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError):
        out = ""
    for line in out.splitlines():
        line = line.strip()
        if line:
            paths.append(line)
    # Also include staged-only relative to HEAD when base is HEAD — already covered
    # by diff HEAD. When base is another ref, also include uncommitted local work:
    if base and base != "HEAD":
        try:
            local = subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(repo),
                    "diff",
                    "--name-only",
                    "--diff-filter=ACMRD",
                    "HEAD",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            for line in local.splitlines():
                line = line.strip()
                if line and line not in paths:
                    paths.append(line)
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    if include_untracked:
        try:
            untracked = subprocess.check_output(
                ["git", "-C", str(repo), "ls-files", "--others", "--exclude-standard"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            untracked = ""
        for line in untracked.splitlines():
            line = line.strip()
            if not line:
                continue
            if any(
                line.startswith(p) or line == p.rstrip("/")
                for p in UNTRACKED_AFFECTED_PREFIXES
            ):
                if line not in paths:
                    paths.append(line)
    return paths


def merge_last_failed(previous_ids, executed_ids, current_failed_ids):
    """Treat last-failed persistence as an unresolved-failure set.

    - Retain prior failed tests that were not actually executed this run
      (partial / unrelated selections must not erase them).
    - Drop prior failures that were rerun and passed.
    - Add current failures.
    """
    previous = list(previous_ids or ())
    executed = set(executed_ids or ())
    current_failed = list(current_failed_ids or ())
    retained = [tid for tid in previous if tid not in executed]
    merged = list(retained)
    seen = set(retained)
    for tid in current_failed:
        if tid not in seen:
            merged.append(tid)
            seen.add(tid)
    return merged


def save_last_failed(path: Path, test_ids, meta=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "test_ids": list(test_ids),
        "meta": meta or {},
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_last_failed(path: Path):
    path = Path(path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    ids = data.get("test_ids")
    if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
        return None
    return data


def extract_failed_ids(result) -> list:
    """Collect unittest failure/error test ids from a TextTestResult."""
    failed = []
    for group in (getattr(result, "failures", None) or (), getattr(result, "errors", None) or ()):
        for test, _trace in group:
            failed.append(test.id())
    return failed


def is_full_gate(tier: str, targeted: bool) -> bool:
    return tier == "full" and not targeted


def report_banner(tier: str, count: int, extra: str = "") -> str:
    label = TIER_LABELS.get(tier, tier)
    gate = "FULL REGRESSION GATE" if tier == "full" else "NOT a full E2E gate"
    parts = ["E2E tier=%s (%s)" % (tier, label), "tests=%d" % count, gate]
    if extra:
        parts.append(extra)
    return " | ".join(parts)
