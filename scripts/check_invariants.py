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
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]

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
    "backend/fs_durability.py",
    "backend/pdf_linearize.py",
    "backend/server.py",
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


def iter_backend_python(root: Path) -> Iterable[Path]:
    backend = root / "backend"
    yield from sorted(p for p in backend.rglob("*.py") if p.is_file())


def check_repo(root: Path = REPO_ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_backend_python(root):
        rel = path.relative_to(root).as_posix()
        findings.extend(check_source(path.read_text(encoding="utf-8"), rel))
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
    if findings:
        for finding in findings:
            print(finding.render())
        print(f"engineering invariant check failed: {len(findings)} violation(s)")
        return 1

    print("engineering invariant check: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
