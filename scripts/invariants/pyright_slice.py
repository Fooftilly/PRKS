"""Pyright typed-slice and data-flow config guards (#69, #1429)."""
from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from typing import Sequence

from .findings import Finding, REPO_ROOT


# #69 / #1429 Pyright typed slice: genuine standard type checking for all of
# backend/, prks_app.py, scripts/ and tests/. The scopes are whole trees, so a
# new module is checked without editing any list.
# Checked with the AST invariants so Fast Static Analysis fails if the typed
# slice silently reverts to effectively-off mode or loses a protected scope.
PYRIGHT_DATAFLOW_CONFIG = "pyrightconfig.json"
PYRIGHT_DATAFLOW_REQUIRED_INCLUDE = "backend"
PYRIGHT_TYPED_SLICE_CONFIG = "pyrightconfig.typed-slice.json"
# Every protected scope must stay in ``include``, exist on disk (Pyright only
# warns and still exits 0 for a missing include path), and stay free of
# ignore/exclude suppression: a file that does not pass is fixed, not excluded.
PYRIGHT_TYPED_SLICE_SCOPES = (
    "backend",
    "prks_app.py",
    "scripts",
    "tests",
)
PYRIGHT_TYPED_SLICE_INCLUDE = PYRIGHT_TYPED_SLICE_SCOPES
# Files and directories already clean under Pyright strict mode, held there by
# the typed-slice config's "strict" list so they cannot regress. The config may
# list more paths (add new ones here too), never fewer.
PYRIGHT_TYPED_SLICE_STRICT = (
    "backend/api/__init__.py",
    "backend/api/person_groups.py",
    "backend/api/positions.py",
    "backend/api/publishers.py",
    "backend/api/research_notes.py",
    "backend/api/search.py",
    "backend/api/settings.py",
    "backend/api/citation_styles.py",
    "backend/api/tags.py",
    "backend/api_contract/__init__.py",
    "backend/api_contract/errors.py",
    "backend/api_contract/jobs.py",
    "backend/api_contract/performance.py",
    "backend/api_contract/positions.py",
    "backend/api_contract/processing_items.py",
    "backend/api_contract/publishers.py",
    "backend/api_contract/saved_views.py",
    "backend/web/byte_range.py",
    "backend/db/__init__.py",
    "backend/db/config.py",
    "backend/db/migrate.py",
    "backend/db/testing_guard.py",
    "backend/runtime/testing_reset.py",
    "backend/media/derived_cache_publish.py",
    "backend/domain/__init__.py",
    "backend/domain/annotations/__init__.py",
    "backend/domain/annotations/materialize.py",
    "backend/domain/identity/__init__.py",
    "backend/domain/identity/admin.py",
    "backend/domain/identity/authorization.py",
    "backend/domain/identity/errors.py",
    "backend/domain/identity/login.py",
    "backend/domain/identity/login_limiter.py",
    "backend/domain/identity/login_names.py",
    "backend/domain/identity/request_context.py",
    "backend/domain/identity/sessions.py",
    "backend/domain/identity/web_start.py",
    "backend/domain/maintenance/__init__.py",
    "backend/domain/maintenance/backup.py",
    "backend/domain/maintenance/format.py",
    "backend/domain/maintenance/staged.py",
    "backend/domain/maintenance/tables.py",
    "backend/domain/maintenance/upload.py",
    "backend/domain/objects/__init__.py",
    "backend/domain/objects/jobs.py",
    "backend/domain/objects/portraits.py",
    "backend/domain/objects/replace.py",
    "backend/domain/organization/__init__.py",
    "backend/domain/organization/lifecycle.py",
    "backend/domain/organization/tag_commands.py",
    "backend/domain/people/__init__.py",
    "backend/domain/people/constraints.py",
    "backend/domain/people/errors.py",
    "backend/domain/people/group_commands.py",
    "backend/domain/people/publisher_commands.py",
    "backend/domain/processing/__init__.py",
    "backend/domain/processing/commands.py",
    "backend/domain/processing/imports.py",
    "backend/domain/processing/uploads.py",
    "backend/domain/research/__init__.py",
    "backend/domain/research/argument_commands.py",
    "backend/domain/research/concept_commands.py",
    "backend/domain/research/constraints.py",
    "backend/domain/research/index_commands.py",
    "backend/domain/research/position_commands.py",
    "backend/domain/saved_views/__init__.py",
    "backend/domain/saved_views/commands.py",
    "backend/domain/saved_views/constraints.py",
    "backend/domain/search/__init__.py",
    "backend/domain/search/queries.py",
    "backend/domain/search/refresh.py",
    "backend/domain/settings/__init__.py",
    "backend/domain/settings/commands.py",
    "backend/domain/text_identity.py",
    "backend/domain/works/__init__.py",
    "backend/domain/works/citation.py",
    "backend/domain/works/integrity.py",
    "backend/domain/works/lifecycle.py",
    "backend/domain/works/merge.py",
    "backend/domain/works/projection.py",
    "backend/domain/works/queries.py",
    "backend/domain/works/revisions.py",
    "backend/domain/works/wiki_links.py",
    "backend/web/etags.py",
    "backend/storage/fs_durability.py",
    "backend/jobs/__init__.py",
    "backend/jobs/diagnostics.py",
    "backend/jobs/embedded.py",
    "backend/jobs/handlers.py",
    "backend/jobs/prune.py",
    "backend/jobs/runner.py",
    "backend/library.py",
    "backend/runtime/config_reference.py",
    "backend/pdf/pdf_byte_mentions.py",
    "backend/runtime/process_roles.py",
    "backend/runtime/readiness.py",
    "backend/domain/research/research_markup.py",
    "backend/pdf/__init__.py",
    "backend/pdf/work_pdf_replace.py",
    "backend/runtime/startup_profile.py",
    "backend/storage/__init__.py",
    "backend/storage/config.py",
    "backend/storage/distribution.py",
    "backend/storage/errors.py",
    "backend/storage/file_lock.py",
    "backend/storage/paths.py",
    "backend/storage/preflight.py",
    "backend/storage/resolver.py",
    "backend/sync/__init__.py",
    "backend/sync/families.py",
    "backend/sync/handlers/__init__.py",
    "backend/domain/organization/tag_color.py",
    "backend/server.py",
    "backend/web/routing.py",
    "backend/api/pdfs.py",
    "backend/api/storage.py",
    "backend/api/deployment.py",
    "backend/api/arguments.py",
    "backend/api/concepts.py",
    "backend/api/folders.py",
    "backend/api/work_roles.py",
    "backend/api/annotations.py",
)
# "basic" is no longer accepted: the slice was raised to "standard" in #69
# slice 6, and a lower mode would silently drop the checks that added.
PYRIGHT_TYPED_SLICE_MODES = frozenset({"standard", "strict"})
# Only __pycache__ exclusions are permitted on the typed slice; anything else
# that covers a protected scope could silently suppress it.
PYRIGHT_TYPED_SLICE_ALLOWED_EXCLUDES = frozenset(
    {
        "**/__pycache__",
        "**/__pycache__/**",
        "__pycache__",
        "__pycache__/",
    }
)
PYRIGHT_REQUIRED_DIAGNOSTICS = (
    "reportUndefinedVariable",
    "reportUnboundVariable",
    "reportUnusedExcept",
)
STATIC_ANALYSIS_WORKFLOW = ".github/workflows/static-analysis.yml"
_PYRIGHT_PROJECT_ARG_RE = re.compile(
    r"--project(?:\s+|=)(?P<q>[\"']?)(?P<path>[^\"'\s]+)(?P=q)"
)
_WORKFLOW_RUN_KEY_RE = re.compile(r"^(\s*)(?:-\s+)?run:\s*(.*)$")


