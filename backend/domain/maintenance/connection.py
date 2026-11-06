"""The dedicated connection a backup or restore runs on (L-B-1 #639; §4.3, §11.4, §13.3).

Backup and restore hold session-level advisory locks (the Library's objects
lock, and in R3 the maintenance lock) for longer than any request, so they
never borrow a pooled connection: a pooled connection returned with a
session lock would leak it to the next borrower. :func:`maintenance_connection`
opens one connection for the run and closes it afterwards, after releasing
every advisory lock it still holds. Its timeouts suit a long run: no
statement timeout and no idle-in-transaction timeout (a backup's object copy
or a restore's staging can outlast the request settings of §4.3), and the
pool's lock timeout, which the R3 drain narrows with ``SET LOCAL`` in its
own transaction.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg

from backend.db.config import DEFAULT_SEARCH_PATH

APPLICATION_NAME = "prks-maintenance"

# The session settings of a maintenance connection (§4.3 lists the pooled ones).
SESSION_SETTINGS: dict[str, str] = {
    "search_path": ", ".join(DEFAULT_SEARCH_PATH),
    "statement_timeout": "0",
    "idle_in_transaction_session_timeout": "0",
}

# The settings an export or staging transaction pins with SET LOCAL, so every
# value has one textual form in the archive (§11.4, "Encoding").
INTERCHANGE_SETTINGS: dict[str, str] = {
    "TimeZone": "UTC",
    "DateStyle": "ISO",
    "IntervalStyle": "iso_8601",
    "bytea_output": "hex",
    "extra_float_digits": "3",
}


@contextmanager
def maintenance_connection(conninfo: str) -> Iterator[psycopg.Connection[Any]]:
    """One autocommit connection for a backup or restore run, closed afterwards.

    The connection is never returned to a pool. On exit it releases every
    advisory lock it still holds before closing, so even an abort that skipped
    its own release leaves nothing behind (and a dead connection releases its
    locks on the server anyway).
    """
    conn = psycopg.connect(conninfo, autocommit=True, application_name=APPLICATION_NAME)
    try:
        for name, value in SESSION_SETTINGS.items():
            conn.execute("SELECT set_config(%s, %s, false)", (name, value))
        yield conn
    finally:
        try:
            if not conn.closed and conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE:
                conn.execute("SELECT pg_advisory_unlock_all()")
        except psycopg.Error:
            pass
        conn.close()


def pin_interchange_settings(conn: psycopg.Connection[Any]) -> None:
    """``SET LOCAL`` the interchange settings; call inside the export or staging transaction."""
    for name, value in INTERCHANGE_SETTINGS.items():
        conn.execute("SELECT set_config(%s, %s, true)", (name, value))
