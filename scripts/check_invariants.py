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
import fnmatch
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

# INV-BACKUP-001: ZipFile.extractall() trusts member names/types and is never
# acceptable on backup input. There is intentionally no production allowlist.
# A receiver is classified only when it provably originates from one of these
# zipfile classes (constructor call, a local name bound to one, or a
# parameter/variable annotated with one); unrelated ``.extractall()`` methods
# are not matched. Instance attributes (``self.archive``), containers, and
# values returned from helper functions are not tracked.
ZIPFILE_ARCHIVE_CLASSES = frozenset({"ZipFile", "PyZipFile"})
BANNED_ZIPFILE_METHOD = "extractall"

_TRACKED_MODULES = frozenset({"os", "shutil", "zipfile"})


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

    __slots__ = ("modules", "names", "zip_archives")

    def __init__(self) -> None:
        self.modules: dict[str, str] = {}
        self.names: dict[str, tuple[str, str]] = {}
        # Local name -> whether this scope binds it to a zipfile archive.
        # ``False`` records a non-archive local binding that shadows an
        # enclosing one. Bindings are sticky within one scope (no flow
        # analysis): any archive binding taints the name for the scope.
        self.zip_archives: dict[str, bool] = {}


def _bind_import(scope: _Scope, node: ast.Import) -> None:
    for item in node.names:
        if item.name in _TRACKED_MODULES:
            scope.modules[item.asname or item.name] = item.name
            continue
        # ``import os.path`` (no ``as``) still binds the top-level name ``os``
        # to the ``os`` package. ``import os.path as p`` binds only ``p``.
        if item.asname is None:
            top = item.name.split(".", 1)[0]
            if top in _TRACKED_MODULES:
                scope.modules[top] = top


def _bind_import_from(scope: _Scope, node: ast.ImportFrom) -> None:
    if node.module not in _TRACKED_MODULES:
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


