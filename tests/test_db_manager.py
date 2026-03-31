import unittest
import os
import sys
import tempfile
import shutil
import uuid
from unittest.mock import patch

# Add the parent directory to sys.path so we can import 'backend'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db_manager import (
    PRKSDatabase,
    PRKS_BIBTEX_EXPORT_FIELD_IDS,
    safe_pdf_path_under_dir,
)

class TestDBManager(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(prefix="prks-db-tests-", suffix=".db")
        os.close(fd)
        self.test_db_path = path
            
        # Resolve the schema path relative to the test file
        schema_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend", "db_schema.sql")
        self.db = PRKSDatabase(db_path=self.test_db_path, schema_path=schema_path)

    def tearDown(self):
        if hasattr(self, 'test_db_path') and os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except Exception:
                pass

    def test_get_all_works_sets_file_size_bytes_for_local_pdf(self):
        payload = b"0123456789abcdef"
        fname = f"test_prks_size_{uuid.uuid4().hex}.pdf"
        with tempfile.TemporaryDirectory(prefix="prks-pdf-test-") as pdfs_dir:
            path = os.path.join(pdfs_dir, fname)
            with open(path, "wb") as f:
                f.write(payload)
            with patch('backend.db_manager._resolve_pdfs_dir', return_value=pdfs_dir):
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
        self.db.delete_work(w_id)
        work = self.db.get_work(w_id)
        self.assertIsNone(work)

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

    def test_delete_tag_removes_aliases(self):
        tid = self.db.add_tag("TaggedForDelete", "#000000")["id"]
        self.db.add_tag_alias(tid, "AltName")
        self.db.delete_tag(tid)
        al = self.db.execute_query("SELECT * FROM tag_aliases WHERE tag_id = ?", (tid,))
        self.assertEqual(len(al), 0)

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

    def test_remove_last_work_tag_deletes_unused_tag_row(self):
        w = self.db.add_work(title="T")
        tid = self.db.add_tag("Lonely", "#111")["id"]
        self.db.add_tag_to_work(w, tid)
        self.db.remove_tag_from_work(w, tid)
        rows = self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tid,))
        self.assertEqual(len(rows), 0)

    def test_remove_tag_keeps_tag_when_still_on_another_work(self):
        w1 = self.db.add_work(title="A")
        w2 = self.db.add_work(title="B")
        tid = self.db.add_tag("Shared", "#222")["id"]
        self.db.add_tag_to_work(w1, tid)
        self.db.add_tag_to_work(w2, tid)
        self.db.remove_tag_from_work(w1, tid)
        rows = self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tid,))
        self.assertEqual(len(rows), 1)

    def test_delete_work_prunes_tags_only_linked_to_that_work(self):
        w = self.db.add_work(title="Gone")
        tid = self.db.add_tag("OnlyHere", "#333")["id"]
        self.db.add_tag_to_work(w, tid)
        self.db.delete_work(w)
        rows = self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tid,))
        self.assertEqual(len(rows), 0)

    def test_get_recent_tags_in_use(self):
        w1 = self.db.add_work(title="W1")
        w2 = self.db.add_work(title="W2")
        ta = self.db.add_tag("older", "#111")["id"]
        tb = self.db.add_tag("newer", "#222")["id"]
        self.db.add_tag_to_work(w1, ta)
        self.db.add_tag_to_work(w2, tb)
        self.db.execute_query(
            "UPDATE works SET last_opened_at = '2020-01-01' WHERE id = ?", (w1,)
        )
        self.db.execute_query(
            "UPDATE works SET last_opened_at = '2025-06-01' WHERE id = ?", (w2,)
        )
        # Tag created_at is set at insert time (often “now”); pin it so sort reflects work activity.
        self.db.execute_query(
            "UPDATE tags SET created_at = '2000-01-01' WHERE id IN (?, ?)", (ta, tb)
        )
        recent = self.db.get_recent_tags_in_use(limit=5)
        names = [t["name"] for t in recent]
        self.assertIn("newer", names)
        self.assertIn("older", names)
        self.assertEqual(names[0], "newer")

    def test_build_graph_wiki_cocite_unresolved(self):
        w1 = self.db.add_work(title="Doc A", abstract="[[Ghost]]", text_content="")
        w2 = self.db.add_work(title="Doc B", text_content="[[Ghost]]", abstract="")
        g = self.db.build_graph_data()
        cocite = [e for e in g["edges"] if e.get("kind") == "wiki_cocite"]
        self.assertEqual(len(cocite), 1)
        pair = {cocite[0]["from"], cocite[0]["to"]}
        self.assertEqual(pair, {w1, w2})

    def test_build_graph_disambiguates_duplicate_titles(self):
        w1 = self.db.add_work(title="Same Title", text_content="")
        w2 = self.db.add_work(title="Same Title", text_content="")
        g = self.db.build_graph_data()
        labels = {n["id"]: n["label"] for n in g["nodes"]}
        self.assertIn("\n", labels[w1])
        self.assertIn(w1, labels[w1])
        self.assertIn("\n", labels[w2])
        self.assertIn(w2, labels[w2])
        self.assertNotEqual(labels[w1], labels[w2])

    def test_build_graph_nodes_include_doc_type(self):
        w1 = self.db.add_work(title="Graph Type A", doc_type="book")
        w2 = self.db.add_work(title="Graph Type B", doc_type="phdthesis")
        g = self.db.build_graph_data()
        by_id = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(by_id[w1]["doc_type"], "book")
        self.assertEqual(by_id[w1]["group"], "book")
        self.assertEqual(by_id[w2]["doc_type"], "phdthesis")
        self.assertEqual(by_id[w2]["group"], "phdthesis")

    def test_build_graph_wiki_edges_keep_direction(self):
        """Opposite wiki links A→B and B→A are two edges (from = source of [[link]])."""
        w1 = self.db.add_work(title="Alpha", text_content="[[Beta]]")
        w2 = self.db.add_work(title="Beta", text_content="[[Alpha]]")
        g = self.db.build_graph_data()
        wiki = [e for e in g["edges"] if e.get("kind") == "wiki"]
        self.assertEqual(len(wiki), 2)
        fwd = next(e for e in wiki if e["from"] == w1 and e["to"] == w2)
        back = next(e for e in wiki if e["from"] == w2 and e["to"] == w1)
        self.assertEqual(fwd["kind"], "wiki")
        self.assertEqual(back["kind"], "wiki")

    def test_safe_pdf_path_rejects_traversal(self):
        pdfs = tempfile.mkdtemp()
        try:
            self.assertIsNone(safe_pdf_path_under_dir(pdfs, ".."))
            self.assertIsNone(safe_pdf_path_under_dir(pdfs, "%2e%2e"))
            safe = os.path.join(pdfs, "keep.pdf")
            with open(safe, "w", encoding="utf-8") as f:
                f.write("x")
            resolved = safe_pdf_path_under_dir(pdfs, "keep.pdf")
            self.assertIsNotNone(resolved)
            self.assertTrue(os.path.isfile(resolved))
        finally:
            shutil.rmtree(pdfs)

    def test_delete_work_with_unsafe_pdf_path_still_removes_row(self):
        w_id = self.db.add_work(title="Unsafe fp", file_path="/api/pdfs/..")
        self.db.delete_work(w_id)
        self.assertIsNone(self.db.get_work(w_id))

if __name__ == '__main__':
    unittest.main()
