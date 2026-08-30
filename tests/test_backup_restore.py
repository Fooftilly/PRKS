import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from dataclasses import fields, replace
from pathlib import Path
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.backup_restore import (
    ARCHIVE_DB_PATH,
    DISK_MARGIN_BYTES,
    FORMAT_ID,
    FORMAT_VERSION,
    IO_CHUNK_SIZE,
    MANIFEST_NAME,
    BackupError,
    RestoreCrash,
    RestoreError,
    apply_restore,
    backup_additional_bytes,
    backup_storage_inventory,
    classified_storage_field_names,
    create_backup,
    hash_and_copy,
    iter_file_chunks,
    recover_incomplete_restore,
    require_restore_upload_space,
    stage_restore,
    storage_config_path_field_names,
    verify_backup,
)
from backend.db_manager import PRKS_SCHEMA_VERSION, PRKSDatabase
from backend.server import bind_storage
from backend.storage.config import StorageConfig
from backend.text_index import get_text_index, reset_text_index
import backend.backup_restore as backup_module
import backend.server as server_module


def _capture_bind():
    try:
        previous_index = get_text_index()
    except RuntimeError:
        previous_index = None
    return (
        server_module._bound_storage,
        server_module.pdfs_dir,
        server_module.thumbs_dir,
        server_module.processing_dir,
        server_module.db,
        server_module.text_index,
        previous_index,
    )


def _restore_bind(snapshot):
    (
        server_module._bound_storage,
        server_module.pdfs_dir,
        server_module.thumbs_dir,
        server_module.processing_dir,
        server_module.db,
        server_module.text_index,
        previous_index,
    ) = snapshot
    if previous_index is None:
        reset_text_index()
    else:
        from backend.text_index import replace_text_index

        replace_text_index(previous_index)


def _pdf_bytes(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text or "")
    out = doc.tobytes()
    doc.close()
    return out


def _copy_backup(src: str, dest_dir: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(src))
    shutil.copy2(src, dest)
    return dest


def _zip_namelist(path: str) -> list[str]:
    with zipfile.ZipFile(path, "r") as zf:
        return zf.namelist()


def _read_manifest(path: str) -> dict:
    with zipfile.ZipFile(path, "r") as zf:
        return json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))


