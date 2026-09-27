#!/usr/bin/env python3
"""Schema-change PR gate (#190).

Keeps the canonical schema, the ordered migration registry, the schema
version, and the load-bearing primary-key registry in sync. The rules come
from backend/AGENTS.md "Database schema changes"; this makes them structural
instead of review memory.

Two families of checks:

Diff-aware (vs ``--base``; ordinary PRs that do not touch the schema or the
schema version pass untouched):

- SCHEMA-GATE-001: ``backend/db_schema.sql`` changed (ignoring SQL comments
  and whitespace) but ``LATEST_SCHEMA_VERSION`` was not bumped.
- SCHEMA-GATE-002: ``LATEST_SCHEMA_VERSION`` moved, but ``MIGRATIONS`` did not
  gain exactly one ``Migration(target_version=N)`` for each new version, or
  lost/changed an existing one.

Structural (always, against the working tree):

- SCHEMA-GATE-003: a canonical table written with ``INSERT ... ON CONFLICT``
  in backend code is not registered in ``_CURRENT_TABLE_PKS`` (the upsert
  relies on a primary key that startup validation would not protect) and is
  not in the reviewed ``PK_REGISTRY_ALLOWLIST`` below.
- SCHEMA-GATE-004: a ``_CURRENT_TABLE_PKS`` entry disagrees with the primary
  key that ``db_schema.sql`` actually creates, or names a missing table.
- SCHEMA-GATE-005: an ``ON CONFLICT(cols)`` target on a registered table is
  not its registered primary key.
- SCHEMA-GATE-006: a ``PK_REGISTRY_ALLOWLIST`` entry is stale (the table is
  now registered, no longer upserted, or no longer canonical).

Tables that are not created by ``db_schema.sql`` (the disposable text/research
index databases) are out of scope.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SCHEMA_RELPATH = "backend/db_schema.sql"
MIGRATIONS_RELPATH = "backend/db_migrations.py"
BACKEND_PREFIX = "backend/"
CHECKER_RELPATH = "scripts/check_schema_change.py"
POLICY = 'backend/AGENTS.md "Database schema changes"'

# Canonical tables that are upserted with ON CONFLICT but deliberately not in
# _CURRENT_TABLE_PKS. Registering a table makes startup reject libraries whose
# PK drifted, so adding one is a reviewed database change, not a lint fix.
# Keep entries narrow (one table, one reason); never add a wildcard. Entries
# that stop applying fail SCHEMA-GATE-006 so the list only shrinks.
PK_REGISTRY_ALLOWLIST: dict[str, str] = {
    "app_settings": "pre-#190 key/value upsert; PK validation not yet registered",
    "folder_files": "pre-#190 membership insert; PK validation not yet registered",
    "folder_tags": "pre-#190 membership insert; PK validation not yet registered",
    "processing_file_tags": "pre-#190 membership insert; PK validation not yet registered",
    "work_tags": "pre-#190 membership insert; PK validation not yet registered",
}

_E2E_WAIT_CHECKER = Path(__file__).resolve().parent / "check_e2e_wait_for_timeout.py"
_SPEC = importlib.util.spec_from_file_location(
    "prks_check_e2e_wait_for_timeout", _E2E_WAIT_CHECKER
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"cannot load {_E2E_WAIT_CHECKER}")
_git_rev = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault(_SPEC.name, _git_rev)
_SPEC.loader.exec_module(_git_rev)

# Shared git-revision plumbing with the #189 diff-aware guard.
DiscoveryError = _git_rev.DiscoveryError
EMPTY_TREE_SHA = _git_rev.EMPTY_TREE_SHA
sanitize_git_revision = _git_rev.sanitize_git_revision
read_file_at_revision = _git_rev.read_file_at_revision

_INSERT_RE = re.compile(
    r"\bINSERT\s+(?:OR\s+[A-Za-z]+\s+)?INTO\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_ON_CONFLICT_RE = re.compile(
    r"\bON\s+CONFLICT\s*(?:\(([^)]*)\))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    line: int
    message: str
    fix: str

    def render(self) -> str:
        return (
            f"{self.code} {self.path}:{self.line}: {self.message}\n"
            f"  fix: {self.fix}\n"
            f"  policy: {POLICY}"
        )


def resolve_base(repo: Path, explicit: str | None) -> str:
    """``--base`` / ``$PRKS_SCHEMA_GATE_BASE`` / ``HEAD`` as a commit SHA."""
    raw = explicit or (os.environ.get("PRKS_SCHEMA_GATE_BASE") or "").strip() or "HEAD"
    return _git_rev.resolve_base(repo, raw)


# ---------------------------------------------------------------------------
# Parsing helpers (AST only: never import either revision of db_migrations)


def normalize_sql(text: str) -> str:
    """Strip ``--`` comments and collapse whitespace; comment edits are not schema."""
    out = []
    for line in text.splitlines():
        in_str = False
        cut = len(line)
        for i, ch in enumerate(line):
            if ch == "'":
                in_str = not in_str
            elif not in_str and line.startswith("--", i):
                cut = i
                break
        out.append(line[:cut])
    return " ".join(" ".join(out).split())


@dataclass(frozen=True)
class MigrationInfo:
    version: int | None
    version_line: int
    targets: dict[int, str]
    registry_line: int
    table_pks: dict[str, tuple[str, ...]] | None
    table_pks_line: int


def _assign_target(node: ast.stmt) -> tuple[str | None, ast.expr | None]:
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id, node.value
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if isinstance(target, ast.Name):
            return target.id, node.value
    return None, None


def _migration_targets(value: ast.expr) -> dict[int, str]:
    targets: dict[int, str] = {}
    if not isinstance(value, (ast.Tuple, ast.List)):
        return targets
    for elt in value.elts:
        if not isinstance(elt, ast.Call):
            continue
        kw = {k.arg: k.value for k in elt.keywords if k.arg}
        tv = kw.get("target_version")
        nm = kw.get("name")
        if isinstance(tv, ast.Constant) and isinstance(tv.value, int):
            label = nm.value if isinstance(nm, ast.Constant) else "?"
            targets[tv.value] = str(label)
    return targets


def _literal_table_pks(value: ast.expr, where: str) -> dict[str, tuple[str, ...]]:
    try:
        raw = ast.literal_eval(value)
    except ValueError as exc:
        raise DiscoveryError(f"{where}: _CURRENT_TABLE_PKS must stay a literal dict") from exc
    return {str(k): tuple(v) for k, v in raw.items()}


def parse_migrations_module(source: str, where: str) -> MigrationInfo:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise DiscoveryError(f"{where}: cannot parse {MIGRATIONS_RELPATH} ({exc.msg})") from exc
    assigns: dict[str, tuple[int, ast.expr]] = {}
    for node in tree.body:
        name, value = _assign_target(node)
        if name is not None and value is not None:
            assigns[name] = (node.lineno, value)
    version_line, version_node = assigns.get("LATEST_SCHEMA_VERSION", (1, None))
    if not (isinstance(version_node, ast.Constant) and isinstance(version_node.value, int)):
        raise DiscoveryError(
            f"{where}: LATEST_SCHEMA_VERSION is not an integer literal in {MIGRATIONS_RELPATH}"
        )
    registry_line, registry_node = assigns.get("MIGRATIONS", (1, None))
    pks_line, pks_node = assigns.get("_CURRENT_TABLE_PKS", (1, None))
    return MigrationInfo(
        version_node.value,
        version_line,
        {} if registry_node is None else _migration_targets(registry_node),
        registry_line,
        None if pks_node is None else _literal_table_pks(pks_node, where),
        pks_line,
    )


def schema_table_pks(schema_sql: str) -> dict[str, tuple[str, ...]]:
    """Primary key of every table the canonical schema creates (fresh DB)."""
    conn = sqlite3.connect(":memory:")
    try:
        try:
            conn.executescript(schema_sql)
        except sqlite3.Error as exc:
            raise DiscoveryError(
                f"{SCHEMA_RELPATH} does not load into SQLite ({exc.__class__.__name__}: {exc})"
            ) from exc
        names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        ]
        pks: dict[str, tuple[str, ...]] = {}
        for name in names:
            cols = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
            pk = sorted((c[5], c[1]) for c in cols if c[5])
            pks[name] = tuple(col for _, col in pk)
        return pks
    finally:
        conn.close()


@dataclass(frozen=True)
class UpsertSite:
    path: str
    line: int
    table: str
    target: tuple[str, ...] | None


def iter_upsert_sites(source: str, relpath: str) -> list[UpsertSite]:
    """``INSERT INTO t ... ON CONFLICT[(cols)]`` in Python string literals."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    sites: list[UpsertSite] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        text = node.value
        if "CONFLICT" not in text.upper():
            continue
        for stmt in text.split(";"):
            insert = _INSERT_RE.search(stmt)
            if insert is None:
                continue
            conflict = _ON_CONFLICT_RE.search(stmt, insert.end())
            if conflict is None:
                continue
            target = None
            if conflict.group(1) is not None:
                target = tuple(c.strip() for c in conflict.group(1).split(",") if c.strip())
            sites.append(UpsertSite(relpath, node.lineno, insert.group(1), target))
    return sites


