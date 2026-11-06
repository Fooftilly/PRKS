"""Restore R0 (archive checks) and R1 (staging) (L-B-1 #639; §11.4, §13.3).

§13.3 "Restore archive containment": absolute, drive, ``..``, backslash and
duplicate members, links and special entries, members outside the layout or
missing from it, and tampered bytes are refused before anything is staged.
The resource bounds stay, and a valid archive of very compressible content
passes them while a hostile over-ratio member does not (SQLite audit #986).
Format 1 and newer formats are refused with a clear message, and so is an
archive of another schema.

§13.3 "Restore staging isolation": staging builds ``restore_<run>`` from the
live tables and leaves them untouched, recomputes generated keys, maps the
Library, users and devices (into the same and into a new instance), checks
every foreign key against the staged parents (a parent missing from the
archive is refused even when it still exists live), refuses entity IDs held
by another Library and a device of another account, never writes
``prks.devices``, and stages objects under ``restore_run_objects``: written
when missing, reused when identical, refused when different.
"""

from __future__ import annotations

import csv
import sys
import hashlib
import io
import json
import os
import shutil
import tempfile
import uuid
import zipfile
from unittest import mock
from collections.abc import Callable
from typing import Any, Optional

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.domain.maintenance import archive as r0
from backend.domain.maintenance import staging
from backend.domain.maintenance.archive import ArchiveRefused, Limits
from backend.entity_ids import generate
from backend.domain.maintenance.connection import maintenance_connection
from backend.storage.objects import LocalFilesystemStorage, StorageKey
from tests.pg.facade_context import provision_actor
from tests.pg.test_backup_export import BackupTestCase


csv.field_size_limit(sys.maxsize)


def rewrite(path: str, out: str, *, change: Optional[dict[str, Optional[bytes]]] = None,
            extra: Optional[dict[str, bytes]] = None, fix_manifest: bool = True,
            manifest_edit: Optional[Callable[[dict[str, Any]], None]] = None,
            info_edit: Optional[Callable[[zipfile.ZipInfo], None]] = None,
            compress: Optional[set[str]] = None) -> str:
    """A copy of an archive with members changed (None drops one), added, or the manifest edited."""
    change = change or {}
    with zipfile.ZipFile(path) as source:
        members = {name: source.read(name) for name in source.namelist()}
    manifest = json.loads(members.pop("manifest.json"))
    for name, data in change.items():
        if data is None:
            members.pop(name, None)
        else:
            members[name] = data
    if fix_manifest:
        for name, data in members.items():
            entry = _entry(manifest, name)
            if entry is not None:
                entry["size"] = len(data)
                entry["sha256"] = hashlib.sha256(data).hexdigest()
                if "rows" in entry:
                    entry["rows"] = max(0, len(list(csv.reader(io.StringIO(data.decode())))) - 1)
    if manifest_edit is not None:
        manifest_edit(manifest)
    members["manifest.json"] = json.dumps(manifest).encode()
    members.update(extra or {})
    with zipfile.ZipFile(out, "w") as target:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED if compress and name in compress else zipfile.ZIP_STORED
            if info_edit is not None:
                info_edit(info)
            target.writestr(info, data)
    return out


def _entry(manifest: dict[str, Any], name: str) -> Optional[dict[str, Any]]:
    if name.startswith("db/"):
        return manifest["tables"].get("prks." + name[3:-4])
    if name == "identities/users.csv":
        return manifest["identities"]["users"]
    if name == "identities/devices.csv":
        return manifest["identities"]["devices"]
    if name.startswith("objects/"):
        return manifest["objects"].get(name[len("objects/"):])
    return None


