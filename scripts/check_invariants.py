#!/usr/bin/env python3
"""High-signal architectural invariant checks for production Python.

This is intentionally narrower than a general linter. It protects bug classes
where PRKS has a canonical capability boundary and where a direct low-level
call is almost certainly a regression.

Keep semantic invariants in tests; add checks here only when the forbidden
syntax has a clear approved replacement/boundary.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]

# First #69 Pyright slice: genuine basic type checking for backend/storage.
# Kept next to the AST invariants so Fast Static Analysis fails if the typed
# slice silently reverts to effectively-off mode.
PYRIGHT_DATAFLOW_CONFIG = "pyrightconfig.json"
PYRIGHT_DATAFLOW_REQUIRED_INCLUDE = "backend"
PYRIGHT_TYPED_SLICE_CONFIG = "pyrightconfig.typed-slice.json"
PYRIGHT_TYPED_SLICE_INCLUDE = ("backend/storage",)
PYRIGHT_TYPED_SLICE_ROOT = "backend/storage"
PYRIGHT_TYPED_SLICE_MODES = frozenset({"basic", "standard", "strict"})
# Only __pycache__ exclusions are permitted on the typed slice; anything else
# that covers backend/storage could silently suppress the checked scope.
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

# Calls that must not appear anywhere under backend/. New managed-file copies
# must go through the durable storage capability instead of ad-hoc copy calls.
BANNED_SHUTIL_COPY_CALLS = {"copy", "copy2", "copyfile"}

# Durable filesystem primitives are deliberately concentrated. Expanding these
# sets requires an explicit review of the durability/recovery contract.
#
# Intentional gap: pathlib.Path.replace is the same atomic rename as os.replace
# but is not matched here (fn.value is typically a Call/Name that is not an
# ``os`` module alias). Cover Path.replace only with an explicit follow-up that
# tracks Path constructors / Path-typed names — do not treat every ``.replace``
# attribute call as os.replace.
OS_REPLACE_ALLOWLIST = {
    "backend/backup_restore.py",
    # Disposable thumb / Person-image cache publication only — not canonical
    # library state. Keep ``backend/server.py`` non-exempt so new raw replaces
    # in the HTTP adapter fail INV-DURABILITY-001 by default.
    "backend/derived_cache_publish.py",
    "backend/fs_durability.py",
    "backend/pdf_linearize.py",
    "backend/services/work_pdf_replace.py",
}
# Bare os.fsync belongs only in fs_durability. Managed-PDF code must use
# fsync_open_file / fsync_directory — work_pdf_replace is not an fsync island.
OS_FSYNC_ALLOWLIST = {
    "backend/fs_durability.py",
}


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    line: int
    message: str

    def render(self) -> str:
        return f"{self.code} {self.path}:{self.line}: {self.message}"


class _Scope:
    """One lexical import scope (module, class body, or function)."""

    __slots__ = ("modules", "names")

    def __init__(self) -> None:
        self.modules: dict[str, str] = {}
        self.names: dict[str, tuple[str, str]] = {}


def _bind_import(scope: _Scope, node: ast.Import) -> None:
    for item in node.names:
        if item.name in {"os", "shutil"}:
            scope.modules[item.asname or item.name] = item.name
            continue
        # ``import os.path`` (no ``as``) still binds the top-level name ``os``
        # to the ``os`` package. ``import os.path as p`` binds only ``p``.
        if item.asname is None:
            top = item.name.split(".", 1)[0]
            if top in {"os", "shutil"}:
                scope.modules[top] = top


def _bind_import_from(scope: _Scope, node: ast.ImportFrom) -> None:
    if node.module not in {"os", "shutil"}:
        return
    for item in node.names:
        if item.name == "*":
            continue
        scope.names[item.asname or item.name] = (node.module, item.name)


def _lookup_module(scopes: list[_Scope], name: str) -> str | None:
    for scope in reversed(scopes):
        if name in scope.modules:
            return scope.modules[name]
    return None


def _lookup_name(scopes: list[_Scope], name: str) -> tuple[str, str] | None:
    for scope in reversed(scopes):
        if name in scope.names:
            return scope.names[name]
    return None


def _call_identity(node: ast.Call, scopes: list[_Scope]) -> tuple[str, str] | None:
    fn = node.func
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
        module = _lookup_module(scopes, fn.value.id)
        if module:
            return module, fn.attr
    if isinstance(fn, ast.Name):
        return _lookup_name(scopes, fn.id)
    return None


class _InvariantVisitor(ast.NodeVisitor):
    """Walk the tree, resolving os/shutil aliases in the current lexical scope."""

    def __init__(self, relpath: str) -> None:
        self.relpath = relpath
        self.scopes: list[_Scope] = [_Scope()]
        self.findings: list[Finding] = []

    def _push(self) -> None:
        self.scopes.append(_Scope())

    def _pop(self) -> None:
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._push()
        self.generic_visit(node)
        self._pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._push()
        self.generic_visit(node)
        self._pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._push()
        self.generic_visit(node)
        self._pop()

    def visit_Import(self, node: ast.Import) -> None:
        _bind_import(self.scopes[-1], node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        _bind_import_from(self.scopes[-1], node)

    def visit_Call(self, node: ast.Call) -> None:
        identity = _call_identity(node, self.scopes)
        if identity is not None:
            module, name = identity
            if module == "shutil" and name in BANNED_SHUTIL_COPY_CALLS:
                self.findings.append(
                    Finding(
                        "INV-STORAGE-001",
                        self.relpath,
                        node.lineno,
                        (
                            f"direct shutil.{name}() is forbidden in backend production code; "
                            "publish managed PDFs through backend.services.work_pdf_replace "
                            "(store_new_managed_pdf_bytes/store_new_managed_pdf_from_path), "
                            "or use a domain-specific storage capability"
                        ),
                    )
                )
            elif module == "os" and name == "replace" and self.relpath not in OS_REPLACE_ALLOWLIST:
                self.findings.append(
                    Finding(
                        "INV-DURABILITY-001",
                        self.relpath,
                        node.lineno,
                        (
                            "direct os.replace() is outside the approved durability boundary; "
                            "use backend.fs_durability or an existing durable domain helper"
                        ),
                    )
                )
            elif module == "os" and name == "fsync" and self.relpath not in OS_FSYNC_ALLOWLIST:
                self.findings.append(
                    Finding(
                        "INV-DURABILITY-002",
                        self.relpath,
                        node.lineno,
                        (
                            "direct os.fsync() is outside the approved durability boundary; "
                            "use backend.fs_durability helpers"
                        ),
                    )
                )
        self.generic_visit(node)


def check_source(source: str, relpath: str) -> list[Finding]:
    try:
        tree = ast.parse(source, filename=relpath)
    except SyntaxError as exc:
        return [
            Finding(
                "INV-PARSE-001",
                relpath,
                int(exc.lineno or 1),
                "could not parse file while checking engineering invariants",
            )
        ]

    visitor = _InvariantVisitor(relpath)
    visitor.visit(tree)
    return visitor.findings


def iter_production_python(root: Path) -> Iterable[Path]:
    """Yield production Python paths the invariant checker must cover.

    Includes the process entry ``prks_app.py`` (same set Ruff checks) plus
    every file under ``backend/``. Scripts and tests are out of scope.
    """
    app = root / "prks_app.py"
    if app.is_file():
        yield app
    backend = root / "backend"
    yield from sorted(p for p in backend.rglob("*.py") if p.is_file())


# Back-compat alias for earlier call sites / imports.
iter_backend_python = iter_production_python


def check_repo(root: Path = REPO_ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_production_python(root):
        rel = path.relative_to(root).as_posix()
        findings.extend(check_source(path.read_text(encoding="utf-8"), rel))
    return findings


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


def _path_covers_typed_slice(entry: object) -> bool:
    """True when ignore/exclude would suppress analysis of backend/storage."""
    raw = str(entry).replace("\\", "/").strip()
    if raw in PYRIGHT_TYPED_SLICE_ALLOWED_EXCLUDES:
        return False
    e = _normalize_pyright_path(raw)
    if any(_normalize_pyright_path(item) == e for item in PYRIGHT_TYPED_SLICE_ALLOWED_EXCLUDES):
        return False
    target = PYRIGHT_TYPED_SLICE_ROOT
    if e in {"", ".", "*", "**", "**/*"}:
        return True
    if e == target or e.startswith(target + "/"):
        return True
    if target.startswith(e + "/"):
        return True
    if "backend/storage" in e:
        return True
    if e in {"storage", "**/storage"} or e.endswith("/storage"):
        return True
    return False


def _reject_typed_slice_suppression(cfg: dict) -> list[Finding]:
    """Reject ignore/exclude entries that would silence the typed slice."""
    findings: list[Finding] = []
    for key in ("ignore", "exclude"):
        value = cfg.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            findings.append(
                Finding(
                    "INV-PYRIGHT-002",
                    PYRIGHT_TYPED_SLICE_CONFIG,
                    1,
                    f"typed-slice {key} must be a list when present (found {type(value).__name__})",
                )
            )
            continue
        for entry in value:
            if _path_covers_typed_slice(entry):
                findings.append(
                    Finding(
                        "INV-PYRIGHT-002",
                        PYRIGHT_TYPED_SLICE_CONFIG,
                        1,
                        (
                            f"typed-slice {key} entry {entry!r} would suppress "
                            f"{PYRIGHT_TYPED_SLICE_ROOT}; only __pycache__ exclusions are allowed"
                        ),
                    )
                )
    return findings


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


def _executable_pyright_projects(workflow_text: str) -> set[str]:
    """Project paths passed to ``pyright --project`` in executable ``run`` steps."""
    projects: set[str] = set()
    for script in _workflow_run_scripts(workflow_text):
        cleaned = _strip_shell_comment_lines(script)
        if "pyright" not in cleaned:
            continue
        for match in _PYRIGHT_PROJECT_ARG_RE.finditer(cleaned):
            projects.add(match.group("path"))
    return projects


def check_pyright_configs(root: Path = REPO_ROOT) -> list[Finding]:
    """Keep the #69 typed slice from silently becoming effectively-off.

    The data-flow config may stay on ``typeCheckingMode: off`` (narrow
    diagnostics only) but must still include ``backend``. The typed-slice
    config must enable genuine analysis for ``backend/storage`` without
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

    typed_path = root / PYRIGHT_TYPED_SLICE_CONFIG
    if not typed_path.is_file():
        findings.append(
            Finding(
                "INV-PYRIGHT-002",
                PYRIGHT_TYPED_SLICE_CONFIG,
                1,
                "missing Pyright typed-slice config (first #69 scope)",
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
                    f"(found {mode!r}); off would silently disable real type analysis"
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
        if normalized != PYRIGHT_TYPED_SLICE_INCLUDE:
            findings.append(
                Finding(
                    "INV-PYRIGHT-002",
                    PYRIGHT_TYPED_SLICE_CONFIG,
                    1,
                    (
                        "typed-slice include must be exactly "
                        f"{list(PYRIGHT_TYPED_SLICE_INCLUDE)} "
                        f"(found {list(normalized)!r}); expand only in a focused follow-up PR"
                    ),
                )
            )

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="repository root (defaults to the checker script's parent repository)",
    )
    args = parser.parse_args(argv)

    findings = check_repo(args.root.resolve())
    findings.extend(check_pyright_configs(args.root.resolve()))
    if findings:
        for finding in findings:
            print(finding.render())
        print(f"engineering invariant check failed: {len(findings)} violation(s)")
        return 1

    print("engineering invariant check: OK")
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
