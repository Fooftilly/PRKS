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
- SCHEMA-GATE-002: ``MIGRATIONS`` did not gain exactly one
  ``Migration(target_version=N)`` per bumped version, appended in order; a
  shipped entry was removed, reordered, renamed or re-pointed to another
  ``apply``; a target repeats; or a migration was added without a bump.

Structural (always, against the working tree):

- SCHEMA-GATE-003: a canonical table written with ``INSERT ... ON CONFLICT``
  in backend code is not registered in ``_CURRENT_TABLE_PKS`` (the upsert
  relies on a primary key that startup validation would not protect) and is
  not in the reviewed ``PK_REGISTRY_ALLOWLIST`` below.
- SCHEMA-GATE-004: a ``_CURRENT_TABLE_PKS`` entry disagrees with the primary
  key that ``db_schema.sql`` actually creates, or names a missing table.
- SCHEMA-GATE-005: an explicit ``ON CONFLICT(cols)`` target on a canonical
  table repeats a column or is not its primary key (the registered one, or the
  ``db_schema.sql`` one for unregistered / allowlisted tables).
- SCHEMA-GATE-006: a ``PK_REGISTRY_ALLOWLIST`` entry is stale (the table is
  now registered, no longer upserted, or no longer canonical).
- SCHEMA-GATE-007: an ``ON CONFLICT ... DO`` upsert whose ``INSERT INTO`` table
  cannot be resolved statically (the registry check would otherwise miss it).

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
# Keep entries narrow (one table, one reason); never add a wildcard. An entry
# waives only SCHEMA-GATE-003; explicit conflict targets are still checked. Entries
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

# One SQL identifier: "double" / `back` / [bracket] quoted (doubled quote
# escapes), or bare. Quoted names are matched whole, so "works backup" is never
# read as its canonical-looking prefix.
_IDENT = r'(?:"(?:[^"]|"")*"|`(?:[^`]|``)*`|\[[^\]]*\]|[A-Za-z_][A-Za-z0-9_]*)'
_INSERT_RE = re.compile(
    r"\bINSERT\s+(?:OR\s+[A-Za-z]+\s+)?INTO\s+"
    rf"(?:({_IDENT})\s*\.\s*)?({_IDENT})",
    re.IGNORECASE,
)


def _unquote_ident(raw: str) -> str:
    if raw[:1] in "\"`" and raw[-1:] == raw[:1]:
        return raw[1:-1].replace(raw[0] * 2, raw[0])
    if raw[:1] == "[" and raw[-1:] == "]":
        return raw[1:-1]
    return raw


def _insert_target(match: re.Match[str]) -> str:
    """Lower-cased INSERT target (SQLite identifiers are case-insensitive).

    Only the ``main`` schema is canonical; ``temp.works`` and attached-database
    tables keep their qualifier so they never match a canonical table.
    """
    table = _unquote_ident(match.group(2)).lower()
    schema = _unquote_ident(match.group(1)).lower() if match.group(1) else "main"
    return table if schema == "main" else f"{schema}.{table}"


