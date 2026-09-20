# Getting Started

PRKS is designed to run locally. The normal installation uses Python and SQLite directly; Docker is optional.

## Requirements

PRKS currently requires Python 3.12 or newer and the exact Python package versions pinned in \`requirements.txt\`. The application validates the Python environment before it performs database recovery, migrations, storage binding, or server startup.

Use a project-local virtual environment. On distributions that enforce PEP 668, do not bypass the package manager with \`sudo pip\` or \`--break-system-packages\`.

\`\`\`bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python prks_app.py
\`\`\`

The default server listens on \`127.0.0.1:8080\`. Open \`http://127.0.0.1:8080\` in a browser.

A different loopback port can be selected with \`--port\`. To make PRKS reachable on another interface, pass an explicit host such as \`--host 0.0.0.0\`. PRKS does not provide application-level authentication, so exposing it beyond a trusted host/network requires an access layer you control.

## Testing mode

\`\`\`bash
python prks_app.py --testing
\`\`\`

Testing mode uses port 8070 by default and, when \`PRKS_STORAGE\` is not set, isolates data under \`data_testing/\`. It contains safety checks intended to prevent tests from binding to the normal repository \`data/\` tree.

Use testing mode for browser automation, demo data, screenshots, and destructive experiments.

## Docker

Build with the repository helper:

\`\`\`bash
./docker-build.sh
\`\`\`

Then start the Compose deployment:

\`\`\`bash
docker compose up -d
\`\`\`

Compose publishes PRKS on host loopback by default and mounts \`./data\` into the container as \`/data\`. The container binds internally to \`0.0.0.0:8080\`; that does not by itself expose the host on every network interface.

For the exact current Docker, UID/GID, storage, and publish-host behavior, use the [README](https://github.com/Fooftilly/PRKS/blob/master/README.md).

## Storage location

Set \`PRKS_STORAGE\` to choose the persistent-data root. When it is unset in a normal run, PRKS uses the repository's \`data/\` directory.

Canonical data includes the SQLite library database and managed files. Thumbnail/search/research indexes are derived and may be rebuilt. See [Storage, Backup, and Restore](Storage-Backup-and-Restore.md).

## Next steps

After the server is running:

1. Create or import Works and organize them into Folders.
2. Add People, Roles, Tags, and Playlists as needed.
3. Use the Work view for PDFs, notes, metadata, annotations, and related research.
4. Use the command palette (Ctrl+K, or Cmd+K on macOS) for fast navigation and creation.
5. Read [Workspace Tabs and Split View](Workspace-Tabs-and-Split-View.md) if you want several research surfaces open together.
6. Read [Offline and Sync](Offline-and-Sync.md) before relying on offline editing; coverage evolves and the rollout-status document is the definitive current score.
