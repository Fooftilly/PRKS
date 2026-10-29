"""Managed-PDF replacement on PostgreSQL (L-A-1 #635; ``backend/domain/objects/replace.py``).

A replacement stores the new document under fresh keys and switches the
Asset to them in one command under the Work and Asset locks: the upload is
the new pristine source, the old working and source keys are claimed unless
something still names them, and the annotation set revisions say truthfully
that the new bytes embed no annotation. Interleavings are fixed with
``hold_work_then`` or by acting from inside the write of the new bytes,
which runs between the check and the command.
"""

from __future__ import annotations

import hashlib
import os
from types import SimpleNamespace
from typing import Any
from unittest import mock

from backend import pdf_linearize
from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.domain.annotations import materialize, operations
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.objects import lifecycle, replace
from backend.domain.objects.ingest import IngestedPdf
from backend.domain.objects.lifecycle import ObjectMissing, Settled
from backend.domain.works import commands
from backend.entity_ids import generate
from backend.pdf_materialization import STALE_CODE
from backend.pg_facade import PostgresLibrary
from backend.pg_facade import context as facade_context
from backend.storage.objects import NAMESPACE_ASSET_OBJECTS, StorageKey
from tests.pg.test_annotations import HIGHLIGHT, annotation
from tests.pg.test_annotations_materialize import RENDERED, MaterializeTestCase, writing

NEW = b"%PDF-1.7\n% another document\n" + b"n" * 300 + b"\n%%EOF\n"
LINEARIZED = b"%PDF-1.7\n/Linearized 1\n% qpdf output\n" + b"l" * 200 + b"\n%%EOF\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ReplaceTestCase(MaterializeTestCase):
    def replace(self, work_id: str, data: bytes = NEW, before: Any = None) -> dict[str, Any]:
        return self.library._replace_work_pdf(work_id, writing(data, before))

    def replace_in(self, scope: LibraryScope, work_id: str, written: IngestedPdf) -> Any:
        return replace.commit(scope, self.objects, work_id, written)

    def full_asset(self, work_id: str) -> dict[str, Any]:
        (row,) = self.sql("SELECT storage_locator, source_locator, content_sha256, ingest_sha256, byte_size,"
                          " canonical_annotation_set_revision AS canonical,"
                          " materialized_pdf_annotation_revision AS materialized"
                          " FROM prks.assets WHERE work_id = %s", (work_id,))
        return row

    def uploaded_work(self, source: bytes, working: bytes) -> str:
        """A Work whose ingest kept a pristine source next to a linearized working file."""
        kept, used = self.put(source), self.put(working)
        work_id = generate("W")
        new = commands.NewWork(
            work_id=work_id, manifestation_id=generate("MF"), title="Uploaded", asset_id=generate("AS"),
            asset=commands.NewAsset(source=commands.ManagedFile(
                storage_locator=used.key.name, sha256=used.sha256, byte_size=used.size,
                version=used.version, source=kept, ingest_sha256=kept.sha256)))
        self.run_command(lambda scope: commands.create_work(scope, new, objects=self.objects))
        return work_id


class LinearizingTestCase(ReplaceTestCase):
    """qpdf faked, as ``tests/pg/test_objects_ingest.py`` does: every write is linearized."""

    def setUp(self) -> None:
        super().setUp()
        for patch in (mock.patch.dict(os.environ, {"PRKS_PDF_LINEARIZE": "1"}),
                      mock.patch.object(pdf_linearize.shutil, "which", return_value="/usr/bin/qpdf"),
                      mock.patch.object(pdf_linearize.subprocess, "run", self.fake_qpdf)):
            patch.start()
            self.addCleanup(patch.stop)

    def fake_qpdf(self, argv: list[str], **_: Any) -> SimpleNamespace:
        with open(argv[3], "wb") as handle:
            handle.write(LINEARIZED)
        return SimpleNamespace(returncode=0, stdout="", stderr="")


