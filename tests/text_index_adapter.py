"""``PRKSTextIndex``'s maintenance calls over the PostgreSQL Library, for tests (L-S-1 #637; PG-C3 #642).

The served PDF text rebuild route calls
:func:`backend.domain.search.search_commands.queue_text_rebuild` with the
admitted context. :class:`PostgresTextIndex` keeps the SQLite
``PRKSTextIndex`` names and signatures the recorded parity tests replay, for
the context bound with :mod:`tests.acting_context`. Until PG-C3 retired the
facade package it was ``backend/pg_facade/text_index.py``, the index object
``server.py`` held.

- The text is kept by the database, never by its reader: every command
  that gives a managed PDF new bytes queues ``text.extract`` in its own
  transaction, and a Work delete drops its rows (§8.3). So
  :meth:`sync_work` and :meth:`remove_work` do nothing.
- :meth:`reconcile_all` queues the Library's ``text`` rebuild (§8.6), as
  the route does; ``force`` is accepted and asks for nothing more.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from tests.acting_context import current_context

if TYPE_CHECKING:
    from backend.db.database import Database
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
        from backend.domain.search import search_commands

        return search_commands.queue_text_rebuild(self._database, current_context())
