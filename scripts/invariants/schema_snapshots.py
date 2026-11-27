"""Current-schema snapshot guard for tests (INV-TESTS-001, #103)."""
from __future__ import annotations

import ast
import re
from pathlib import Path

from .findings import Finding, REPO_ROOT


# --- Current-schema snapshots in tests (INV-TESTS-001, #103) ---------------
#
# A test that pins the repository-wide schema counter to an integer
# (``assertEqual(PRKS_SCHEMA_VERSION, 17)``, ``assert LATEST_SCHEMA_VERSION ==
# 17``) or greps ``db_migrations.py`` for ``"LATEST_SCHEMA_VERSION = 17"`` fails
# on every legitimate future migration, whatever feature it covers. Feature
# tests assert the columns/tables they need instead; consistency tests compare
# the two constants with each other.
#
# Only the version constants are matched (by lexical name, so module-qualified
# ``db_migrations.LATEST_SCHEMA_VERSION`` counts too). Other numeric versions
# (``UPDATE schema_version SET version = 12`` fixtures, derived-index versions)
# are untouched. The allowlist is the migration-focused tests, where the
# number itself is the contract; keep it that narrow.
CURRENT_SCHEMA_VERSION_NAMES = frozenset({"LATEST_SCHEMA_VERSION", "PRKS_SCHEMA_VERSION"})
SCHEMA_VERSION_SNAPSHOT_ALLOWLIST = {
    # Migration registry contract: pins the latest version a migration targets.
    "tests/test_db_migrations.py",
    # Schema-change gate: synthetic repositories with fabricated versions.
    "tests/test_schema_change_gate.py",
}
_EQUALITY_ASSERTS = frozenset({"assertEqual", "assertEquals"})
_SCHEMA_VERSION_SOURCE_RE = re.compile(
    r"\b(?:LATEST|PRKS)_SCHEMA_VERSION\s*(?::\s*\w+\s*)?=\s*\d"
)


def _is_current_schema_version_ref(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id in CURRENT_SCHEMA_VERSION_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in CURRENT_SCHEMA_VERSION_NAMES
    return False


def _is_int_literal(node: ast.expr) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        node = node.operand
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    )


def _pins_current_schema(left: ast.expr, right: ast.expr) -> bool:
    return (_is_current_schema_version_ref(left) and _is_int_literal(right)) or (
        _is_current_schema_version_ref(right) and _is_int_literal(left)
    )


def check_test_schema_snapshots(source: str, relpath: str) -> list[Finding]:
    """INV-TESTS-001 findings for one test module (see the block comment above)."""
    if relpath in SCHEMA_VERSION_SNAPSHOT_ALLOWLIST:
        return []
    try:
        tree = ast.parse(source, filename=relpath)
    except SyntaxError:
        return []
    findings: list[Finding] = []
    hint = (
        "assert the feature's columns/tables, compare PRKS_SCHEMA_VERSION with "
        "LATEST_SCHEMA_VERSION, or drop an obsolete no-schema-bump guard"
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in _EQUALITY_ASSERTS and len(node.args) >= 2 and _pins_current_schema(
                node.args[0], node.args[1]
            ):
                findings.append(
                    Finding(
                        "INV-TESTS-001",
                        relpath,
                        node.lineno,
                        f"test pins the current schema version to an integer; {hint}",
                    )
                )
        elif isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for op, left, right in zip(node.ops, operands, operands[1:]):
                if isinstance(op, ast.Eq) and _pins_current_schema(left, right):
                    findings.append(
                        Finding(
                            "INV-TESTS-001",
                            relpath,
                            node.lineno,
                            f"test pins the current schema version to an integer; {hint}",
                        )
                    )
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _SCHEMA_VERSION_SOURCE_RE.search(node.value)
        ):
            findings.append(
                Finding(
                    "INV-TESTS-001",
                    relpath,
                    node.lineno,
                    f"test matches a current schema-version assignment in source text; {hint}",
                )
            )
    return findings


def check_test_schema_snapshots_repo(root: Path = REPO_ROOT) -> list[Finding]:
    findings: list[Finding] = []
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        return findings
    for path in sorted(tests_dir.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        findings.extend(check_test_schema_snapshots(path.read_text(encoding="utf-8"), rel))
    return findings
