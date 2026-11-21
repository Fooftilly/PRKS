"""Saved remote images as immutable objects on PostgreSQL (#1351).

``backend/domain/objects/remote_images.py``: a copy is fetched only for a URL
the Library itself names (an Asset's thumbnail, or Markdown it renders),
stored under a fresh ``remote-images`` key and named by one row per Library
and URL; a concurrent save keeps one copy and claims the other key; a reader
and another Library never write; the reference check, the sweep and the
backup's object list count the rows.
"""

from __future__ import annotations

import hashlib
import time
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Optional

from backend.db.errors import ConstraintViolationError
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.maintenance.format import object_references
from backend.domain.objects import lifecycle, remote_images
from backend.domain.objects.lifecycle import Settled, StoredObject
from backend.person_image import PortraitImage, decode_and_transcode
from backend.storage.objects import NAMESPACE_REMOTE_IMAGES, LocalFilesystemStorage, StorageKey
from tests import acting_context as facade_context
from tests.pg.test_works_mutations import MutationTestCase

THUMB = "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
FIGURE = "https://example.org/figures/graph.png"
SPACED = "https://example.org/figures/my graph.png"


def image(color: tuple[int, int, int] = (200, 30, 30)) -> PortraitImage:
    """A real copy: a PNG transcoded by ``person_image``, as a fetch would produce."""
    from PIL import Image

    raw = BytesIO()
    Image.new("RGB", (16, 16), color).save(raw, "PNG")
    encoded = decode_and_transcode(raw.getvalue(), "image/png", remote_images.MAX_EDGE)
    assert encoded is not None
    return PortraitImage(*encoded)


