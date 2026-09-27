"""Regression tests for scripts/check_schema_change.py (#190)."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "check_schema_change.py"
_SPEC = importlib.util.spec_from_file_location("prks_check_schema_change", _SCRIPT)
assert _SPEC and _SPEC.loader
gate = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = gate
_SPEC.loader.exec_module(gate)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


BASE_SCHEMA = """\
-- Canonical schema (synthetic)
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS works (id TEXT PRIMARY KEY, title TEXT);
CREATE TABLE sync_entity_revisions (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    PRIMARY KEY (scope_type, scope_id)
);
"""


def _migrations(version: int, targets: list[tuple[int, str]], pks: str) -> str:
    entries = "".join(
        f"    Migration(target_version={v}, name={n!r}, apply=_noop),\n" for v, n in targets
    )
    return (
        "from typing import Dict, Tuple\n"
        f"LATEST_SCHEMA_VERSION = {version}\n"
        f"_CURRENT_TABLE_PKS: Dict[str, Tuple[str, ...]] = {pks}\n"
        "def _noop(conn):\n    pass\n"
        f"MIGRATIONS = (\n{entries})\n"
    )


BASE_PKS = '{"sync_entity_revisions": ("scope_type", "scope_id")}'
BASE_MIGRATIONS = _migrations(2, [(2, "first")], BASE_PKS)
BASE_SYNC = (
    "def bump(conn, scope_type, scope_id):\n"
    "    conn.execute(\n"
    '        """INSERT INTO sync_entity_revisions (scope_type, scope_id, revision)\n'
    "           VALUES (?, ?, 1) ON CONFLICT (scope_type, scope_id)\n"
    '           DO UPDATE SET revision = revision + 1""",\n'
    "        (scope_type, scope_id),\n"
    "    )\n"
)


class GateRepoTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        _git(self.repo, "init", "-q", "-b", "master")
        _git(self.repo, "config", "user.email", "prks-test@example.com")
        _git(self.repo, "config", "user.name", "PRKS Test")
        self.write(
            {
                "backend/db_schema.sql": BASE_SCHEMA,
                "backend/db_migrations.py": BASE_MIGRATIONS,
                "backend/sync.py": BASE_SYNC,
                "README.md": "base\n",
            }
        )
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "base")
        self.base = _git(self.repo, "rev-parse", "HEAD").strip()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, files: dict[str, str]) -> None:
        for rel, text in files.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def findings(self, allowlist: dict[str, str] | None = None) -> list:
        return gate.collect_findings(self.repo, self.base, allowlist=allowlist or {})

    def codes(self, allowlist: dict[str, str] | None = None) -> list[str]:
        return [f.code for f in self.findings(allowlist)]

    # 4. ordinary PRs are unaffected --------------------------------------

    def test_non_schema_change_passes(self):
        self.write({"README.md": "changed\n", "backend/other.py": "X = 1\n"})
        self.assertEqual(self.codes(), [])

    def test_schema_comment_and_whitespace_edits_need_no_migration(self):
        self.write(
            {"backend/db_schema.sql": BASE_SCHEMA.replace("(synthetic)", "(reworded)") + "\n\n"}
        )
        self.assertEqual(self.codes(), [])

    # 1. schema change without companions fails ---------------------------

    def test_schema_change_without_version_or_migration_fails(self):
        self.write(
            {"backend/db_schema.sql": BASE_SCHEMA.replace("title TEXT", "title TEXT, year INTEGER")}
        )
        findings = self.findings()
        self.assertEqual([f.code for f in findings], ["SCHEMA-GATE-001"])
        rendered = findings[0].render()
        self.assertIn("backend/db_schema.sql changed", rendered)
        self.assertIn("LATEST_SCHEMA_VERSION is still 2", rendered)
        self.assertIn("Migration(target_version=3", rendered)

    def test_version_bump_without_migration_fails(self):
        self.write(
            {
                "backend/db_schema.sql": BASE_SCHEMA.replace("title TEXT", "title TEXT, year INTEGER"),
                "backend/db_migrations.py": _migrations(3, [(2, "first")], BASE_PKS),
            }
        )
        findings = self.findings()
        self.assertEqual([f.code for f in findings], ["SCHEMA-GATE-002"])
        self.assertIn("no new Migration(target_version=3)", findings[0].message)

    def test_migration_without_version_bump_fails(self):
        self.write({"backend/db_migrations.py": _migrations(2, [(2, "first"), (3, "second")], BASE_PKS)})
        findings = self.findings()
        self.assertEqual([f.code for f in findings], ["SCHEMA-GATE-002"])
        self.assertIn("LATEST_SCHEMA_VERSION is still 2", findings[0].message)

    def test_removing_shipped_migration_fails(self):
        self.write({"backend/db_migrations.py": _migrations(3, [(3, "second")], BASE_PKS)})
        self.assertIn("removed, reordered, renamed or re-pointed", " ".join(f.message for f in self.findings()))

    # review follow-ups (Qodo on #265) ------------------------------------

    def test_whitespace_inside_quoted_default_is_a_schema_change(self):
        self.write({"backend/db_schema.sql": BASE_SCHEMA.replace("title TEXT", "title TEXT DEFAULT 'a  b'")})
        _git(self.repo, "commit", "-qam", "default")
        self.base = _git(self.repo, "rev-parse", "HEAD").strip()
        self.write({"backend/db_schema.sql": BASE_SCHEMA.replace("title TEXT", "title TEXT DEFAULT 'a b'")})
        self.assertEqual(self.codes(), ["SCHEMA-GATE-001"])

    def test_block_comments_are_not_schema_changes(self):
        self.write({"backend/db_schema.sql": "/* header */\n" + BASE_SCHEMA})
        self.assertEqual(self.codes(), [])

    def test_duplicate_migration_target_fails(self):
        self.write(
            {
                "backend/db_schema.sql": BASE_SCHEMA.replace("title TEXT", "title TEXT, year INTEGER"),
                "backend/db_migrations.py": _migrations(3, [(2, "first"), (3, "a"), (3, "b")], BASE_PKS),
            }
        )
        self.assertIn("target_version=3 more than once", " ".join(f.message for f in self.findings()))

    def test_reordered_shipped_migrations_fail(self):
        self.write({"backend/db_migrations.py": _migrations(3, [(2, "first"), (3, "second")], BASE_PKS)})
        _git(self.repo, "commit", "-qam", "v3")
        self.base = _git(self.repo, "rev-parse", "HEAD").strip()
        self.write({"backend/db_migrations.py": _migrations(3, [(3, "second"), (2, "first")], BASE_PKS)})
        self.assertIn("SCHEMA-GATE-002", self.codes())

    def test_repointed_shipped_migration_apply_fails(self):
        self.write(
            {"backend/db_migrations.py": BASE_MIGRATIONS.replace("apply=_noop", "apply=_other")}
        )
        findings = self.findings()
        self.assertEqual([f.code for f in findings], ["SCHEMA-GATE-002"])
        self.assertIn("apply=_other", findings[0].message)

    def test_positional_migration_arguments_are_understood(self):
        self.write(
            {
                "backend/db_schema.sql": BASE_SCHEMA.replace("title TEXT", "title TEXT, year INTEGER"),
                "backend/db_migrations.py": _migrations(3, [(2, "first")], BASE_PKS).replace(
                    ")\n", "    Migration(3, 'add_year', _noop),\n)\n"
                ),
            }
        )
        self.assertEqual(self.codes(), [])

    def test_split_concatenated_upsert_is_still_checked(self):
        self.write(
            {
                "backend/membership.py": (
                    "def f(conn, clause):\n"
                    "    conn.execute('INSERT INTO works (id) VALUES (?) ' + clause + "
                    "' ON CONFLICT DO NOTHING', ('x',))\n"
                )
            }
        )
        self.assertEqual(self.codes(), ["SCHEMA-GATE-003"])

    def test_unresolvable_upsert_table_fails(self):
        self.write(
            {
                "backend/dyn.py": (
                    "def f(conn, table):\n"
                    "    conn.execute(f'INSERT INTO {table} (id) VALUES (?) "
                    "ON CONFLICT(id) DO NOTHING', ('x',))\n"
                )
            }
        )
        findings = self.findings()
        self.assertEqual([f.code for f in findings], ["SCHEMA-GATE-007"])
        self.assertEqual((findings[0].path, findings[0].line), ("backend/dyn.py", 2))

    def test_docstrings_mentioning_upserts_are_ignored(self):
        self.write({"backend/doc.py": 'def f():\n    """Uses ``ON CONFLICT DO NOTHING``."""\n'})
        self.assertEqual(self.codes(), [])

    def test_repeated_conflict_target_column_fails(self):
        self.write(
            {
                "backend/sync.py": BASE_SYNC.replace(
                    "ON CONFLICT (scope_type, scope_id)", "ON CONFLICT (scope_type, scope_id, scope_id)"
                )
            }
        )
        self.assertEqual(self.codes(), ["SCHEMA-GATE-005"])

    # 3. complete change passes --------------------------------------------

    def test_complete_schema_migration_version_change_passes(self):
        self.write(
            {
                "backend/db_schema.sql": BASE_SCHEMA.replace("title TEXT", "title TEXT, year INTEGER"),
                "backend/db_migrations.py": _migrations(3, [(2, "first"), (3, "add_year")], BASE_PKS),
            }
        )
        self.assertEqual(self.codes(), [])

    def test_complete_new_load_bearing_table_passes(self):
        self.write(
            {
                "backend/db_schema.sql": BASE_SCHEMA
                + "CREATE TABLE pending_cleanup (filename TEXT PRIMARY KEY);\n",
                "backend/db_migrations.py": _migrations(
                    3,
                    [(2, "first"), (3, "pending_cleanup")],
                    '{"sync_entity_revisions": ("scope_type", "scope_id"),'
                    ' "pending_cleanup": ("filename",)}',
                ),
                "backend/cleanup.py": (
                    "SQL = ('INSERT INTO pending_cleanup (filename) VALUES (?) '\n"
                    "       'ON CONFLICT(filename) DO NOTHING')\n"
                ),
            }
        )
        self.assertEqual(self.codes(), [])

    # 2. missing PK registry entry fails -----------------------------------

    def test_load_bearing_table_missing_pk_registry_fails(self):
        self.write(
            {
                "backend/db_schema.sql": BASE_SCHEMA
                + "CREATE TABLE pending_cleanup (filename TEXT PRIMARY KEY);\n",
                "backend/db_migrations.py": _migrations(
                    3, [(2, "first"), (3, "pending_cleanup")], BASE_PKS
                ),
                "backend/cleanup.py": (
                    "SQL = ('INSERT INTO pending_cleanup (filename) VALUES (?) '\n"
                    "       'ON CONFLICT(filename) DO NOTHING')\n"
                ),
            }
        )
        findings = self.findings()
        self.assertEqual([f.code for f in findings], ["SCHEMA-GATE-003"])
        self.assertEqual(findings[0].path, "backend/cleanup.py")
        self.assertIn("'pending_cleanup' is missing from _CURRENT_TABLE_PKS", findings[0].message)
        self.assertIn("'pending_cleanup': ('filename',)", findings[0].fix)

    def test_registry_disagreeing_with_schema_pk_fails(self):
        self.write(
            {"backend/db_migrations.py": _migrations(2, [(2, "first")], '{"sync_entity_revisions": ("scope_id",)}')}
        )
        codes = self.codes()
        self.assertIn("SCHEMA-GATE-004", codes)
        self.assertIn("SCHEMA-GATE-005", codes)

    def test_derived_index_tables_are_out_of_scope(self):
        self.write(
            {
                "backend/text_index.py": (
                    "SQL = 'INSERT INTO text_index_meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value'\n"
                )
            }
        )
        self.assertEqual(self.codes(), [])

    # narrow allowlist -----------------------------------------------------

    def test_allowlist_is_per_table_and_must_not_go_stale(self):
        self.write(
            {
                "backend/membership.py": (
                    "SQL = 'INSERT INTO works (id) VALUES (?) ON CONFLICT DO NOTHING'\n"
                )
            }
        )
        self.assertEqual(self.codes(), ["SCHEMA-GATE-003"])
        self.assertEqual(self.codes({"works": "fixture"}), [])
        (self.repo / "backend/membership.py").unlink()
        stale = self.findings({"works": "fixture"})
        self.assertEqual([f.code for f in stale], ["SCHEMA-GATE-006"])
        self.assertIn("no longer written with ON CONFLICT", stale[0].message)

    def test_cli_exit_codes(self):
        def run(base: str) -> int:
            with mock.patch.object(gate, "PK_REGISTRY_ALLOWLIST", {}), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                return gate.main(["--root", str(self.repo), "--base", base])

        self.assertEqual(run(self.base), 0)
        self.write(
            {"backend/db_schema.sql": BASE_SCHEMA.replace("title TEXT", "title TEXT, year INTEGER")}
        )
        self.assertEqual(run(self.base), 1)
        self.assertEqual(run("HEAD..x"), 2)


