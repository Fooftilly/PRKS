"""Managed-PDF ingestion on PostgreSQL (L-A-1 #635; ``backend/domain/objects/ingest.py``).

The filesystem half (``store_pdf``: a source key written with exclusive
create and hashed while written, then a linearized copy under a second key)
and the database half (one Library command that adopts both keys and
inserts the Asset), with the failures between them: processing that fails,
an ingest interrupted before its transaction or rolled back after it, a
sweep that settles the keys before adoption or meets an adoption in
flight, bytes replaced before adoption, and another Library's keys.

qpdf is not required: linearization runs a fake ``subprocess.run`` that
writes what the binary would, as ``tests/test_pdf_linearize_durability.py``
does.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Optional
from unittest import mock

from backend import pdf_linearize
from backend.domain.identity.authorization import AccessRefused
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.objects import ingest, lifecycle
from backend.domain.objects.ingest import IngestedPdf
from backend.domain.objects.lifecycle import ObjectMissing, Settled
from backend.domain.works import commands
from backend.entity_ids import generate
from backend.pg_facade import context as facade_context
from backend.storage.objects import NAMESPACE_ASSET_OBJECTS, StorageKey
from tests.pg.test_objects_lifecycle import ObjectsTestCase

RAW = b"%PDF-1.7\n% the bytes as uploaded\n%%EOF\n"
LINEARIZED = b"%PDF-1.7\n%\x80\x80\x80\x80\n/Linearized 1\n% qpdf output\n%%EOF\n"
ALREADY = b"%PDF-1.7\n/Linearized 1\n% linearized before it arrived\n%%EOF\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def writer(data: bytes) -> Callable[[Any], None]:
    return lambda sink: sink.write(data)


class IngestTestCase(ObjectsTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.qpdf_runs = 0
        self.qpdf_exit = 0
        patches = [
            mock.patch.dict(os.environ, {"PRKS_PDF_LINEARIZE": "1"}),
            mock.patch.object(pdf_linearize.shutil, "which", return_value="/usr/bin/qpdf"),
            mock.patch.object(pdf_linearize.subprocess, "run", self.fake_qpdf),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def fake_qpdf(self, argv: list[str], **_: Any) -> SimpleNamespace:
        self.qpdf_runs += 1
        if self.qpdf_exit == 0:
            with open(argv[3], "wb") as handle:
                handle.write(LINEARIZED)
        return SimpleNamespace(returncode=self.qpdf_exit, stdout="", stderr="")

    def ingest(self, data: bytes = RAW, name: str = "paper.pdf") -> IngestedPdf:
        return ingest.store_pdf(self.objects, name, writer(data))

    def read(self, name: str) -> bytes:
        with self.objects.open_read(StorageKey(NAMESPACE_ASSET_OBJECTS, name)) as handle:
            return handle.read()

    def keys(self) -> set[str]:
        return {found.name for found in self.objects.iter_keys(NAMESPACE_ASSET_OBJECTS)}

    def new_work(self, pdf: IngestedPdf) -> commands.NewWork:
        return commands.NewWork(work_id=generate("W"), manifestation_id=generate("MF"), title="Ingested",
                                asset_id=generate("AS"),
                                asset=commands.NewAsset(source=pdf.managed_file()))

    def adopt(self, pdf: IngestedPdf, ctx: Any = None) -> str:
        new = self.new_work(pdf)
        self.command(lambda scope: commands.create_work(scope, new, objects=self.objects), ctx)
        return new.work_id

    def asset(self, work_id: str) -> dict[str, Any]:
        return self.sql("SELECT storage_locator, source_locator, ingest_sha256, content_sha256, byte_size "
                        "FROM prks.assets WHERE work_id = %s", (work_id,))[0]


class StoreTests(IngestTestCase):
    def test_a_linearized_ingest_keeps_the_source_under_its_own_key(self) -> None:
        pdf = self.ingest()
        self.assertEqual(pdf.linearize, "ok")
        self.assertNotEqual(pdf.source.key, pdf.working.key)
        self.assertEqual((self.read(pdf.source.key.name), pdf.source.sha256, pdf.source.size),
                         (RAW, sha(RAW), len(RAW)), "the raw stream, hashed as written")
        self.assertEqual((self.read(pdf.working.key.name), pdf.working.sha256, pdf.working.size),
                         (LINEARIZED, sha(LINEARIZED), len(LINEARIZED)))
        self.assertEqual(self.keys(), {pdf.source.key.name, pdf.working.key.name})
        work_id = self.adopt(pdf)
        self.assertEqual(self.asset(work_id), {
            "storage_locator": pdf.working.key.name, "source_locator": pdf.source.key.name,
            "ingest_sha256": sha(RAW), "content_sha256": sha(LINEARIZED), "byte_size": len(LINEARIZED)})

    def test_an_already_linearized_pdf_is_its_own_working_file(self) -> None:
        pdf = self.ingest(ALREADY)
        self.assertEqual((pdf.linearize, self.qpdf_runs), ("already", 0))
        self.assertEqual(pdf.working, pdf.source)
        work_id = self.adopt(pdf)
        self.assertEqual(self.asset(work_id), {
            "storage_locator": pdf.source.key.name, "source_locator": None,
            "ingest_sha256": sha(ALREADY), "content_sha256": sha(ALREADY), "byte_size": len(ALREADY)})
        self.assertEqual(self.keys(), {pdf.source.key.name})

    def test_with_linearization_off_the_source_is_the_working_file(self) -> None:
        with mock.patch.dict(os.environ, {"PRKS_PDF_LINEARIZE": "0"}):
            pdf = self.ingest()
        self.assertEqual((pdf.linearize, self.qpdf_runs), ("disabled", 0))
        self.assertEqual(self.asset(self.adopt(pdf))["source_locator"], None)

    def test_failed_processing_keeps_the_upload_and_writes_nothing_else(self) -> None:
        self.qpdf_exit = 2
        pdf = self.ingest()
        self.assertEqual((pdf.linearize, self.qpdf_runs), ("qpdf-failed", 1))
        self.assertEqual(pdf.working, pdf.source)
        self.assertEqual(self.keys(), {pdf.source.key.name})
        self.assertEqual(self.read(self.asset(self.adopt(pdf))["storage_locator"]), RAW)

    def test_a_linearized_copy_that_cannot_be_stored_falls_back_to_the_source(self) -> None:
        real = self.objects.put_new
        calls: list[StorageKey] = []

        def second_fails(key: StorageKey, write: Any, **kwargs: Any) -> Any:
            calls.append(key)
            if len(calls) == 2:
                raise OSError(28, "No space left on device")
            return real(key, write, **kwargs)

        with mock.patch.object(self.objects, "put_new", second_fails):
            pdf = self.ingest()
        self.assertEqual((pdf.linearize, len(calls)), ("error", 2))
        self.assertEqual(pdf.working, pdf.source)
        self.assertEqual(self.keys(), {pdf.source.key.name})

    def test_a_failed_source_write_publishes_nothing(self) -> None:
        def broken(sink: Any) -> None:
            sink.write(RAW[:5])
            raise ConnectionResetError("the client went away")

        with self.assertRaises(ConnectionResetError):
            ingest.store_pdf(self.objects, "paper.pdf", broken)
        self.assertEqual(self.keys(), set())
        self.assertEqual(os.listdir(self.storage.pdfs_dir), [], "no temporary is left behind")

    def test_two_ingests_of_one_file_are_two_objects(self) -> None:
        first, second = self.ingest(), self.ingest()
        self.assertEqual(len({first.source.key, first.working.key, second.source.key, second.working.key}), 4)
        self.assertEqual(first.source.sha256, second.source.sha256)


class FailureTests(IngestTestCase):
    def test_an_ingest_interrupted_before_its_transaction_is_swept_later(self) -> None:
        pdf = self.ingest()  # the request dies here: no transaction names the keys
        self.assertEqual(self.sweep(days=0), 0, "a write in progress is younger than the grace period")
        self.assertEqual(self.sweep(), 2)
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects), {Settled.REMOVED: 2})
        self.assertFalse(self.stored(pdf.source.key.name) or self.stored(pdf.working.key.name))

    def test_a_rolled_back_create_leaves_the_bytes_to_the_sweep(self) -> None:
        pdf = self.ingest()
        new = self.new_work(pdf)

        def create_then_fail(scope: LibraryScope) -> None:
            commands.create_work(scope, new, objects=self.objects)
            raise RuntimeError("the transaction fails after adoption")

        with self.assertRaises(RuntimeError):
            self.command(create_then_fail)
        self.assertEqual((self.count("works"), self.count("assets"), self.claims()), (0, 0, {}))
        self.assertEqual(self.keys(), {pdf.source.key.name, pdf.working.key.name}, "nothing deleted in place")
        self.assertEqual(self.sweep(), 2)

    def test_keys_the_sweep_settled_before_adoption_fail_the_create(self) -> None:
        for gone in ("source", "working"):
            with self.subTest(gone=gone):
                pdf = self.ingest()
                self.sweep()
                self.assertIs(self.settle(getattr(pdf, gone).key), Settled.REMOVED)
                with self.assertRaises(ObjectMissing):
                    self.adopt(pdf)
                self.assertEqual(self.count("assets"), 0, "no locator to an unlinked file")
                lifecycle.retry_claims(self.db, self.objects)

    def test_an_adoption_before_settlement_keeps_both_keys(self) -> None:
        pdf = self.ingest()
        self.sweep()  # the request stalled past the grace period: both keys claimed
        work_id = self.adopt(pdf)
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects), {Settled.REFERENCED: 2})
        self.assertEqual(self.claims(), {})
        self.assertEqual(self.read(self.asset(work_id)["storage_locator"]), LINEARIZED)
        self.assertEqual(self.read(self.asset(work_id)["source_locator"]), RAW)

    def test_bytes_replaced_or_removed_before_adoption_are_refused(self) -> None:
        swapped = self.ingest()
        same_size = bytes(len(LINEARIZED))
        self.objects.replace(swapped.working.key, writer(same_size))
        with self.assertRaises(ObjectMissing):
            self.adopt(swapped)
        removed = self.ingest()
        self.objects.delete(removed.source.key)
        with self.assertRaises(ObjectMissing):
            self.adopt(removed)
        self.assertEqual(self.count("assets"), 0)

    def test_a_failed_ingest_leaves_existing_objects_alone(self) -> None:
        kept = self.ingest()
        kept_id = self.adopt(kept)
        doomed = self.ingest()
        self.objects.delete(doomed.working.key)
        with self.assertRaises(ObjectMissing):
            self.adopt(doomed)
        self.assertEqual(self.claims(), {})
        self.assertEqual(self.read(self.asset(kept_id)["storage_locator"]), LINEARIZED)
        self.assertEqual(self.read(self.asset(kept_id)["source_locator"]), RAW)
        self.assertEqual(self.sweep(), 1, "only the unadopted source of the failed ingest")

    def test_a_deleted_work_claims_its_working_and_source_keys(self) -> None:
        pdf = self.ingest()
        work_id = self.adopt(pdf)
        self.command(lambda scope: commands.delete_work(scope, work_id))
        self.assertEqual(set(self.claims()), {pdf.source.key.name, pdf.working.key.name})
        self.assertEqual(lifecycle.retry_claims(self.db, self.objects), {Settled.REMOVED: 2})


class IsolationTests(IngestTestCase):
    def test_another_library_cannot_adopt_an_ingest(self) -> None:
        other = self.other_actor()
        pdf = self.ingest()
        self.adopt(pdf)
        with self.assertRaises(ObjectMissing):
            self.adopt(pdf, other.context)
        # Nor the source alone, as another Work's file.
        source_only = IngestedPdf(source=pdf.source, working=pdf.source, linearize="already")
        with self.assertRaises(ObjectMissing):
            self.adopt(source_only, other.context)
        self.assertEqual(self.count("assets"), 1)

    def test_a_reader_cannot_upload_and_writes_nothing(self) -> None:
        with facade_context.acting_as(self.reader_context()):
            with self.assertRaises(AccessRefused):
                self.library._add_work_with_pdf("paper.pdf", writer(RAW), title="Refused")
        self.assertEqual((self.keys(), self.count("works")), (set(), 0))


class FacadeTests(IngestTestCase):
    def test_an_upload_creates_the_work_in_one_command(self) -> None:
        work_id = self.library._add_work_with_pdf("Paper One.pdf", writer(RAW), title="Uploaded",
                                                  year="2024")
        work = self.library.get_work(work_id)
        self.assertEqual((work["title"], work["year"]), ("Uploaded", "2024"))
        asset = self.asset(work_id)
        self.assertEqual(work["file_path"], "/api/pdfs/" + asset["storage_locator"])
        self.assertEqual((self.read(asset["storage_locator"]), self.read(asset["source_locator"])),
                         (LINEARIZED, RAW))

    def test_an_upload_refused_before_storing_writes_nothing(self) -> None:
        with self.assertRaises(ValueError):
            self.library._add_work_with_pdf("clip.pdf", writer(RAW), title="Video",
                                            source_kind="video", source_url="https://youtu.be/dQw4w9WgXcQ")
        with self.assertRaises(TypeError):
            self.library._add_work_with_pdf("paper.pdf", writer(RAW), file_path="/api/pdfs/x.pdf")
        with self.assertRaises(TypeError):
            self.library._add_work_with_pdf("paper.pdf", writer(RAW), title="T", colour="red")
        self.assertEqual(self.keys(), set())

    def test_a_create_refused_after_storing_leaves_only_orphans(self) -> None:
        with mock.patch.object(commands, "create_work", side_effect=RuntimeError("refused")):
            with self.assertRaises(RuntimeError):
                self.library._add_work_with_pdf("paper.pdf", writer(RAW), title="Lost")
        self.assertEqual(len(self.keys()), 2)
        self.assertEqual((self.count("works"), self.claims()), (0, {}))
        self.assertEqual(self.sweep(), 2)


class InterleavingTests(IngestTestCase):
    """§13.3 for the two keys of one ingest, with the per-object locks of #958."""

    @contextmanager
    def thread(self, fn: Callable[[], Any]) -> Iterator[list[Any]]:
        outcome: list[Any] = []

        def run() -> None:
            try:
                with facade_context.acting_as(self.actor.context):
                    outcome.append(fn())
            except BaseException as exc:  # noqa: BLE001 - reported to the test thread
                outcome.append(exc)

        worker = threading.Thread(target=run)
        worker.start()
        try:
            yield outcome
        finally:
            worker.join(30)
            self.assertFalse(worker.is_alive())

    def test_a_settle_meeting_an_ingest_in_flight_keeps_both_keys(self) -> None:
        pdf = self.ingest()
        self.sweep()
        adopted, release = threading.Event(), threading.Event()
        new = self.new_work(pdf)

        def create_and_pause(scope: LibraryScope) -> Optional[str]:
            created = commands.create_work(scope, new, objects=self.objects)
            adopted.set()
            release.wait(30)
            return created

        with self.thread(lambda: self.command(create_and_pause)) as written:
            self.assertTrue(adopted.wait(30))
            with self.thread(lambda: lifecycle.retry_claims(self.db, self.objects)) as settled:
                self.wait_until(lambda: self.lock_waiters() == 1, "the settle to wait on an object lock")
                release.set()
        self.assertEqual(written, [new.work_id])
        self.assertEqual(settled, [{Settled.REFERENCED: 2}])
        self.assertTrue(self.stored(pdf.source.key.name) and self.stored(pdf.working.key.name))

    def test_an_ingest_meeting_a_settle_in_flight_fails_cleanly(self) -> None:
        pdf = self.ingest()
        self.sweep()
        deciding, release = threading.Event(), threading.Event()

        def pause() -> None:
            deciding.set()
            release.wait(30)

        self.objects.before_delete = pause
        first = min(pdf.source.key, pdf.working.key, key=str)  # the key the create locks first
        with self.thread(lambda: self.settle(first)) as settled:
            self.assertTrue(deciding.wait(30))
            self.objects.before_delete = None
            with self.thread(lambda: self.adopt(pdf)) as written:
                self.wait_until(lambda: self.lock_waiters() == 1, "the create to wait on the object lock")
                release.set()
        self.assertEqual(settled, [Settled.REMOVED])
        self.assertEqual(len(written), 1)
        self.assertIsInstance(written[0], ObjectMissing)
        self.assertEqual((self.count("works"), self.count("assets")), (0, 0))