def _rewrite_backup(src: str, dest: str, mutator) -> None:
    with zipfile.ZipFile(src, "r") as zin, zipfile.ZipFile(dest, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            name, payload, zipinfo = mutator(info, data)
            if name is None:
                continue
            zipinfo.filename = name
            zout.writestr(zipinfo, payload)


class BackupRestoreTestCase(unittest.TestCase):
    def setUp(self):
        self._prev = _capture_bind()
        self._tmps = []

    def tearDown(self):
        _restore_bind(self._prev)
        for path in self._tmps:
            shutil.rmtree(path, ignore_errors=True)

    def _tmpdir(self, prefix="prks-backup-"):
        path = tempfile.mkdtemp(prefix=prefix)
        self._tmps.append(path)
        return path

    def _cfg(self, root=None):
        if root is None:
            root = self._tmpdir()
        return StorageConfig.for_testing(root)

    def _bind_library(
        self,
        *,
        title="Alpha Work",
        pdf_text="searchable unique token alpha",
        pdf_name="alpha.pdf",
        person_bytes=b"PERSON-BYTES-A",
        author="Backup Author",
        extra_pdf_name=None,
        extra_pdf_bytes=None,
        processing_name=None,
        processing_bytes=b"QUEUE",
    ):
        root = self._tmpdir()
        cfg = self._cfg(root)
        bound = bind_storage(cfg)
        os.makedirs(bound.pdfs_dir, exist_ok=True)
        os.makedirs(bound.people_dir, exist_ok=True)
        os.makedirs(bound.thumbs_dir, exist_ok=True)
        os.makedirs(bound.processing_dir, exist_ok=True)
        pdf_bytes = _pdf_bytes(pdf_text)
        with open(os.path.join(bound.pdfs_dir, pdf_name), "wb") as handle:
            handle.write(pdf_bytes)
        if extra_pdf_name:
            with open(os.path.join(bound.pdfs_dir, extra_pdf_name), "wb") as handle:
                handle.write(extra_pdf_bytes or b"%PDF-1.4 orphan\n%%EOF\n")
        person_path = os.path.join(bound.people_dir, "portrait.webp")
        with open(person_path, "wb") as handle:
            handle.write(person_bytes)
        with open(os.path.join(bound.thumbs_dir, "stale-thumb.webp"), "wb") as handle:
            handle.write(b"OLD-THUMB")
        if processing_name:
            with open(os.path.join(bound.processing_dir, processing_name), "wb") as handle:
                handle.write(processing_bytes)
        db = server_module.db
        work_id = db.add_work(title, file_path=f"/api/pdfs/{pdf_name}", source_kind="pdf")
        person_id = db.add_person("Ada", "Lovelace")
        db.add_role(person_id, work_id, "Author")
        folder_id = db.add_folder("Research")
        db.add_work_to_folder(folder_id, work_id)
        tag = db.add_tag("rhetoric")
        db.add_tag_to_work(work_id, tag["id"])
        db.save_work_annotations(
            work_id, json.dumps([{"id": "a1", "type": "highlight", "content": "note"}])
        )
        db.patch_app_settings({"annotation_author": author})
        server_module.text_index.upsert_from_pdf(
            work_id, os.path.join(bound.pdfs_dir, pdf_name)
        )
        return {
            "cfg": bound,
            "work_id": work_id,
            "person_id": person_id,
            "folder_id": folder_id,
            "pdf_name": pdf_name,
            "pdf_bytes": pdf_bytes,
            "person_bytes": person_bytes,
            "title": title,
            "author": author,
            "pdf_text": pdf_text,
        }

    def _stage_copy(self, cfg, archive_path):
        copied = _copy_backup(archive_path, os.path.join(self._tmpdir(), "upload"))
        return stage_restore(cfg, copied)


class TestBackupInventory(BackupRestoreTestCase):
    def test_every_storage_config_path_is_classified(self):
        self.assertEqual(storage_config_path_field_names(), classified_storage_field_names())
        inv = backup_storage_inventory()
        self.assertIn("db_path", inv.canonical)
        self.assertIn("pdfs_dir", inv.canonical)
        self.assertIn("people_dir", inv.canonical)
        self.assertIn("thumbs_dir", inv.derived)
        self.assertIn("index_db_path", inv.derived)
        self.assertIn("log_file", inv.operational)
        self.assertIn("processing_dir", inv.conditional)

    def test_non_path_fields_are_explicit(self):
        names = {f.name for f in fields(StorageConfig)}
        leftover = names - classified_storage_field_names()
        self.assertEqual(leftover, {"mode", "processing_fallback_allowed"})


class TestBackupRoundTrip(BackupRestoreTestCase):
    def test_round_trip_to_empty_storage(self):
        source = self._bind_library(extra_pdf_name="orphan.pdf")
        backup = create_backup(source["cfg"])
        self.assertTrue(backup.verified)
        names = _zip_namelist(backup.archive_path)
        self.assertIn(MANIFEST_NAME, names)
        self.assertIn(ARCHIVE_DB_PATH, names)
        self.assertIn(f"files/pdfs/{source['pdf_name']}", names)
        self.assertIn("files/pdfs/orphan.pdf", names)
        self.assertIn("files/people/portrait.webp", names)
        self.assertTrue(all(not n.startswith("thumbs/") for n in names))
        self.assertNotIn("prks_text_index.db", " ".join(names))
        self.assertFalse(any(".prks-maintenance" in n for n in names))
        self.assertFalse(any("prks-errors.log" in n for n in names))
        manifest = _read_manifest(backup.archive_path)
        blob = json.dumps(manifest)
        self.assertNotIn(str(source["cfg"].root), blob)
        self.assertEqual(manifest["format"], FORMAT_ID)
        self.assertEqual(manifest["format_version"], FORMAT_VERSION)
        self.assertTrue(backup.filename.startswith("prks-backup-"))
        self.assertTrue(backup.filename.endswith(".prks-backup"))
        self.assertNotIn("Alpha", backup.filename)

        dest_root = self._tmpdir()
        dest = bind_storage(self._cfg(dest_root))
        staged = self._stage_copy(dest, backup.archive_path)
        self.assertTrue(staged.verified)
        out = apply_restore(dest, staged.token, "RESTORE", rebind=bind_storage)
        self.assertTrue(out["restored"])
        live = server_module.db
        rows = live.execute_query("SELECT title FROM works")
        self.assertEqual([r["title"] for r in rows], [source["title"]])
        restored_pdf = os.path.join(dest.pdfs_dir, source["pdf_name"])
        with open(restored_pdf, "rb") as handle:
            self.assertEqual(handle.read(), source["pdf_bytes"])
        with open(os.path.join(dest.people_dir, "portrait.webp"), "rb") as handle:
            self.assertEqual(handle.read(), source["person_bytes"])
        settings = live.get_app_settings_response()
        self.assertEqual(settings["annotation_author"], source["author"])
        folders = live.execute_query("SELECT title FROM folders WHERE title = ?", ("Research",))
        self.assertEqual(len(folders), 1)
        tags = live.execute_query("SELECT name FROM tags WHERE name = ?", ("rhetoric",))
        self.assertEqual(len(tags), 1)
        roles = live.execute_query("SELECT role_type FROM roles")
        self.assertTrue(roles)
        anns = live.get_work_annotations(source["work_id"])
        self.assertIn("highlight", anns)
        thumbs = os.listdir(dest.thumbs_dir) if os.path.isdir(dest.thumbs_dir) else []
        self.assertNotIn("stale-thumb.webp", thumbs)
        hits = server_module.text_index.search_work_ids("unique token alpha")
        self.assertIn(source["work_id"], hits)

    def test_restore_replaces_existing_library(self):
        lib_a = self._bind_library(
            title="Library A",
            pdf_text="only in A",
            pdf_name="a.pdf",
            person_bytes=b"PERSON-A",
            author="Author A",
        )
        lib_b = self._bind_library(
            title="Library B",
            pdf_text="only in B searchable",
            pdf_name="b.pdf",
            person_bytes=b"PERSON-B",
            author="Author B",
        )
        backup_b = create_backup(lib_b["cfg"])
        bind_storage(lib_a["cfg"])
        staged = self._stage_copy(lib_a["cfg"], backup_b.archive_path)
        apply_restore(lib_a["cfg"], staged.token, "RESTORE", rebind=bind_storage)
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Library B"])
        self.assertFalse(os.path.isfile(os.path.join(lib_a["cfg"].pdfs_dir, "a.pdf")))
        self.assertTrue(os.path.isfile(os.path.join(lib_a["cfg"].pdfs_dir, "b.pdf")))
        with open(os.path.join(lib_a["cfg"].people_dir, "portrait.webp"), "rb") as handle:
            self.assertEqual(handle.read(), b"PERSON-B")
        self.assertFalse(os.path.isfile(os.path.join(lib_a["cfg"].thumbs_dir, "stale-thumb.webp")))
        hits = server_module.text_index.search_work_ids("only in B")
        self.assertTrue(hits)
        self.assertFalse(server_module.text_index.search_work_ids("only in A"))

    def test_sqlite_online_backup_is_used(self):
        src = Path(_PROJECT_DIR, "backend", "backup_restore.py").read_text(encoding="utf-8")
        self.assertIn("source.backup(dest)", src)
        self.assertNotIn("shutil.copy2(config.db_path", src)
        self.assertNotIn("shutil.copy(config.db_path", src)

    def test_processing_included_when_under_root(self):
        lib = self._bind_library(processing_name="inbox.pdf", processing_bytes=b"QUEUE-PDF")
        backup = create_backup(lib["cfg"])
        manifest = _read_manifest(backup.archive_path)
        self.assertTrue(manifest["components"]["processing"])
        self.assertIn("files/for_processing/inbox.pdf", _zip_namelist(backup.archive_path))

    def test_external_processing_not_included(self):
        lib = self._bind_library()
        outside = self._tmpdir()
        cfg = replace(lib["cfg"], processing_dir=outside)
        with open(os.path.join(outside, "secret.bin"), "wb") as handle:
            handle.write(b"SECRET")
        backup = create_backup(cfg)
        manifest = _read_manifest(backup.archive_path)
        self.assertFalse(manifest["components"]["processing"])
        names = _zip_namelist(backup.archive_path)
        self.assertFalse(any(n.startswith("files/for_processing/") and n != "files/for_processing/" for n in names))
        self.assertTrue(any("outside PRKS storage" in w for w in backup.warnings))

    def test_symlink_in_pdfs_is_not_followed(self):
        lib = self._bind_library()
        target = os.path.join(self._tmpdir(), "outside.bin")
        with open(target, "wb") as handle:
            handle.write(b"OUTSIDE")
        os.symlink(target, os.path.join(lib["cfg"].pdfs_dir, "link.bin"))
        backup = create_backup(lib["cfg"])
        names = _zip_namelist(backup.archive_path)
        self.assertNotIn("files/pdfs/link.bin", names)
        self.assertTrue(any("symbolic link" in w for w in backup.warnings))

    def test_missing_referenced_pdf_is_warning(self):
        lib = self._bind_library()
        os.remove(os.path.join(lib["cfg"].pdfs_dir, lib["pdf_name"]))
        backup = create_backup(lib["cfg"])
        self.assertTrue(backup.verified)
        self.assertTrue(any("already missing" in w for w in backup.warnings))

    def test_progress_callback_reports_phases(self):
        lib = self._bind_library()
        events = []
        backup = create_backup(lib["cfg"], progress=events.append)
        self.assertTrue(backup.verified)
        phases = [ev["phase"] for ev in events]
        self.assertIn("snapshot", phases)
        self.assertIn("archiving", phases)
        self.assertIn("verifying", phases)
        self.assertTrue(all("path" not in ev for ev in events))
        percents = [ev["percent"] for ev in events]
        self.assertGreaterEqual(percents[-1], percents[0])
        self.assertLessEqual(max(percents), 99)

    def test_cancel_stops_backup_and_deletes_temps(self):
        lib = self._bind_library()
        big = os.path.join(lib["cfg"].pdfs_dir, "big.bin")
        with open(big, "wb") as handle:
            handle.write(b"x" * (IO_CHUNK_SIZE * 4))
        cancel = threading.Event()

        def on_progress(ev):
            if ev.get("phase") == "archiving" and int(ev.get("bytes_done") or 0) > 0:
                cancel.set()

        with self.assertRaises(BackupError) as ctx:
            create_backup(lib["cfg"], progress=on_progress, cancel_event=cancel)
        self.assertEqual(ctx.exception.reason, "cancelled")
        maint = os.path.join(lib["cfg"].root, ".prks-maintenance", "backup")
        leftovers = []
        if os.path.isdir(maint):
            for name in os.listdir(maint):
                leftovers.append(name)
        self.assertFalse(any(name.endswith(".prks-backup") for name in leftovers))

    def test_orphan_work_annotations_are_backup_warning(self):
        lib = self._bind_library()
        conn = sqlite3.connect(lib["cfg"].db_path)
        conn.execute(
            "INSERT INTO work_annotations (work_id, annotations_json) VALUES (?, ?)",
            ("W-MISSING", "[]"),
        )
        conn.commit()
        conn.close()
        backup = create_backup(lib["cfg"])
        self.assertTrue(backup.verified)
        self.assertTrue(any("foreign-key" in w for w in backup.warnings))
        dest = bind_storage(self._cfg(self._tmpdir()))
        staged = self._stage_copy(dest, backup.archive_path)
        out = apply_restore(dest, staged.token, "RESTORE", rebind=bind_storage)
        self.assertTrue(out["restored"])
        self.assertTrue(any("foreign-key" in w for w in staged.warnings))

    def test_live_schema_ahead_of_constant_can_backup_and_restore_locally(self):
        lib = self._bind_library()
        ahead = PRKS_SCHEMA_VERSION + 1
        conn = sqlite3.connect(lib["cfg"].db_path)
        conn.execute("UPDATE schema_version SET version = ?", (ahead,))
        conn.commit()
        conn.close()
        backup = create_backup(lib["cfg"])
        self.assertTrue(backup.verified)
        fresh = bind_storage(self._cfg(self._tmpdir()))
        with self.assertRaises(RestoreError) as ctx:
            self._stage_copy(fresh, backup.archive_path)
        self.assertEqual(ctx.exception.reason, "schema_newer")
        staged = self._stage_copy(lib["cfg"], backup.archive_path)
        out = apply_restore(lib["cfg"], staged.token, "RESTORE", rebind=bind_storage)
        self.assertTrue(out["restored"])


class TestBackupCorruption(BackupRestoreTestCase):
    def _valid_backup(self):
        lib = self._bind_library()
        backup = create_backup(lib["cfg"])
        return lib, backup

    def test_corrupt_pdf_payload_fails_before_mutation(self):
        lib, backup = self._valid_backup()
        dest_root = self._tmpdir()
        dest = bind_storage(self._cfg(dest_root))
        live_marker = os.path.join(dest.pdfs_dir, "keep-me.pdf")
        os.makedirs(dest.pdfs_dir, exist_ok=True)
        with open(live_marker, "wb") as handle:
            handle.write(b"KEEP")
        broken = os.path.join(self._tmpdir(), "broken.prks-backup")

        def mutate(info, data):
            if info.filename.startswith("files/pdfs/"):
                return info.filename, data + b"x", info
            return info.filename, data, info

        _rewrite_backup(backup.archive_path, broken, mutate)
        with self.assertRaises(RestoreError):
            stage_restore(dest, broken)
        self.assertTrue(os.path.isfile(live_marker))

    def test_corrupt_db_payload_fails(self):
        lib, backup = self._valid_backup()
        dest = bind_storage(self._cfg(self._tmpdir()))
        broken = os.path.join(self._tmpdir(), "broken.prks-backup")

        def mutate(info, data):
            if info.filename == ARCHIVE_DB_PATH:
                return info.filename, data + b"corrupt", info
            return info.filename, data, info

        _rewrite_backup(backup.archive_path, broken, mutate)
        with self.assertRaises(RestoreError):
            stage_restore(dest, broken)

    def test_corrupt_manifest_hash_fails(self):
        lib, backup = self._valid_backup()
        dest = bind_storage(self._cfg(self._tmpdir()))
        broken = os.path.join(self._tmpdir(), "broken.prks-backup")

        def mutate(info, data):
            if info.filename == MANIFEST_NAME:
                manifest = json.loads(data.decode("utf-8"))
                manifest["entries"][0]["sha256"] = "0" * 64
                return info.filename, json.dumps(manifest).encode("utf-8"), info
            return info.filename, data, info

        _rewrite_backup(backup.archive_path, broken, mutate)
        with self.assertRaises(RestoreError):
            stage_restore(dest, broken)

    def test_corrupt_manifest_size_fails(self):
        lib, backup = self._valid_backup()
        dest = bind_storage(self._cfg(self._tmpdir()))
        broken = os.path.join(self._tmpdir(), "broken.prks-backup")

        def mutate(info, data):
            if info.filename == MANIFEST_NAME:
                manifest = json.loads(data.decode("utf-8"))
                manifest["entries"][0]["size"] = 1
                return info.filename, json.dumps(manifest).encode("utf-8"), info
            return info.filename, data, info

        _rewrite_backup(backup.archive_path, broken, mutate)
        with self.assertRaises(RestoreError):
            stage_restore(dest, broken)

    def test_unknown_format_version_fails(self):
        lib, backup = self._valid_backup()
        dest = bind_storage(self._cfg(self._tmpdir()))
        broken = os.path.join(self._tmpdir(), "broken.prks-backup")

        def mutate(info, data):
            if info.filename == MANIFEST_NAME:
                manifest = json.loads(data.decode("utf-8"))
                manifest["format_version"] = 2
                return info.filename, json.dumps(manifest).encode("utf-8"), info
            return info.filename, data, info

        _rewrite_backup(backup.archive_path, broken, mutate)
        with self.assertRaises(RestoreError) as ctx:
            stage_restore(dest, broken)
        self.assertEqual(ctx.exception.reason, "unsupported_format_version")

    def test_newer_schema_is_refused(self):
        lib, backup = self._valid_backup()
        dest = bind_storage(self._cfg(self._tmpdir()))
        work = self._tmpdir()
        extracted = os.path.join(work, "tree")
        os.makedirs(extracted, exist_ok=True)
        with zipfile.ZipFile(backup.archive_path, "r") as zf:
            for info in zf.infolist():
                if info.filename.endswith("/"):
                    continue
                dest_path = os.path.join(extracted, *info.filename.split("/"))
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                with zf.open(info) as src, open(dest_path, "wb") as out:
                    out.write(src.read())
        db_path = os.path.join(extracted, "data", "prks_data.db")
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE schema_version SET version = ?", (PRKS_SCHEMA_VERSION + 5,))
        conn.commit()
        conn.close()
        manifest_path = os.path.join(extracted, MANIFEST_NAME)
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        manifest["db_schema_version"] = PRKS_SCHEMA_VERSION + 5
        for entry in manifest["entries"]:
            if entry["path"] == ARCHIVE_DB_PATH:
                size, digest = 0, hashlib.sha256()
                with open(db_path, "rb") as handle:
                    while True:
                        chunk = handle.read(65536)
                        if not chunk:
                            break
                        digest.update(chunk)
                        size += len(chunk)
                entry["size"] = size
                entry["sha256"] = digest.hexdigest()
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        broken = os.path.join(work, "newer.prks-backup")
        with zipfile.ZipFile(broken, "w") as zf:
            zf.write(manifest_path, MANIFEST_NAME)
            zf.write(db_path, ARCHIVE_DB_PATH)
            pdfs = os.path.join(extracted, "files", "pdfs")
            for name in os.listdir(pdfs):
                zf.write(os.path.join(pdfs, name), f"files/pdfs/{name}")
            people = os.path.join(extracted, "files", "people")
            for name in os.listdir(people):
                zf.write(os.path.join(people, name), f"files/people/{name}")
            # refresh hashes for rewritten files except we already updated db; others unchanged
        # Rebuild entries from actual files for hash match
        rebuilt = os.path.join(work, "newer-fixed.prks-backup")
        entries = []
        with zipfile.ZipFile(broken, "r") as zin, zipfile.ZipFile(rebuilt, "w") as zout:
            members = [i for i in zin.infolist() if i.filename != MANIFEST_NAME]
            for info in members:
                data = zin.read(info.filename)
                zout.writestr(info.filename, data)
                entries.append(
                    {
                        "path": info.filename,
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
            manifest["entries"] = entries
            zout.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))
        with self.assertRaises(RestoreError) as ctx:
            stage_restore(dest, rebuilt)
        self.assertEqual(ctx.exception.reason, "schema_newer")
        self.assertIn("Update PRKS before restoring it", ctx.exception.message)
        self.assertTrue(os.path.isfile(dest.db_path))


class TestZipTraversal(BackupRestoreTestCase):
    def _stage_malicious(self, members):
        dest = bind_storage(self._cfg(self._tmpdir()))
        path = os.path.join(self._tmpdir(), "evil.prks-backup")
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in members:
                zf.writestr(name, data)
        with self.assertRaises(RestoreError):
            stage_restore(dest, path)
        root = dest.root
        for dirpath, dirnames, filenames in os.walk(root):
            if ".prks-maintenance" in dirpath:
                continue
            for name in filenames:
                self.assertNotEqual(name, "secret")
        return dest

    def test_parent_traversal_rejected(self):
        self._stage_malicious(
            [
                (MANIFEST_NAME, b"{}"),
                ("../prks_data.db", b"nope"),
            ]
        )

    def test_absolute_path_rejected(self):
        self._stage_malicious([(MANIFEST_NAME, b"{}"), ("/data/file", b"x")])

    def test_windows_drive_rejected(self):
        self._stage_malicious([(MANIFEST_NAME, b"{}"), (r"C:\file", b"x")])

    def test_nested_dotdot_rejected(self):
        self._stage_malicious(
            [(MANIFEST_NAME, b"{}"), ("files/pdfs/../../secret", b"x")]
        )

    def test_duplicate_members_rejected(self):
        dest = bind_storage(self._cfg(self._tmpdir()))
        path = os.path.join(self._tmpdir(), "dup.prks-backup")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(ARCHIVE_DB_PATH, b"a")
            zf.writestr(ARCHIVE_DB_PATH, b"b")
            zf.writestr(MANIFEST_NAME, b"{}")
        with self.assertRaises(RestoreError):
            stage_restore(dest, path)

    def test_symlink_member_rejected(self):
        dest = bind_storage(self._cfg(self._tmpdir()))
        path = os.path.join(self._tmpdir(), "link.prks-backup")
        info = zipfile.ZipInfo("files/pdfs/link")
        info.create_system = 3
        info.external_attr = (0o120777 & 0xFFFF) << 16
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(MANIFEST_NAME, b"{}")
            zf.writestr(info, b"/etc/passwd")
        with self.assertRaises(RestoreError):
            stage_restore(dest, path)

    def test_plain_zip_with_manifest_rejected(self):
        dest = bind_storage(self._cfg(self._tmpdir()))
        path = os.path.join(self._tmpdir(), "plain.zip")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(MANIFEST_NAME, json.dumps({"hello": "world"}).encode("utf-8"))
        with self.assertRaises(RestoreError):
            stage_restore(dest, path)

    def test_module_never_calls_extractall(self):
        src = Path(_PROJECT_DIR, "backend", "backup_restore.py").read_text(encoding="utf-8")
        self.assertNotIn("extractall(", src)


class TestRestoreRollback(BackupRestoreTestCase):
    def test_fail_after_old_db_moved_restores_original(self):
        lib = self._bind_library(title="Keep Me", pdf_name="keep.pdf")
        other = self._bind_library(title="Incoming", pdf_name="new.pdf", pdf_text="incoming")
        backup = create_backup(other["cfg"])
        bind_storage(lib["cfg"])
        staged = self._stage_copy(lib["cfg"], backup.archive_path)
        with self.assertRaises(RestoreError):
            apply_restore(
                lib["cfg"],
                staged.token,
                "RESTORE",
                rebind=bind_storage,
                fail_after="old_moved",
            )
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Keep Me"])
        self.assertTrue(os.path.isfile(os.path.join(lib["cfg"].pdfs_dir, "keep.pdf")))
        self.assertFalse(os.path.isfile(os.path.join(lib["cfg"].pdfs_dir, "new.pdf")))

    def test_fail_after_new_pdfs_installed_restores_original(self):
        lib = self._bind_library(title="Keep Me", pdf_name="keep.pdf")
        other = self._bind_library(title="Incoming", pdf_name="new.pdf", pdf_text="incoming")
        backup = create_backup(other["cfg"])
        bind_storage(lib["cfg"])
        staged = self._stage_copy(lib["cfg"], backup.archive_path)
        with self.assertRaises(RestoreError):
            apply_restore(
                lib["cfg"],
                staged.token,
                "RESTORE",
                rebind=bind_storage,
                fail_after="new_installed",
            )
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Keep Me"])
        self.assertTrue(os.path.isfile(os.path.join(lib["cfg"].pdfs_dir, "keep.pdf")))
        self.assertFalse(os.path.isfile(os.path.join(lib["cfg"].pdfs_dir, "new.pdf")))

    def test_stage_keeps_in_flight_upload_file(self):
        lib = self._bind_library()
        backup = create_backup(lib["cfg"])
        dest = bind_storage(self._cfg(self._tmpdir()))
        staging = os.path.join(dest.root, ".prks-maintenance", "restore-staging")
        os.makedirs(staging, exist_ok=True)
        upload = os.path.join(staging, ".upload-inflight")
        shutil.copy2(backup.archive_path, upload)
        staged = stage_restore(dest, upload)
        self.assertTrue(staged.verified)

    def test_confirm_required(self):
        lib = self._bind_library()
        backup = create_backup(lib["cfg"])
        dest = bind_storage(self._cfg(self._tmpdir()))
        staged = self._stage_copy(dest, backup.archive_path)
        with self.assertRaises(RestoreError) as ctx:
            apply_restore(dest, staged.token, "yes", rebind=bind_storage)
        self.assertEqual(ctx.exception.reason, "confirmation_required")

    def test_token_is_single_use(self):
        lib = self._bind_library()
        backup = create_backup(lib["cfg"])
        dest = bind_storage(self._cfg(self._tmpdir()))
        staged = self._stage_copy(dest, backup.archive_path)
        apply_restore(dest, staged.token, "RESTORE", rebind=bind_storage)
        dest2 = bind_storage(self._cfg(self._tmpdir()))
        with self.assertRaises(RestoreError) as ctx:
            apply_restore(dest2, staged.token, "RESTORE", rebind=bind_storage)
        self.assertEqual(ctx.exception.reason, "unknown_token")


class TestCrashJournalRecovery(BackupRestoreTestCase):
    def test_incomplete_journal_restores_previous(self):
        lib = self._bind_library(title="Original", pdf_name="orig.pdf")
        cfg = lib["cfg"]
        maint = os.path.join(cfg.root, ".prks-maintenance", "rollback", "txn1")
        os.makedirs(os.path.join(maint, "database"), exist_ok=True)
        os.makedirs(os.path.join(maint, "pdfs"), exist_ok=True)
        os.makedirs(os.path.join(maint, "people"), exist_ok=True)
        db_name = os.path.basename(cfg.db_path)
        os.replace(cfg.db_path, os.path.join(maint, "database", db_name))
        os.replace(cfg.pdfs_dir, os.path.join(maint, "pdfs"))
        os.replace(cfg.people_dir, os.path.join(maint, "people"))
        os.makedirs(cfg.pdfs_dir, exist_ok=True)
        with open(os.path.join(cfg.pdfs_dir, "partial.pdf"), "wb") as handle:
            handle.write(b"PARTIAL")
        journal = {
            "format": "prks-restore-journal",
            "format_version": 1,
            "transaction_id": "txn1",
            "phase": "installing_new",
            "components": {
                "database": {"old_moved": True, "new_installed": False},
                "pdfs": {"old_moved": True, "new_installed": True},
                "people": {"old_moved": True, "new_installed": False},
            },
        }
        os.makedirs(os.path.join(cfg.root, ".prks-maintenance"), exist_ok=True)
        with open(os.path.join(cfg.root, ".prks-maintenance", "restore-journal.json"), "w") as handle:
            json.dump(journal, handle)
        out = recover_incomplete_restore(cfg)
        self.assertEqual(out["outcome"], "restored_previous")
        bind_storage(cfg)
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Original"])
        self.assertTrue(os.path.isfile(os.path.join(cfg.pdfs_dir, "orig.pdf")))
        self.assertFalse(os.path.isfile(os.path.join(cfg.pdfs_dir, "partial.pdf")))

    def test_committed_journal_keeps_new_and_cleans_rollback(self):
        lib = self._bind_library(title="New Library", pdf_name="new.pdf")
        cfg = lib["cfg"]
        rollback = os.path.join(cfg.root, ".prks-maintenance", "rollback", "txn2")
        os.makedirs(rollback, exist_ok=True)
        with open(os.path.join(rollback, "leftover"), "w") as handle:
            handle.write("old")
        journal = {
            "format": "prks-restore-journal",
            "format_version": 1,
            "transaction_id": "txn2",
            "phase": "committed",
            "components": {
                "database": {"old_moved": True, "new_installed": True},
                "pdfs": {"old_moved": True, "new_installed": True},
                "people": {"old_moved": True, "new_installed": True},
            },
        }
        with open(os.path.join(cfg.root, ".prks-maintenance", "restore-journal.json"), "w") as handle:
            json.dump(journal, handle)
        out = recover_incomplete_restore(cfg)
        self.assertEqual(out["outcome"], "keep_restored")
        self.assertFalse(out["needs_reindex"])
        self.assertFalse(os.path.isdir(rollback))
        self.assertFalse(
            os.path.isfile(os.path.join(cfg.root, ".prks-maintenance", "restore-journal.json"))
        )
        bind_storage(cfg)
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["New Library"])

    def test_canonical_installed_journal_restores_previous(self):
        orig = self._bind_library(title="Original", pdf_name="orig.pdf", person_bytes=b"OLD-PORTRAIT")
        incoming = self._bind_library(
            title="Incoming",
            pdf_name="new.pdf",
            pdf_text="incoming",
            person_bytes=b"NEW-PORTRAIT",
        )
        cfg = orig["cfg"]
        txn = "txn-canonical"
        rollback = os.path.join(cfg.root, ".prks-maintenance", "rollback", txn)
        os.makedirs(os.path.join(rollback, "database"), exist_ok=True)
        db_name = os.path.basename(cfg.db_path)
        os.replace(cfg.db_path, os.path.join(rollback, "database", db_name))
        os.replace(cfg.pdfs_dir, os.path.join(rollback, "pdfs"))
        os.replace(cfg.people_dir, os.path.join(rollback, "people"))
        shutil.copy2(incoming["cfg"].db_path, cfg.db_path)
        shutil.copytree(incoming["cfg"].pdfs_dir, cfg.pdfs_dir)
        shutil.copytree(incoming["cfg"].people_dir, cfg.people_dir)
        flags = {
            "old_existed": True,
            "old_move_started": True,
            "old_moved": True,
            "new_install_started": True,
            "new_installed": True,
        }
        journal = {
            "format": "prks-restore-journal",
            "format_version": 1,
            "transaction_id": txn,
            "phase": "canonical_installed",
            "components": {
                "database": dict(flags),
                "pdfs": dict(flags),
                "people": dict(flags),
            },
        }
        os.makedirs(os.path.join(cfg.root, ".prks-maintenance"), exist_ok=True)
        with open(os.path.join(cfg.root, ".prks-maintenance", "restore-journal.json"), "w") as handle:
            json.dump(journal, handle)
        out = recover_incomplete_restore(cfg)
        self.assertEqual(out["outcome"], "restored_previous")
        self.assertFalse(out["needs_reindex"])
        bind_storage(cfg)
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Original"])
        self.assertTrue(os.path.isfile(os.path.join(cfg.pdfs_dir, "orig.pdf")))
        self.assertFalse(os.path.isfile(os.path.join(cfg.pdfs_dir, "new.pdf")))
        with open(os.path.join(cfg.people_dir, "portrait.webp"), "rb") as handle:
            self.assertEqual(handle.read(), b"OLD-PORTRAIT")