class ParsingTests(unittest.TestCase):
    def test_upsert_sites_ignore_create_table_conflict_clauses(self):
        src = (
            "A = 'CREATE TABLE t (x TEXT UNIQUE ON CONFLICT REPLACE)'\n"
            "B = 'INSERT OR IGNORE INTO t (x) VALUES (?); "
            "INSERT INTO u (k) VALUES (?) ON CONFLICT(k) DO NOTHING'\n"
        )
        sites = gate.iter_upsert_sites(src, "backend/x.py")
        self.assertEqual([(s.table, s.target) for s in sites], [("u", ("k",))])


class CurrentRepoTests(unittest.TestCase):
    def test_current_tree_passes_against_head(self):
        head = _git(_ROOT, "rev-parse", "HEAD").strip()
        self.assertEqual(
            [f.render() for f in gate.collect_findings(_ROOT, head)], []
        )

    def test_live_registry_matches_runtime_module(self):
        from backend import db_migrations

        info = gate.parse_migrations_module(
            (_ROOT / gate.MIGRATIONS_RELPATH).read_text(encoding="utf-8"), "tree"
        )
        self.assertEqual(info.version, db_migrations.LATEST_SCHEMA_VERSION)
        self.assertEqual(info.table_pks, db_migrations._CURRENT_TABLE_PKS)
        self.assertEqual(
            info.targets, {m.target_version: m.name for m in db_migrations.MIGRATIONS}
        )


if __name__ == "__main__":
    unittest.main()