class RemoteImageTestCase(MutationTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.objects = LocalFilesystemStorage.for_config(self.storage)
        self.fetched: list[str] = []

    def video(self, url: str = THUMB) -> str:
        return self.library.add_work("A talk", source_kind="video",
                                     source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ", thumb_url=url)

    def noted(self, text: str) -> str:
        work_id = self.library.add_work("A paper")
        self.sql("INSERT INTO prks.work_notes (library_id, work_id, kind, body) VALUES (%s, %s, 'research', %s)",
                 (self.actor.library_id, work_id, text))
        return work_id

    def rows(self) -> dict[str, str]:
        return {row["source_url"]: row["locator"] for row in self.sql(
            "SELECT source_url, locator FROM prks.remote_images")}

    def names(self) -> set[str]:
        return {key.name for key in self.objects.iter_keys(NAMESPACE_REMOTE_IMAGES)}

    def claims(self) -> set[str]:
        return {row["name"] for row in self.sql(
            "SELECT name FROM prks.object_cleanup_claims WHERE namespace = 'remote-images'")}

    def preparing(self, picture: Optional[PortraitImage] = None, before: Any = None) -> Callable[[str], Any]:
        def prepare(url: str) -> Optional[PortraitImage]:
            self.fetched.append(url)
            if before is not None:
                before()
            return image() if picture is None else picture

        return prepare

    def save(self, url: str, picture: Optional[PortraitImage] = None, before: Any = None,
             ctx: Any = None) -> Any:
        return remote_images.save(self.db, ctx or self.actor.context, self.objects, url,
                                  self.preparing(picture, before))

    def saved(self, url: str, ctx: Any = None) -> Any:
        return remote_images.saved(self.db, ctx or self.actor.context, self.objects, url)

    def stored(self) -> StoredObject:
        return remote_images.store(self.objects, image())


class SaveTests(RemoteImageTestCase):
    def test_a_thumbnail_is_saved_under_a_fresh_key_and_read_back(self) -> None:
        self.video()
        picture = image()
        done = self.save(THUMB, picture)
        self.assertEqual(done, {"body": picture.body, "subtype": "webp",
                                "sha256": hashlib.sha256(picture.body).hexdigest()})
        self.assertEqual(self.fetched, [THUMB])
        locator = self.rows()[THUMB]
        self.assertTrue(locator.endswith(".webp"))
        self.assertEqual(self.names(), {locator})
        (row,) = self.sql("SELECT sha256, byte_size FROM prks.remote_images")
        self.assertEqual((row["sha256"], row["byte_size"]),
                         (hashlib.sha256(picture.body).hexdigest(), len(picture.body)))
        self.assertEqual(self.saved(THUMB), done)
        self.assertEqual(self.claims(), set())

    def test_a_figure_in_any_rendered_markdown_is_named(self) -> None:
        self.noted(f"See ![graph]({FIGURE}).")
        self.assertIsNotNone(self.save(FIGURE))
        lib = self.actor.library_id
        concept = "https://example.org/concepts.png"
        self.assertIsNone(self.save(concept))
        self.library.create_concept("Graph", f"![x]({concept})")
        self.assertIsNotNone(self.save(concept))
        for table, column, extra in (
                ("positions", "description", {"id": "P-" + "A" * 32, "name": "Graphs help"}),
                ("arguments", "main_text", {"id": "A-" + "A" * 32, "name": "Why", "kind": "argument"})):
            url = f"https://example.org/{table}.png"
            self.assertIsNone(self.save(url), table)
            cols = ["library_id", column, *extra]
            self.sql(f"INSERT INTO prks.{table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                     (lib, f"![x]({url})", *extra.values()))
            self.assertIsNotNone(self.save(url), table)
        self.assertEqual(len(self.rows()), 4)

    def test_a_percent_encoded_figure_matches_the_raw_text(self) -> None:
        self.noted(f"![x]({SPACED})")
        self.assertIsNotNone(self.save(SPACED.replace(" ", "%20")))

    def test_a_figure_mixing_raw_non_ascii_and_escapes_matches(self) -> None:
        # The renderer encodes the raw "é" and keeps the "%20" it found.
        self.noted("![x](https://example.org/caf\u00e9%20plot.png)")
        self.assertIsNotNone(self.save("https://example.org/caf%C3%A9%20plot.png"))

    def test_an_uppercase_scheme_is_saved(self) -> None:
        url = "HTTPS://example.org/figures/Graph.png"
        self.noted(f"![x]({url})")
        self.assertIsNotNone(self.save(url))
        self.assertEqual(set(self.rows()), {url})

    def test_a_lost_or_damaged_copy_is_replaced_and_its_key_claimed(self) -> None:
        self.video()
        for damage in ("missing", "corrupt"):
            with self.subTest(damage=damage):
                self.save(THUMB)
                old = self.rows()[THUMB]
                with self.objects.local_path(StorageKey(NAMESPACE_REMOTE_IMAGES, old)) as local:
                    path = Path(local)
                if damage == "missing":
                    path.unlink()
                else:
                    path.write_bytes(b"\0" * path.stat().st_size)
                self.assertIsNone(self.saved(THUMB))
                fresh = self.save(THUMB, image((10, 200, 10)))
                self.assertIsNotNone(fresh)
                new = self.rows()[THUMB]
                self.assertNotEqual(new, old)
                self.assertIn(old, self.claims())
                self.assertEqual(self.saved(THUMB), fresh)
                refs = self.sql(object_references("prks"), {"library_id": self.actor.library_id})
                self.assertNotIn(old, {ref["name"] for ref in refs})

    def test_an_unreadable_copy_is_kept_and_the_fetch_served_unsaved(self) -> None:
        self.video()
        self.save(THUMB)
        old = self.rows()[THUMB]
        real_open = self.objects.open_read

        def refuse(key: StorageKey) -> Any:
            if key.name == old:
                raise PermissionError("no read permission")
            return real_open(key)

        self.objects.open_read = refuse  # type: ignore[method-assign]
        fresh = self.save(THUMB, image((10, 10, 200)))
        self.assertIsNotNone(fresh)
        self.assertNotIn("sha256", fresh)
        self.assertEqual(self.rows(), {THUMB: old})
        self.assertEqual(self.claims(), set())
        self.assertEqual(self.names(), {old})

    def test_a_url_the_library_does_not_name_is_never_fetched(self) -> None:
        self.video()
        self.noted("no figures here")
        for url in (FIGURE, THUMB + "?x=1"):
            self.assertIsNone(self.save(url))
        self.assertEqual((self.fetched, self.rows(), self.names()), ([], {}, set()))
        self.assertIsNone(self.saved(FIGURE))

    def test_a_failed_fetch_saves_nothing(self) -> None:
        self.video()
        done = remote_images.save(self.db, self.actor.context, self.objects, THUMB, lambda url: None)
        self.assertIsNone(done)
        self.assertEqual((self.rows(), self.names()), ({}, set()))

    def test_a_second_save_keeps_the_first_copy_and_claims_its_own_key(self) -> None:
        self.video()
        first = image((10, 10, 10))
        self.save(THUMB, first)
        kept = self.rows()[THUMB]
        loser = self.stored()
        done = self.run_command(lambda scope: remote_images.commit(scope, self.objects, THUMB, loser))
        assert isinstance(done, remote_images.RemoteImage)
        self.assertEqual(done.locator, kept)
        self.assertEqual(self.claims(), {loser.key.name})
        self.assertEqual(self.saved(THUMB)["body"], first.body)
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects), {Settled.REMOVED: 1})
        self.assertEqual(self.names(), {kept})

    def test_a_url_unnamed_by_commit_time_claims_the_key(self) -> None:
        work_id = self.video()

        def forget() -> None:
            self.sql("UPDATE prks.assets SET thumb_url = NULL WHERE work_id = %s", (work_id,))

        self.assertIsNone(self.save(THUMB, before=forget))
        self.assertEqual(self.rows(), {})
        self.assertEqual(len(self.claims()), 1)

    def test_a_damaged_copy_reads_as_none(self) -> None:
        self.video()
        self.save(THUMB)
        self.sql("UPDATE prks.remote_images SET sha256 = %s", ("0" * 64,))
        self.assertIsNone(self.saved(THUMB))

    def test_the_schema_refuses_a_bad_row(self) -> None:
        lib = self.actor.library_id
        for url, locator, digest, size in (
                ("ftp://example.org/x.png", "a.webp", "0" * 64, 1),
                (FIGURE, "../a.webp", "0" * 64, 1),
                (FIGURE, "a.webp", "nothex", 1),
                (FIGURE, "a.webp", "0" * 64, 0)):
            with self.assertRaises(ConstraintViolationError):
                self.sql("INSERT INTO prks.remote_images (library_id, source_url, locator, sha256, byte_size)"
                         " VALUES (%s, %s, %s, %s, %s)", (lib, url, locator, digest, size))

    def test_only_public_http_urls_are_saved(self) -> None:
        for value in ("", "  ", "javascript:alert(1)", "data:image/png;base64,AAAA", "http://127.0.0.1/x.png",
                      "https://user:pw@example.org/x.png", "https://example.org/" + "a" * 2100, None, 7):
            self.assertIsNone(remote_images.normalize_url(value), value)
        self.assertEqual(remote_images.normalize_url(" " + FIGURE + " "), FIGURE)


class AccessTests(RemoteImageTestCase):
    def test_a_reader_reads_but_is_refused_before_any_fetch(self) -> None:
        self.video()
        self.save(THUMB)
        self.fetched.clear()
        reader = self.reader_context()
        with facade_context.acting_as(reader):
            self.assertIsNotNone(self.saved(THUMB, reader))
            self.assertTrue(remote_images.is_named(self.db, reader, THUMB))
            self.sql("INSERT INTO prks.work_notes (library_id, work_id, kind, body)"
                     " SELECT library_id, id, 'research', %s FROM prks.works WHERE library_id = %s LIMIT 1",
                     (f"![x]({FIGURE})", self.actor.library_id))
            with self.assertRaises(AccessRefused) as caught:
                self.save(FIGURE, ctx=reader)
            self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual((self.fetched, set(self.rows())), ([], {THUMB}))

    def test_another_library_neither_reads_nor_adopts_this_librarys_copy(self) -> None:
        self.video()
        self.save(THUMB)
        mine = self.rows()[THUMB]
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            self.assertIsNone(self.saved(THUMB, other.context))
            self.assertIsNone(self.save(THUMB, ctx=other.context), "the other Library names no thumbnail")
            self.video()
            obj = StoredObject(StorageKey(NAMESPACE_REMOTE_IMAGES, mine), 1, "0" * 64)
            with self.assertRaises(lifecycle.ObjectMissing):
                self.run_command(lambda scope: remote_images.commit(scope, self.objects, THUMB, obj),
                                 other.context)
            self.assertIsNotNone(self.save(THUMB, ctx=other.context))
        self.assertEqual(len(self.sql("SELECT 1 FROM prks.remote_images")), 2)


class LifecycleTests(RemoteImageTestCase):
    def test_a_saved_copy_is_referenced_and_an_orphan_is_swept(self) -> None:
        self.video()
        self.save(THUMB)
        named = self.rows()[THUMB]
        orphan = self.stored()
        swept = lifecycle.sweep(self.db, self.objects, grace=timedelta(0), now=time.time() + 60,
                                namespaces=(NAMESPACE_REMOTE_IMAGES,))
        self.assertEqual(swept, 1)
        self.assertEqual(self.claims(), {orphan.key.name})
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects), {Settled.REMOVED: 1})
        self.assertEqual(self.names(), {named})
        self.sql("INSERT INTO prks.object_cleanup_claims (namespace, name) VALUES ('remote-images', %s)",
                 (named,))
        self.assertIs(lifecycle.settle(self.db, self.objects, StorageKey(NAMESPACE_REMOTE_IMAGES, named)),
                      Settled.REFERENCED)
        self.assertEqual(self.names(), {named})

    def test_the_row_outlives_the_work_that_named_it(self) -> None:
        work_id = self.video()
        self.save(THUMB)
        self.sql("UPDATE prks.assets SET thumb_url = NULL WHERE work_id = %s", (work_id,))
        self.assertIsNotNone(self.saved(THUMB))

    def test_a_backup_carries_the_saved_copy(self) -> None:
        self.video()
        self.save(THUMB)
        (row,) = self.sql("SELECT locator, sha256, byte_size FROM prks.remote_images")
        refs = self.sql(object_references("prks"), {"library_id": self.actor.library_id})
        self.assertIn({"namespace": "remote-images", "name": row["locator"], "sha256": row["sha256"],
                       "size": row["byte_size"]}, refs)