# ---------------------------------------------------------------------------
# Checks


def _version_step_findings(base: MigrationInfo, head: MigrationInfo) -> list[Finding]:
    """One new Migration per bumped version, and no migration without a bump."""
    findings: list[Finding] = []
    if base.version is None or head.version is None:
        raise DiscoveryError("LATEST_SCHEMA_VERSION could not be parsed")
    if head.version < base.version:
        findings.append(
            Finding(
                "SCHEMA-GATE-002",
                MIGRATIONS_RELPATH,
                head.version_line,
                f"LATEST_SCHEMA_VERSION went backwards ({base.version} -> {head.version})",
                "never lower the schema version; add a forward migration instead",
            )
        )
    elif head.version > base.version:
        expected = set(range(base.version + 1, head.version + 1))
        added = set(head.targets) - set(base.targets)
        for version in sorted(expected - added):
            findings.append(
                Finding(
                    "SCHEMA-GATE-002",
                    MIGRATIONS_RELPATH,
                    head.registry_line,
                    (
                        f"LATEST_SCHEMA_VERSION is {head.version} (base {base.version}) "
                        f"but MIGRATIONS has no new Migration(target_version={version})"
                    ),
                    (
                        f"append Migration(target_version={version}, name=..., apply=...) "
                        f"to MIGRATIONS in {MIGRATIONS_RELPATH}"
                    ),
                )
            )
        for version in sorted(added - expected):
            findings.append(
                Finding(
                    "SCHEMA-GATE-002",
                    MIGRATIONS_RELPATH,
                    head.registry_line,
                    (
                        f"new Migration(target_version={version}) is outside the bumped "
                        f"range {base.version + 1}..{head.version}"
                    ),
                    "make LATEST_SCHEMA_VERSION equal the last migration target",
                )
            )
    elif set(head.targets) - set(base.targets):
        findings.append(
            Finding(
                "SCHEMA-GATE-002",
                MIGRATIONS_RELPATH,
                head.registry_line,
                (
                    "MIGRATIONS gained "
                    + ", ".join(
                        f"target_version={v}"
                        for v in sorted(set(head.targets) - set(base.targets))
                    )
                    + f" but LATEST_SCHEMA_VERSION is still {head.version}"
                ),
                "bump LATEST_SCHEMA_VERSION to the new migration's target_version",
            )
        )
    return findings


