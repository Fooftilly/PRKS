"""Person portraits as immutable objects on PostgreSQL (L-A-1 #635).

``backend/domain/objects/portraits.py`` and the facade's private portrait
methods: a fetched and transcoded portrait under a fresh ``portraits`` key,
named on the Person row in one command under the Person row lock; the key
it supersedes claimed in that command; a changed ``image_url``, a cleared
portrait or a deleted Person claiming the key; the three-valued reference
check counting every Library's Persons; and failures that leave the Person
as it was and the new key to the orphan sweep. Interleavings are fixed with
:meth:`PortraitTestCase.hold_then` (no timing) or by acting from inside the
fetch, which runs between the checks and the command.
"""

from __future__ import annotations

import hashlib
import threading
import time
from datetime import timedelta
from io import BytesIO
from typing import Any, Callable, Optional
from unittest import mock

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.db.errors import ConstraintViolationError
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.objects import lifecycle, portraits
from backend.domain.objects.lifecycle import ObjectMissing, Settled, StoredObject
from backend.domain.people import persons
from backend.person_image import PortraitImage, decode_and_transcode
from backend.pg_facade import PostgresLibrary
from backend.pg_facade import context as facade_context
from backend.storage.objects import NAMESPACE_PORTRAITS, LocalFilesystemStorage, StorageKey
from tests.pg.test_works_mutations import MutationTestCase

URL = "https://example.org/ada.png"
OTHER_URL = "https://example.org/ada-2.png"


def image(color: tuple[int, int, int] = (200, 30, 30)) -> PortraitImage:
    """A real portrait: a PNG transcoded by ``person_image``, as a fetch would produce."""
    from PIL import Image

    raw = BytesIO()
    Image.new("RGB", (16, 16), color).save(raw, "PNG")
    encoded = decode_and_transcode(raw.getvalue(), "image/png")
    assert encoded is not None
    return PortraitImage(*encoded)


