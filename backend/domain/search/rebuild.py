"""``derived.rebuild`` and the search repair (L-S-1 #637; ``docs/postgresql-architecture.md`` §8.6).

``prks admin rebuild-derived`` (and, from PR 6 on, restore and the folding
check) enqueues one ``derived.rebuild`` job per Library with a set of
scopes, ``search``, ``text`` and ``research``. It recomputes the ``derived``
rows while ordinary commands keep running, and never truncates a table that
commands also write. Each scope walks the Library in id order in short
transactions and stores its cursor in the job's payload **in the
transaction of the batch it covers** (:func:`queue.save_library_job_payload`),
so a failed, retried or reclaimed run resumes after the last committed batch:

- **search** refreshes a batch of Works through
  :func:`~backend.domain.search.refresh.refresh_work_search`, the one write
  path of ``derived.work_search`` (§8.3). A Work created during the rebuild
  gets its row from its own create command.
- **text** keeps the extracted text. One ``UPDATE`` per batch recomputes
  ``asset_text.doc`` from the same row's stored ``text`` (a concurrent
  ``text.extract`` that rewrites the row is re-read before the update
  applies). An Asset without a row for its current bytes and extractor
  version is extracted again through the ``text.extract`` write path
  (:func:`~backend.domain.search.text.extract_asset`), and the cursor moves
  past the batch only after those extractions. An unreadable PDF is an
  outcome (status ``failed``); an unavailable extractor or a database error
  fails the run, which then resumes from the stored cursor.
- **research** runs the ``research.reindex_work`` transaction
  (:func:`~backend.domain.research.reindex.reindex_work`) for each Work, one
  transaction per Work, which also stores the cursor. A Work whose reindex
  fails fails the run.

Each scope ends with one transaction that deletes the rows whose Work or
Asset no longer exists (or, for text, whose bytes are no longer the
Asset's) and marks the scope done. None of the write paths recreates such a
row afterwards: a refresh or reindex never writes for a gone Work, and
``text.extract`` never writes for a gone Asset or for bytes it no longer
holds (§8.3, §8.4). Every batch recomputes from the canonical rows current
when it runs, so a rebuild is idempotent: run twice, or killed and run
again, it leaves only rows equal to a fresh recompute.

Jobs use ``dedupe_key = <library_id>:<sorted scopes>`` and
``singleton_key = derived:<library_id>``, so a queued rebuild of the same
scopes coalesces and two rebuilds of one Library never run at once.

**Repair.** :func:`consistency` is the cheap count of §8.6 (Works without a
``work_search`` row, rows without a Work). :func:`schedule_repair` enqueues
``search.repair`` when either is non-zero; the job refreshes the Works that
have no row, in batches through the same refresh path, and deletes rows
whose Work is gone.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, LiteralString, Optional

from backend.db.database import Database, Transaction
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.research import reindex
from backend.domain.search import refresh
from backend.domain.search import text as search_text
from backend.jobs import queue
from backend.jobs.handlers import HandlerRegistry
from backend.jobs.runner import JobRun
from backend.storage.objects import StorageBackend

KIND = "derived.rebuild"
REPAIR_KIND = "search.repair"
# The order a run works through its scopes.
SCOPES = ("search", "text", "research")
BATCH = 200


def scope_key(scopes: Iterable[str]) -> list[str]:
    """The scopes as a job stores them: known, unique and sorted. Refuses an empty or unknown set."""
    chosen = sorted(set(scopes))
    unknown = [name for name in chosen if name not in SCOPES]
    if unknown or not chosen:
        raise ValueError("a rebuild scope is one or more of search, text and research")
    return chosen


def enqueue(scope: LibraryScope, scopes: Iterable[str] = SCOPES) -> Optional[uuid.UUID]:
    """Enqueue the Library's rebuild of ``scopes``; None when the same rebuild is already queued."""
    chosen = scope_key(scopes)
    return queue.enqueue_library_job(
        scope, KIND, {"scopes": chosen},
        dedupe_key=f"{scope.library_id}:{','.join(chosen)}",
        singleton_key=f"derived:{scope.library_id}")


# -- search --------------------------------------------------------------------------------

