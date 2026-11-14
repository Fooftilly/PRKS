"""The PDF text index of the PostgreSQL transition facade (L-S-1 #637).

``server.py`` kept PDF text current through the bound ``PRKSTextIndex``
(``backend/text_index.py``, a separate SQLite FTS5 file), not through
``PRKSDatabase``. :class:`PostgresTextIndex` stands where that index object
is used, with the ``PRKSTextIndex`` names and signatures the routes call, as
:class:`~backend.pg_facade.research_index.PostgresResearchIndex` does for the
Research index. ``/api/search``'s any-mode PDF text hits no longer come
through it: since PG-C3 (#642) the route reads
:func:`backend.domain.search.text.pdf_text_work_ids` in its own snapshot
(:mod:`backend.domain.search.search_commands`).

- The text is kept by the database, never by its reader: every command
  that gives a managed PDF new bytes queues ``text.extract`` in its own
  transaction, and a Work delete drops its rows (§8.3). So
  :meth:`sync_work` and :meth:`remove_work` do nothing.
- :meth:`reconcile_all` (``POST /api/works/reindex-pdf-text``) queues the
  Library's ``text`` rebuild (§8.6) instead of extracting in the request;
  its counters are zero, because the work happens later in the worker, and
  ``queued`` says whether a rebuild was queued (False: one already is).
  ``force`` is accepted: a text rebuild re-extracts only an Asset whose
  current bytes and extractor version have no row, and recomputes every
  stored row's ``doc``. The startup reconcile has no counterpart either:
  the worker's search idle task runs the consistency count and the folding
  check (:class:`backend.domain.search.rebuild.SearchMaintenance`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from backend.pg_facade.context import current_context

if TYPE_CHECKING:
    from backend.db.database import Database
    from backend.domain.identity.library_scope import LibraryScope
    from backend.pdf_text import TextIndexSyncResult


class PostgresTextIndex:
    """``PRKSTextIndex``'s maintenance calls for the bound Library."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def sync_work(self, work_id: str, file_path: str, **_options: Any) -> TextIndexSyncResult:
        """Nothing to do: the command that changed the PDF queued ``text.extract`` in its transaction."""
        from backend.pdf_text import TextIndexSyncResult

        return TextIndexSyncResult(action="queued")

    def remove_work(self, work_id: str) -> int:
        """Nothing to do: the Work delete dropped its Assets' text rows in its transaction."""
        return 0

    def reconcile_all(self, db: Any = None, *, force: bool = False) -> Dict[str, Any]:
        """Queue the bound Library's ``text`` rebuild; zero counters and ``queued``."""
        from backend.db.database import Isolation
        from backend.domain.identity.authorization import Capability
        from backend.domain.identity.library_scope import library_command
        from backend.domain.search import rebuild
        from backend.pdf_text import _empty_summary

        def enqueue(scope: LibraryScope) -> bool:
            return rebuild.enqueue(scope, ["text"]) is not None

        queued = library_command(self._database, current_context(), Capability.EDIT_LIBRARY, enqueue,
                                 isolation=Isolation.READ_COMMITTED)
        return {**_empty_summary(), "queued": queued}
