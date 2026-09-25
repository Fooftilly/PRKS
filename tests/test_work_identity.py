"""#60 Slice A (schema 17): Work -> Manifestation -> Asset schema, backfill,
integrity layer and compatibility mirror. See docs/work-identity-model.md.
"""
import builtins
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tests.test_db_migrations import MigrationTestCase, _SCHEMA_PATH, _raw, _version
from tests.work_identity_fixtures import revert_to_v16_schema

from backend import argument_sync, work_identity, work_role_sync
from backend.db_manager import PRKSDatabase
from backend.db_migrations import (
    LATEST_SCHEMA_VERSION,
    MigrationError,
    _normalize_ddl,
    _v17_objects,
    application_schema_signature,
    apply_ordered_migrations,
)
from backend.storage.config import StorageConfig

YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _scope(*parts):
    return json.dumps(list(parts), ensure_ascii=True, separators=(",", ":"))


def _tx(path):
    """Autocommit connection for explicit BEGIN/COMMIT transition tests."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class WorkIdentityCase(MigrationTestCase):
    def _fresh(self):
        return self._open()

    def _v16(self, build=None, raw=None):
        """A real schema-16 library: built through the API, then reverted."""
        db = self._open()
        ids = build(db) if build else {}
        conn = _raw(self.storage.db_path)
        revert_to_v16_schema(conn)
        if raw:
            conn.execute("PRAGMA foreign_keys = OFF")
            raw(conn, ids)
            conn.commit()
        conn.close()
        self.assertEqual(_version(self.storage.db_path), 16)
        return ids

    def _q(self, sql, params=()):
        conn = _raw(self.storage.db_path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def _primary_mf(self, work_id):
        return self._q("SELECT primary_manifestation_id FROM works WHERE id = ?", (work_id,))[0][0]

    def _assert_clean(self):
        conn = _raw(self.storage.db_path)
        try:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(work_identity.integrity_violations(conn), [])
            self.assertEqual(work_identity.mirror_drift(conn), [])
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Schema parity and validation
# ---------------------------------------------------------------------------

class SchemaParityTests(WorkIdentityCase):
    def _object_sql(self, path):
        conn = _raw(path)
        try:
            out = {}
            for kind, name, _sql in _v17_objects():
                row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?", (kind, name)
                ).fetchone()
                out[(kind, name)] = _normalize_ddl(row[0])
            for name in ("idx_roles_person_work_role_unique", "idx_roles_work_id",
                         "idx_roles_person_id", "idx_annotations_work_id",
                         "idx_argument_sources_work_id"):
                row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?", (name,)
                ).fetchone()
                out[("index", name)] = _normalize_ddl(row[0])
            return out
        finally:
            conn.close()

    def test_fresh_and_upgraded_schema_are_identical(self):
        self._v16(lambda db: {"w": db.add_work(title="Upgrade me", file_path="/api/pdfs/u.pdf")})
        upgraded = self._open()
        self.assertEqual(_version(upgraded.db_path), LATEST_SCHEMA_VERSION)
        with upgraded.connection() as conn:
            upgraded_sig = application_schema_signature(conn)

        fresh_dir = tempfile.mkdtemp(prefix="prks-wi-fresh-")
        self.addCleanup(shutil.rmtree, fresh_dir, ignore_errors=True)
        fresh_storage = StorageConfig.for_testing(fresh_dir)
        os.makedirs(os.path.dirname(fresh_storage.db_path) or fresh_dir, exist_ok=True)
        fresh = PRKSDatabase(storage=fresh_storage, schema_path=_SCHEMA_PATH)
        with fresh.connection() as conn:
            fresh_sig = application_schema_signature(conn)
        self.assertEqual(upgraded_sig, fresh_sig)
        self.assertEqual(self._object_sql(upgraded.db_path), self._object_sql(fresh.db_path))
        for column in ("primary_manifestation_id", "citation_manifestation_id"):
            self.assertIn(column, upgraded_sig["columns"]["works"])
        self.assertIn("legacy_work_asset_mirror", fresh_sig["views"])

    def test_every_slice_a_object_is_validated_by_definition(self):
        cases = {
            # a missing trigger
            "works_mirror_ai": "DROP TRIGGER works_mirror_ai",
            # the retirement guard FK no longer deferred
            "work_retirement": (
                "DROP TABLE work_retirement; CREATE TABLE work_retirement ("
                "work_id TEXT PRIMARY KEY, must_clear INTEGER NOT NULL DEFAULT 1 "
                "REFERENCES work_retirement_guard(id))"
            ),
            # a guard that could hold a row
            "work_retirement_guard": (
                "DROP TABLE work_retirement_guard; "
                "CREATE TABLE work_retirement_guard (id INTEGER PRIMARY KEY)"
            ),
        }
        for obj, ddl in cases.items():
            with self.subTest(obj=obj):
                db = self._open()
                conn = _raw(db.db_path)
                conn.execute("PRAGMA foreign_keys = OFF")
                conn.executescript(ddl)
                conn.close()
                with self.assertRaises(MigrationError) as ctx:
                    self._open()
                self.assertEqual(ctx.exception.code, "schema_drift")
                self.assertEqual(ctx.exception.details.get("object"), obj)
                shutil.rmtree(self._tmpdir, ignore_errors=True)
                self.setUp()


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

def _build_fixture_library(db):
    ids = {}
    ids["pdf"] = db.add_work(title="PDF", file_path="/api/pdfs/paper.pdf", source_kind="pdf",
                             source_url="https://example.org/paper", doi="10.1/pdf")
    ids["pdf_mime"] = db.add_work(title="PDF mime", file_path="/api/pdfs/typed.pdf",
                                  source_mime="application/x-pdf")
    ids["shared_a"] = db.add_work(title="Shared A", file_path="/api/pdfs/shared.pdf")
    ids["shared_b"] = db.add_work(title="Shared B", file_path="/api/pdfs/shared.pdf")
    ids["video"] = db.add_work(title="Video", source_kind="video", source_url=YT,
                               thumb_url="https://i.ytimg.com/vi/x/hq.jpg")
    ids["no_file"] = db.add_work(title="No file", source_kind="pdf", doi="10.1/nofile")
    ids["meta_mime"] = db.add_work(title="Meta mime", source_mime="text/plain")
    ids["meta_thumb_url"] = db.add_work(title="Meta thumb", thumb_url="https://example.org/t.png")
    ids["meta_thumb_page"] = db.add_work(title="Meta page", thumb_page=4)
    ids["notes_only"] = db.add_work(title="Notes", text_content="Research note body")
    for key in ("tomb_thumb", "tomb_ann", "tomb_source", "inferred_video",
                "inferred_bad", "ann_no_file"):
        ids[key] = db.add_work(title=key)
    return ids


def _raw_fixture_states(conn, ids):
    conn.execute("UPDATE works SET canonical_annotation_set_revision = 3, "
                 "materialized_pdf_annotation_revision = 2 WHERE id = ?", (ids["pdf"],))
    conn.execute("INSERT INTO annotations (id, work_id, type, page_index) VALUES "
                 "('ann-pdf', ?, 'highlight', 0)", (ids["pdf"],))
    conn.execute("INSERT INTO annotations (id, work_id, type, page_index) VALUES "
                 "('ann-nofile', ?, 'note', 1)", (ids["ann_no_file"],))
    conn.execute("INSERT INTO sync_entity_revisions (scope_type, scope_id, revision) VALUES "
                 "('work-field', ?, 2)", (_scope(ids["tomb_thumb"], "thumb_page"),))
    conn.execute("INSERT INTO sync_entity_revisions (scope_type, scope_id, revision) VALUES "
                 "('pdf-annotation', ?, 1)", (_scope(ids["tomb_ann"], "ann-gone"),))
    conn.execute("INSERT INTO sync_entity_revisions (scope_type, scope_id, revision) VALUES "
                 "('work-source', ?, 1)", (_scope(ids["tomb_source"]),))
    conn.execute("UPDATE works SET source_kind = NULL, source_url = ? WHERE id = ?",
                 (YT, ids["inferred_video"]))
    conn.execute("UPDATE works SET source_kind = NULL, source_url = ? WHERE id = ?",
                 ("https://example.org/not-a-video", ids["inferred_bad"]))
    # A non-thumb field revision on a notes-only Work is not Asset-bound.
    conn.execute("INSERT INTO sync_entity_revisions (scope_type, scope_id, revision) VALUES "
                 "('work-field', ?, 5)", (_scope(ids["notes_only"], "doi"),))


class BackfillTests(WorkIdentityCase):
    def setUp(self):
        super().setUp()
        self.ids = self._v16(_build_fixture_library, _raw_fixture_states)
        self.db = self._open()

    def _asset(self, key):
        rows = self._q("SELECT * FROM assets WHERE origin_work_id = ?", (self.ids[key],))
        return rows[0] if rows else None

    def _asset_row(self, key):
        conn = _raw(self.storage.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM assets WHERE origin_work_id = ?",
                               (self.ids[key],)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def test_every_work_gets_exactly_one_deterministic_primary_manifestation(self):
        for key, work_id in self.ids.items():
            with self.subTest(key=key):
                rows = self._q("SELECT id, work_id, kind, title, abstract FROM manifestations "
                               "WHERE origin_work_id = ?", (work_id,))
                self.assertEqual(len(rows), 1)
                mf_id = work_identity.backfill_manifestation_id(work_id)
                self.assertEqual(rows[0], (mf_id, work_id, "unspecified", None, None))
                self.assertEqual(self._primary_mf(work_id), mf_id)
                self.assertIsNone(self._q(
                    "SELECT citation_manifestation_id FROM works WHERE id = ?", (work_id,))[0][0])
        self.assertEqual(self._q("SELECT COUNT(*) FROM manifestations")[0][0], len(self.ids))

    def test_asset_creation_predicate(self):
        with_asset = {"pdf", "pdf_mime", "shared_a", "shared_b", "video", "meta_mime",
                      "meta_thumb_url", "meta_thumb_page", "tomb_thumb", "tomb_ann",
                      "tomb_source", "inferred_video", "ann_no_file"}
        for key, work_id in self.ids.items():
            with self.subTest(key=key):
                rows = self._q("SELECT id FROM assets WHERE work_id = ?", (work_id,))
                if key in with_asset:
                    self.assertEqual(rows, [(work_identity.backfill_asset_id(work_id),)])
                    self.assertEqual(
                        self._q("SELECT primary_asset_id FROM manifestations WHERE id = ?",
                                (self._primary_mf(work_id),))[0][0],
                        rows[0][0])
                else:
                    # no_file, notes_only, inferred_bad: metadata only, no fake File.
                    self.assertEqual(rows, [])
        self._assert_clean()

    def test_asset_values_are_preserved(self):
        pdf = self._asset_row("pdf")
        self.assertEqual((pdf["kind"], pdf["role"], pdf["storage_locator"], pdf["source_locator"]),
                         ("managed_file", "document", "paper.pdf", None))
        self.assertEqual(pdf["media_type"], "application/pdf")
        self.assertEqual((pdf["canonical_annotation_set_revision"],
                          pdf["materialized_pdf_annotation_revision"]), (3, 2))
        self.assertEqual((pdf["origin"], pdf["state"], pdf["content_generation"]),
                         ("legacy", "active", 0))
        for column in ("ingest_sha256", "content_sha256", "byte_size", "fingerprinted_at"):
            self.assertIsNone(pdf[column])
        # MIME: a stored value survives exactly; application/pdf is only inferred.
        self.assertEqual(self._asset_row("pdf_mime")["media_type"], "application/x-pdf")
        self.assertEqual(self._asset_row("meta_mime")["media_type"], "text/plain")
        self.assertIsNone(self._asset_row("meta_mime")["storage_locator"])
        self.assertIsNone(self._asset_row("meta_thumb_url")["media_type"])
        self.assertEqual(self._asset_row("meta_thumb_page")["thumb_page"], 4)
        self.assertEqual(self._asset_row("meta_thumb_url")["thumb_url"],
                         "https://example.org/t.png")
        # Shared bytes stay shared: two Assets, one locator.
        self.assertEqual(self._asset_row("shared_a")["storage_locator"], "shared.pdf")
        self.assertEqual(self._asset_row("shared_b")["storage_locator"], "shared.pdf")
        # Video identity moves whole into an external_stream Asset.
        video = self._asset_row("video")
        self.assertEqual((video["kind"], video["provider"], video["provider_id"], video["url"]),
                         ("external_stream", "youtube", "dQw4w9WgXcQ", YT))
        self.assertIsNone(video["media_type"])
        inferred = self._asset_row("inferred_video")
        self.assertEqual((inferred["kind"], inferred["url"]), ("external_stream", YT))
        # Tombstone-only Works get a placeholder managed_file with nothing set.
        tomb = self._asset_row("tomb_thumb")
        self.assertEqual((tomb["kind"], tomb["storage_locator"], tomb["thumb_page"]),
                         ("managed_file", None, None))

    def test_manifestation_fields_are_copied_and_url_follows_its_owner(self):
        conn = _raw(self.storage.db_path)
        try:
            def mf(key):
                return conn.execute("SELECT doi, url, doc_type FROM manifestations "
                                    "WHERE origin_work_id = ?", (self.ids[key],)).fetchone()
            self.assertEqual(mf("pdf")[:2], ("10.1/pdf", "https://example.org/paper"))
            self.assertEqual(mf("no_file")[0], "10.1/nofile")
            # A video's URL is Asset identity, not a citation URL.
            self.assertIsNone(mf("video")[1])
            self.assertIsNone(mf("inferred_video")[1])
            # An unparseable inferred-video URL stays on the Manifestation.
            self.assertEqual(mf("inferred_bad")[1], "https://example.org/not-a-video")
            self.assertEqual(
                conn.execute("SELECT doc_type FROM works WHERE id = ?",
                             (self.ids["pdf"],)).fetchone()[0],
                mf("pdf")[2])
        finally:
            conn.close()

    def test_annotations_are_bound_to_the_origin_asset(self):
        rows = self._q("SELECT id, work_id, asset_id FROM annotations ORDER BY id")
        self.assertEqual(rows, [
            ("ann-nofile", self.ids["ann_no_file"],
             work_identity.backfill_asset_id(self.ids["ann_no_file"])),
            ("ann-pdf", self.ids["pdf"], work_identity.backfill_asset_id(self.ids["pdf"])),
        ])

    def test_work_shape_is_unchanged(self):
        work = self.db.get_work(self.ids["pdf"])
        for column in work_identity.WORK_POINTER_COLUMNS:
            self.assertNotIn(column, work)
        self.assertEqual(work["file_path"], "/api/pdfs/paper.pdf")


class DeterminismTests(WorkIdentityCase):
    def _dump(self, path):
        conn = _raw(path)
        try:
            return {
                table: conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in ("manifestations", "assets", "annotations", "argument_sources",
                              "roles", "migration_quarantine")
            } | {"pointers": conn.execute(
                "SELECT id, primary_manifestation_id FROM works ORDER BY id").fetchall()}
        finally:
            conn.close()

    def test_same_v16_library_always_migrates_to_the_same_ids(self):
        self._v16(_build_fixture_library, _raw_fixture_states)
        pristine = os.path.join(self._tmpdir, "pristine-v16.db")
        shutil.copyfile(self.storage.db_path, pristine)
        self._open()
        first = self._dump(self.storage.db_path)
        # Restore the same pre-migration backup and migrate again.
        os.replace(pristine, self.storage.db_path)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(self.storage.db_path + suffix):
                os.remove(self.storage.db_path + suffix)
        self._open()
        second = self._dump(self.storage.db_path)
        self.assertEqual(first, second)

    def test_backfill_id_formula(self):
        self.assertEqual(str(work_identity.NS_PRKS_BACKFILL), "6d26588b-55c0-5737-95b2-bd0bdd2827e4")
        import uuid
        expected = "MF-" + uuid.uuid5(work_identity.NS_PRKS_BACKFILL, "manifestation:W-1").hex.upper()
        self.assertEqual(work_identity.backfill_manifestation_id("W-1"), expected)
        expected = "AS-" + uuid.uuid5(work_identity.NS_PRKS_BACKFILL, "asset:W-1").hex.upper()
        self.assertEqual(work_identity.backfill_asset_id("W-1"), expected)
        from backend.entity_ids import is_distributed
        self.assertTrue(is_distributed(work_identity.backfill_manifestation_id("W-1"), "MF"))
        self.assertTrue(is_distributed(work_identity.backfill_asset_id("W-1"), "AS"))

    def test_failed_migration_rolls_back_to_v16_untouched(self):
        self._v16(_build_fixture_library, _raw_fixture_states)
        before = self._q("SELECT * FROM works ORDER BY id")
        with patch.object(work_identity, "integrity_violations",
                          return_value=[("INJECTED", "x")]):
            with self.assertRaises(MigrationError):
                self._open()
        self.assertEqual(_version(self.storage.db_path), 16)
        self.assertEqual(self._q("SELECT * FROM works ORDER BY id"), before)
        self.assertEqual(self._q("SELECT name FROM sqlite_master WHERE name IN "
                                 "('manifestations', 'assets', 'migration_quarantine')"), [])
        # A retry after the crash converges on the same deterministic result.
        self._open()
        self._assert_clean()


# ---------------------------------------------------------------------------
# argument_sources rebuild and quarantine
# ---------------------------------------------------------------------------

class ArgumentSourcesMigrationTests(WorkIdentityCase):
    def _current(self, argument_id):
        conn = _raw(self.storage.db_path)
        try:
            return (argument_sync.current_sources(conn, argument_id),
                    argument_sync.get_sources_revision(conn, argument_id))
        finally:
            conn.close()

    def test_normal_list_keeps_order_revision_and_pins_pages(self):
        def build(db):
            w = [db.add_work(title=f"S{i}") for i in range(3)]
            from backend import research_network
            arg = research_network.create_argument(db, **{
                "name": "A", "kind": "argument",
                "sources": [{"work_id": w[2], "pages": "45"},
                            {"work_id": w[0], "pages": ""},
                            {"work_id": w[1], "pages": "9-10"}]})
            aid = arg["id"]
            research_network.replace_argument_sources(db, aid, [
                {"work_id": w[1], "pages": "9-10"}, {"work_id": w[2], "pages": "45"},
                {"work_id": w[0], "pages": ""}])
            return {"w": w, "a": aid}
        ids = self._v16(build)
        before = self._current(ids["a"])
        self._open()
        self.assertEqual(self._current(ids["a"]), before)
        w = ids["w"]
        self.assertEqual(
            self._q("SELECT order_index, work_id, manifestation_id, pages FROM argument_sources "
                    "WHERE argument_id = ? ORDER BY order_index", (ids["a"],)),
            [(0, w[1], work_identity.backfill_manifestation_id(w[1]), "9-10"),
             (1, w[2], work_identity.backfill_manifestation_id(w[2]), "45"),
             (2, w[0], None, "")])
        self._assert_clean()

    def test_equal_order_index_rows_are_renumbered_in_canonical_order(self):
        def build(db):
            w = sorted(db.add_work(title=f"T{i}") for i in range(3))
            from backend import research_network
            aid = research_network.create_argument(db, **{"name": "Legacy", "kind": "argument"})["id"]
            return {"w": w, "a": aid}

        def raw(conn, ids):
            for wid in reversed(ids["w"]):
                conn.execute("INSERT INTO argument_sources (argument_id, work_id, pages, "
                             "order_index) VALUES (?, ?, '', 0)", (ids["a"], wid))
            conn.execute("INSERT INTO sync_entity_revisions (scope_type, scope_id, revision) "
                         "VALUES ('argument-sources', ?, 7)", (ids["a"],))
        ids = self._v16(build, raw)
        before = self._current(ids["a"])
        self.assertEqual([s["work_id"] for s in before[0]], ids["w"])
        self._open()
        self.assertEqual(self._current(ids["a"]), before)
        self.assertEqual(before[1], 7)
        self.assertEqual(
            self._q("SELECT order_index, work_id FROM argument_sources WHERE argument_id = ? "
                    "ORDER BY order_index", (ids["a"],)),
            list(enumerate(ids["w"])))

    def test_orphan_citation_is_quarantined_and_advances_the_revision(self):
        def build(db):
            w = db.add_work(title="Kept")
            from backend import research_network
            aid = research_network.create_argument(db, **{"name": "A", "kind": "argument"})["id"]
            gone = research_network.create_argument(db, **{"name": "Gone", "kind": "argument"})["id"]
            return {"w": w, "a": aid, "gone": gone}

        def raw(conn, ids):
            conn.execute("INSERT INTO argument_sources (argument_id, work_id, pages, order_index, "
                         "created_at) VALUES (?, 'W-MISSING', 'p. 3', 0, '2020-01-01 00:00:00')",
                         (ids["a"],))
            conn.execute("INSERT INTO argument_sources (argument_id, work_id, pages, order_index) "
                         "VALUES (?, ?, '12', 1)", (ids["a"], ids["w"]))
            conn.execute("INSERT INTO sync_entity_revisions (scope_type, scope_id, revision) "
                         "VALUES ('argument-sources', ?, 4)", (ids["a"],))
            # An Argument that no longer exists: quarantined, no scope invented.
            conn.execute("DELETE FROM arguments WHERE id = ?", (ids["gone"],))
            conn.execute("INSERT INTO argument_sources (argument_id, work_id, pages, order_index) "
                         "VALUES (?, ?, '', 0)", (ids["gone"], ids["w"]))
        ids = self._v16(build, raw)
        self._open()
        sources, revision = self._current(ids["a"])
        self.assertEqual(sources, [{"work_id": ids["w"], "pages": "12"}])
        self.assertEqual(revision, 5)
        self.assertEqual(
            self._q("SELECT order_index FROM argument_sources WHERE argument_id = ?", (ids["a"],)),
            [(0,)])
        q = self._q("SELECT source_table, row_json, reason FROM migration_quarantine "
                    "WHERE source_table = 'argument_sources' ORDER BY id")
        self.assertEqual(len(q), 2)
        self.assertEqual(json.loads(q[0][1]), {
            "argument_id": ids["a"], "work_id": "W-MISSING", "pages": "p. 3",
            "order_index": 0, "created_at": "2020-01-01 00:00:00"})
        self.assertEqual(q[0][2], "missing_parent:works")
        self.assertEqual(q[1][2], "missing_parent:arguments")
        self.assertEqual(self._q("SELECT COUNT(*) FROM sync_entity_revisions WHERE "
                                 "scope_type = 'argument-sources' AND scope_id = ?",
                                 (ids["gone"],))[0][0], 0)
        self._assert_clean()


# ---------------------------------------------------------------------------
# Role and annotation quarantine
# ---------------------------------------------------------------------------

class RoleQuarantineTests(WorkIdentityCase):
    def _state(self, db, work_id, person_id, role):
        state = work_role_sync.get_roles_state(db, work_id)
        return [s for s in state["scopes"] if (s["person_id"], s["role_type"]) == (person_id, role)]

    def test_live_work_missing_person_becomes_a_newer_tombstone(self):
        def build(db):
            return {"w": db.add_work(title="Live"), "w2": db.add_work(title="Live 2")}

        def raw(conn, ids):
            conn.execute("INSERT INTO roles (person_id, work_id, role_type, order_index) "
                         "VALUES ('P-GHOST', ?, 'Author', 0)", (ids["w"],))
            conn.execute("INSERT INTO roles (person_id, work_id, role_type, order_index, "
                         "credit_name) VALUES ('P-GHOST2', ?, 'Editor', 1, 'Ed.')", (ids["w2"],))
            conn.execute("INSERT INTO sync_entity_revisions (scope_type, scope_id, revision) "
                         "VALUES ('work-person-role', ?, 3)",
                         (work_role_sync.scope_key(ids["w2"], "P-GHOST2", "Editor"),))
            # Missing Work and Person: no live scope reported it.
            conn.execute("INSERT INTO roles (person_id, work_id, role_type, order_index) "
                         "VALUES ('P-NONE', 'W-NONE', 'Author', 0)")
            conn.execute("INSERT INTO annotations (id, work_id, content) "
                         "VALUES ('ann-orphan', 'W-NONE', 'kept verbatim')")
        ids = self._v16(build, raw)
        pre = self._v16_role_state(ids)
        self.assertEqual(pre, [{"person_id": "P-GHOST", "role_type": "Author",
                                "revision": 0, "present": True}])
        db = self._open()
        self.assertEqual(self._state(db, ids["w"], "P-GHOST", "Author"),
                         [{"person_id": "P-GHOST", "role_type": "Author",
                           "revision": 1, "present": False}])
        self.assertEqual(self._state(db, ids["w2"], "P-GHOST2", "Editor"),
                         [{"person_id": "P-GHOST2", "role_type": "Editor",
                           "revision": 4, "present": False}])
        self.assertEqual(self._q("SELECT COUNT(*) FROM sync_entity_revisions WHERE scope_id = ?",
                                 (work_role_sync.scope_key("W-NONE", "P-NONE", "Author"),))[0][0], 0)
        self.assertEqual(self._q("SELECT COUNT(*) FROM roles")[0][0], 0)
        rows = {t: json.loads(j) for t, j in self._q(
            "SELECT source_table || ':' || json_extract(row_json, '$.work_id') || ':' || "
            "COALESCE(json_extract(row_json, '$.person_id'), ''), row_json "
            "FROM migration_quarantine")}
        self.assertEqual(rows["annotations:W-NONE:"]["content"], "kept verbatim")
        self.assertEqual(rows[f"roles:{ids['w2']}:P-GHOST2"]["credit_name"], "Ed.")
        self.assertEqual(len(rows), 4)
        self._assert_clean()

    def _v16_role_state(self, ids):
        conn = _raw(self.storage.db_path)
        try:
            present = conn.execute("SELECT person_id, role_type FROM roles WHERE work_id = ?",
                                   (ids["w"],)).fetchall()
            return [{"person_id": p, "role_type": r, "revision": 0, "present": True}
                    for p, r in present]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Integrity layer: ownership, pointers, relations, transitions
# ---------------------------------------------------------------------------

class IntegrityCase(WorkIdentityCase):
    def setUp(self):
        super().setUp()
        self.db = self._open()
        self.w1 = self.db.add_work(title="One", file_path="/api/pdfs/one.pdf")
        self.w2 = self.db.add_work(title="Two", file_path="/api/pdfs/two.pdf")
        self.mf1 = self._primary_mf(self.w1)
        self.mf2 = self._primary_mf(self.w2)
        self.as1 = self._q("SELECT id FROM assets WHERE manifestation_id = ?", (self.mf1,))[0][0]
        self.as2 = self._q("SELECT id FROM assets WHERE manifestation_id = ?", (self.mf2,))[0][0]
        self.conn = _tx(self.storage.db_path)
        self.addCleanup(self.conn.close)

    def refused(self, sql, params=(), code=None):
        with self.assertRaises(sqlite3.DatabaseError) as ctx:
            self.conn.execute(sql, params)
        if code:
            self.assertIn(code, str(ctx.exception))
        return ctx.exception

    def commit_refused(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("COMMIT")
        self.conn.execute("ROLLBACK")

    def snapshot(self):
        return {t: self.conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
                for t in ("works", "manifestations", "assets", "annotations", "roles",
                          "argument_sources", "manifestation_relations", "work_retirement")}

    def add_mf(self, work_id, mf_id):
        self.conn.execute("INSERT INTO manifestations (id, work_id) VALUES (?, ?)", (mf_id, work_id))


class PointerOwnershipTests(IntegrityCase):
    def test_primary_and_citation_must_belong_to_the_work(self):
        self.refused("UPDATE works SET primary_manifestation_id = ? WHERE id = ?",
                     (self.mf2, self.w1), "MANIFESTATION_OWNER_MISMATCH")
        self.refused("UPDATE works SET citation_manifestation_id = ? WHERE id = ?",
                     (self.mf2, self.w1), "MANIFESTATION_OWNER_MISMATCH")
        self.refused("UPDATE works SET primary_manifestation_id = 'MF-NOPE' WHERE id = ?",
                     (self.w1,), "MANIFESTATION_OWNER_MISMATCH")
        self.conn.execute("UPDATE works SET citation_manifestation_id = ? WHERE id = ?",
                          (self.mf1, self.w1))
        self.conn.execute("UPDATE works SET citation_manifestation_id = NULL WHERE id = ?",
                          (self.w1,))

    def test_primary_cannot_be_cleared_and_pointers_start_null(self):
        self.refused("UPDATE works SET primary_manifestation_id = NULL WHERE id = ?",
                     (self.w1,), "WORK_PRIMARY_MANIFESTATION_REQUIRED")
        self.refused("INSERT INTO works (id, title, primary_manifestation_id) VALUES "
                     "('W-X', 'x', ?)", (self.mf1,), "WORK_POINTERS_START_NULL")

    def test_pointer_target_cannot_be_deleted_or_moved_away(self):
        self.refused("DELETE FROM manifestations WHERE id = ?", (self.mf1,),
                     "MANIFESTATION_IS_POINTER_TARGET")
        self.refused("UPDATE manifestations SET work_id = ? WHERE id = ?", (self.w2, self.mf1),
                     "MANIFESTATION_IS_POINTER_TARGET")
        self.refused("UPDATE manifestations SET id = 'MF-RENAMED' WHERE id = ?", (self.mf1,),
                     "MANIFESTATION_IS_POINTER_TARGET")

    def test_primary_asset_must_belong_to_the_manifestation(self):
        self.conn.execute("BEGIN")
        self.conn.execute("UPDATE manifestations SET primary_asset_id = ? WHERE id = ?",
                          (self.as2, self.mf1))
        self.commit_refused()

    def test_origin_mapping_is_immutable(self):
        self.refused("UPDATE manifestations SET origin_work_id = 'W-OTHER' WHERE id = ?",
                     (self.mf1,), "ORIGIN_WORK_IMMUTABLE")
        self.refused("UPDATE assets SET origin_work_id = NULL WHERE id = ?", (self.as1,),
                     "ORIGIN_WORK_IMMUTABLE")

    def test_mirrored_columns_are_read_only(self):
        self.refused("UPDATE manifestations SET doi = '10.9/forged' WHERE id = ?", (self.mf1,),
                     "MIRRORED_FIELD_READ_ONLY")
        self.refused("UPDATE assets SET storage_locator = 'other.pdf' WHERE id = ?", (self.as1,),
                     "MIRRORED_FIELD_READ_ONLY")
        # Non-mirrored Asset state stays writable for later slices (Slice B).
        self.conn.execute("UPDATE assets SET content_sha256 = 'x', content_generation = 1 "
                          "WHERE id = ?", (self.as1,))


class LeafOwnershipTests(IntegrityCase):
    def test_roles_argument_sources_and_annotations_must_match_their_owner(self):
        person = self.db.add_person("Ada", "Lovelace")
        self.refused("INSERT INTO roles (person_id, work_id, role_type, manifestation_id) "
                     "VALUES (?, ?, 'Translator', ?)", (person, self.w1, self.mf2),
                     "FOREIGN KEY")
        self.conn.execute("INSERT INTO roles (person_id, work_id, role_type, manifestation_id) "
                          "VALUES (?, ?, 'Translator', ?)", (person, self.w1, self.mf1))
        self.conn.execute("INSERT INTO arguments (id, name, kind) VALUES ('A-1', 'A', 'argument')")
        self.refused("INSERT INTO argument_sources (argument_id, order_index, work_id, "
                     "manifestation_id, pages) VALUES ('A-1', 0, ?, ?, '4')",
                     (self.w1, self.mf2), "FOREIGN KEY")
        self.refused("INSERT INTO annotations (id, work_id, asset_id) VALUES ('ann-x', ?, ?)",
                     (self.w1, self.as2), "FOREIGN KEY")
        self.refused("UPDATE assets SET work_id = ? WHERE id = ?", (self.w2, self.as1),
                     "FOREIGN KEY")


class RelationTests(IntegrityCase):
    def setUp(self):
        super().setUp()
        self.add_mf(self.w1, "MF-B1")
        self.add_mf(self.w2, "MF-B2")

    def test_cross_work_and_self_relations_are_refused(self):
        self.conn.execute("BEGIN")
        self.conn.execute("INSERT INTO manifestation_relations (work_id, from_id, to_id, relation) "
                          "VALUES (?, ?, 'MF-B2', 'translation_of')", (self.w1, self.mf1))
        self.commit_refused()
        self.refused("INSERT INTO manifestation_relations (work_id, from_id, to_id, relation) "
                     "VALUES (?, ?, ?, 'revision_of')", (self.w1, self.mf1, self.mf1), "CHECK")

    def test_moving_one_endpoint_fails_and_moving_both_keeps_the_relation(self):
        self.conn.execute("INSERT INTO manifestation_relations (work_id, from_id, to_id, relation) "
                          "VALUES (?, 'MF-B1', ?, 'revision_of')", (self.w1, self.mf1))
        self.add_mf(self.w1, "MF-C1")
        self.conn.execute("INSERT INTO manifestation_relations (work_id, from_id, to_id, relation) "
                          "VALUES (?, 'MF-C1', 'MF-B1', 'reprint_of')", (self.w1,))
        self.conn.execute("DELETE FROM manifestation_relations WHERE from_id = 'MF-B1'")
        self.conn.execute("BEGIN")
        self.conn.execute("UPDATE manifestations SET work_id = ? WHERE id = 'MF-C1'", (self.w2,))
        self.commit_refused()
        self.conn.execute("BEGIN")
        self.conn.execute("UPDATE manifestations SET work_id = ? WHERE id = 'MF-C1'", (self.w2,))
        self.conn.execute("UPDATE manifestations SET work_id = ? WHERE id = 'MF-B1'", (self.w2,))
        self.conn.execute("COMMIT")
        self.assertEqual(
            self.conn.execute("SELECT work_id FROM manifestation_relations").fetchall(),
            [(self.w2,)])


class TransitionTests(IntegrityCase):
    def setUp(self):
        super().setUp()
        self.person = self.db.add_person("Ada", "Lovelace")
        self.conn.execute("INSERT INTO annotations (id, work_id, content) VALUES ('ann-1', ?, 'n')",
                          (self.w1,))
        self.conn.execute("INSERT INTO roles (person_id, work_id, role_type, manifestation_id) "
                          "VALUES (?, ?, 'Translator', ?)", (self.person, self.w1, self.mf1))
        self.conn.execute("INSERT INTO arguments (id, name, kind) VALUES ('A-1', 'A', 'argument')")
        self.conn.execute("INSERT INTO argument_sources (argument_id, order_index, work_id, pages) "
                          "VALUES ('A-1', 0, ?, '45')", (self.w1,))

    def test_move_only_primary_asset_out(self):
        self.conn.execute("BEGIN")
        self.conn.execute("UPDATE assets SET manifestation_id = ?, work_id = ? WHERE id = ?",
                          (self.mf2, self.w2, self.as1))
        self.conn.execute("UPDATE manifestations SET primary_asset_id = NULL WHERE id = ?",
                          (self.mf1,))
        self.conn.execute("COMMIT")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM assets WHERE manifestation_id = ?",
                                           (self.mf1,)).fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT work_id, asset_id FROM annotations").fetchone(),
                         (self.w2, self.as1))
        self.assertEqual(work_identity.integrity_violations(self.conn), [])

    def test_moving_a_primary_asset_without_repointing_fails_at_commit(self):
        before = self.snapshot()
        self.conn.execute("BEGIN")
        self.conn.execute("UPDATE assets SET manifestation_id = ?, work_id = ? WHERE id = ?",
                          (self.mf2, self.w2, self.as1))
        self.commit_refused()
        self.assertEqual(self.snapshot(), before)

    def test_move_non_primary_asset_needs_no_pointer_change(self):
        self.conn.execute("INSERT INTO assets (id, manifestation_id, work_id, kind, origin) "
                          "VALUES ('AS-EXTRA', ?, ?, 'managed_file', 'upload')", (self.mf1, self.w1))
        self.conn.execute("BEGIN")
        self.conn.execute("UPDATE assets SET manifestation_id = ?, work_id = ? "
                          "WHERE id = 'AS-EXTRA'", (self.mf2, self.w2))
        self.conn.execute("COMMIT")
        self.assertEqual(work_identity.integrity_violations(self.conn), [])

    def _retire_and_move(self, *, merge):
        self.conn.execute("BEGIN")
        if merge:
            self.conn.execute("INSERT INTO sync_work_lifecycle (work_id, state, target_work_id) "
                              "VALUES (?, 'merged', ?)", (self.w1, self.w2))
        self.conn.execute("INSERT INTO work_retirement (work_id) VALUES (?)", (self.w1,))
        self.conn.execute("UPDATE works SET primary_manifestation_id = NULL, "
                          "citation_manifestation_id = NULL WHERE id = ?", (self.w1,))
        self.conn.execute("UPDATE manifestations SET work_id = ? WHERE id = ?", (self.w2, self.mf1))

    def _assert_moved(self):
        c = self.conn
        self.assertIsNone(c.execute("SELECT 1 FROM works WHERE id = ?", (self.w1,)).fetchone())
        self.assertEqual(c.execute("SELECT work_id FROM manifestations WHERE id = ?",
                                   (self.mf1,)).fetchone(), (self.w2,))
        self.assertEqual(c.execute("SELECT work_id FROM assets WHERE id = ?",
                                   (self.as1,)).fetchone(), (self.w2,))
        self.assertEqual(c.execute("SELECT work_id FROM annotations").fetchone(), (self.w2,))
        self.assertEqual(c.execute("SELECT work_id, manifestation_id FROM roles").fetchone(),
                         (self.w2, self.mf1))
        self.assertEqual(c.execute("SELECT work_id, manifestation_id FROM argument_sources")
                         .fetchone(), (self.w2, self.mf1))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM work_retirement").fetchone()[0], 0)
        self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(work_identity.integrity_violations(c), [])

    def test_move_only_manifestation_out_and_delete_the_empty_work(self):
        self._retire_and_move(merge=False)
        self.conn.execute("DELETE FROM works WHERE id = ?", (self.w1,))
        self.conn.execute("COMMIT")
        self._assert_moved()

    def test_move_only_manifestation_out_and_merge_the_empty_work(self):
        self._retire_and_move(merge=True)
        self.conn.execute("DELETE FROM works WHERE id = ?", (self.w1,))
        self.conn.execute("COMMIT")
        self._assert_moved()
        self.assertEqual(self.conn.execute("SELECT state, target_work_id FROM sync_work_lifecycle "
                                           "WHERE work_id = ?", (self.w1,)).fetchone(),
                         ("merged", self.w2))

    def test_abandoned_retirement_marker_fails_commit_and_rolls_back(self):
        before = self.snapshot()
        self._retire_and_move(merge=False)
        self.commit_refused()
        self.assertEqual(self.snapshot(), before)
        # Even the marker alone, with nothing else done.
        self.conn.execute("BEGIN")
        self.conn.execute("INSERT INTO work_retirement (work_id) VALUES (?)", (self.w1,))
        self.commit_refused()
        self.assertEqual(self.snapshot(), before)

    def test_direct_bypasses_are_refused(self):
        self.refused("INSERT INTO work_retirement_guard (id) VALUES (1)", code="CHECK")
        self.refused("INSERT INTO work_retirement_guard (id) VALUES (NULL)", code="CHECK")
        self.refused("INSERT INTO work_retirement (work_id, must_clear) VALUES (?, NULL)",
                     (self.w1,), "NOT NULL")
        self.conn.execute("BEGIN")
        self.conn.execute("INSERT INTO work_retirement (work_id) VALUES (?)", (self.w1,))
        self.conn.execute("UPDATE works SET primary_manifestation_id = NULL WHERE id = ?",
                          (self.w1,))
        self.refused("DELETE FROM work_retirement WHERE work_id = ?", (self.w1,),
                     "WORK_RETIREMENT_WORK_STILL_EXISTS")
        self.refused("UPDATE work_retirement SET work_id = 'W-GONE' WHERE work_id = ?",
                     (self.w1,), "WORK_RETIREMENT_IMMUTABLE")
        self.refused("UPDATE work_retirement SET must_clear = NULL", code="WORK_RETIREMENT_IMMUTABLE")
        self.commit_refused()
        self.assertIsNotNone(self._primary_mf(self.w1))

    def test_pinned_version_delete_is_refused_but_whole_work_delete_cascades(self):
        self.add_mf(self.w1, "MF-SIDE")
        self.conn.execute("INSERT INTO argument_sources (argument_id, order_index, work_id, "
                          "manifestation_id, pages) VALUES ('A-1', 1, ?, 'MF-SIDE', '7')", (self.w1,))
        self.refused("DELETE FROM manifestations WHERE id = 'MF-SIDE'", code="FOREIGN KEY")
        self.db.delete_work_record(self.w1)
        for table in ("manifestations", "assets", "annotations", "roles", "argument_sources"):
            self.assertEqual(self.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE work_id = ?",
                                               (self.w1,)).fetchone()[0], 0, table)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])


class UpgradedIntegrityTests(WorkIdentityCase):
    """The rebuilt tables sit in a different creation order than a fresh DB.
    Whole-Work deletion with a pinned citation must not depend on it."""

    def test_whole_work_delete_with_pinned_citation_after_upgrade(self):
        def build(db):
            w = db.add_work(title="Pinned")
            from backend import research_network
            aid = research_network.create_argument(db, **{
                "name": "A", "kind": "argument", "sources": [{"work_id": w, "pages": "3"}]})["id"]
            return {"w": w, "a": aid}
        ids = self._v16(build)
        db = self._open()
        self.assertIsNotNone(self._q("SELECT manifestation_id FROM argument_sources")[0][0])
        db.delete_work_record(ids["w"])
        self.assertEqual(self._q("SELECT COUNT(*) FROM argument_sources")[0][0], 0)
        self._assert_clean()


# ---------------------------------------------------------------------------
# Compatibility mirror (works -> new rows) through the existing code paths
# ---------------------------------------------------------------------------

class MirrorTests(WorkIdentityCase):
    def setUp(self):
        super().setUp()
        self.db = self._open()

    def _assets(self, work_id):
        return self._q("SELECT id, kind, storage_locator, thumb_page, url FROM assets "
                       "WHERE work_id = ?", (work_id,))

    def test_metadata_only_work_gets_an_asset_only_when_it_gains_asset_state(self):
        w = self.db.add_work(title="Plain", doi="10.1/a")
        self.assertEqual(self._assets(w), [])
        self.db.update_work_metadata(w, {"doi": "10.1/b", "title": "Plainer"})
        self.assertEqual(self._q("SELECT doi FROM manifestations WHERE origin_work_id = ?", (w,)),
                         [("10.1/b",)])
        self.assertEqual(self._assets(w), [])
        self.db.update_work_metadata(w, {"thumb_page": 2})
        (asset,) = self._assets(w)
        self.assertEqual(asset[1:4], ("managed_file", None, 2))
        self.db.update_work_metadata(w, {"thumb_page": None})
        self.assertEqual(self._assets(w), [(asset[0], "managed_file", None, None, None)])
        self._assert_clean()

    def test_annotation_on_a_file_less_work_creates_its_asset(self):
        from backend import pdf_annotation_sync
        w = self.db.add_work(title="Notes")
        with self.db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            pdf_annotation_sync.insert_annotation_on_conn(conn, w, {
                "id": "ann-mirror", "type": 9, "pageIndex": 0,
                "rect": {"origin": {"x": 1, "y": 2}, "size": {"width": 3, "height": 4}}})
        (asset,) = self._assets(w)
        self.assertEqual(self._q("SELECT asset_id FROM annotations WHERE id = 'ann-mirror'"),
                         [(asset[0],)])
        self._assert_clean()

    def test_asset_bound_revision_alone_creates_the_asset(self):
        w = self.db.add_work(title="Tomb")
        other = self.db.add_work(title="Other field")
        conn = _raw(self.storage.db_path)
        conn.execute("INSERT INTO sync_entity_revisions (scope_type, scope_id, revision) "
                     "VALUES ('work-field', ?, 1)", (_scope(w, "thumb_page"),))
        conn.execute("INSERT INTO sync_entity_revisions (scope_type, scope_id, revision) "
                     "VALUES ('work-field', ?, 1)", (_scope(other, "doi"),))
        conn.execute("INSERT INTO sync_entity_revisions (scope_type, scope_id, revision) "
                     "VALUES ('pdf-annotation', 'not json', 1)")
        conn.commit()
        conn.close()
        self.assertEqual(len(self._assets(w)), 1)
        self.assertEqual(self._assets(other), [])

    def test_video_and_pdf_urls_have_one_owner(self):
        v = self.db.add_work(title="Clip", source_kind="video", source_url=YT)
        p = self.db.add_work(title="Paper", file_path="/api/pdfs/p.pdf",
                             source_url="https://example.org/src")
        self.assertEqual(self._assets(v)[0][1:], ("external_stream", None, None, YT))
        self.assertEqual(self._q("SELECT url FROM manifestations WHERE origin_work_id = ?", (v,)),
                         [(None,)])
        self.assertEqual(self._q("SELECT url FROM manifestations WHERE origin_work_id = ?", (p,)),
                         [("https://example.org/src",)])
        self.db.update_work_metadata(p, {"file_path": "/api/pdfs/q.pdf"})
        self.assertEqual(self._assets(p)[0][2], "q.pdf")
        self._assert_clean()

    def test_legacy_argument_writes_are_pinned_like_the_backfill(self):
        from backend import research_network
        w1 = self.db.add_work(title="A")
        w2 = self.db.add_work(title="B")
        aid = research_network.create_argument(self.db, **{
            "name": "Arg", "kind": "argument",
            "sources": [{"work_id": w1, "pages": "12"}, {"work_id": w2, "pages": ""}]})["id"]
        self.assertEqual(
            self._q("SELECT work_id, manifestation_id FROM argument_sources ORDER BY order_index"),
            [(w1, self._primary_mf(w1)), (w2, None)])
        with self.db.connection() as conn:
            self.assertEqual(argument_sync.current_sources(conn, aid),
                             [{"work_id": w1, "pages": "12"}, {"work_id": w2, "pages": ""}])
        self._assert_clean()

    def test_work_delete_removes_every_identity_row(self):
        w = self.db.add_work(title="Gone", file_path="/api/pdfs/g.pdf")
        self.db.delete_work_record(w)
        for table in ("manifestations", "assets"):
            self.assertEqual(self._q(f"SELECT COUNT(*) FROM {table}")[0][0], 0)
        self._assert_clean()

    def test_get_work_keys_are_unchanged(self):
        w = self.db.add_work(title="Shape")
        work = self.db.get_work(w)
        for column in work_identity.WORK_POINTER_COLUMNS:
            self.assertNotIn(column, work)

    def test_ensure_origin_asset_is_deterministic_and_idempotent(self):
        w = self.db.add_work(title="Lazy")
        conn = _raw(self.storage.db_path)
        try:
            self.assertFalse(work_identity.origin_asset_required(conn, w))
            asset_id = work_identity.ensure_origin_asset(conn, w)
            self.assertEqual(asset_id, work_identity.backfill_asset_id(w))
            self.assertEqual(work_identity.ensure_origin_asset(conn, w), asset_id)
            self.assertIsNone(work_identity.ensure_origin_asset(conn, "W-MISSING"))
            conn.commit()
        finally:
            conn.close()
        self._assert_clean()


class InferredVideoAfterMigrationTests(WorkIdentityCase):
    """PR #202 review: the same inferred-video shape must get the same Asset
    whether it existed before v17 or is produced by an ordinary write after."""

    def setUp(self):
        super().setUp()
        self.db = self._open()

    def _inferred_candidate(self):
        w = self.db.add_work(title="Plain")
        # A Work with no stored kind: the shape legacy and API-created rows have.
        conn = _raw(self.storage.db_path)
        conn.execute("UPDATE works SET source_kind = NULL WHERE id = ?", (w,))
        conn.commit()
        conn.close()
        self.assertEqual(self._q("SELECT COUNT(*) FROM assets WHERE work_id = ?", (w,)), [(0,)])
        return w

    def _mf_url(self, w):
        return self._q("SELECT url FROM manifestations WHERE origin_work_id = ?", (w,))[0][0]

    def test_patching_a_video_url_creates_the_deterministic_stream_asset(self):
        w = self._inferred_candidate()
        self.db.update_work_metadata(w, {"source_url": YT})
        self.assertEqual(
            self._q("SELECT id, kind, url, storage_locator FROM assets WHERE work_id = ?", (w,)),
            [(work_identity.backfill_asset_id(w), "external_stream", YT, None)])
        self.assertIsNone(self._mf_url(w))
        self._assert_clean()

    def test_the_durable_field_write_takes_the_same_path(self):
        from backend import work_metadata_sync
        w = self._inferred_candidate()
        conn = _raw(self.storage.db_path)
        work_metadata_sync.set_field_on_conn(conn, w, "source_url", YT)
        conn.commit()
        conn.close()
        self.assertEqual(self._q("SELECT kind FROM assets WHERE work_id = ?", (w,)),
                         [("external_stream",)])
        self._assert_clean()

    def test_a_non_video_url_creates_no_asset_and_stays_on_the_manifestation(self):
        w = self._inferred_candidate()
        self.db.update_work_metadata(w, {"source_url": "https://example.org/article"})
        self.assertEqual(self._q("SELECT COUNT(*) FROM assets WHERE work_id = ?", (w,)), [(0,)])
        self.assertEqual(self._mf_url(w), "https://example.org/article")
        self._assert_clean()

    def test_removing_the_file_can_expose_an_inferred_video(self):
        w = self.db.add_work(title="Was a PDF", file_path="/api/pdfs/w.pdf")
        conn = _raw(self.storage.db_path)
        conn.execute("UPDATE works SET source_kind = NULL, source_url = ? WHERE id = ?", (YT, w))
        conn.commit()
        conn.close()
        self.db.update_work_metadata(w, {"file_path": ""})
        self.assertEqual(self._q("SELECT kind, url FROM assets WHERE work_id = ?", (w,)),
                         [("external_stream", YT)])
        self._assert_clean()

    def test_mirror_drift_reports_a_required_but_missing_asset(self):
        w = self._inferred_candidate()
        conn = _raw(self.storage.db_path)
        try:
            # Direct SQL bypasses the Python boundary: the check must see it.
            conn.execute("UPDATE works SET source_url = ? WHERE id = ?", (YT, w))
            self.assertIn(("asset", w, "missing"), work_identity.mirror_drift(conn))
            work_identity.reconcile_origin_asset(conn, w)
            self.assertEqual(work_identity.mirror_drift(conn), [])
        finally:
            conn.close()


class StreamAssetTests(WorkIdentityCase):
    """PR #202 review (Qodo): an external stream never claims managed bytes."""

    def test_video_with_a_managed_path_projects_no_locator(self):
        db = self._open()
        w = db.add_work(title="Clip", source_kind="video", source_url=YT)
        db.update_work_metadata(w, {"file_path": "/api/pdfs/stray.pdf"})
        self.assertEqual(
            self._q("SELECT kind, storage_locator, media_type FROM assets WHERE work_id = ?", (w,)),
            [("external_stream", None, None)])
        self._assert_clean()

    def test_video_with_a_managed_path_after_migration(self):
        def raw(conn, ids):
            conn.execute("UPDATE works SET source_kind = 'video', source_url = ?, "
                         "file_path = '/api/pdfs/stray.pdf' WHERE id = ?", (YT, ids["w"]))
        ids = self._v16(lambda db: {"w": db.add_work(title="Clip")}, raw)
        self._open()
        self.assertEqual(
            self._q("SELECT kind, storage_locator FROM assets WHERE work_id = ?", (ids["w"],)),
            [("external_stream", None)])
        self._assert_clean()


