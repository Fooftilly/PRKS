"""``derived.rebuild`` and the search repair (L-S-1 #637; §8.6).

A rebuild walks each scope in id order and stores its cursor in the job's
payload with each batch. These tests damage the ``derived`` rows and check
that a rebuild restores exactly what a fresh recompute gives, twice in a
row; that ``text`` keeps extracted text and only extracts what is missing;
that a failed run resumes from its stored cursor; and that commands running
during a rebuild keep their projection. Concurrent work is paused with
events, never with sleeps.
"""

from __future__ import annotations

import threading
from typing import Any
from unittest import mock

from backend.domain.identity.library_scope import LibraryScope
from backend.domain.search import rebuild, refresh
from backend.domain.search import text as search_text
from backend.domain.works import notes
from backend.jobs.handlers import HandlerRegistry
from backend.jobs.runner import Outcome
from backend.pg_facade import context as facade_context
from backend.text_index import PDFTextExtractorUnavailable
from tests.pg.test_search_text import TextTestCase


class RebuildTestCase(TextTestCase):
    def registry(self) -> HandlerRegistry:
        registry = super().registry()
        rebuild.attach(registry, self.objects).extractor = self.extract
        return registry

    def enqueue(self, *scopes: str) -> Any:
        return self.command(lambda scope: rebuild.enqueue(scope, scopes or rebuild.SCOPES))

    def rebuild(self, *scopes: str) -> list[Outcome]:
        self.enqueue(*scopes)
        return self.run_jobs()

    def library_with_everything(self) -> list[str]:
        person = self.library.add_person("Ada", "Lovelace")
        first = self.pdf_work("Analytical engine", "notes on the engine", "a.pdf")
        self.library.add_role(person, first, "Author")
        second = self.pdf_work("Second paper", "bernoulli numbers", "b.pdf")
        third = self.library.add_work("Plain record", abstract="An understatement")
        self.command(lambda scope: notes.set_note(scope, third, notes.RESEARCH, "[[concept:Engines]]"))
        self.run_jobs()
        return [first, second, third]

    def snapshot(self) -> dict[str, Any]:
        """Every ``derived`` row of the test Library, without timestamps."""
        return {
            "search": self.sql("SELECT work_id, doc::text AS doc, title_norm, people_norm, text_norm"
                               " FROM derived.work_search ORDER BY work_id"),
            "text": self.sql("SELECT asset_id, content_sha256, extractor_version, status, truncated, text,"
                             " doc::text AS doc FROM derived.asset_text ORDER BY asset_id"),
            "mentions": self.sql("SELECT work_id, concept_id, start_offset FROM derived.concept_mentions"
                                 " ORDER BY work_id, start_offset"),
            "notes": self.sql("SELECT work_id, content_hash FROM derived.work_note_state ORDER BY work_id"),
        }

    def damage(self, works: list[str]) -> None:
        """Stale, missing and orphaned rows in every scope."""
        self.sql("UPDATE derived.work_search SET doc = ''::tsvector, text_norm = 'stale' WHERE work_id = %s",
                 (works[0],))
        self.sql("DELETE FROM derived.work_search WHERE work_id = %s", (works[1],))
        self.sql("INSERT INTO derived.work_search (library_id, work_id, doc, title_norm, people_norm, text_norm)"
                 " SELECT library_id, 'gone-work', ''::tsvector, '', '', '' FROM prks.works WHERE id = %s",
                 (works[0],))
        self.sql("UPDATE derived.asset_text SET doc = ''::tsvector")
        self.sql("INSERT INTO derived.asset_text (library_id, asset_id, content_sha256, extractor_version,"
                 " status, doc) SELECT library_id, 'gone-asset', repeat('a', 64), 1, 'empty', ''::tsvector"
                 " FROM prks.works WHERE id = %s", (works[0],))
        self.sql("DELETE FROM derived.concept_mentions")
        self.sql("INSERT INTO derived.work_note_state (library_id, work_id, content_hash)"
                 " SELECT library_id, 'gone-work', 'x' FROM prks.works WHERE id = %s", (works[0],))

    def rebuild_job(self) -> dict[str, Any]:
        [job] = self.sql("SELECT state, payload, dedupe_key, singleton_key FROM prks.jobs WHERE kind = %s",
                         (rebuild.KIND,))
        return job

    def facade_thread(self, call: Any) -> tuple[threading.Thread, list[Any]]:
        """Start a facade ``call`` on its own thread, bound to the test actor; the list gets its result."""
        out: list[Any] = []

        def run() -> None:
            token = facade_context.bind(self.actor.context)
            try:
                out.append(call())
            except BaseException as exc:  # noqa: BLE001 - reported to the test thread
                out.append(exc)
            finally:
                facade_context.unbind(token)

        thread = threading.Thread(target=run)
        thread.start()
        return thread, out

    def make_runnable(self) -> None:
        self.sql("UPDATE prks.jobs SET run_after = now() WHERE state = 'queued'")


