import unittest
import os
import sys
import tempfile
import shutil
import uuid
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.db_manager import (
    PRKSDatabase,
    PRKS_BIBTEX_EXPORT_FIELD_IDS,
    PRKS_BULK_WORK_MAX,
    PRKS_SAVED_VIEW_MAX,
    BulkWorkError,
    SavedViewError,
    safe_pdf_path_under_dir,
    safe_processing_path_under_dir,
    prks_thumb_cache_safe_wid,
    prks_thumb_cache_stem,
    prune_orphan_pdf_thumbnails,
    prune_empty_processing_parent_dirs,
)
from backend.storage import paths as storage_paths
from backend.storage.config import StorageConfig
from backend.text_index import PRKSTextIndex
from backend.work_deletion import delete_work

_SCHEMA_PATH = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")

class TestDBManager(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="prks-db-tests-")
        self.storage = StorageConfig.for_testing(self._tmpdir)
        os.makedirs(self.storage.pdfs_dir, exist_ok=True)
        os.makedirs(self.storage.thumbs_dir, exist_ok=True)
        os.makedirs(self.storage.processing_dir, exist_ok=True)
        self.db = PRKSDatabase(storage=self.storage, schema_path=_SCHEMA_PATH)
        self.test_db_path = self.storage.db_path

    def tearDown(self):
        if getattr(self, "_tmpdir", None):
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_get_all_works_sets_file_size_bytes_for_local_pdf(self):
        payload = b"0123456789abcdef"
        fname = f"test_prks_size_{uuid.uuid4().hex}.pdf"
        path = os.path.join(self.storage.pdfs_dir, fname)
        with open(path, "wb") as f:
            f.write(payload)
        w_id = self.db.add_work(title="Sized PDF", file_path=f"/api/pdfs/{fname}")
        rows = self.db.get_all_works()
        row = next(r for r in rows if r["id"] == w_id)
        self.assertEqual(row.get("file_size_bytes"), len(payload))
        work = self.db.get_work(w_id)
        self.assertEqual(work.get("file_size_bytes"), len(payload))

    def test_add_and_get_work(self):
        w_id = self.db.add_work(title="Test Work", status="Completed", author_text="John Doe", year="2023")
        work = self.db.get_work(w_id)
        self.assertIsNotNone(work)
        self.assertEqual(work['title'], "Test Work")
        self.assertEqual(work['status'], "Completed")
        self.assertEqual(work['author_text'], "John Doe")
        self.assertEqual(work['year'], "2023")
        self.assertEqual(work.get("doc_type"), "article")

    def test_app_settings_annotation_author(self):
        r0 = self.db.get_app_settings_response()
        self.assertEqual(r0["annotation_author"], "")
        self.assertIn("bibtex_export_fields", r0)
        for k in PRKS_BIBTEX_EXPORT_FIELD_IDS:
            self.assertTrue(r0["bibtex_export_fields"].get(k), k)
        self.db.patch_app_settings({"annotation_author": "Dr. Ada"})
        self.assertEqual(self.db.get_app_settings_response()["annotation_author"], "Dr. Ada")
        self.db.patch_app_settings({"annotation_author": ""})
        self.assertEqual(self.db.get_app_settings_response()["annotation_author"], "")

    def test_app_settings_bibtex_export_fields_patch_merge(self):
        self.db.patch_app_settings({"bibtex_export_fields": {"isbn": False, "location": False}})
        r = self.db.get_app_settings_response()["bibtex_export_fields"]
        self.assertFalse(r["isbn"])
        self.assertFalse(r["location"])
        self.assertTrue(r["doi"])
        self.db.patch_app_settings({"bibtex_export_fields": {"doi": False}})
        r2 = self.db.get_app_settings_response()["bibtex_export_fields"]
        self.assertFalse(r2["isbn"])
        self.assertFalse(r2["location"])
        self.assertFalse(r2["doi"])
        self.db.patch_app_settings({"bibtex_export_fields": {"isbn": True}})
        r3 = self.db.get_app_settings_response()["bibtex_export_fields"]
        self.assertTrue(r3["isbn"])
        self.assertFalse(r3["location"])
        self.db.patch_app_settings({"bibtex_export_fields": {"foreword": False, "afterword": False}})
        r4 = self.db.get_app_settings_response()["bibtex_export_fields"]
        self.assertFalse(r4["foreword"])
        self.assertFalse(r4["afterword"])
        self.assertTrue(r4["introduction"])

    def test_app_settings_bibtex_export_fields_invalid(self):
        with self.assertRaises(ValueError):
            self.db.patch_app_settings({"bibtex_export_fields": {"not_a_field": False}})
        with self.assertRaises(ValueError):
            self.db.patch_app_settings({"bibtex_export_fields": {"isbn": "no"}})
        with self.assertRaises(ValueError):
            self.db.patch_app_settings({"bibtex_export_fields": []})

    def test_bibtex_respects_export_field_omissions(self):
        w_id = self.db.add_work(
            title="Export Omit",
            year="2022",
            publisher="P",
            location="Here",
            isbn="978-0-TEST",
            doi="10.1000/test",
            abstract="Abs",
            doc_type="book",
        )
        bib_all = self.db.generate_bibtex(w_id)
        self.assertIn("isbn = {978-0-TEST}", bib_all)
        self.assertIn("location = {Here}", bib_all)

        self.db.patch_app_settings({"bibtex_export_fields": {"isbn": False, "location": False}})
        bib_cut = self.db.generate_bibtex(w_id)
        self.assertNotIn("isbn = ", bib_cut)
        self.assertNotIn("location = ", bib_cut)
        self.assertIn("title = {Export Omit}", bib_cut)
        self.assertIn("publisher = {P}", bib_cut)

        self.db.patch_app_settings({"bibtex_export_fields": {"isbn": True, "location": True}})
        bib_restored = self.db.generate_bibtex(w_id)
        self.assertIn("isbn = {978-0-TEST}", bib_restored)
        self.assertIn("location = {Here}", bib_restored)

    def test_update_work_metadata(self):
        w_id = self.db.add_work(title="Initial", status="Not Started")
        self.db.update_work_metadata(w_id, {"title": "Updated", "status": "Completed"})
        work = self.db.get_work(w_id)
        self.assertEqual(work['title'], "Updated")
        self.assertEqual(work['status'], "Completed")

    def test_update_work_metadata_edition(self):
        w_id = self.db.add_work(title="Book", doc_type="book")
        self.db.update_work_metadata(w_id, {"edition": "3"})
        work = self.db.get_work(w_id)
        self.assertEqual(work.get("edition"), "3")

    def test_update_work_metadata_doc_type(self):
        w_id = self.db.add_work(title="T", doc_type="article")
        self.db.update_work_metadata(w_id, {"doc_type": "inproceedings"})
        row = self.db.execute_query("SELECT doc_type FROM works WHERE id = ?", (w_id,))
        self.assertEqual(row[0]["doc_type"], "inproceedings")
        self.db.update_work_metadata(w_id, {"doc_type": "not-a-real-type"})
        row2 = self.db.execute_query("SELECT doc_type FROM works WHERE id = ?", (w_id,))
        self.assertEqual(row2[0]["doc_type"], "misc")

    def test_update_work_metadata_hide_pdf_link_annotations(self):
        w_id = self.db.add_work(title="Pdfish")
        self.db.update_work_metadata(w_id, {"hide_pdf_link_annotations": True})
        work = self.db.get_work(w_id)
        self.assertEqual(work.get("hide_pdf_link_annotations"), 1)
        self.db.update_work_metadata(w_id, {"hide_pdf_link_annotations": False})
        work2 = self.db.get_work(w_id)
        self.assertEqual(work2.get("hide_pdf_link_annotations"), 0)

    def test_delete_work(self):
        w_id = self.db.add_work(title="To be deleted")
        self.db.delete_work_record(w_id)
        work = self.db.get_work(w_id)
        self.assertIsNone(work)

    def test_delete_work_removes_pdf_thumbnail_cache_files(self):
        fname = f"td_{uuid.uuid4().hex}.pdf"
        pdfs_dir = self.storage.pdfs_dir
        thumbs_dir = self.storage.thumbs_dir
        with open(os.path.join(pdfs_dir, fname), "wb") as f:
            f.write(b"x")
        w_id = self.db.add_work(title="DelThumb", file_path=f"/api/pdfs/{fname}")
        stem = prks_thumb_cache_stem(w_id, 1)
        p1 = os.path.join(thumbs_dir, f"{stem}.webp")
        p2 = os.path.join(thumbs_dir, f"{prks_thumb_cache_stem(w_id, 2)}.png")
        tmp = os.path.join(thumbs_dir, f"{stem}.webp.tmp")
        for p in (p1, p2, tmp):
            with open(p, "wb") as f:
                f.write(b"z")
        delete_work(self.db, PRKSTextIndex(storage=self.storage), w_id)
        self.assertFalse(os.path.exists(p1))
        self.assertFalse(os.path.exists(p2))
        self.assertFalse(os.path.exists(tmp))

    def test_prune_orphan_pdf_thumbnails(self):
        fname = f"tp_{uuid.uuid4().hex}.pdf"
        pdfs_dir = self.storage.pdfs_dir
        thumbs_dir = self.storage.thumbs_dir
        with open(os.path.join(pdfs_dir, fname), "wb") as f:
            f.write(b"x")
        w_id = self.db.add_work(
            title="KeepThumb",
            file_path=f"/api/pdfs/{fname}",
            thumb_page=2,
        )
        good = os.path.join(thumbs_dir, f"{prks_thumb_cache_stem(w_id, 2)}.webp")
        stale_page = os.path.join(thumbs_dir, f"{prks_thumb_cache_stem(w_id, 9)}.png")
        orphan = os.path.join(thumbs_dir, "zzzorphan_p1_v2.webp")
        for p in (good, stale_page, orphan):
            with open(p, "wb") as f:
                f.write(b"z")
        n = prune_orphan_pdf_thumbnails(self.db)
        self.assertEqual(n, 2)
        self.assertTrue(os.path.isfile(good))
        self.assertFalse(os.path.exists(stale_page))
        self.assertFalse(os.path.exists(orphan))

    def test_get_all_works_omits_text_and_private_notes(self):
        w_id = self.db.add_work(title="Heavy", text_content="x" * 5000, abstract="Short abs")
        self.db.update_work_metadata(w_id, {"private_notes": "secret"})
        rows = self.db.get_all_works()
        self.assertEqual(len(rows), 1)
        self.assertNotIn("text_content", rows[0])
        self.assertNotIn("private_notes", rows[0])
        self.assertEqual(rows[0]["title"], "Heavy")
        self.assertEqual(rows[0]["abstract"], "Short abs")
        full = self.db.get_work(w_id)
        self.assertIn("text_content", full)
        self.assertEqual(len(full["text_content"]), 5000)
        self.assertEqual(full.get("private_notes"), "secret")

    def test_search_works_list_shape_omits_text_content(self):
        w_id = self.db.add_work(title="UniqueSnailTitle", text_content="bulk notes here")
        found = self.db.search_works("UniqueSnailTitle")
        self.assertEqual(len(found), 1)
        self.assertNotIn("text_content", found[0])
        self.assertEqual(found[0]["id"], w_id)

    def test_work_ids_matching_publisher_substring(self):
        w1 = self.db.add_work(title="PubSubA", publisher="Acme Press International")
        w2 = self.db.add_work(title="PubSubB", publisher="Other")
        ids = set(self.db.work_ids_matching_publisher("acme press"))
        self.assertEqual(ids, {w1})
        self.assertIn(w2, self.db.work_ids_matching_publisher("other"))

    def test_work_ids_matching_publisher_alias_equivalence(self):
        w_long = self.db.add_work(title="OxLong", publisher="Oxford University Press")
        w_short = self.db.add_work(title="OxShort", publisher="OUP")
        out = self.db.add_publisher("Oxford University Press")
        pid = out["id"]
        self.db.add_publisher_alias(pid, "OUP")
        ids = set(self.db.work_ids_matching_publisher("oup"))
        self.assertEqual(ids, {w_long, w_short})
        ids2 = set(self.db.work_ids_matching_publisher("oxford"))
        self.assertEqual(ids2, {w_long, w_short})

    def test_search_works_publisher_and_author_filters(self):
        p_id = self.db.add_person(first_name="Ann", last_name="Author")
        w_ok = self.db.add_work(title="BothMatch", author_text="Ann Author", publisher="TestPub LLC")
        w_wrong_pub = self.db.add_work(title="WrongPub", author_text="Ann Author", publisher="Other")
        w_wrong_auth = self.db.add_work(title="WrongAuth", publisher="TestPub LLC")
        self.db.add_role(p_id, w_ok, "Author")
        found = self.db.search_works("", author_filter="Ann", publisher_filter="TestPub")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["id"], w_ok)
        self.assertNotIn(w_wrong_auth, {r["id"] for r in found})
        self.assertEqual(len(self.db.search_works("", author_filter="Ann", publisher_filter="nomatch")), 0)
        self.assertIn(w_wrong_pub, {r["id"] for r in self.db.search_works("", publisher_filter="Other")})

    def test_get_publishers_in_use_and_delete_publisher(self):
        r = self.db.add_publisher("Canonical Pub Name")
        pid = r["id"]
        self.db.add_publisher_alias(pid, "CPN Short")
        rows = self.db.get_publishers_in_use()
        self.assertTrue(any(x["id"] == pid for x in rows))
        row = next(x for x in rows if x["id"] == pid)
        self.assertEqual(row["name"], "Canonical Pub Name")
        self.assertIn("CPN Short", row["aliases"])
        self.db.delete_publisher(pid)
        rows2 = self.db.get_publishers_in_use()
        self.assertFalse(any(x["id"] == pid for x in rows2))

    def test_folder_operations(self):
        f_id = self.db.add_folder(title="Test Folder", description="Testing")
        folders = self.db.get_all_folders()
        self.assertEqual(len(folders), 1)
        self.assertEqual(folders[0]['title'], "Test Folder")
        
        # Add a work to the folder
        w_id = self.db.add_work(title="Folder Work")
        self.db.add_work_to_folder(f_id, w_id)
        
        folder = self.db.get_folder(f_id)
        self.assertEqual(len(folder['works']), 1)
        self.assertEqual(folder['works'][0]['title'], "Folder Work")
        
        # Prevent deletion of non-empty folder
        with self.assertRaises(ValueError):
            self.db.delete_empty_folder(f_id)

    def test_add_work_to_folder_rejects_other_folder(self):
        f1 = self.db.add_folder(title="F One", description="")
        f2 = self.db.add_folder(title="F Two", description="")
        w_id = self.db.add_work(title="Shared")
        self.db.add_work_to_folder(f1, w_id)
        with self.assertRaises(ValueError):
            self.db.add_work_to_folder(f2, w_id)

    def test_move_work_to_folder_assign_move_clear(self):
        f1 = self.db.add_folder(title="A", description="")
        f2 = self.db.add_folder(title="B", description="")
        w_id = self.db.add_work(title="Movable")
        self.db.move_work_to_folder(w_id, f1)
        folder = self.db.get_folder(f1)
        self.assertEqual(len(folder["works"]), 1)
        self.db.move_work_to_folder(w_id, f2)
        folder1 = self.db.get_folder(f1)
        folder2 = self.db.get_folder(f2)
        self.assertEqual(len(folder1["works"]), 0)
        self.assertEqual(len(folder2["works"]), 1)
        self.db.move_work_to_folder(w_id, None)
        folder2b = self.db.get_folder(f2)
        self.assertEqual(len(folder2b["works"]), 0)

    def test_move_work_to_folder_rejects_bad_folder(self):
        w_id = self.db.add_work(title="X")
        with self.assertRaises(ValueError):
            self.db.move_work_to_folder(w_id, "F-NOT-REAL")

    def test_get_all_works_includes_folder_id(self):
        f_id = self.db.add_folder(title="Tagged", description="")
        w_id = self.db.add_work(title="In F")
        self.db.add_work_to_folder(f_id, w_id)
        rows = self.db.get_all_works()
        row = next(r for r in rows if r["id"] == w_id)
        self.assertEqual(row.get("folder_id"), f_id)

    def test_get_all_works_linked_authors_multiple(self):
        w_id = self.db.add_work(title="Dual Book", doc_type="book")
        p_a = self.db.add_person(first_name="Ann", last_name="Ayer")
        p_b = self.db.add_person(first_name="Ben", last_name="Boss")
        self.db.add_role(p_a, w_id, "Author", order_index=0)
        self.db.add_role(p_b, w_id, "Author", order_index=1)
        rows = self.db.get_all_works()
        row = next(r for r in rows if r["id"] == w_id)
        self.assertEqual(row.get("primary_author"), "Ann Ayer")
        la = row.get("linked_authors") or ""
        self.assertIn("Ann Ayer", la)
        self.assertIn("Ben Boss", la)

    def test_role_credit_name_stored_and_returned(self):
        w_id = self.db.add_work(title="Credit Work")
        p_id = self.db.add_person(first_name="Mark", last_name="Johnson")
        self.db.add_role(p_id, w_id, "Author", credit_name="Mark S. Johnson")
        roles = self.db.get_work_roles(w_id)
        self.assertEqual(len(roles), 1)
        self.assertEqual(roles[0]["credit_name"], "Mark S. Johnson")

    def test_linked_authors_uses_credit_name(self):
        w_id = self.db.add_work(title="Alias Card")
        p_id = self.db.add_person(first_name="Mark", last_name="Johnson")
        self.db.add_role(p_id, w_id, "Author", credit_name="Mark Johnson")
        row = next(r for r in self.db.get_all_works() if r["id"] == w_id)
        self.assertEqual(row.get("primary_author"), "Mark Johnson")
        self.assertEqual(row.get("linked_authors"), "Mark Johnson")

    def test_bibtex_uses_credit_name_for_author(self):
        w_id = self.db.add_work(title="Byline Book", year="2024", doc_type="book")
        p_id = self.db.add_person(first_name="Mark", last_name="Johnson")
        self.db.add_role(p_id, w_id, "Author", credit_name="Mark S. Johnson")
        bibtex = self.db.generate_bibtex(w_id)
        self.assertIn("author = {Mark S. Johnson}", bibtex)
        self.assertNotIn("Johnson, Mark", bibtex)

    def test_append_person_alias_if_new(self):
        p_id = self.db.add_person(first_name="Mark", last_name="Johnson", aliases="Mark Johnson")
        self.assertFalse(self.db.append_person_alias_if_new(p_id, "Mark Johnson"))
        self.assertTrue(self.db.append_person_alias_if_new(p_id, "Mark S. Johnson"))
        person = self.db.execute_query("SELECT aliases FROM persons WHERE id = ?", (p_id,))[0]
        self.assertIn("Mark S. Johnson", person["aliases"])

    def test_update_role_credit_name_clear_and_set(self):
        w_id = self.db.add_work(title="Patch Credit")
        p_id = self.db.add_person(first_name="Ann", last_name="Ayer")
        self.db.add_role(p_id, w_id, "Author", order_index=0, credit_name="Ann A.")
        self.assertTrue(
            self.db.update_role_credit_name(w_id, p_id, "Author", 0, "Ann Ayer Alias")
        )
        roles = self.db.get_work_roles(w_id)
        self.assertEqual(roles[0]["credit_name"], "Ann Ayer Alias")
        row = next(r for r in self.db.get_all_works() if r["id"] == w_id)
        self.assertEqual(row.get("linked_authors"), "Ann Ayer Alias")
        self.assertTrue(self.db.update_role_credit_name(w_id, p_id, "Author", 0, ""))
        roles2 = self.db.get_work_roles(w_id)
        self.assertIsNone(roles2[0].get("credit_name"))
        row2 = next(r for r in self.db.get_all_works() if r["id"] == w_id)
        self.assertEqual(row2.get("linked_authors"), "Ann Ayer")

    def test_etag_works_catalog_updates_on_add_role(self):
        w_id = self.db.add_work(title="Etag Role Work")
        e1 = self.db.etag_works_catalog(self.db.get_all_works())
        p = self.db.add_person(first_name="P", last_name="Q")
        self.db.add_role(p, w_id, "Author")
        e2 = self.db.etag_works_catalog(self.db.get_all_works())
        self.assertNotEqual(e1, e2)

    def test_etag_works_catalog_updates_on_delete_work_role(self):
        w_id = self.db.add_work(title="Etag Delete Role")
        p1 = self.db.add_person(first_name="A", last_name="One")
        p2 = self.db.add_person(first_name="B", last_name="Two")
        self.db.add_role(p1, w_id, "Author", order_index=0)
        self.db.add_role(p2, w_id, "Editor", order_index=1)
        e1 = self.db.etag_works_catalog(self.db.get_all_works())
        self.assertTrue(self.db.delete_work_role(w_id, p2, "Editor", 1))
        e2 = self.db.etag_works_catalog(self.db.get_all_works())
        self.assertNotEqual(e1, e2)

    def test_etag_works_catalog_follows_pdf_size_without_row_change(self):
        payload = b"%PDF-1.4\n" + b"a" * 40
        fname = f"etag_size_{uuid.uuid4().hex}.pdf"
        path = os.path.join(self.storage.pdfs_dir, fname)
        with open(path, "wb") as handle:
            handle.write(payload)
        w_id = self.db.add_work(title="Etag Sized PDF", file_path=f"/api/pdfs/{fname}")
        before_rows = self.db.get_all_works()
        before = next(row for row in before_rows if row["id"] == w_id)
        stamp = self.db.execute_query(
            "SELECT updated_at FROM works WHERE id = ?", (w_id,)
        )[0]["updated_at"]
        e1 = self.db.etag_works_catalog(before_rows)
        with open(path, "ab") as handle:
            handle.write(b"b" * 25)
        after_rows = self.db.get_all_works()
        after = next(row for row in after_rows if row["id"] == w_id)
        self.assertEqual(
            self.db.execute_query("SELECT updated_at FROM works WHERE id = ?", (w_id,))[0]["updated_at"],
            stamp,
        )
        self.assertEqual(before.get("file_size_bytes"), len(payload))
        self.assertEqual(after.get("file_size_bytes"), len(payload) + 25)
        self.assertNotEqual(self.db.etag_works_catalog(after_rows), e1)
        self.assertEqual(self.db.etag_works_catalog(after_rows), self.db.etag_works_catalog(self.db.get_all_works()))

    def test_add_folder_rejects_duplicate_title(self):
        self.db.add_folder(title="Unique Name", description="")
        with self.assertRaises(ValueError):
            self.db.add_folder(title="unique name", description="other")

    def test_add_folder_allows_same_title_under_different_parents(self):
        root_a = self.db.add_folder(title="Root A", description="")
        root_b = self.db.add_folder(title="Root B", description="")
        self.db.add_folder(title="Shared Child", description="", parent_id=root_a)
        self.db.add_folder(title="Shared Child", description="", parent_id=root_b)
        with self.assertRaises(ValueError):
            self.db.add_folder(title="shared child", description="", parent_id=root_a)

    def test_folder_reparent_cycle_guard_and_top_level_move(self):
        root = self.db.add_folder(title="Root", description="")
        child = self.db.add_folder(title="Child", description="", parent_id=root)
        grand = self.db.add_folder(title="Grand", description="", parent_id=child)
        with self.assertRaises(ValueError):
            self.db.update_folder_metadata(root, {"parent_id": grand})
        self.db.update_folder_metadata(child, {"parent_id": None})
        moved = self.db.get_folder(child)
        self.assertIsNone(moved.get("parent_id"))

    def test_delete_folder_rejects_subfolders(self):
        root = self.db.add_folder(title="Root X", description="")
        self.db.add_folder(title="Child X", description="", parent_id=root)
        with self.assertRaises(ValueError):
            self.db.delete_empty_folder(root)

    def test_move_work_to_nested_folder(self):
        parent = self.db.add_folder(title="Nested Parent", description="")
        child = self.db.add_folder(title="Nested Child", description="", parent_id=parent)
        work_id = self.db.add_work(title="Nested Move")
        self.db.move_work_to_folder(work_id, child)
        work = self.db.get_work(work_id)
        self.assertEqual(work.get("folder_id"), child)

    def test_person_groups_hierarchy_and_membership(self):
        g_phil = self.db.add_person_group(name="Philosophy", parent_id=None)
        g_fs = self.db.add_person_group(name="Frankfurt School", parent_id=g_phil)
        p_id = self.db.add_person(first_name="Theodor", last_name="Adorno")
        self.db.add_person_to_group(p_id, g_phil)
        self.db.add_person_to_group(p_id, g_fs)

        all_g = self.db.get_all_person_groups()
        self.assertEqual(len(all_g), 2)
        person = self.db.get_person(p_id)
        self.assertEqual(len(person["groups"]), 2)
        names = sorted(g["name"] for g in person["groups"])
        self.assertEqual(names, ["Frankfurt School", "Philosophy"])

        all_p = self.db.get_all_persons()
        row = next(p for p in all_p if p["id"] == p_id)
        self.assertEqual(len(row["groups"]), 2)

        detail = self.db.get_person_group(g_fs)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["parent"]["id"], g_phil)
        self.assertEqual(len(detail["members"]), 1)
        self.assertEqual(detail["members"][0]["last_name"], "Adorno")

        with self.assertRaises(ValueError):
            self.db.update_person_group(g_phil, {"parent_id": g_fs})

        self.db.set_person_group_memberships(p_id, [g_phil])
        person2 = self.db.get_person(p_id)
        self.assertEqual(len(person2["groups"]), 1)
        self.assertEqual(person2["groups"][0]["name"], "Philosophy")

    def test_person_group_rejects_duplicate_name_case_insensitive(self):
        self.db.add_person_group("My School", None)
        with self.assertRaises(ValueError):
            self.db.add_person_group("my school", None)

    def test_add_person_group_parent_name_creates_parent(self):
        cid = self.db.add_person_group_with_parent_options(
            name="Child Only",
            parent_name="Auto Parent",
            description="",
        )
        self.assertIsNotNone(cid)
        allg = self.db.get_all_person_groups()
        names = {g["name"] for g in allg}
        self.assertIn("Auto Parent", names)
        self.assertIn("Child Only", names)
        child = self.db.get_person_group(cid)
        self.assertIsNotNone(child.get("parent_id"))

    def test_add_role_rejects_duplicate_person_and_role(self):
        w_id = self.db.add_work(title="Dup Role Work")
        p_id = self.db.add_person(first_name="Ann", last_name="Author")
        self.db.add_role(p_id, w_id, "Author", order_index=0)
        with self.assertRaises(ValueError):
            self.db.add_role(p_id, w_id, "Author", order_index=1)
        self.assertTrue(self.db.has_work_role(p_id, w_id, "Author"))
        roles = self.db.get_work_roles(w_id)
        self.assertEqual(len(roles), 1)

    def test_group_create_rolls_back_typed_parent(self):
        self.db.add_person_group('A')
        before = self.db.get_all_person_groups()
        with self.assertRaises(ValueError):
            self.db.add_person_group_with_parent_options('A', parent_name='New Parent')
        self.assertEqual(self.db.get_all_person_groups(), before)

    def test_group_update_rolls_back_typed_parent(self):
        a = self.db.add_person_group('A')
        self.db.add_person_group('B')
        before = self.db.get_all_person_groups()
        with self.assertRaises(ValueError):
            self.db.update_person_group(a, {'name': 'B', 'parent_name': 'New Parent'})
        self.assertEqual(self.db.get_all_person_groups(), before)

    def test_group_delete_rolls_back_reparenting(self):
        import sqlite3
        parent = self.db.add_person_group('Parent')
        group = self.db.add_person_group('Group', parent)
        child = self.db.add_person_group('Child', group)
        with self.db.connection() as conn:
            # A persistent trigger is needed because the operation owns its connection.
            conn.execute("""CREATE TRIGGER reject_group_delete BEFORE DELETE ON person_groups
                BEGIN SELECT RAISE(ABORT, 'forced delete failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.delete_person_group(group)
        self.assertIsNotNone(self.db.get_person_group(group))
        self.assertEqual(self.db.get_person_group(child)['parent_id'], group)

    def test_playlist_membership_removal_rolls_back_on_a_failed_second_write(self):
        """A failed canonical membership removal must not leave the membership
        deleted. Offline coherence treats a failed request as a no-op, so a
        partially committed removal would make a stale cache look eligible."""
        import sqlite3
        w_id = self.db.add_work(title="Playlist Rollback Work")
        pl_id = self.db.add_playlist("Rollback Playlist")
        self.db.add_work_to_playlist(pl_id, w_id)
        self.assertEqual([i["id"] for i in self.db.get_playlist(pl_id)["items"]], [w_id])
        before = self.db.get_playlist(pl_id)

        with self.db.connection() as conn:
            # The timestamp bump is the write that runs *after* the membership
            # DELETE, so failing it is exactly the partial-commit window.
            # A persistent trigger is needed because the operation owns its
            # own connection.
            conn.execute(
                """CREATE TRIGGER reject_playlist_touch BEFORE UPDATE ON playlists
                BEGIN SELECT RAISE(ABORT, 'forced timestamp failure'); END"""
            )
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                self.db.remove_work_from_playlist(pl_id, w_id)
        finally:
            with self.db.connection() as conn:
                conn.execute("DROP TRIGGER reject_playlist_touch")

        after = self.db.get_playlist(pl_id)
        self.assertEqual([i["id"] for i in after["items"]], [w_id], "membership was partially removed")
        self.assertEqual(after["updated_at"], before["updated_at"])
        # And the ordinary path still works once the injected failure is gone.
        self.db.remove_work_from_playlist(pl_id, w_id)
        self.assertEqual(self.db.get_playlist(pl_id)["items"], [])

    def _read_catalog_while_writing(self, read, marker, write):
        """Run `read` and attempt `write` as the SELECT named by `marker` begins.

        That SELECT is the second catalog read, so the attempt sits between
        the two statements. ``locked`` means the first read still held the
        database. ``committed`` means the write landed and the returned rows
        are still the snapshot from before that write.
        """
        import sqlite3
        state = {}
        real_get = self.db.get_connection

        def wrapped():
            conn = real_get()

            def trace(sql):
                text = " ".join(str(sql).split()).upper()
                if "outcome" not in state and text.startswith("SELECT") and marker in text:
                    self.assertTrue(conn.in_transaction)
                    other = sqlite3.connect(self.db.db_path, timeout=0)
                    try:
                        other.execute("BEGIN IMMEDIATE")
                        write(other)
                        other.commit()
                        state["outcome"] = "committed"
                    except sqlite3.OperationalError as exc:
                        if "locked" not in str(exc).lower():
                            raise
                        state["outcome"] = "locked"
                    finally:
                        other.close()

            conn.set_trace_callback(trace)
            return conn

        self.db.get_connection = wrapped
        try:
            return read(), state
        finally:
            self.db.get_connection = real_get

    def test_playlist_catalog_reads_are_one_snapshot(self):
        pl_id = self.db.add_playlist("Snapshot Playlist")
        first = self.db.add_work(title="Snapshot One")
        second = self.db.add_work(title="Snapshot Two")
        self.db.add_work_to_playlist(pl_id, first)

        def write(conn):
            conn.execute(
                "INSERT INTO playlist_items (playlist_id, work_id, position) VALUES (?, ?, ?)",
                (pl_id, second, 1),
            )

        rows, state = self._read_catalog_while_writing(
            self.db.get_all_playlists, "FROM PLAYLIST_ITEMS", write
        )
        row = next(item for item in rows if item["id"] == pl_id)
        self.assertIn(state["outcome"], ("locked", "committed"))
        self.assertEqual(row["item_ids"], [first])
        self.assertEqual(row["item_count"], 1)

    def test_persons_catalog_reads_are_one_snapshot(self):
        person_id = self.db.add_person(first_name="Ada", last_name="Snapshot")
        kept = self.db.add_person_group(name="Kept Group")
        added = self.db.add_person_group(name="Added During Read")
        self.db.add_person_to_group(person_id, kept)

        def write(conn):
            conn.execute(
                "INSERT INTO person_group_members (person_id, group_id) VALUES (?, ?)",
                (person_id, added),
            )

        rows, state = self._read_catalog_while_writing(
            self.db.get_all_persons, "FROM PERSON_GROUP_MEMBERS", write
        )
        row = next(item for item in rows if item["id"] == person_id)
        self.assertIn(state["outcome"], ("locked", "committed"))
        self.assertEqual([group["id"] for group in row["groups"]], [kept])
        self.assertEqual(row["assigned_roles"], [])

    def test_person_and_role_operations(self):
        p_id = self.db.add_person(first_name="Jane", last_name="Smith", aliases="J. Smith")
        w_id = self.db.add_work(title="Jane's Book")
        self.db.add_role(p_id, w_id, "Author")
        
        person = self.db.get_person(p_id)
        self.assertIsNotNone(person)
        self.assertEqual(len(person['works']), 1)
        self.assertEqual(person['works'][0]['title'], "Jane's Book")
        self.assertEqual(person['works'][0]['role_type'], "Author")

        work_roles = self.db.get_work_roles(w_id)
        self.assertEqual(len(work_roles), 1)
        self.assertEqual(work_roles[0]['first_name'], "Jane")

        all_persons = self.db.get_all_persons()
        jane_row = next(p for p in all_persons if p["id"] == p_id)
        self.assertEqual(jane_row["assigned_roles"], ["Author"])

    def test_update_person_metadata(self):
        p_id = self.db.add_person(first_name="Karl", last_name="Popper")
        self.db.update_person_metadata(
            p_id,
            {
                "image_url": "https://example.com/p.png",
                "link_wikipedia": "https://en.wikipedia.org/wiki/Karl_Popper",
                "link_stanford_encyclopedia": "https://plato.stanford.edu/entries/popper/",
                "link_iep": "https://iep.utm.edu/popper/",
                "links_other": "https://www.inphoproject.org/\nNote line",
                "birth_date": "1902-07-28",
                "death_date": "1994-09-17",
            },
        )
        person = self.db.get_person(p_id)
        self.assertEqual(person["image_url"], "https://example.com/p.png")
        self.assertIn("wikipedia.org", person["link_wikipedia"])
        self.assertIn("plato.stanford.edu", person["link_stanford_encyclopedia"])
        self.assertIn("iep.utm.edu", person["link_iep"])
        self.assertIn("inphoproject.org", person["links_other"])
        self.assertEqual(person["birth_date"], "1902-07-28")
        self.assertEqual(person["death_date"], "1994-09-17")

        self.db.update_person_metadata(
            p_id, {"birth_date": "1879", "death_date": "-55"}
        )
        person2 = self.db.get_person(p_id)
        self.assertEqual(person2["birth_date"], "1879")
        self.assertEqual(person2["death_date"], "-55")

    def test_bibtex_generation(self):
        w_id = self.db.add_work(
            title="The Theory",
            year="2020",
            publisher="University Press",
            edition="2",
            doc_type="book",
        )
        p_id = self.db.add_person(first_name="Albert", last_name="Einstein")
        self.db.add_role(p_id, w_id, "Author")

        bibtex = self.db.generate_bibtex(w_id)
        self.assertIn("@book{Einstein2020", bibtex)
        self.assertIn("title = {The Theory}", bibtex)
        self.assertIn("author = {Einstein, Albert}", bibtex)
        self.assertIn("edition = {2}", bibtex)

    def test_bibtex_ignores_author_text_without_linked_author(self):
        w_id = self.db.add_work(
            title="Solo Text",
            author_text="Someone, A.",
            year="2015",
            doc_type="book",
        )
        bibtex = self.db.generate_bibtex(w_id)
        self.assertNotIn("author = {Someone", bibtex)
        self.assertIn("@book{Unknown2015", bibtex)

    def test_bibtex_multiple_authors_follow_role_order(self):
        w_id = self.db.add_work(title="Coauthored", year="2021", doc_type="article")
        p_a = self.db.add_person(first_name="Alice", last_name="Alpha")
        p_b = self.db.add_person(first_name="Bob", last_name="Beta")
        self.db.add_role(p_a, w_id, "Author", order_index=0)
        self.db.add_role(p_b, w_id, "Author", order_index=1)
        bibtex = self.db.generate_bibtex(w_id)
        self.assertIn("author = {Alpha, Alice and Beta, Bob}", bibtex)

    def test_bibtex_author_without_given_name_or_mononym(self):
        w_id = self.db.add_work(title="Republic", year="380", doc_type="book")
        p_mono = self.db.add_person(first_name="", last_name="Plato")
        p_full = self.db.add_person(first_name="Theodor", last_name="Adorno")
        self.db.add_role(p_full, w_id, "Author", order_index=0)
        self.db.add_role(p_mono, w_id, "Author", order_index=1)
        bibtex = self.db.generate_bibtex(w_id)
        self.assertIn("author = {Adorno, Theodor and Plato}", bibtex)
        self.assertNotIn("Plato,", bibtex)

        w2 = self.db.add_work(title="Solo", year="2000", doc_type="article")
        p_only_given = self.db.add_person(first_name="Madonna", last_name="")
        self.db.add_role(p_only_given, w2, "Author")
        bib2 = self.db.generate_bibtex(w2)
        self.assertIn("author = {Madonna}", bib2)

    def test_bibtex_includes_translator_role(self):
        w_id = self.db.add_work(title="Translated Work", year="1997", doc_type="book")
        t_id = self.db.add_person(first_name="Mary", last_name="Lamb")
        self.db.add_role(t_id, w_id, "Translator")

        bibtex = self.db.generate_bibtex(w_id)
        self.assertIn("translator = {Lamb, Mary}", bibtex)

        self.db.patch_app_settings({"bibtex_export_fields": {"translator": False}})
        bibtex_no_translator = self.db.generate_bibtex(w_id)
        self.assertNotIn("translator = ", bibtex_no_translator)

    def test_bibtex_includes_intro_foreword_afterword_roles(self):
        w_id = self.db.add_work(title="Critical Edition", year="2003", doc_type="book")
        p_intro = self.db.add_person(first_name="Iris", last_name="Intro")
        p_fore = self.db.add_person(first_name="Frank", last_name="Fore")
        p_after = self.db.add_person(first_name="Alice", last_name="After")
        self.db.add_role(p_intro, w_id, "Introduction")
        self.db.add_role(p_fore, w_id, "Foreword")
        self.db.add_role(p_after, w_id, "Afterword")

        bibtex = self.db.generate_bibtex(w_id)
        self.assertIn("introduction = {Intro, Iris}", bibtex)
        self.assertIn("foreword = {Fore, Frank}", bibtex)
        self.assertIn("afterword = {After, Alice}", bibtex)

        self.db.patch_app_settings({"bibtex_export_fields": {"introduction": False, "foreword": False, "afterword": False}})
        bibtex_trimmed = self.db.generate_bibtex(w_id)
        self.assertNotIn("introduction = ", bibtex_trimmed)
        self.assertNotIn("foreword = ", bibtex_trimmed)
        self.assertNotIn("afterword = ", bibtex_trimmed)

    def test_next_role_order_index(self):
        w_id = self.db.add_work(title="OrderIdx")
        self.assertEqual(self.db.next_role_order_index(w_id), 0)
        p1 = self.db.add_person(first_name="A", last_name="One")
        self.db.add_role(p1, w_id, "Author", order_index=self.db.next_role_order_index(w_id))
        self.assertEqual(self.db.next_role_order_index(w_id), 1)
        p2 = self.db.add_person(first_name="B", last_name="Two")
        self.db.add_role(p2, w_id, "Author", order_index=self.db.next_role_order_index(w_id))
        self.assertEqual(self.db.next_role_order_index(w_id), 2)

    def test_delete_work_role(self):
        w_id = self.db.add_work(title="Unlink Me")
        p_keep = self.db.add_person(first_name="Keep", last_name="Person")
        p_drop = self.db.add_person(first_name="Drop", last_name="Person")
        self.db.add_role(p_keep, w_id, "Author", order_index=0)
        self.db.add_role(p_drop, w_id, "Editor", order_index=1)
        roles_before = self.db.get_work_roles(w_id)
        self.assertEqual(len(roles_before), 2)
        self.assertTrue(self.db.delete_work_role(w_id, p_drop, "Editor", 1))
        self.assertFalse(self.db.delete_work_role(w_id, p_drop, "Editor", 1))
        roles_after = self.db.get_work_roles(w_id)
        self.assertEqual(len(roles_after), 1)
        self.assertEqual(roles_after[0]["id"], p_keep)

    def test_bibtex_location_single_place(self):
        w_id = self.db.add_work(
            title="Local Book",
            year="2019",
            publisher="Press",
            location="Cambridge, UK",
            doc_type="book",
        )
        bibtex = self.db.generate_bibtex(w_id)
        self.assertIn("location = {Cambridge, UK}", bibtex)
        self.assertEqual(bibtex.count("location = "), 1)

    def test_bibtex_location_multiple_places(self):
        w_id = self.db.add_work(
            title="Multi City",
            year="2021",
            location="Paris; Berlin",
            doc_type="book",
        )
        bibtex = self.db.generate_bibtex(w_id)
        self.assertIn("location = {Paris and Berlin}", bibtex)

    def test_bibtex_location_semicolon_normalizes_whitespace(self):
        w_id = self.db.add_work(title="Semi", year="2020", location="  Oxford  ;  New York  ", doc_type="book")
        bibtex = self.db.generate_bibtex(w_id)
        self.assertIn("location = {Oxford and New York}", bibtex)

    def test_add_work_thumb_page_and_private_notes(self):
        w_id = self.db.add_work(
            title="Thumb Note",
            thumb_page=3,
            private_notes="  keep this  ",
        )
        row = self.db.get_work(w_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["thumb_page"], 3)
        self.assertEqual(row["private_notes"], "keep this")

    def test_add_work_thumb_page_invalid_becomes_null(self):
        w_id = self.db.add_work(title="No Thumb", thumb_page="x", private_notes="")
        row = self.db.get_work(w_id)
        self.assertIsNone(row.get("thumb_page"))

    def test_bibtex_misc_from_doc_type(self):
        w_id = self.db.add_work(title="Odd Note", year="1999", doc_type="misc")
        bibtex = self.db.generate_bibtex(w_id)
        self.assertIn("@misc{", bibtex)
        self.assertIn("title = {Odd Note}", bibtex)

    def test_bibtex_heuristic_when_doc_type_empty(self):
        w_id = self.db.add_work(title="Heuristic Book", year="2001", publisher="Pub Co", doc_type="article")
        self.db.execute_query("UPDATE works SET doc_type = NULL WHERE id = ?", (w_id,))
        bibtex = self.db.generate_bibtex(w_id)
        self.assertIn("@book{", bibtex)

    def test_get_works_by_tag_name(self):
        w1 = self.db.add_work(title="Tagged Alpha")
        w_untagged = self.db.add_work(title="Tagged Beta")
        t_id = self.db.add_tag("MyTag", "#ff0000")["id"]
        self.db.add_tag_to_work(w1, t_id)
        rows = self.db.get_works_by_tag_name("mytag")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], w1)
        self.assertNotIn(w_untagged, {r["id"] for r in rows})
        self.assertEqual(len(self.db.get_works_by_tag_name("")), 0)

    def test_add_tag_case_insensitive_returns_same_id(self):
        r1 = self.db.add_tag("foo", "#111111")
        r2 = self.db.add_tag("FOO", "#222222")
        self.assertEqual(r1["id"], r2["id"])
        self.assertFalse(r1.get("existed"))
        self.assertTrue(r2.get("existed"))
        rows = self.db.execute_query("SELECT id FROM tags WHERE LOWER(name) = 'foo'")
        self.assertEqual(len(rows), 1)

    def test_get_works_by_tag_alias(self):
        w1 = self.db.add_work(title="AliasTagged")
        tid = self.db.add_tag("Philosophy", "#000000")["id"]
        self.db.add_tag_alias(tid, "Philosophie")
        self.db.add_tag_to_work(w1, tid)
        rows = self.db.get_works_by_tag_name("philosophie")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], w1)

    def test_delete_tag_removes_tag_and_aliases(self):
        w1 = self.db.add_work(title="DeleteTagWork")
        tid = self.db.add_tag("TaggedForDelete", "#000000")["id"]
        self.db.add_tag_alias(tid, "AltName")
        self.db.add_tag_to_work(w1, tid)
        result = self.db.delete_tag(tid)
        self.assertEqual(result.get("status"), "deleted")
        self.assertNotIn("promoted", result)
        self.assertFalse(self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tid,)))
        al = self.db.execute_query("SELECT * FROM tag_aliases WHERE tag_id = ?", (tid,))
        self.assertEqual(len(al), 0)
        self.assertEqual(len(self.db.get_work_tags(w1)), 0)
        self.assertFalse(
            self.db.execute_query(
                "SELECT id FROM tags WHERE LOWER(name) = LOWER(?)", ("AltName",)
            )
        )

    def test_get_all_tags_includes_aliases_array(self):
        tid = self.db.add_tag("Canon", "#abc")["id"]
        self.db.add_tag_alias(tid, "AliasOne")
        tags = self.db.get_all_tags()
        row = next(t for t in tags if t["id"] == tid)
        self.assertEqual(row.get("aliases"), ["AliasOne"])

    def test_merge_tags_moves_work_and_folder_links(self):
        w = self.db.add_work(title="MergeW")
        f = self.db.add_folder(title="MergeF", description="")
        src = self.db.add_tag("MergeSource", "#111")["id"]
        tgt = self.db.add_tag("MergeTarget", "#222")["id"]
        self.db.add_tag_to_work(w, src)
        self.db.add_tag_to_folder(f, src)
        out = self.db.merge_tags_into(src, tgt)
        self.assertEqual(out["canonical_tag_id"], tgt)
        self.assertEqual(out["canonical_name"], "MergeTarget")
        self.assertFalse(self.db.execute_query("SELECT id FROM tags WHERE id = ?", (src,)))
        wtags = self.db.get_work_tags(w)
        self.assertEqual(len(wtags), 1)
        self.assertEqual(wtags[0]["id"], tgt)
        fts = self.db.get_folder_tags(f)
        self.assertEqual(len(fts), 1)
        self.assertEqual(fts[0]["id"], tgt)
        self.assertEqual(self.db.resolve_tag_id_by_label("MergeSource"), tgt)

    def test_merge_tags_dedupes_work_with_both_tags(self):
        w = self.db.add_work(title="MergeW2")
        src = self.db.add_tag("MergeS", "#1")["id"]
        tgt = self.db.add_tag("MergeT", "#2")["id"]
        self.db.add_tag_to_work(w, src)
        self.db.add_tag_to_work(w, tgt)
        self.db.merge_tags_into(src, tgt)
        rows = self.db.execute_query("SELECT tag_id FROM work_tags WHERE work_id = ?", (w,))
        self.assertEqual({r["tag_id"] for r in rows}, {tgt})

    def test_merge_tags_migrates_source_aliases(self):
        src = self.db.add_tag("MSrc", "#1")["id"]
        tgt = self.db.add_tag("MTgt", "#2")["id"]
        self.db.add_tag_alias(src, "MigratedAlias")
        self.db.merge_tags_into(src, tgt)
        tags = self.db.get_all_tags()
        trow = next(t for t in tags if t["id"] == tgt)
        self.assertIn("MigratedAlias", trow.get("aliases", []))

    def test_merge_tags_drops_source_alias_matching_target_canonical(self):
        src = self.db.add_tag("MSrc2", "#1")["id"]
        tgt = self.db.add_tag("CanonicalTgt", "#2")["id"]
        # Bypass add_tag_alias: same text as target canonical (nocase) is invalid via API but may exist in data.
        aid = self.db.generate_id("L")
        self.db.execute_query(
            "INSERT INTO tag_aliases (id, tag_id, alias) VALUES (?, ?, ?)",
            (aid, src, "canonicaltgt"),
        )
        self.db.merge_tags_into(src, tgt)
        rows = self.db.execute_query(
            "SELECT LOWER(alias) AS a FROM tag_aliases WHERE tag_id = ?", (tgt,)
        )
        lowered = {r["a"] for r in rows}
        self.assertNotIn("canonicaltgt", lowered)

    def test_merge_tags_self_raises(self):
        tid = self.db.add_tag("Solo", "#fff")["id"]
        with self.assertRaises(ValueError):
            self.db.merge_tags_into(tid, tid)

    def test_merge_tags_missing_raises(self):
        tgt = self.db.add_tag("OnlyTgt", "#eee")["id"]
        with self.assertRaises(ValueError):
            self.db.merge_tags_into("T-BADBADBA", tgt)

    def test_remove_last_work_tag_keeps_the_tag_in_the_catalog(self):
        """Tag identity is persistent: removing the last relationship is not a
        request to delete the Tag. PRKS used to garbage-collect it here."""
        w = self.db.add_work(title="T")
        tid = self.db.add_tag("Lonely", "#111")["id"]
        self.db.add_tag_to_work(w, tid)
        self.db.remove_tag_from_work(w, tid)
        rows = self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tid,))
        self.assertEqual(len(rows), 1)
        # ... and the relationship really is gone.
        self.assertEqual(self.db.get_work_tags(w), [])

    def test_remove_tag_keeps_tag_when_still_on_another_work(self):
        w1 = self.db.add_work(title="A")
        w2 = self.db.add_work(title="B")
        tid = self.db.add_tag("Shared", "#222")["id"]
        self.db.add_tag_to_work(w1, tid)
        self.db.add_tag_to_work(w2, tid)
        self.db.remove_tag_from_work(w1, tid)
        rows = self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tid,))
        self.assertEqual(len(rows), 1)

    def test_delete_work_keeps_tags_that_were_only_linked_to_it(self):
        """Deleting a Work removes its relationships, never Tag identity."""
        w = self.db.add_work(title="Gone")
        tid = self.db.add_tag("OnlyHere", "#333")["id"]
        self.db.add_tag_to_work(w, tid)
        self.db.delete_work_record(w)
        rows = self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tid,))
        self.assertEqual(len(rows), 1)
        links = self.db.execute_query(
            "SELECT 1 FROM work_tags WHERE tag_id = ?", (tid,)
        )
        self.assertEqual(links, [])

    def test_get_recently_added_works_order(self):
        w_old = self.db.add_work(title="Older added")
        w_new = self.db.add_work(title="Newer added")
        self.db.execute_query(
            "UPDATE works SET created_at = '2020-01-01 00:00:00' WHERE id = ?", (w_old,)
        )
        self.db.execute_query(
            "UPDATE works SET created_at = '2025-06-01 00:00:00' WHERE id = ?", (w_new,)
        )
        rows = self.db.get_recently_added_works(limit=10)
        ids = [r["id"] for r in rows]
        self.assertGreaterEqual(len(ids), 2)
        self.assertEqual(ids[0], w_new)
        self.assertIn(w_old, ids)

    def test_safe_pdf_path_rejects_path_shaped_input(self):
        pdfs = tempfile.mkdtemp()
        try:
            for unsafe in (
                "..",
                "%2e%2e",
                "../keep.pdf",
                "%2e%2e%2fkeep.pdf",
                "nested/keep.pdf",
                "nested%2Fkeep.pdf",
                r"nested\\keep.pdf",
                "bad\x00name.pdf",
                " victim.pdf",
                "victim.pdf ",
            ):
                with self.subTest(unsafe=unsafe):
                    self.assertIsNone(safe_pdf_path_under_dir(pdfs, unsafe))
            safe = os.path.join(pdfs, "keep.pdf")
            with open(safe, "w", encoding="utf-8") as f:
                f.write("x")
            resolved = safe_pdf_path_under_dir(pdfs, "keep.pdf")
            self.assertEqual(resolved, os.path.realpath(safe))
        finally:
            shutil.rmtree(pdfs)

    def test_safe_processing_path_rejects_path_shaped_input_everywhere(self):
        root = tempfile.mkdtemp()
        try:
            for unsafe in (
                "",
                ".",
                "..",
                "../sample.pdf",
                "batch/../sample.pdf",
                "batch/sample.pdf/..",
                "/sample.pdf",
                "//server/share/sample.pdf",
                r"\\server\share\sample.pdf",
                "\\sample.pdf",
                "batch//sample.pdf",
                "batch/./sample.pdf",
                "batch/sample.pdf/",
                "bad\x00name.pdf",
            ):
                with self.subTest(unsafe=unsafe):
                    self.assertIsNone(safe_processing_path_under_dir(root, unsafe))
        finally:
            shutil.rmtree(root)

    def test_safe_processing_path_resolves_exact_relative_paths(self):
        root = tempfile.mkdtemp()
        try:
            expected = os.path.realpath(os.path.join(root, "batch", "sample.pdf"))
            self.assertEqual(
                safe_processing_path_under_dir(root, "batch/sample.pdf"),
                expected,
            )
            # Leading whitespace is part of the directory name, not noise to trim.
            self.assertEqual(
                safe_processing_path_under_dir(root, " batch/sample.pdf"),
                os.path.realpath(os.path.join(root, " batch", "sample.pdf")),
            )
            # A percent-encoded separator is a literal filename character here:
            # rel_path is a filesystem path, never a URL, so nothing is decoded.
            self.assertEqual(
                safe_processing_path_under_dir(root, "batch%2Fsample.pdf"),
                os.path.realpath(os.path.join(root, "batch%2Fsample.pdf")),
            )
        finally:
            shutil.rmtree(root)

    @unittest.skipIf(os.name == "nt", "asserts POSIX filesystem name semantics")
    def test_safe_processing_path_keeps_posix_names_that_look_like_windows_paths(self):
        root = tempfile.mkdtemp()
        try:
            # On POSIX these are ordinary filename characters. Rejecting them
            # would make discovery advertise files that preview cannot serve and
            # that import deletes as "no longer present".
            for name in (
                "C:sample.pdf",
                r"C:\sample.pdf",
                r"batch\sample.pdf",
                "batch/C:sample.pdf",
                "batch/D:other.pdf",
                "batch/sub/C:deep.pdf",
            ):
                with self.subTest(name=name):
                    self.assertEqual(
                        safe_processing_path_under_dir(
                            root, name, windows_semantics=False
                        ),
                        os.path.realpath(os.path.join(root, name)),
                    )
        finally:
            shutil.rmtree(root)

    # Runs on every host: "\\" and "C:" only mean something on Windows.
    def test_safe_processing_path_rejects_windows_drive_and_separator_spellings(self):
        root = tempfile.mkdtemp()
        try:
            for unsafe in (
                "C:sample.pdf",
                "D:sample.pdf",
                r"C:\sample.pdf",
                r"batch\sample.pdf",
                r"batch\..\..\sample.pdf",
                # A drive qualifier anchors to a SEGMENT, not to the start of
                # the string. joinpath() re-anchors on a later one: on Windows
                # ("batch", "C:sample.pdf") resolves to <root>/batch/sample.pdf,
                # silently targeting different bytes that containment cannot
                # catch because the result stays beneath the root.
                "batch/C:sample.pdf",
                "batch/D:other.pdf",
                "batch/sub/C:deep.pdf",
                "batch/c:lower.pdf",
                "../sample.pdf",
                "/sample.pdf",
                "batch//sample.pdf",
            ):
                with self.subTest(unsafe=unsafe):
                    self.assertIsNone(
                        safe_processing_path_under_dir(
                            root, unsafe, windows_semantics=True
                        )
                    )
        finally:
            shutil.rmtree(root)

    def _assert_posix_child(self, actual, root, *parts):
        """Assert a POSIX acceptance result, skipping the check on Windows.

        ``windows_semantics=False`` disables drive-prefix *validation* only; the
        join still uses native ``Path`` semantics. On Windows a drive-looking
        segment re-anchors regardless, so the helper returns None (or another
        path) and there is no POSIX expectation to assert.
        """
        if os.name == "nt":
            return
        self.assertEqual(actual, os.path.realpath(os.path.join(root, *parts)))

    def test_resolved_child_path_rejects_a_drive_qualified_segment_at_any_depth(self):
        """The rule belongs to the join, so it is tested on the primitive.

        ``joinpath()`` re-anchors on a drive-qualified component wherever it
        appears. Same drive silently rewrites the target and stays beneath the
        root, so containment cannot catch it; a different drive leaves the root
        altogether.
        """
        root = tempfile.mkdtemp()
        try:
            for parts in (
                ("C:sample.pdf",),
                ("batch", "C:sample.pdf"),
                ("batch", "D:other.pdf"),
                ("batch", "sub", "c:deep.pdf"),
            ):
                with self.subTest(parts=parts):
                    self.assertIsNone(
                        storage_paths.resolved_child_path(
                            root, *parts, windows_semantics=True
                        )
                    )
                    # POSIX keeps them: ordinary filename characters there.
                    self._assert_posix_child(
                        storage_paths.resolved_child_path(
                            root, *parts, windows_semantics=False
                        ),
                        root,
                        *parts,
                    )
            self.assertEqual(
                storage_paths.resolved_child_path(
                    root, "batch", "plain.pdf", windows_semantics=True
                ),
                os.path.realpath(os.path.join(root, "batch", "plain.pdf")),
            )
        finally:
            shutil.rmtree(root)

    def test_safe_pdf_path_rejects_a_drive_qualified_filename(self):
        """The managed-PDF validator shares the hazard.

        ``managed_pdf_filename("/api/pdfs/C:foo.pdf")`` returns ``C:foo.pdf``,
        and on Windows joining that onto pdfs/ resolves to ``pdfs/foo.pdf`` --
        a different managed PDF, silently, and still inside the root.
        """
        pdfs = tempfile.mkdtemp()
        try:
            for name in ("C:foo.pdf", "D:foo.pdf", "c:foo.pdf"):
                with self.subTest(name=name):
                    self.assertIsNone(
                        safe_pdf_path_under_dir(pdfs, name, windows_semantics=True)
                    )
                    self._assert_posix_child(
                        safe_pdf_path_under_dir(pdfs, name, windows_semantics=False),
                        pdfs,
                        name,
                    )
            self.assertEqual(
                safe_pdf_path_under_dir(pdfs, "keep.pdf", windows_semantics=True),
                os.path.realpath(os.path.join(pdfs, "keep.pdf")),
            )
        finally:
            shutil.rmtree(pdfs)

    @unittest.skipIf(os.name == "nt", "POSIX filename semantics")
    def test_processing_backslash_name_survives_rescan_preview_and_import(self):
        """Discovery, reconciliation, preview and import must agree byte-for-byte.

        A POSIX filename containing a backslash used to be folded to '/' by some
        consumers and not others: rescans re-inserted the row forever, and
        preview resolved a different (or missing) file than import used.
        """
        processing_root = self.storage.processing_dir
        odd_name = "weird\\name.pdf"
        decoy_dir = os.path.join(processing_root, "weird")
        os.makedirs(decoy_dir, exist_ok=True)
        with open(os.path.join(processing_root, odd_name), "wb") as f:
            f.write(b"%PDF-1.4 odd")
        with open(os.path.join(decoy_dir, "name.pdf"), "wb") as f:
            f.write(b"%PDF-1.4 decoy")

        staged = self.db.scan_processing_files()
        by_rel = {row["rel_path"]: row for row in staged}
        self.assertIn(odd_name, by_rel)
        self.assertIn("weird/name.pdf", by_rel)
        self.assertEqual(by_rel[odd_name]["folder"], "/")

        # A second scan must recognise the row it just wrote, not duplicate it.
        staged_again = self.db.scan_processing_files()
        self.assertEqual(
            len([r for r in staged_again if r["rel_path"] == odd_name]), 1
        )

        row_id = by_rel[odd_name]["id"]
        preview = self.db.get_processing_file_pdf_path(row_id)
        self.assertEqual(preview, os.path.realpath(os.path.join(processing_root, odd_name)))
        with open(preview, "rb") as f:
            self.assertEqual(f.read(), b"%PDF-1.4 odd")

        self.db.import_processing_file(row_id)
        self.assertFalse(os.path.exists(os.path.join(processing_root, odd_name)))
        self.assertTrue(os.path.isfile(os.path.join(decoy_dir, "name.pdf")))

    def test_processing_preview_does_not_strip_into_a_different_file(self):
        """A stored leading space must not make preview serve the unpadded file."""
        processing_root = self.storage.processing_dir
        padded_dir = os.path.join(processing_root, " batch")
        plain_dir = os.path.join(processing_root, "batch")
        os.makedirs(padded_dir, exist_ok=True)
        os.makedirs(plain_dir, exist_ok=True)
        with open(os.path.join(padded_dir, "sample.pdf"), "wb") as f:
            f.write(b"%PDF-1.4 padded")
        with open(os.path.join(plain_dir, "sample.pdf"), "wb") as f:
            f.write(b"%PDF-1.4 plain")

        staged = self.db.scan_processing_files()
        by_rel = {row["rel_path"]: row for row in staged}
        self.assertIn(" batch/sample.pdf", by_rel)

        preview = self.db.get_processing_file_pdf_path(by_rel[" batch/sample.pdf"]["id"])
        with open(preview, "rb") as f:
            self.assertEqual(f.read(), b"%PDF-1.4 padded")

    def test_delete_work_with_unsafe_pdf_path_still_removes_row(self):
        w_id = self.db.add_work(title="Unsafe fp", file_path="/api/pdfs/..")
        self.db.delete_work_record(w_id)
        self.assertIsNone(self.db.get_work(w_id))

    def test_processing_files_scan_update_and_import_move_flow(self):
        processing_root = self.storage.processing_dir
        nested = os.path.join(processing_root, "batch_a", "batch_b")
        os.makedirs(nested, exist_ok=True)
        pdf_path = os.path.join(nested, "sample.pdf")
        txt_path = os.path.join(nested, "skip.txt")
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4\n%DBTEST\n%%EOF\n")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("ignore")
        person_id = self.db.add_person(first_name="Role", last_name="Author")
        folder_id = self.db.add_folder(title="Processing Target", description="")
        tag_row = self.db.add_tag("Inbox Tag", "#6d6cf7")
        tag_id = tag_row["id"] if isinstance(tag_row, dict) else tag_row
        staged = self.db.scan_processing_files()
        self.assertEqual(len(staged), 1)
        row = staged[0]
        self.assertEqual(row["rel_path"], "batch_a/batch_b/sample.pdf")
        self.assertEqual(self.db.search_works("Imported from inbox"), [])
        self.db.update_processing_file(
            row["id"],
            {
                "title": "Imported from inbox",
                "status_draft": "Planned",
                "doc_type": "book",
                "target_folder_id": folder_id,
                "roles": [{"person_id": person_id, "role_type": "Author"}],
                "tags": [{"id": tag_id, "name": "Inbox Tag"}],
            },
        )
        updated = self.db.get_processing_file(row["id"])
        self.assertEqual(len(updated.get("tags") or []), 1)
        self.assertEqual(updated["tags"][0]["id"], tag_id)
        out = self.db.import_processing_file(row["id"])
        self.assertIn("work_id", out)
        self.assertFalse(os.path.exists(pdf_path))
        imported_work = self.db.get_work(out["work_id"])
        self.assertEqual(imported_work.get("folder_id"), folder_id)
        self.assertEqual(imported_work["title"], "Imported from inbox")
        self.assertEqual(imported_work["status"], "Planned")
        self.assertEqual(imported_work["doc_type"], "book")
        self.assertTrue(any(r.get("id") == person_id and r.get("role_type") == "Author" for r in imported_work.get("roles", [])))
        work_tags = self.db.get_work_tags(out["work_id"])
        self.assertTrue(any(t.get("id") == tag_id for t in work_tags))
        self.assertTrue(str(imported_work.get("file_path") or "").startswith("/api/pdfs/"))
        pdf_name = imported_work["file_path"].split("/")[-1]
        moved_abs = os.path.join(self.storage.pdfs_dir, pdf_name)
        self.assertTrue(os.path.isfile(moved_abs))
        self.assertTrue(any(r["id"] == out["work_id"] for r in self.db.search_works("Imported from inbox")))
        self.assertEqual(self.db.get_processing_files(), [])

    def test_import_processing_prunes_empty_nested_dirs(self):
        """After import removes inbox PDF, empty ancestor dirs under for_processing/ removed."""
        processing_root = self.storage.processing_dir
        nested = os.path.join(processing_root, "a", "b", "c")
        os.makedirs(nested, exist_ok=True)
        pdf_path = os.path.join(nested, "solo.pdf")
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4\n%PRUNE\n%%EOF\n")
        folder_id = self.db.add_folder(title="PruneTarget", description="")
        staged = self.db.scan_processing_files()
        self.assertEqual(len(staged), 1)
        row = staged[0]
        self.db.update_processing_file(
            row["id"],
            {"title": "Pruned path work", "target_folder_id": folder_id},
        )
        self.db.import_processing_file(row["id"])
        self.assertFalse(os.path.exists(pdf_path))
        self.assertFalse(os.path.isdir(nested))
        self.assertFalse(os.path.isdir(os.path.join(processing_root, "a", "b")))
        self.assertFalse(os.path.isdir(os.path.join(processing_root, "a")))
        self.assertTrue(os.path.isdir(processing_root))
        prune_empty_processing_parent_dirs(processing_root, "/tmp/definitely_not_under_inbox")
        self.assertTrue(os.path.isdir(processing_root))

    def test_import_processing_file_keeps_inbox_when_add_work_fails(self):
        """Copy-then-commit: inbox PDF must survive add_work failure (no row + no ghost dest)."""
        processing_root = self.storage.processing_dir
        pdf_path = os.path.join(processing_root, "fragile.pdf")
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4\n%FAILTEST\n%%EOF\n")
        staged = self.db.scan_processing_files()
        self.assertEqual(len(staged), 1)
        row = staged[0]
        with patch.object(self.db, "add_work", side_effect=RuntimeError("simulated DB failure")):
            with self.assertRaises(ValueError) as ctx:
                self.db.import_processing_file(row["id"])
            self.assertIn("simulated DB failure", str(ctx.exception))
        self.assertTrue(os.path.isfile(pdf_path), "inbox PDF must remain after failed import")
        leftovers = [n for n in os.listdir(self.storage.pdfs_dir) if n.lower().endswith(".pdf")]
        self.assertEqual(leftovers, [])
        rows = self.db.execute_query(
            "SELECT status, last_error FROM processing_files WHERE id = ?",
            (row["id"],),
        )
        self.assertTrue(rows)
        self.assertEqual(rows[0]["status"], "error")
        self.assertIn("simulated DB failure", rows[0]["last_error"] or "")

    def test_processing_files_rescan_deletes_stale(self):
        processing_root = self.storage.processing_dir
        pdf_path = os.path.join(processing_root, "gone.pdf")
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4\n%MISSING\n%%EOF\n")
        staged = self.db.scan_processing_files()
        self.assertEqual(len(staged), 1)
        os.remove(pdf_path)
        staged2 = self.db.scan_processing_files()
        self.assertEqual(staged2, [])

    def test_ensure_default_uncategorized_folder_id_idempotent(self):
        a1 = self.db.ensure_default_uncategorized_folder_id()
        a2 = self.db.ensure_default_uncategorized_folder_id()
        self.assertEqual(a1, a2)
        row = self.db.execute_query("SELECT title, parent_id FROM folders WHERE id = ?", (a1,))
        self.assertTrue(row)
        self.assertEqual(row[0]["title"], "Uncategorized")
        self.assertIsNone(row[0]["parent_id"])

    def test_get_all_folders_hides_empty_uncategorized(self):
        unc = self.db.ensure_default_uncategorized_folder_id()
        titles = [f["title"] for f in self.db.get_all_folders()]
        self.assertNotIn("Uncategorized", titles)
        self.assertIsNotNone(self.db.get_folder(unc))
        w = self.db.add_work(title="Only in uncategorized")
        self.db.add_work_to_folder(unc, w)
        titles2 = [f["title"] for f in self.db.get_all_folders()]
        self.assertIn("Uncategorized", titles2)

    def test_import_processing_without_target_folder_uses_uncategorized(self):
        processing_root = self.storage.processing_dir
        pdf_path = os.path.join(processing_root, "free.pdf")
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4\n%UNCAT\n%%EOF\n")
        staged = self.db.scan_processing_files()
        self.assertEqual(len(staged), 1)
        row = staged[0]
        self.db.update_processing_file(
            row["id"],
            {"title": "No target folder import", "status_draft": "Planned"},
        )
        out = self.db.import_processing_file(row["id"])
        w = self.db.get_work(out["work_id"])
        self.assertEqual(w.get("folder_title"), "Uncategorized")

    def _count_execute_query(self, fn):
        original = self.db.execute_query
        counts = {"n": 0}

        def wrapped(*args, **kwargs):
            counts["n"] += 1
            return original(*args, **kwargs)

        with patch.object(self.db, "execute_query", side_effect=wrapped):
            result = fn()
        return result, counts["n"]

    def test_processing_files_list_uses_constant_query_count(self):
        processing_root = self.storage.processing_dir
        person_a = self.db.add_person(first_name="Ada", last_name="Role")
        person_b = self.db.add_person(first_name="Bea", last_name="Role")
        tag_alpha = self.db.add_tag("alpha", "#111111")
        tag_zed = self.db.add_tag("Zed", "#222222")
        tag_alpha_id = tag_alpha["id"] if isinstance(tag_alpha, dict) else tag_alpha
        tag_zed_id = tag_zed["id"] if isinstance(tag_zed, dict) else tag_zed
        n_files = 60
        for i in range(n_files):
            name = f"inbox_{i:03d}.pdf"
            pdf_path = os.path.join(processing_root, name)
            with open(pdf_path, "wb") as handle:
                handle.write(b"%PDF-1.4\n%N1\n%%EOF\n")
        staged = self.db.scan_processing_files()
        self.assertEqual(len(staged), n_files)
        mixed = staged[0]
        empty = staged[1]
        self.db.update_processing_file(
            mixed["id"],
            {
                "roles": [
                    {"person_id": person_b, "role_type": "Editor"},
                    {"person_id": person_a, "role_type": "Author"},
                ],
                "tags": [{"id": tag_zed_id}, {"id": tag_alpha_id}],
            },
        )
        listed, query_count = self._count_execute_query(
            lambda: self.db.get_processing_files(include_imported=False)
        )
        self.assertEqual(query_count, 3)
        self.assertEqual(len(listed), n_files)
        by_id = {row["id"]: row for row in listed}
        mixed_pub = by_id[mixed["id"]]
        empty_pub = by_id[empty["id"]]
        self.assertEqual([r["person_id"] for r in mixed_pub["roles"]], [person_b, person_a])
        self.assertEqual([r["role_type"] for r in mixed_pub["roles"]], ["Editor", "Author"])
        self.assertEqual([t["id"] for t in mixed_pub["tags"]], [tag_alpha_id, tag_zed_id])
        self.assertEqual(empty_pub["roles"], [])
        self.assertEqual(empty_pub["tags"], [])
        empty_pub["roles"].append({"person_id": "x"})
        self.assertEqual(by_id[mixed["id"]]["roles"][0]["person_id"], person_b)
        single = self.db.get_processing_file(mixed["id"])
        self.assertEqual(single["roles"], mixed_pub["roles"])
        self.assertEqual(single["tags"], mixed_pub["tags"])

        for i in range(n_files, n_files + 40):
            name = f"inbox_{i:03d}.pdf"
            with open(os.path.join(processing_root, name), "wb") as handle:
                handle.write(b"%PDF-1.4\n%N1\n%%EOF\n")
        self.db.scan_processing_files()
        _listed2, query_count2 = self._count_execute_query(
            lambda: self.db.get_processing_files(include_imported=False)
        )
        self.assertEqual(query_count2, 3)
        self.assertEqual(len(_listed2), n_files + 40)

    def test_processing_role_order_index_then_rowid(self):
        processing_root = self.storage.processing_dir
        pdf_path = os.path.join(processing_root, "order.pdf")
        with open(pdf_path, "wb") as handle:
            handle.write(b"%PDF-1.4\n%ORD\n%%EOF\n")
        staged = self.db.scan_processing_files()
        pfid = staged[0]["id"]
        p1 = self.db.add_person(first_name="First", last_name="Inserted")
        p2 = self.db.add_person(first_name="Second", last_name="Inserted")
        p3 = self.db.add_person(first_name="Third", last_name="Inserted")
        with self.db.connection() as conn:
            conn.execute(
                """
                INSERT INTO processing_file_roles
                    (processing_file_id, person_id, role_type, order_index)
                VALUES (?, ?, 'Author', 5)
                """,
                (pfid, p1),
            )
            conn.execute(
                """
                INSERT INTO processing_file_roles
                    (processing_file_id, person_id, role_type, order_index)
                VALUES (?, ?, 'Author', 1)
                """,
                (pfid, p2),
            )
            conn.execute(
                """
                INSERT INTO processing_file_roles
                    (processing_file_id, person_id, role_type, order_index)
                VALUES (?, ?, 'Editor', 1)
                """,
                (pfid, p3),
            )
            conn.commit()
        row = self.db.get_processing_file(pfid)
        self.assertEqual(
            [(r["person_id"], r["order_index"]) for r in row["roles"]],
            [(p2, 1), (p3, 1), (p1, 5)],
        )
        listed = self.db.get_processing_files()
        self.assertEqual(
            [(r["person_id"], r["order_index"]) for r in listed[0]["roles"]],
            [(p2, 1), (p3, 1), (p1, 5)],
        )

    def test_processing_scan_keeps_existing_ids_and_imported_rows(self):
        processing_root = self.storage.processing_dir
        pdf_path = os.path.join(processing_root, "keep.pdf")
        with open(pdf_path, "wb") as handle:
            handle.write(b"%PDF-1.4\n%KEEP\n%%EOF\n")
        first = self.db.scan_processing_files()
        keep_id = first[0]["id"]
        with patch.object(self.db, "generate_id", wraps=self.db.generate_id) as gen:
            second = self.db.scan_processing_files()
        self.assertEqual(gen.call_count, 0)
        self.assertEqual(second[0]["id"], keep_id)
        self.db.execute_query(
            """
            INSERT INTO processing_files (id, rel_path, abs_path, filename, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("PF-KEEPIMP", "missing-imported.pdf", "/absent", "missing-imported.pdf", "imported"),
        )
        os.remove(pdf_path)
        after = self.db.scan_processing_files()
        self.assertEqual(after, [])
        kept = self.db.execute_query(
            "SELECT status FROM processing_files WHERE id = ?",
            ("PF-KEEPIMP",),
        )
        self.assertEqual(kept[0]["status"], "imported")

    def test_processing_scan_avoids_not_in_and_handles_many_files(self):
        processing_root = self.storage.processing_dir
        n_files = 1200
        for i in range(n_files):
            with open(os.path.join(processing_root, f"bulk_{i:04d}.pdf"), "wb") as handle:
                handle.write(b"%PDF-1.4\n%%EOF\n")
        sqls: list[str] = []
        real_get = self.db.get_connection

        def wrapped_get():
            conn = real_get()
            conn.set_trace_callback(lambda sql: sqls.append(str(sql)))
            return conn

        with patch.object(self.db, "get_connection", side_effect=wrapped_get):
            staged = self.db.scan_processing_files()
        self.assertEqual(len(staged), n_files)
        blob = "\n".join(sqls).upper()
        self.assertNotIn("NOT IN", blob)
        os.remove(os.path.join(processing_root, "bulk_0000.pdf"))
        staged2 = self.db.scan_processing_files()
        self.assertEqual(len(staged2), n_files - 1)

    def test_processing_scan_ignores_symlink_escape(self):
        outside = os.path.join(self._tmpdir, "outside.pdf")
        with open(outside, "wb") as handle:
            handle.write(b"%PDF-1.4\n%%EOF\n")
        link = os.path.join(self.storage.processing_dir, "escape.pdf")
        try:
            os.symlink(outside, link)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        staged = self.db.scan_processing_files()
        self.assertEqual(staged, [])
        kept = self.db.execute_query("SELECT id FROM processing_files")
        self.assertEqual(kept, [])

    def _folder_ids_for_work(self, work_id):
        rows = self.db.execute_query(
            "SELECT folder_id FROM folder_files WHERE work_id = ? ORDER BY folder_id",
            (work_id,),
        )
        return [r["folder_id"] for r in rows]

    def _tag_ids_for_work(self, work_id):
        return sorted(t["id"] for t in self.db.get_work_tags(work_id))

    def test_bulk_set_status_updates_selected_only(self):
        a = self.db.add_work(title="A", status="Paused")
        b = self.db.add_work(title="B", status="Paused")
        c = self.db.add_work(title="C", status="Paused")
        before_c = self.db.get_work(c)["updated_at"]
        result = self.db.bulk_update_works(
            {"work_ids": [a, b], "action": "set_status", "status": "Completed"}
        )
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["action"], "set_status")
        self.assertEqual(result["requested"], 2)
        self.assertEqual(result["updated"], 2)
        self.assertEqual(self.db.get_work(a)["status"], "Completed")
        self.assertEqual(self.db.get_work(b)["status"], "Completed")
        self.assertEqual(self.db.get_work(c)["status"], "Paused")
        self.assertEqual(self.db.get_work(c)["updated_at"], before_c)
        self.assertGreaterEqual(self.db.get_work(a)["updated_at"], before_c)

    def test_bulk_set_status_rejects_invalid_values(self):
        w = self.db.add_work(title="S", status="Planned")
        for bad in ("Finished", "Read", ""):
            with self.assertRaises(BulkWorkError) as ctx:
                self.db.bulk_update_works(
                    {"work_ids": [w], "action": "set_status", "status": bad}
                )
            self.assertEqual(ctx.exception.http_status, 400)
            self.assertEqual(self.db.get_work(w)["status"], "Planned")

    def test_bulk_move_folder_and_clear(self):
        fx = self.db.add_folder(title="X", description="")
        fy = self.db.add_folder(title="Y", description="")
        a = self.db.add_work(title="A")
        b = self.db.add_work(title="B")
        c = self.db.add_work(title="C")
        self.db.move_work_to_folder(a, fx)
        self.db.move_work_to_folder(b, fx)
        self.db.move_work_to_folder(c, fx)
        result = self.db.bulk_update_works(
            {"work_ids": [a, b], "action": "move_folder", "folder_id": fy}
        )
        self.assertEqual(result["updated"], 2)
        self.assertEqual(self._folder_ids_for_work(a), [fy])
        self.assertEqual(self._folder_ids_for_work(b), [fy])
        self.assertEqual(self._folder_ids_for_work(c), [fx])
        self.db.bulk_update_works(
            {"work_ids": [a, b], "action": "move_folder", "folder_id": None}
        )
        self.assertEqual(self._folder_ids_for_work(a), [])
        self.assertEqual(self._folder_ids_for_work(b), [])
        self.assertEqual(self._folder_ids_for_work(c), [fx])

    def test_bulk_tags_add_remove_idempotent(self):
        a = self.db.add_work(title="A")
        b = self.db.add_work(title="B")
        c = self.db.add_work(title="C")
        t1 = self.db.add_tag("T1")["id"]
        t2 = self.db.add_tag("T2")["id"]
        self.db.add_tag_to_work(a, t1)
        self.db.add_tag_to_work(b, t2)
        self.db.add_tag_to_work(c, t1)
        self.db.bulk_update_works(
            {"work_ids": [a, b], "action": "add_tags", "tag_ids": [t1, t2]}
        )
        self.assertEqual(self._tag_ids_for_work(a), sorted([t1, t2]))
        self.assertEqual(self._tag_ids_for_work(b), sorted([t1, t2]))
        self.assertEqual(self._tag_ids_for_work(c), [t1])
        rels = self.db.execute_query("SELECT work_id, tag_id FROM work_tags")
        pairs = [(r["work_id"], r["tag_id"]) for r in rels]
        self.assertEqual(len(pairs), len(set(pairs)))
        self.db.bulk_update_works(
            {"work_ids": [a, b], "action": "remove_tags", "tag_ids": [t1]}
        )
        self.assertEqual(self._tag_ids_for_work(a), [t2])
        self.assertEqual(self._tag_ids_for_work(b), [t2])
        self.assertEqual(self._tag_ids_for_work(c), [t1])

    def test_bulk_invalid_work_is_atomic(self):
        w = self.db.add_work(title="Valid", status="Planned")
        with self.assertRaises(BulkWorkError) as ctx:
            self.db.bulk_update_works(
                {
                    "work_ids": [w, "W-MISSING"],
                    "action": "set_status",
                    "status": "Completed",
                }
            )
        self.assertEqual(ctx.exception.http_status, 404)
        self.assertEqual(self.db.get_work(w)["status"], "Planned")

    def test_bulk_invalid_folder_is_atomic(self):
        f = self.db.add_folder(title="Keep", description="")
        w = self.db.add_work(title="Valid")
        self.db.move_work_to_folder(w, f)
        with self.assertRaises(BulkWorkError) as ctx:
            self.db.bulk_update_works(
                {"work_ids": [w], "action": "move_folder", "folder_id": "F-MISSING"}
            )
        self.assertEqual(ctx.exception.http_status, 404)
        self.assertEqual(self._folder_ids_for_work(w), [f])

    def test_bulk_invalid_tag_is_atomic(self):
        w = self.db.add_work(title="Valid")
        t1 = self.db.add_tag("Keep")["id"]
        with self.assertRaises(BulkWorkError) as ctx:
            self.db.bulk_update_works(
                {
                    "work_ids": [w],
                    "action": "add_tags",
                    "tag_ids": [t1, "T-MISSING"],
                }
            )
        self.assertEqual(ctx.exception.http_status, 404)
        self.assertEqual(self._tag_ids_for_work(w), [])

    def test_bulk_deduplicates_work_ids(self):
        a = self.db.add_work(title="A", status="Paused")
        b = self.db.add_work(title="B", status="Paused")
        result = self.db.bulk_update_works(
            {
                "work_ids": [a, a, b],
                "action": "set_status",
                "status": "Completed",
            }
        )
        self.assertEqual(result["requested"], 2)
        self.assertEqual(result["updated"], 2)
        self.assertEqual(self.db.get_work(a)["status"], "Completed")
        self.assertEqual(self.db.get_work(b)["status"], "Completed")

    def test_bulk_rejects_over_limit(self):
        w = self.db.add_work(title="Only", status="Planned")
        too_many = [f"W-{i:04d}" for i in range(PRKS_BULK_WORK_MAX + 1)]
        with self.assertRaises(BulkWorkError) as ctx:
            self.db.bulk_update_works(
                {"work_ids": too_many, "action": "set_status", "status": "Completed"}
            )
        self.assertEqual(ctx.exception.http_status, 400)
        self.assertEqual(self.db.get_work(w)["status"], "Planned")

    def test_bulk_rejects_unknown_action(self):
        w = self.db.add_work(title="X", status="Planned")
        with self.assertRaises(BulkWorkError):
            self.db.bulk_update_works(
                {"work_ids": [w], "action": "delete_everything"}
            )
        self.assertEqual(self.db.get_work(w)["status"], "Planned")

    def test_bulk_transaction_rollback_on_injected_failure(self):
        a = self.db.add_work(title="A", status="Paused")
        b = self.db.add_work(title="B", status="Paused")
        orig = self.db.get_connection

        class _ConnProxy:
            def __init__(self, conn):
                object.__setattr__(self, "_conn", conn)

            def __getattr__(self, name):
                return getattr(self._conn, name)

            def __setattr__(self, name, value):
                if name == "_conn":
                    object.__setattr__(self, name, value)
                else:
                    setattr(self._conn, name, value)

            def commit(self):
                raise RuntimeError("injected")

        def wrapped():
            return _ConnProxy(orig())

        with patch.object(self.db, "get_connection", wrapped):
            with self.assertRaises(RuntimeError):
                self.db.bulk_update_works(
                    {
                        "work_ids": [a, b],
                        "action": "set_status",
                        "status": "Completed",
                    }
                )
        self.assertEqual(self.db.get_work(a)["status"], "Paused")
        self.assertEqual(self.db.get_work(b)["status"], "Paused")

    def _sv_search(self, **overrides):
        base = {
            "mode": "advanced",
            "q": "culture industry",
            "tag": "",
            "author": "Adorno",
            "publisher": "",
        }
        base.update(overrides)
        return base

    def test_saved_view_crud_order_and_timestamps(self):
        beta = self.db.create_saved_view("Beta", self._sv_search(q="beta"))
        alpha = self.db.create_saved_view("Alpha", self._sv_search(q="alpha"))
        listed = self.db.get_saved_views()
        self.assertEqual([v["name"] for v in listed], ["Alpha", "Beta"])
        self.assertTrue(listed[0]["id"].startswith("SV-"))
        self.assertNotIn("search_q", listed[0])
        self.assertEqual(listed[0]["search"]["q"], "alpha")
        got = self.db.get_saved_view(alpha["id"])
        self.assertEqual(got["name"], "Alpha")
        self.db.execute_query(
            "UPDATE saved_views SET created_at = ?, updated_at = ? WHERE id = ?",
            ("2020-01-01 00:00:00", "2020-01-01 00:00:00", alpha["id"]),
        )
        updated = self.db.update_saved_view(alpha["id"], name="Alpha renamed")
        self.assertEqual(updated["id"], alpha["id"])
        self.assertEqual(updated["name"], "Alpha renamed")
        self.assertEqual(updated["created_at"], "2020-01-01 00:00:00")
        self.assertNotEqual(updated["updated_at"], "2020-01-01 00:00:00")
        changed = self.db.update_saved_view(
            alpha["id"],
            search=self._sv_search(mode="all", q="critical theory", author="", publisher=""),
        )
        self.assertEqual(changed["search"]["mode"], "all")
        self.assertEqual(changed["search"]["q"], "critical theory")
        self.assertEqual(changed["id"], alpha["id"])
        self.db.delete_saved_view(beta["id"])
        names = [v["name"] for v in self.db.get_saved_views()]
        self.assertEqual(names, ["Alpha renamed"])
        self.assertIsNone(self.db.get_saved_view(beta["id"]))

    def test_saved_view_validation_rejects_invalid_definitions(self):
        cases = [
            {"name": "", "search": self._sv_search()},
            {"name": "X", "search": self._sv_search(mode="nope")},
            {"name": "X", "search": self._sv_search(mode="all", q="", author="", publisher="")},
            {"name": "X", "search": self._sv_search(q="", author="", publisher="")},
            {"name": "X", "search": self._sv_search(tag="Frankfurt")},
            {"name": "X", "search": self._sv_search(mode="tag", q="", tag="", author="")},
            {"name": "X", "search": self._sv_search(mode="tag", q="nope", tag="Frankfurt", author="")},
            {"name": "X", "search": {"mode": "all", "q": 1, "tag": "", "author": "", "publisher": ""}},
            {"name": "X" * 81, "search": self._sv_search()},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(SavedViewError):
                    self.db.create_saved_view(payload["name"], payload["search"])
        self.assertEqual(self.db.get_saved_views(), [])

    def test_saved_view_duplicate_name_is_case_insensitive(self):
        self.db.create_saved_view("Critical Theory", self._sv_search())
        with self.assertRaises(SavedViewError) as ctx:
            self.db.create_saved_view("critical theory", self._sv_search(q="other"))
        self.assertEqual(ctx.exception.http_status, 409)
        self.assertEqual(str(ctx.exception), "A Saved View with that name already exists.")
        other = self.db.create_saved_view("Other", self._sv_search(q="other"))
        with self.assertRaises(SavedViewError) as ctx2:
            self.db.update_saved_view(other["id"], name="CRITICAL THEORY")
        self.assertEqual(ctx2.exception.http_status, 409)

    def test_saved_view_create_rolls_back_on_failure(self):
        orig = self.db.get_connection

        class _ConnProxy:
            def __init__(self, conn):
                object.__setattr__(self, "_conn", conn)

            def __getattr__(self, name):
                return getattr(self._conn, name)

            def __setattr__(self, name, value):
                if name == "_conn":
                    object.__setattr__(self, name, value)
                else:
                    setattr(self._conn, name, value)

            def execute(self, sql, params=()):
                result = self._conn.execute(sql, params)
                if isinstance(sql, str) and "INSERT INTO saved_views" in sql:
                    raise RuntimeError("injected")
                return result

            def __enter__(self):
                self._conn.__enter__()
                return self

            def __exit__(self, exc_type, exc, tb):
                return self._conn.__exit__(exc_type, exc, tb)

        def wrapped():
            return _ConnProxy(orig())

        with patch.object(self.db, "get_connection", wrapped):
            with self.assertRaises(RuntimeError):
                self.db.create_saved_view("Injected", self._sv_search())
        self.assertEqual(self.db.get_saved_views(), [])

    def test_saved_view_delete_does_not_touch_works(self):
        w = self.db.add_work(title="Keep File")
        view = self.db.create_saved_view("Keep File Search", self._sv_search())
        self.db.delete_saved_view(view["id"])
        self.assertEqual(self.db.get_work(w)["title"], "Keep File")

    def test_saved_view_max_bound(self):
        with patch("backend.db_manager.PRKS_SAVED_VIEW_MAX", 1):
            self.db.create_saved_view("One", self._sv_search())
            with self.assertRaises(SavedViewError) as ctx:
                self.db.create_saved_view("Two", self._sv_search(q="two"))
            self.assertEqual(ctx.exception.http_status, 409)
        self.assertEqual(len(self.db.get_saved_views()), 1)