# Any ON CONFLICT that is not a constraint resolution clause; inside an INSERT
# it is an upsert even when _UPSERT_RE cannot parse its target.
_ANY_UPSERT_RE = re.compile(
    r"\bON\s+CONFLICT\b(?!\s*(?:ROLLBACK|ABORT|FAIL|IGNORE|REPLACE)\b)",
    re.IGNORECASE,
)
# Upsert clause only (``DO`` required): ``UNIQUE ... ON CONFLICT REPLACE``
# constraint clauses in CREATE TABLE are not upserts.
_UPSERT_RE = re.compile(
    r"\bON\s+CONFLICT\s*(?:\(([^)]*)\))?(?:\s*WHERE\b.*?)?\s*DO\b",
    re.IGNORECASE | re.DOTALL,
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


def _quoted_end(text: str, i: int) -> int:
    """Index just past the quoted token starting at ``i`` (doubled quotes escape)."""
    quote, j = text[i], i + 1
    while j < len(text):
        if text[j] != quote:
            j += 1
        elif text.startswith(quote * 2, j):
            j += 2
        else:
            return j + 1
    return len(text)


def _comment_end(text: str, i: int) -> int | None:
    """Index just past the SQL comment starting at ``i``, or None if none starts there."""
    if text.startswith("--", i):
        end = text.find("\n", i)
        return len(text) if end < 0 else end
    if text.startswith("/*", i):
        end = text.find("*/", i + 2)
        return len(text) if end < 0 else end + 2
    return None


def _mask_sql(text: str) -> str:
    """Blank string literals and comments (same length), keep identifiers.

    Structural scans then never read ``'ON CONFLICT(x) DO ...'`` inside a
    literal or comment as SQL, while quoted table names still parse.
    """
    out: list[str] = []
    i = 0
    while i < len(text):
        comment_end = _comment_end(text, i)
        if comment_end is not None:
            out.append(" " * (comment_end - i))
            i = comment_end
            continue
        ch = text[i]
        if ch in "'\"`":
            j = _quoted_end(text, i)
            body = text[i:j]
            out.append(ch + " " * (len(body) - 2) + body[-1] if ch == "'" and len(body) >= 2 else body)
            i = j
        elif ch == "[":
            j = text.find("]", i)
            j = len(text) if j < 0 else j + 1
            out.append(text[i:j])
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def normalize_sql(text: str) -> str:
    """Drop SQL comments and insignificant whitespace; keep quoted text verbatim.

    Comment and layout edits are not schema changes, but whitespace inside a
    string literal or quoted identifier is (for example a changed DEFAULT).
    """
    out: list[str] = []
    i = 0
    pending_space = False
    while i < len(text):
        comment_end = _comment_end(text, i)
        if comment_end is not None or text[i].isspace():
            i = comment_end if comment_end is not None else i + 1
            pending_space = True
            continue
        j = _quoted_end(text, i) if text[i] in "'\"`" else i + 1
        if pending_space and out:
            out.append(" ")
        out.append(text[i:j])
        pending_space = False
        i = j
    return "".join(out)


@dataclass(frozen=True)
class MigrationEntry:
    target: int
    name: str
    apply: str

    def render(self) -> str:
        return f"Migration(target_version={self.target}, name={self.name!r}, apply={self.apply})"


@dataclass(frozen=True)
class MigrationInfo:
    version: int
    version_line: int
    entries: tuple[MigrationEntry, ...]
    registry_line: int
    table_pks: dict[str, tuple[str, ...]] | None
    table_pks_line: int

    @property
    def targets(self) -> dict[int, str]:
        return {e.target: e.name for e in self.entries}


def _assign_target(node: ast.stmt) -> tuple[str | None, ast.expr | None]:
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id, node.value
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if isinstance(target, ast.Name):
            return target.id, node.value
    return None, None


_MIGRATION_FIELDS = ("target_version", "name", "apply")


def _migration_entries(value: ast.expr, where: str) -> tuple[MigrationEntry, ...]:
    """Ordered ``MIGRATIONS`` entries; positional or keyword ``Migration`` args."""
    if not isinstance(value, (ast.Tuple, ast.List)):
        raise DiscoveryError(f"{where}: MIGRATIONS must stay a literal tuple of Migration(...)")
    entries: list[MigrationEntry] = []
    for index, elt in enumerate(value.elts):
        args: dict[str, ast.expr] = {}
        if isinstance(elt, ast.Call):
            args = dict(zip(_MIGRATION_FIELDS, elt.args))
            args.update({k.arg: k.value for k in elt.keywords if k.arg})
        tv = args.get("target_version")
        if not (isinstance(tv, ast.Constant) and isinstance(tv.value, int)):
            raise DiscoveryError(
                f"{where}: MIGRATIONS entry {index} is not a Migration(...) call with a "
                "literal integer target_version"
            )
        nm = args.get("name")
        apply = args.get("apply")
        entries.append(
            MigrationEntry(
                tv.value,
                str(nm.value) if isinstance(nm, ast.Constant) else "?",
                ast.unparse(apply) if apply is not None else "?",
            )
        )
    return tuple(entries)


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
        () if registry_node is None else _migration_entries(registry_node, where),
        registry_line,
        None if pks_node is None else _literal_table_pks(pks_node, where),
        pks_line,
    )


