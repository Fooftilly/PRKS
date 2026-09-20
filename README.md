# PRKS — Personal Research Knowledge System

PRKS is a self-hosted, local-first research library for organizing PDFs, Markdown research notes, online video references, bibliographic metadata, people, tags, reading progress, PDF annotations, playlists, and structured research relationships.

It uses Python, SQLite, and on-disk files on your machine—no separate database server is required.

![Folder of public-domain books](docs/screenshots/folders.png)

![Origin of Species open in the PDF reader](docs/screenshots/work.png)

![People in the library](docs/screenshots/people.png)

## Highlights

- Organize research with Works, Folders, Tags, People/Roles, Playlists, progress, and Saved Views.
- Read and annotate managed PDFs inside PRKS.
- Keep Research Notes alongside source material and connect them to Concepts, Positions, Arguments/Stances, and the Research Graph.
- Work across in-app tabs and split panes without opening a collection of browser tabs.
- Use progressively local-first/offline-capable workflows with durable browser-side operations for supported domains.
- Back up and restore the canonical PRKS library through the application.
- Run locally with Python/SQLite or with Docker Compose.

## Requirements

- **Python 3.12+**
- Exact runtime package pins from `requirements.txt`

Prefer a project-local virtual environment. On PEP 668 “externally managed” systems, do not use `sudo pip` or `--break-system-packages`.

## Quick start

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python prks_app.py
```

Open **http://127.0.0.1:8080**.

PRKS binds to loopback by default and has **no application-level authentication**. Do not expose it to an untrusted network. See [Security and Operations](docs/wiki/Security-and-Operations.md) before changing the bind/publish address.

For isolated demo/test storage:

```bash
./.venv/bin/python prks_app.py --testing
```

Testing mode normally uses port **8070** and a separate storage tree.

## Docker

```bash
./docker-build.sh
docker compose up -d
```

Open **http://127.0.0.1:8080** on the host. Compose publishes loopback by default and stores persistent data under the repository `./data` bind mount.

See [Getting Started](docs/wiki/Getting-Started.md) and [Configuration and Operations](docs/wiki/Configuration-and-Operations.md) for host, port, storage, Docker, logging, and diagnostics details.

## Documentation

The version-controlled [Wiki source](docs/wiki/Home.md) is the main long-form documentation and is published to the [GitHub Wiki](https://github.com/Fooftilly/PRKS/wiki).

| Topic | Documentation |
| --- | --- |
| Install and run | [Getting Started](docs/wiki/Getting-Started.md) |
| Everyday workflows | [User Guide](docs/wiki/User-Guide.md) |
| Tabs and split view | [Workspace Tabs and Split View](docs/wiki/Workspace-Tabs-and-Split-View.md) |
| PDFs and annotations | [PDFs and Annotations](docs/wiki/PDFs-and-Annotations.md) |
| Research graph/model | [Research Network](docs/wiki/Research-Network.md) |
| Offline/local-first | [Offline and Sync](docs/wiki/Offline-and-Sync.md) |
| Storage and backups | [Storage, Backup, and Restore](docs/wiki/Storage-Backup-and-Restore.md) |
| Configuration/logging/performance | [Configuration and Operations](docs/wiki/Configuration-and-Operations.md) |
| Troubleshooting | [Troubleshooting](docs/wiki/Troubleshooting.md) |
| Architecture | [Architecture](docs/wiki/Architecture.md) |
| Contributor commands | [Developer Reference](docs/wiki/Developer-Reference.md) |
| Dependencies/vendoring | [Dependencies and Vendoring](docs/wiki/Dependencies-and-Vendoring.md) |
| Security | [Security and Operations](docs/wiki/Security-and-Operations.md) |

Implementation/design authorities that intentionally remain outside the Wiki:

- [AGENTS.md](AGENTS.md) — contributor/agent architecture and testing rules.
- [DESIGN.md](DESIGN.md) — authoritative visual/interaction contract.
- [SECURITY.md](SECURITY.md) — vulnerability reporting.
- [docs/local-first-sync.md](docs/local-first-sync.md) — detailed sync design.
- [docs/local-first-rollout-status.md](docs/local-first-rollout-status.md) — current local-first coverage.

## Data and backups

Normal runs use `data/` unless `PRKS_STORAGE` selects another storage root. The canonical library includes the SQLite database and managed research files; several indexes and caches are derived and rebuildable.

Use **Settings → Backup & restore → Download backup** for supported backups. A `.prks-backup` contains private research data and should be protected like the live library.

See [Storage, Backup, and Restore](docs/wiki/Storage-Backup-and-Restore.md) before manual recovery, moving storage, or restoring a library.

## Development

Fast test suite:

```bash
python run_tests.py
```

Full browser E2E gate:

```bash
python run_tests.py --e2e
```

Use [Developer Reference](docs/wiki/Developer-Reference.md) for targeted E2E modes, UX tours, browser fixtures, Sonar tooling, project layout, and the local post-commit archive. Contributor/agent rules are authoritative in [AGENTS.md](AGENTS.md).

## Security

PRKS is a single-user application without built-in authentication. Loopback is the safe default. Network exposure, trusted host behavior, browser-origin protections, Markdown sanitization, privacy-safe logging, and remote portrait constraints are documented in [Security and Operations](docs/wiki/Security-and-Operations.md).

For vulnerability reporting, see [SECURITY.md](SECURITY.md).
