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
OS_REPLACE_ALLOWLIST = {
    "backend/backup_restore.py",
    "backend/fs_durability.py",
    "backend/pdf_linearize.py",
    "backend/server.py",
    "backend/services/work_pdf_replace.py",
}
OS_FSYNC_ALLOWLIST = {
    "backend/fs_durability.py",
    "backend/services/work_pdf_replace.py",
}


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    line: int
    message: str

    def render(self) -> str:
        return f"{self.code} {self.path}:{self.line}: {self.message}"


class ImportAliases(ast.NodeVisitor):
    """Resolve only the simple stdlib aliases this checker needs."""

    def __init__(self) -> None:
        self.modules: dict[str, str] = {}
        self.names: dict[str, tuple[str, str]] = {}

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            if item.name in {"os", "shutil"}:
                self.modules[item.asname or item.name] = item.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module not in {"os", "shutil"}:
            return
        for item in node.names:
            if item.name == "*":
                continue
            self.names[item.asname or item.name] = (node.module, item.name)


def _call_identity(node: ast.Call, aliases: ImportAliases) -> tuple[str, str] | None:
    fn = node.func
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
        module = aliases.modules.get(fn.value.id)
        if module:
            return module, fn.attr
    if isinstance(fn, ast.Name):
        return aliases.names.get(fn.id)
    return None


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

    aliases = ImportAliases()
    aliases.visit(tree)
    findings: list[Finding] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        identity = _call_identity(node, aliases)
        if identity is None:
            continue
        module, name = identity

        if module == "shutil" and name in BANNED_SHUTIL_COPY_CALLS:
            findings.append(
                Finding(
                    "INV-STORAGE-001",
                    relpath,
                    node.lineno,
                    (
                        f"direct shutil.{name}() is forbidden in backend production code; "
                        "publish managed PDFs through backend.services.work_pdf_replace "
                        "(store_new_managed_pdf_bytes/store_new_managed_pdf_from_path), "
                        "or use a domain-specific storage capability"
                    ),
                )
            )
        elif module == "os" and name == "replace" and relpath not in OS_REPLACE_ALLOWLIST:
            findings.append(
                Finding(
                    "INV-DURABILITY-001",
                    relpath,
                    node.lineno,
                    (
                        "direct os.replace() is outside the approved durability boundary; "
                        "use backend.fs_durability or an existing durable domain helper"
                    ),
                )
            )
        elif module == "os" and name == "fsync" and relpath not in OS_FSYNC_ALLOWLIST:
            findings.append(
                Finding(
                    "INV-DURABILITY-002",
                    relpath,
                    node.lineno,
                    (
                        "direct os.fsync() is outside the approved durability boundary; "
                        "use backend.fs_durability helpers"
                    ),
                )
            )

    return findings


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
