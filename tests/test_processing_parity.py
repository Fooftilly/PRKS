"""Files for Processing: PostgreSQL answers as SQLite does (L-PR-1 #636).

Each test runs on this leg's library object over its own data root, whose
``processing_dir`` is the inbox: the scan, the list and its order, a file's
edits (fields, roles, tags, target Folder) and their refusals, the PDF
preview path, and the import (the Work, its roles, tags and Folder, the
source removed and its empty directories pruned). What only PostgreSQL has
(no absolute path, the inbox lock, a crash between the import's commit and
its source removal, restore holds, the scan job, isolation) is in
``tests/pg/test_processing.py``.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from typing import Any

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.storage.config import StorageConfig
from tests.backend_parity import SQLITE, BackendParityTestCase, parametrize_backends

PDF = b"%PDF-1.4\n%processing parity\n%%EOF\n"


@parametrize_backends
class ProcessingParityTests(BackendParityTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="prks-processing-parity-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.storage = StorageConfig.for_testing(os.path.join(self.tmp, "root"))
        os.makedirs(self.storage.processing_dir, exist_ok=True)
        self.db = self.open_library(self.storage)
        self.inbox = self.storage.processing_dir

    def put(self, rel_path: str, data: bytes = PDF) -> str:
        path = os.path.join(self.inbox, *rel_path.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def staged(self, rel_path: str) -> dict[str, Any]:
        rows = {row["rel_path"]: row for row in self.db.scan_processing_files()}
        return rows[rel_path]

    def tag(self, name: str) -> str:
        row = self.db.add_tag(name, "#6d6cf7")
        return row["id"] if isinstance(row, dict) else row

    # -- scan and list ------------------------------------------------------------

    def test_a_scan_stages_the_inbox_pdfs_with_their_public_shape(self) -> None:
        self.put("batch_a/batch_b/sample.pdf")
        self.put("batch_a/skip.txt", b"ignore")
        self.put("Top.PDF")
        staged = self.db.scan_processing_files()
        self.assertEqual([row["rel_path"] for row in staged], ["batch_a/batch_b/sample.pdf", "Top.PDF"])
        row = staged[0]
        self.assertEqual(row["filename"], "sample.pdf")
        self.assertEqual(row["folder"], "batch_a/batch_b")
        self.assertEqual(staged[1]["folder"], "/")
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["last_error"])
        self.assertIsNone(row["imported_work_id"])
        self.assertIsNone(row["imported_at"])
        self.assertTrue(row["exists"])
        self.assertEqual(row["title"], "")
        self.assertEqual(row["status_draft"], "Not Started")
        self.assertEqual(row["doc_type"], "article")
        self.assertIsNone(row["thumb_page"])
        self.assertEqual(row["target_folder_id"], "")
        self.assertEqual(row["roles"], [])
        self.assertEqual(row["tags"], [])
        self.assertRegex(row["id"], r"^PF-[0-9A-F]{32}$")
        self.assertRegex(row["discovered_at"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertEqual(self.db.get_processing_file(row["id"]), row)
        self.assertEqual(self.db.get_processing_files(), staged)

    def test_a_rescan_keeps_ids_and_deletes_rows_whose_file_is_gone(self) -> None:
        keep = self.put("keep.pdf")
        gone = self.put("gone.pdf")
        first = {row["rel_path"]: row["id"] for row in self.db.scan_processing_files()}
        os.remove(gone)
        second = self.db.scan_processing_files()
        self.assertEqual([(row["rel_path"], row["id"]) for row in second], [("keep.pdf", first["keep.pdf"])])
        os.remove(keep)
        self.assertEqual(self.db.scan_processing_files(), [])
        self.assertIsNone(self.db.get_processing_file(first["keep.pdf"]))

    def test_the_list_is_by_status_then_case_folded_path(self) -> None:
        for name in ("b.pdf", "A.pdf", "c.pdf"):
            self.put(name)
        staged = {row["rel_path"]: row["id"] for row in self.db.scan_processing_files()}
        self.put("b.pdf", b"not a pdf")
        with self.assertRaises(ValueError):
            self.db.import_processing_file(staged["b.pdf"])
        listed = self.db.get_processing_files()
        self.assertEqual([(row["rel_path"], row["status"]) for row in listed],
                         [("A.pdf", "pending"), ("c.pdf", "pending"), ("b.pdf", "error")])
        # A rescan offers an error row again.
        self.assertEqual([row["status"] for row in self.db.scan_processing_files()], ["pending"] * 3)

    @unittest.skipIf(os.name == "nt", "POSIX filename semantics")
    def test_the_stored_spelling_is_the_files_identity(self) -> None:
        self.put("weird\\name.pdf", b"%PDF-1.4 odd")
        self.put("weird/name.pdf", b"%PDF-1.4 decoy")
        self.put(" batch/sample.pdf", b"%PDF-1.4 padded")
        self.put("batch/sample.pdf", b"%PDF-1.4 plain")
        staged = {row["rel_path"]: row for row in self.db.scan_processing_files()}
        again = self.db.scan_processing_files()
        self.assertEqual(sorted(row["id"] for row in again), sorted(row["id"] for row in staged.values()))
        self.assertEqual(staged["weird\\name.pdf"]["folder"], "/")
        for rel_path, data in (("weird\\name.pdf", b"%PDF-1.4 odd"), (" batch/sample.pdf", b"%PDF-1.4 padded")):
            preview = self.db.get_processing_file_pdf_path(staged[rel_path]["id"])
            self.assertEqual(preview, os.path.realpath(os.path.join(self.inbox, rel_path)))
            with open(preview, "rb") as handle:
                self.assertEqual(handle.read(), data)
        self.db.import_processing_file(staged["weird\\name.pdf"]["id"])
        self.assertFalse(os.path.exists(os.path.join(self.inbox, "weird\\name.pdf")))
        self.assertTrue(os.path.isfile(os.path.join(self.inbox, "weird", "name.pdf")))

    def test_a_link_out_of_the_inbox_is_not_staged(self) -> None:
        outside = os.path.join(self.tmp, "outside.pdf")
        with open(outside, "wb") as handle:
            handle.write(PDF)
        try:
            os.symlink(outside, os.path.join(self.inbox, "escape.pdf"))
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        self.assertEqual(self.db.scan_processing_files(), [])

    def test_the_preview_path_is_none_for_a_missing_file_or_row(self) -> None:
        path = self.put("x.pdf")
        file_id = self.staged("x.pdf")["id"]
        self.assertEqual(self.db.get_processing_file_pdf_path(file_id), os.path.realpath(path))
        os.remove(path)
        self.assertIsNone(self.db.get_processing_file_pdf_path(file_id))
        self.assertFalse(self.db.get_processing_file(file_id)["exists"])
        self.assertIsNone(self.db.get_processing_file_pdf_path("PF-" + "0" * 32))

    # -- update -------------------------------------------------------------------

    def test_an_update_normalizes_fields_and_returns_the_file(self) -> None:
        self.put("u.pdf")
        file_id = self.staged("u.pdf")["id"]
        folder = self.db.add_folder(title="Target", description="")
        out = self.db.update_processing_file(file_id, {
            "title": "  Spaced  ", "status": "Planned", "doc_type": "BOOK", "thumb_page": "0",
            "year": 2024, "abstract": None, "target_folder_id": f" {folder} ", "ignored": "x",
        })
        self.assertEqual(out["title"], "Spaced")
        self.assertEqual(out["status_draft"], "Planned")
        self.assertEqual(out["doc_type"], "book")
        self.assertIsNone(out["thumb_page"])
        self.assertEqual(out["year"], "2024")
        self.assertEqual(out["abstract"], "")
        self.assertEqual(out["target_folder_id"], folder)
        self.assertNotIn("ignored", out)
        self.assertEqual(self.db.get_processing_file(file_id), out)
        out = self.db.update_processing_file(file_id, {"thumb_page": 3, "target_folder_id": ""})
        self.assertEqual(out["thumb_page"], 3)
        self.assertEqual(out["target_folder_id"], "")

    def test_an_update_refuses_as_sqlite_does(self) -> None:
        self.put("r.pdf")
        file_id = self.staged("r.pdf")["id"]
        person = self.db.add_person(first_name="Role", last_name="Holder")
        cases = [
            ("PF-" + "0" * 32, {"title": "x"}, "Processing file not found."),
            ("PF-" + "0" * 32, "not a dict", "Processing file not found."),
            (file_id, "not a dict", "JSON object body required."),
            (file_id, {"status_draft": "Done"}, "Invalid status_draft value."),
            (file_id, {"target_folder_id": "F-" + "0" * 32}, "Unknown folder."),
            (file_id, {"roles": "x"}, "roles must be an array."),
            (file_id, {"roles": [{"person_id": person, "role_type": "Mentioned"}]}, "Unsupported role_type: Mentioned"),
            (file_id, {"roles": [{"person_id": "P-" + "0" * 32, "role_type": "Author"}]},
             "Unknown person id: P-" + "0" * 32),
            (file_id, {"tags": {"id": "x"}}, "tags must be an array."),
            (file_id, {"tags": [{"id": "T-" + "0" * 32}]}, "Unknown tag id: T-" + "0" * 32),
        ]
        for target, fields, message in cases:
            with self.subTest(fields=fields), self.assertRaises(ValueError) as ctx:
                self.db.update_processing_file(target, fields)
            self.assertEqual(str(ctx.exception), message)

    def test_roles_keep_payload_order_and_tags_sort_by_name(self) -> None:
        self.put("o.pdf")
        file_id = self.staged("o.pdf")["id"]
        ada = self.db.add_person(first_name="Ada", last_name="Role")
        bea = self.db.add_person(first_name="Bea", last_name="")
        zed, alpha = self.tag("Zed"), self.tag("alpha")
        out = self.db.update_processing_file(file_id, {
            "roles": [{"person_id": bea, "role_type": "Editor"}, {"person_id": ada, "role_type": "Author"},
                      {"person_id": bea, "role_type": "Editor"}, {"person_id": "", "role_type": "Author"}, "x"],
            "tags": [{"id": zed}, {"tag_id": alpha}, {"id": zed}, {"id": ""}],
        })
        self.assertEqual([(r["person_id"], r["person_name"], r["role_type"], r["order_index"]) for r in out["roles"]],
                         [(bea, "Bea", "Editor", 0), (ada, "Ada Role", "Author", 1)])
        self.assertEqual([t["id"] for t in out["tags"]], [alpha, zed])
        self.assertEqual(set(out["tags"][0]), {"id", "name", "color", "created_at"})
        listed = self.db.get_processing_files()[0]
        self.assertEqual(listed["roles"], out["roles"])
        self.assertEqual(listed["tags"], out["tags"])
        cleared = self.db.update_processing_file(file_id, {"roles": [], "tags": []})
        self.assertEqual((cleared["roles"], cleared["tags"]), ([], []))

    def test_a_tag_merge_moves_and_a_tag_delete_removes_a_files_tags(self) -> None:
        self.put("t.pdf")
        file_id = self.staged("t.pdf")["id"]
        source, target, other = self.tag("Source"), self.tag("Target"), self.tag("Other")
        self.db.update_processing_file(file_id, {"tags": [{"id": source}, {"id": other}]})
        self.db.merge_tags_into(source, target)
        self.assertEqual([t["id"] for t in self.db.get_processing_file(file_id)["tags"]], [other, target])
        self.db.delete_tag(other)
        self.assertEqual([t["id"] for t in self.db.get_processing_file(file_id)["tags"]], [target])

    # -- import -------------------------------------------------------------------

    def test_an_import_creates_the_work_with_its_roles_tags_and_folder(self) -> None:
        path = self.put("batch_a/batch_b/sample.pdf")
        file_id = self.staged("batch_a/batch_b/sample.pdf")["id"]
        person = self.db.add_person(first_name="Role", last_name="Author")
        folder = self.db.add_folder(title="Processing Target", description="")
        tag_id = self.tag("Inbox Tag")
        self.db.update_processing_file(file_id, {
            "title": "Imported from inbox", "status_draft": "Planned", "doc_type": "book",
            "year": "1999", "publisher": "Pub", "target_folder_id": folder, "private_notes": "  secret ",
            "source_url": "https://example.org/paper", "thumb_page": 2,
            "roles": [{"person_id": person, "role_type": "Author"}], "tags": [{"id": tag_id}],
        })
        out = self.db.import_processing_file(file_id)
        self.assertEqual(set(out), {"processing_file_id", "work_id"})
        self.assertEqual(out["processing_file_id"], file_id)
        work = self.db.get_work(out["work_id"])
        self.assertEqual(work["title"], "Imported from inbox")
        self.assertEqual(work["status"], "Planned")
        self.assertEqual(work["doc_type"], "book")
        self.assertEqual(work["year"], "1999")
        self.assertEqual(work["publisher"], "Pub")
        self.assertEqual(work["folder_id"], folder)
        self.assertEqual(work["thumb_page"], 2)
        self.assertEqual(work["source_url"], "https://example.org/paper")
        self.assertTrue(str(work["file_path"]).startswith("/api/pdfs/"))
        self.assertEqual([(r["id"], r["role_type"]) for r in work["roles"]], [(person, "Author")])
        self.assertEqual([t["id"] for t in self.db.get_work_tags(out["work_id"])], [tag_id])
        self.assertFalse(os.path.exists(path))
        self.assertFalse(os.path.isdir(os.path.join(self.inbox, "batch_a")))
        self.assertTrue(os.path.isdir(self.inbox))
        self.assertEqual(self.db.get_processing_files(), [])

    def test_an_import_without_a_target_files_the_work_in_uncategorized(self) -> None:
        self.put("Free Paper.pdf")
        out = self.db.import_processing_file(self.staged("Free Paper.pdf")["id"])
        work = self.db.get_work(out["work_id"])
        self.assertEqual(work["title"], "Free Paper")
        self.assertEqual(work["status"], "Not Started")
        self.assertEqual(work["folder_title"], "Uncategorized")

    def test_an_import_refuses_as_sqlite_does(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.db.import_processing_file("PF-" + "0" * 32)
        self.assertEqual(str(ctx.exception), "Processing file not found.")

        path = self.put("vanished.pdf")
        file_id = self.staged("vanished.pdf")["id"]
        os.remove(path)
        with self.assertRaises(ValueError) as ctx:
            self.db.import_processing_file(file_id)
        self.assertEqual(str(ctx.exception), "Inbox file no longer present.")
        self.assertIsNone(self.db.get_processing_file(file_id))

        self.put("fake.pdf", b"<html>not a pdf</html>")
        file_id = self.staged("fake.pdf")["id"]
        with self.assertRaises(ValueError) as ctx:
            self.db.import_processing_file(file_id)
        message = "File does not look like a valid PDF (missing %PDF header)."
        self.assertEqual(str(ctx.exception), message)
        row = self.db.get_processing_file(file_id)
        self.assertEqual((row["status"], row["last_error"]), ("error", message))
        self.assertTrue(os.path.isfile(os.path.join(self.inbox, "fake.pdf")))
        # An edit clears the error message, not the status.
        row = self.db.update_processing_file(file_id, {"title": "Retry"})
        self.assertEqual((row["status"], row["last_error"]), ("error", None))

    def test_a_deleted_target_folder(self) -> None:
        self.put("f.pdf")
        file_id = self.staged("f.pdf")["id"]
        folder = self.db.add_folder(title="Doomed", description="")
        self.db.update_processing_file(file_id, {"target_folder_id": folder})
        self.db.delete_empty_folder(folder)
        if self.parity_backend == SQLITE:
            # SQLite keeps the dangling target, and the import refuses it.
            self.assertEqual(self.db.get_processing_file(file_id)["target_folder_id"], folder)
            with self.assertRaises(ValueError) as ctx:
                self.db.import_processing_file(file_id)
            self.assertEqual(str(ctx.exception), "Unknown folder.")
            return
        # The key clears it (a deliberate difference): the Work goes to "Uncategorized".
        self.assertEqual(self.db.get_processing_file(file_id)["target_folder_id"], "")
        out = self.db.import_processing_file(file_id)
        self.assertEqual(self.db.get_work(out["work_id"])["folder_title"], "Uncategorized")

if __name__ == "__main__":
    unittest.main()