_WORKS_AFTER: LiteralString = """
SELECT id FROM prks.works
WHERE library_id = %(library_id)s AND (%(after)s::text IS NULL OR id COLLATE "C" > %(after)s::text)
ORDER BY id COLLATE "C"
LIMIT %(batch)s
"""
_SEARCH_ORPHANS: LiteralString = """
DELETE FROM derived.work_search s
WHERE s.library_id = %(library_id)s
  AND NOT EXISTS (SELECT 1 FROM prks.works w WHERE w.library_id = s.library_id AND w.id = s.work_id)
"""

# -- text ----------------------------------------------------------------------------------

# The managed Assets after the cursor, and whether each lacks this
# extractor's row for its current bytes.
_ASSETS_AFTER: LiteralString = """
SELECT a.id,
       NOT EXISTS (SELECT 1 FROM derived.asset_text t
                   WHERE t.library_id = a.library_id AND t.asset_id = a.id
                     AND t.content_sha256 = a.content_sha256
                     AND t.extractor_version = %(version)s) AS extract
FROM prks.assets a
WHERE a.library_id = %(library_id)s AND a.kind = 'managed_file'
  AND a.storage_locator IS NOT NULL AND a.content_sha256 IS NOT NULL
  AND (%(after)s::text IS NULL OR a.id COLLATE "C" > %(after)s::text)
ORDER BY a.id COLLATE "C"
LIMIT %(batch)s
"""
_REFOLD: LiteralString = """
UPDATE derived.asset_text
SET doc = strip(to_tsvector('prks.prks_search'::regconfig, prks.search_fold(text))), updated_at = now()
WHERE library_id = %(library_id)s AND asset_id = ANY(%(asset_ids)s::text[])
"""
_TEXT_ORPHANS: LiteralString = """
DELETE FROM derived.asset_text t
WHERE t.library_id = %(library_id)s
  AND NOT EXISTS (SELECT 1 FROM prks.assets a
                  WHERE a.library_id = t.library_id AND a.id = t.asset_id
                    AND a.content_sha256 = t.content_sha256)
"""

# -- research ------------------------------------------------------------------------------

_RESEARCH_ORPHANS: tuple[LiteralString, ...] = tuple(
    f"""
DELETE FROM derived.{table} d
WHERE d.library_id = %(library_id)s
  AND NOT EXISTS (SELECT 1 FROM prks.works w WHERE w.library_id = d.library_id AND w.id = d.work_id)
"""
    for table in ("concept_mentions", "argument_mentions", "work_note_state"))


@dataclass
class _Progress:
    """The payload a run keeps current: its scopes, the ones done, and the cursor of the next."""

    scopes: list[str]
    done: list[str]
    cursor: Optional[str]

    @classmethod
    def of(cls, payload: Any) -> _Progress:
        scopes = scope_key(payload.get("scopes") or ())
        done = [name for name in payload.get("done") or () if name in scopes]
        cursor = payload.get("cursor")
        return cls(scopes, done, cursor if isinstance(cursor, str) and cursor else None)

    def payload(self) -> dict[str, Any]:
        return {"scopes": self.scopes, "done": self.done, "cursor": self.cursor}

    def pending(self) -> list[str]:
        return [name for name in SCOPES if name in self.scopes and name not in self.done]