class RebuildTests(RebuildTestCase):
    def test_a_full_rebuild_restores_a_fresh_recompute_and_a_second_changes_nothing(self) -> None:
        works = self.library_with_everything()
        self.assertEqual(self.rebuild(), [Outcome.SUCCEEDED])  # a fresh recompute
        fresh = self.snapshot()
        self.assertTrue(fresh["mentions"] and len(fresh["text"]) == 2 and len(fresh["search"]) == 3)
        self.sql("DELETE FROM prks.jobs")
        self.damage(works)
        self.assertNotEqual(self.snapshot(), fresh)
        self.extracted.clear()
        self.assertEqual(self.rebuild(), [Outcome.SUCCEEDED])
        self.assertEqual(self.snapshot(), fresh)
        self.assertEqual(self.extracted, [])  # the stored text was kept
        self.assertEqual(self.rebuild_job()["payload"],
                         {"scopes": ["research", "search", "text"], "done": ["search", "text", "research"],
                          "cursor": None})
        self.sql("DELETE FROM prks.jobs")
        self.assertEqual(self.rebuild(), [Outcome.SUCCEEDED])
        self.assertEqual(self.snapshot(), fresh)

    def test_only_text_recomputes_doc_without_extracting_again(self) -> None:
        works = self.library_with_everything()
        fresh = self.snapshot()
        self.damage(works)
        self.extracted.clear()
        self.assertEqual(self.rebuild("text"), [Outcome.SUCCEEDED])
        self.assertEqual(self.extracted, [])
        self.assertEqual(self.snapshot()["text"], fresh["text"])
        self.assertNotEqual(self.snapshot()["search"], fresh["search"])  # not this scope
        self.assertEqual(self.found("bernoulli"), [works[1]])

    def test_text_extracts_what_is_missing_and_records_a_corrupt_pdf(self) -> None:
        good = self.pdf_work("Good", "readable words", "g.pdf")
        broken = self.library.add_work("Broken", file_path=self.store_pdf("x.pdf", b"%PDF-1.4 not a pdf"))
        later = self.pdf_work("Later", "more readable words", "l.pdf")
        self.sql("DELETE FROM prks.jobs")
        self.assertEqual(self.rebuild("text"), [Outcome.SUCCEEDED])
        statuses = {work: [row["status"] for row in self.rows(work)] for work in (good, broken, later)}
        self.assertEqual(statuses, {good: ["indexed"], broken: ["failed"], later: ["indexed"]})
        self.assertEqual(set(self.found("readable")), {good, later})

    def test_a_missing_extractor_fails_the_run_which_resumes_from_its_cursor(self) -> None:
        works = [self.pdf_work(f"Paper {i}", f"word{i} text", f"{i}.pdf") for i in range(3)]
        self.sql("DELETE FROM prks.jobs")
        self.sql("DELETE FROM derived.asset_text")
        assets = sorted((self.asset(work)["id"] for work in works), key=lambda a: a.encode())
        calls: list[str] = []

        def extract(path: str) -> tuple[str, bool]:
            calls.append(path)
            if len(calls) == 2:
                raise PDFTextExtractorUnavailable()
            return search_text.extract_pdf(path)

        with mock.patch.object(rebuild, "BATCH", 1), mock.patch.object(self, "extract", extract):
            self.assertEqual(self.rebuild("text"), [Outcome.RETRYING])
            job = self.rebuild_job()
            self.assertEqual((job["state"], job["payload"]["cursor"]), ("queued", assets[0]))
            self.make_runnable()
            self.assertEqual(self.run_jobs(), [Outcome.SUCCEEDED])
        self.assertEqual(len(calls), 4)  # the first Asset was not extracted again
        self.assertEqual(self.all_rows(), 3)

    def test_a_work_whose_reindex_fails_fails_the_run_which_resumes_from_its_work(self) -> None:
        works = sorted((self.library.add_work(f"Work {i}") for i in range(3)), key=lambda w: w.encode())
        for work in works:
            self.command(lambda scope, work=work: notes.set_note(scope, work, notes.RESEARCH, "[[concept:C]]"))
        self.run_jobs()
        self.sql("DELETE FROM derived.concept_mentions")
        real = rebuild.reindex.reindex_work
        seen: list[str] = []

        def flaky(scope: LibraryScope, work_id: str) -> bool:
            seen.append(work_id)
            if work_id == works[1] and seen.count(work_id) == 1:
                raise RuntimeError("reindex failed")
            return real(scope, work_id)

        with mock.patch.object(rebuild.reindex, "reindex_work", flaky):
            self.assertEqual(self.rebuild("research"), [Outcome.RETRYING])
            self.assertEqual(self.rebuild_job()["payload"]["cursor"], works[0])
            self.make_runnable()
            self.assertEqual(self.run_jobs(), [Outcome.SUCCEEDED])
        self.assertEqual(seen, [works[0], works[1], works[1], works[2]])
        self.assertEqual(int(self.scalar("SELECT count(*) FROM derived.concept_mentions")), 3)

    def test_a_queued_rebuild_coalesces_and_another_scope_is_its_own_job(self) -> None:
        first = self.enqueue()
        self.assertIsNotNone(first)
        self.assertIsNone(self.enqueue("text", "search", "research"))
        self.assertIsNotNone(self.enqueue("search"))
        rows = self.sql("SELECT dedupe_key, singleton_key FROM prks.jobs WHERE kind = %s ORDER BY created_at",
                        (rebuild.KIND,))
        library = self.actor.context.library_id
        self.assertEqual([row["dedupe_key"] for row in rows],
                         [f"{library}:research,search,text", f"{library}:search"])
        self.assertEqual({row["singleton_key"] for row in rows}, {f"derived:{library}"})
        with self.assertRaises(ValueError):
            rebuild.scope_key(["search", "everything"])

    def test_another_librarys_rows_are_never_touched(self) -> None:
        self.library_with_everything()
        self.bind(self.other_actor().context)
        theirs = self.pdf_work("Theirs", "their words", "t.pdf")
        self.run_jobs()
        self.bind(self.actor.context)
        self.sql("UPDATE derived.work_search SET text_norm = 'theirs, untouched' WHERE work_id = %s", (theirs,))
        self.rebuild()
        self.assertEqual(self.scalar("SELECT text_norm FROM derived.work_search WHERE work_id = %s", theirs),
                         "theirs, untouched")


