"""Research Note mentions and ``research.reindex_work`` on PostgreSQL (L-R-1 #634; §8.4).

The derived index is rebuilt by a job each Research Note change enqueues in
its own transaction. These tests run the jobs explicitly (claim, run) and
pause a reindex inside its transaction with events, never with sleeps: a
note edit, a first note and a Work delete each overlap a paused reindex,
and a stale job runs after its Work is gone. They also cover the markup a
note's mentions come from, Concept auto-creation, repeated jobs, the
Research-ACK fence and Library isolation.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.research import reindex
from backend.domain.works import commands, notes
from backend.jobs import queue
from backend.jobs.runner import Outcome, run_job
from tests.pg.jobs_fixtures import LEASE
from tests.pg.test_arguments_sync import ResearchSyncTestCase


class ResearchIndexTestCase(ResearchSyncTestCase):
    def run_jobs(self) -> list[Outcome]:
        """Claim and run every runnable job, in queue order."""
        outcomes = []
        while True:
            job = queue.claim(self.db, "test-worker", lease=LEASE)
            if job is None:
                return outcomes
            outcomes.append(run_job(self.db, job, lease=LEASE))

    def queued(self) -> list[str]:
        rows = self.sql("SELECT payload->>'work_id' AS work_id FROM prks.jobs"
                        " WHERE kind = %s AND state = 'queued' ORDER BY created_at, id", (reindex.KIND,))
        return [row["work_id"] for row in rows]

    def mentions(self, work_id: str) -> dict[str, Any]:
        """The Work's derived rows: Concept and Argument mentions, and the note state's hash."""
        concepts = self.sql("SELECT concept_id, start_offset, end_offset FROM derived.concept_mentions"
                            " WHERE work_id = %s ORDER BY start_offset", (work_id,))
        arguments = self.sql("SELECT argument_id, start_offset, end_offset FROM derived.argument_mentions"
                             " WHERE work_id = %s ORDER BY start_offset", (work_id,))
        state = self.sql("SELECT content_hash FROM derived.work_note_state WHERE work_id = %s", (work_id,))
        return {"concepts": [(r["concept_id"], r["start_offset"], r["end_offset"]) for r in concepts],
                "arguments": [(r["argument_id"], r["start_offset"], r["end_offset"]) for r in arguments],
                "state": state[0]["content_hash"] if state else None}

    def derived_rows(self, work_id: str) -> int:
        return sum(int(self.sql(f"SELECT count(*) AS n FROM derived.{table} WHERE work_id = %s",
                                (work_id,))[0]["n"])
                   for table in ("concept_mentions", "argument_mentions", "work_note_state"))

    def refs(self, work_id: str, ctx: RequestContext | None = None) -> Any:
        return library_query(self.db, ctx or self.actor.context,
                             lambda scope: reindex.research_refs(scope, work_id))

    def concept_ids(self) -> dict[str, str]:
        return {row["name"]: row["id"] for row in self.library.list_concepts()}

    @contextmanager
    def paused_reindex(self, work_id: str) -> Iterator[None]:
        """A reindex of ``work_id`` that has written its rows and holds its transaction open.

        The block runs while it is paused; on leaving, the reindex commits.
        """
        held, release = threading.Event(), threading.Event()
        errors: list[BaseException] = []

        def hold(scope: LibraryScope) -> None:
            reindex.reindex_work(scope, work_id)
            held.set()
            release.wait(10)

        def run() -> None:
            try:
                library_command(self.db, self.actor.context, Capability.EDIT_LIBRARY, hold)
            except BaseException as exc:  # noqa: BLE001 - reported to the test thread
                errors.append(exc)
                held.set()

        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(held.wait(10))
            yield
        finally:
            release.set()
            thread.join(10)
        self.assertEqual(errors, [])

    def in_thread(self, fn: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
        out: list[Any] = []

        def run() -> None:
            try:
                out.append(fn())
            except BaseException as exc:  # noqa: BLE001 - reported to the test thread
                out.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        return thread, out


class MentionTests(ResearchIndexTestCase):
    def test_concept_and_argument_references_become_mentions(self) -> None:
        ethics = self.concept("Ethics")
        self.library.replace_concept_aliases(ethics, ["Morals"])
        argument = self.argument("Claim")
        work = self.library.add_work("W")
        text = (f"On [[concept:ethics]] and [[concept:MORALS]], see [[argument:{argument}|the claim]];"
                " `[[concept:Code]]` and \\[[concept:Escaped]] are not references; [[argument:A-gone]].")
        self.research_note(work, text)
        self.assertEqual(self.mentions(work), {"concepts": [], "arguments": [], "state": None})
        self.assertEqual(self.queued(), [work])
        self.assertEqual(self.run_jobs(), [Outcome.SUCCEEDED])
        found = self.mentions(work)
        first, second = text.index("[[concept:ethics]]"), text.index("[[concept:MORALS]]")
        claim = text.index("[[argument:")
        self.assertEqual(found["concepts"], [(ethics, first, first + len("[[concept:ethics]]")),
                                             (ethics, second, second + len("[[concept:MORALS]]"))])
        gone = text.index("[[argument:A-gone]]")
        # Argument references are recorded as written, as on SQLite.
        self.assertEqual(found["arguments"], [
            (argument, claim, claim + len(f"[[argument:{argument}|the claim]]")),
            ("A-gone", gone, gone + len("[[argument:A-gone]]"))])
        self.assertEqual(found["state"], reindex.content_hash(text))
        self.assertEqual(self.refs(work), {
            "concepts": [{"id": ethics, "name": "Ethics", "aliases": ["Morals"]}],
            "arguments": [{"id": argument, "name": "Claim"}], "pending": False})

    def test_a_note_creates_the_concepts_it_names_once_each(self) -> None:
        existing = self.concept("Virtue")
        work = self.library.add_work("W")
        self.research_note(work, "[[concept:  New   idea ]] [[concept:virtue]] [[concept:NEW IDEA]]"
                                 " `[[concept:Code]]`")
        concepts = self.concept_ids()
        self.assertEqual(sorted(concepts), ["New idea", "Virtue"])
        self.assertEqual(concepts["Virtue"], existing)
        # No revision is advanced for an auto-created Concept (as on SQLite).
        self.assertEqual(self.state(concepts["New idea"])["identity_revision"], 0)
        self.run_jobs()
        self.assertEqual([row[0] for row in self.mentions(work)["concepts"]],
                         [concepts["New idea"], existing, concepts["New idea"]])
        # A note left as it was enqueues nothing and creates nothing.
        self.research_note(work, "[[concept:  New   idea ]] [[concept:virtue]] [[concept:NEW IDEA]]"
                                 " `[[concept:Code]]`")
        self.assertEqual(self.queued(), [])
        self.assertEqual(len(self.concept_ids()), 2)

    def test_a_failed_note_write_rolls_back_its_concepts_and_job(self) -> None:
        work = self.library.add_work("W")

        def failing(scope: LibraryScope) -> None:
            notes.set_note(scope, work, notes.RESEARCH, "[[concept:Rolled back]]")
            raise RuntimeError("after the note")

        with self.assertRaises(RuntimeError):
            self.run_command(failing)
        self.assertEqual((self.concept_ids(), self.queued()), ({}, []))

    def test_a_work_created_with_a_note_and_a_cleared_note_are_indexed(self) -> None:
        ethics = self.concept("Ethics")
        work = self.library.add_work("W", text_content="[[concept:Ethics]]")
        self.assertEqual(self.refs(work)["pending"], True)
        self.run_jobs()
        self.assertEqual(self.mentions(work)["concepts"], [(ethics, 0, 18)])
        self.research_note(work, "")
        self.assertEqual(self.refs(work)["pending"], True)
        self.run_jobs()
        self.assertEqual(self.mentions(work), {"concepts": [], "arguments": [], "state": reindex.content_hash("")})
        self.assertEqual(self.refs(work), {"concepts": [], "arguments": [], "pending": False})
        # A Work created with a note naming an unknown Concept creates it, as a note write does.
        novel = self.library.add_work("Novel", text_content="[[concept:Novel topic]]")
        self.run_jobs()
        self.assertEqual(self.mentions(novel)["concepts"], [(self.concept_ids()["Novel topic"], 0, 23)])
        # A Work never given a note has nothing to wait for.
        self.assertEqual(self.refs(self.library.add_work("Plain"))["pending"], False)
        self.assertIsNone(self.refs("W-" + "0" * 32))


class JobTests(ResearchIndexTestCase):
    def test_running_the_job_again_or_twice_changes_nothing(self) -> None:
        ethics = self.concept("Ethics")
        work = self.library.add_work("W")
        self.research_note(work, "[[concept:Ethics]] and [[concept:Ethics]]")
        self.research_note(work, "[[concept:Ethics]] once")
        self.assertEqual(self.queued(), [work, work])  # never coalesced
        self.assertEqual(self.run_jobs(), [Outcome.SUCCEEDED, Outcome.SUCCEEDED])
        settled = self.mentions(work)
        self.assertEqual(settled["concepts"], [(ethics, 0, 18)])
        for _ in range(2):
            library_command(self.db, self.actor.context, Capability.EDIT_LIBRARY,
                            lambda scope: reindex.reindex_work(scope, work))
            self.assertEqual(self.mentions(work), settled)

    def test_a_job_run_again_after_its_commit_changes_nothing(self) -> None:
        self.concept("Ethics")
        work = self.library.add_work("W")
        self.research_note(work, "[[concept:Ethics]]")
        job = queue.claim(self.db, "lost-worker", lease=LEASE)
        assert job is not None
        self.assertIs(run_job(self.db, job, lease=LEASE), Outcome.SUCCEEDED)
        before = self.mentions(work)
        # At-least-once: a worker that committed and then lost its job runs it again.
        self.sql("UPDATE prks.jobs SET state = 'queued', lease_id = NULL, locked_by = NULL,"
                 " locked_until = NULL, finished_at = NULL WHERE id = %s", (job.id,))
        self.assertEqual(self.run_jobs(), [Outcome.SUCCEEDED])
        self.assertEqual(self.mentions(work), before)

    def test_a_job_without_a_work_id_fails(self) -> None:
        library_command(self.db, self.actor.context, Capability.EDIT_LIBRARY,
                        lambda scope: queue.enqueue_library_job(scope, reindex.KIND, {}, max_attempts=1))
        self.assertEqual(self.run_jobs(), [Outcome.FAILED])
        self.assertEqual(self.sql("SELECT last_error FROM prks.jobs WHERE kind = %s", (reindex.KIND,))[0]
                         ["last_error"], "ValueError")


class ConcurrencyTests(ResearchIndexTestCase):
    def test_a_note_edited_while_a_reindex_of_the_old_note_is_paused(self) -> None:
        old, new = self.concept("Old"), self.concept("New")
        work = self.library.add_work("W")
        self.research_note(work, "[[concept:Old]]")
        self.run_jobs()
        with self.paused_reindex(work):
            # The edit waits for the paused reindex, which holds the note FOR SHARE.
            thread, out = self.in_thread(lambda: self.research_note(work, "[[concept:New]]"))
            self.wait_for_lock_waiter()
            self.assertEqual(out, [])
        thread.join(10)
        self.assertEqual(out, [None])
        self.assertEqual(self.mentions(work)["concepts"], [(old, 0, 15)])  # the paused one committed
        self.run_jobs()
        self.assertEqual(self.mentions(work)["concepts"], [(new, 0, 15)])
        self.assertEqual(self.mentions(work)["state"], reindex.content_hash("[[concept:New]]"))

    def test_a_first_note_created_while_a_reindex_that_found_no_note_is_paused(self) -> None:
        ethics = self.concept("Ethics")
        work = self.library.add_work("W")
        with self.paused_reindex(work):
            # No note row to wait on: the note commits while the reindex is open.
            self.research_note(work, "[[concept:Ethics]]")
        self.assertEqual(self.mentions(work)["concepts"], [])
        self.assertEqual(self.refs(work)["pending"], True)
        self.run_jobs()
        self.assertEqual(self.mentions(work)["concepts"], [(ethics, 0, 18)])
        self.assertEqual(self.refs(work)["pending"], False)

    def test_a_work_deleted_while_a_reindex_of_it_is_paused_leaves_no_rows(self) -> None:
        self.concept("Ethics")
        work = self.library.add_work("W")
        self.research_note(work, "[[concept:Ethics]]")
        with self.paused_reindex(work):
            # The delete waits for the Work row the reindex holds FOR KEY SHARE.
            thread, out = self.in_thread(lambda: self.run_command(lambda scope: commands.delete_work(scope, work)))
            self.wait_for_lock_waiter()
        thread.join(10)
        self.assertEqual(out[0].work_id if out else None, work)
        self.assertGreater(self.derived_rows(work), 0)  # the paused reindex committed its rows
        self.run_jobs()
        self.assertEqual(self.derived_rows(work), 0)

    def test_a_reindex_that_starts_during_a_delete_writes_nothing(self) -> None:
        self.concept("Ethics")
        work = self.library.add_work("W")
        self.research_note(work, "[[concept:Ethics]]")
        self.run_jobs()

        def delete(scope: LibraryScope) -> Any:
            return commands.delete_work(scope, work)

        holder, contender = self.race(delete, lambda: library_command(
            self.db, self.actor.context, Capability.EDIT_LIBRARY,
            lambda scope: reindex.reindex_work(scope, work)))
        self.assertEqual(holder.work_id, work)
        self.assertIs(contender, False)  # it waited for the delete, then found the Work gone
        self.assertEqual(self.derived_rows(work), 0)
        self.run_jobs()
        self.assertEqual(self.derived_rows(work), 0)

    def test_a_stale_reindex_after_the_delete_cannot_recreate_rows(self) -> None:
        self.concept("Ethics")
        work = self.library.add_work("W")
        self.research_note(work, "[[concept:Ethics]]")
        stale = queue.claim(self.db, "slow-worker", lease=LEASE)  # the note's job, not yet run
        assert stale is not None
        self.library.delete_work_record(work)
        self.assertEqual(self.run_jobs(), [Outcome.SUCCEEDED])  # the delete's job
        self.assertIs(run_job(self.db, stale, lease=LEASE), Outcome.SUCCEEDED)
        self.assertEqual(self.derived_rows(work), 0)


class FenceTests(ResearchIndexTestCase):
    def test_the_research_ack_does_not_wait_for_the_index(self) -> None:
        ethics = self.concept("Ethics")
        work = self.library.add_work("W")
        data = self.envelope("SET_WORK_RESEARCH_NOTE", work, {"text": "[[concept:Ethics]]"}, 0,
                             entity_type="work")
        acked = self.send(data)
        self.assertEqual(acked, (200, {"work_id": work, "note_kind": "work-research-note",
                                       "code": "ACKNOWLEDGED", "changed": True, "server_revision": 1,
                                       "value_omitted": True}))
        # Committed with the note: the views wait on the fence until the job ran.
        self.assertEqual(self.queued(), [work])
        self.assertEqual(self.refs(work), {"concepts": [], "arguments": [], "pending": True})
        self.run_jobs()
        self.assertEqual(self.refs(work), {"concepts": [{"id": ethics, "name": "Ethics", "aliases": []}],
                                           "arguments": [], "pending": False})
        # A replay answers from the ledger: no second job, the index unchanged.
        self.assertEqual(self.send(data), acked)
        self.assertEqual(self.queued(), [])
        # The same body under a new op_id changes nothing and enqueues nothing.
        same = self.envelope("SET_WORK_RESEARCH_NOTE", work, {"text": "[[concept:Ethics]]"}, 1,
                             entity_type="work")
        self.assertEqual(self.send(same)[1]["changed"], False)
        self.assertEqual(self.queued(), [])
        self.assertEqual(self.refs(work)["pending"], False)


class IsolationTests(ResearchIndexTestCase):
    def test_libraries_index_their_own_notes(self) -> None:
        ours = self.library.add_work("Ours")
        self.research_note(ours, "[[concept:Shared name]]")
        other = self.other_actor()
        from backend.pg_facade import context as facade_context

        with facade_context.acting_as(other.context):
            theirs = self.library.add_work("Theirs")
            library_command(self.db, other.context, Capability.EDIT_LIBRARY,
                            lambda scope: notes.set_note(scope, theirs, notes.RESEARCH, "[[concept:Shared name]]"))
            their_concept = self.concept_ids()["Shared name"]
            self.assertIsNone(self.refs(ours, other.context))
        our_concept = self.concept_ids()["Shared name"]
        self.assertNotEqual(our_concept, their_concept)  # one per Library
        self.run_jobs()
        self.assertEqual(self.mentions(ours)["concepts"], [(our_concept, 0, 23)])
        self.assertEqual(self.mentions(theirs)["concepts"], [(their_concept, 0, 23)])
        self.assertIsNone(self.refs(theirs))
        # A job of this Library naming another Library's Work finds no Work and touches nothing.
        library_command(self.db, self.actor.context, Capability.EDIT_LIBRARY,
                        lambda scope: reindex.enqueue(scope, theirs))
        self.run_jobs()
        self.assertEqual(self.mentions(theirs)["concepts"], [(their_concept, 0, 23)])
        self.assertEqual(self.sql("SELECT library_id FROM derived.work_note_state WHERE work_id = %s",
                                  (theirs,))[0]["library_id"], other.library_id)
