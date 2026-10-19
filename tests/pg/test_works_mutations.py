"""Work field, note, source, open and delete commands on PostgreSQL (L-W-1 #628).

``tests/test_works_mutations_parity.py`` proves the facade's patches and
deletes leave SQLite's JSON and revisions. These tests drive the domain
commands the sync handlers call: each decision table row, the revision
locking under concurrent writers (§7.2), the Work-row lock that orders an
edit and a delete, the tombstones a delete leaves, and Library isolation.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TypeVar

import psycopg

from backend.db.database import Isolation
from backend.domain.identity.authorization import AccessRefused, Capability, Refusal
from backend.domain.identity.library_scope import LibraryScope, library_command
from backend.domain.identity.request_context import RequestContext
from backend.domain.works import commands, metadata, notes, revisions, source
from backend.pg_facade import context as facade_context
from tests.pg.test_works_facade import WorksFacadeTestCase

T = TypeVar("T")

_VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
_OTHER_VIDEO = "https://www.youtube.com/watch?v=9bZkp7q19f0"
_MISSING = "W-" + "E" * 32


class MutationTestCase(WorksFacadeTestCase):
    def run_command(self, fn: Callable[[LibraryScope], T], ctx: RequestContext | None = None) -> T:
        return library_command(self.db, ctx or self.actor.context, Capability.EDIT_LIBRARY, fn,
                               isolation=Isolation.READ_COMMITTED)

    def field(self, work_id: str, name: str, value: str, base: int) -> tuple[int, dict[str, Any]]:
        return self.run_command(lambda scope: metadata.apply_field_operation(scope, work_id, name, value, base))

    def note(self, work_id: str, kind: str, text: str, base: int) -> tuple[int, dict[str, Any]]:
        operation = {"research": notes.rules.RESEARCH_OPERATION, "private": notes.rules.PRIVATE_OPERATION}[kind]
        return self.run_command(lambda scope: notes.apply_note_operation(scope, operation, work_id, text, base))

    def set_source(self, work_id: str, url: str, base: int) -> tuple[int, dict[str, Any]]:
        return self.run_command(lambda scope: source.apply_source_operation(
            scope, work_id, {"kind": "video", "url": url}, base))

    def revisions_of(self, work_id: str) -> dict[tuple[str, str], int]:
        rows = self.sql("SELECT scope_type, scope_id, revision FROM prks.entity_revisions"
                        " WHERE scope_id = %s OR scope_id LIKE %s",
                        (work_id, '["' + work_id + '"%'))
        return {(row["scope_type"], row["scope_id"]): row["revision"] for row in rows}

    def wait_for_lock_waiter(self) -> None:
        with psycopg.connect(self.db_conninfo, autocommit=True) as conn:
            for _ in range(500):
                # A row-lock wait is on the holder's transaction id, which
                # pg_locks lists without a database, so ask pg_stat_activity.
                row = conn.execute(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE wait_event_type = 'Lock' AND datname = current_database()").fetchone()
                if row is not None and row[0]:
                    return
                threading.Event().wait(0.02)
        self.fail("no transaction ever waited for the lock")

    @property
    def db_conninfo(self) -> str:
        return self.db.config.conninfo

    def hold_work_then(self, holder: Callable[[LibraryScope], Any],
                       waiter: Callable[[LibraryScope], T]) -> tuple[Any, T]:
        """Run ``holder`` and keep its transaction open until ``waiter`` blocks on it.

        The order is fixed, not raced: ``waiter`` starts only after
        ``holder`` has taken its locks, and ``holder`` commits only once
        PostgreSQL reports ``waiter`` waiting.
        """
        locked, release = threading.Event(), threading.Event()
        outcome: dict[str, Any] = {}

        def hold(scope: LibraryScope) -> Any:
            result = holder(scope)
            locked.set()
            if not release.wait(30):
                raise RuntimeError("never released")
            return result

        def run(name: str, fn: Callable[[], Any]) -> None:
            try:
                outcome[name] = fn()
            except BaseException as exc:  # noqa: BLE001 - re-raised by the assertion below
                outcome[name] = exc

        ctx = self.actor.context
        first = threading.Thread(target=run, args=("holder", lambda: self.run_command(hold, ctx)))
        first.start()
        self.assertTrue(locked.wait(30))
        second = threading.Thread(target=run, args=("waiter", lambda: self.run_command(waiter, ctx)))
        second.start()
        try:
            self.wait_for_lock_waiter()
        finally:
            release.set()
            first.join(30)
            second.join(30)
        for name in ("holder", "waiter"):
            if isinstance(outcome.get(name), BaseException):
                raise outcome[name]
        return outcome["holder"], outcome["waiter"]

    def concurrently(self, calls: list[Callable[[], T]]) -> list[T]:
        barrier = threading.Barrier(len(calls), timeout=30)
        results: list[Any] = [None] * len(calls)
        ctx = self.actor.context

        def run(index: int) -> None:
            with facade_context.acting_as(ctx):
                barrier.wait()
                try:
                    results[index] = calls[index]()
                except BaseException as exc:  # noqa: BLE001 - re-raised below
                    results[index] = exc

        threads = [threading.Thread(target=run, args=(i,)) for i in range(len(calls))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return results


class FieldDecisionTests(MutationTestCase):
    def test_each_row_of_the_decision_table(self) -> None:
        work_id = self.library.add_work("Work")
        status, body = self.field(work_id, "title", "Renamed", 0)
        self.assertEqual((status, body["code"], body["server_revision"], body["changed"]),
                         (200, "ACKNOWLEDGED", 1, True))
        self.assertEqual(body["value_omitted"], True)  # a title is byte-limited
        # The same value on the current base: acknowledged, nothing advances.
        status, body = self.field(work_id, "title", "Renamed", 1)
        self.assertEqual((status, body["server_revision"], body["changed"]), (200, 1, False))
        # A stale base that agrees converges without a conflict.
        status, body = self.field(work_id, "title", "Renamed", 0)
        self.assertEqual((status, body["code"], body["changed"]), (200, "ACKNOWLEDGED", False))
        # A stale base that disagrees conflicts and reports the current revision.
        status, body = self.field(work_id, "title", "Other", 0)
        self.assertEqual((status, body["code"], body["current_revision"]), (409, "REVISION_CONFLICT", 1))
        # A base from the future is refused whatever the value.
        status, body = self.field(work_id, "title", "Renamed", 2)
        self.assertEqual((status, body["code"], body["current_revision"]), (400, "FUTURE_REVISION", 1))
        self.assertEqual(self.core(work_id)["title"], "Renamed")
        status, body = self.field(work_id, "status", "Completed", 0)
        self.assertEqual((status, body["value"]), (200, "Completed"))
        self.assertEqual(self.revisions_of(work_id), {
            ("work-field", metadata.scope_id(work_id, "title")): 1,
            ("work-field", metadata.scope_id(work_id, "status")): 1,
        })

    def test_fields_land_in_the_row_that_owns_them(self) -> None:
        work_id = self.library.add_work("Paper", file_path=self.store_pdf("p.pdf", b"%PDF-1.4\n"))
        for name, value in (("year", "1999"), ("source_url", "https://e.org/p"), ("thumb_page", "3"),
                            ("source_mime", "application/pdf")):
            self.assertEqual(self.field(work_id, name, value, 0)[0], 200, name)
        rows = self.sql("SELECT m.year, m.url, a.thumb_page, a.media_type FROM prks.manifestations m"
                        " JOIN prks.assets a ON a.id = m.primary_asset_id WHERE m.work_id = %s", (work_id,))
        self.assertEqual(rows, [{"year": "1999", "url": "https://e.org/p", "thumb_page": 3,
                                 "media_type": "application/pdf"}])

    def test_fields_the_source_decides_are_refused(self) -> None:
        video = self.library.add_work("Video", source_url=_VIDEO)
        status, body = self.field(video, "source_url", "https://e.org/x", 0)
        self.assertEqual((status, body["code"]), (409, metadata.WRONG_OPERATION_FOR_SOURCE))
        plain = self.library.add_work("Plain")
        status, body = self.field(plain, "thumb_page", "2", 0)
        self.assertEqual((status, body["code"]), (409, metadata.WRONG_OPERATION_FOR_SOURCE))
        # Clearing it is a no-op, not a refusal: there is nothing to clear.
        status, body = self.field(plain, "thumb_page", "", 0)
        self.assertEqual((status, body["changed"]), (200, False))
        self.assertEqual(self.revisions_of(video), {})  # refused before any revision is locked
        self.assertEqual(self.field(_MISSING, "title", "Ghost", 0)[1]["code"], "ENTITY_NOT_FOUND")

    def test_concurrent_writers_of_one_field_on_one_base(self) -> None:
        work_id = self.library.add_work("Work")
        for _ in range(3):
            base = self.library.get_work_metadata_state(work_id)["fields"]["title"]["revision"]
            results = self.concurrently([
                (lambda value=value, base=base: self.field(work_id, "title", value, base))
                for value in ("A%d" % base, "B%d" % base, "C%d" % base, "D%d" % base)
            ])
            codes = sorted(body["code"] for _status, body in results)
            self.assertEqual(codes, ["ACKNOWLEDGED"] + ["REVISION_CONFLICT"] * 3)
            winner = next(body for _status, body in results if body["code"] == "ACKNOWLEDGED")
            self.assertEqual(winner["server_revision"], base + 1)
            for _status, body in results:
                if body["code"] == "REVISION_CONFLICT":
                    self.assertEqual(body["current_revision"], base + 1)
        self.assertEqual(self.library.get_work_metadata_state(work_id)["fields"]["title"]["revision"], 3)

    def test_concurrent_writers_of_different_fields_never_block_each_other_out(self) -> None:
        work_id = self.library.add_work("Work")
        names = ("year", "publisher", "journal", "volume", "issue", "pages")
        results = self.concurrently([(lambda name=name: self.field(work_id, name, name, 0)) for name in names])
        self.assertEqual([body["server_revision"] for _status, body in results], [1] * len(names))
        state = self.library.get_work_metadata_state(work_id)["fields"]
        self.assertEqual({name: state[name]["value"] for name in names}, {name: name for name in names})


class WaitedForLockTests(MutationTestCase):
    """A command that waited for the Work lock decides on what the holder committed.

    The fields and the source live on the Manifestation and the Asset, not on
    the locked Work row, so these pin that the read after the lock is fresh.
    """

    def test_the_same_value_on_one_base_converges(self) -> None:
        work_id = self.library.add_work("Paper")
        first, second = self.hold_work_then(
            lambda scope: metadata.apply_field_operation(scope, work_id, "doi", "10.1/x", 0),
            lambda scope: metadata.apply_field_operation(scope, work_id, "doi", "10.1/x", 0))
        self.assertEqual((first[1]["code"], first[1]["server_revision"]), ("ACKNOWLEDGED", 1))
        self.assertEqual((second[1]["code"], second[1]["server_revision"], second[1]["changed"]),
                         ("ACKNOWLEDGED", 1, False))

    def test_a_stale_base_holding_the_old_value_conflicts(self) -> None:
        work_id = self.library.add_work("Paper", doi="10.1/old")
        first, second = self.hold_work_then(
            lambda scope: metadata.apply_field_operation(scope, work_id, "doi", "10.1/new", 0),
            lambda scope: metadata.apply_field_operation(scope, work_id, "doi", "10.1/old", 0))
        self.assertEqual(first[1]["server_revision"], 1)
        self.assertEqual((second[0], second[1]["code"], second[1]["current_revision"]),
                         (409, "REVISION_CONFLICT", 1))
        self.assertEqual(self.library.get_work(work_id)["doi"], "10.1/new")

    def test_the_same_video_on_one_base_converges(self) -> None:
        work_id = self.library.add_work("Video", source_url=_VIDEO)
        first, second = self.hold_work_then(
            lambda scope: source.apply_source_operation(
                scope, work_id, {"kind": "video", "url": _OTHER_VIDEO}, 0),
            lambda scope: source.apply_source_operation(
                scope, work_id, {"kind": "video", "url": _OTHER_VIDEO}, 0))
        self.assertEqual((first[1]["changed"], first[1]["server_revision"]), (True, 1))
        self.assertEqual((second[1]["code"], second[1]["changed"], second[1]["server_revision"],
                          second[1]["provider_id"]), ("ACKNOWLEDGED", False, 1, "9bZkp7q19f0"))

    def test_the_old_video_on_a_stale_base_conflicts(self) -> None:
        work_id = self.library.add_work("Video", source_url=_VIDEO)
        _first, second = self.hold_work_then(
            lambda scope: source.apply_source_operation(
                scope, work_id, {"kind": "video", "url": _OTHER_VIDEO}, 0),
            lambda scope: source.apply_source_operation(
                scope, work_id, {"kind": "video", "url": _VIDEO}, 0))
        self.assertEqual((second[0], second[1]["code"], second[1]["current_revision"]),
                         (409, "SOURCE_REVISION_CONFLICT", 1))
        self.assertEqual(self.library.get_work(work_id)["source_url"], _OTHER_VIDEO)


class NoteDecisionTests(MutationTestCase):
    def test_each_row_of_the_decision_table(self) -> None:
        work_id = self.library.add_work("Work", private_notes="secret")
        status, body = self.note(work_id, "research", "First", 0)
        self.assertEqual((status, body["code"], body["server_revision"], body["value_omitted"]),
                         (200, "ACKNOWLEDGED", 1, True))
        self.assertEqual(self.note(work_id, "research", "First", 0)[1]["changed"], False)
        status, body = self.note(work_id, "research", "Second", 0)
        self.assertEqual((status, body["code"], body["current_revision"]), (409, "REVISION_CONFLICT", 1))
        self.assertNotIn("First", repr(body))  # a conflict reports sizes, never prose
        self.assertEqual(self.note(work_id, "research", "First", 5)[1]["code"], "FUTURE_REVISION")
        # Emptying a note removes its row; the revision still advances.
        status, body = self.note(work_id, "private", "", 0)
        self.assertEqual((status, body["server_revision"]), (200, 1))
        self.assertEqual(self.sql("SELECT kind, body FROM prks.work_notes WHERE work_id = %s", (work_id,)),
                         [{"kind": "research", "body": "First"}])
        self.assertEqual(self.note(_MISSING, "private", "x", 0)[1]["code"], "ENTITY_NOT_FOUND")
        with self.assertRaises(ValueError):
            notes.validate("research", "bell\x07")

    def test_concurrent_writers_of_one_note(self) -> None:
        work_id = self.library.add_work("Work")
        results = self.concurrently([
            (lambda text=text: self.note(work_id, "research", text, 0)) for text in ("a", "b", "c", "d", "e")
        ])
        self.assertEqual(sorted(body["code"] for _status, body in results),
                         ["ACKNOWLEDGED"] + ["REVISION_CONFLICT"] * 4)
        self.assertEqual(self.library.get_work_notes_state(work_id)["research_note_revision"], 1)


class SourceDecisionTests(MutationTestCase):
    def test_replacing_a_video(self) -> None:
        work_id = self.library.add_work("Video", source_url=_VIDEO, thumb_url="https://img/old.jpg")
        # Another spelling of the same video is the same source: nothing changes.
        status, body = self.set_source(work_id, "https://youtu.be/dQw4w9WgXcQ", 0)
        self.assertEqual((status, body["changed"], body["server_revision"], body["thumb_url"]),
                         (200, False, 0, "https://img/old.jpg"))
        status, body = self.set_source(work_id, _OTHER_VIDEO, 0)
        self.assertEqual((status, body["changed"], body["server_revision"], body["provider_id"],
                          body["thumb_url"]), (200, True, 1, "9bZkp7q19f0", None))
        self.assertRegex(body["urldate"], r"^\d{4}-\d\d-\d\d$")
        status, body = self.set_source(work_id, _VIDEO, 0)
        self.assertEqual((status, body["code"], body["current_revision"]), (409, "SOURCE_REVISION_CONFLICT", 1))
        self.assertEqual(self.set_source(work_id, _VIDEO, 2)[1]["code"], "FUTURE_REVISION")
        self.assertEqual(self.library.get_work(work_id)["source_url"], _OTHER_VIDEO)
        state = self.run_command(lambda scope: source.source_state(scope, work_id))
        self.assertEqual(state, {"work_id": work_id, "revision": 1})

    def test_only_a_video_may_be_replaced(self) -> None:
        pdf = self.library.add_work("Paper", file_path=self.store_pdf("p.pdf", b"%PDF-1.4\n"))
        plain = self.library.add_work("Plain")
        for work_id in (pdf, plain):
            status, body = self.set_source(work_id, _VIDEO, 0)
            self.assertEqual((status, body["code"]), (409, "UNSUPPORTED_SOURCE_TRANSITION"))
        self.assertEqual(self.set_source(_MISSING, _VIDEO, 0)[1]["code"], "ENTITY_NOT_FOUND")
        with self.assertRaises(ValueError):
            self.set_source(pdf, "https://example.org/not-a-video", 0)
        self.assertEqual(self.revisions_of(pdf), {})

    def test_a_stream_with_a_broken_identity_is_refused(self) -> None:
        work_id = self.library.add_work("Video", source_url=_VIDEO)
        self.sql("UPDATE prks.assets SET provider_id = 'not valid!' WHERE work_id = %s", (work_id,))
        self.assertEqual(self.set_source(work_id, _OTHER_VIDEO, 0)[1]["code"], source.INVALID_SOURCE_STATE)
        self.assertEqual(self.run_command(lambda scope: source.source_state(scope, work_id)),
                         source.INVALID_SOURCE_STATE)


class OpenTests(MutationTestCase):
    def test_concurrent_opens_keep_the_latest(self) -> None:
        work_id = self.library.add_work("Work")
        start = datetime(2026, 1, 2, 3, 4, 5, 678_901, tzinfo=timezone.utc)
        moments = [start + timedelta(milliseconds=i * 7) for i in range(8)]
        self.concurrently([
            (lambda moment=moment: self.run_command(lambda scope: commands.mark_opened(scope, work_id, moment)))
            for moment in reversed(moments)
        ])
        self.assertEqual(self.library.get_work(work_id)["last_opened_at"], "2026-01-02 03:04:05.727")
        # An older event changes nothing and reports the recorded value.
        self.assertEqual(self.run_command(lambda scope: commands.mark_opened(scope, work_id, start)),
                         (True, False, "2026-01-02 03:04:05.727"))
        with self.assertRaises(ValueError):
            self.run_command(lambda scope: commands.mark_opened(scope, work_id, datetime(2026, 1, 1)))


class DeleteTests(MutationTestCase):
    def test_a_delete_cascades_and_tombstones_every_scope(self) -> None:
        work_id = self.library.add_work("Video", source_url=_VIDEO, text_content="research", private_notes="p")
        self.field(work_id, "title", "Renamed", 0)
        self.field(work_id, "year", "2001", 0)
        self.note(work_id, "private", "changed", 0)
        self.set_source(work_id, _OTHER_VIDEO, 0)
        self.run_command(lambda scope: revisions.lock(scope, "work-research-note", work_id))
        before = self.revisions_of(work_id)
        self.assertEqual(len(before), 5)
        survivor = self.library.add_work("Survivor")
        self.field(survivor, "title", "Kept", 0)
        deleted = self.library.delete_work_record(work_id)
        assert deleted is not None
        self.assertEqual((deleted.file_path, deleted.managed_pdf_still_referenced), ("", False))
        self.assertEqual(self.revisions_of(work_id), {key: value + 1 for key, value in before.items()})
        self.assertEqual(self.revisions_of(survivor), {("work-field", metadata.scope_id(survivor, "title")): 1})
        for table in ("manifestations", "assets", "work_notes"):
            self.assertEqual(int(self.sql(f"SELECT count(*) AS n FROM prks.{table} WHERE work_id = %s",
                                          (work_id,))[0]["n"]), 0, table)
        # A device that edited before the delete is stale against the tombstone.
        status, body = self.field(work_id, "title", "Late", 1)
        self.assertEqual((status, body["code"]), (404, "ENTITY_NOT_FOUND"))

    def test_an_edit_holding_the_work_delays_the_delete(self) -> None:
        work_id = self.library.add_work("Work")
        edit, deleted = self.hold_work_then(
            lambda scope: metadata.apply_field_operation(scope, work_id, "title", "Edited", 0),
            lambda scope: commands.delete_work(scope, work_id))
        self.assertEqual(edit[1]["server_revision"], 1)
        self.assertIsNotNone(deleted)
        # The delete saw the edit's committed revision and tombstoned past it.
        self.assertEqual(self.revisions_of(work_id), {("work-field", metadata.scope_id(work_id, "title")): 2})

    def test_a_delete_holding_the_work_makes_the_edit_find_nothing(self) -> None:
        work_id = self.library.add_work("Work")
        deleted, edit = self.hold_work_then(
            lambda scope: commands.delete_work(scope, work_id),
            lambda scope: metadata.apply_field_operation(scope, work_id, "title", "Edited", 0))
        self.assertIsNotNone(deleted)
        self.assertEqual(edit, (404, {"work_id": work_id, "field": "title", "code": "ENTITY_NOT_FOUND"}))
        # No revision row was created for a Work that no longer exists.
        self.assertEqual(self.revisions_of(work_id), {})

    def test_a_shared_file_stays_referenced_only_within_the_library(self) -> None:
        path = self.store_pdf("shared.pdf", b"%PDF-1.4\n")
        first = self.library.add_work("First", file_path=path)
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            theirs = self.library.add_work("Theirs", file_path=self.store_pdf("theirs.pdf", b"%PDF-1.4\n"))
        # Create refuses another Library's key, so give theirs the same name directly:
        # the delete's reference check still looks only inside its own Library.
        self.sql("UPDATE prks.assets SET storage_locator = 'shared.pdf' WHERE work_id = %s", (theirs,))
        deleted = self.library.delete_work_record(first)
        assert deleted is not None
        self.assertEqual((deleted.file_path, deleted.managed_pdf_still_referenced), (path, False))
        with facade_context.acting_as(other.context):
            self.assertIsNotNone(self.library.get_work(theirs))


class IsolationTests(MutationTestCase):
    def test_another_library_cannot_touch_a_work(self) -> None:
        work_id = self.library.add_work("Mine", source_url=_VIDEO)
        other = self.other_actor()
        ctx = other.context
        self.assertEqual(self.run_command(lambda scope: metadata.apply_field_operation(
            scope, work_id, "title", "Stolen", 0), ctx)[1]["code"], "ENTITY_NOT_FOUND")
        self.assertEqual(self.run_command(lambda scope: notes.apply_note_operation(
            scope, notes.rules.RESEARCH_OPERATION, work_id, "x", 0), ctx)[1]["code"], "ENTITY_NOT_FOUND")
        self.assertEqual(self.run_command(lambda scope: source.apply_source_operation(
            scope, work_id, {"kind": "video", "url": _OTHER_VIDEO}, 0), ctx)[1]["code"], "ENTITY_NOT_FOUND")
        self.assertEqual(self.run_command(lambda scope: commands.mark_opened(
            scope, work_id, datetime.now(timezone.utc)), ctx),
            (False, False, None))
        self.assertIsNone(self.run_command(lambda scope: commands.delete_work(scope, work_id), ctx))
        with facade_context.acting_as(ctx):
            self.library.update_work_metadata(work_id, {"title": "Stolen"})
            self.assertIsNone(self.library.get_work_metadata_state(work_id))
        self.assertEqual(self.library.get_work(work_id)["title"], "Mine")
        self.assertEqual(self.revisions_of(work_id), {})

    def test_a_reader_cannot_change_a_work(self) -> None:
        work_id = self.library.add_work("Mine", source_url=_VIDEO)
        with facade_context.acting_as(self.reader_context()):
            for call in (lambda: self.library.mark_work_opened(work_id),
                         lambda: self.library.update_work_metadata(work_id, {"title": "Changed"}),
                         lambda: self.library.update_work_metadata(work_id, {"private_notes": "x"}),
                         lambda: self.library.delete_work_record(work_id)):
                with self.assertRaises(AccessRefused) as caught:
                    call()
                self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
            self.assertEqual(self.library.get_work(work_id)["title"], "Mine")
        self.assertIsNone(self.library.get_work(work_id)["last_opened_at"])
        self.assertEqual(self.revisions_of(work_id), {})
