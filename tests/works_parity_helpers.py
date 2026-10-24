"""Shared fixture of the Works parity tests (L-W-1 #628).

A test does the same calls on its leg's library object and on a reference
SQLite ``PRKSDatabase`` built in the same test, then compares the results
after replacing what differs by construction: generated ids (each becomes a
label naming its Work) and the current time (each timestamp becomes its
format). On the ``postgres`` leg the left side is the PostgreSQL facade.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from typing import Any, Optional

from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig
from tests.backend_parity import POSTGRES, SQLITE, BackendParityTestCase

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCHEMA_PATH = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")
_ID = re.compile(r"\b(W|MF|AS)-[0-9A-F]{32}\b")
_MOMENT = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(\.\d{3})?")
_ETAG = re.compile(r'W/"prks-([a-z-]+)-(\d+)-([0-9a-f]{32})"')
VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
PDF_BYTES = b"%PDF-1.4\n% parity fixture\n%%EOF\n"


class _Side:
    """One library object plus the labels of the ids it generated."""

    def __init__(self, db: Any, pdfs_dir: str, device_id: str) -> None:
        self.db = db
        self.pdfs_dir = pdfs_dir
        self.device_id = device_id
        self.labels: dict[str, str] = {}

    def envelope(self, operation: str, entity_id: str, payload: dict[str, Any],
                 base_revision: Optional[int] = None, *, op_id: Optional[str] = None,
                 occurred_at: str = "2026-09-11T10:00:00Z", depends_on: Optional[list[str]] = None,
                 entity_type: str = "work") -> dict[str, Any]:
        """A semantic operation from this side's device."""
        return {"op_id": op_id or str(uuid.uuid4()), "device_id": self.device_id,
                "operation": operation, "entity_type": entity_type, "entity_id": entity_id,
                "payload": payload, "base_revision": base_revision, "occurred_at": occurred_at,
                "created_at": occurred_at, "depends_on": depends_on or []}

    def add(self, label: str, **fields: Any) -> str:
        work_id = self.db.add_work(**fields)
        self.labels[work_id] = f"<W {label}>"
        work = self.db.get_work(work_id)
        self.labels[work["primary_manifestation_id"]] = f"<MF {label}>"
        if work["primary_asset_id"]:
            self.labels[work["primary_asset_id"]] = f"<AS {label}>"
        return work_id

    def store_pdf(self, name: str, data: bytes = PDF_BYTES) -> str:
        os.makedirs(self.pdfs_dir, exist_ok=True)
        with open(os.path.join(self.pdfs_dir, name), "wb") as handle:
            handle.write(data)
        return f"/api/pdfs/{name}"

    def normal(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self.normal(item) for key, item in sorted(value.items())}
        if isinstance(value, list):
            return [self.normal(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.normal(item) for item in value)
        if isinstance(value, str):
            text = _ID.sub(lambda match: self.labels.get(match.group(0), "<unknown id>"), value)
            text = _MOMENT.sub(lambda match: "<ms>" if match.group(1) else "<s>", text)
            return _ETAG.sub(lambda match: f"<etag {match.group(1)} {match.group(2)}>", text)
        return value


class WorksParityTestCase(BackendParityTestCase):
    """Two sides per test: ``self.leg`` (this leg's library) and ``self.ref`` (SQLite)."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="prks-works-parity-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        leg_storage = StorageConfig.for_testing(os.path.join(self.tmp, "leg"))
        ref_storage = StorageConfig.for_testing(os.path.join(self.tmp, "ref"))
        leg_db = self.open_library(leg_storage)
        self.leg = _Side(leg_db, leg_storage.pdfs_dir, self._leg_device())
        self.ref = _Side(PRKSDatabase(storage=ref_storage, schema_path=_SCHEMA_PATH), ref_storage.pdfs_dir,
                         str(uuid.uuid4()))

    def _leg_device(self) -> str:
        if self.parity_backend == POSTGRES:
            from backend.pg_facade.context import current_context

            device = current_context().device_id
            assert device is not None
            return str(device)
        return str(uuid.uuid4())

    def send(self, side: _Side, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Process one operation with the protocol of ``side``'s backend.

        On the ``postgres`` leg that is :mod:`backend.sync.protocol`, acting
        for the bound actor; an operation it has no handler for is a
        refusal the parity log records, so the test is skipped, not failed.
        """
        if side is self.ref or self.parity_backend == SQLITE:
            from backend import sync_protocol

            return sync_protocol.process_operation(side.db, data)
        from backend.pg_facade import NotPortedError
        from backend.pg_facade.context import current_context
        from backend.sync import protocol
        from backend.sync.families import Environment

        library = side.db.parity_library
        try:
            return protocol.process_operation(library.database, current_context(), data,
                                              Environment(pdfs_dir=side.pdfs_dir))
        except NotPortedError as exc:
            self.parity_log.unported.add(exc.method)
            self.parity_log.refusals += 1
            raise

    def send_both(self, operation: str, ids: tuple[str, str], payload: dict[str, Any],
                  base_revision: Optional[int] = None, **options: Any) -> tuple[int, dict[str, Any]]:
        """The same operation on both sides; asserts equal results, returns this leg's."""
        mine = self.send(self.leg, self.leg.envelope(operation, ids[0], payload, base_revision, **options))
        theirs = self.send(self.ref, self.ref.envelope(operation, ids[1], payload, base_revision, **options))
        self.assertEqual(self.leg.normal(mine), self.ref.normal(theirs), operation)
        return mine

    def both(self, call: str, *args: Any, **kwargs: Any) -> tuple[Any, Any]:
        return (getattr(self.leg.db, call)(*args, **kwargs),
                getattr(self.ref.db, call)(*args, **kwargs))

    def same(self, call: str, *args: Any, **kwargs: Any) -> Any:
        mine, theirs = self.both(call, *args, **kwargs)
        self.assertEqual(self.leg.normal(mine), self.ref.normal(theirs), call)
        return mine

    def add_both(self, label: str, **fields: Any) -> tuple[str, str]:
        return self.leg.add(label, **fields), self.ref.add(label, **fields)

    def same_for(self, call: str, ids: tuple[str, str], *args: Any) -> Any:
        mine = getattr(self.leg.db, call)(ids[0], *args)
        theirs = getattr(self.ref.db, call)(ids[1], *args)
        self.assertEqual(self.leg.normal(mine), self.ref.normal(theirs), call)
        return mine

    def add_pdf(self, label: str, name: str, **fields: Any) -> tuple[str, str]:
        mine = self.leg.add(label, file_path=self.leg.store_pdf(name), source_kind="pdf", **fields)
        theirs = self.ref.add(label, file_path=self.ref.store_pdf(name), source_kind="pdf", **fields)
        return mine, theirs

