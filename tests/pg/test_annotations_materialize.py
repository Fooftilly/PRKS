"""Revision-safe PDF annotation materialization on PostgreSQL (L-A-1 #635).

``backend/domain/annotations/materialize.py`` and the facade's claim, mark
and materialize methods: new bytes under a fresh key, adopted in one command
that rechecks the canonical revision under the Work and Asset locks; the old
key claimed in that command and kept until settled; ``source_locator`` and
other Assets' keys never claimed; refusals that claim the refused output;
failures that leave the Asset as it was; and acknowledgements that hold only
for committed bytes. Interleavings are fixed with ``hold_work_then`` (no
timing) or by acting from inside the write of the rendered bytes, which
runs between the claim check and the command.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from datetime import timedelta
from typing import Any, Optional
from unittest import mock

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.domain.annotations import materialize, operations
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.objects import lifecycle
from backend.domain.objects.ingest import IngestedPdf
from backend.domain.objects.lifecycle import ObjectMissing, Settled, StoredObject
from backend.domain.works import commands
from backend.entity_ids import generate
from backend.pdf_materialization import STALE_CODE
from backend.pg_facade import PostgresLibrary
from backend.pg_facade import context as facade_context
from backend.storage.objects import NAMESPACE_ASSET_OBJECTS, LocalFilesystemStorage, StorageKey
from tests.pg.test_annotations import HIGHLIGHT, AnnotationTestCase, annotation

RENDERED = b"%PDF-1.4\n% rendered with the annotation set\n" + b"x" * 512


def writing(data: bytes, before: Any = None) -> Any:
    """A ``Writer`` of ``data``; ``before()`` runs first, while the viewer's bytes arrive."""

    def write(sink: Any) -> None:
        if before is not None:
            before()
        sink.write(data)

    return write