class Rebuild:
    """The ``derived.rebuild`` handler of one worker, bound to its data root's storage (for text)."""

    def __init__(self, storage: StorageBackend,
                 extractor: search_text.Extractor = search_text.extract_pdf) -> None:
        self.storage = storage
        self.extractor = extractor

    def handle(self, run: JobRun) -> None:
        progress = _Progress.of(run.payload)
        for name in progress.pending():
            walk = {"search": self._search, "text": self._text, "research": self._research}[name]
            walk(run, progress)
            self._finish(run, progress, name)

    # Each walk returns once its last batch is committed; ``progress.cursor``
    # is the cursor stored with that batch.

    def _search(self, run: JobRun, progress: _Progress) -> None:
        while True:
            def batch(scope: LibraryScope) -> Optional[str]:
                ids = _ids_after(scope, _WORKS_AFTER, progress.cursor)
                if not ids:
                    return None
                refresh.refresh_work_search(scope, ids)
                _save(scope, run, progress, ids[-1])
                return ids[-1]

            if self._step(run, progress, "search", batch) is None:
                return

    def _text(self, run: JobRun, progress: _Progress) -> None:
        while True:
            def batch(scope: LibraryScope) -> list[dict[str, Any]]:
                rows = [dict(row) for row in scope.execute(_ASSETS_AFTER, {
                    "after": progress.cursor, "batch": BATCH,
                    "version": search_text.EXTRACTOR_VERSION}).fetchall()]
                if rows:
                    scope.execute(_REFOLD, {"asset_ids": [str(row["id"]) for row in rows]})
                return rows

            run.check()
            rows = run.library_command(Capability.EDIT_LIBRARY, batch)
            if not rows:
                return
            for row in rows:
                if row["extract"]:
                    run.check()
                    search_text.extract_asset(run, self.storage, str(row["id"]), self.extractor)
            last = str(rows[-1]["id"])

            def save(scope: LibraryScope, last: str = last) -> None:
                _save(scope, run, progress, last)

            run.library_command(Capability.EDIT_LIBRARY, save)
            run.heartbeat({"scope": "text", "cursor": last})

    def _research(self, run: JobRun, progress: _Progress) -> None:
        while True:
            run.check()
            ids = run.library_command(Capability.EDIT_LIBRARY,
                                      lambda scope: _ids_after(scope, _WORKS_AFTER, progress.cursor))
            if not ids:
                return
            for work_id in ids:
                def one(scope: LibraryScope, work_id: str = work_id) -> None:
                    reindex.reindex_work(scope, work_id)
                    _save(scope, run, progress, work_id)

                run.check()
                run.library_command(Capability.EDIT_LIBRARY, one)
            run.heartbeat({"scope": "research", "cursor": progress.cursor})

    def _step(self, run: JobRun, progress: _Progress, name: str, batch: Any) -> Optional[str]:
        run.check()
        last = run.library_command(Capability.EDIT_LIBRARY, batch)
        if last is not None:
            run.heartbeat({"scope": name, "cursor": last})
        return last

    def _finish(self, run: JobRun, progress: _Progress, name: str) -> None:
        statements: tuple[LiteralString, ...] = {
            "search": (_SEARCH_ORPHANS,), "text": (_TEXT_ORPHANS,), "research": _RESEARCH_ORPHANS}[name]

        def finish(scope: LibraryScope) -> None:
            for statement in statements:
                scope.execute(statement)
            done = _Progress(progress.scopes, [*progress.done, name], None)
            queue.save_library_job_payload(scope, run.job, done.payload())

        run.check()
        run.library_command(Capability.EDIT_LIBRARY, finish)
        progress.done.append(name)
        progress.cursor = None
        run.heartbeat({"scope": name, "done": True})


def _ids_after(scope: LibraryScope, statement: LiteralString, after: Optional[str]) -> list[str]:
    return [str(row["id"]) for row in scope.execute(statement, {"after": after, "batch": BATCH}).fetchall()]


def _save(scope: LibraryScope, run: JobRun, progress: _Progress, cursor: str) -> None:
    """Store ``cursor`` with the batch in ``scope``'s transaction; the local copy follows it."""
    queue.save_library_job_payload(scope, run.job, _Progress(progress.scopes, progress.done, cursor).payload())
    progress.cursor = cursor


# -- repair --------------------------------------------------------------------------------

_COUNT: LiteralString = """
SELECT
  (SELECT count(*) FROM prks.works w
   WHERE w.library_id = %(library_id)s
     AND NOT EXISTS (SELECT 1 FROM derived.work_search s
                     WHERE s.library_id = w.library_id AND s.work_id = w.id)) AS missing,
  (SELECT count(*) FROM derived.work_search s
   WHERE s.library_id = %(library_id)s
     AND NOT EXISTS (SELECT 1 FROM prks.works w
                     WHERE w.library_id = s.library_id AND w.id = s.work_id)) AS orphaned
"""
_MISSING: LiteralString = """
SELECT w.id FROM prks.works w
WHERE w.library_id = %(library_id)s
  AND NOT EXISTS (SELECT 1 FROM derived.work_search s WHERE s.library_id = w.library_id AND s.work_id = w.id)
ORDER BY w.id COLLATE "C"
LIMIT %(batch)s
"""


