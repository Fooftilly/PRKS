"""Regression tests for scripts/check_correlated_catalog_counts.py (#192)."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "check_correlated_catalog_counts.py"
_SPEC = importlib.util.spec_from_file_location(
    "prks_check_correlated_catalog_counts", _SCRIPT
)
assert _SPEC and _SPEC.loader
checker = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = checker
_SPEC.loader.exec_module(checker)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


def _py(sql: str, name: str = "q") -> str:
    return f'{name} = """\n{sql}\n"""\n'


PLAYLIST_SQL = """
SELECT p.*,
    (SELECT COUNT(*) FROM playlist_items i WHERE i.playlist_id = p.id) AS item_count
FROM playlists p
ORDER BY p.updated_at DESC
"""

TAG_SQL = """
SELECT t.*,
    (SELECT COUNT(*) FROM work_tags wt WHERE wt.tag_id = t.id) AS work_count
FROM tags t
"""

SET_BASED_SQL = """
SELECT parent.*, COALESCE(counts.count, 0) AS child_count
FROM parent
LEFT JOIN (
    SELECT parent_id, COUNT(*) AS count
    FROM child
    GROUP BY parent_id
) counts ON counts.parent_id = parent.id
"""


def _hits(sql: str, path: str = "backend/x.py"):
    return checker.scan_source(path, _py(sql))


class DetectionTests(unittest.TestCase):
    def test_correlated_count_star_detected(self):
        hits = _hits(PLAYLIST_SQL)
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit.outer, "playlists p")
        self.assertEqual(hit.inner, "playlist_items")
        self.assertEqual(hit.output_alias, "item_count")
        self.assertEqual(hit.aggregate, "COUNT")

    def test_count_distinct_detected(self):
        sql = """
        SELECT w.id,
               (SELECT COUNT(DISTINCT r.person_id) FROM roles r WHERE r.work_id = w.id)
                   AS people
        FROM works w
        """
        hits = _hits(sql)
        self.assertEqual([h.output_alias for h in hits], ["people"])

    def test_case_whitespace_and_multiline_variants(self):
        variants = [
            "select p.*, (select count(*) from playlist_items i where i.playlist_id=p.id) as item_count from playlists p",
            "SeLeCt p.*,\n  (\n    SELECT\n      Count( * )\n    FROM playlist_items AS i\n    WHERE\n      i.playlist_id\n        = p.id\n  ) item_count\nFROM playlists AS p",
            "SELECT playlists.*, (SELECT COUNT(i.id) FROM playlist_items i WHERE i.playlist_id = playlists.id) AS item_count FROM playlists",
        ]
        for sql in variants:
            with self.subTest(sql=sql):
                hits = _hits(sql)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0].output_alias, "item_count")

    def test_whitespace_and_case_do_not_change_fingerprint(self):
        a = _hits(PLAYLIST_SQL)[0]
        b = _hits(PLAYLIST_SQL.replace("(SELECT COUNT(*)", "(\n  select   count( * )").lower())[0]
        self.assertEqual(a.fingerprint, b.fingerprint)

    def test_correlated_count_in_where_clause_detected(self):
        sql = """
        SELECT f.* FROM folders f
        WHERE NOT (f.parent_id IS NULL
                   AND (SELECT COUNT(*) FROM folder_files ff WHERE ff.folder_id = f.id) = 0)
        """
        self.assertEqual(len(_hits(sql)), 1)

    def test_aliased_inner_table_does_not_shadow_outer_table_name(self):
        sql = """
        SELECT id, (SELECT COUNT(*) FROM folders c WHERE c.parent_id = folders.id) AS child_count
        FROM folders WHERE parent_id = ?
        """
        hits = _hits(sql)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].outer, "folders")

    def test_nested_alias_reusing_outer_name_does_not_hide_correlation(self):
        sql = """
        SELECT p.*,
            (SELECT COUNT(*) FROM playlist_items i
             WHERE i.playlist_id = p.id
               AND EXISTS (SELECT 1 FROM works p WHERE p.id = i.work_id)) AS item_count
        FROM playlists p
        """
        hits = _hits(sql)
        self.assertEqual([h.output_alias for h in hits], ["item_count"])
        self.assertEqual(hits[0].inner, "playlist_items")

    def test_correlation_through_nested_subquery_detected(self):
        sql = """
        SELECT p.id,
            (SELECT COUNT(*) FROM works w
             WHERE w.id IN (SELECT i.work_id FROM playlist_items i WHERE i.playlist_id = p.id))
                AS n
        FROM playlists p
        """
        self.assertEqual([h.output_alias for h in _hits(sql)], ["n"])

    def test_nested_references_to_own_scopes_are_not_correlated(self):
        sql = """
        SELECT p.id,
            (SELECT COUNT(*) FROM works w
             WHERE w.id IN (SELECT p.work_id FROM playlist_items p WHERE p.kind = w.kind))
                AS n
        FROM playlists p
        """
        self.assertEqual(_hits(sql), [])

    def test_independent_counts_pass(self):
        for sql in (
            "SELECT COUNT(*) FROM pending_pdf_cleanup",
            "SELECT COUNT(*) FROM roles WHERE person_id = ?",
            "SELECT * FROM works WHERE id = ? AND (SELECT COUNT(*) FROM roles WHERE work_id = ?) > 0",
            "SELECT 1 FROM tags GROUP BY name HAVING COUNT(*) > 1 LIMIT 1",
            "SELECT t.* FROM tags t WHERE EXISTS (SELECT 1 FROM work_tags wt WHERE wt.tag_id = t.id)",
            "UPDATE playlists SET n = (SELECT COUNT(*) FROM playlist_items i WHERE i.playlist_id = playlists.id)",
        ):
            with self.subTest(sql=sql):
                self.assertEqual(_hits(sql), [])

    def test_set_based_replacement_passes(self):
        self.assertEqual(_hits(SET_BASED_SQL), [])

    def test_correlation_to_outer_derived_table_alias_detected(self):
        sql = """
        SELECT p.id,
            (SELECT COUNT(*) FROM child c WHERE c.parent_id = p.id) AS n
        FROM (SELECT id FROM parent WHERE visible = 1) AS p
        """
        hits = _hits(sql)
        self.assertEqual([h.outer for h in hits], ["(subquery) p"])

    def test_comma_separated_outer_relations_detected(self):
        for sql in (
            """
            SELECT p.id,
                (SELECT COUNT(*) FROM child c WHERE c.extra_id = x.id) AS n
            FROM parent p, extra x
            WHERE x.parent_id = p.id
            """,
            """
            SELECT p.id,
                (SELECT COUNT(*) FROM child c WHERE c.extra_id = x.id) AS n
            FROM parent p, (SELECT id, parent_id FROM extra) AS x
            WHERE x.parent_id = p.id
            """,
        ):
            with self.subTest(sql=sql):
                self.assertEqual([h.output_alias for h in _hits(sql)], ["n"])

    def test_select_list_subquery_alias_is_not_an_outer_relation(self):
        sql = """
        SELECT p.id, (SELECT MAX(v) FROM t) AS n,
            (SELECT COUNT(*) FROM child c WHERE c.parent_id = n.id) AS m
        FROM parent p
        """
        self.assertEqual(_hits(sql), [])

    def test_space_around_qualifier_dot_keeps_fingerprint(self):
        base = _hits(PLAYLIST_SQL)[0]
        spaced = PLAYLIST_SQL.replace("i.playlist_id = p.id", "i . playlist_id = p . id")
        self.assertEqual(_hits(spaced)[0].fingerprint, base.fingerprint)

    def test_helper_projection_fragment_detected(self):
        src = (
            "def member_count_sql(alias):\n"
            '    return f"(SELECT COUNT(*) FROM person_group_members m '
            'WHERE m.group_id = {alias}.id) AS member_count"\n'
            "\n"
            'FRAGMENT = "(SELECT COUNT(*) FROM work_tags wt WHERE wt.tag_id = t.id) AS n"\n'
        )
        hits = checker.scan_source("backend/x.py", src)
        self.assertEqual(
            [(h.outer, h.output_alias) for h in hits],
            [("fragment:__expr__", "member_count"), ("fragment:t", "n")],
        )

    def test_independent_fragment_passes(self):
        src = 'F = "(SELECT COUNT(*) FROM roles r WHERE r.person_id = ?) AS n"\n'
        self.assertEqual(checker.scan_source("backend/x.py", src), [])

    def test_sql_comments_do_not_change_fingerprint(self):
        base = _hits(PLAYLIST_SQL)[0]
        commented = PLAYLIST_SQL.replace(
            "WHERE i.playlist_id = p.id",
            "-- counts every item\n WHERE /* per playlist */ i.playlist_id = p.id",
        )
        self.assertEqual(_hits(commented)[0].fingerprint, base.fingerprint)

    def test_quoted_text_and_docstrings_ignored(self):
        src = (
            'def f():\n'
            '    """SELECT p.*, (SELECT COUNT(*) FROM i WHERE i.p = p.id) FROM playlists p"""\n'
            "    return \"SELECT 'x.(SELECT COUNT(*) FROM i WHERE i.p = p.id)' FROM playlists p\"\n"
        )
        self.assertEqual(checker.scan_source("backend/x.py", src), [])

    def test_fstring_and_implicit_concatenation(self):
        src = (
            "def q(order):\n"
            "    return (\n"
            '        "SELECT g.*, (SELECT COUNT(*) FROM person_group_members m "\n'
            '        "WHERE m.group_id = g.id) AS member_count "\n'
            '        f"FROM person_groups g ORDER BY {order}"\n'
            "    )\n"
        )
        hits = checker.scan_source("backend/x.py", src)
        self.assertEqual([h.output_alias for h in hits], ["member_count"])

    def test_line_number_points_at_subquery(self):
        src = "x = 1\n" + _py(PLAYLIST_SQL)
        (hit,) = checker.scan_source("backend/x.py", src)
        # line 2 opens the literal; PLAYLIST_SQL starts with a newline, so the
        # subquery is on the fourth line of the file.
        self.assertEqual(hit.line, 5)

    def test_reviewed_marker_exempts_only_that_subquery(self):
        sql = PLAYLIST_SQL.replace(
            "(SELECT COUNT(*) FROM",
            "(SELECT COUNT(*) -- prks-allow-correlated-count: diagnostics only\n FROM",
        )
        self.assertEqual(_hits(sql), [])
        no_reason = PLAYLIST_SQL.replace(
            "(SELECT COUNT(*) FROM",
            "(SELECT COUNT(*) -- prks-allow-correlated-count:\n FROM",
        )
        self.assertEqual(len(_hits(no_reason)), 1)
        block = PLAYLIST_SQL.replace(
            "(SELECT COUNT(*) FROM",
            "(SELECT COUNT(*) /* prks-allow-correlated-count: one-row diagnostics */ FROM",
        )
        self.assertEqual(_hits(block), [])

    def test_marker_text_outside_a_sql_comment_does_not_exempt(self):
        for sql in (
            PLAYLIST_SQL.replace(
                "WHERE i.playlist_id = p.id",
                "WHERE i.playlist_id = p.id AND i.kind = 'prks-allow-correlated-count: normal'",
            ),
            PLAYLIST_SQL.replace(
                "FROM playlists p",
                "FROM playlists p -- prks-allow-correlated-count: outside the subquery",
            ),
            PLAYLIST_SQL.replace(
                "WHERE i.playlist_id = p.id",
                "WHERE i.playlist_id = p.id AND EXISTS (SELECT 1 -- "
                "prks-allow-correlated-count: nested only\n FROM works w WHERE w.id = i.work_id)",
            ),
        ):
            with self.subTest(sql=sql):
                self.assertEqual(len(_hits(sql)), 1)


class RepoGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "prks-test@example.com")
        _git(self.repo, "config", "user.name", "PRKS Test")
        _git(self.repo, "checkout", "-q", "-b", "master")

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel: str, content: str) -> None:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def remove(self, rel: str) -> None:
        (self.repo / rel).unlink()

    def commit(self, message: str = "c") -> str:
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "--allow-empty", "-m", message)
        return _git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def allowlist_current(self, issue: int = 157) -> None:
        hits = checker.scan_working_tree(self.repo)
        entries = [
            {"issue": issue, **{k: getattr(h, k) for k in checker._ENTRY_KEYS}}
            for h in hits
        ]
        self.write(checker.ALLOWLIST_RELPATH, json.dumps({"entries": entries}))

    def run_gate(self, base: str = "HEAD") -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = checker.main(["--root", str(self.repo), f"--base={base}"])
        return rc, out.getvalue() + err.getvalue()

    def seed_debt(self) -> str:
        self.write("backend/db.py", _py(PLAYLIST_SQL, "PLAYLISTS"))
        self.write("backend/other.py", "x = 1\n")
        self.allowlist_current()
        return self.commit("seed")

    def test_new_correlated_count_fails_with_location_and_guidance(self):
        base = self.commit("empty")
        self.write("backend/db.py", "x = 1\n" + _py(PLAYLIST_SQL))
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 1)
        self.assertIn("SQL-CATALOG-001 backend/db.py:5:", out)
        self.assertIn("per-row catalogue work", out)
        self.assertIn("GROUP BY", out)

    def test_unchanged_allowlisted_debt_passes(self):
        base = self.seed_debt()
        self.write("backend/other.py", "x = 2\n")
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 0, out)

    def test_reformatted_allowlisted_debt_passes(self):
        base = self.seed_debt()
        self.write(
            "backend/db.py",
            "\n\n" + _py(PLAYLIST_SQL.replace("    (SELECT", "  (  select").lower(), "PLAYLISTS"),
        )
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 0, out)

    def test_second_violation_in_allowlisted_file_fails(self):
        base = self.seed_debt()
        self.write(
            "backend/db.py",
            _py(PLAYLIST_SQL, "PLAYLISTS") + _py(TAG_SQL, "TAGS"),
        )
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 1)
        self.assertIn("SQL-CATALOG-001 backend/db.py:", out)
        self.assertIn("work_tags", out)
        self.assertNotIn("SQL-CATALOG-002", out)

    def test_duplicate_of_allowlisted_query_fails(self):
        base = self.seed_debt()
        self.write(
            "backend/db.py",
            _py(PLAYLIST_SQL, "PLAYLISTS") + _py(PLAYLIST_SQL, "AGAIN"),
        )
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 1)
        self.assertEqual(out.count("SQL-CATALOG-001"), 1)

    def test_materially_rewritten_query_does_not_inherit_exemption(self):
        base = self.seed_debt()
        rewritten = PLAYLIST_SQL.replace(
            "WHERE i.playlist_id = p.id", "WHERE i.playlist_id = p.id AND i.hidden = 0"
        )
        self.write("backend/db.py", _py(rewritten, "PLAYLISTS"))
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 1)
        self.assertIn("SQL-CATALOG-001", out)
        self.assertIn("SQL-CATALOG-002", out)

    def test_reblessing_rewritten_debt_fails_provenance(self):
        base = self.seed_debt()
        rewritten = PLAYLIST_SQL.replace(
            "WHERE i.playlist_id = p.id", "WHERE i.playlist_id = p.id AND i.hidden = 0"
        )
        self.write("backend/db.py", _py(rewritten, "PLAYLISTS"))
        self.allowlist_current()
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 1)
        self.assertIn("SQL-CATALOG-003", out)
        self.assertNotIn("SQL-CATALOG-001", out)

    def test_new_query_with_new_allowlist_entry_fails_provenance(self):
        base = self.seed_debt()
        self.write("backend/other.py", _py(TAG_SQL, "TAGS"))
        self.allowlist_current()
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 1)
        self.assertEqual(out.count("SQL-CATALOG-003"), 1)

    def test_copying_grandfathered_debt_with_second_entry_fails_provenance(self):
        base = self.seed_debt()
        self.write("backend/other.py", _py(PLAYLIST_SQL, "PLAYLISTS"))
        self.allowlist_current()
        entries = json.loads((self.repo / checker.ALLOWLIST_RELPATH).read_text())["entries"]
        self.assertEqual(len(entries), 2)
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 1)
        self.assertEqual(out.count("SQL-CATALOG-003"), 1)
        self.assertNotIn("SQL-CATALOG-001", out)

    def test_duplicated_historical_debt_keeps_both_entries(self):
        self.write(
            "backend/db.py",
            _py(PLAYLIST_SQL, "PLAYLISTS") + _py(PLAYLIST_SQL, "AGAIN"),
        )
        self.allowlist_current()
        base = self.commit("seed with two identical historical copies")
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 0, out)

    def test_transplanting_subquery_to_other_outer_relation_fails_provenance(self):
        base = self.seed_debt()
        self.write(
            "backend/db.py",
            _py(PLAYLIST_SQL.replace("FROM playlists p", "FROM archived_playlists p"), "PLAYLISTS"),
        )
        self.allowlist_current()
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 1)
        self.assertEqual(out.count("SQL-CATALOG-003"), 1)

    def test_comment_only_edit_to_grandfathered_debt_passes(self):
        base = self.seed_debt()
        self.write(
            "backend/db.py",
            _py(
                PLAYLIST_SQL.replace(
                    "WHERE i.playlist_id = p.id",
                    "-- per playlist\n WHERE i.playlist_id = p.id",
                ),
                "PLAYLISTS",
            ),
        )
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 0, out)

    def test_moving_unchanged_debt_to_another_file_passes_with_updated_entry(self):
        base = self.seed_debt()
        self.remove("backend/db.py")
        self.write("backend/playlists.py", _py(PLAYLIST_SQL, "PLAYLISTS"))
        self.allowlist_current()
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 0, out)

    def test_fixed_query_with_leftover_entry_is_stale(self):
        base = self.seed_debt()
        self.write("backend/db.py", _py(SET_BASED_SQL, "PLAYLISTS"))
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 1)
        self.assertIn("SQL-CATALOG-002", out)
        self.assertIn("stale allowlist entry", out)

    def test_fixed_query_and_removed_entry_pass(self):
        base = self.seed_debt()
        self.write("backend/db.py", _py(SET_BASED_SQL, "PLAYLISTS"))
        self.write(checker.ALLOWLIST_RELPATH, json.dumps({"entries": []}))
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 0, out)

    def test_non_production_sql_is_out_of_scope(self):
        base = self.commit("empty")
        for rel in (
            "tests/test_x.py",
            "scripts/tool.py",
            "docs/example.py",
            "frontend/x.py",
        ):
            self.write(rel, _py(PLAYLIST_SQL))
        self.write("backend/notes.md", PLAYLIST_SQL)
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 0, out)
        self.write("prks_app.py", _py(PLAYLIST_SQL))
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 1)
        self.assertIn("prks_app.py:", out)

    def test_untracked_entry_requires_reason_and_is_reported(self):
        self.write("backend/db.py", _py(PLAYLIST_SQL, "PLAYLISTS"))
        (hit,) = checker.scan_working_tree(self.repo)
        entry = {k: getattr(hit, k) for k in checker._ENTRY_KEYS}
        self.write(
            checker.ALLOWLIST_RELPATH,
            json.dumps({"entries": [{"issue": None, **entry}]}),
        )
        base = self.commit("seed")
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 1)
        self.assertIn("SQL-CATALOG-004", out)
        self.write(
            checker.ALLOWLIST_RELPATH,
            json.dumps(
                {"entries": [{"issue": None, "untracked_reason": "owner pending", **entry}]}
            ),
        )
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 0, out)
        self.assertIn("UNTRACKED backend/db.py", out)

    def test_entry_without_issue_is_malformed(self):
        base = self.seed_debt()
        data = json.loads((self.repo / checker.ALLOWLIST_RELPATH).read_text())
        del data["entries"][0]["issue"]
        self.write(checker.ALLOWLIST_RELPATH, json.dumps(data))
        rc, out = self.run_gate(base)
        self.assertEqual(rc, 1)
        self.assertIn("SQL-CATALOG-004", out)

    def test_unavailable_or_malformed_base_fails_closed(self):
        self.seed_debt()
        for base in ("0" * 40, "no-such-branch", "--output=/tmp/x", "HEAD..master", "a b"):
            with self.subTest(base=base):
                rc, out = self.run_gate(base)
                self.assertEqual(rc, 2)
                self.assertIn("failed closed", out)

    def test_empty_tree_base_treats_all_debt_as_new(self):
        self.seed_debt()
        rc, out = self.run_gate(checker.EMPTY_TREE_SHA)
        self.assertEqual(rc, 1)
        self.assertIn("SQL-CATALOG-003", out)


class LiveCorpusTests(unittest.TestCase):
    def test_current_tree_passes_against_head(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = checker.main(["--root", str(_ROOT), "--base", "HEAD"])
        self.assertEqual(rc, 0, out.getvalue() + err.getvalue())

    def test_every_allowlist_entry_has_owner_or_untracked_reason(self):
        entries = checker.load_allowlist(_ROOT)
        self.assertTrue(entries)
        for idx, entry in enumerate(entries):
            with self.subTest(entry=entry):
                self.assertIsNone(checker._validate_entry(idx, entry))


if __name__ == "__main__":
    unittest.main()