class PortraitTestCase(MutationTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.objects = LocalFilesystemStorage.for_config(self.storage)
        self.fetched: list[str] = []

    # -- state ------------------------------------------------------------------

    def person(self, url: str = URL) -> str:
        return self.library.add_person("Ada", "Lovelace", image_url=url)

    def row(self, person_id: str) -> dict[str, Any]:
        (row,) = self.sql("SELECT image_url, portrait_locator, portrait_sha256, portrait_byte_size,"
                          " portrait_source_url FROM prks.persons WHERE id = %s", (person_id,))
        return row

    def locator(self, person_id: str) -> Optional[str]:
        return self.row(person_id)["portrait_locator"]

    def names(self) -> set[str]:
        return {key.name for key in self.objects.iter_keys(NAMESPACE_PORTRAITS)}

    def claims(self) -> set[str]:
        return {row["name"] for row in self.sql(
            "SELECT name FROM prks.object_cleanup_claims WHERE namespace = 'portraits'")}

    def read(self, name: str) -> bytes:
        with self.objects.open_read(StorageKey(NAMESPACE_PORTRAITS, name)) as handle:
            return handle.read()

    def settle_all(self) -> Any:
        return lifecycle.retry_claims(self.db, self.objects)

    def sweep(self) -> int:
        return lifecycle.sweep(self.db, self.objects, grace=timedelta(0), now=time.time() + 60,
                               namespaces=(NAMESPACE_PORTRAITS,))

    # -- writes -----------------------------------------------------------------

    def preparing(self, picture: Optional[PortraitImage] = None, before: Any = None) -> Callable[[str], Any]:
        """A fetch of the URL that returns ``picture``; ``before()`` runs while it is fetched."""

        def prepare(url: str) -> Optional[PortraitImage]:
            self.fetched.append(url)
            if before is not None:
                before()
            return image() if picture is None else picture

        return prepare

    def cache(self, person_id: str, picture: Optional[PortraitImage] = None, before: Any = None) -> Any:
        return self.library._cache_person_portrait(person_id, self.preparing(picture, before))

    def stored(self, picture: Optional[PortraitImage] = None) -> StoredObject:
        return portraits.store(self.objects, picture or image())

    def commit(self, person_id: str, obj: StoredObject, url: str = URL, ctx: Any = None) -> Any:
        return self.run_command(lambda scope: portraits.commit(scope, self.objects, person_id, url, obj), ctx)

    def hold_then(self, holder: Callable[[LibraryScope], Any], waiter: Callable[[], Any]) -> tuple[Any, Any]:
        """Run ``holder`` in a command and keep it open until ``waiter`` (any call) blocks on it."""
        locked, release = threading.Event(), threading.Event()
        outcome: dict[str, Any] = {}
        ctx = self.actor.context

        def hold(scope: LibraryScope) -> Any:
            result = holder(scope)
            locked.set()
            if not release.wait(30):
                raise RuntimeError("never released")
            return result

        def run(name: str, fn: Callable[[], Any]) -> None:
            try:
                with facade_context.acting_as(ctx):
                    outcome[name] = fn()
            except BaseException as exc:  # noqa: BLE001 - returned to the test
                outcome[name] = exc

        first = threading.Thread(target=run, args=("holder", lambda: self.run_command(hold, ctx)))
        first.start()
        self.assertTrue(locked.wait(30))
        second = threading.Thread(target=run, args=("waiter", waiter))
        second.start()
        try:
            self.wait_for_lock_waiter()
        finally:
            release.set()
            first.join(30)
            second.join(30)
        if isinstance(outcome.get("holder"), BaseException):
            raise outcome["holder"]
        return outcome["holder"], outcome["waiter"]


class SuccessTests(PortraitTestCase):
    def test_a_portrait_is_stored_under_a_fresh_key_and_named_on_the_person(self) -> None:
        person_id = self.person()
        picture = image()
        done = self.cache(person_id, picture)
        row = self.row(person_id)
        digest = hashlib.sha256(picture.body).hexdigest()
        self.assertEqual(self.fetched, [URL])
        self.assertEqual(done, {"body": picture.body, "subtype": "webp", "content_sha256": digest,
                                "byte_size": len(picture.body)})
        self.assertEqual((row["image_url"], row["portrait_sha256"], row["portrait_byte_size"],
                          row["portrait_source_url"]), (URL, digest, len(picture.body), URL))
        self.assertTrue(row["portrait_locator"].endswith(".webp"))
        self.assertEqual(self.names(), {row["portrait_locator"]})
        self.assertEqual(self.read(row["portrait_locator"]), picture.body)
        self.assertIs(self.objects.verify(StorageKey(NAMESPACE_PORTRAITS, row["portrait_locator"]), digest), True)
        self.assertEqual(self.library._person_portrait(person_id), done)
        # The profile is unchanged: the locator is not a profile field.
        person = self.library.get_person(person_id)
        assert person is not None
        self.assertEqual(person["image_url"], URL)
        self.assertNotIn("portrait_locator", person)
        self.assertEqual(self.claims(), set())

    def test_a_jpeg_portrait_keeps_its_encoding(self) -> None:
        person_id = self.person()
        with mock.patch("backend.person_image._encode_webp", return_value=None):
            picture = image()
        self.assertEqual(picture.subtype, "jpeg")
        done = self.cache(person_id, picture)
        self.assertEqual(done["subtype"], "jpeg")
        locator = self.locator(person_id)
        assert locator is not None
        self.assertTrue(locator.endswith(".jpg"))

    def test_no_image_url_caches_nothing(self) -> None:
        person_id = self.person(url="")
        self.assertIsNone(self.cache(person_id))
        self.assertEqual((self.fetched, self.names()), ([], set()))
        self.assertIsNone(self.library._person_portrait(person_id))


class ReplacementTests(PortraitTestCase):
    def test_a_replacement_uses_a_fresh_key_and_claims_the_old_one(self) -> None:
        person_id = self.person()
        first = image((10, 10, 10))
        self.cache(person_id, first)
        old = self.locator(person_id)
        second = image((240, 240, 240))
        self.cache(person_id, second)
        new = self.locator(person_id)
        assert old is not None and new is not None
        self.assertNotEqual(new, old)
        self.assertEqual(self.read(old), first.body, "the old object is never modified")
        self.assertEqual(self.claims(), {old})
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 1})
        self.assertEqual(self.names(), {new})
        self.assertEqual(self.library._person_portrait(person_id)["body"], second.body)

    def test_four_concurrent_replacements_leave_one_key_named(self) -> None:
        person_id = self.person()
        colors = [(i * 40, 0, 0) for i in range(4)]
        results = self.concurrently([lambda c=c: self.cache(person_id, image(c)) for c in colors])
        named = self.locator(person_id)
        self.assertTrue(all(result is not None for result in results))
        self.assertEqual(self.claims(), self.names() - {named})
        self.assertEqual(len(self.names()), 4)
        self.settle_all()
        self.assertEqual(self.names(), {named})