def schema_table_pks(schema_sql: str) -> dict[str, tuple[str, ...]]:
    """Primary key of every table the canonical schema creates (fresh DB)."""
    conn = sqlite3.connect(":memory:")
    # The schema text comes from the PR under test: allow DDL into this private
    # in-memory database only, never ATTACH (which could write runner files).
    conn.set_authorizer(
        lambda action, *_: sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH)
        else sqlite3.SQLITE_OK
    )
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
            pks[name.lower()] = tuple(col for _, col in pk)
        return pks
    finally:
        conn.close()


@dataclass(frozen=True)
class UpsertSite:
    path: str
    line: int
    table: str | None  # None: the INSERT target could not be resolved statically
    target: tuple[str, ...] | None


def _static_sql(node: ast.AST) -> str | None:
    """Best-effort static text of a string expression; ``{}`` marks dynamic parts."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}"
            for v in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _static_sql(node.left), _static_sql(node.right)
        if left is None and right is None:
            return None
        return (left if left is not None else "{}") + (right if right is not None else "{}")
    return None


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _conflict_columns(raw: str | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    return tuple(c.strip().strip('"`[]').lower() for c in raw.split(",") if c.strip())


def _statement_upserts(stmt: str, relpath: str, line: int) -> list[UpsertSite]:
    """One site per ON CONFLICT clause of an INSERT (SQLite allows several).

    A clause whose target cannot be parsed (e.g. ``ON CONFLICT(lower(x))``) is
    reported with ``table=None`` even when a sibling clause parses.
    """
    insert = _INSERT_RE.search(stmt)
    sites: list[UpsertSite] = []
    for clause in _ANY_UPSERT_RE.finditer(stmt):
        if insert is None or clause.start() < insert.end():
            # No INSERT before it: a constructed table name, or not an upsert.
            if insert is None and _UPSERT_RE.match(stmt, clause.start()):
                sites.append(UpsertSite(relpath, line, None, None))
            continue
        upsert = _UPSERT_RE.match(stmt, clause.start())
        if upsert is None:
            sites.append(UpsertSite(relpath, line, None, None))
        else:
            sites.append(
                UpsertSite(relpath, line, _insert_target(insert), _conflict_columns(upsert.group(1)))
            )
    return sites


def iter_upsert_sites(source: str, relpath: str) -> list[UpsertSite]:
    """``INSERT INTO t ... ON CONFLICT[(cols)] DO`` in backend SQL strings.

    String concatenation and f-strings are joined before matching, so a split
    INSERT / ON CONFLICT is still seen. An upsert whose INSERT table cannot be
    resolved statically is returned with ``table=None`` (reported, not skipped).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise DiscoveryError(
            f"{relpath}:{exc.lineno}: cannot parse backend source ({exc.msg}); "
            "upsert sites cannot be checked"
        ) from exc
    covered = _docstring_ids(tree)
    sites: list[UpsertSite] = []
    for node in ast.walk(tree):
        if id(node) in covered:
            continue
        text = _static_sql(node)
        if text is None:
            continue
        covered.update(id(child) for child in ast.walk(node))
        if "CONFLICT" not in text.upper():
            continue
        for stmt in _mask_sql(text).split(";"):
            sites.extend(_statement_upserts(stmt, relpath, getattr(node, "lineno", 1)))
    return sites


# ---------------------------------------------------------------------------
# Checks


def _registry_order_findings(base: MigrationInfo, head: MigrationInfo) -> list[Finding]:
    """Shipped entries stay in place unchanged; no target appears twice."""
    findings: list[Finding] = []
    seen: set[int] = set()
    for entry in head.entries:
        if entry.target in seen:
            findings.append(
                Finding(
                    "SCHEMA-GATE-002",
                    MIGRATIONS_RELPATH,
                    head.registry_line,
                    f"MIGRATIONS lists target_version={entry.target} more than once",
                    "keep exactly one Migration per target_version",
                )
            )
        seen.add(entry.target)
    for index, shipped in enumerate(base.entries):
        current = head.entries[index] if index < len(head.entries) else None
        if current != shipped:
            now = current.render() if current is not None else "nothing"
            findings.append(
                Finding(
                    "SCHEMA-GATE-002",
                    MIGRATIONS_RELPATH,
                    head.registry_line,
                    (
                        f"shipped {shipped.render()} at MIGRATIONS[{index}] was removed, "
                        f"reordered, renamed or re-pointed (now {now}); shipped "
                        "migrations are append-only"
                    ),
                    "restore the shipped entry in place and append a new migration instead",
                )
            )
    return findings