class SuccessTests(ReplaceTestCase):
    def test_a_replacement_switches_the_asset_to_the_new_document(self) -> None:
        work_id = self.add_pdf_work()
        before = self.full_asset(work_id)
        old_bytes = self.read(before["storage_locator"])
        done = self.replace(work_id)
        after = self.full_asset(work_id)
        self.assertNotEqual(after["storage_locator"], before["storage_locator"])
        self.assertEqual((after["content_sha256"], after["ingest_sha256"], after["byte_size"], after["source_locator"]),
                         (sha(NEW), sha(NEW), len(NEW), None))
        self.assertEqual(self.read(after["storage_locator"]), NEW)
        self.assertEqual(done, {"status": "success", "work_id": work_id,
                                "file_path": "/api/pdfs/" + after["storage_locator"],
                                "content_sha256": sha(NEW), "ingest_sha256": sha(NEW), "byte_size": len(NEW),
                                "canonical_annotation_set_revision": 1,
                                "materialized_pdf_annotation_revision": 1, "stale": False})
        self.assertEqual(self.library.get_work(work_id)["file_path"], done["file_path"])
        # Nothing was modified in place; the old object is claimed and kept until settled.
        self.assertEqual(self.read(before["storage_locator"]), old_bytes)
        self.assertEqual(self.claims(), {before["storage_locator"]})
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 1})
        self.assertEqual(self.names(), {after["storage_locator"]})

    def test_annotations_stay_and_the_new_bytes_are_stale(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        self.materialize(work_id, canonical)
        self.assertEqual(self.library.mark_work_pdf_materialized_if_claim_current(work_id, canonical), canonical)
        listed, state = self.listed(work_id), self.library.get_work_annotations_state(work_id)
        done = self.replace(work_id)
        self.assertEqual((self.listed(work_id), self.library.get_work_annotations_state(work_id)), (listed, state))
        self.assertEqual((done["canonical_annotation_set_revision"], done["materialized_pdf_annotation_revision"],
                          done["stale"]), (canonical + 1, 0, True))
        status = self.library.get_work_pdf_materialization(work_id)
        self.assertEqual((status["stale"], status["code"]), (True, STALE_CODE))
        # No acknowledgement for a claim on the old document, nor for the new revision before materializing.
        for claim in (canonical, canonical + 1):
            self.assert_refused(STALE_CODE, lambda claim=claim: self.library.mark_work_pdf_materialized_if_claim_current(
                work_id, claim))
        self.assert_refused(STALE_CODE, lambda: self.library.accept_work_pdf_materialization_claim(work_id, canonical))
        # Materializing the set into the new document makes it current again.
        self.materialize(work_id, canonical + 1)
        self.assertEqual(self.library.mark_work_pdf_materialized_if_claim_current(work_id, canonical + 1),
                         canonical + 1)

    def test_without_annotations_the_new_bytes_are_current(self) -> None:
        work_id = self.add_pdf_work()
        self.edited(work_id)
        self.send(self.op("DELETE_PDF_ANNOTATION", work_id, HIGHLIGHT, 0))
        canonical = self.full_asset(work_id)["canonical"]
        done = self.replace(work_id)
        self.assertEqual((done["canonical_annotation_set_revision"], done["materialized_pdf_annotation_revision"],
                          done["stale"]), (canonical + 1, canonical + 1, False))
        self.assertEqual(self.library.mark_work_pdf_materialized_if_claim_current(work_id, canonical + 1),
                         canonical + 1)
        # Byte-only markup in the new document may be adopted: the bytes are not stale.
        adopted = self.library.adopt_byte_only_user_markup(work_id, [annotation(id="ann-from-bytes")])
        self.assertEqual(adopted["adopted"], ["ann-from-bytes"])

    def test_mentioned_people_are_linked(self) -> None:
        person = self.library.add_person("Ada", "Lovelace")
        work_id = self.add_pdf_work()
        self.replace(work_id, NEW + b"\n% see [[Ada Lovelace]] and [[Nobody Known]]\n")
        self.assertTrue(self.library.has_work_role(person, work_id, "Mentioned"))
        # Again: the role is not duplicated.
        self.replace(work_id, NEW + b"\n% [[Lovelace]]\n")
        self.assertEqual(self.sql("SELECT count(*) AS n FROM prks.roles WHERE work_id = %s AND role_type = 'Mentioned'",
                                  (work_id,))[0]["n"], 1)

    def test_a_shared_surname_links_the_oldest_person_not_the_lowest_id(self) -> None:
        first = self.library.add_person("Ada", "Smith")
        second = self.library.add_person("Bob", "Smith")
        # Make the higher ID the older Person, so ID order would pick the other one.
        low_id, high_id = sorted((first, second))
        self.sql("UPDATE prks.persons SET created_at = now() - interval '1 day' WHERE id = %s",
                 (high_id,))
        self.sql("UPDATE prks.persons SET created_at = now() WHERE id = %s", (low_id,))
        work_id = self.add_pdf_work()
        self.replace(work_id, NEW + b"\n% [[Smith]]\n")
        self.assertTrue(self.library.has_work_role(high_id, work_id, "Mentioned"))
        self.assertFalse(self.library.has_work_role(low_id, work_id, "Mentioned"))

    def test_a_materialization_links_mentioned_people_too(self) -> None:
        person = self.library.add_person("Grace", "Hopper")
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        self.materialize(work_id, canonical, RENDERED + b"\n% [[Grace Hopper]]\n")
        self.assertTrue(self.library.has_work_role(person, work_id, "Mentioned"))


class LinearizedTests(LinearizingTestCase):
    def test_the_upload_becomes_the_pristine_source(self) -> None:
        work_id = self.add_pdf_work()
        old = self.full_asset(work_id)["storage_locator"]
        done = self.replace(work_id)
        after = self.full_asset(work_id)
        self.assertIsNotNone(after["source_locator"])
        self.assertNotEqual(after["source_locator"], after["storage_locator"])
        self.assertEqual((self.read(after["source_locator"]), self.read(after["storage_locator"])), (NEW, LINEARIZED))
        self.assertEqual((after["ingest_sha256"], after["content_sha256"], after["byte_size"]),
                         (sha(NEW), sha(LINEARIZED), len(LINEARIZED)))
        self.assertEqual(done["ingest_sha256"], sha(NEW))
        self.assertEqual(self.claims(), {old})
        self.settle_all()
        self.assertEqual(self.names(), {after["source_locator"], after["storage_locator"]})

    def test_a_superseded_pristine_source_is_claimed(self) -> None:
        work_id = self.uploaded_work(b"%PDF-1.4 first upload", b"%PDF-1.4 first, linearized")
        before = self.full_asset(work_id)
        self.replace(work_id)
        after = self.full_asset(work_id)
        self.assertEqual(self.claims(), {before["storage_locator"], before["source_locator"]})
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 2})
        self.assertEqual(self.names(), {after["storage_locator"], after["source_locator"]})
        # A second replacement claims the first one's two keys, nothing else.
        self.replace(work_id, NEW + b"again")
        self.assertEqual(self.claims(), {after["storage_locator"], after["source_locator"]})

    def test_a_linearized_materialization_through_the_facade(self) -> None:
        # The facade hands ingest's two keys to the commit: the working key is
        # adopted, the raw intermediate claimed, and only the working key survives.
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        old = self.full_asset(work_id)["storage_locator"]
        names = self.names()
        done = self.materialize(work_id, canonical)
        after = self.full_asset(work_id)
        written = self.names() - names
        self.assertEqual(len(written), 2)
        (raw,) = written - {after["storage_locator"]}
        self.assertEqual((self.read(raw), self.read(after["storage_locator"])), (RENDERED, LINEARIZED))
        self.assertEqual((after["content_sha256"], done["content_sha256"], after["source_locator"]),
                         (sha(LINEARIZED), sha(LINEARIZED), None))
        self.assertEqual(self.claims(), {old, raw})
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 2})
        self.assertEqual(self.names(), {after["storage_locator"]})


