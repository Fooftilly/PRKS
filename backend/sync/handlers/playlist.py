"""``CREATE_PLAYLIST``, ``SET_PLAYLIST_FIELD``, ``SET_WORK_PLAYLIST``, ``REORDER_PLAYLIST_ITEMS`` and ``DELETE_PLAYLIST`` on PostgreSQL (L-O-1 #633).

Validated by ``backend/playlist_sync.py``'s rules and decided by
:mod:`backend.domain.organization.playlists`, result for result.

Creation is construction: the id is client-minted, and a create of an id
that already exists acknowledges the stored Playlist (``changed: false``).
An id of another Library is refused unledgered, as for a Work.

Isolation (§7.1): ``playlist_items`` is a peer table of the Work merge, so
``SET_WORK_PLAYLIST`` (which adds a Work to a Playlist or moves it) runs
SERIALIZABLE. A field edit, a reorder (which rewrites positions of the rows
it holds) and a delete (which removes rows) run READ COMMITTED behind the
Playlist row lock.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend import playlist_sync as rules
from backend.db.database import Isolation
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.organization import playlists
from backend.sync.families import Environment, FamilyHandler, Result


def apply_create(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                 env: Environment) -> Result:
    del received_at, env
    try:
        return playlists.apply_create(scope, op["entity_id"], op["payload"])
    except playlists.ForeignPlaylistId:
        # Ids are unique across Libraries: refused, and not ledgered.
        raise AccessRefused(Refusal.FORBIDDEN) from None


def apply_field(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                env: Environment) -> Result:
    del received_at, env
    payload = op["payload"]
    return playlists.apply_field_operation(scope, op["entity_id"], payload["field"], payload["value"],
                                           op["base_revision"])


def apply_work_playlist(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                        env: Environment) -> Result:
    del received_at, env
    return playlists.apply_work_playlist_operation(scope, op["entity_id"], op["payload"]["playlist_id"],
                                                   op["base_revision"])


def apply_order(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                env: Environment) -> Result:
    del received_at, env
    return playlists.apply_order_operation(scope, op["entity_id"], op["payload"]["work_ids"],
                                           op["base_revision"])


def apply_delete(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                 env: Environment) -> Result:
    del received_at, env
    playlist_id = op["entity_id"]
    # Deleting a Playlist that is already gone is convergence.
    return 200, {"code": "ACKNOWLEDGED", "playlist_id": playlist_id,
                 "changed": playlists.delete(scope, playlist_id)}


CREATE_HANDLER = FamilyHandler(rules.validate_create, apply_create)
FIELD_HANDLER = FamilyHandler(rules.validate_field, apply_field)
WORK_PLAYLIST_HANDLER = FamilyHandler(rules.validate_work_playlist, apply_work_playlist,
                                      isolation=Isolation.SERIALIZABLE)
ORDER_HANDLER = FamilyHandler(rules.validate_order, apply_order)
DELETE_HANDLER = FamilyHandler(rules.validate_delete, apply_delete)
