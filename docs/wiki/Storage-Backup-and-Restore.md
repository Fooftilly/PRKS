# Storage, Backup, and Restore

PRKS stores canonical research data locally and provides an application-level backup/restore format.

## Storage root

`PRKS_STORAGE` selects the persistent-data root. Without it, normal repository runs use `data/`. `python prks_app.py --storage-root PATH` overrides both for one run.

Each storage root has a `prks-root.json` marker that PRKS creates the first time it opens the root. It identifies the root's files (`storage_root_id`). PRKS will not start on a non-empty directory that is not a PRKS library, which catches a mistyped path or an unmounted disk. Only one PRKS web process can use a storage root at a time (the `worker` and `migrate` processes share it); a second web process stops at startup. The marker is not included in backups, and restoring into a root keeps that root's marker.

Canonical data is the Library in the PostgreSQL database plus the managed research files in the storage root (PDFs and portraits, stored as immutable objects). Thumbnail caches, PDF text-search rows, research-reference rows, and similar rebuildable data are derived.

For the full path/environment-variable reference, see [Configuration and Operations](Configuration-and-Operations.md).

## Backup and restore

Use **Settings → Backup & restore → Download backup**. That is the supported way to preserve a PRKS library.

A verified `.prks-backup` archive (format 2) holds one Library:

- a consistent snapshot of the Library's rows in the database (works, people, roles, tags, folders, playlists, annotations, relationships, research, Files for Processing items, and the Library's settings), one CSV file per table
- every managed object that snapshot names: PDFs, portraits, and the PDFs queued in Files for Processing
- display names of the Library's users and devices, but no login names, passwords, or sessions

It does **not** include thumbnails, the derived search and research-reference rows, logs, or temporary maintenance files. After a restore those are rebuilt in the background.

`.prks-backup` files hold private research data. Store them as carefully as the live library. The archive is ZIP-based, but restore it through PRKS (**Settings → Backup & restore**) rather than unzipping it into `/data` by hand.

Restore uploads the archive, verifies its structure, hashes, and schema compatibility, and stages its rows and objects, then asks you to type `RESTORE` before replacing the current Library. A malformed or corrupt backup cannot change live data. The replacement is one database transaction: if restore stops before it commits, the previous Library stays as it was.

A backup reads one database snapshot and never blocks ordinary edits; objects that snapshot names are kept until the backup has copied them. During a restore the Library is in maintenance: requests to it are answered 503 `MAINTENANCE` until the replacement commits. The commit advances the Library epoch, so a client still holding the old epoch is answered 409 and must reload.

The backup does not include machine-specific deployment settings (`PRKS_STORAGE`, bind host, Docker UID/GID, and similar). A backup made under Docker `/data` can be restored to `./data` or another `PRKS_STORAGE`. Browser `localStorage` (theme, force-mobile layout, open workspace tabs/split layout, and other device-only settings) is not part of the server backup.

There is no cloud backup, schedule, or encryption in this release. If you copy a `.prks-backup` off a trusted disk, use filesystem or container encryption, or wait for a later encrypted-backup feature.

### Emergency cold copy

If you cannot use Settings backup, stop PRKS and PostgreSQL first, then copy both the database's data directory and the entire storage root, taken at the same moment, and keep them together. A copy of the storage root alone does not contain the Library.

Docker:

```bash
docker compose stop
# copy the host storage directory (normally ./data) and the database volume
# (pgdata) to a backup location outside the live storage tree
docker compose start
```

The copy destination must be outside the live storage tree. Do not copy into `./data` or into `/data`.

## Schema compatibility

PRKS applies its PostgreSQL schema at startup (or in the `migrate` process) and refuses a database migrated by a newer PRKS rather than attempting a downgrade. Before 1.0 there is no migration from a pre-0.8 SQLite Library. See [Configuration and Operations](Configuration-and-Operations.md#database-migrations).

## What backup does not preserve

Machine-specific deployment configuration is not research data. Bind address, Docker UID/GID, `PRKS_STORAGE`, the database URL, and other deployment settings are not portable library state.

Browser-local workspace state—theme, open tabs, split layout, and similar localStorage state—is also not part of the server backup.