class RevisionScopeShapeTests(WorkIdentityCase):
    """PR #202 review (Qodo): only well-formed Asset-bound scopes create Assets."""

    def test_malformed_scopes_create_no_asset(self):
        db = self._open()
        w = db.add_work(title="Plain")
        bad = [
            ("work-source", _scope(w, "extra")),
            ("work-source", json.dumps([1])),
            ("pdf-annotation", _scope(w)),
            ("pdf-annotation", json.dumps([w, 7])),
            ("pdf-annotation", _scope(w, "a", "b")),
            ("work-field", _scope(w, "thumb_page", "x")),
            ("work-field", json.dumps([w, None])),
            ("work-field", json.dumps({"0": w, "1": "thumb_page"})),
            ("work-source", "not json"),
        ]
        conn = _raw(self.storage.db_path)
        try:
            for scope_type, scope_id in bad:
                conn.execute("INSERT INTO sync_entity_revisions (scope_type, scope_id, revision) "
                             "VALUES (?, ?, 1)", (scope_type, scope_id))
            conn.commit()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 0)
            conn.execute("INSERT INTO sync_entity_revisions (scope_type, scope_id, revision) "
                         "VALUES ('pdf-annotation', ?, 1)", (_scope(w, "ann-1"),))
            conn.commit()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 1)
        finally:
            conn.close()
        self._assert_clean()


