"""Child process for the crash-point matrix (tests/pg/test_crash_points.py, #1284).

Runs one PostgreSQL write workflow for a test's Library and ends the process
with ``os._exit`` at the named point, so no ``finally``, rollback or cleanup
of this process runs: the kernel closes its files and the server ends its
session, as for a killed container. The parent then "restarts" (recovery,
the sweep, the cleanup jobs) and checks the invariants.

Usage: python -m tests.pg.crash_point_child < SPEC_JSON

The spec, read from stdin (an upload body does not fit in argv), names ``conninfo``, ``root`` (the data root), ``library_id``,
``user_id``, ``device_id``, ``workflow``, ``kill_at`` and the workflow's own
arguments. The child signs in as the test's user with a session of its own,
so every command is authorized as a browser request is.
"""

from __future__ import annotations

import io
import json
import os
import sys
from typing import Any, Callable, NoReturn
from unittest import mock

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.domain.identity import sessions
from backend.domain.identity.request_context import RequestContext
from backend.storage.objects import LocalFilesystemStorage

KILLED_EXIT = 77
# The child finished without reaching its kill point: the parent fails on it.
NOT_KILLED_EXIT = 3
MAX_BYTES = 10 * 1024 * 1024


def die() -> NoReturn:
    os._exit(KILLED_EXIT)


def then_die(real: Callable[..., Any]) -> Callable[..., Any]:
    """``real``, then the kill: the call's effects happen, its caller never continues."""

    def wrapper(*args: Any, **kwargs: Any) -> NoReturn:
        real(*args, **kwargs)
        die()

    return wrapper


class DyingBody(io.RawIOBase):
    """An upload body that is killed after ``after`` bytes were handed to the store."""

    def __init__(self, data: bytes, after: int) -> None:
        super().__init__()
        self.data, self.after, self.pos = data, after, 0

    def read(self, size: int = -1) -> bytes:
        if self.pos >= self.after:
            die()
        end = min(len(self.data), self.after, self.pos + (size if size >= 0 else len(self.data)))
        chunk, self.pos = self.data[self.pos:end], end
        return chunk


def context(db: Database, spec: dict[str, Any]) -> RequestContext:
    new = db.command(lambda tx: sessions.create_session(tx, user_id=spec["user_id"], library_id=spec["library_id"]))
    valid = sessions.resolve_session(db, new.token.reveal())
    if valid is None:  # pragma: no cover - the session was just created
        raise RuntimeError("the child's session does not resolve")
    return RequestContext.for_session(valid, epoch=1, device_id=spec["device_id"])


def upload(db: Database, ctx: RequestContext, storage: LocalFilesystemStorage, spec: dict[str, Any]) -> None:
    from backend.domain.processing import commands, uploads

    data = spec["data"].encode("latin-1")
    body: Any = io.BytesIO(data)
    kill_at = spec["kill_at"]
    if kill_at == "mid_write":
        body = DyingBody(data, len(data) // 2)
    patches = [mock.patch.object(uploads, "store", then_die(uploads.store))] if kill_at == "stored" else []
    for patch in patches:
        patch.start()
    commands.upload(db, ctx, storage, upload_key=spec["upload_key"], filename="paper.pdf", body=body,
                    content_length=len(data), max_bytes=MAX_BYTES, item_id=spec.get("item_id"))
    if kill_at == "committed":
        die()  # committed; the answer never reaches the client


def import_(db: Database, ctx: RequestContext, storage: LocalFilesystemStorage, spec: dict[str, Any]) -> None:
    from backend.domain.organization import folders
    from backend.domain.processing import commands

    if spec["kill_at"] == "mid_transaction":
        # The Work, roles and tags are written; the filing and the item's mark are not, nor the commit.
        mock.patch.object(folders, "file_new_work", lambda *_args, **_kwargs: die()).start()
    commands.import_one(db, ctx, storage, spec["item_id"])
    if spec["kill_at"] == "committed":
        die()


def replace(db: Database, ctx: RequestContext, storage: LocalFilesystemStorage, spec: dict[str, Any]) -> None:
    from backend.domain.annotations import annotation_commands
    from backend.domain.objects import ingest, lifecycle, replace as replace_module

    data = spec["data"].encode("latin-1")
    if spec["kill_at"] == "stored":
        mock.patch.object(annotation_commands.ingest, "store_pdf", then_die(ingest.store_pdf)).start()
    elif spec["kill_at"] == "adopted":
        # Inside the command: the new keys are adopted, the Asset not yet switched, nothing committed.
        real_adopt = lifecycle.adopt
        mock.patch.object(replace_module.lifecycle, "adopt", then_die(real_adopt)).start()
    annotation_commands.replace_pdf(db, ctx, storage, spec["work_id"], lambda sink: sink.write(data))
    if spec["kill_at"] == "committed":
        die()


def backup(spec: dict[str, Any], storage: LocalFilesystemStorage) -> None:
    from backend.domain.maintenance import backup as backup_module

    hooks: dict[str, Callable[[], None]] = {backup_module.PHASE_OBJECTS: die} if spec["kill_at"] == "objects" else {}
    backup_module.write_backup(spec["conninfo"], storage, spec["library_id"], spec["dest_dir"],
                               prks_version="0.8.0-test", hooks=hooks)


def settle(db: Database, storage: LocalFilesystemStorage, spec: dict[str, Any]) -> None:
    from backend.domain.objects import lifecycle
    from backend.storage.objects import NAMESPACE_ASSET_OBJECTS, StorageKey

    if spec["kill_at"] == "unlinked":
        # The bytes are deleted inside the settle transaction; the claim's delete never commits.
        mock.patch.object(storage, "delete", then_die(storage.delete)).start()
    lifecycle.settle(db, storage, StorageKey(NAMESPACE_ASSET_OBJECTS, spec["name"]))


def main() -> int:
    spec = json.loads(sys.stdin.read())
    storage = LocalFilesystemStorage(spec["root"])
    workflow = spec["workflow"]
    if workflow == "backup":
        backup(spec, storage)
        return NOT_KILLED_EXIT
    with Database(DatabaseConfig(conninfo=spec["conninfo"], role="test", pool_min=0)) as db:
        if workflow == "settle":
            settle(db, storage, spec)
            return NOT_KILLED_EXIT
        ctx = context(db, spec)
        {"upload": upload, "import": import_, "replace": replace}[workflow](db, ctx, storage, spec)
    return NOT_KILLED_EXIT


if __name__ == "__main__":
    sys.exit(main())
