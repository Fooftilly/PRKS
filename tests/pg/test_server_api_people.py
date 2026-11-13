"""Person portraits and profile edits over HTTP on the PostgreSQL Library (PG-C2 #641).

A Person's portrait is served from its ``image_url`` and stays served when
the remote later fails; an ``image_url`` that is not a public http(s) URL is
refused on create and on edit and never fetched; bytes that are not an image
are never served. A profile edit that names a missing group changes nothing,
and one that names groups applies metadata and memberships together; a
Person Group edit naming a missing parent creates nothing.

The route serves the portrait object the Person row names
(``portrait_locator``, ``tests/pg/test_objects_portraits.py``): a fetch made
through it becomes that object, never a file in ``people_dir``; a catalogued
portrait is served without fetching; a reader's miss is served uncached; and
the object a backup took is the one served after a restore.
These replace the matching ``tests/test_server_api.py`` cases.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from io import BytesIO
from typing import Any
from unittest import mock
from urllib.parse import quote

from backend.derived_caches import prks_person_image_cache_safe_id
from backend.domain.maintenance import backup, restore
from backend.person_image import PortraitImage, decode_and_transcode
from backend.storage.objects import NAMESPACE_PORTRAITS, LocalFilesystemStorage, StorageKey
from tests.pg.server_api_fixtures import ServerApiTestCase


def portrait_png(color: tuple[int, int, int] = (120, 80, 200)) -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return buf.getvalue()


class PortraitTests(ServerApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        encoded = decode_and_transcode(portrait_png())
        if encoded is None:
            self.skipTest("Pillow/WebP encode not available")
        self.portrait = PortraitImage(body=encoded[0], subtype=encoded[1])

    def person(self, first: str, last: str, image_url: str) -> str:
        return self.ok(self.send("POST", "/api/persons", {"first_name": first, "last_name": last,
                                                          "image_url": image_url}))["id"]

    def image(self, person_id: str):
        return self.get(f"/api/persons/{quote(person_id)}/profile-image")

    def catalogued(self, person_id: str) -> dict[str, Any]:
        return self.sql("SELECT portrait_locator, portrait_sha256, portrait_byte_size FROM prks.persons "
                        "WHERE id = %s", (person_id,))[0]

    def cache_files(self, person_id: str) -> list[str]:
        """Files of the retired per-Person cache (``<safe id>_<url hash>_v<rev>.webp``) in ``people_dir``.

        Portrait objects share the directory under their own random names.
        """
        people_dir = self.storage.people_dir
        prefix = prks_person_image_cache_safe_id(person_id) + "_"
        return sorted(n for n in (os.listdir(people_dir) if os.path.isdir(people_dir) else [])
                      if n.startswith(prefix))

    def test_a_fetched_portrait_becomes_the_catalogued_object(self) -> None:
        with mock.patch("backend.api.persons.fetch_and_prepare", return_value=self.portrait):
            person_id = self.person("Object", "Portrait", "https://example.invalid/o.jpg")
            served = self.image(person_id)
        self.assertEqual((served.status, served.raw), (200, self.portrait.body))
        row = self.catalogued(person_id)
        self.assertIsNotNone(row["portrait_locator"], "the fetch names a portrait object")
        self.assertEqual((row["portrait_sha256"], row["portrait_byte_size"]),
                         (hashlib.sha256(self.portrait.body).hexdigest(), len(self.portrait.body)))
        objects = LocalFilesystemStorage.for_config(self.storage)
        with objects.open_read(StorageKey(NAMESPACE_PORTRAITS, row["portrait_locator"])) as handle:
            self.assertEqual(handle.read(), self.portrait.body)
        self.assertEqual(self.cache_files(person_id), [], "no per-Person cache file is written")

    def test_a_catalogued_portrait_is_served_without_fetching(self) -> None:
        person_id = self.person("Stored", "Portrait", "https://example.invalid/s.jpg")
        with self.acting():
            stored = self.facade._cache_person_portrait(person_id, lambda _url: self.portrait)
        assert stored is not None
        with mock.patch("backend.api.persons.fetch_and_prepare", side_effect=AssertionError("must use the object")):
            served = self.image(person_id)
        self.assertEqual((served.status, served.raw), (200, self.portrait.body))

    def test_a_readers_miss_is_served_and_never_cached(self) -> None:
        person_id = self.person("Reader", "Portrait", "https://example.invalid/r.jpg")
        self.user("Reader", library=self.lib, role="reader")
        reader = self.session_cookie("Reader")
        with mock.patch("backend.api.persons.fetch_and_prepare", return_value=self.portrait):
            served = self.call("GET", f"/api/persons/{quote(person_id)}/profile-image", cookies=reader)
        self.assertEqual((served.status, served.raw), (200, self.portrait.body))
        self.assertIsNone(self.catalogued(person_id)["portrait_locator"], "a reader never writes")
        self.assertEqual(self.cache_files(person_id), [])

    def test_the_backed_up_portrait_is_served_after_a_restore(self) -> None:
        encoded = decode_and_transcode(portrait_png((10, 200, 40)))
        assert encoded is not None
        later = PortraitImage(body=encoded[0], subtype=encoded[1])
        self.assertNotEqual(later.body, self.portrait.body)
        with mock.patch("backend.api.persons.fetch_and_prepare", return_value=self.portrait):
            person_id = self.person("Restored", "Portrait", "https://example.invalid/before.jpg")
            self.assertEqual(self.image(person_id).raw, self.portrait.body)
        out = tempfile.mkdtemp(prefix="prks-pg-portrait-backup-")
        self.addCleanup(shutil.rmtree, out, True)
        objects = LocalFilesystemStorage.for_config(self.storage)
        archive = backup.write_backup(self.db.config.conninfo, objects, self.lib, out,
                                      prks_version="0.8.0-test").path
        # After the backup the Person gets another portrait.
        self.ok(self.send("PATCH", f"/api/persons/{quote(person_id)}", {"image_url": "https://example.invalid/after.jpg"}))
        with mock.patch("backend.api.persons.fetch_and_prepare", return_value=later):
            self.assertEqual(self.image(person_id).raw, later.body)
        restore.restore_library(self.db.config.conninfo, objects, archive, self.lib,
                                owner_user_id=self.owner, staging_dir=out)
        with mock.patch("backend.api.persons.fetch_and_prepare", side_effect=AssertionError("must use the object")):
            served = self.image(person_id)
        self.assertEqual((served.status, served.raw), (200, self.portrait.body))
        self.assertEqual(self.catalogued(person_id)["portrait_sha256"],
                         hashlib.sha256(self.portrait.body).hexdigest())

    def test_a_portrait_is_served_and_stays_served_when_the_remote_fails(self) -> None:
        with mock.patch("backend.api.persons.fetch_and_prepare", return_value=self.portrait):
            person_id = self.person("Cache", "Portrait", "https://example.invalid/p.jpg")
            first = self.image(person_id)
        self.assertEqual(first.status, 200)
        self.assertIn("image/webp", first.header("Content-Type")[0])
        self.assertEqual((first.raw[:4], first.raw[8:12]), (b"RIFF", b"WEBP"))
        with mock.patch("backend.api.persons.fetch_and_prepare", return_value=None):
            again = self.image(person_id)
        self.assertEqual((again.status, again.raw), (200, first.raw))

    def test_a_new_image_url_is_fetched_again(self) -> None:
        fetch = mock.Mock(return_value=self.portrait)
        with mock.patch("backend.api.persons.fetch_and_prepare", fetch):
            person_id = self.person("Purge", "Portrait", "https://example.invalid/a.jpg")
            self.assertEqual(self.image(person_id).status, 200)
            self.ok(self.send("PATCH", f"/api/persons/{quote(person_id)}", {"image_url": "https://example.invalid/b.jpg"}))
            self.assertEqual(self.image(person_id).status, 200)
        self.assertEqual([call.args[0] for call in fetch.call_args_list],
                         ["https://example.invalid/a.jpg", "https://example.invalid/b.jpg"])

    def test_a_private_image_url_is_refused_on_create_and_creates_no_person(self) -> None:
        body = self.refused(self.send("POST", "/api/persons", {"first_name": "Bad", "last_name": "Url",
                                                               "image_url": "http://127.0.0.1/x.jpg"}), 400)
        self.assertEqual(body.get("error"), "Invalid image_url")
        self.assertFalse(any((p.get("first_name"), p.get("last_name")) == ("Bad", "Url")
                             for p in self.ok(self.get("/api/persons"))))

    def test_a_private_image_url_is_refused_on_edit_and_keeps_the_portrait(self) -> None:
        url = "https://example.invalid/ok.jpg"
        with mock.patch("backend.api.persons.fetch_and_prepare", return_value=self.portrait):
            person_id = self.person("Keep", "Cache", url)
            served = self.image(person_id).raw
        self.refused(self.send("PATCH", f"/api/persons/{quote(person_id)}", {"image_url": "http://192.168.1.10/x.jpg"}),
                     400)
        self.assertEqual(self.ok(self.get(f"/api/persons/{quote(person_id)}")).get("image_url"), url)
        with mock.patch("backend.api.persons.fetch_and_prepare", side_effect=AssertionError("must use the portrait")):
            self.assertEqual(self.image(person_id).raw, served)

    def test_a_stored_loopback_image_url_is_never_connected_to(self) -> None:
        # The facade refuses to store such a URL; a row from before that guard
        # is seeded directly.
        person_id = self.ok(self.send("POST", "/api/persons", {"first_name": "Legacy", "last_name": "Loopback"}))["id"]
        self.sql("UPDATE prks.persons SET image_url = %s WHERE id = %s RETURNING id",
                 ("http://127.0.0.1/image", person_id))
        with mock.patch("backend.person_image._getaddrinfo") as dns, \
                mock.patch("backend.person_image._create_socket") as sock:
            self.assertEqual(self.image(person_id).status, 404)
        dns.assert_not_called()
        sock.assert_not_called()

    def test_remote_bytes_that_are_not_an_image_are_never_served(self) -> None:
        person_id = self.person("Html", "Remote", "https://example.invalid/not-image")
        with mock.patch("backend.person_image._fetch_remote_response", return_value=(b"<html>nope</html>", "text/html")):
            self.assertEqual(self.image(person_id).status, 404)
        with mock.patch("backend.api.persons.fetch_and_prepare", return_value=None):
            self.assertEqual(self.image(person_id).status, 404)


class ProfileEditTests(ServerApiTestCase):
    def setup_person(self, first: str, last: str, *groups: str) -> tuple[str, list[str]]:
        group_ids = [self.group(name) for name in groups]
        person_id = self.ok(self.send("POST", "/api/persons", {"first_name": first, "last_name": last}))["id"]
        if group_ids:
            self.ok(self.send("PATCH", f"/api/persons/{quote(person_id)}", {"group_ids": group_ids[:1]}))
        return person_id, group_ids

    def group(self, name: str) -> str:
        return self.ok(self.send("POST", "/api/person-groups", {"name": name}))["id"]

    def person(self, person_id: str) -> dict[str, Any]:
        return self.ok(self.get(f"/api/persons/{quote(person_id)}"))

    def test_a_missing_group_refuses_the_whole_edit(self) -> None:
        person_id, (group_id,) = self.setup_person("Old", "Atomic", "Atomic Test Group")
        self.refused(self.send("PATCH", f"/api/persons/{quote(person_id)}",
                               {"first_name": "New", "group_ids": ["missing-group"]}), 400)
        person = self.person(person_id)
        self.assertEqual(person["first_name"], "Old", "metadata was written despite a 400")
        self.assertEqual([g["id"] for g in person["groups"]], [group_id])

    def test_metadata_and_groups_are_applied_together(self) -> None:
        person_id, (_, group_b) = self.setup_person("Before", "Atomic", "Atomic Group A", "Atomic Group B")
        self.ok(self.send("PATCH", f"/api/persons/{quote(person_id)}", {"first_name": "After", "group_ids": [group_b]}))
        person = self.person(person_id)
        self.assertEqual((person["first_name"], [g["id"] for g in person["groups"]]), ("After", [group_b]))

    def test_a_metadata_only_edit_keeps_the_memberships(self) -> None:
        person_id, (group_id,) = self.setup_person("Meta", "Only", "Metadata Only Group")
        self.ok(self.send("PATCH", f"/api/persons/{quote(person_id)}", {"about": "A new biography."}))
        person = self.person(person_id)
        self.assertEqual((person["about"], [g["id"] for g in person["groups"]]), ("A new biography.", [group_id]))

    def test_a_refused_group_edit_leaves_its_typed_parent_uncreated(self) -> None:
        # A typed parent that does not exist yet is created in the same
        # transaction as the group. Each edit below is refused for a name that
        # is already taken, after the parent was made: the refusal must roll the
        # parent back with it.
        group_a = self.group("Atomic A")
        self.group("Atomic B")
        before = self.ok(self.get("/api/person-groups"))
        for method, path, name in (("POST", "/api/person-groups", "Atomic A"),
                                   ("PATCH", f"/api/person-groups/{group_a}", "Atomic B")):
            with self.subTest(method=method):
                self.refused(self.send(method, path, {"name": name, "parent_name": "Atomic Orphan"}), 400)
                self.assertEqual(self.ok(self.get("/api/person-groups")), before)