# ---------------------------------------------------------------------------
# I10 / D2: the migration never touches the filesystem
# ---------------------------------------------------------------------------

class NoFilesystemTests(WorkIdentityCase):
    def test_migration_reads_writes_lists_and_backs_up_nothing(self):
        self._v16(_build_fixture_library, _raw_fixture_states)
        pdf = os.path.join(self.storage.pdfs_dir, "paper.pdf")
        with open(pdf, "wb") as fh:
            fh.write(b"%PDF-1.4 untouched")
        before = (os.stat(pdf).st_mtime_ns, sorted(os.listdir(self.storage.pdfs_dir)))

        def forbidden(*_a, **_k):
            raise AssertionError("filesystem access during the v17 migration")

        conn = sqlite3.connect(self.storage.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        from backend import backup_restore
        import hashlib
        import zipfile
        try:
            with patch.object(builtins, "open", forbidden), \
                    patch.object(os, "listdir", forbidden), \
                    patch.object(os, "scandir", forbidden), \
                    patch.object(os, "walk", forbidden), \
                    patch.object(os, "stat", forbidden), \
                    patch.object(os, "remove", forbidden), \
                    patch.object(os, "replace", forbidden), \
                    patch.object(shutil, "copyfile", forbidden), \
                    patch.object(zipfile, "ZipFile", forbidden), \
                    patch.object(hashlib, "sha256", forbidden), \
                    patch.object(backup_restore, "create_backup", forbidden):
                apply_ordered_migrations(conn, 16)
        finally:
            conn.close()
        self.assertEqual(_version(self.storage.db_path), LATEST_SCHEMA_VERSION)
        self.assertEqual((os.stat(pdf).st_mtime_ns, sorted(os.listdir(self.storage.pdfs_dir))),
                         before)
        with open(pdf, "rb") as fh:
            self.assertEqual(fh.read(), b"%PDF-1.4 untouched")


if __name__ == "__main__":
    unittest.main()