def _is_zip_class_expr(node: ast.expr, scopes: list[_Scope]) -> bool:
    """``zipfile.ZipFile`` / ``z.ZipFile`` / ``ZipFile`` / ``Z`` (import alias)."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return (
            _lookup_module(scopes, node.value.id) == "zipfile"
            and node.attr in ZIPFILE_ARCHIVE_CLASSES
        )
    if isinstance(node, ast.Name):
        identity = _lookup_name(scopes, node.id)
        return (
            identity is not None
            and identity[0] == "zipfile"
            and identity[1] in ZIPFILE_ARCHIVE_CLASSES
        )
    return False


def _is_zip_constructor(node: ast.expr, scopes: list[_Scope]) -> bool:
    return isinstance(node, ast.Call) and _is_zip_class_expr(node.func, scopes)


def _annotation_mentions_zip(node: ast.expr | None, scopes: list[_Scope]) -> bool:
    """True for ``ZipFile``, ``ZipFile | None``, ``Optional[ZipFile]``, or a string form."""
    if node is None:
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return False
    return any(
        isinstance(sub, ast.expr) and _is_zip_class_expr(sub, scopes)
        for sub in ast.walk(node)
    )


def _lookup_zip_archive(scopes: list[_Scope], name: str) -> bool:
    for scope in reversed(scopes):
        if name in scope.zip_archives:
            return scope.zip_archives[name]
    return False


def _is_zip_archive_expr(node: ast.expr, scopes: list[_Scope]) -> bool:
    if isinstance(node, ast.Name):
        return _lookup_zip_archive(scopes, node.id)
    return _is_zip_constructor(node, scopes)


class _InvariantVisitor(ast.NodeVisitor):
    """Walk the tree, resolving os/shutil/zipfile aliases in the current lexical scope."""

    def __init__(self, relpath: str) -> None:
        self.relpath = relpath
        self.scopes: list[_Scope] = [_Scope()]
        self.findings: list[Finding] = []

    def _push(self) -> None:
        self.scopes.append(_Scope())

    def _pop(self) -> None:
        self.scopes.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # Decorators, defaults and annotations evaluate in the enclosing scope.
        for decorator in node.decorator_list:
            self.visit(decorator)
        args = node.args
        params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        params.extend(a for a in (args.vararg, args.kwarg) if a is not None)
        for default in [*args.defaults, *(d for d in args.kw_defaults if d is not None)]:
            self.visit(default)
        for param in params:
            if param.annotation is not None:
                self.visit(param.annotation)
        if node.returns is not None:
            self.visit(node.returns)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)
        zip_params = {
            param.arg: _annotation_mentions_zip(param.annotation, self.scopes)
            for param in params
        }
        self._push()
        # Parameters are locals: they shadow enclosing archive bindings unless
        # annotated as a zipfile archive themselves.
        self.scopes[-1].zip_archives.update(zip_params)
        for stmt in node.body:
            self.visit(stmt)
        self._pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _bind_target(self, target: ast.expr, is_archive: bool) -> None:
        if not isinstance(target, ast.Name):
            return
        bindings = self.scopes[-1].zip_archives
        if is_archive:
            bindings[target.id] = True
        else:
            bindings.setdefault(target.id, False)

    def visit_Assign(self, node: ast.Assign) -> None:
        is_archive = _is_zip_archive_expr(node.value, self.scopes)
        for target in node.targets:
            self._bind_target(target, is_archive)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        is_archive = _annotation_mentions_zip(node.annotation, self.scopes) or (
            node.value is not None and _is_zip_archive_expr(node.value, self.scopes)
        )
        self._bind_target(node.target, is_archive)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._bind_target(node.target, _is_zip_archive_expr(node.value, self.scopes))
        self.generic_visit(node)

    def _bind_with_items(self, items: list[ast.withitem]) -> None:
        for item in items:
            if item.optional_vars is not None:
                self._bind_target(
                    item.optional_vars,
                    _is_zip_archive_expr(item.context_expr, self.scopes),
                )

    def visit_With(self, node: ast.With) -> None:
        self._bind_with_items(node.items)
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._bind_with_items(node.items)
        self.generic_visit(node)

    def _report_zip_extractall(self, node: ast.AST) -> None:
        self.findings.append(
            Finding(
                "INV-BACKUP-001",
                self.relpath,
                getattr(node, "lineno", 1),
                (
                    "direct ZipFile.extractall() is forbidden in production PRKS code; "
                    "it trusts archive member names and types. Restore must use the "
                    "validated, staged per-member extraction in backend.backup_restore"
                ),
            )
        )

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Match the attribute itself so method references (``f = zf.extractall``)
        # and unbound calls (``ZipFile.extractall(zf, dest)``) are covered too.
        if node.attr == BANNED_ZIPFILE_METHOD and (
            _is_zip_archive_expr(node.value, self.scopes)
            or _is_zip_class_expr(node.value, self.scopes)
        ):
            self._report_zip_extractall(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._push()
        self.generic_visit(node)
        self._pop()

    def visit_Import(self, node: ast.Import) -> None:
        _bind_import(self.scopes[-1], node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        _bind_import_from(self.scopes[-1], node)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == BANNED_ZIPFILE_METHOD
            and _is_zip_archive_expr(node.args[0], self.scopes)
        ):
            self._report_zip_extractall(node)
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
    """True when ignore/exclude can match ``backend/storage`` or anything under it."""
    return _path_covers_root(entry, PYRIGHT_TYPED_SLICE_ROOT)


def _reject_ignore_exclude_covering(
    cfg: dict,
    *,
    protected_root: str,
    config_name: str,
    code: str,
) -> list[Finding]:
    """Reject ignore/exclude entries that would silence ``protected_root``."""
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
            if _path_covers_root(entry, protected_root):
                findings.append(
                    Finding(
                        code,
                        config_name,
                        1,
                        (
                            f"{config_name} {key} entry {entry!r} would suppress "
                            f"{protected_root}; only __pycache__ exclusions are allowed"
                        ),
                    )
                )
    return findings


def _reject_typed_slice_suppression(cfg: dict) -> list[Finding]:
    """Reject ignore/exclude entries that would silence the typed slice."""
    return _reject_ignore_exclude_covering(
        cfg,
        protected_root=PYRIGHT_TYPED_SLICE_ROOT,
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
            findings.extend(
                _reject_ignore_exclude_covering(
                    dataflow,
                    protected_root=PYRIGHT_DATAFLOW_REQUIRED_INCLUDE,
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