class SharedReferenceTests(LinearizingTestCase):
    def test_keys_another_asset_names_are_not_claimed(self) -> None:
        work_id = self.add_pdf_work("shared.pdf")
        sibling = self.library.add_work("Sibling", file_path="/api/pdfs/shared.pdf")
        self.replace(work_id)
        self.assertEqual(self.claims(), set())
        self.assertEqual(self.full_asset(sibling)["storage_locator"], "shared.pdf")
        self.library.delete_work_record(sibling)
        self.assertEqual(self.claims(), {"shared.pdf"})

    def test_a_shared_pristine_source_is_not_claimed(self) -> None:
        work_id = self.uploaded_work(b"%PDF-1.4 pristine", b"%PDF-1.4 linearized")
        before = self.full_asset(work_id)
        sibling = self.library.add_work("Sibling", file_path="/api/pdfs/" + before["source_locator"])
        self.replace(work_id)
        self.assertEqual(self.claims(), {before["storage_locator"]})
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 1})
        self.assertEqual(self.read(self.full_asset(sibling)["storage_locator"]), b"%PDF-1.4 pristine")


class RefusalTests(ReplaceTestCase):
    def test_a_missing_work_or_one_without_a_managed_pdf(self) -> None:
        video = self.library.add_work("Video", source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        for work_id in (video, "W-" + "E" * 32):
            with self.subTest(work_id=work_id):
                self.assert_refused(replace.NOT_FOUND, lambda work_id=work_id: self.replace(work_id))
                written = self.written(NEW)
                self.assertEqual(self.commit_replace(work_id, written), replace.NOT_FOUND)
                self.assertIn(written.working.key.name, self.claims())
        self.settle_all()
        self.assertEqual(self.names(), set())

    def commit_replace(self, work_id: str, written: IngestedPdf, ctx: Any = None) -> Any:
        return self.run_command(lambda scope: self.replace_in(scope, work_id, written), ctx)

    def test_a_reader_is_refused_before_any_byte_is_written(self) -> None:
        work_id = self.add_pdf_work()
        names, before = self.names(), self.full_asset(work_id)
        with facade_context.acting_as(self.reader_context()):
            with self.assertRaises(AccessRefused) as caught:
                self.replace(work_id)
            self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual((self.names(), self.full_asset(work_id)), (names, before))

    def test_another_library_cannot_replace(self) -> None:
        work_id = self.add_pdf_work()
        names, before = self.names(), self.full_asset(work_id)
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            self.assert_refused(replace.NOT_FOUND, lambda: self.replace(work_id))
        self.assertEqual(self.names(), names)
        written = self.written(NEW)
        self.assertEqual(self.commit_replace(work_id, written, other.context), replace.NOT_FOUND)
        self.assertEqual(self.full_asset(work_id), before)


class ConcurrencyTests(ReplaceTestCase):
    def test_two_replacements_hold_the_locks_in_turn(self) -> None:
        work_id = self.add_pdf_work()
        old = self.full_asset(work_id)["storage_locator"]
        first, second = self.written(NEW + b"1"), self.written(NEW + b"22")
        one, two = self.hold_work_then(lambda scope: self.replace_in(scope, work_id, first),
                                       lambda scope: self.replace_in(scope, work_id, second))
        self.assertEqual((one.released, two.released), ((old,), (first.working.key.name,)))
        after = self.full_asset(work_id)
        self.assertEqual((after["storage_locator"], after["byte_size"], after["canonical"]),
                         (second.working.key.name, len(NEW) + 2, 2))
        self.assertEqual(self.claims(), {old, first.working.key.name})
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 2})

    def test_concurrent_replacements_leave_one_object_named(self) -> None:
        work_id = self.add_pdf_work()
        old = self.full_asset(work_id)["storage_locator"]
        results = self.concurrently([(lambda n=n: self.replace(work_id, NEW + bytes([65 + n]) * (n + 1)))
                                     for n in range(4)])
        named = self.full_asset(work_id)["storage_locator"]
        written = {result["file_path"][len("/api/pdfs/"):] for result in results}
        self.assertEqual((len(written), named in written), (4, True))
        self.assertEqual(self.claims(), (written - {named}) | {old})
        self.assertEqual(self.full_asset(work_id)["canonical"], 4)

    def test_an_annotation_create_holding_the_locks_then_a_replacement(self) -> None:
        work_id = self.add_pdf_work()
        written = self.written(NEW)
        created, done = self.hold_work_then(
            lambda scope: operations.apply_create(scope, work_id, {"annotation_id": "ann-1", "annotation": HIGHLIGHT}),
            lambda scope: self.replace_in(scope, work_id, written))
        self.assertEqual(created[1]["code"], "ACKNOWLEDGED")
        # The replacement saw the committed annotation: its bytes do not embed it.
        self.assertEqual((done.canonical, done.materialized), (2, 0))
        self.assertEqual(len(self.listed(work_id)), 1)

    def test_a_replacement_holding_the_locks_then_an_annotation_delete(self) -> None:
        work_id = self.add_pdf_work()
        self.edited(work_id)
        written = self.written(NEW)
        done, deleted = self.hold_work_then(
            lambda scope: self.replace_in(scope, work_id, written),
            lambda scope: operations.apply_delete(scope, work_id, "ann-1", 0))
        self.assertEqual((done.canonical, done.materialized), (2, 0))
        self.assertEqual(deleted[1]["code"], "ACKNOWLEDGED")
        after = self.full_asset(work_id)
        # The bytes embed nothing and the set is now empty, but only a materialization says so.
        self.assertEqual((after["canonical"], after["materialized"]), (3, 0))
        self.assertEqual(self.listed(work_id), [])

    def test_a_replacement_while_a_materialization_renders(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        # The viewer renders the old document; the replacement commits meanwhile.
        self.assert_refused(STALE_CODE, lambda: self.materialize(
            work_id, canonical, before=lambda: self.replace(work_id)))
        after = self.full_asset(work_id)
        self.assertEqual((self.read(after["storage_locator"]), after["materialized"]), (NEW, 0))

    def test_a_materialization_holding_the_locks_then_a_replacement(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        rendered, written = self.written(RENDERED), self.written(NEW)
        done, replaced = self.hold_work_then(
            lambda scope: materialize.commit(scope, self.objects, work_id, rendered, canonical),
            lambda scope: self.replace_in(scope, work_id, written))
        self.assertIsInstance(done, materialize.Materialized)
        self.assertEqual((replaced.canonical, replaced.materialized), (canonical + 1, 0))
        self.assertEqual(self.claims(), {"p.pdf", rendered.working.key.name})
        self.assert_refused(STALE_CODE, lambda: self.library.mark_work_pdf_materialized_if_claim_current(
            work_id, canonical))

    def test_a_work_delete_holding_the_work_refuses_the_replacement(self) -> None:
        work_id = self.add_pdf_work()
        written = self.written(NEW)
        deleted, refused = self.hold_work_then(lambda scope: commands.delete_work(scope, work_id),
                                               lambda scope: self.replace_in(scope, work_id, written))
        self.assertIsNotNone(deleted)
        self.assertEqual(refused, replace.NOT_FOUND)
        self.assertEqual(self.claims(), {"p.pdf", written.working.key.name})
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 2})
        self.assertEqual(self.names(), set())

    def test_a_replacement_holding_the_work_then_the_delete(self) -> None:
        work_id = self.add_pdf_work()
        written = self.written(NEW)
        done, deleted = self.hold_work_then(lambda scope: self.replace_in(scope, work_id, written),
                                            lambda scope: commands.delete_work(scope, work_id))
        self.assertIsInstance(done, replace.Replaced)
        self.assertIsNotNone(deleted)
        self.assertEqual(self.claims(), {"p.pdf", written.working.key.name})

    def test_a_work_deleted_while_uploading_is_not_found(self) -> None:
        work_id = self.add_pdf_work()
        self.assert_refused(replace.NOT_FOUND, lambda: self.replace(
            work_id, before=lambda: self.library.delete_work_record(work_id)))
        self.assertEqual(self.claims(), self.names())
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 2})