class MaterializeTestCase(AnnotationTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.objects = LocalFilesystemStorage.for_config(self.storage)
        # One key per write, whatever qpdf is installed: the linearized case is built by hand.
        patcher = mock.patch.dict(os.environ, {"PRKS_PDF_LINEARIZE": "0"})
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- state ------------------------------------------------------------------

    def asset(self, work_id: str) -> dict[str, Any]:
        (row,) = self.sql("SELECT id, storage_locator, source_locator, content_sha256, byte_size,"
                          " canonical_annotation_set_revision AS canonical,"
                          " materialized_pdf_annotation_revision AS materialized"
                          " FROM prks.assets WHERE work_id = %s", (work_id,))
        return row

    def claims(self) -> set[str]:
        return {row["name"] for row in self.sql("SELECT name FROM prks.object_cleanup_claims")}

    def names(self) -> set[str]:
        return {key.name for key in self.objects.iter_keys(NAMESPACE_ASSET_OBJECTS)}

    def read(self, name: str) -> bytes:
        with self.objects.open_read(StorageKey(NAMESPACE_ASSET_OBJECTS, name)) as handle:
            return handle.read()

    def edited(self, work_id: str) -> int:
        """One annotation created on ``work_id``; returns the canonical revision."""
        self.create(work_id, HIGHLIGHT)
        return self.asset(work_id)["canonical"]

    def settle_all(self) -> Any:
        return lifecycle.retry_claims(self.db, self.objects)

    def sweep(self) -> int:
        return lifecycle.sweep(self.db, self.objects, grace=timedelta(0), now=time.time() + 60)

    # -- writes -----------------------------------------------------------------

    def materialize(self, work_id: str, claim: Any, data: bytes = RENDERED, before: Any = None) -> dict[str, Any]:
        return self.library._materialize_work_pdf(work_id, writing(data, before), claim)

    def put(self, data: bytes) -> StoredObject:
        key = StorageKey(NAMESPACE_ASSET_OBJECTS, uuid.uuid4().hex + ".pdf")
        info = self.objects.put_new(key, writing(data))
        assert info.sha256 is not None
        return StoredObject(key, info.size, info.sha256, info.version)

    def written(self, data: bytes = RENDERED, *, linearized: Optional[bytes] = None) -> IngestedPdf:
        source = self.put(data)
        working = self.put(linearized) if linearized is not None else source
        return IngestedPdf(source, working, "ok" if linearized is not None else "off")

    def commit(self, work_id: str, written: IngestedPdf, claim: Any, ctx: Any = None) -> Any:
        return self.run_command(lambda scope: self.commit_in(scope, work_id, written, claim), ctx)

    def commit_in(self, scope: LibraryScope, work_id: str, written: IngestedPdf, claim: Any) -> Any:
        return materialize.commit(scope, self.objects, work_id, written, claim)

    def assert_refused(self, code: str, call: Any) -> None:
        error = LookupError if code == materialize.NOT_FOUND else ValueError
        with self.assertRaises(error) as caught:
            call()
        self.assertEqual(str(caught.exception), code)


class SuccessTests(MaterializeTestCase):
    def test_the_rendered_bytes_become_the_works_pdf_at_the_claimed_revision(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        before = self.asset(work_id)
        old_bytes = self.read(before["storage_locator"])
        done = self.materialize(work_id, canonical)
        after = self.asset(work_id)
        digest = hashlib.sha256(RENDERED).hexdigest()
        self.assertNotEqual(after["storage_locator"], before["storage_locator"])
        self.assertEqual((after["content_sha256"], after["byte_size"], after["canonical"], after["materialized"]),
                         (digest, len(RENDERED), canonical, canonical))
        self.assertEqual(self.read(after["storage_locator"]), RENDERED)
        self.assertEqual(done, {"work_id": work_id, "file_path": "/api/pdfs/" + after["storage_locator"],
                                "content_sha256": digest, "byte_size": len(RENDERED),
                                "materialized_pdf_annotation_revision": canonical,
                                "canonical_annotation_set_revision": canonical, "stale": False})
        self.assertEqual(self.objects.verify(StorageKey(NAMESPACE_ASSET_OBJECTS, after["storage_locator"]), digest),
                         True)
        # Nothing was modified in place: the old object still holds the old bytes, claimed.
        self.assertEqual(self.read(before["storage_locator"]), old_bytes)
        self.assertEqual(self.claims(), {before["storage_locator"]})
        self.assertEqual((after["id"], after["source_locator"]), (before["id"], before["source_locator"]))
        self.assertEqual(self.library.get_work(work_id)["file_path"], done["file_path"])
        status = self.library.get_work_pdf_materialization(work_id)
        self.assertEqual((status["stale"], status["code"]), (False, None))
        self.assertEqual(self.library.accept_work_pdf_materialization_claim(work_id, canonical), canonical)
        self.assertEqual(self.library.mark_work_pdf_materialized_if_claim_current(work_id, canonical), canonical)

    def test_annotations_unchanged_while_rendering_are_left_as_they_are(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        self.send(self.op("SET_PDF_ANNOTATION", work_id, annotation(color="#010101"), 0))
        canonical = self.asset(work_id)["canonical"]
        listed, state = self.listed(work_id), self.library.get_work_annotations_state(work_id)
        self.materialize(work_id, canonical)
        # Materialization changes the bytes, never the annotation set or its revisions.
        self.assertEqual((self.listed(work_id), self.library.get_work_annotations_state(work_id)), (listed, state))
        self.assertEqual(self.asset(work_id)["canonical"], canonical)
        # The next edit makes the new bytes stale again.
        self.send(self.op("SET_PDF_ANNOTATION", work_id, annotation(color="#020202"), 1))
        self.assertEqual(self.library.get_work_pdf_materialization(work_id)["stale"], True)
        self.assert_refused(STALE_CODE, lambda: self.library.mark_work_pdf_materialized_if_claim_current(
            work_id, canonical))

    def test_a_linearized_write_claims_its_raw_intermediate(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        old = self.asset(work_id)["storage_locator"]
        written = self.written(RENDERED, linearized=RENDERED + b"% linearized\n")
        done = self.commit(work_id, written, canonical)
        self.assertIsInstance(done, materialize.Materialized)
        after = self.asset(work_id)
        self.assertEqual((after["storage_locator"], after["content_sha256"]),
                         (written.working.key.name, written.working.sha256))
        self.assertEqual(self.claims(), {old, written.source.key.name})
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 2})
        self.assertEqual(self.names(), {written.working.key.name})


class RenderingRaceTests(MaterializeTestCase):
    def test_an_edit_while_rendering_makes_the_output_stale(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        before, names = self.asset(work_id), self.names()
        edit = self.op("SET_PDF_ANNOTATION", work_id, annotation(color="#0A0A0A"), 0)
        self.assert_refused(STALE_CODE, lambda: self.materialize(work_id, canonical, before=lambda: self.send(edit)))
        after = self.asset(work_id)
        self.assertEqual((after["storage_locator"], after["content_sha256"], after["materialized"]),
                         (before["storage_locator"], before["content_sha256"], 0))
        self.assertEqual(after["canonical"], canonical + 1)
        # The refused output is claimed in the refusing command; the old bytes are kept.
        (output,) = self.names() - names
        self.assertEqual(self.claims(), {output})
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 1})
        self.assertEqual(self.names(), names)

    def test_a_delete_while_rendering_makes_the_output_stale(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        delete = self.op("DELETE_PDF_ANNOTATION", work_id, HIGHLIGHT, 0)
        self.assert_refused(STALE_CODE, lambda: self.materialize(work_id, canonical, before=lambda: self.send(delete)))
        self.assertEqual((self.asset(work_id)["materialized"], self.listed(work_id)), (0, []))
        self.assertEqual(len(self.claims()), 1)
        # Rendering the set as it now is succeeds.
        self.materialize(work_id, canonical + 1)
        self.assertEqual(self.asset(work_id)["materialized"], canonical + 1)

    def test_an_edit_holding_the_locks_is_seen_by_the_commit(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        written = self.written()
        edited, refused = self.hold_work_then(
            lambda scope: operations.apply_set(scope, work_id, {"annotation_id": HIGHLIGHT["id"],
                                                                "annotation": annotation(color="#0B0B0B")}, 0),
            lambda scope: self.commit_in(scope, work_id, written, canonical))
        self.assertEqual((edited[1]["code"], refused), ("ACKNOWLEDGED", STALE_CODE))
        self.assertEqual(self.asset(work_id)["materialized"], 0)
        self.assertEqual(self.claims(), {written.working.key.name})

    def test_a_commit_holding_the_locks_is_seen_by_an_edit(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        written = self.written()
        done, edited = self.hold_work_then(
            lambda scope: self.commit_in(scope, work_id, written, canonical),
            lambda scope: operations.apply_set(scope, work_id, {"annotation_id": HIGHLIGHT["id"],
                                                                "annotation": annotation(color="#0C0C0C")}, 0))
        self.assertIsInstance(done, materialize.Materialized)
        self.assertEqual(edited[1]["code"], "ACKNOWLEDGED")
        after = self.asset(work_id)
        # The edit came after the bytes: they are current for the revision they claimed, now stale.
        self.assertEqual((after["storage_locator"], after["materialized"], after["canonical"]),
                         (written.working.key.name, canonical, canonical + 1))


class ConcurrencyTests(MaterializeTestCase):
    def test_two_materializations_of_one_asset_hold_the_locks_in_turn(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        old = self.asset(work_id)["storage_locator"]
        first, second = self.written(RENDERED + b"1"), self.written(RENDERED + b"22")
        one, two = self.hold_work_then(lambda scope: self.commit_in(scope, work_id, first, canonical),
                                       lambda scope: self.commit_in(scope, work_id, second, canonical))
        self.assertEqual((one.replaced, two.replaced), (old, first.working.key.name))
        after = self.asset(work_id)
        self.assertEqual((after["storage_locator"], after["byte_size"], after["materialized"]),
                         (second.working.key.name, len(RENDERED) + 2, canonical))
        self.assertEqual(self.claims(), {old, first.working.key.name})
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 2})
        self.assertEqual(self.read(after["storage_locator"]), RENDERED + b"22")

    def test_concurrent_materializations_leave_one_object_named(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        old = self.asset(work_id)["storage_locator"]
        results = self.concurrently([
            (lambda n=n: self.materialize(work_id, canonical, RENDERED + bytes([65 + n]) * (n + 1)))
            for n in range(4)
        ])
        named = self.asset(work_id)["storage_locator"]
        self.assertIn("/api/pdfs/" + named, [result["file_path"] for result in results])
        written = {result["file_path"][len("/api/pdfs/"):] for result in results}
        self.assertEqual(len(written), 4)
        # Every other write, and the old object, is claimed; only the named one survives settle.
        self.assertEqual(self.claims(), (written - {named}) | {old})
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 4})
        self.assertEqual(self.names(), {named})
        self.assertEqual(self.library.mark_work_pdf_materialized_if_claim_current(work_id, canonical), canonical)

    def test_a_work_delete_holding_the_work_refuses_the_commit(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        old = self.asset(work_id)["storage_locator"]
        written = self.written()
        deleted, refused = self.hold_work_then(lambda scope: commands.delete_work(scope, work_id),
                                               lambda scope: self.commit_in(scope, work_id, written, canonical))
        self.assertIsNotNone(deleted)
        self.assertEqual(refused, materialize.NOT_FOUND)
        self.assertEqual(self.claims(), {old, written.working.key.name})
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 2})
        self.assertEqual(self.names(), set())

    def test_a_commit_holding_the_work_is_released_by_the_delete(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        old = self.asset(work_id)["storage_locator"]
        written = self.written()
        done, deleted = self.hold_work_then(lambda scope: self.commit_in(scope, work_id, written, canonical),
                                            lambda scope: commands.delete_work(scope, work_id))
        self.assertIsInstance(done, materialize.Materialized)
        self.assertIsNotNone(deleted)
        self.assertEqual(self.claims(), {old, written.working.key.name})

    def test_a_work_deleted_while_rendering_is_not_found(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        names = self.names()
        self.assert_refused(materialize.NOT_FOUND, lambda: self.materialize(
            work_id, canonical, before=lambda: self.library.delete_work_record(work_id)))
        self.assertEqual(self.claims(), self.names())
        self.assertEqual(len(self.names() - names), 1)
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 2})


class FailureTests(MaterializeTestCase):
    def test_output_missing_before_adoption_fails_and_changes_nothing(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        before = self.asset(work_id)
        written = self.written()
        self.objects.delete(written.working.key)
        with self.assertRaises(ObjectMissing):
            self.commit(work_id, written, canonical)
        self.assertEqual(self.asset(work_id), before)
        self.assertEqual(self.claims(), set())

    def test_output_replaced_before_adoption_fails_and_changes_nothing(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        before = self.asset(work_id)
        for replacement in (RENDERED + b"longer", RENDERED[:-1] + b"y"):  # another size; the same size
            with self.subTest(size=len(replacement)):
                written = self.written()
                self.objects.replace(written.working.key, writing(replacement))
                with self.assertRaises(ObjectMissing):
                    self.commit(work_id, written, canonical)
                self.assertEqual(self.asset(work_id), before)
        self.assertEqual(self.claims(), set())
        # The unadopted writes are orphans: the sweep claims them, the Asset's bytes stay.
        self.assertEqual(self.sweep(), 2)
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 2})
        self.assertEqual(self.names(), {before["storage_locator"]})

    def test_a_failure_after_adoption_rolls_the_command_back(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        before = self.asset(work_id)
        names = self.names()
        real = lifecycle.replace_object

        def then_fail(*args: Any, **kwargs: Any) -> Any:
            real(*args, **kwargs)
            raise RuntimeError("fails after the switch")

        with mock.patch.object(lifecycle, "replace_object", side_effect=then_fail), \
                self.assertRaises(RuntimeError):
            self.materialize(work_id, canonical)
        self.assertEqual(self.asset(work_id), before)
        self.assertEqual(self.claims(), set())
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 1})
        self.assertEqual(self.names(), names)
        # Nothing was acknowledged; a retry succeeds.
        self.assert_refused(STALE_CODE, lambda: self.library.mark_work_pdf_materialized_if_claim_current(
            work_id, canonical))
        self.materialize(work_id, canonical)
        self.assertEqual(self.asset(work_id)["materialized"], canonical)

    def test_a_write_that_fails_publishes_nothing(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        before, names = self.asset(work_id), self.names()

        def broken(sink: Any) -> None:
            sink.write(RENDERED[:10])
            raise OSError("the upload broke off")

        with self.assertRaises(OSError):
            self.library._materialize_work_pdf(work_id, broken, canonical)
        self.assertEqual((self.asset(work_id), self.names(), self.claims()), (before, names, set()))


class RetentionTests(MaterializeTestCase):
    def test_the_old_object_is_kept_until_its_claim_is_settled(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        old = self.asset(work_id)["storage_locator"]
        old_bytes = self.read(old)
        self.materialize(work_id, canonical)
        self.assertEqual(self.read(old), old_bytes, "a reader of the old key still gets its bytes")
        self.assertEqual(self.claims(), {old})
        self.assertIs(lifecycle.settle(self.db, self.objects, StorageKey(NAMESPACE_ASSET_OBJECTS, old)),
                      Settled.REMOVED)
        self.assertNotIn(old, self.names())

    def test_a_key_another_asset_names_is_not_claimed(self) -> None:
        work_id = self.add_pdf_work("shared.pdf")
        sibling = self.library.add_work("Sibling", file_path="/api/pdfs/shared.pdf")
        canonical = self.edited(work_id)
        self.materialize(work_id, canonical)
        self.assertEqual(self.claims(), set())
        self.assertEqual(self.asset(sibling)["storage_locator"], "shared.pdf")
        self.assertIn("shared.pdf", self.names())
        # Once the sibling lets go, the key is claimed and settled.
        self.library.delete_work_record(sibling)
        self.assertEqual(self.claims(), {"shared.pdf"})
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 1})

    def test_the_pristine_source_is_kept(self) -> None:
        source, working = self.put(b"%PDF-1.4 pristine"), self.put(b"%PDF-1.4 linearized upload")
        work_id = generate("W")
        new = commands.NewWork(
            work_id=work_id, manifestation_id=generate("MF"), title="Uploaded", asset_id=generate("AS"),
            asset=commands.NewAsset(source=commands.ManagedFile(
                storage_locator=working.key.name, sha256=working.sha256, byte_size=working.size,
                version=working.version, source=source, ingest_sha256=source.sha256)))
        self.run_command(lambda scope: commands.create_work(scope, new, objects=self.objects))
        canonical = self.edited(work_id)
        self.materialize(work_id, canonical)
        after = self.asset(work_id)
        self.assertEqual(after["source_locator"], source.key.name)
        self.assertEqual(self.claims(), {working.key.name})
        self.assertEqual(self.settle_all(), {Settled.REMOVED: 1})
        self.assertEqual(self.read(source.key.name), b"%PDF-1.4 pristine")
        # A second materialization claims the first one's bytes, never the source.
        self.materialize(work_id, canonical, RENDERED + b"again")
        self.assertEqual(self.claims(), {after["storage_locator"]})
        self.assertEqual(self.asset(work_id)["source_locator"], source.key.name)


