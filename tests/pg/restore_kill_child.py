"""Child process for the kill-after-R4 test (tests/pg/test_restore_kill.py, L-B-1 #639).

Runs one restore and exits the process with ``os._exit`` the moment the R4
transaction has committed, before R5 runs, so neither the staging schema drop
nor the lock release in ``_release`` happens: the server ends the session, as
for a killed container.

Usage: python -m tests.pg.restore_kill_child CONNINFO ROOT ARCHIVE LIBRARY_ID OWNER_USER_ID STAGING_DIR
"""

from __future__ import annotations

import os
import sys
from typing import Any
from unittest import mock

from backend.domain.maintenance import restore
from backend.storage.objects import LocalFilesystemStorage

KILLED_EXIT = 77


def main(argv: list[str]) -> int:
    conninfo, root, archive, library_id, owner_user_id, staging_dir = argv
    real_commit = restore._commit_with_retry

    def commit_then_die(*args: Any, **kwargs: Any) -> Any:
        real_commit(*args, **kwargs)
        os._exit(KILLED_EXIT)

    with mock.patch.object(restore, "_commit_with_retry", commit_then_die):
        restore.restore_library(conninfo, LocalFilesystemStorage(root), archive, library_id,
                                owner_user_id=owner_user_id, staging_dir=staging_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