class FailureTests(ReplaceTestCase):
    def test_a_new_object_missing_or_replaced_before_adoption(self) -> None:
        work_id = self.add_pdf_work()
        before = self.full_asset(work_id)
        for spoil in ("delete", "replace"):
            with self.subTest(spoil=spoil):
                written = self.written(NEW)
                if spoil == "delete":
                    self.objects.delete(written.working.key)
                else:
                    self.objects.replace(written.working.key, writing(NEW[:-1] + b"x"))
                with self.assertRaises(ObjectMissing):
                    self.run_command(lambda scope, written=written: self.replace_in(scope, work_id, written))
                self.assertEqual(self.full_asset(work_id), before)
        self.assertEqual(self.claims(), set())

    def test_a_stalled_upload_swept_before_adoption_fails_cleanly(self) -> None:
        work_id = self.add_pdf_work()
        before = self.full_asset(work_id)
        written = self.written(NEW)
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 1})
        with self.assertRaises(ObjectMissing):
            self.run_command(lambda scope: self.replace_in(scope, work_id, written))
        self.assertEqual((self.full_asset(work_id), self.claims()), (before, set()))

    def test_a_failure_after_the_switch_rolls_back(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        before, names = self.full_asset(work_id), self.names()
        real = lifecycle.release

        def then_fail(*args: Any, **kwargs: Any) -> Any:
            real(*args, **kwargs)
            raise RuntimeError("fails after the switch")

        with mock.patch.object(lifecycle, "release", side_effect=then_fail), self.assertRaises(RuntimeError):
            self.replace(work_id)
        self.assertEqual((self.full_asset(work_id), self.claims()), (before, set()))
        self.assertEqual(self.full_asset(work_id)["canonical"], canonical)
        # The written object is an orphan the sweep claims; a retry succeeds.
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 1})
        self.assertEqual(self.names(), names)
        self.replace(work_id)
        self.assertEqual(self.full_asset(work_id)["canonical"], canonical + 1)

    def test_an_interrupted_upload_publishes_nothing(self) -> None:
        work_id = self.add_pdf_work()
        before, names = self.full_asset(work_id), self.names()

        def broken(sink: Any) -> None:
            sink.write(NEW[:10])
            raise OSError("the upload broke off")

        with self.assertRaises(OSError):
            self.library._replace_work_pdf(work_id, broken)
        self.assertEqual((self.full_asset(work_id), self.names(), self.claims()), (before, names, set()))


class RestartTests(ReplaceTestCase):
    def test_the_replacement_and_its_claims_survive_a_restart(self) -> None:
        work_id = self.add_pdf_work()
        self.edited(work_id)
        done = self.replace(work_id)
        self.db.close()
        self.db = Database(DatabaseConfig(conninfo=self.db_conninfo, role="test", pool_min=0, pool_max=2))
        self.addCleanup(self.db.close)
        library = PostgresLibrary(self.db, storage=self.storage)
        self.assertEqual(library.get_work(work_id)["file_path"], done["file_path"])
        status = library.get_work_pdf_materialization(work_id)
        self.assertEqual((status["canonical_annotation_set_revision"], status["stale"]), (2, True))
        self.assertEqual(self.claims(), {"p.pdf"})
        # Settling is idempotent: a second pass finds nothing owed.
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects), {Settled.REMOVED: 1})
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects), {})
        self.assertEqual(self.read(done["file_path"][len("/api/pdfs/"):]), NEW)