def check_companions(
    base_schema: str | None,
    head_schema: str,
    base: MigrationInfo | None,
    head: MigrationInfo,
) -> list[Finding]:
    """Diff-aware: schema edits need a version bump and one migration per bump."""
    if base_schema is None or base is None:
        # Greenfield (no base revision of the schema/migrations): nothing to diff.
        return []
    findings: list[Finding] = []
    schema_changed = normalize_sql(base_schema) != normalize_sql(head_schema)
    if base.version is None or head.version is None:
        raise DiscoveryError("LATEST_SCHEMA_VERSION could not be parsed")
    if schema_changed and head.version <= base.version:
        findings.append(
            Finding(
                "SCHEMA-GATE-001",
                MIGRATIONS_RELPATH,
                head.version_line,
                (
                    f"{SCHEMA_RELPATH} changed but LATEST_SCHEMA_VERSION is still "
                    f"{head.version} (base {base.version}); existing libraries would "
                    "never receive the change"
                ),
                (
                    f"bump LATEST_SCHEMA_VERSION to {base.version + 1} and add "
                    f"Migration(target_version={base.version + 1}, ...) to MIGRATIONS "
                    f"in {MIGRATIONS_RELPATH}, plus fresh-DB and upgraded-DB tests"
                ),
            )
        )
    for version, name in sorted(base.targets.items()):
        if head.targets.get(version) != name:
            findings.append(
                Finding(
                    "SCHEMA-GATE-002",
                    MIGRATIONS_RELPATH,
                    head.registry_line,
                    (
                        f"existing Migration(target_version={version}, name={name!r}) "
                        "was removed or renamed; shipped migrations are append-only"
                    ),
                    "restore it and add a new migration for the new change instead",
                )
            )
    findings.extend(_version_step_findings(base, head))
    return findings


