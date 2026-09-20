# Storage, Backup, and Restore

PRKS stores canonical research data locally and provides an application-level backup/restore format.

## Storage root

`PRKS_STORAGE` selects the persistent-data root. Without it, normal repository runs use `data/`.

Typical storage includes:

- `prks_data.db` — canonical SQLite database;
- `pdfs/` — managed PDFs;
- person-managed/cache files where applicable;
- processing-queue data when configured inside the storage root;
- derived indexes/caches/logs.

The exact current environment variables and paths are documented in the [README](https://github.com/Fooftilly/PRKS/blob/master/README.md).

## Canonical vs derived

Backups preserve research state, not every byte that happens to exist under the runtime directory.

Canonical data includes the main library database and managed research files.

Derived data includes thumbnail caches, PDF text-search indexes, research-reference indexes, and similar rebuildable artifacts. These are excluded from the canonical archive and rebuilt/reconciled after restore.

## Supported backup

Use **Settings → Backup & restore → Download backup**.

A `.prks-backup` archive contains a consistent SQLite snapshot plus managed research files covered by the backup format. Treat it as private research data.

Creating a backup coordinates with canonical mutations so the archive is internally consistent.

## Restore

Restore is intentionally stricter than extracting a ZIP over the storage directory.

The application stages and verifies the archive, checks structure/hashes/SQLite/schema compatibility, and only then replaces live data. The UI requires an explicit destructive confirmation.

If replacement is interrupted before commit completes, restore logic is designed to preserve/put back the prior library rather than leave a partially replaced state.

## What backup does not preserve

Machine-specific deployment configuration is not research data. Bind address, Docker UID/GID, `PRKS_STORAGE`, processing-directory location, and other deployment settings are not portable library state.

Browser-local workspace state—theme, open tabs, split layout, and similar localStorage state—is also not part of the server backup.

## Cold copy

If the application-level backup cannot be used, stop PRKS before copying the complete storage root. A blind live copy of SQLite/WAL-managed storage should not be the normal backup method.

## Schema migrations

PRKS performs supported schema migrations at startup and refuses databases created by an unsupported newer schema rather than attempting a downgrade.

Before installing a release/revision that announces a schema change, keep a verified backup.