class TestRestoreCrashWindows(BackupRestoreTestCase):
    def _crash_and_recover_old_library(self, fail_after):
        lib = self._bind_library(title="Keep Me", pdf_name="keep.pdf", person_bytes=b"KEEP-PORTRAIT")
        other = self._bind_library(
            title="Incoming",
            pdf_name="new.pdf",
            pdf_text="incoming",
            person_bytes=b"NEW-PORTRAIT",
        )
        backup = create_backup(other["cfg"])
        bind_storage(lib["cfg"])
        staged = self._stage_copy(lib["cfg"], backup.archive_path)
        with self.assertRaises(RestoreCrash):
            apply_restore(
                lib["cfg"],
                staged.token,
                "RESTORE",
                rebind=bind_storage,
                fail_after=fail_after,
            )
        out = recover_incomplete_restore(lib["cfg"])
        self.assertEqual(out["outcome"], "restored_previous")
        self.assertFalse(out["needs_reindex"])
        bind_storage(lib["cfg"])
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Keep Me"])
        self.assertTrue(os.path.isfile(os.path.join(lib["cfg"].pdfs_dir, "keep.pdf")))
        self.assertFalse(os.path.isfile(os.path.join(lib["cfg"].pdfs_dir, "new.pdf")))
        with open(os.path.join(lib["cfg"].people_dir, "portrait.webp"), "rb") as handle:
            self.assertEqual(handle.read(), b"KEEP-PORTRAIT")
        settings = server_module.db.get_app_settings_response()
        self.assertEqual(settings["annotation_author"], "Backup Author")
        self.assertFalse(
            os.path.isfile(os.path.join(lib["cfg"].root, ".prks-maintenance", "restore-journal.json"))
        )

    def test_crash_after_old_move_started_database(self):
        self._crash_and_recover_old_library("old_move_started:database")

    def test_crash_after_old_renamed_before_old_moved_database(self):
        self._crash_and_recover_old_library("old_renamed:database")

    def test_crash_after_new_install_started_database(self):
        self._crash_and_recover_old_library("new_install_started:database")

    def test_crash_after_new_renamed_before_new_installed_database(self):
        self._crash_and_recover_old_library("new_renamed:database")

    def test_crash_after_old_move_started_pdfs(self):
        self._crash_and_recover_old_library("old_move_started:pdfs")

    def test_crash_after_old_renamed_before_old_moved_pdfs(self):
        self._crash_and_recover_old_library("old_renamed:pdfs")

    def test_crash_after_new_install_started_pdfs(self):
        self._crash_and_recover_old_library("new_install_started:pdfs")

    def test_crash_after_new_renamed_before_new_installed_pdfs(self):
        self._crash_and_recover_old_library("new_renamed:pdfs")


