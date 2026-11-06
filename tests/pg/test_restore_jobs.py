"""Restores against the jobs runtime (L-B-1 #639; §11.4, §13.3).

A long job running across a real restore: it holds no Library lock while it
works, so the restore does not wait for it, and its write after R4 is
refused, never landing in the replacement state.

A restore killed straight after its R4 commit: the child process commits R4 and exits before R5, so its staging schema stays
and its session ends without the release. The Library is already the
archive's at the new epoch, with its derived rows deleted and the rebuild
queued in the R4 transaction. After the "restart" (recovery, then the worker
running the queued jobs) the stale schema is gone and search finds the
restored Work again, without anything the killed process should have done.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Any

from backend.jobs import queue
from backend.jobs.runner import Outcome, run_job
from tests.pg.jobs_fixtures import LEASE

from tests.pg.restore_kill_child import KILLED_EXIT
from tests.pg.test_restore_commit import RestoreTestCase
from tests.pg.test_search_rebuild import RebuildTestCase

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class KillAfterCommitTests(RestoreTestCase, RebuildTestCase):
    def test_a_restore_killed_after_r4_still_rebuilds_search_after_restart(self) -> None:
        kept = self.pdf_work("Analytical engine", "bernoulli numbers", "a.pdf")
        self.run_jobs()
        self.assertEqual(self.found("bernoulli"), [kept])
        archive = self.backup().path
        later = self.pdf_work("Later paper", "difference engine", "b.pdf")
        self.run_jobs()

        child = subprocess.run(
            [sys.executable, "-m", "tests.pg.restore_kill_child", self.conninfo, self.storage.root, archive,
             self.actor.library_id, self.actor.user_id, self.staging_dir],
            cwd=_PROJECT_DIR, capture_output=True, timeout=120, env=dict(os.environ),
        )
        self.assertEqual(child.returncode, KILLED_EXIT, child.stderr.decode(errors="replace"))

        # R4 committed: the archive's rows at epoch 2, flag cleared, follow-ups queued.
        self.assertEqual(self.work_ids(), [kept])
        library = self.library_row()
        self.assertEqual((library["maintenance"], library["epoch"]), (None, 2))
        (run,) = self.restore_runs()
        self.assertEqual(run["state"], "committed")
        self.assertEqual(self.sql("SELECT 1 FROM derived.work_search WHERE library_id = %s",
                                  (self.actor.library_id,)), [])
        self.assertEqual(self.sql("SELECT 1 FROM derived.asset_text"), [])
        self.assertIn(("derived.rebuild", "queued", 2),
                      {(row["kind"], row["state"], row["library_epoch"]) for row in self.jobs()})
        # R5 never ran: its staging schema is left; the server freed the session's locks.
        self.assertEqual(len(self.staging_schemas()), 1)
        self.assertEqual(self.advisory_locks(), 0)

        # Restart: recovery drops the schema, the worker runs the queued rebuild.
        recovered = self.recover()
        self.assertEqual((recovered.restores_failed, recovered.flags_cleared, recovered.schemas_dropped),
                         (0, 0, 1))
        self.assertEqual(self.staging_schemas(), [])
        self.sql("DELETE FROM prks.jobs WHERE kind IN ('objects.cleanup', 'objects.sweep')")  # objects lane's
        self.run_jobs()
        # The actor's context still carries epoch 1, so read the rebuilt rows directly.
        searched = self.sql("SELECT work_id FROM derived.work_search WHERE library_id = %s",
                            (self.actor.library_id,))
        self.assertEqual([row["work_id"] for row in searched], [kept])
        self.assertTrue(self.matches(kept, "analytical"))
        # The PDF text R4 deleted is extracted again for the restored Asset.
        text = self.sql("SELECT t.status, t.text FROM derived.asset_text t JOIN prks.assets a ON a.id = t.asset_id"
                        " WHERE a.work_id = %s", (kept,))
        self.assertEqual(text, [{"status": "indexed", "text": "bernoulli numbers\n"}])
        self.assertNotIn(later, self.work_ids())


class LongJobTests(RestoreTestCase, RebuildTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.extracting, self.finish = threading.Event(), threading.Event()

    def extract(self, path: str) -> tuple[str, bool]:
        if self.fake == "block":
            self.extracting.set()
            self.finish.wait(30)
            return "stale old epoch text", False
        return super().extract(path)

    def test_a_job_running_across_a_restore_never_writes_to_the_replacement(self) -> None:
        kept = self.pdf_work("Analytical engine", "bernoulli numbers", "a.pdf")
        self.run_jobs()
        archive = self.backup().path
        self.pdf_work("Later paper", "difference engine", "b.pdf")
        job = queue.claim(self.db, "long-worker", lease=LEASE)
        assert job is not None and job.kind == "text.extract"
        self.fake = "block"
        outcome: list[Any] = []
        worker = threading.Thread(target=lambda: outcome.append(
            run_job(self.db, job, registry=self.registry(), lease=LEASE)))
        worker.start()
        self.addCleanup(worker.join, 30)
        self.addCleanup(self.finish.set)
        self.assertTrue(self.extracting.wait(30))
        # The job holds no Library lock while it works: the restore does not wait for it.
        result = self.restore(archive)
        self.assertEqual(result.epoch, 2)
        self.finish.set()
        worker.join(30)
        self.assertFalse(worker.is_alive())
        self.assertIn(outcome[0], (Outcome.LOST, Outcome.CANCELLED))
        self.assertEqual(self.work_ids(), [kept])
        self.assertEqual(self.sql("SELECT 1 FROM derived.asset_text WHERE text LIKE 'stale%%'"), [])
        (row,) = self.sql("SELECT state FROM prks.jobs WHERE id = %s", (job.id,))
        self.assertEqual(row["state"], "cancelled")