class ConcurrencyTests(RebuildTestCase):
    """§8.6: commands keep running during a rebuild, and keep their projection."""

    def run_paused_in_search_batch(self, during: Any) -> None:
        """Run a search rebuild whose first batch pauses after its refresh, with its locks held."""
        held, release = threading.Event(), threading.Event()
        real = refresh.refresh_work_search

        def pausing(scope: LibraryScope, work_ids: Any) -> list[str]:
            locked = real(scope, work_ids)
            if not held.is_set():
                held.set()
                release.wait(10)
            return locked

        self.enqueue("search")
        outcomes: list[Any] = []
        with mock.patch.object(rebuild.refresh, "refresh_work_search", pausing):
            thread = threading.Thread(target=lambda: outcomes.extend(self.run_jobs()))
            thread.start()
            try:
                self.assertTrue(held.wait(10))
                during()
            finally:
                release.set()
                thread.join(10)
        self.assertEqual(outcomes, [Outcome.SUCCEEDED])

    def test_a_metadata_edit_during_a_batch_waits_and_keeps_its_projection(self) -> None:
        work = self.library.add_work("Original title")
        self.sql("DELETE FROM prks.jobs")
        edits: list[Any] = []

        def edit() -> None:
            edits.extend(self.facade_thread(
                lambda: self.library.update_work_metadata(work, {"title": "Renamed title"})))
            self.wait_for_lock_waiter()

        self.run_paused_in_search_batch(edit)
        edits[0].join(10)
        self.assertNotIsInstance(edits[1][0], BaseException)
        self.assertTrue(self.matches(work, "renamed"))
        self.assertFalse(self.matches(work, "original"))

    def test_a_work_created_during_a_rebuild_keeps_its_row(self) -> None:
        self.library.add_work("Existing")
        self.sql("DELETE FROM prks.jobs")
        created: list[str] = []
        self.run_paused_in_search_batch(lambda: created.append(self.library.add_work("Created meanwhile")))
        self.assertTrue(self.matches(created[0], "created", "meanwhile"))

    def test_a_work_deleted_during_a_rebuild_leaves_no_row(self) -> None:
        doomed = self.library.add_work("Doomed")
        self.sql("DELETE FROM prks.jobs")
        deleted: list[Any] = []

        def delete() -> None:
            deleted.extend(self.facade_thread(lambda: self.library.delete_work_record(doomed)))
            self.wait_for_lock_waiter()

        self.run_paused_in_search_batch(delete)
        deleted[0].join(10)
        self.assertNotIsInstance(deleted[1][0], BaseException)
        self.assertIsNone(self.row(doomed))
        self.assertEqual(self.refresh(doomed), [])
        self.assertIsNone(self.row(doomed))


class RepairTests(RebuildTestCase):
    def test_a_gap_schedules_a_repair_that_closes_it(self) -> None:
        works = [self.library.add_work(f"Work {i}") for i in range(3)]
        self.assertTrue(self.command(rebuild.consistency).consistent)
        self.assertIsNone(self.command(rebuild.schedule_repair))
        fresh = self.snapshot()["search"]
        self.sql("DELETE FROM derived.work_search WHERE work_id = ANY(%s)", (works[:2],))
        self.sql("INSERT INTO derived.work_search (library_id, work_id, doc, title_norm, people_norm, text_norm)"
                 " SELECT library_id, 'gone-work', ''::tsvector, '', '', '' FROM prks.works WHERE id = %s",
                 (works[2],))
        self.assertEqual(self.command(rebuild.consistency), rebuild.Consistency(missing=2, orphaned=1))
        self.sql("DELETE FROM prks.jobs")
        self.assertIsNotNone(self.command(rebuild.schedule_repair))
        self.assertIsNone(self.command(rebuild.schedule_repair))  # one queued repair per Library
        with mock.patch.object(rebuild, "BATCH", 1):
            self.assertEqual(self.run_jobs(), [Outcome.SUCCEEDED])
        self.assertEqual(self.snapshot()["search"], fresh)
        self.assertTrue(self.command(rebuild.consistency).consistent)