class TestDiskAccounting(BackupRestoreTestCase):
    def _file_bytes(self, cfg):
        total = backup_module._dir_size_bytes(cfg.pdfs_dir)
        total += backup_module._dir_size_bytes(cfg.people_dir)
        if backup_module.processing_is_under_storage(cfg):
            total += backup_module._dir_size_bytes(cfg.processing_dir)
        return total

    def test_backup_does_not_reserve_two_copies_of_pdf_library(self):
        blob = b"%PDF-1.4\n" + (b"X" * (2 * 1024 * 1024)) + b"\n%%EOF\n"
        lib = self._bind_library(extra_pdf_name="big.pdf", extra_pdf_bytes=blob)
        cfg = lib["cfg"]
        db_bytes = backup_module._db_on_disk_bytes(cfg)
        file_bytes = self._file_bytes(cfg)
        needed = backup_additional_bytes(db_bytes, file_bytes)
        old_needed = (db_bytes + file_bytes) * 2 + DISK_MARGIN_BYTES
        self.assertLess(needed, old_needed)
        with patch.object(backup_module, "_free_bytes", return_value=needed):
            backup = create_backup(cfg)
        self.assertTrue(os.path.isfile(backup.archive_path))

    def test_backup_rejects_when_snapshot_archive_margin_unavailable(self):
        lib = self._bind_library()
        cfg = lib["cfg"]
        needed = backup_additional_bytes(backup_module._db_on_disk_bytes(cfg), self._file_bytes(cfg))
        with patch.object(backup_module, "_free_bytes", return_value=needed - 1):
            with self.assertRaises(BackupError) as ctx:
                create_backup(cfg)
        self.assertEqual(ctx.exception.reason, "insufficient_storage")

    def test_restore_upload_space_uses_content_length_not_live_library(self):
        lib = self._bind_library()
        with patch.object(backup_module, "_free_bytes", return_value=0):
            with self.assertRaises(RestoreError) as ctx:
                require_restore_upload_space(lib["cfg"], 1024)
        self.assertEqual(ctx.exception.reason, "insufficient_storage")
        with patch.object(backup_module, "_free_bytes", return_value=1024 + DISK_MARGIN_BYTES):
            require_restore_upload_space(lib["cfg"], 1024)

    def test_extract_space_uses_declared_uncompressed_not_archive_again(self):
        lib = self._bind_library()
        backup = create_backup(lib["cfg"])
        dest = bind_storage(self._cfg(self._tmpdir()))
        copied = _copy_backup(backup.archive_path, os.path.join(self._tmpdir(), "upload"))
        with patch.object(backup_module, "_free_bytes", return_value=DISK_MARGIN_BYTES):
            with self.assertRaises(RestoreError) as ctx:
                stage_restore(dest, copied)
        self.assertEqual(ctx.exception.reason, "insufficient_storage")

    def test_commit_rename_does_not_require_live_plus_staged_copy(self):
        blob = b"%PDF-1.4\n" + (b"X" * (512 * 1024)) + b"\n%%EOF\n"
        lib = self._bind_library(title="Keep Me", pdf_name="keep.pdf")
        other = self._bind_library(
            title="Incoming",
            pdf_name="new.pdf",
            pdf_text="incoming",
            extra_pdf_name="big.pdf",
            extra_pdf_bytes=blob,
        )
        backup = create_backup(other["cfg"])
        bind_storage(lib["cfg"])
        staged = self._stage_copy(lib["cfg"], backup.archive_path)
        tree_dir = os.path.join(
            lib["cfg"].root, ".prks-maintenance", "restore-staging", staged.token, "tree"
        )
        live_size = backup_module._db_on_disk_bytes(lib["cfg"]) + self._file_bytes(lib["cfg"])
        staged_size = backup_module._dir_size_bytes(tree_dir)
        old_needed = live_size + staged_size + DISK_MARGIN_BYTES
        self.assertGreater(old_needed, DISK_MARGIN_BYTES)
        with patch.object(backup_module, "_free_bytes", return_value=DISK_MARGIN_BYTES):
            out = apply_restore(lib["cfg"], staged.token, "RESTORE", rebind=bind_storage)
        self.assertTrue(out["restored"])
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Incoming"])


