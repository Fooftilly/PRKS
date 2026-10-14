"""Child process for the root-pairing kill tests (tests/pg/test_root_pairing.py).

Runs one bootstrap or start against a harness database and a scratch root and
exits the process with ``os._exit`` at the named fault point, so the kernel
(not a ``finally``) releases its file locks and the server ends its session,
as for a killed container.

Usage: python -m tests.pg.root_pairing_child (bootstrap|start) CONNINFO ROOT KILL_AT
"""

from __future__ import annotations

import os
import sys

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.domain.identity import root_pairing
from backend.storage.root_binding import acquire_shared_root_lease

KILLED_EXIT = 77


def main(argv: list[str]) -> int:
    action, conninfo, root, kill_at = argv

    def faults(point: str) -> None:
        if point == kill_at:
            os._exit(KILLED_EXIT)

    lease = acquire_shared_root_lease(root)
    with Database(DatabaseConfig(conninfo=conninfo, role="test", pool_min=0)) as db:
        if action == "bootstrap":
            root_pairing.bootstrap(
                db,
                root_pairing.OwnerSpec("owner", "Owner", "Library"),
                root=root,
                lease=lease,
                testing=True,
                faults=faults,
            )
        else:
            root_pairing.start_process(
                db, root=root, lease=lease, testing=True, bump=True, faults=faults
            )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