class ClaimTests(MaterializeTestCase):
    def test_invalid_future_and_stale_claims_write_nothing(self) -> None:
        work_id = self.add_pdf_work()
        self.edited(work_id)
        self.send(self.op("SET_PDF_ANNOTATION", work_id, annotation(color="#0D0D0D"), 0))
        canonical = self.asset(work_id)["canonical"]
        names = self.names()
        for claim in (True, float(canonical), str(canonical), None, -1, canonical + 1, canonical - 1, 0):
            with self.subTest(claim=claim):
                for call in (lambda claim=claim: self.library.accept_work_pdf_materialization_claim(work_id, claim),
                             lambda claim=claim: self.library.mark_work_pdf_materialized_if_claim_current(
                                 work_id, claim),
                             lambda claim=claim: self.materialize(work_id, claim)):
                    self.assert_refused(STALE_CODE, call)
                # The claim is checked before any byte is written.
                self.assertEqual((self.names(), self.claims()), (names, set()))
                # Rechecked in the command too.
                written = self.written()
                self.assertEqual(self.commit(work_id, written, claim), STALE_CODE)
                self.assertIn(written.working.key.name, self.claims())
                self.settle_all()
        self.assertEqual(self.asset(work_id)["materialized"], 0)

    def test_no_acknowledgement_without_committed_bytes(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        self.assertEqual(self.library.accept_work_pdf_materialization_claim(work_id, canonical), canonical)
        # Accepting a claim is not materializing: nothing was committed for it.
        self.assert_refused(STALE_CODE, lambda: self.library.mark_work_pdf_materialized_if_claim_current(
            work_id, canonical))
        self.materialize(work_id, canonical)
        self.assertEqual(self.library.mark_work_pdf_materialized_if_claim_current(work_id, canonical), canonical)
        # The committed object gone (or of another size): no acknowledgement either.
        stored = self.asset(work_id)["storage_locator"]
        self.objects.replace(StorageKey(NAMESPACE_ASSET_OBJECTS, stored), writing(b"truncated"))
        self.assert_refused(STALE_CODE, lambda: self.library.mark_work_pdf_materialized_if_claim_current(
            work_id, canonical))
        self.objects.delete(StorageKey(NAMESPACE_ASSET_OBJECTS, stored))
        self.assert_refused(STALE_CODE, lambda: self.library.mark_work_pdf_materialized_if_claim_current(
            work_id, canonical))

    def test_marking_only_lowers_the_materialized_revision(self) -> None:
        work_id = self.add_pdf_work()
        self.edited(work_id)
        self.send(self.op("SET_PDF_ANNOTATION", work_id, annotation(color="#0E0E0E"), 0))
        canonical = self.asset(work_id)["canonical"]
        # SQLite marks the canonical revision here; PostgreSQL has no bytes that embed it.
        self.assert_refused(STALE_CODE, lambda: self.library.mark_work_pdf_materialized(work_id, at_revision=1))
        self.assertEqual(self.library.mark_work_pdf_materialized(work_id), 0)
        self.materialize(work_id, canonical)
        self.assertEqual(self.library.mark_work_pdf_materialized(work_id), canonical)
        for bad in (canonical + 1, True, "1", -1):
            with self.subTest(at_revision=bad):
                self.assert_refused(STALE_CODE, lambda bad=bad: self.library.mark_work_pdf_materialized(
                    work_id, at_revision=bad))
        self.assertEqual(self.library.mark_work_pdf_materialized(work_id, at_revision=canonical - 1), canonical - 1)
        self.assertEqual(self.library.get_work_pdf_materialization(work_id)["stale"], True)
        self.assertEqual(self.library.mark_work_pdf_materialized("W-" + "E" * 32), 0)

    def test_a_work_without_a_managed_pdf_or_missing(self) -> None:
        video = self.library.add_work("Video", source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        for work_id in (video, "W-" + "E" * 32):
            with self.subTest(work_id=work_id):
                for call in (lambda work_id=work_id: self.library.accept_work_pdf_materialization_claim(work_id, 0),
                             lambda work_id=work_id: self.library.mark_work_pdf_materialized_if_claim_current(
                                 work_id, 0),
                             lambda work_id=work_id: self.materialize(work_id, 0)):
                    self.assert_refused(materialize.NOT_FOUND, call)
        self.assertEqual(self.names(), set())


class AccessTests(MaterializeTestCase):
    def test_another_library_cannot_materialize_or_acknowledge(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        before, names = self.asset(work_id), self.names()
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            for call in (lambda: self.library.accept_work_pdf_materialization_claim(work_id, canonical),
                         lambda: self.library.mark_work_pdf_materialized_if_claim_current(work_id, canonical),
                         lambda: self.materialize(work_id, canonical)):
                self.assert_refused(materialize.NOT_FOUND, call)
            self.assertEqual(self.library.mark_work_pdf_materialized(work_id), 0)
        self.assertEqual(self.names(), names)
        written = self.written()
        self.assertEqual(self.commit(work_id, written, canonical, other.context), materialize.NOT_FOUND)
        self.assertEqual(self.asset(work_id), before)

    def test_a_reader_is_refused_before_any_byte_is_written(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        names = self.names()
        reader = self.reader_context()
        with facade_context.acting_as(reader):
            for call in (lambda: self.materialize(work_id, canonical),
                         lambda: self.library.mark_work_pdf_materialized(work_id, at_revision=0)):
                with self.assertRaises(AccessRefused) as caught:
                    call()
                self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
            # Reading the claim's validity is a read.
            self.assertEqual(self.library.accept_work_pdf_materialization_claim(work_id, canonical), canonical)
        self.assertEqual((self.names(), self.asset(work_id)["materialized"]), (names, 0))


class RestartTests(MaterializeTestCase):
    def test_the_revisions_and_bytes_survive_a_restart(self) -> None:
        work_id = self.add_pdf_work()
        canonical = self.edited(work_id)
        done = self.materialize(work_id, canonical)
        self.send(self.op("SET_PDF_ANNOTATION", work_id, annotation(color="#0F0F0F"), 0))
        self.db.close()
        self.db = Database(DatabaseConfig(conninfo=self.db_conninfo, role="test", pool_min=0, pool_max=2))
        self.addCleanup(self.db.close)
        library = PostgresLibrary(self.db, storage=self.storage)
        status = library.get_work_pdf_materialization(work_id)
        self.assertEqual((status["canonical_annotation_set_revision"], status["materialized_pdf_annotation_revision"],
                          status["stale"]), (canonical + 1, canonical, True))
        self.assertEqual(library.get_work(work_id)["file_path"], done["file_path"])
        self.assertEqual(self.read(done["file_path"][len("/api/pdfs/"):]), RENDERED)
        self.assertEqual(self.claims(), {"p.pdf"})
        library._materialize_work_pdf(work_id, writing(RENDERED + b"later"), canonical + 1)
        self.assertEqual(library.mark_work_pdf_materialized_if_claim_current(work_id, canonical + 1), canonical + 1)