class TestSelfVerificationAndChunks(BackupRestoreTestCase):
    def test_invalid_hash_is_not_served(self):
        lib = self._bind_library()

        def corrupt(archive_path):
            with open(archive_path, "r+b") as handle:
                handle.seek(-8, os.SEEK_END)
                handle.write(b"XXXXXXXX")

        with self.assertRaises(BackupError):
            create_backup(lib["cfg"], post_archive_hook=corrupt)

    def test_chunked_copy_never_reads_whole_file(self):
        payload = b"A" * (IO_CHUNK_SIZE * 3 + 17)
        src = io.BytesIO(payload)
        dest = io.BytesIO()
        reads = []
        original_read = src.read

        def spy(n=-1):
            reads.append(n)
            if n is None or n < 0:
                raise AssertionError("unbounded read")
            return original_read(n)

        src.read = spy
        size, digest = hash_and_copy(src, dest, chunk_size=IO_CHUNK_SIZE)
        self.assertEqual(size, len(payload))
        self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
        self.assertTrue(reads)
        self.assertTrue(all(r == IO_CHUNK_SIZE for r in reads[:-1]))
        self.assertLessEqual(max(reads), IO_CHUNK_SIZE)

    def test_iter_file_chunks_bounded(self):
        src = io.BytesIO(b"abcdef")
        chunks = list(iter_file_chunks(src, chunk_size=2))
        self.assertEqual(chunks, [b"ab", b"cd", b"ef"])

    def test_tests_never_use_production_storage(self):
        with self.assertRaises(RuntimeError):
            StorageConfig.for_testing("/data")
        repo_data = os.path.join(_PROJECT_DIR, "data")
        with self.assertRaises(RuntimeError):
            StorageConfig.for_testing(repo_data)


