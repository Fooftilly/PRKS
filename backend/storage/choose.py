"""Choose the Library folder before setup claims it (storage-architecture §8.1, #720).

Under PostgreSQL the database is paired with exactly one data root
(postgresql-architecture §11.1), so "choose" exists only while the selected
root is still **unclaimed**: no marker names an instance and its start
generation is 0. Choosing records the new folder in the bootstrap file
(§5.1) through the guarded compare-and-set; it takes effect at the next
start and never rebinds a running process.

Refusals (``StorageRootRefused`` with a stable ``reason``; nothing written):

- ``choose_testing``: testing mode never reads the bootstrap file (§5.3).
- ``choose_set_by_host``: ``--storage-root`` or ``PRKS_STORAGE`` selects the
  root, and a higher source never rewrites a lower one (§5.2).
- ``choose_root_claimed``: setup already claimed the current root; another
  folder now means a storage move (Phase E, #721) or a new installation and a
  Library restore.
- ``choose_move_unresolved``: the bootstrap file records a storage move.
- ``choose_same_root``: the target is the current root.
- ``open_another_library_refused``: the target already belongs to a
  database. "Open another library" is not offered under PostgreSQL: only a
  copy of this root could pass the start check, and switching to a copy
  leaves two usable copies of one library (risk R20).
- ``root_in_use``: a PRKS process holds the current or the target root.
  The target's lease is a non-blocking try-lock, so nothing waits while the
  config lock is held (§8.1 step 2).
- Every §7.2 refusal of the target, from the same binder a start uses.

**Running processes.** The CLI takes the current root's lease exclusively
while it writes, so it refuses while any PRKS process runs there. The setup
page runs in the web process that holds the current root, so it passes that
lease in and the caller stops offering setup until a restart. A process that
read the bootstrap file before a write and leased the old root after it
refuses to start (``root_binding``: the selection is re-read under the
lease), so no process serves a root the committed selection does not name.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional

from backend.storage import paths, resolver
from backend.storage.bootstrap_config import BootstrapConfigStore, expect_unchanged
from backend.storage.errors import StorageRootRefused
from backend.storage.root_binding import acquire_root_lease, open_storage_root
from backend.storage.root_marker import RootMarker, read_marker

# The sources a choice may replace: only a root nothing higher selected (§5.2).
CHOOSABLE_SOURCES = frozenset(
    {resolver.SOURCE_CONFIG_FILE, resolver.SOURCE_PLATFORM_DEFAULT, resolver.SOURCE_DEVELOPMENT_DEFAULT}
)


@dataclass(frozen=True)
class Chosen:
    """A recorded choice: the normalized folder and whether choosing created it as a new root."""

    root: str
    created: bool
    storage_root_id: str


def _claimed(marker: Optional[RootMarker]) -> bool:
    if marker is None:
        return False
    return marker.instance_id is not None or marker.start_state.generation > 0


def _refuse(reason: str, message: str) -> StorageRootRefused:
    return StorageRootRefused(reason, message)


def refusal_before_target(environ: Mapping[str, str], current_root: str) -> Optional[StorageRootRefused]:
    """Why no folder can be chosen now, whatever the target, or ``None``.

    ``current_root`` is the root the current selection resolves to.
    """
    if paths.testing_from_value(environ.get("PRKS_TESTING", "")):
        return _refuse("choose_testing", "A testing instance never uses the bootstrap file, so it cannot choose a folder.")
    resolved = resolver.resolve_storage_root(cli_root=None, environ=environ, testing=False)
    if resolved.source not in CHOOSABLE_SOURCES:
        return _refuse(
            "choose_set_by_host",
            "PRKS_STORAGE selects the Library folder. Change it where PRKS is deployed instead.",
        )
    if _claimed(_read_marker_or_none(current_root)):
        return _refuse(
            "choose_root_claimed",
            "Setup has already paired this installation with its Library folder. Another folder needs a "
            "storage move, or a new installation and a Library restore.",
        )
    return None


def _read_marker_or_none(root: str) -> Optional[RootMarker]:
    try:
        return read_marker(os.path.realpath(root)) if os.path.isdir(root) else None
    except OSError:
        return None


def _target_config(target: str, environ: Mapping[str, str]) -> Any:
    from backend.storage.config import StorageConfig

    # As if given on the command line: a new, empty folder may become a root.
    env = {key: value for key, value in environ.items() if key not in ("PRKS_STORAGE", "PRKS_TESTING")}
    return StorageConfig.from_env(cli_root=target, environ=env)


def choose_root(
    target: str,
    *,
    environ: Optional[Mapping[str, str]] = None,
    held_current_root: Optional[str] = None,
    cwd: Optional[str] = None,
) -> Chosen:
    """Validate ``target`` and record it as the Library folder for the next start.

    ``held_current_root`` is the resolved current root when the caller is the
    process serving it (the setup page); otherwise the current root's lease is
    taken here, exclusively and without waiting, for the whole write.
    """
    env = dict(os.environ if environ is None else environ)
    if paths.testing_from_value(env.get("PRKS_TESTING", "")):
        raise _refuse("choose_testing", "A testing instance never uses the bootstrap file, so it cannot choose a folder.")
    current_root = resolver.resolve_storage_root(cli_root=None, environ=env, testing=False).root
    early = refusal_before_target(env, current_root)
    if early is not None:
        raise early
    if held_current_root is not None and os.path.realpath(held_current_root) != os.path.realpath(current_root):
        raise _refuse("choose_set_by_host", "This process does not serve the folder the bootstrap file selects.")
    folder = resolver.normalize_root_spelling(target, environ=env, what="the Library folder", cwd=cwd)
    if os.path.realpath(folder) == os.path.realpath(current_root):
        raise _refuse("choose_same_root", "That folder is already the Library folder.")
    store = BootstrapConfigStore(resolver.bootstrap_config_path(env))
    with store.transaction() as txn:
        snapshot = txn.read()
        if snapshot is not None and snapshot.relocation is not None:
            raise _refuse(
                "choose_move_unresolved",
                "The bootstrap file records a storage move. Finish or abort the move first.",
            )
        # Re-check under the config lock: another writer may have moved on.
        again = refusal_before_target(env, current_root)
        if again is not None:
            raise again
        current_lease = None
        if held_current_root is None and _read_marker_or_none(current_root) is not None:
            current_lease = acquire_root_lease(os.path.realpath(current_root))
        try:
            target_marker = _read_marker_or_none(folder)
            if _claimed(target_marker):
                raise _refuse(
                    "open_another_library_refused",
                    "That folder already holds a Library paired with a database. Opening another library is "
                    "not supported: use a storage move, or a new installation and a Library restore.",
                )
            bound = open_storage_root(
                _target_config(folder, env), config_file_path=store.path, register=False
            )
            try:
                if _claimed(bound.marker):
                    raise _refuse(
                        "open_another_library_refused",
                        "That folder already holds a Library paired with a database.",
                    )
                txn.write(expect=expect_unchanged(snapshot), local_root=folder)
                return Chosen(root=folder, created=bound.created, storage_root_id=bound.storage_root_id)
            finally:
                bound.release()
        finally:
            if current_lease is not None:
                current_lease.release()
