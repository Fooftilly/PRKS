"""The Playlist operations for one actor: what ``backend/api/playlists.py`` calls (PG-C3 #642).

Playlists, their fields, their Works and order, and their revision states.
A read runs in one :func:`~backend.domain.identity.library_scope.library_query`
snapshot, authorized for ``READ_LIBRARY`` (reads are not fenced by epoch); a
write is one :func:`~backend.domain.identity.library_scope.library_command`
transaction needing ``EDIT_LIBRARY``, fenced by maintenance and the epoch.
Putting a Work in a Playlist runs SERIALIZABLE, as §7.1 lists filing a Work
(``docs/postgresql-architecture.md``); the rest run READ COMMITTED.
Refusals are ``ValueError`` with SQLite's message, and ``AccessRefused``.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

from backend import playlist_sync
from backend.db.database import Database, Isolation
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.organization import playlists
from backend.domain.works import projection, queries
from backend.domain.works.rules import finish_work_summary_rows
from backend.entity_ids import generate

T = TypeVar("T")

ID_PREFIX = "PL"


def _command(db: Database, ctx: RequestContext, fn: Callable[[LibraryScope], T], *,
             serializable: bool = False) -> T:
    isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
    return library_command(db, ctx, Capability.EDIT_LIBRARY, fn, isolation=isolation)


# -- reads --------------------------------------------------------------------------

def catalog(db: Database, ctx: RequestContext) -> list[dict[str, Any]]:
    """Every Playlist: its columns plus ``item_count``."""
    return library_query(db, ctx, playlists.catalog)


def detail(db: Database, ctx: RequestContext, playlist_id: str, pdfs_dir: str) -> Optional[dict[str, Any]]:
    """One Playlist with its Works in order (``items``), or ``None``.

    Each item is the Work summary plus its ``position``; file sizes are read
    from ``pdfs_dir`` after the snapshot, as every Work summary's are.
    """

    def read(scope: LibraryScope) -> Optional[dict[str, Any]]:
        playlist = playlists.playlist_row(scope, playlist_id)
        if playlist is None:
            return None
        playlist["items"] = [{**projection.work_summary(core, with_folder=False),
                              "position": core["playlist_position"]}
                             for core in queries.works_in_playlist(scope, playlist_id)]
        return playlist

    found = library_query(db, ctx, read)
    if found is not None:
        finish_work_summary_rows(found["items"], pdfs_dir)
    return found


def sync_state(db: Database, ctx: RequestContext, playlist_id: str) -> Optional[dict[str, Any]]:
    """The Playlist's field revisions and its order revision, or ``None``."""
    return library_query(db, ctx, lambda scope: playlists.playlist_state(scope, playlist_id))


def work_state(db: Database, ctx: RequestContext, work_id: str) -> Optional[dict[str, Any]]:
    """Which Playlist a Work is in and the revision that says so, or ``None``."""
    return library_query(db, ctx, lambda scope: playlists.work_playlist_state(scope, work_id))


# -- writes -------------------------------------------------------------------------

def create(db: Database, ctx: RequestContext, title: Any, description: Any = "",
           original_url: Any = "") -> str:
    """The new Playlist's id."""
    playlist_id = generate(ID_PREFIX)
    _command(db, ctx, lambda scope: playlists.insert(scope, playlist_id, title, description, original_url))
    return playlist_id


def update(db: Database, ctx: RequestContext, playlist_id: str, fields: dict[str, Any]) -> None:
    """Edit a Playlist's fields through the revision-aware boundary; other keys are ignored."""
    updates = {k: v for k, v in fields.items() if k in playlist_sync.FIELD_SET}
    if not updates:
        return

    def run(scope: LibraryScope) -> None:
        if not playlists.lock(scope, playlist_id):
            raise ValueError("Playlist not found.")
        for field in playlist_sync.FIELDS:
            if field in updates:
                value = updates[field]
                playlists.set_field(scope, playlist_id, field, "" if value in (None, False) else str(value))

    _command(db, ctx, run)


def delete(db: Database, ctx: RequestContext, playlist_id: str) -> None:
    """Destruction: the Playlist's Works are released and its scopes advance."""
    _command(db, ctx, lambda scope: playlists.delete(scope, playlist_id))


def add_item(db: Database, ctx: RequestContext, playlist_id: Optional[str], work_id: Optional[str],
             position: Optional[int] = None) -> None:
    """Put a Work in a Playlist (membership, then the order when ``position`` is given)."""
    pid = (playlist_id or "").strip()
    wid = (work_id or "").strip()
    if not pid or not wid:
        raise ValueError("Playlist or work not found.")

    def run(scope: LibraryScope) -> None:
        if not playlists.lock_work(scope, wid):
            raise ValueError("Playlist or work not found.")
        try:
            playlists.set_work_playlist(scope, wid, pid)
        except playlist_sync.PlaylistRuleError as error:
            raise ValueError("Playlist or work not found.") from error
        if position is None:
            return
        order = [w for w in playlists.order(scope, pid) if w != wid]
        order.insert(max(0, min(int(position), len(order))), wid)
        playlists.set_order(scope, pid, order)

    _command(db, ctx, run, serializable=True)


def remove_item(db: Database, ctx: RequestContext, playlist_id: Optional[str], work_id: Optional[str]) -> None:
    """Take a Work out of the named Playlist; nothing changes when it is not in it."""
    wid = (work_id or "").strip()
    pid = (playlist_id or "").strip()

    def run(scope: LibraryScope) -> None:
        if playlists.lock_work(scope, wid) and playlists.work_playlist(scope, wid) == pid:
            playlists.set_work_playlist(scope, wid, "")

    _command(db, ctx, run)


def reorder(db: Database, ctx: RequestContext, playlist_id: str, work_ids: list[str]) -> None:
    """Rewrite the whole order, through the boundary the aggregate owns."""
    if not work_ids:
        return

    def run(scope: LibraryScope) -> None:
        try:
            playlists.set_order(scope, playlist_id, list(work_ids))
        except playlist_sync.PlaylistRuleError as error:
            raise ValueError("Playlist not found.") from error

    _command(db, ctx, run)