def _version_step_findings(base: MigrationInfo, head: MigrationInfo) -> list[Finding]:
    """One new Migration per bumped version, in order, and none without a bump."""
    if head.version < base.version:
        return [
            Finding(
                "SCHEMA-GATE-002",
                MIGRATIONS_RELPATH,
                head.version_line,
                f"LATEST_SCHEMA_VERSION went backwards ({base.version} -> {head.version})",
                "never lower the schema version; add a forward migration instead",
            )
        ]
    added = [e.target for e in head.entries[len(base.entries):]]
    expected = list(range(base.version + 1, head.version + 1))
    if added == expected:
        return []
    if not expected:
        message = (
            "MIGRATIONS gained "
            + ", ".join(f"target_version={v}" for v in added)
            + f" but LATEST_SCHEMA_VERSION is still {head.version}"
        )
        fix = "bump LATEST_SCHEMA_VERSION to the new migration's target_version"
    elif added == expected[: len(added)]:
        missing = ", ".join(f"Migration(target_version={v})" for v in expected[len(added):])
        message = (
            f"LATEST_SCHEMA_VERSION is {head.version} (base {base.version}) "
            f"but MIGRATIONS has no new {missing}"
        )
        fix = f"append {missing} (name=..., apply=...) to MIGRATIONS in {MIGRATIONS_RELPATH}"
    else:
        message = (
            f"new MIGRATIONS entries target {added} but bumping {base.version} -> "
            f"{head.version} needs exactly {expected}, appended in order"
        )
        fix = "append one Migration per bumped version, in order, after the shipped entries"
    return [Finding("SCHEMA-GATE-002", MIGRATIONS_RELPATH, head.registry_line, message, fix)]


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
    findings.extend(_registry_order_findings(base, head))
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


def _unresolved_upsert_finding(site: UpsertSite) -> Finding:
    return Finding(
        "SCHEMA-GATE-007",
        site.path,
        site.line,
        "ON CONFLICT upsert whose INSERT INTO table or conflict target cannot be resolved statically",
        (
            "keep INSERT INTO <literal table> and an ON CONFLICT(<plain columns>) clause in "
            "one string, concatenation or f-string so the PK registry check can see it"
        ),
    )


def _conflict_target_finding(
    site: UpsertSite, pk: tuple[str, ...], registered: bool
) -> Finding | None:
    """SCHEMA-GATE-005 when an explicit target is not exactly ``pk`` (order-free)."""
    target = site.target
    if target is None:
        return None
    if len(set(target)) == len(target) and set(target) == {c.lower() for c in pk}:
        return None
    source = "registered" if registered else f"canonical ({SCHEMA_RELPATH})"
    return Finding(
        "SCHEMA-GATE-005",
        site.path,
        site.line,
        f"ON CONFLICT{target!r} on {site.table} does not match its {source} primary key {pk!r}",
        "target the primary key, or change the key (and registry) with a migration",
    )


def _upsert_findings(
    schema_pks: dict[str, tuple[str, ...]],
    registry: dict[str, tuple[str, ...]],
    sites: list[UpsertSite],
    allow: dict[str, str],
) -> list[Finding]:
    """003/005/007 per upsert site. The allowlist waives only 003 (missing registry)."""
    findings: list[Finding] = []
    reported: set[str] = set(allow)
    for site in sorted(sites, key=lambda s: (s.path, s.line)):
        if site.table is None:
            findings.append(_unresolved_upsert_finding(site))
            continue
        if site.table not in schema_pks:
            continue  # derived/disposable index DBs are out of scope
        registered = site.table in registry
        if not registered and site.table not in reported:
            reported.add(site.table)
            findings.append(_missing_registry_finding(site, schema_pks[site.table]))
        pk = registry[site.table] if registered else schema_pks[site.table]
        target_finding = _conflict_target_finding(site, pk, registered)
        if target_finding is not None:
            findings.append(target_finding)
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
    allow = {
        k.lower(): v for k, v in (PK_REGISTRY_ALLOWLIST if allowlist is None else allowlist).items()
    }
    registry = (
        None if head.table_pks is None else {k.lower(): v for k, v in head.table_pks.items()}
    )
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
    upserted = {site.table for site in sites if site.table is not None and site.table in schema_pks}
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