def _registry_parity_findings(
    schema_pks: dict[str, tuple[str, ...]],
    registry: dict[str, tuple[str, ...]],
    line: int,
) -> list[Finding]:
    findings: list[Finding] = []
    for table, pk in sorted(registry.items()):
        actual = schema_pks.get(table)
        if actual is None:
            findings.append(
                Finding(
                    "SCHEMA-GATE-004",
                    MIGRATIONS_RELPATH,
                    line,
                    f"_CURRENT_TABLE_PKS[{table!r}] names a table {SCHEMA_RELPATH} does not create",
                    f"create {table} in {SCHEMA_RELPATH} (with its migration) or drop the entry",
                )
            )
        elif actual != pk:
            findings.append(
                Finding(
                    "SCHEMA-GATE-004",
                    MIGRATIONS_RELPATH,
                    line,
                    (
                        f"_CURRENT_TABLE_PKS[{table!r}] is {pk!r} but {SCHEMA_RELPATH} "
                        f"creates PRIMARY KEY {actual!r}"
                    ),
                    "make the registry and the canonical schema agree (a PK change needs a migration)",
                )
            )
    return findings


def _missing_registry_finding(site: UpsertSite, schema_pk: tuple[str, ...]) -> Finding:
    return Finding(
        "SCHEMA-GATE-003",
        site.path,
        site.line,
        (
            f"INSERT INTO {site.table} ... ON CONFLICT relies on the "
            f"{site.table} primary key {schema_pk!r}, but "
            f"{site.table!r} is missing from _CURRENT_TABLE_PKS"
        ),
        (
            f"add {site.table!r}: {schema_pk!r} to "
            f"_CURRENT_TABLE_PKS in {MIGRATIONS_RELPATH} (and its "
            "_CURRENT_TABLE_FKS entry) so startup validation protects it"
        ),
    )


def _upsert_findings(
    schema_pks: dict[str, tuple[str, ...]],
    registry: dict[str, tuple[str, ...]],
    sites: list[UpsertSite],
    allow: dict[str, str],
) -> list[Finding]:
    findings: list[Finding] = []
    reported: set[str] = set(allow)
    for site in sorted(sites, key=lambda s: (s.path, s.line)):
        if site.table not in schema_pks:
            continue  # derived/disposable index DBs are out of scope
        pk = registry.get(site.table)
        if pk is None:
            if site.table not in reported:
                reported.add(site.table)
                findings.append(_missing_registry_finding(site, schema_pks[site.table]))
        elif site.target is not None and set(site.target) != set(pk):
            findings.append(
                Finding(
                    "SCHEMA-GATE-005",
                    site.path,
                    site.line,
                    (
                        f"ON CONFLICT{site.target!r} on {site.table} does not match "
                        f"its registered primary key {pk!r}"
                    ),
                    "target the registered primary key, or update the registry with a migration",
                )
            )
    return findings