class StagingTestCase(BackupTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.staging_dir = tempfile.mkdtemp(prefix="prks-pg-restore-")
        self.addCleanup(shutil.rmtree, self.staging_dir, True)

    def path(self, name: str) -> str:
        return os.path.join(self.staging_dir, name)

    def refused(self, path: str, reason: str, **kwargs: Any) -> ArchiveRefused:
        with self.assertRaises(ArchiveRefused) as caught:
            r0.open_archive(path, staging_dir=self.staging_dir, **kwargs)
        self.assertEqual(caught.exception.reason, reason, caught.exception.message)
        return caught.exception

    def run_row(self, db: Optional[Database] = None, library_id: Optional[str] = None) -> uuid.UUID:
        rows = (db or self.db).command(lambda tx: tx.execute(
            "INSERT INTO prks.restore_runs (library_id) VALUES (%s) RETURNING id",
            (library_id or self.actor.library_id,)).fetchall())
        return rows[0]["id"]

    def stage(self, path: str, *, conninfo: Optional[str] = None, library_id: Optional[str] = None,
              owner: Optional[str] = None, db: Optional[Database] = None,
              storage: Any = "default") -> staging.Staged:
        archive = r0.open_archive(path, staging_dir=self.staging_dir)
        self.addCleanup(archive.close)
        run_id = self.run_row(db, library_id)
        with maintenance_connection(conninfo or self.conninfo) as conn:
            r0.check_schema(conn, archive)
            try:
                return staging.stage(conn, archive, run_id=run_id,
                                     target_library_id=library_id or self.actor.library_id,
                                     owner_user_id=owner or self.actor.user_id,
                                     storage=self.objects if storage == "default" else storage)
            except BaseException:
                staging.drop_staging(conn, run_id)
                raise

    def staged(self, result: staging.Staged, table: str, db: Optional[Database] = None) -> list[dict[str, Any]]:
        query = f"SELECT * FROM {result.schema}.{table}"
        return (db or self.db).command(lambda tx: tx.execute(query.encode()).fetchall())


class ArchiveContainmentTests(StagingTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.create(self.put(b"%PDF one"))
        self.good = self.backup().path

    def test_a_first_party_archive_passes(self) -> None:
        archive = r0.open_archive(self.good, staging_dir=self.staging_dir)
        self.assertEqual(archive.library_id, self.actor.library_id)
        self.assertEqual(len(archive.objects), 1)

    def test_unsafe_member_names_are_refused(self) -> None:
        for bad in ("/etc/passwd", "C:/x", "objects/../x", "./manifest2", "a\\b", "db//works.csv", "dir/"):
            with self.subTest(name=bad):
                out = rewrite(self.good, self.path("bad.zip"), extra={bad: b"x"})
                self.refused(out, "unsafe_archive_path")

    def test_links_and_special_entries_are_refused(self) -> None:
        def as_symlink(info: zipfile.ZipInfo) -> None:
            if info.filename == "db/works.csv":
                info.external_attr = 0o120777 << 16

        self.refused(rewrite(self.good, self.path("link.zip"), info_edit=as_symlink), "unsafe_archive_entry")

    def test_duplicate_members_are_refused(self) -> None:
        out = rewrite(self.good, self.path("dup.zip"))
        with zipfile.ZipFile(out, "a") as archive, self.assertWarns(UserWarning):
            archive.writestr("db/works.csv", b"again")
        self.refused(out, "duplicate_entry")

    def test_members_outside_the_layout_or_missing_are_refused(self) -> None:
        self.refused(rewrite(self.good, self.path("extra.zip"), extra={"db/evil.csv": b"x"}), "layout")
        self.refused(rewrite(self.good, self.path("missing.zip"), change={"db/works.csv": None},
                             fix_manifest=False), "layout")

    def test_tampered_bytes_are_refused(self) -> None:
        with zipfile.ZipFile(self.good) as source:
            works = source.read("db/works.csv")
        out = rewrite(self.good, self.path("tamper.zip"), change={"db/works.csv": works.replace(b"Created", b"Cr3ated")},
                      fix_manifest=False)
        self.refused(out, "hash_mismatch")

    def test_compression_bounds_pass_first_party_output_and_refuse_bombs(self) -> None:
        # Very compressible content stays stored in a first-party archive, so
        # its ratio is 1 however compressible it is.
        self.sql("UPDATE prks.works SET abstract = %s", ("a" * 2_000_000,))
        compressible = self.backup().path
        r0.open_archive(compressible, staging_dir=self.staging_dir)
        # The same bytes deflated by a hostile writer exceed the ratio limit.
        bomb = rewrite(compressible, self.path("bomb.zip"), compress={"db/works.csv"})
        self.refused(bomb, "compression_ratio")

    def test_entry_count_size_and_free_space_bounds(self) -> None:
        self.refused(self.good, "too_many_entries", limits=Limits(max_entries=5))
        self.refused(self.good, "too_large", limits=Limits(max_uncompressed_bytes=100))
        self.refused(self.good, "disk_space", free_bytes=lambda _path: 1024)

    def test_format_1_and_newer_formats_are_refused_clearly(self) -> None:
        def version(n: int) -> Callable[[dict[str, Any]], None]:
            return lambda manifest: manifest.__setitem__("format_version", n)

        error = self.refused(rewrite(self.good, self.path("v1.zip"), manifest_edit=version(1)), "format_1")
        self.assertIn("SQLite", error.message)
        self.refused(rewrite(self.good, self.path("v3.zip"), manifest_edit=version(3)), "format_newer")
        self.refused(rewrite(self.good, self.path("other.zip"),
                             manifest_edit=lambda m: m.__setitem__("format", "zip")), "not_a_backup")
        with open(self.path("plain.txt"), "wb") as handle:
            handle.write(b"not a zip")
        self.refused(self.path("plain.txt"), "not_a_backup")

    def test_an_archive_of_another_schema_is_refused(self) -> None:
        def drop_column(manifest: dict[str, Any]) -> None:
            manifest["interchange"]["prks.works"] = manifest["interchange"]["prks.works"][:-1]

        def other_fingerprint(manifest: dict[str, Any]) -> None:
            manifest["schema_fingerprint"] = "0" * 64

        for name, edit in (("cols.zip", drop_column), ("fp.zip", other_fingerprint)):
            with self.subTest(case=name):
                archive = r0.open_archive(rewrite(self.good, self.path(name), manifest_edit=edit),
                                          staging_dir=self.staging_dir)
                with maintenance_connection(self.conninfo) as conn, self.assertRaises(ArchiveRefused) as caught:
                    r0.check_schema(conn, archive)
                self.assertEqual(caught.exception.reason, "schema_mismatch")


    def test_encrypted_or_corrupt_members_are_refused_as_unreadable(self) -> None:
        encrypted = rewrite(self.good, self.path("enc.zip"))
        with open(encrypted, "r+b") as handle:  # set the "encrypted" flag of works.csv's directory entry
            raw = handle.read()
            entry = raw.index(b"PK\x01\x02")
            while raw[entry + 46:entry + 46 + len(b"db/works.csv")] != b"db/works.csv":
                entry = raw.index(b"PK\x01\x02", entry + 4)
            handle.seek(entry + 8)
            handle.write((int.from_bytes(raw[entry + 8:entry + 10], "little") | 0x1).to_bytes(2, "little"))
        self.refused(encrypted, "not_a_backup")
        deflated = rewrite(self.good, self.path("deflate.zip"), compress={"db/works.csv"})
        with zipfile.ZipFile(deflated) as source:
            info = source.getinfo("db/works.csv")
        with open(deflated, "r+b") as handle:  # the compressed bytes become an invalid deflate stream
            handle.seek(info.header_offset + 26)
            name_len, extra_len = int.from_bytes(handle.read(2), "little"), int.from_bytes(handle.read(2), "little")
            handle.seek(info.header_offset + 30 + name_len + extra_len)
            handle.write(b"\xff" * info.compress_size)
        self.refused(deflated, "not_a_backup")

    def test_a_zero_length_read_of_a_member_returns_nothing_and_refuses_nothing(self) -> None:
        archive = r0.open_archive(self.good, staging_dir=self.staging_dir)
        self.addCleanup(archive.close)
        member = archive.tables["prks.works"]
        with archive.open_member(member) as handle:
            self.assertEqual(handle.read(0), b"")
            data = b""
            while chunk := handle.read(7):
                data += chunk
        self.assertEqual(len(data), member.size)

    def test_refusals_never_echo_the_manifests_own_text(self) -> None:
        secret = "Private Diary Title"
        error = self.refused(rewrite(self.good, self.path("v.zip"),
                                     manifest_edit=lambda m: m.__setitem__("format_version", secret)), "manifest")
        self.assertNotIn(secret, error.message)

        def other_schema(manifest: dict[str, Any]) -> None:
            manifest["schema_version"] = secret
            manifest["schema_fingerprint"] = "0" * 64

        archive = r0.open_archive(rewrite(self.good, self.path("s.zip"), manifest_edit=other_schema),
                                  staging_dir=self.staging_dir)
        self.addCleanup(archive.close)
        with maintenance_connection(self.conninfo) as conn, self.assertRaises(ArchiveRefused) as caught:
            r0.check_schema(conn, archive)
        self.assertNotIn(secret, caught.exception.message)


class StagingTests(StagingTestCase):
    def test_a_file_put_in_the_archives_path_after_r0_is_never_read(self) -> None:
        self.create(self.put(b"%PDF pinned"))
        result = self.backup()
        archive = r0.open_archive(result.path, staging_dir=self.staging_dir)
        self.addCleanup(archive.close)
        works = source_text(result.path, "db/works.csv")
        os.replace(rewrite(result.path, self.path("swap.zip"),
                           change={"db/works.csv": works.replace("Created", "Swapped").encode()}), result.path)
        run_id = self.run_row()
        with maintenance_connection(self.conninfo) as conn:
            r0.check_schema(conn, archive)
            staged = staging.stage(conn, archive, run_id=run_id, target_library_id=self.actor.library_id,
                                   owner_user_id=self.actor.user_id, storage=self.objects)
        self.addCleanup(self.drop, staged)
        self.assertNotIn("Swapped", json.dumps(self.staged(staged, "works"), default=str))

    def test_member_bytes_changed_in_place_after_r0_are_refused(self) -> None:
        self.create(self.put(b"%PDF in place"))
        result = self.backup()
        archive = r0.open_archive(result.path, staging_dir=self.staging_dir)
        self.addCleanup(archive.close)
        with open(result.path, "r+b") as handle:
            raw = handle.read()
            at = raw.index(b"Created")
            handle.seek(at)
            handle.write(b"Cr3ated")
        run_id = self.run_row()
        with maintenance_connection(self.conninfo) as conn, self.assertRaises(ArchiveRefused) as caught:
            r0.check_schema(conn, archive)
            try:
                staging.stage(conn, archive, run_id=run_id, target_library_id=self.actor.library_id,
                              owner_user_id=self.actor.user_id, storage=self.objects)
            finally:
                staging.drop_staging(conn, run_id)
        # The ZIP's own CRC or the manifest hash catches it, whichever ends the read first.
        self.assertIn(caught.exception.reason, {"hash_mismatch", "not_a_backup"})

    def test_a_users_count_other_than_the_manifests_is_refused(self) -> None:
        self.create(self.put())
        result = self.backup()

        def more_users(manifest: dict[str, Any]) -> None:
            manifest["identities"]["users"]["rows"] += 1

        with self.assertRaises(ArchiveRefused) as caught:
            self.stage(rewrite(result.path, self.path("users.zip"), manifest_edit=more_users))
        self.assertEqual(caught.exception.reason, "row_count")

    def test_the_lease_is_renewed_while_a_member_streams(self) -> None:
        from backend.domain.maintenance.format import IO_CHUNK_SIZE

        self.create(self.put(b"%PDF " + os.urandom(4 * IO_CHUNK_SIZE)))
        result = self.backup()
        for key in list(self.objects.iter_keys("asset-objects")):
            self.objects.delete(key)
        hashing = mock.Mock()
        archive = r0.open_archive(result.path, staging_dir=self.staging_dir, renew=hashing)
        self.addCleanup(archive.close)
        self.assertGreaterEqual(hashing.call_count, 4)
        calls: list[str] = []
        real_put = self.objects.put_new

        def put_new(*args: Any, **kwargs: Any) -> Any:
            calls.append("put")
            return real_put(*args, **kwargs)

        run_id = self.run_row()
        with maintenance_connection(self.conninfo) as conn, \
                mock.patch.object(self.objects, "put_new", side_effect=put_new):
            r0.check_schema(conn, archive)
            staged = staging.stage(conn, archive, run_id=run_id, target_library_id=self.actor.library_id,
                                   owner_user_id=self.actor.user_id, storage=self.objects,
                                   renew=lambda: calls.append("renew"))
        self.addCleanup(self.drop, staged)
        # Renewed per chunk inside the one object's write, not only after it.
        after_put = calls[calls.index("put") + 1:]
        self.assertGreaterEqual(after_put.count("renew"), 5)

    def test_staging_rebuilds_the_library_beside_the_live_tables(self) -> None:
        obj = self.put(b"%PDF staged")
        work_id = self.create(obj)
        tag = self.library.add_tag("Philosophy")
        self.library.add_tag_to_work(work_id, tag["id"])
        self.ledger_row(self.actor, work_id)
        result = self.backup()
        before = self.sql("SELECT count(*) AS n FROM prks.works")[0]["n"]
        staged = self.stage(result.path)
        self.addCleanup(self.drop, staged)
        self.assertEqual([w["id"] for w in self.staged(staged, "works")], [work_id])
        (tag_row,) = self.staged(staged, "tags")
        live_key = self.sql("SELECT name_key FROM prks.tags WHERE id = %s", (tag["id"],))[0]["name_key"]
        self.assertEqual(tag_row["name_key"], live_key)  # recomputed, never exported
        self.assertEqual(self.sql("SELECT count(*) AS n FROM prks.works")[0]["n"], before)
        self.assertEqual((staged.new_devices, staged.objects_written, staged.objects_reused), (0, 0, 1))
        self.assertEqual(self.sql("SELECT namespace, name FROM prks.restore_run_objects WHERE run_id = %s",
                                  (staged.run_id,)), [{"namespace": "asset-objects", "name": obj.key.name}])

    def drop(self, staged: staging.Staged) -> None:
        with maintenance_connection(self.conninfo) as conn:
            staging.drop_staging(conn, staged.run_id)
            staging.drop_staging(conn, staged.run_id)  # idempotent
        self.assertEqual(self.sql("SELECT count(*) AS n FROM pg_namespace WHERE nspname = %s",
                                  (staged.schema,))[0]["n"], 0)

    def test_a_missing_object_is_written_and_a_different_one_is_never_overwritten(self) -> None:
        obj = self.put(b"%PDF original")
        self.create(obj)
        result = self.backup()
        self.objects.delete(obj.key)
        staged = self.stage(result.path)
        self.addCleanup(self.drop, staged)
        self.assertEqual(staged.objects_written, 1)
        with self.objects.open_read(obj.key) as handle:
            self.assertEqual(handle.read(), b"%PDF original")
        self.objects.delete(obj.key)
        self.objects.put_new(obj.key, lambda stream: stream.write(b"%PDF imposter"))
        with self.assertRaises(ArchiveRefused) as caught:
            self.stage(result.path)
        self.assertEqual(caught.exception.reason, "object_conflict")
        with self.objects.open_read(obj.key) as handle:
            self.assertEqual(handle.read(), b"%PDF imposter")

    def test_a_parent_missing_from_the_archive_is_refused_even_if_it_exists_live(self) -> None:
        work_id = self.create(self.put())
        tag = self.library.add_tag("Kept live")
        self.library.add_tag_to_work(work_id, tag["id"])
        result = self.backup()
        with zipfile.ZipFile(result.path) as source:
            tags = source.read("db/tags.csv").decode()
        header = tags.splitlines()[0] + "\n"
        keys = source_text(result.path, "db/tag_keys.csv").splitlines()[0] + "\n"
        lifecycle = source_text(result.path, "db/sync_tag_lifecycle.csv").splitlines()[0] + "\n"
        out = rewrite(result.path, self.path("orphan.zip"), change={
            "db/tags.csv": header.encode(), "db/tag_keys.csv": keys.encode(),
            "db/sync_tag_lifecycle.csv": lifecycle.encode()})
        with self.assertRaises(ArchiveRefused) as caught:
            self.stage(out)
        self.assertEqual(caught.exception.reason, "foreign_key")
        self.assertIn("prks.work_tags", caught.exception.message)

    def test_ids_still_held_by_another_library_are_refused(self) -> None:
        self.create(self.put())
        result = self.backup()
        other = self.other_actor()
        with self.assertRaises(ArchiveRefused) as caught:
            self.stage(result.path, library_id=other.library_id, owner=other.user_id)
        self.assertEqual(caught.exception.reason, "ids_in_other_library")

    def test_staging_reads_every_member_through_one_open_archive(self) -> None:
        for n in range(3):
            self.create(self.put(b"%PDF " + bytes([65 + n])))
        result = self.backup()
        for key in list(self.objects.iter_keys("asset-objects")):
            self.objects.delete(key)  # every object is written from the archive
        real = zipfile.ZipFile
        with mock.patch.object(r0.zipfile, "ZipFile", side_effect=real) as opened:
            staged = self.stage(result.path)
        self.addCleanup(self.drop, staged)
        self.assertEqual(staged.objects_written, 3)
        self.assertEqual(opened.call_count, 1)  # R0 validates it, and staging reads that same handle

    def test_a_partial_global_key_held_by_another_library_is_refused(self) -> None:
        obj = self.put(b"%PDF upload")
        item_sql = ("INSERT INTO prks.processing_items (library_id, id, original_filename, object_locator, sha256, "
                    "byte_size, upload_key) VALUES (%s, %s, 'a.pdf', %s, %s, %s, %s)")
        mine = generate("PI")
        self.sql(item_sql, (self.actor.library_id, mine, obj.key.name, obj.sha256, obj.size, "up-1"))
        result = self.backup()
        # The upload left this Library and another one now holds its key (ready items only are unique).
        self.sql("UPDATE prks.processing_items SET status = 'deleted', object_locator = NULL, deleted_at = now() "
                 "WHERE id = %s", (mine,))
        other = self.other_actor()
        self.sql(item_sql, (other.library_id, generate("PI"), obj.key.name, obj.sha256, obj.size, "up-2"))
        with self.assertRaises(ArchiveRefused) as caught:
            self.stage(result.path)
        self.assertEqual(caught.exception.reason, "ids_in_other_library")

    def test_library_scoped_keys_shared_with_another_library_are_no_collision(self) -> None:
        other = self.other_actor()
        for library_id in (self.actor.library_id, other.library_id):
            self.sql("INSERT INTO prks.library_settings (library_id, key, value) VALUES (%s, 'theme', 'dark')",
                     (library_id,))
        result = self.backup()  # verified by staging, so this alone fails on a false collision
        staged = self.stage(result.path)
        self.addCleanup(self.drop, staged)
        self.assertEqual([r["key"] for r in self.staged(staged, "library_settings")], ["theme"])

    def test_a_device_of_another_account_is_refused_with_its_operation_count(self) -> None:
        self.ledger_row(self.actor)
        self.ledger_row(self.actor, "W-2")
        result = self.backup()
        intruder = self.other_actor()
        self.sql("DELETE FROM prks.sync_operations")
        self.sql("UPDATE prks.devices SET user_id = %s WHERE id = %s", (intruder.user_id, self.actor.device_id))
        with self.assertRaises(ArchiveRefused) as caught:
            self.stage(result.path)
        self.assertEqual(caught.exception.reason, "device_collision")
        self.assertIn("2 recorded operation", caught.exception.message)


    def test_rows_must_name_exactly_the_archives_objects(self) -> None:
        obj = self.put(b"%PDF referenced")
        self.create(obj)
        result = self.backup()
        member = f"objects/asset-objects/{obj.key.name}"

        def drop_entry(manifest: dict[str, Any]) -> None:
            manifest["objects"].pop(f"asset-objects/{obj.key.name}")

        missing = rewrite(result.path, self.path("missing.zip"), change={member: None}, manifest_edit=drop_entry)
        with self.assertRaises(ArchiveRefused) as caught:
            self.stage(missing)
        self.assertEqual(caught.exception.reason, "object_missing")

        stray = b"%PDF stray"

        def add_entry(manifest: dict[str, Any]) -> None:
            manifest["objects"]["asset-objects/stray.pdf"] = {
                "size": len(stray), "sha256": hashlib.sha256(stray).hexdigest()}

        extra = rewrite(result.path, self.path("extra.zip"), manifest_edit=add_entry,
                        extra={"objects/asset-objects/stray.pdf": stray})
        with self.assertRaises(ArchiveRefused) as caught:
            self.stage(extra)
        self.assertEqual(caught.exception.reason, "object_unreferenced")

        assets = source_text(result.path, "db/assets.csv").replace(obj.sha256, hashlib.sha256(b"other").hexdigest())
        mismatch = rewrite(result.path, self.path("mismatch.zip"), change={"db/assets.csv": assets.encode()})
        with self.assertRaises(ArchiveRefused) as caught:
            self.stage(mismatch)
        self.assertEqual(caught.exception.reason, "object_mismatch")

    def test_role_tiebreaks_are_renumbered_from_the_live_sequence_in_order(self) -> None:
        work_id = self.create(self.put())
        people = [generate("P") for _ in range(3)]
        for person_id in people:
            self.sql("INSERT INTO prks.persons (library_id, id, last_name) VALUES (%s, %s, 'X')",
                     (self.actor.library_id, person_id))
        for person_id in reversed(people):  # all tie on order_index; seq alone orders them
            self.sql("INSERT INTO prks.roles (library_id, work_id, person_id, role_type, order_index) "
                     "VALUES (%s, %s, %s, 'Author', 0)", (self.actor.library_id, work_id, person_id))
        archived = [r["person_id"] for r in self.sql("SELECT person_id FROM prks.roles ORDER BY seq")]
        live_seqs = {r["seq"] for r in self.sql("SELECT seq FROM prks.roles")}
        result = self.backup()
        staged = self.stage(result.path)
        self.addCleanup(self.drop, staged)
        rows = sorted(self.staged(staged, "roles"), key=lambda r: r["seq"])
        self.assertEqual([r["person_id"] for r in rows], archived)
        self.assertFalse({r["seq"] for r in rows} & live_seqs)  # no other Library's value can be hit
        later = self.sql("SELECT nextval(pg_get_serial_sequence('prks.roles', 'seq')) AS v")[0]["v"]
        self.assertGreater(later, max(r["seq"] for r in rows))  # R4 needs no setval

class NewInstanceTests(StagingTestCase):
    def test_a_restore_into_a_new_instance_maps_library_users_and_devices(self) -> None:
        obj = self.put(b"%PDF travelling")
        work_id = self.create(obj)
        self.ledger_row(self.actor, work_id)
        result = self.backup()
        name = self.harness.create_database("target")
        self.addCleanup(self.harness.drop_database, name)
        target_db = Database(DatabaseConfig(conninfo=self.harness.conninfo(name), role="test",
                                            pool_min=0, pool_max=4))
        self.addCleanup(target_db.close)
        target = provision_actor(target_db, name="New")
        root = tempfile.mkdtemp(prefix="prks-pg-target-")
        self.addCleanup(shutil.rmtree, root, True)
        target_objects = LocalFilesystemStorage(root)
        devices_before = target_db.command(lambda tx: tx.execute("SELECT count(*) AS n FROM prks.devices")
                                           .fetchall())[0]["n"]
        staged = self.stage(result.path, conninfo=self.harness.conninfo(name), library_id=target.library_id,
                            owner=target.user_id, db=target_db, storage=target_objects)
        works = self.staged(staged, "works", target_db)
        self.assertEqual([(w["id"], w["library_id"]) for w in works], [(work_id, target.library_id)])
        (op,) = self.staged(staged, "sync_operations", target_db)
        self.assertEqual((op["user_id"], op["library_id"]), (target.user_id, target.library_id))
        self.assertEqual(op["device_id"], self.actor.device_id)
        self.assertEqual(staged.new_devices, 1)
        self.assertEqual(staged.objects_written, 1)
        self.assertIsNotNone(target_objects.stat(StorageKey("asset-objects", obj.key.name)))
        # Staging never writes prks.devices: R4 inserts new devices.
        self.assertEqual(target_db.command(lambda tx: tx.execute("SELECT count(*) AS n FROM prks.devices")
                                           .fetchall())[0]["n"], devices_before)


def source_text(path: str, name: str) -> str:
    with zipfile.ZipFile(path) as source:
        return source.read(name).decode()
