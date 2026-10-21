"""``CREATE_FOLDER``, ``SET_FOLDER_FIELD``, ``SET_WORK_FOLDER`` and ``DELETE_FOLDER`` on PostgreSQL (L-O-1 #633).

Validated by ``backend/folder_sync.py``'s rules and decided by
:mod:`backend.domain.organization.folders`, result for result.

Creation is construction: the id is client-minted, a create of an id that
already exists acknowledges the stored Folder (``changed: false``), and a
title another Folder under the same parent holds is ``TITLE_TAKEN``, decided
by the unique index, so two concurrent creates of one title make one
Folder. An id of another Library is refused unledgered, as for a Work.

A rename or move that loses a race for a title fails on the index, which
aborts its transaction; the family names that index (``retry_constraints``),
so it runs once more and ledgers ``TITLE_TAKEN`` from what it then reads.

Isolation (§7.1): ``folder_files`` is a peer table of the Work merge, so
``SET_WORK_FOLDER`` runs SERIALIZABLE; the Folder's own commands update or
delete Folder rows behind their row locks and run READ COMMITTED.

:class:`FolderFiling` is ``CREATE_WORK``'s filing
(:class:`~backend.sync.families.WorkFiling`): the Folder the new Work goes
into, checked before the Work exists (``FOLDER_NOT_FOUND``) and by default
the root "Uncategorized", then the roles through the People lane's
:class:`~backend.sync.handlers.work_role.RoleFiling`. Filing is construction:
no ``work-folder`` revision moves. A playlist in the payload is the Playlist
slice's and refuses with ``NotPortedError``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend import folder_sync as rules
from backend.db.database import Isolation
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.organization import folders
from backend.entity_ids import generate
from backend.pg_facade.registry import NotPortedError
from backend.sync.families import Environment, FamilyHandler, Result
from backend.sync.handlers.work_role import RoleFiling


def apply_create(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                 env: Environment) -> Result:
    del received_at, env
    try:
        return folders.apply_create(scope, op["entity_id"], op["payload"])
    except folders.ForeignFolderId:
        # Ids are unique across Libraries: refused, and not ledgered.
        raise AccessRefused(Refusal.FORBIDDEN) from None


def apply_field(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                env: Environment) -> Result:
    del received_at, env
    payload = op["payload"]
    return folders.apply_field_operation(scope, op["entity_id"], payload["field"], payload["value"],
                                         op["base_revision"])


def apply_work_folder(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                      env: Environment) -> Result:
    del received_at, env
    return folders.apply_work_folder_operation(scope, op["entity_id"], op["payload"]["folder_id"],
                                               op["base_revision"])


def apply_delete(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                 env: Environment) -> Result:
    del received_at, env
    folder_id = op["entity_id"]
    deleted, refusal = folders.delete(scope, folder_id)
    if refusal:
        return 409, {"code": refusal, "folder_id": folder_id}
    # Deleting a Folder that is already gone is convergence.
    return 200, {"code": "ACKNOWLEDGED", "folder_id": folder_id, "changed": deleted}


class FolderFiling:
    """``CREATE_WORK``'s filing: its Folder (this lane), then its roles (People).

    ``prepare`` resolves the Folder SQLite would: the payload's, refused with
    ``FOLDER_NOT_FOUND`` when it is not this Library's, or the root
    "Uncategorized", created in this transaction when there is none (as on
    SQLite, also when the create is then refused or converges). The named
    Folder is held ``FOR KEY SHARE`` until COMMIT, so it cannot be deleted
    before ``file`` files the Work into it.
    """

    def __init__(self) -> None:
        self.roles = RoleFiling()

    def prepare(self, scope: LibraryScope, work_id: str,
                fields: dict[str, Any]) -> dict[str, Any] | Result:
        folder_id = (fields["folder_id"] or "").strip()
        if not folder_id:
            folder_id = folders.ensure_uncategorized(scope, generate("F"))
        elif not folders.share(scope, folder_id):
            return 404, {"code": "FOLDER_NOT_FOUND", "work_id": work_id}
        playlist_id = (fields["playlist_id"] or "").strip()
        if playlist_id:
            raise NotPortedError("CREATE_WORK filing in a playlist")
        people = self.roles.prepare(scope, work_id, fields)
        if isinstance(people, tuple):
            return people
        return {"folder_id": folder_id, "playlist_id": playlist_id, **people}

    def file(self, scope: LibraryScope, work_id: str, fields: dict[str, Any],
             prepared: dict[str, Any]) -> dict[str, Any]:
        folders.file_new_work(scope, work_id, prepared["folder_id"])
        return self.roles.file(scope, work_id, fields, prepared)


CREATE_HANDLER = FamilyHandler(rules.validate_create, apply_create)
FIELD_HANDLER = FamilyHandler(rules.validate_field, apply_field,
                              retry_constraints=frozenset({folders.TITLE_KEY}))
WORK_FOLDER_HANDLER = FamilyHandler(rules.validate_work_folder, apply_work_folder,
                                    isolation=Isolation.SERIALIZABLE)
DELETE_HANDLER = FamilyHandler(rules.validate_delete, apply_delete)