class PersonChangeTests(PortraitTestCase):
    def test_changing_the_image_url_claims_the_portrait(self) -> None:
        person_id = self.person()
        self.cache(person_id)
        old = self.locator(person_id)
        self.library.update_person_profile(person_id, {"image_url": OTHER_URL})
        self.assertEqual(self.row(person_id)["image_url"], OTHER_URL)
        self.assertIsNone(self.locator(person_id))
        self.assertEqual(self.claims(), {old})
        self.assertIsNone(self.library._person_portrait(person_id))
        self.cache(person_id)
        self.assertEqual(self.fetched, [URL, OTHER_URL])
        self.assertEqual(self.row(person_id)["portrait_source_url"], OTHER_URL)

    def test_saving_the_same_image_url_keeps_the_portrait(self) -> None:
        person_id = self.person()
        self.cache(person_id)
        named = self.locator(person_id)
        self.library.update_person_profile(person_id, {"image_url": f"  {URL} ", "about": "x"})
        self.assertEqual((self.locator(person_id), self.claims()), (named, set()))

    def test_clearing_the_image_url_claims_the_portrait(self) -> None:
        person_id = self.person()
        self.cache(person_id)
        old = self.locator(person_id)
        self.library.update_person_profile(person_id, {"image_url": ""})
        self.assertEqual((self.locator(person_id), self.claims()), (None, {old}))

    def test_clearing_the_portrait_keeps_the_image_url(self) -> None:
        person_id = self.person()
        self.cache(person_id)
        old = self.locator(person_id)
        self.assertTrue(self.library._clear_person_portrait(person_id))
        self.assertFalse(self.library._clear_person_portrait(person_id))
        self.assertEqual((self.row(person_id)["image_url"], self.locator(person_id)), (URL, None))
        self.assertEqual(self.claims(), {old})
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 1})

    def test_deleting_an_unlinked_person_claims_its_portrait(self) -> None:
        person_id = self.person()
        self.cache(person_id)
        old = self.locator(person_id)
        self.library.delete_person_if_unlinked(person_id)
        self.assertEqual(self.count("persons"), 0)
        self.assertEqual(self.claims(), {old})
        self.assertTrue(old in self.names(), "a command never deletes bytes")
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 1})
        self.assertEqual(self.names(), set())
        for call in (lambda: self.library._person_portrait(person_id), lambda: self.cache(person_id),
                     lambda: self.library._clear_person_portrait(person_id)):
            with self.assertRaises(LookupError):
                call()

    def test_a_linked_person_is_not_deleted_and_keeps_its_portrait(self) -> None:
        person_id = self.person()
        self.cache(person_id)
        named = self.locator(person_id)
        work_id = self.library.add_work("Paper")
        self.library.add_role(person_id, work_id, "Author")
        with self.assertRaises(ValueError):
            self.library.delete_person_if_unlinked(person_id)
        self.assertEqual((self.locator(person_id), self.claims()), (named, set()))

    def test_the_schema_refuses_a_partial_or_stale_reference(self) -> None:
        person_id = self.person()
        self.cache(person_id)
        for statement, constraint in (
                ("UPDATE prks.persons SET portrait_sha256 = NULL WHERE id = %s", "persons_portrait_check"),
                ("UPDATE prks.persons SET portrait_locator = NULL WHERE id = %s", "persons_portrait_check"),
                ("UPDATE prks.persons SET portrait_source_url = 'https://x.org/' WHERE id = %s",
                 "persons_portrait_check"),
                ("UPDATE prks.persons SET image_url = 'https://x.org/' WHERE id = %s", "persons_portrait_check"),
                ("UPDATE prks.persons SET portrait_locator = '../x.webp' WHERE id = %s",
                 "persons_portrait_locator_check"),
                ("UPDATE prks.persons SET portrait_byte_size = 0 WHERE id = %s", "persons_portrait_check")):
            with self.subTest(statement=statement):
                with self.assertRaises(ConstraintViolationError) as caught:
                    self.sql(statement, (person_id,))
                self.assertEqual(caught.exception.constraint, constraint)


