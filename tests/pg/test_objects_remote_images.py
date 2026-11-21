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
from unittest import mock

from backend.db.errors import ConstraintViolationError
from backend.domain.identity.authorization import AccessRefused, Capability, Refusal
from backend.domain.identity.library_scope import library_command
from backend.domain.maintenance.format import object_references
from backend.domain.objects import lifecycle, remote_images
from backend.domain.objects.lifecycle import Settled, StoredObject
from backend.jobs import queue
from backend.jobs.runner import Outcome, run_job
from backend.person_image import PortraitImage, decode_and_transcode
from backend.storage.objects import NAMESPACE_REMOTE_IMAGES, LocalFilesystemStorage, StorageKey
from tests import acting_context as facade_context
from tests.pg.jobs_fixtures import LEASE
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

    def test_an_html_escaped_figure_matches(self) -> None:
        url = "https://example.org/chart.png?w=400&format=png"
        self.noted('<img src="https://example.org/chart.png?w=400&amp;format=png">')
        self.assertIsNotNone(self.save(url))

    def test_an_ampersand_spelt_as_a_character_reference_matches(self) -> None:
        for number, spelling in enumerate(("&#38;", "&#038;", "&#x26;", "&#X26;", "&AMP;",
                                            "&#38", "&#0038")):
            with self.subTest(spelling=spelling):
                self.noted(f'<img src="https://example.org/chart{number}.png?w=400{spelling}format=png">')
                self.assertIsNotNone(self.save(f"https://example.org/chart{number}.png?w=400&format=png"))

    def test_quote_and_apostrophe_references_are_decoded_too(self) -> None:
        self.noted('<img src="https://example.org/chart.png?w=400&#38;label=O&#x27;Neil&amp;t=&quot;x&quot;">')
        self.assertIsNotNone(self.save("https://example.org/chart.png?w=400&label=O'Neil&t=\"x\""))

    def test_a_reference_without_its_semicolon_ends_where_html_ends_it(self) -> None:
        self.noted('<img src="https://example.org/a.png?x=1&amp-y&amp=z&#x26width&#x26format">')
        self.assertIsNotNone(self.save("https://example.org/a.png?x=1&-y&amp=z&width&#x26format"))

    def test_reference_names_are_case_sensitive(self) -> None:
        self.noted('<img src="https://example.org/chart.png?w=400&#38;label=&APOS;x&Amp;y">')
        self.assertIsNone(self.save("https://example.org/chart.png?w=400&label='x&y"))
        self.assertIsNotNone(self.save("https://example.org/chart.png?w=400&label=&APOS;x&Amp;y"))

    def test_a_reference_is_decoded_only_once(self) -> None:
        self.noted('<img src="https://example.org/chart.png?w=400&amp;amp;format=png">')
        self.assertIsNone(self.save("https://example.org/chart.png?w=400&format=png"))
        self.assertIsNotNone(self.save("https://example.org/chart.png?w=400&amp;format=png"))

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

    def prunes(self) -> list[dict[str, Any]]:
        return self.sql("SELECT state, run_after > now() + interval '6 days' AS later FROM prks.jobs"
                        " WHERE kind = %s ORDER BY created_at", (remote_images.PRUNE_KIND,))

    def run_prune(self) -> Outcome:
        """Make the queued prune due, and the saved copies old enough, then run it as the worker would."""
        self.sql("UPDATE prks.remote_images SET saved_at = now() - interval '2 days'")
        self.sql("UPDATE prks.jobs SET run_after = now() WHERE kind = %s AND state = 'queued'",
                 (remote_images.PRUNE_KIND,))
        job = queue.claim(self.db, "test-worker", lease=LEASE)
        assert job is not None and job.kind == remote_images.PRUNE_KIND
        return run_job(self.db, job, lease=LEASE)

    def test_the_worker_runs_the_prune(self) -> None:
        from backend.jobs import worker  # noqa: F401  the worker's imports register its kinds
        from backend.jobs.handlers import REGISTRY

        self.assertIs(REGISTRY.get(remote_images.PRUNE_KIND), remote_images.prune)

    def test_a_save_schedules_one_prune_a_week_later(self) -> None:
        self.video()
        self.noted(f"![x]({FIGURE})")
        self.save(THUMB)
        self.save(FIGURE)
        self.assertEqual(self.prunes(), [{"state": "queued", "later": True}])

    def test_the_row_outlives_the_work_until_the_prune(self) -> None:
        work_id = self.video()
        self.save(THUMB)
        self.sql("UPDATE prks.assets SET thumb_url = NULL WHERE work_id = %s", (work_id,))
        self.assertIsNotNone(self.saved(THUMB))
        locator = self.rows()[THUMB]
        self.assertEqual(self.run_prune(), Outcome.SUCCEEDED)
        self.assertEqual(self.rows(), {})
        self.assertEqual(self.claims(), {locator})
        # Nothing left to check: no next prune.
        self.assertEqual([row["state"] for row in self.prunes()], ["succeeded"])

    def test_the_prune_keeps_named_and_recent_copies_and_comes_back(self) -> None:
        self.video()
        work_id = self.noted(f"![a]({FIGURE}) ![b]({SPACED})")
        self.save(THUMB)
        self.save(FIGURE)
        self.save(SPACED)
        self.sql("UPDATE prks.work_notes SET body = %s WHERE work_id = %s", (f"![a]({FIGURE})", work_id))
        recent = "https://example.org/figures/new.png"
        self.sql("UPDATE prks.work_notes SET body = body || %s WHERE work_id = %s", (f" ![n]({recent})", work_id))
        with mock.patch.object(remote_images, "PRUNE_BATCH", 1):
            self.assertEqual(self.run_prune(), Outcome.SUCCEEDED)
        self.assertEqual(set(self.rows()), {THUMB, FIGURE})
        self.assertEqual(len(self.claims()), 1)
        self.assertEqual(self.prunes()[-1], {"state": "queued", "later": True})
        # A copy saved within the grace period is never examined.
        self.save(recent)
        self.sql("UPDATE prks.work_notes SET body = '' WHERE work_id = %s", (work_id,))
        _last, dropped = library_command(self.db, self.actor.context, Capability.EDIT_LIBRARY,
                                         lambda scope: remote_images.prune_page(scope, ""))
        self.assertEqual(dropped, 1)  # the old figure
        self.assertEqual(set(self.rows()), {THUMB, recent})

    def test_the_prune_keeps_a_figure_named_through_a_numeric_reference(self) -> None:
        url = "https://example.org/chart.png?w=400&format=png"
        work_id = self.noted(f"![x]({url})")
        self.save(url)
        self.sql("UPDATE prks.work_notes SET body = %s WHERE work_id = %s",
                 ('<img src="https://example.org/chart.png?w=400&#38;format=png">', work_id))
        self.assertEqual(self.run_prune(), Outcome.SUCCEEDED)
        self.assertEqual(set(self.rows()), {url})
        self.assertEqual(self.claims(), set())

    def test_the_prune_keeps_a_figure_named_through_a_reference_without_its_semicolon(self) -> None:
        url = "https://example.org/chart.png?w=400&format=png"
        work_id = self.noted(f"![x]({url})")
        self.save(url)
        self.sql("UPDATE prks.work_notes SET body = %s WHERE work_id = %s",
                 ('<img src="https://example.org/chart.png?w=400&#38format=png">', work_id))
        self.assertEqual(self.run_prune(), Outcome.SUCCEEDED)
        self.assertEqual(set(self.rows()), {url})
        self.assertEqual(self.claims(), set())

    def test_a_backup_carries_the_saved_copy(self) -> None:
        self.video()
        self.save(THUMB)
        (row,) = self.sql("SELECT locator, sha256, byte_size FROM prks.remote_images")
        refs = self.sql(object_references("prks"), {"library_id": self.actor.library_id})
        self.assertIn({"namespace": "remote-images", "name": row["locator"], "sha256": row["sha256"],
                       "size": row["byte_size"]}, refs)