def _stale_allowlist_findings(
    schema_pks: dict[str, tuple[str, ...]],
    registry: dict[str, tuple[str, ...]],
    upserted: set[str],
    allow: dict[str, str],
) -> list[Finding]:
    findings: list[Finding] = []
    for table in sorted(allow):
        if table in registry:
            why = "is now registered in _CURRENT_TABLE_PKS"
        elif table not in schema_pks:
            why = f"is no longer created by {SCHEMA_RELPATH}"
        elif table not in upserted:
            why = "is no longer written with ON CONFLICT in backend/"
        else:
            continue
        findings.append(
            Finding(
                "SCHEMA-GATE-006",
                CHECKER_RELPATH,
                1,
                f"PK_REGISTRY_ALLOWLIST entry {table!r} is stale: it {why}",
                "remove the entry so the allowlist only shrinks",
            )
        )
    return findings


def check_pk_registry(
    schema_pks: dict[str, tuple[str, ...]],
    head: MigrationInfo,
    sites: list[UpsertSite],
    *,
    allowlist: dict[str, str] | None = None,
) -> list[Finding]:
    """Structural: load-bearing upserts on canonical tables are PK-registered."""
    allow = PK_REGISTRY_ALLOWLIST if allowlist is None else allowlist
    registry = head.table_pks
    if registry is None:
        return [
            Finding(
                "SCHEMA-GATE-004",
                MIGRATIONS_RELPATH,
                1,
                "_CURRENT_TABLE_PKS registry is missing",
                f"restore the literal _CURRENT_TABLE_PKS dict in {MIGRATIONS_RELPATH}",
            )
        ]
    upserted = {site.table for site in sites if site.table in schema_pks}
    return (
        _registry_parity_findings(schema_pks, registry, head.table_pks_line)
        + _upsert_findings(schema_pks, registry, sites, allow)
        + _stale_allowlist_findings(schema_pks, registry, upserted, allow)
    )


def _backend_sources(repo: Path) -> list[tuple[str, str]]:
    out = []
    for path in sorted((repo / "backend").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        out.append((path.relative_to(repo).as_posix(), path.read_text(encoding="utf-8")))
    return out


def collect_findings(
    repo: Path,
    base_sha: str,
    *,
    allowlist: dict[str, str] | None = None,
) -> list[Finding]:
    schema_path = repo / SCHEMA_RELPATH
    migrations_path = repo / MIGRATIONS_RELPATH
    if not schema_path.is_file() or not migrations_path.is_file():
        raise DiscoveryError(
            f"missing {SCHEMA_RELPATH} or {MIGRATIONS_RELPATH} in the working tree"
        )
    head_schema = schema_path.read_text(encoding="utf-8")
    head = parse_migrations_module(migrations_path.read_text(encoding="utf-8"), "working tree")

    base_schema_lines = read_file_at_revision(repo, base_sha, SCHEMA_RELPATH)
    base_migrations_lines = read_file_at_revision(repo, base_sha, MIGRATIONS_RELPATH)
    base_schema = None if base_schema_lines is None else "\n".join(base_schema_lines)
    base = (
        None
        if base_migrations_lines is None
        else parse_migrations_module("\n".join(base_migrations_lines), f"base {base_sha}")
    )

    findings = check_companions(base_schema, head_schema, base, head)
    sites: list[UpsertSite] = []
    for relpath, source in _backend_sources(repo):
        sites.extend(iter_upsert_sites(source, relpath))
    findings.extend(
        check_pk_registry(schema_table_pks(head_schema), head, sites, allowlist=allowlist)
    )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repository root")
    parser.add_argument(
        "--base",
        default=None,
        help=(
            "git revision to diff against (default: $PRKS_SCHEMA_GATE_BASE or HEAD). "
            "Pull-request CI passes github.event.pull_request.base.sha."
        ),
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        base = resolve_base(root, args.base)
        findings = collect_findings(root, base)
    except DiscoveryError as exc:
        print(f"schema-change gate failed closed: {exc}", file=sys.stderr)
        return 2
    if findings:
        for finding in findings:
            print(finding.render())
            print()
        print(f"schema-change gate failed: {len(findings)} finding(s) vs {base}")
        return 1
    print(f"schema-change gate: OK vs {base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
