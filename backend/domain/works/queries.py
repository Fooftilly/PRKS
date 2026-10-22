"""Work reads on PostgreSQL (L-W-1 #628): the rows behind today's Work JSON.

Each function runs inside one :func:`~backend.domain.identity.library_scope.library_query`
snapshot (the caller's), so a list and its counts come from one consistent
read, and returns :func:`~backend.domain.works.projection.work_core` dicts
that :mod:`backend.domain.works.projection` shapes per endpoint. Every
statement filters by the scope's Library.
"""

from __future__ import annotations

from typing import Any, LiteralString, Optional

from backend.domain.identity.library_scope import LibraryScope
from backend.domain.people import roles
from backend.domain.works import projection

_RECENT_ETAG_SQL: LiteralString = """
SELECT count(*) AS c,
       COALESCE(to_char(max(last_opened_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.MS'), '') AS m
FROM prks.works WHERE library_id = %(library_id)s AND last_opened_at IS NOT NULL
"""
_ADDED_ETAG_SQL: LiteralString = """
SELECT count(*) AS c,
       COALESCE(to_char(max(created_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'), '') AS m
FROM prks.works WHERE library_id = %(library_id)s
"""


def _cores(scope: LibraryScope, query: LiteralString,
           params: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    cores = [projection.work_core(row) for row in scope.execute(query, params).fetchall()]
    # The People lane's part of the projection, read in the same snapshot.
    roles.attach_credits(scope, cores)
    return cores


def work(scope: LibraryScope, work_id: str) -> Optional[dict[str, Any]]:
    rows = _cores(scope, projection.WORK_ROWS_SQL + projection.BY_ID_SQL, {"work_id": work_id})
    return rows[0] if rows else None


def works_by_created(scope: LibraryScope, limit: Optional[int] = None) -> list[dict[str, Any]]:
    """Newest first (``get_all_works``, Recently Added)."""
    if limit is None:
        return _cores(scope, projection.WORK_ROWS_SQL + projection.BY_CREATED_SQL)
    return _cores(scope, projection.WORK_ROWS_SQL + projection.BY_CREATED_SQL + projection.LIMIT_SQL,
                  {"limit": _limit(limit)})


def works_by_title(scope: LibraryScope) -> list[dict[str, Any]]:
    """The browse catalog order: effective title, then id."""
    return _cores(scope, projection.WORK_ROWS_SQL + projection.BY_TITLE_SQL)


def works_by_opened(scope: LibraryScope, limit: int) -> list[dict[str, Any]]:
    """Opened Works, most recently opened first (Recent)."""
    return _cores(scope, projection.WORK_ROWS_SQL + projection.BY_OPENED_SQL + projection.LIMIT_SQL,
                  {"limit": _limit(limit)})


def works_by_ids(scope: LibraryScope, work_ids: list[str]) -> dict[str, dict[str, Any]]:
    """The named Works of this Library, by id (absent ids are left out)."""
    if not work_ids:
        return {}
    rows = _cores(scope, projection.WORK_ROWS_SQL + projection.BY_IDS_SQL, {"work_ids": work_ids})
    return {row["id"]: row for row in rows}


def works_in_folder(scope: LibraryScope, folder_id: str) -> list[dict[str, Any]]:
    """The Works filed in a Folder, newest first (``get_folder``'s ``works``)."""
    return _cores(scope, projection.WORK_ROWS_SQL + projection.IN_FOLDER_SQL + projection.BY_CREATED_SQL,
                  {"folder_id": folder_id})


def works_in_playlist(scope: LibraryScope, playlist_id: str) -> list[dict[str, Any]]:
    """A Playlist's Works in its order (``get_playlist``'s ``items``)."""
    return _cores(scope, projection.WORK_ROWS_SQL + projection.IN_PLAYLIST_SQL, {"playlist_id": playlist_id})


def recent_etag_parts(scope: LibraryScope) -> tuple[int, str]:
    return _etag_parts(scope, _RECENT_ETAG_SQL)


def recently_added_etag_parts(scope: LibraryScope) -> tuple[int, str]:
    return _etag_parts(scope, _ADDED_ETAG_SQL)


def _etag_parts(scope: LibraryScope, query: LiteralString) -> tuple[int, str]:
    row = scope.execute(query).fetchone()
    if row is None:
        return 0, ""
    return int(row["c"]), str(row["m"])


def _limit(limit: int) -> Optional[int]:
    # SQLite reads a negative LIMIT as "no limit"; PostgreSQL refuses one.
    return None if limit < 0 else limit