class SharedReferenceTests(PortraitTestCase):
    def test_a_key_another_person_names_is_never_claimed(self) -> None:
        first, second = self.person(), self.person()
        shared = self.stored()
        self.assertIsInstance(self.commit(first, shared), portraits.Portrait)
        self.assertIsInstance(self.commit(second, shared), portraits.Portrait)
        self.cache(first)
        self.assertEqual(self.claims(), set(), "the second Person still names the key")
        # A stale claim is retired, never acted on.
        self.sql("INSERT INTO prks.object_cleanup_claims (namespace, name, library_id) VALUES ('portraits', %s, %s)",
                 (shared.key.name, self.actor.library_id))
        self.assertIs(lifecycle.settle(self.db, self.objects, shared.key), Settled.REFERENCED)
        self.assertIn(shared.key.name, self.names())
        self.library.delete_person_if_unlinked(second)
        self.assertEqual(self.claims(), {shared.key.name})
        self.assertIs(lifecycle.settle(self.db, self.objects, shared.key), Settled.REMOVED)


class AccessTests(PortraitTestCase):
    def test_another_library_sees_no_person_and_writes_nothing(self) -> None:
        person_id = self.person()
        self.cache(person_id)
        named, names = self.locator(person_id), self.names()
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            for call in (lambda: self.library._person_portrait(person_id), lambda: self.cache(person_id),
                         lambda: self.library._clear_person_portrait(person_id)):
                with self.assertRaises(LookupError):
                    call()
        self.assertEqual((self.fetched, self.names(), self.locator(person_id)), ([URL], names, named))
        # Its own Person cannot adopt this Library's key either.
        with facade_context.acting_as(other.context):
            theirs = self.library.add_person("Ada", "Lovelace", image_url=URL)
        obj = self.stored()
        self.assertIsInstance(self.commit(person_id, obj), portraits.Portrait)
        with self.assertRaises(ObjectMissing):
            self.commit(theirs, obj, ctx=other.context)
        self.assertEqual(self.commit(person_id, self.stored(), ctx=other.context), portraits.NOT_FOUND)

    def test_a_reader_reads_but_is_refused_before_any_fetch(self) -> None:
        person_id = self.person()
        self.cache(person_id)
        named, names = self.locator(person_id), self.names()
        self.fetched.clear()
        with facade_context.acting_as(self.reader_context()):
            self.assertIsNotNone(self.library._person_portrait(person_id))
            for call in (lambda: self.cache(person_id), lambda: self.library._clear_person_portrait(person_id)):
                with self.assertRaises(AccessRefused) as caught:
                    call()
                self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual((self.fetched, self.names(), self.locator(person_id)), ([], names, named))


