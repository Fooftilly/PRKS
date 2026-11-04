"""``GET /readyz`` for a PostgreSQL web process (§12.3; L-D-1 #630).

Ready means all of these hold:

- the web pool can lend a connection, within a short bound;
- the database applied exactly the schema files this code ships (§4.4);
- setup is ``ready``;
- the storage root this process opened is still paired with this database
  (§11.1): its marker names this instance and this root, and the marker's
  start generation and token equal the database's.

The last check is what keeps ``web`` not ready while a start elsewhere left
the two apart, for example a ``migrate`` killed between its database bump and
its marker write: the next start of any process reconciles the marker, and
``/readyz`` turns ready again once it has.

The answer is a bare yes or no. Nothing about the database, the schema, the
root or the reason is ever returned to the client; the reason goes to the
log once per change, as a stable code.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from typing import Any, Optional

from backend.db.database import Database, Transaction
from backend.db.errors import DatabaseError
from backend.db.migrate import SCHEMA_DIR, AppliedFile, MigrationError, load_schema_files, plan
from backend.domain.identity.root_pairing import SETUP_READY
from backend.storage.errors import StorageRootError
from backend.storage.root_marker import StartState, binding_refusal, read_marker

LOGGER = logging.getLogger(__name__)

# A probe must answer well within the Compose healthcheck's timeout.
# Both bounds together stay within it: the wait for a pooled connection
# while every connection is busy, then the statements.
PROBE_BORROW_TIMEOUT_S = 1.0
PROBE_STATEMENT_TIMEOUT_MS = 2000

_INSTANCE = (
    "SELECT id, storage_root_id, setup_state, start_generation, start_token FROM prks.instance"
)
_APPLIED = "SELECT version, name, sha256 FROM prks.schema_migrations ORDER BY version"

class PostgresReadiness:
    """The readiness of one web process; :meth:`not_ready_reason` is None when ready."""

    def __init__(
        self,
        db: Database,
        *,
        instance_id: str,
        root: str,
        schema_dir: str = SCHEMA_DIR,
        read_root_marker: Callable[[str], Any] = read_marker,
    ) -> None:
        self._db = db
        self._instance_id = instance_id
        self._root = root
        # Loaded once: the files on disk do not change under a running process.
        self._files = load_schema_files(schema_dir)
        self._read_marker = read_root_marker
        self._lock = threading.Lock()
        self._last_reason: Optional[str] = "starting"

    def ready(self) -> bool:
        reason = self.not_ready_reason()
        with self._lock:
            if reason != self._last_reason:
                if reason is None:
                    LOGGER.info("readiness_ready")
                else:
                    LOGGER.warning("readiness_not_ready reason=%s", reason)
                self._last_reason = reason
        return reason is None

    def not_ready_reason(self) -> Optional[str]:
        try:
            state = self._db.query(self._read_state, borrow_timeout=PROBE_BORROW_TIMEOUT_S)
        except DatabaseError:
            return "database_unavailable"
        if state is None:
            return "setup_pending"
        row, applied = state
        try:
            if plan(self._files, applied):
                return "schema_pending"
        except MigrationError:
            return "schema_mismatch"
        if row["id"] != self._instance_id:
            return "instance_changed"
        if row["setup_state"] != SETUP_READY:
            return "setup_pending"
        return self._root_reason(row)

    @staticmethod
    def _read_state(tx: Transaction) -> Optional[tuple[dict[str, Any], Sequence[AppliedFile]]]:
        tx.execute(
            "SELECT set_config('statement_timeout', %s, true)", (str(PROBE_STATEMENT_TIMEOUT_MS),)
        )
        row = tx.execute(_INSTANCE).fetchone()
        if row is None:
            return None
        applied = [
            AppliedFile(int(r["version"]), str(r["name"]), str(r["sha256"]))
            for r in tx.execute(_APPLIED).fetchall()
        ]
        return dict(row), applied

    def _root_reason(self, row: dict[str, Any]) -> Optional[str]:
        try:
            marker = self._read_marker(self._root)
        except (StorageRootError, OSError):
            return "root_unreadable"
        if marker is None:
            return "root_unmarked"
        if binding_refusal(marker) is not None:
            return "root_not_bindable"
        if marker.storage_root_id != row["storage_root_id"] or marker.instance_id != self._instance_id:
            return "root_pairing_mismatch"
        database = StartState(int(row["start_generation"]), row["start_token"])
        if marker.start_state != database:
            return "start_generation_unreconciled"
        return None
