# Storage, Backup, and Restore

PRKS stores canonical research data locally and provides an application-level backup/restore format.

## Storage root

`PRKS_STORAGE` selects the persistent-data root. Without it, normal repository runs use `data/`.

Canonical data includes the main SQLite library database and managed research files. Thumbnail caches, PDF text-search indexes, research-reference indexes, and similar rebuildable artifacts are derived data.

For the full path/environment-variable reference, see [Configuration and Operations](Configuration-and-Operations.md).

## Backup and restore

Use **Settings → Backup & restore → Download backup**. That is the supported way to preserve a PRKS library.

A verified `.prks-backup` archive contains:

- a consistent SQLite snapshot of the main database (works, people, roles, tags, folders, playlists, annotations, PDF annotation metadata, relationships, and app settings stored in the database)
- managed PDFs
- managed person files
- the processing queue, when that directory lives under the configured PRKS storage root

It does **not** include thumbnail cache, the PDF text-search index (`prks_text_index.db` and WAL/SHM files), the derived research-reference index (`prks_research_index.db` and WAL/SHM files), logs, or temporary maintenance files. After restore, thumbnails are discarded and the text index and research-reference index are rebuilt.

`.prks-backup` files hold private research data. Store them as carefully as the live library. The archive is ZIP-based, but restore it through PRKS (**Settings → Backup & restore**) rather than unzipping it into `/data` by hand.

Restore uploads the archive into a staging area, verifies structure, hashes, SQLite integrity, and schema compatibility, then asks you to type `RESTORE` before replacing the current library. A malformed or corrupt backup cannot change live data. If restore is interrupted before it commits, PRKS puts the previous library back.

While PRKS is running, ordinary reads may overlap. Canonical mutations are serialized (one writer at a time). Creating a backup blocks canonical mutations for the whole snapshot and archive so the ZIP stays consistent; ordinary reads may continue. Restore is exclusive: it waits for in-flight storage access, then blocks new reads, mutations, and backups until replacement and rebind finish. SQLite connections remain per-operation. Threading does not mean parallel SQLite writes, and there is no connection pool.

The backup does not include machine-specific deployment settings (`PRKS_STORAGE`, `PRKS_FOR_PROCESSING_DIR`, bind host, Docker UID/GID, and similar). A backup made under Docker `/data` can be restored to `./data` or another `PRKS_STORAGE`. Browser `localStorage` (theme, force-mobile layout, open workspace tabs/split layout, and other device-only settings) is not part of the server backup.

There is no cloud backup, schedule, or encryption in this release. If you copy a `.prks-backup` off a trusted disk, use filesystem or container encryption, or wait for a later encrypted-backup feature.

### Emergency cold copy

If you cannot use Settings backup, stop PRKS first, copy the entire storage root, then start it again. Do not copy a live SQLite directory as the primary backup method.

Docker:

```bash
docker compose stop prks
# copy the host storage directory (normally ./data) to a backup location
# outside the live storage tree
docker compose start prks
```

The copy destination must be outside the live storage tree. Do not copy into `./data` or into `/data`.

## Schema compatibility

PRKS performs supported schema migrations at startup and refuses databases created by an unsupported newer schema rather than attempting a downgrade. See [Configuration and Operations](Configuration-and-Operations.md#database-migrations).

## What backup does not preserve

Machine-specific deployment configuration is not research data. Bind address, Docker UID/GID, `PRKS_STORAGE`, processing-directory location, and other deployment settings are not portable library state.

Browser-local workspace state—theme, open tabs, split layout, and similar localStorage state—is also not part of the server backup.
