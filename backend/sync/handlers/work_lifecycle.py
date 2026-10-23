"""``CREATE_WORK`` and ``DELETE_WORK`` on PostgreSQL (L-W-1 #628).

The SQLite family's rules (``backend/work_lifecycle_sync.py``), imported:
construction is a video only, its payload is validated and canonicalized by
``canonical_create_payload``, and its source by ``_canonical_new_source``,
the boundary ``add_work`` and ``SET_WORK_SOURCE`` share. A create of an id
that already exists is a convergent acknowledgement (``changed: false``);
the id is client-minted, so a retry with a new ``op_id`` cannot make a
second Work. Construction runs SERIALIZABLE (§7.1), so two creates of one
id serialize instead of both inserting.

Filing the new Work (its folder, by default the root "Uncategorized"; its
playlist; its roles) writes Organization and People rows, so it goes
through :class:`~backend.sync.families.WorkFiling`, which those lanes
implement. The checks and the result fields are SQLite's: a missing folder,
playlist or person refuses the create before the Work exists.

Deletion carries no base, removes the Work level by level and converges on
absence, through
:func:`backend.domain.works.commands.delete_work`, the command behind
``delete_work_record``. As on SQLite, the first acknowledgement carries the
managed file's path for the caller's cleanup and the ledger keeps only
``code``, ``work_id`` and ``changed``, never a path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, LiteralString, Optional

from backend import work_lifecycle_sync as rules
from backend.db.database import Isolation
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.works import commands
from backend.sync.families import Environment, FamilyHandler, Result

_EXISTS: LiteralString = "SELECT 1 FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s"


def _text(value: Optional[str]) -> Optional[str]:
    return (value or "").strip() or None


def new_work(work_id: str, fields: dict[str, Any], identity: dict[str, Any]) -> commands.NewWork:
    """The Work ``insert_work_on_conn`` writes, as Work, Manifestation and stream Asset."""
    from backend.entity_ids import generate

    return commands.NewWork(
        work_id=work_id,
        manifestation_id=generate("MF"),
        title=(fields["title"] or "").strip() or "Untitled",
        status=fields["status"],
        abstract=fields["abstract"] or None,
        author_text=fields["author_text"] or None,
        bibliographic={
            "doc_type": fields["doc_type"],
            "year": _text(fields["year"]),
            "published_date": _text(fields["published_date"]),
            "urldate": _text(fields["urldate"]),
        },
        asset_id=generate("AS"),
        asset=commands.NewAsset(
            source=commands.Stream(url=identity["source_url"], provider=identity["provider"],
                                   provider_id=identity["provider_id"]),
            thumb_url=_text(fields["thumb_url"]),
        ),
        private_note=_text(fields["private_notes"]),
    )


def apply_create(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                 env: Environment) -> Result:
    from backend.db_manager import _canonical_new_source

    del received_at
    work_id = op["entity_id"]
    fields = rules.canonical_create_payload(op["payload"])
    kind, url, provider, provider_id = _canonical_new_source(
        "video", fields["_identity"]["source_url"], "", "", "")
    identity = {"source_kind": kind, "source_url": url, "provider": provider,
                "provider_id": provider_id}
    existing = scope.execute(_EXISTS, {"work_id": work_id}).fetchone() is not None
    prepared = env.filing.prepare(scope, work_id, fields)
    if isinstance(prepared, tuple):
        return prepared
    extra: dict[str, Any] = {}
    created = False
    if not existing:
        created = commands.create_work(scope, new_work(work_id, fields, identity),
                                       if_absent=True) is not None
        if not created:
            # Ids are unique across Libraries: this one names a Work of a
            # Library the actor cannot see. Refused, and not ledgered.
            raise AccessRefused(Refusal.FORBIDDEN)
        extra = env.filing.file(scope, work_id, fields, prepared)
    return 200, {"code": "ACKNOWLEDGED", "work_id": work_id, "changed": created,
                 **prepared, **extra}


def apply_delete(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                 env: Environment) -> Result:
    del received_at, env
    work_id = op["entity_id"]
    deleted = commands.delete_work(scope, work_id)
    result: dict[str, Any] = {"code": "ACKNOWLEDGED", "work_id": work_id,
                              "changed": deleted is not None}
    if deleted is not None and deleted.file_path:
        result["file_path"] = deleted.file_path  # for the first post-commit cleanup only
    return 200, result


def ledgered_delete(op: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    del op
    return {"code": result["code"], "work_id": result["work_id"], "changed": result["changed"]}


CREATE_HANDLER = FamilyHandler(rules.validate_create, apply_create, isolation=Isolation.SERIALIZABLE)
DELETE_HANDLER = FamilyHandler(rules.validate_delete, apply_delete, ledger_fn=ledgered_delete)