class ConcurrencyTests(PortraitTestCase):
    def test_a_commit_holding_the_person_then_a_delete_claims_the_new_key(self) -> None:
        person_id = self.person()
        obj = self.stored()
        done, deleted = self.hold_then(
            lambda scope: portraits.commit(scope, self.objects, person_id, URL, obj),
            lambda: self.library.delete_person_if_unlinked(person_id))
        self.assertIsInstance(done, portraits.Portrait)
        self.assertIsNone(deleted)
        self.assertEqual((self.count("persons"), self.claims()), (0, {obj.key.name}))
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 1})

    def test_a_delete_holding_the_person_then_a_commit_is_refused(self) -> None:
        person_id = self.person()
        self.cache(person_id)
        old = self.locator(person_id)
        obj = self.stored()
        deleted, done = self.hold_then(
            lambda scope: persons.delete_person(scope, person_id),
            lambda: self.commit(person_id, obj))
        self.assertEqual((deleted, done), ((True, 0), portraits.NOT_FOUND))
        self.assertEqual(self.claims(), {old, obj.key.name})
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 2})

    def test_a_url_change_holding_the_person_then_a_commit_is_refused(self) -> None:
        person_id = self.person()
        obj = self.stored()
        _, done = self.hold_then(
            lambda scope: persons.update_profile(scope, person_id, {"image_url": OTHER_URL}, None),
            lambda: self.commit(person_id, obj))
        self.assertEqual(done, portraits.SOURCE_CHANGED)
        self.assertEqual((self.locator(person_id), self.claims()), (None, {obj.key.name}))

    def test_a_person_deleted_while_fetching(self) -> None:
        person_id = self.person()
        with self.assertRaises(LookupError):
            self.cache(person_id, before=lambda: self.library.delete_person_if_unlinked(person_id))
        self.assertEqual(self.claims(), self.names())
        self.assertEqual(len(self.names()), 1)

    def test_a_url_changed_while_fetching_caches_nothing(self) -> None:
        person_id = self.person()
        self.assertIsNone(self.cache(
            person_id, before=lambda: self.library.update_person_profile(person_id, {"image_url": OTHER_URL})))
        self.assertIsNone(self.locator(person_id))
        self.assertEqual(self.claims(), self.names())


class FailureTests(PortraitTestCase):
    def test_a_failed_fetch_or_transcode_writes_nothing(self) -> None:
        person_id = self.person()
        self.assertIsNone(self.library._cache_person_portrait(person_id, lambda url: None))
        with self.assertRaises(RuntimeError):
            self.library._cache_person_portrait(person_id, mock.Mock(side_effect=RuntimeError("fetch")))
        # Bytes that are not the WebP or JPEG they claim are refused before a write.
        with self.assertRaises(ValueError):
            self.cache(person_id, PortraitImage(b"<html>", "webp"))
        with self.assertRaises(ValueError):
            self.cache(person_id, PortraitImage(image().body, "jpeg"))
        self.assertEqual((self.names(), self.locator(person_id)), (set(), None))

    def test_an_interrupted_write_publishes_nothing(self) -> None:
        person_id = self.person()

        def broken(_storage: Any, key: StorageKey, write: Any, **_: Any) -> Any:
            raise OSError("disk full")

        with mock.patch.object(LocalFilesystemStorage, "put_new", broken):
            with self.assertRaises(OSError):
                self.cache(person_id)
        self.assertEqual((self.names(), self.locator(person_id)), (set(), None))

    def test_bytes_gone_or_replaced_before_adoption(self) -> None:
        person_id = self.person()
        gone = self.stored()
        self.objects.delete(gone.key)
        replaced = self.stored()
        self.objects.replace(replaced.key, lambda stream: stream.write(b"\0" * replaced.size))
        for obj in (gone, replaced):
            with self.subTest(key=obj.key.name):
                with self.assertRaises(ObjectMissing):
                    self.commit(person_id, obj)
                self.assertIsNone(self.locator(person_id))
        self.assertEqual(self.claims(), set())
        self.assertEqual(self.sweep(), 1, "the replaced orphan")

    def test_a_stalled_write_swept_and_settled_before_adoption_fails_cleanly(self) -> None:
        person_id = self.person()
        obj = self.stored()
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 1})
        with self.assertRaises(ObjectMissing):
            self.commit(person_id, obj)
        self.assertIsNone(self.locator(person_id))

    def test_a_rollback_after_the_switch_leaves_the_person_and_an_orphan(self) -> None:
        person_id = self.person()
        self.cache(person_id)
        before = self.row(person_id)
        obj = self.stored()

        def failing(scope: LibraryScope) -> None:
            portraits.commit(scope, self.objects, person_id, URL, obj)
            raise RuntimeError("after the switch")

        with self.assertRaises(RuntimeError):
            self.run_command(failing)
        self.assertEqual((self.row(person_id), self.claims()), (before, set()))
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.claims(), {obj.key.name})
        self.cache(person_id)
        self.assertEqual(self.claims(), {obj.key.name, before["portrait_locator"]})

    def test_a_missing_changed_or_truncated_object_reads_as_no_portrait(self) -> None:
        person_id = self.person()
        self.cache(person_id)
        key = StorageKey(NAMESPACE_PORTRAITS, self.locator(person_id) or "")
        size = int(self.row(person_id)["portrait_byte_size"])
        # Same size, same WebP header, other bytes: the hash does not describe them.
        body = self.read(key.name)
        self.objects.replace(key, lambda stream: stream.write(body[:-1] + bytes([body[-1] ^ 1])))
        self.assertEqual(len(self.read(key.name)), size)
        self.assertIsNone(self.library._person_portrait(person_id))
        self.objects.replace(key, lambda stream: stream.write(b"RIFF"))
        self.assertIsNone(self.library._person_portrait(person_id))
        self.objects.delete(key)
        self.assertIsNone(self.library._person_portrait(person_id))