class TestBackupRestoreHTTP(BackupRestoreTestCase):
    def test_download_and_restore_via_http(self):
        import http.client
        import socket

        lib = self._bind_library(title="HTTP Work", pdf_text="http unique phrase")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        thread = threading.Thread(
            target=server_module.run_server, args=(port, "127.0.0.1"), daemon=True
        )
        thread.start()
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                conn.request("GET", "/api/works")
                res = conn.getresponse()
                res.read()
                conn.close()
                if res.status == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("server did not start")

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        payload = b"{}"
        conn.request(
            "POST",
            "/api/backups/progress",
            body=payload,
            headers={
                "Host": "127.0.0.1",
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
            },
        )
        res = conn.getresponse()
        body = res.read()
        self.assertEqual(res.status, 200)
        events = [json.loads(line) for line in body.decode("utf-8").splitlines() if line.strip()]
        self.assertEqual(events[-1]["phase"], "ready")
        token = events[-1]["token"]
        conn.close()

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        conn.request(
            "GET",
            "/api/backups/download?token=" + token,
            headers={"Host": "127.0.0.1"},
        )
        res = conn.getresponse()
        body = res.read()
        self.assertEqual(res.status, 200)
        self.assertIn(".prks-backup", res.getheader("Content-Disposition") or "")
        archive = os.path.join(self._tmpdir(), "from-http.prks-backup")
        with open(archive, "wb") as handle:
            handle.write(body)
        conn.close()

        dest = bind_storage(self._cfg(self._tmpdir()))
        staged = self._stage_copy(dest, archive)
        out = apply_restore(dest, staged.token, "RESTORE", rebind=bind_storage)
        self.assertTrue(out["restored"])
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["HTTP Work"])

    def test_progress_stream_then_token_download(self):
        import http.client
        import socket

        self._bind_library(title="HTTP Progress Work")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        thread = threading.Thread(
            target=server_module.run_server, args=(port, "127.0.0.1"), daemon=True
        )
        thread.start()
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                conn.request("GET", "/api/works")
                res = conn.getresponse()
                res.read()
                conn.close()
                if res.status == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("server did not start")

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        payload = b"{}"
        conn.request(
            "POST",
            "/api/backups/progress",
            body=payload,
            headers={
                "Host": "127.0.0.1",
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
            },
        )
        res = conn.getresponse()
        body = res.read().decode("utf-8")
        self.assertEqual(res.status, 200)
        self.assertIn("ndjson", (res.getheader("Content-Type") or ""))
        conn.close()
        events = [json.loads(line) for line in body.splitlines() if line.strip()]
        self.assertTrue(events)
        self.assertEqual(events[-1]["phase"], "ready")
        token = events[-1]["token"]
        self.assertTrue(token)
        self.assertTrue(all("path" not in ev for ev in events))

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        conn.request(
            "GET",
            "/api/backups/download?token=" + token,
            headers={"Host": "127.0.0.1"},
        )
        res = conn.getresponse()
        blob = res.read()
        self.assertEqual(res.status, 200)
        self.assertIn(".prks-backup", res.getheader("Content-Disposition") or "")
        self.assertGreater(len(blob), 64)
        conn.close()

    def test_restore_post_requires_origin_when_present(self):
        import http.client
        import socket

        self._bind_library()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        thread = threading.Thread(
            target=server_module.run_server, args=(port, "127.0.0.1"), daemon=True
        )
        thread.start()
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                conn.request("GET", "/api/works")
                res = conn.getresponse()
                res.read()
                conn.close()
                if res.status == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("server did not start")
        payload = json.dumps({"token": "x", "confirm": "RESTORE"}).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/backups/restore",
            body=payload,
            headers={
                "Host": "127.0.0.1",
                "Origin": "http://evil.example",
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
            },
        )
        res = conn.getresponse()
        res.read()
        conn.close()
        self.assertEqual(res.status, 403)

    def test_download_without_token_does_not_create_backup(self):
        import http.client
        import socket

        self._bind_library()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        thread = threading.Thread(
            target=server_module.run_server, args=(port, "127.0.0.1"), daemon=True
        )
        thread.start()
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                conn.request("GET", "/api/works")
                res = conn.getresponse()
                res.read()
                conn.close()
                if res.status == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("server did not start")

        with patch.object(backup_module, "create_backup") as mocked:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/backups/download", headers={"Host": "127.0.0.1"})
            res = conn.getresponse()
            res.read()
            conn.close()
            self.assertEqual(res.status, 400)
            mocked.assert_not_called()

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "GET",
                "/api/backups/download?token=aaaaaaaaaaaaaaaa",
                headers={"Host": "127.0.0.1"},
            )
            res = conn.getresponse()
            res.read()
            conn.close()
            self.assertEqual(res.status, 404)
            mocked.assert_not_called()

    def test_get_progress_does_not_create_backup(self):
        import http.client
        import socket

        self._bind_library()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        thread = threading.Thread(
            target=server_module.run_server, args=(port, "127.0.0.1"), daemon=True
        )
        thread.start()
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                conn.request("GET", "/api/works")
                res = conn.getresponse()
                res.read()
                conn.close()
                if res.status == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("server did not start")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/backups/progress", headers={"Host": "127.0.0.1"})
        res = conn.getresponse()
        res.read()
        conn.close()
        self.assertEqual(res.status, 404)

    def test_progress_post_requires_json_content_type(self):
        import http.client
        import socket

        self._bind_library()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        thread = threading.Thread(
            target=server_module.run_server, args=(port, "127.0.0.1"), daemon=True
        )
        thread.start()
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                conn.request("GET", "/api/works")
                res = conn.getresponse()
                res.read()
                conn.close()
                if res.status == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("server did not start")
        payload = b"{}"
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/backups/progress",
            body=payload,
            headers={
                "Host": "127.0.0.1",
                "Content-Length": str(len(payload)),
            },
        )
        res = conn.getresponse()
        res.read()
        conn.close()
        self.assertEqual(res.status, 415)

    def test_stage_rejects_upload_when_content_length_exceeds_free_space(self):
        import http.client
        import socket

        self._bind_library()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        thread = threading.Thread(
            target=server_module.run_server, args=(port, "127.0.0.1"), daemon=True
        )
        thread.start()
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                conn.request("GET", "/api/works")
                res = conn.getresponse()
                res.read()
                conn.close()
                if res.status == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("server did not start")
        payload = b"not-a-zip"
        with patch.object(backup_module, "_free_bytes", return_value=0):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                "/api/backups/stage",
                body=payload,
                headers={
                    "Host": "127.0.0.1",
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(payload)),
                },
            )
            res = conn.getresponse()
            body = res.read()
            conn.close()
        self.assertEqual(res.status, 400)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data.get("reason"), "insufficient_storage")


if __name__ == "__main__":
    unittest.main()