@dataclass(frozen=True)
class Consistency:
    missing: int  # Works without a work_search row
    orphaned: int  # work_search rows without a Work

    @property
    def consistent(self) -> bool:
        return not (self.missing or self.orphaned)


def consistency(scope: LibraryScope) -> Consistency:
    """The cheap §8.6 count: never a full verification."""
    row = scope.execute(_COUNT).fetchone()
    assert row is not None
    return Consistency(int(row["missing"]), int(row["orphaned"]))


def schedule_repair(scope: LibraryScope) -> Optional[uuid.UUID]:
    """Enqueue ``search.repair`` when :func:`consistency` finds a gap; None when there is none or one is queued."""
    if consistency(scope).consistent:
        return None
    return queue.enqueue_library_job(scope, REPAIR_KIND, {}, dedupe_key=scope.library_id)


def repair(run: JobRun) -> None:
    """The ``search.repair`` job: rows for the Works that have none, then no rows without a Work."""
    def batch(scope: LibraryScope) -> int:
        ids = [str(row["id"]) for row in scope.execute(_MISSING, {"batch": BATCH}).fetchall()]
        refresh.refresh_work_search(scope, ids)
        return len(ids)

    while True:
        run.check()
        if run.library_command(Capability.EDIT_LIBRARY, batch) < BATCH:
            break
        run.heartbeat()
    run.check()
    run.library_command(Capability.EDIT_LIBRARY, lambda scope: scope.execute(_SEARCH_ORPHANS))


# -- host maintenance: the admin command (§8.6) ----------------------------------------------

# These run for no actor (the host admin CLI). Each Library is handled in one
# host transaction that locks its row, skips it while in maintenance (a
# restore queues its own rebuild), and enqueues with the epoch it read under
# that lock (``queue.enqueue_library_job_for_host``).

_LIBRARY_IDS: LiteralString = "SELECT id FROM prks.libraries ORDER BY id"
_LOCK_LIBRARY: LiteralString = """
SELECT epoch, maintenance IS NOT NULL AS in_maintenance
FROM prks.libraries WHERE id = %(library_id)s
FOR NO KEY UPDATE
"""


def _host_enqueue(tx: Transaction, library_id: str, epoch: int, scopes: Iterable[str]) -> Optional[uuid.UUID]:
    chosen = scope_key(scopes)
    return queue.enqueue_library_job_for_host(
        tx, library_id, epoch, KIND, {"scopes": chosen},
        dedupe_key=f"{library_id}:{','.join(chosen)}", singleton_key=f"derived:{library_id}")


def _for_library(db: Database, library_id: str,
                 fn: Callable[[Transaction, int], Optional[uuid.UUID]]) -> Optional[uuid.UUID]:
    """Run ``fn(tx, epoch)`` under the Library row lock; None for a gone Library or one in maintenance."""
    def run(tx: Transaction) -> Optional[uuid.UUID]:
        row = tx.execute(_LOCK_LIBRARY, {"library_id": library_id}).fetchone()
        if row is None or row["in_maintenance"]:
            return None
        return fn(tx, int(row["epoch"]))

    return db.command(run)


def library_ids(db: Database) -> list[str]:
    return [str(row["id"]) for row in db.query(lambda tx: tx.execute(_LIBRARY_IDS).fetchall())]


def request_rebuild(db: Database, library_id: str, scopes: Iterable[str] = SCOPES) -> Optional[uuid.UUID]:
    """``prks admin rebuild-derived``: queue the Library's rebuild; None when it is already queued or unavailable."""
    return _for_library(db, library_id, lambda tx, epoch: _host_enqueue(tx, library_id, epoch, scopes))


def attach(registry: HandlerRegistry, storage: StorageBackend) -> Rebuild:
    """Register ``derived.rebuild`` and ``search.repair`` on a worker's own registry."""
    rebuild = Rebuild(storage)
    registry.register(KIND, rebuild.handle)
    registry.register(REPAIR_KIND, repair)
    return rebuild