class ReferenceTests(PortraitTestCase):
    def test_the_sweep_claims_only_unnamed_portraits_and_settle_is_idempotent(self) -> None:
        person_id = self.person()
        self.cache(person_id)
        named = self.locator(person_id)
        orphan = self.stored()
        self.assertEqual(lifecycle.sweep(self.db, self.objects), 0, "a write in progress is young")
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.claims(), {orphan.key.name})
        self.assertEqual(self.sweep(), 0)
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 1})
        self.assertIs(lifecycle.settle(self.db, self.objects, orphan.key), Settled.ABSENT)
        self.assertEqual(self.names(), {named})

    def test_a_live_restore_run_protects_a_staged_portrait(self) -> None:
        staged = self.stored()
        run_id = self.sql("INSERT INTO prks.restore_runs (library_id) VALUES (%s) RETURNING id",
                          (self.actor.library_id,))[0]["id"]
        self.sql("INSERT INTO prks.restore_run_objects (run_id, namespace, name) VALUES (%s, 'portraits', %s)",
                 (run_id, staged.key.name))
        self.assertEqual(self.sweep(), 0)
        self.sql("INSERT INTO prks.object_cleanup_claims (namespace, name) VALUES ('portraits', %s)",
                 (staged.key.name,))
        self.assertIs(lifecycle.settle(self.db, self.objects, staged.key), Settled.REFERENCED)
        self.assertIn(staged.key.name, self.names())
        self.sql("UPDATE prks.restore_runs SET state = 'failed', finished_at = now() WHERE id = %s", (run_id,))
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 1})


class RestartTests(PortraitTestCase):
    def test_the_portrait_and_its_claims_survive_a_restart(self) -> None:
        person_id = self.person()
        first = image((1, 2, 3))
        self.cache(person_id, first)
        old = self.locator(person_id)
        second = image((90, 90, 90))
        self.cache(person_id, second)
        db = Database(DatabaseConfig(conninfo=self.db.config.conninfo, role="test", pool_min=0, pool_max=2))
        self.addCleanup(db.close)
        library = PostgresLibrary(db, storage=self.storage)
        self.assertEqual(library._person_portrait(person_id)["body"], second.body)
        self.assertEqual(self.claims(), {old})
        self.assertEqual(lifecycle.retry_claims(db, self.objects), {Settled.REMOVED: 1})
        self.assertEqual(lifecycle.retry_claims(db, self.objects), {})