def _load_json_object(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _require_diagnostic_errors(cfg: dict, relpath: str) -> list[Finding]:
    findings: list[Finding] = []
    for key in PYRIGHT_REQUIRED_DIAGNOSTICS:
        if cfg.get(key) != "error":
            findings.append(
                Finding(
                    "INV-PYRIGHT-001",
                    relpath,
                    1,
                    f"{key} must remain \"error\" (found {cfg.get(key)!r})",
                )
            )
    return findings


def _normalize_pyright_path(entry: object) -> str:
    return str(entry).replace("\\", "/").rstrip("/")


def _normalize_pyright_glob_pattern(pattern: str) -> str | None:
    """Drop empty/``.`` segments and resolve ``..`` against a preceding literal.

    Returns ``None`` when ``..`` cannot be resolved (no preceding literal, or
    the preceding segment is a glob such as ``**``). Callers treat ``None`` as
    fail-closed: the entry covers the protected root.
    """
    text = pattern.replace("\\", "/").strip()
    out: list[str] = []
    for segment in text.split("/"):
        if segment in {"", "."}:
            continue
        if segment == "..":
            if not out:
                return None
            prev = out[-1]
            if prev == "**" or any(ch in prev for ch in "*?["):
                return None
            out.pop()
            continue
        out.append(segment)
    return "/".join(out)


def _is_allowed_cache_exclude(entry: object) -> bool:
    raw = str(entry).replace("\\", "/").strip()
    if raw in PYRIGHT_TYPED_SLICE_ALLOWED_EXCLUDES:
        return True
    normalized = _normalize_pyright_glob_pattern(raw)
    if normalized is None:
        return False
    allowed = {
        _normalize_pyright_path(item) for item in PYRIGHT_TYPED_SLICE_ALLOWED_EXCLUDES
    }
    return _normalize_pyright_path(normalized) in allowed


def _is_allowed_typed_slice_exclude(entry: object) -> bool:
    return _is_allowed_cache_exclude(entry)


def _pyright_glob_match(path: str, pattern: str) -> bool:
    """Match ``path`` against a Pyright/gitignore-style glob.

    ``**`` matches zero or more directories (unlike stdlib ``fnmatch``, where
    mid-path ``**`` does not consume an empty directory span).
    ``*`` and ``?`` match within a single path segment.
    """
    path = path.replace("\\", "/").strip("/")
    pattern = pattern.replace("\\", "/").strip("/")
    if pattern in {"", "**"}:
        return True
    path_parts = path.split("/") if path else []
    pat_parts = pattern.split("/") if pattern else []

    def match_from(pi: int, pti: int) -> bool:
        while pti < len(pat_parts):
            token = pat_parts[pti]
            if token == "**":
                # Zero-or-more directories: try consuming nothing, then 1..N parts.
                if pti == len(pat_parts) - 1:
                    return True
                for skip in range(pi, len(path_parts) + 1):
                    if match_from(skip, pti + 1):
                        return True
                return False
            if pi >= len(path_parts):
                return False
            if not fnmatch.fnmatchcase(path_parts[pi], token):
                return False
            pi += 1
            pti += 1
        return pi == len(path_parts)

    return match_from(0, 0)


def _literal_prefix_before_glob(pattern: str) -> str:
    """Path segments before the first glob token (``*``, ``?``, ``**``, ``[…]``)."""
    parts: list[str] = []
    for segment in pattern.replace("\\", "/").strip("/").split("/"):
        if not segment:
            continue
        if segment == "**" or "*" in segment or "?" in segment or "[" in segment:
            break
        parts.append(segment)
    return "/".join(parts)


def _glob_overlaps_typed_root(pattern: str, root: str) -> bool:
    """True if ``pattern`` can match ``root``, an ancestor, or any path under it."""
    root_parts = root.replace("\\", "/").strip("/").split("/")
    pat_parts = [p for p in pattern.replace("\\", "/").strip("/").split("/") if p]
    if not pat_parts:
        return True

    def dfs(pti: int, ri: int) -> bool:
        if pti == len(pat_parts):
            # Pattern exhausted on an ancestor or the root itself.
            return ri <= len(root_parts)

        token = pat_parts[pti]
        if token == "**":
            if pti == len(pat_parts) - 1:
                return True
            for skip in range(ri, len(root_parts) + 1):
                if dfs(pti + 1, skip):
                    return True
            # Remaining tokens can match invented descendants under root.
            return True

        if ri < len(root_parts):
            if fnmatch.fnmatchcase(root_parts[ri], token):
                return dfs(pti + 1, ri + 1)
            return False

        # Past the root: any further pattern segments match some descendant.
        return True

    return dfs(0, 0)


def _path_covers_root(entry: object, root: str) -> bool:
    """True when ignore/exclude can match ``root`` or anything under it.

    Dot-segments are normalized first (``.`` dropped; ``..`` resolved against a
    preceding literal). Unresolvable ``..`` (``../x``, ``**/..``) fails closed.
    Cache-only ``__pycache__`` excludes are allowlisted.
    """
    if _is_allowed_cache_exclude(entry):
        return False
    raw = str(entry).replace("\\", "/").strip()
    normalized = _normalize_pyright_glob_pattern(raw)
    if normalized is None:
        return True
    pattern = normalized.rstrip("/")
    if pattern in {"", ".", "*", "**", "**/*", "**/**"}:
        return True

    lit = _literal_prefix_before_glob(pattern)
    # Clearly rooted at or under the protected root (globs may follow).
    if lit == root or lit.startswith(root + "/"):
        return True
    # Literal-only parent of the root (no glob metacharacters anywhere).
    if lit and not any(ch in pattern for ch in "*?["):
        if root.startswith(lit + "/"):
            return True

    return _glob_overlaps_typed_root(pattern, root)


def _path_covers_typed_slice(entry: object) -> bool:
    """True when ignore/exclude can match any protected typed-slice scope."""
    return any(_path_covers_root(entry, scope) for scope in PYRIGHT_TYPED_SLICE_SCOPES)


def _reject_ignore_exclude_covering(
    cfg: dict,
    *,
    protected_roots: Sequence[str],
    config_name: str,
    code: str,
) -> list[Finding]:
    """Reject ignore/exclude entries that would silence any ``protected_roots``."""
    findings: list[Finding] = []
    for key in ("ignore", "exclude"):
        value = cfg.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            findings.append(
                Finding(
                    code,
                    config_name,
                    1,
                    f"{config_name} {key} must be a list when present (found {type(value).__name__})",
                )
            )
            continue
        for entry in value:
            covered = [root for root in protected_roots if _path_covers_root(entry, root)]
            if covered:
                findings.append(
                    Finding(
                        code,
                        config_name,
                        1,
                        (
                            f"{config_name} {key} entry {entry!r} would suppress "
                            f"{', '.join(covered)}; only __pycache__ exclusions are allowed"
                        ),
                    )
                )
    return findings


def _check_typed_slice_strict(cfg: dict, root: Path) -> list[Finding]:
    """Keep every PYRIGHT_TYPED_SLICE_STRICT path in the config's strict list."""
    strict = cfg.get("strict", [])
    if not isinstance(strict, list) or not all(isinstance(item, str) for item in strict):
        return [
            Finding(
                "INV-PYRIGHT-004",
                PYRIGHT_TYPED_SLICE_CONFIG,
                1,
                "typed-slice strict must be a list of paths",
            )
        ]
    listed = {_normalize_pyright_path(item) for item in strict}
    findings: list[Finding] = []
    missing = [path for path in PYRIGHT_TYPED_SLICE_STRICT if path not in listed]
    if missing:
        findings.append(
            Finding(
                "INV-PYRIGHT-004",
                PYRIGHT_TYPED_SLICE_CONFIG,
                1,
                (
                    f"typed-slice strict list is missing {missing!r}; files that "
                    "pass strict mode must stay strict (fix the new errors instead)"
                ),
            )
        )
    for path in sorted(listed):
        # A missing typed-slice scope is already reported as INV-PYRIGHT-002.
        if path in PYRIGHT_TYPED_SLICE_SCOPES:
            continue
        if not (root / path).exists():
            findings.append(
                Finding(
                    "INV-PYRIGHT-004",
                    PYRIGHT_TYPED_SLICE_CONFIG,
                    1,
                    (
                        f"typed-slice strict path {path!r} does not exist; Pyright "
                        "would skip it silently. Update the strict list with the "
                        "move or rename"
                    ),
                )
            )
        elif not _under_typed_slice_scope(path):
            findings.append(
                Finding(
                    "INV-PYRIGHT-004",
                    PYRIGHT_TYPED_SLICE_CONFIG,
                    1,
                    (
                        f"typed-slice strict path {path!r} is outside every typed-slice "
                        "include scope; Pyright applies strict only to included files, "
                        "so add it to PYRIGHT_TYPED_SLICE_SCOPES first"
                    ),
                )
            )
    return findings


def _under_typed_slice_scope(path: str) -> bool:
    return any(
        path == scope or path.startswith(scope.rstrip("/") + "/")
        for scope in PYRIGHT_TYPED_SLICE_SCOPES
    )


def _reject_typed_slice_suppression(cfg: dict) -> list[Finding]:
    """Reject ignore/exclude entries that would silence any typed-slice scope."""
    return _reject_ignore_exclude_covering(
        cfg,
        protected_roots=PYRIGHT_TYPED_SLICE_SCOPES,
        config_name=PYRIGHT_TYPED_SLICE_CONFIG,
        code="INV-PYRIGHT-002",
    )


def _workflow_run_scripts(workflow_text: str) -> list[str]:
    """Collect executable ``run:`` script bodies (not YAML ``#`` comments).

    Dependency-free: Fast Static Analysis runs this checker without PyYAML.
    Handles single-line ``run:`` and block scalars (``|`` / ``>``).
    """
    scripts: list[str] = []
    lines = workflow_text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        if raw.lstrip().startswith("#"):
            i += 1
            continue
        match = _WORKFLOW_RUN_KEY_RE.match(raw)
        if match is None:
            i += 1
            continue
        indent = len(match.group(1))
        rest = match.group(2).rstrip()
        block = rest in {"", "|", ">", "|-", ">-", "|+", ">+"} or rest.startswith(("|", ">"))
        if not block:
            scripts.append(rest)
            i += 1
            continue
        body: list[str] = []
        i += 1
        while i < len(lines):
            nxt = lines[i]
            if nxt.strip() == "":
                body.append("")
                i += 1
                continue
            content_indent = len(nxt) - len(nxt.lstrip(" "))
            if content_indent <= indent:
                break
            body.append(nxt)
            i += 1
        scripts.append("\n".join(body))
    return scripts


def _strip_shell_comment_lines(script: str) -> str:
    kept: list[str] = []
    for line in script.splitlines():
        if line.lstrip().startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _shell_chunk_invokes_pyright(chunk: str) -> bool:
    """True when ``pyright`` is an invoked command, not text inside ``echo``."""
    # Drop quoted strings so ``echo "pyright --project X"`` does not count.
    unquoted = re.sub(r'"[^"]*"', ' "" ', chunk)
    unquoted = re.sub(r"'[^']*'", " '' ", unquoted)
    tokens = unquoted.split()
    if not tokens:
        return False
    head = tokens[0].rsplit("/", 1)[-1]
    if head in {"echo", "printf", "cat"}:
        return False
    for index, token in enumerate(tokens):
        name = token.rsplit("/", 1)[-1]
        if name != "pyright":
            continue
        if index == 0:
            return True
        # npm/npx exec … -- pyright  (or similar package runners)
        if head in {"npm", "npx", "yarn", "pnpm"} and "--" in tokens[:index]:
            return True
        if tokens[index - 1] in {"--", "time", "command", "exec", "env"}:
            return True
    return False


def _executable_pyright_projects(workflow_text: str) -> set[str]:
    """Project paths from real ``pyright --project`` invocations in ``run`` steps."""
    projects: set[str] = set()
    for script in _workflow_run_scripts(workflow_text):
        cleaned = _strip_shell_comment_lines(script)
        for chunk in re.split(r"[;\n|&]+", cleaned):
            chunk = chunk.strip()
            if not chunk or not _shell_chunk_invokes_pyright(chunk):
                continue
            for match in _PYRIGHT_PROJECT_ARG_RE.finditer(chunk):
                projects.add(match.group("path"))
    return projects


def check_pyright_configs(root: Path = REPO_ROOT) -> list[Finding]:
    """Keep the #69 typed slice from silently becoming effectively-off.

    The data-flow config may stay on ``typeCheckingMode: off`` (narrow
    diagnostics only) but must still include ``backend``. The typed-slice
    config must enable genuine analysis for every protected scope in
    ``PYRIGHT_TYPED_SLICE_SCOPES`` (each present on disk) without
    ignore/exclude suppression, and CI must execute ``pyright --project``
    for both configs (filename mentions in comments do not count).
    """
    findings: list[Finding] = []

    dataflow_path = root / PYRIGHT_DATAFLOW_CONFIG
    if not dataflow_path.is_file():
        findings.append(
            Finding(
                "INV-PYRIGHT-001",
                PYRIGHT_DATAFLOW_CONFIG,
                1,
                "missing Pyright data-flow config",
            )
        )
    else:
        dataflow = _load_json_object(dataflow_path)
        if dataflow is None:
            findings.append(
                Finding(
                    "INV-PYRIGHT-001",
                    PYRIGHT_DATAFLOW_CONFIG,
                    1,
                    "Pyright data-flow config is not a JSON object",
                )
            )
        else:
            findings.extend(_require_diagnostic_errors(dataflow, PYRIGHT_DATAFLOW_CONFIG))
            include = dataflow.get("include")
            include_paths = (
                {_normalize_pyright_path(item) for item in include}
                if isinstance(include, list)
                else set()
            )
            if PYRIGHT_DATAFLOW_REQUIRED_INCLUDE not in include_paths:
                findings.append(
                    Finding(
                        "INV-PYRIGHT-001",
                        PYRIGHT_DATAFLOW_CONFIG,
                        1,
                        (
                            "data-flow include must contain "
                            f"{PYRIGHT_DATAFLOW_REQUIRED_INCLUDE!r} "
                            f"(found {sorted(include_paths)!r})"
                        ),
                    )
                )
            findings.extend(
                _reject_ignore_exclude_covering(
                    dataflow,
                    protected_roots=(PYRIGHT_DATAFLOW_REQUIRED_INCLUDE,),
                    config_name=PYRIGHT_DATAFLOW_CONFIG,
                    code="INV-PYRIGHT-001",
                )
            )

    typed_path = root / PYRIGHT_TYPED_SLICE_CONFIG
    if not typed_path.is_file():
        findings.append(
            Finding(
                "INV-PYRIGHT-002",
                PYRIGHT_TYPED_SLICE_CONFIG,
                1,
                "missing Pyright typed-slice config (#69 typed scopes)",
            )
        )
        return findings

    typed = _load_json_object(typed_path)
    if typed is None:
        findings.append(
            Finding(
                "INV-PYRIGHT-002",
                PYRIGHT_TYPED_SLICE_CONFIG,
                1,
                "Pyright typed-slice config is not a JSON object",
            )
        )
        return findings

    findings.extend(_require_diagnostic_errors(typed, PYRIGHT_TYPED_SLICE_CONFIG))

    mode = typed.get("typeCheckingMode")
    if mode not in PYRIGHT_TYPED_SLICE_MODES:
        findings.append(
            Finding(
                "INV-PYRIGHT-002",
                PYRIGHT_TYPED_SLICE_CONFIG,
                1,
                (
                    "typed-slice typeCheckingMode must be one of "
                    f"{sorted(PYRIGHT_TYPED_SLICE_MODES)} "
                    f"(found {mode!r}); off or basic would silently weaken type analysis"
                ),
            )
        )

    include = typed.get("include")
    if not isinstance(include, list) or not include:
        findings.append(
            Finding(
                "INV-PYRIGHT-002",
                PYRIGHT_TYPED_SLICE_CONFIG,
                1,
                "typed-slice include must be a non-empty list",
            )
        )
    else:
        normalized = tuple(_normalize_pyright_path(item) for item in include)
        if sorted(normalized) != sorted(PYRIGHT_TYPED_SLICE_INCLUDE):
            missing = [s for s in PYRIGHT_TYPED_SLICE_INCLUDE if s not in normalized]
            findings.append(
                Finding(
                    "INV-PYRIGHT-002",
                    PYRIGHT_TYPED_SLICE_CONFIG,
                    1,
                    (
                        "typed-slice include must be exactly "
                        f"{list(PYRIGHT_TYPED_SLICE_INCLUDE)} "
                        f"(found {list(normalized)!r}; missing {missing!r}); "
                        "expand only in a focused follow-up PR"
                    ),
                )
            )

    for scope in PYRIGHT_TYPED_SLICE_SCOPES:
        if not (root / scope).exists():
            findings.append(
                Finding(
                    "INV-PYRIGHT-002",
                    PYRIGHT_TYPED_SLICE_CONFIG,
                    1,
                    (
                        f"protected typed-slice scope {scope!r} does not exist; "
                        "Pyright would skip it silently. Update the #69 scope list "
                        "together with the move or rename"
                    ),
                )
            )

    findings.extend(_check_typed_slice_strict(typed, root))
    findings.extend(_reject_typed_slice_suppression(typed))

    workflow_path = root / STATIC_ANALYSIS_WORKFLOW
    if not workflow_path.is_file():
        findings.append(
            Finding(
                "INV-PYRIGHT-003",
                STATIC_ANALYSIS_WORKFLOW,
                1,
                "missing Fast Static Analysis workflow",
            )
        )
    else:
        projects = _executable_pyright_projects(workflow_path.read_text(encoding="utf-8"))
        if PYRIGHT_TYPED_SLICE_CONFIG not in projects:
            findings.append(
                Finding(
                    "INV-PYRIGHT-003",
                    STATIC_ANALYSIS_WORKFLOW,
                    1,
                    (
                        f"workflow must execute pyright --project {PYRIGHT_TYPED_SLICE_CONFIG} "
                        "in a run step (comments / filename mentions do not count)"
                    ),
                )
            )
        if PYRIGHT_DATAFLOW_CONFIG not in projects:
            findings.append(
                Finding(
                    "INV-PYRIGHT-003",
                    STATIC_ANALYSIS_WORKFLOW,
                    1,
                    (
                        f"workflow must execute pyright --project {PYRIGHT_DATAFLOW_CONFIG} "
                        "in a run step (comments / filename mentions do not count)"
                    ),
                )
            )

    return findings
