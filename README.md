# PRKS — Personal Research Knowledge System

PRKS is a self-hosted web application for organizing research materials: PDFs, Markdown notes, and online video references. It stores everything in a SQLite database and on-disk files on your machine—no separate database server. The UI supports folders, tags, reading progress, people and bibliographic metadata, PDF annotations, and playlists for videos.

![Folder of public-domain books](docs/screenshots/folders.png)

![Origin of Species open in the PDF reader](docs/screenshots/work.png)

![People in the library](docs/screenshots/people.png)

## Requirements

- **Python 3.12+**
- **PyMuPDF** 1.24.10 

The HTTP server and SQLite access use the Python standard library.

Ordinary UI `/api` calls go UI → Client Request Coordinator → threaded PRKS HTTP
server → `LibraryAccessGate`. The coordinator cache is memory-only and short-lived.
It is not offline support.

## Quick start (local)

From the repository root:

```bash
pip install -r requirements.txt
python prks_app.py
```

The process listens on **127.0.0.1:8080** only. Open [http://127.0.0.1:8080](http://127.0.0.1:8080) in a browser. No extra firewall or network setup is required for this case.

Optional port (still loopback):

```bash
python prks_app.py --port 9000
```

To listen on every local interface (LAN or VPN), pass an explicit host:

```bash
python prks_app.py --host 0.0.0.0
```

`--host localhost` also works and binds that name. The default is the literal address `127.0.0.1`, not `localhost`.

### Testing mode (Creates seperate testing database)

```bash
python prks_app.py --testing
```

This sets `PRKS_TESTING=1` and uses port **8070** by default (unless you pass `--port`). With `PRKS_STORAGE` unset it defaults to `data_testing/` so repo `data/` is untouched. You may set `PRKS_STORAGE` to an explicit safe testing root. Testing mode refuses `/data` and the repository `data/` directory (and descendants), including via symlinks. `prks_app.py` is the only process entry.

## Docker

Build:

Use `./docker-build.sh`, which builds `prks:latest` and prunes dangling images (from previous builds).

And run with Compose (from the repo root):

```bash
docker compose up -d
```

The container process binds **0.0.0.0:8080** so Docker port forwarding can reach it. Compose then publishes that port on the **host loopback** only (`127.0.0.1:8080:8080`). Container `0.0.0.0` is not the same as exposing PRKS on every host interface.

Open [http://127.0.0.1:8080](http://127.0.0.1:8080) on the machine that runs Compose. This also sets `PRKS_STORAGE=/data`, mounts **`./data` on the host to `/data` in the container**, and runs the process as **`${UID:-1000}:${GID:-1000}`** so files on the bind mount match your user. The entrypoint creates `/data/pdfs` if needed and runs `python /app/prks_app.py --host 0.0.0.0`.

To publish the host port on every interface (LAN access):

```bash
PRKS_PUBLISH_HOST=0.0.0.0 docker compose up -d
```

PRKS has no application-level authentication. Use that override only on a network you already trust, or behind an access layer you control.

## Configuration and data layout

| Variable | Purpose |
| -------- | ------- |
| `PRKS_STORAGE` | If set, root directory for persistent data. Database: `$PRKS_STORAGE/prks_data.db`. PDFs: `$PRKS_STORAGE/pdfs/`. Thumbnails: `$PRKS_STORAGE/thumbs/`. |
| `PRKS_TESTING` | When truthy (`1`, `true`, `yes`), uses testing paths and stricter checks (see testing mode above). |
| `PRKS_THUMB_LOSSLESS` | When truthy, PDF card thumbnails use lossless WebP/PNG cache encoding (debugging). Default is card-optimized lossy WebP; cache filenames use rev `_v2`. |
| `PRKS_LOG_LEVEL` | Stderr log level. Default `INFO`. Changes volume, not what kinds of data may be logged. |
| `PRKS_LOG_FILE_LEVEL` | Persistent file log level. Default `ERROR`. |
| `PRKS_LOG_RETENTION_DAYS` | Rotated persistent log copies to keep. Default `7`. |
| `PRKS_LOG_FILE` | Override path for the rotating error log. Default `$PRKS_STORAGE/prks-errors.log`. |
| `PRKS_PERF_SLOW_MS` | API request duration in milliseconds counted as slow. Default `250`. Clamped to 10–60000. Does not timeout requests. |
| `PRKS_PERF_LOG_SLOW` | When truthy (`1`, `true`, `yes`, `on`), emit a privacy-safe `slow_request` INFO log for slow API requests. Default off. |
| `PRKS_BACKUP_MAX_UPLOAD_BYTES` | Maximum size of an uploaded `.prks-backup` restore archive. Default 64 GiB. A valid `Content-Length` is required. |

If `PRKS_STORAGE` is **unset**, non-testing runs use the project’s **`data/`** directory: `data/prks_data.db`, `data/pdfs/`, `data/thumbs/`, and person portrait cache `data/people/` (lossy WebP, max 512px edge, keyed by person id + `image_url` hash).

Person profile images (`GET /api/persons/{id}/profile-image`) are optional. `image_url` must be a direct public HTTP/HTTPS URL (HTTPS preferred) that itself returns HTTP 200. PRKS does not follow redirects, and private/local/link-local targets are refused. Only static JPEG/PNG/WebP/GIF rasters are accepted. The download is size- and time-bounded; the image is decoded and transcoded (max 512px edge, usually WebP) before anything is cached. Original remote bytes are not kept. Local portrait upload is not part of this feature. Updating a valid `image_url` clears that person’s cached portraits.

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
# copy ./data to your backup location
docker compose start prks
```

## Database migrations

PRKS automatically upgrades supported older databases at startup.

Current schema version: **13**.

Schema migrations are transactional and version-ordered. A database marked version N has passed every migration through N.

Databases created by a newer PRKS version are refused rather than downgraded. Download a verified backup before installing a PRKS revision that announces a database schema upgrade.

## PDF annotations

PDF file bytes are the canonical rendered document, including markup the viewer writes into the file.

The `annotations` table is the sole PRKS annotation-metadata store, used by the sidebar, comments, and `GET /api/works/{id}/annotations`. Extra EmbedPDF fields that are not first-class columns are stored in `geometry_json` so the submitted object can be reconstructed.

Schema v13 removed the former `work_annotations` JSON snapshot. PDF bytes remain the canonical rendered/embedded markup; `annotations` remains PRKS sidebar/comment metadata.

## PDF text search

PRKS keeps a separate derived PDF text-search index (`prks_text_index.db`). It is automatically reconciled with managed PDFs at startup. Unchanged PDFs are compared by stored source fingerprint and filesystem metadata; they are not re-extracted and do not trigger a full FTS integrity scan. Image-only or scanned PDFs may contain no searchable text; PRKS does not perform OCR.

The index is disposable and is rebuilt after backup restore. **Settings → Rebuild PDF text index** forces a complete re-extraction and FTS integrity verification if search results seem incomplete or stale.

## Bulk organization

On supported work-list pages (folder, recent, search, file type, progress, and Saved View results), choose **Select**, pick files, and use the bulk toolbar to change progress, move or clear folders, or add and remove tags.

A bulk action is one request and one database transaction. If any selected file or target is invalid, none of the selected files are changed.

## Command palette

Press Ctrl+K (Cmd+K on macOS), or choose **Search or jump**.

Use it to:

- open files, folders, people, groups, playlists, Saved Views, Concepts, Positions, and Arguments/Stances
- search the library
- navigate to sections/progress views
- create files/folders/people/groups
- open Settings and common actions

People, Progress, and Research sidebar shortcuts are collapsible. Active child routes stay visible without overwriting the collapsed preference.

## Workspace tabs

PRKS keeps a strip of in-app tabs under the top ribbon. Stacked mode shows one page at a time. Split view shows Main on the left and a Secondary area on the right that can itself be split further, up to 4 panes on screen at once (Main plus 3 Secondary). PRKS remembers your open tabs and split layout between sessions on this browser/device. That memory is local to the browser profile; there is no server-side workspace synchronization in this version.

**Tab actions**

Right-click a tab, or press Shift+F10 while it is focused, for tab actions. From that menu you can close the tab, **Close other tabs**, or **Close tabs to the right**. Parked tabs can also be opened in split view there. A visible Secondary tab's menu additionally offers **Split right**, **Split down**, **Make main**, and **Hide from split**.

If the strip is too narrow for every tab, an overflow button at the end lists the open tabs. Choose a tab to switch to it. Parked rows also offer split view and close.

A small marker on a tab means research notes or PDF annotations are drafting, saving, or that a save failed. Failures stay visible even if you keep editing.

**Split view**

1. Click **Split** beside the workspace tabs, then pick a page in **Open in split view**.
2. Or click the split icon on an already-open parked tab.

Only pages that can render beside Main appear in that picker. Already-open tabs are reused instead of duplicated.

Then the shortcuts:

- Normal click: open in the originating PRKS tab
- Ctrl/Cmd-click or middle-click: open a background PRKS tab (no extra browser page)
- Alt-click a link, or Alt+Enter in the command palette: open in split view
- Click inside a split pane: focus it (the details panel follows focus; the browser URL does not)
- Click a Secondary tab that's already visible, in the workspace strip: focus it in place (it does not become Main)
- **Make main**: **Pane actions** (`…`) on a Secondary header, or a Secondary tab/pane's context-menu action (swaps roles; URL becomes that page)
- **Hide split view**: Split control beside the workspace tabs. Parks every visible Secondary pane at once; the layout comes back with **Show split**.
- **Hide from split**: per-pane action on a Secondary tab's context menu. Keeps that pane's tab open (parked) but removes just that one pane.
- **+**: choose a page in the command palette (“Open in new tab”) and open it as a new main tab
- Close: a pane header's **×** closes that PRKS tab outright. Closing the last tab leaves Folders

Only Main controls the browser URL. The details panel follows whichever pane is focused.

**Split a pane further**

Any Secondary pane can be split again: open **Pane actions** (`…`) on that header, or **Split right** / **Split down** from its tab context menu, then pick a page the same way as the main Split button. The new pane opens beside (or below) that specific pane and becomes focused. Once 4 panes are visible at once, further splitting is disabled with an explanation until you close or hide a pane — ordinary new tabs still open normally, just parked.

**Resize split view**

Drag the thin divider between any two adjacent panes to resize them — this includes the root Main/Secondary divider and every divider between nested panes. Keyboard: focus a divider, then the appropriate arrow keys to resize (Shift + arrow for a larger step), Home / End for the smallest / largest allowed size for that divider. Double-click a divider to reset just that one split to its default size. Every pane keeps a comfortable minimum size. Resizing one divider never changes any other divider's size. Preferred split sizes are remembered with the rest of the workspace on this browser/device.

Cold-parked tabs do no rendering or network work until activated. Ordinary switching keeps up to three recently parked PDF Work tabs warm, preserving viewer state and resuming without another Work/PDF load. Explicit hide, close, narrow fallback, and LRU eviction still unload them.

**Drag and drop**

Dragging is an optional shortcut for the same actions above — every menu command still works without it.

- Reorder tabs: drag a tab along the workspace tab bar. Dragging near either edge of an overflowing strip scrolls it.
- Create the first split: drag a parked tab into the drop region on the right side of the workspace canvas.
- Add a parked tab to split view: drag it onto an edge of an existing Secondary pane (left/right/above/below) to split that pane in that direction.
- Move a pane: drag its header's grip handle onto another pane's edge to reposition it in the split layout.
- Park a pane: drag its grip handle back onto the tab bar. Equivalent to **Hide from split**, and asks first if the pane has unsaved work.

Main can be reordered in the tab bar but is never dropped into the split layout itself — use **Make main** for that. Press Escape at any point during a drag to cancel it without changing anything.

Each visible tab has a TabContext. Route state, page DOM (`ctx.root` / `ctx.query`),
async generation, and live resources (PDF viewer, notes editor, graph) live
there. Stacked mode mounts one context. Split view mounts Main plus every visible Secondary pane (up to 4 panes total).

## Offline / PWA (read-only, Work-only in Phase 1)

PRKS can be installed as a Progressive Web App and stays useful for a while when the PRKS server becomes temporarily unreachable. This is **read-only** support, and this first slice covers **Work detail pages and PDFs only** — Person, Concept, Position, Argument, and Playlist pages are not part of the offline cache yet and behave exactly as they do without any of this (a temporarily unreachable server surfaces their normal error state, not a cached read). Phase 1 also has no offline mutation queue.

Connectivity is decided by whether the PRKS server actually answers, never by the browser's own `navigator.onLine` guess: any real HTTP response (even an error status) means PRKS is reachable, and only a genuinely failed request (no transport response at all) means it is not. A probe starts immediately at app startup and again whenever the browser's `online`/`offline` hints fire. Ordinary PRKS `/api` traffic through `prksRequest()` also feeds that same reachability state, so a Work request that fails at the transport layer while the browser still thinks it is online will move PRKS to "Offline," and a later real HTTP response (or the bounded probe) will restore "Online." A browser that already thinks it's online but can't actually reach PRKS still ends up "Offline," and a browser that thinks it's offline but can reach PRKS still ends up "Online."

What works offline:

- The app shell itself launches on the very first offline attempt after the service worker has installed once — it does not need a second online page load first. Every core CSS/JS/font/icon file the ordinary shell needs to boot is precached eagerly on install from an explicit manifest in `sw.js` (checked against `index.html` by `tests/test_frontend_service_worker.py` so the two can't silently drift). The PDF-viewer bundle itself stays cache-on-first-PDF-use, since it is not needed just to launch the shell.
- A previously opened Work detail page reopens from an on-device cache.
- A previously fully opened PDF reopens and renders from cache, including page scrolling (the service worker slices the cached whole file for the viewer's normal range requests).
- Navigating between cached Works, including via the command palette and links inside research notes, works exactly like online navigation (`prksNavigate`) — there is no separate offline router.
- A shell-level connectivity pill appears ("Offline" / "Reconnecting…") using calm, non-destructive styling. A cached Work page also shows its own concise marker, e.g. *Offline · cached 18:42*. Cached data is never presented as if it were current.

What stays read-only or unavailable offline:

- Every canonical Work mutation — metadata save, delete, role/person links, folder/playlist/tag attach-or-remove, PDF annotation create/edit/delete — is blocked client-side with **"This change requires a connection to PRKS."** Nothing is silently discarded, faked as saved, or written only to the on-device cache.
- Research notes and private notes render their last-cached text but the editor starts out explicitly read-only from the moment it mounts ("Offline — notes are read-only"), even when you reopen a cached Work directly while already offline; they are not queued for later sync in this phase.
- A previously cached PDF reopened offline mounts the viewer in its own read-only preview mode: you can render, scroll, zoom, and navigate pages, but the highlight/underline/annotation toolbar is not available, and no annotation is created, edited, or deleted. If connectivity drops while a PDF is already open, the same mounted viewer is switched live into that read-only interaction mode and annotation persistence is paused, preserving any pending in-memory annotation state — the document is not torn down or remounted.
- A Work you never opened while online is not available offline: you see "This item is not available offline." rather than a false "not found." A real HTTP error from a reachable server (404, 403, 500, …) is never disguised as an offline/cache condition either way.
- Search/browse offline is limited to Works already cached on this device — there is no offline full-text search.

What is cached on the device, and how to clear it:

- The service worker caches the app shell and static JS/CSS/icons (Cache Storage) plus complete managed PDFs you have actually opened (keyed by the server's own file identity, e.g. ETag/Content-Length, so a replaced PDF is not served stale once you are back online).
- The app additionally keeps a small IndexedDB (`prks-offline-v1`) of the Work records above, each stamped with when it was cached.
- All of this is a **disposable client-side cache**, never another source of truth. The PRKS server's SQLite database and managed files remain canonical; deleting this browser storage never touches them.
- **Settings → Offline storage** shows cache availability, an approximate on-device size, and **Clear offline cache**, which only clears this browser's cache (never the server, database, or PDFs) and asks for a normal confirmation first.
- If the browser's IndexedDB is unavailable, blocked, or corrupted, PRKS keeps working online exactly as before — that only turns off the offline cache, never the live app.

When the server becomes reachable again, PRKS quietly moves from "Offline" to "Reconnecting…" to "Online," and only the page you are currently looking at is refreshed with the current server version (other open tabs refresh normally the next time you visit them — there is no request storm on reconnect).

## Research network

Concepts, Positions, and Arguments/Stances are persistent research records. They are not Work metadata.

Work↔Concept membership exists only because research notes (`works.text_content`) contain explicit `[[concept:Name]]` markup. Private notes never participate. Ordinary prose such as `Culture Industry` does not become a Concept; only `[[concept:Culture Industry]]` does. Unknown valid Concept names are created on note save. Concept records remain if every note reference is later removed.

Concept aliases/search keys resolve note references. A Concept identity rename keeps the old name as an alias so existing notes keep working. Capitalization-only display changes update the Concept name without adding an alias. Concepts may have multiple parents; hierarchy cycles are rejected.

Arguments/Stances use stable IDs in notes: `[[argument:A-123|Label]]`. Typing an unknown Argument ID does not create a record. Arguments record main Markdown text, source Works with page-range strings (Where made/taken), Position or Argument targets with an explicit verdict (Responds to), and derived reverse Counter/Response Arguments.

Positions are lightweight claim records used as Argument targets. Glossaries/Concept Senses and Debates/Theories are deliberately omitted in this version.

Note mentions live in a disposable derived index (`prks_research_index.db`), not in `prks_data.db`. Derived indexing failure never rolls back a valid note save. Concept and Argument deletion is guarded by canonical research notes, not by the derived index.

## Research Graph

`#/graph` is a read-only map of existing research relationships. It does not store edges of its own.

The graph shows:

- Concept hierarchy (`concept_parents`)
- Positions and the Arguments/Stances that support, oppose, qualify, or hold them
- Argument → Argument responses (the same directed target relation; no extra reverse edge)
- Argument source Works (where an Argument/Stance was made or taken)
- explicit research-note Concept and Argument references (`[[concept:…]]`, `[[argument:…]]`)
- optional Author links for Works already in the graph (`?people=1`)

The Graph is read-only. Editing relationships is done on their normal PRKS records.

A Work→Concept or Work→Argument edge means that Work's research notes contain explicit semantic markup. It does not mean the Work is objectively about that Concept, and it is not the same as an Argument source Work.

Graph node IDs are namespaced (`concept:C-…`, `position:P-…`, `person:P-…`) because raw PRKS IDs are not unique across record types. The graph is rebuilt on request from `prks_data.db` plus the derived research-reference index. There is no graph database and no schema migration for rendering.

## Saved Views

Run a search and choose **Save View**. Saved Views remember the search definition, not the current result list. Opening a Saved View always shows the files that match now.

Saved Views are stored in the main PRKS database and included in backups. They are also searchable/openable from Ctrl/Cmd+K.

A Saved View is not a snapshot of matching work IDs. If files, tags, authors, publishers, or PDF text change, the next time you open the view it re-runs the current search engine.

## Logging and privacy

Persistent log: `<storage>/prks-errors.log`. Default persistent threshold is **ERROR**. Rotation is daily at midnight. Retention is **7 days**.

PRKS logs describe operations and failures, not the contents of the research library. Increasing `PRKS_LOG_LEVEL` or `PRKS_LOG_FILE_LEVEL` (including `DEBUG`) changes volume, not privacy policy.

Logs may include:

- event names
- request IDs
- safe endpoint templates (`/api/search`, `/api/pdfs/:pdf`, `/api/works/:id`)
- HTTP status
- internal opaque IDs (`work_id`, `processing_file_id`)
- counts, page numbers, byte ranges, file sizes
- exception class names
- repository-relative traceback locations

PRKS deliberately does not log:

- research titles, notes, abstracts, annotations, or selected PDF text
- person, tag, folder, publisher, Concept, Position, or Argument names
- Concept aliases, definitions, Argument main text, verdict labels, or backlink snippets
- search terms
- PDF filenames or absolute filesystem paths
- source, portrait, or image URLs
- request bodies, query strings, or headers (`Host`, `Origin`, `User-Agent`, …)
- client/LAN IP addresses
- browser messages, stacks, routes, or hash state
- raw exception messages
- qpdf stderr

Docker captures process stderr. Console output follows the same privacy rules as the persistent file.

There is no remote telemetry. `POST /api/client-errors` is same-application metadata for correlating browser failures with server request IDs.

## Performance diagnostics

Use **Settings → Performance diagnostics** to see what is slow in this PRKS process.

Measurements live only in memory. They reset when PRKS restarts or when you click **Reset**. They are not written to disk, not included in backups, and contain aggregate operational metadata only: safe route templates, HTTP method/status, durations, counts, and response sizes. They never include search terms, query strings, request bodies, titles, notes, filenames, paths, SQL, or person names. The same Settings page also shows Client request coordinator counters (in-flight occupancy, retries, dedupe joins, burst-cache hits). Those are memory-only too and never include URLs, query strings, bodies, or entity ids. A copied report is safe to paste into a bug report.

The table lists API routes with:

- **Calls** — how often the route ran in this measurement window (frequency, not just latency)
- **Avg / P50 / P95 / Max** — duration in milliseconds. P50 and P95 are nearest-rank percentiles of the recent bounded sample (last 128 durations for that route), not a permanent historical distribution. Tiny samples are not statistically strong.
- **DB** — measured DB share: instrumented `execute_query()` time divided by request time. This is directional, not profiler-grade SQL accounting. Some direct SQLite work is not included.

Subsystem lines (PDF file stats, JSON encode, gzip, thumbnail render, PDF text search, text-index phases, and similar) use the same timing rules with fixed span names only. Text-index reconciliation reports `text_index_reconcile` plus `text_index_load_state`, `text_index_source_scan`, `text_index_extract`, `text_index_write`, and `text_index_fts_verify` when those phases ran.

Optional:

```bash
PRKS_PERF_SLOW_MS=150
PRKS_PERF_LOG_SLOW=1
```

Slow logs stay privacy-safe (`method`, templated `route`, status, durations, call counts, request id). They do not stop or timeout requests.

Do not add indexes, caching, threading, or SQLite tuning solely because an endpoint looks expensive on paper. Measure first.

## UI design

`DESIGN.md` is authoritative for PRKS visual and interaction work. New UI primitives must be specified there and shown in `tests/browser/design_system.html` before they are used in production. Do not treat a generic design skill as a license to replace Inter, round the chrome, or add decorative surfaces.

Open the gallery (both themes) from the fixture server:

```bash
python tests/browser/serve.py
```

Then open the printed origin’s `/tests/browser/design_system.html?theme=light` and `?theme=dark`.

## Development and tests

```bash
python run_tests.py          # unit/API/structural/Node (no Chromium)
python run_tests.py --e2e    # real Chromium + real PRKS server
python run_tests.py --all    # unit suite, then E2E
python run_tests.py --ux-tour                    # UX interaction tour (see below)
PRKS_UX_RECORD=1 python run_tests.py --ux-tour   # record every scenario for review
```

`-e2e`, `-all`, and `-ux-tour` are the same flags. Unflagged `python run_tests.py` discovers tests under `tests/` and does not launch Chromium. It always forces `PRKS_TESTING=1` and `PRKS_STORAGE` to the repo’s `data_testing/` directory and clears `PRKS_FOR_PROCESSING_DIR` and `PRKS_LOG_FILE`. That is stricter than `python prks_app.py --testing`, which may honor an explicit safe `PRKS_STORAGE`. Neither path uses `./data` or container `/data`. `--ux-tour` is a separate, explicitly opt-in suite: it never runs as part of the default, `--e2e`, or `--all` modes.

### UX Interaction Tour

`tests/ux_tour/` is a deliberately human-oriented, artifact-producing scenario suite -- longer than E2E, one recording per workflow (Workspace, Work/PDF, Library/Creation, Research/Graph, People/Groups, Organization/Progress, Settings, and shell navigation). It complements the fast E2E suite; it does not replace it. See `tests/ux_tour/COVERAGE.md` for what each tour covers and where everything else is exercised instead.

```bash
python run_tests.py --ux-tour
```

runs every tour against fresh isolated PRKS storage (same `tests.e2e.harness.AppServer` isolation as E2E) and produces one run directory per invocation:

```
artifacts/ux-tour/<run-id>/
├── manifest.json
├── REPORT.md
└── <scenario>/            # video.webm, trace.zip, checkpoints/, events.jsonl, server.log, browser-events.json
```

By default only a **failed** scenario keeps its heavy artifacts (video/trace/screenshots); a passing scenario's directory is deleted after its manifest entry is recorded. Set `PRKS_UX_RECORD=1` to keep everything and produce one upload-friendly archive:

```bash
PRKS_UX_RECORD=1 python run_tests.py --ux-tour
```

prints where the run directory and the `prks-ux-tour-<run-id>.zip` archive were written. `artifacts/ux-tour/` is gitignored -- these recordings are never committed.

### Browser tests

`python run_tests.py` is the Python/API/structural/Node suite. It does not launch Chromium.

Real Chromium against a real PRKS server:

```bash
python -m pip install -r requirements-dev.txt
python run_tests.py --e2e
```

`python tests/e2e/run.py` remains the direct E2E entry point. Before any browser download, the runner checks `importlib.metadata.version("playwright")` against the exact pin in `requirements-dev.txt` (`playwright==1.62.0`). A mismatch fails immediately. If the pin matches and Chromium is missing, it installs into repository-local `.playwright-browsers/` (gitignored). Later runs reuse that cache. `python tests/e2e/install_browser.py` is the same installer on its own. Test execution sets `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1` so Playwright cannot silently fetch browsers into the virtualenv or OS user cache.

Every E2E run creates a fresh temporary `PRKS_STORAGE` with `PRKS_TESTING=1`, binds `127.0.0.1`, and deletes that tree on teardown. It never targets `data/` or a live production storage directory.

Focused static fixtures (Markdown sanitizer, PDF viewer island, navigation, design system gallery) stay available without the full app:

```bash
python tests/browser/serve.py
python tests/browser/pointer_capture.py
```

`python tests/e2e/run.py` also automates the Markdown sanitizer fixture variants and runs the pointer-capture checks so those failures count.

## Project layout

| Path | Role |
| ---- | ---- |
| `DESIGN.md` | Authoritative UI visual/interaction contract. |
| `prks_app.py` | Only process entry: parses `--testing`, `--port`, `--host`, starts the server. |
| `backend/server.py` | Threaded stdlib HTTP server and handler: static frontend, REST-style `/api/...` routes. |
| `backend/concurrency.py` | Process-local library access gate (reads, mutations, backup, restore). |
| `backend/storage/config.py` | Frozen storage snapshot and env parser. |
| `backend/storage/paths.py` | Path derivation and testing-mode containment. |
| `backend/performance.py` | In-memory API/DB/span performance diagnostics. |
| `backend/db_manager.py` | SQLite access and business logic. |
| `backend/pdf_annotations.py` | Canonical PDF annotation normalize/validate/reconstruct. |
| `backend/db_migrations.py` | Ordered schema migrations and current-schema validation. |
| `backend/backup_restore.py` | Verified library backup and restore. |
| `backend/research_markup.py` | Authoritative `[[concept:]]` / `[[argument:]]` parser. |
| `backend/research_network.py` | Concept, Position, and Argument/Stance domain. |
| `backend/research_index.py` | Disposable derived note-reference index. |
| `backend/db_schema.sql` | Complete latest schema for fresh databases. |
| `frontend/` | Static SPA (HTML, CSS, JS), PWA assets. |
| `frontend/js/request-coordinator.js` | Client request coordinator for ordinary same-origin `/api` traffic. Memory-only; not offline support. |
| `frontend/js/offline-store.js` | Disposable IndexedDB client cache (entities/lists/metadata). No DOM, no routing, no connectivity policy. |
| `frontend/js/offline-runtime.js` | Online/offline/reconnecting state, read-through cache policy, mutation guard. No canonical persistence of its own. |
| `frontend/sw.js` | Service worker: app-shell/static-asset availability plus a focused managed-PDF cache. Never queues API mutations. |
| `data/` | Default production database and files (gitignored as appropriate). |
| `data_testing/` | Test fixtures and isolated DB/PDFs for automated tests. |
| `tests/` | `unittest` modules. |

## Security note

PRKS is a single-user app with **no built-in authentication**. Direct runs bind **127.0.0.1** by default. Docker Compose publishes the host port on **127.0.0.1** by default. Reaching it from another machine requires an explicit `--host` or `PRKS_PUBLISH_HOST` override. Do that only on a trusted network.

Local browser use through `http://127.0.0.1:8080` or `http://localhost:8080` works without extra Host configuration. LAN access by IP literal (after `PRKS_PUBLISH_HOST=0.0.0.0`) also needs no `PRKS_TRUSTED_HOSTS` setting.

Custom LAN DNS names must be listed exactly:

```bash
PRKS_PUBLISH_HOST=0.0.0.0 \
PRKS_TRUSTED_HOSTS=prks.home.arpa \
docker compose up -d
```

Malformed `PRKS_TRUSTED_HOSTS` entries refuse to start the server. This variable is for extra DNS hostnames on direct HTTP/LAN access, not reverse-proxy or HTTPS termination.

The HTTP adapter validates `Host` on every request, rejects cross-origin state-changing `/api/` requests when `Origin` is supplied (`Origin: null` included), and requires `application/json` for JSON POST/PATCH bodies. Missing `Origin` remains allowed for local scripts and non-browser clients. PRKS does not send CORS headers and does not allow cross-origin API access.

These controls reduce accidental/cross-origin access and DNS-rebinding risk. They are not authentication. Public Internet exposure is still unsafe.

Research notes (`works.text_content`) are stored as raw Markdown, including literal `[[concept:Name]]` and `[[argument:A-ID|Label]]` markup. A preprocessor turns recognized references into internal hash links, then EasyMDE/Marked renders Markdown, then a pinned local DOMPurify allowlist sanitizes the preview (`frontend/vendor/dompurify`, `frontend/js/markdown-sanitize.js`). Markup inside code spans/fences or escaped as `\[[` is not a semantic reference. Arbitrary or active HTML is not a supported contract: unsafe tags, attributes, and URL schemes are stripped from the preview only. Sanitization never rewrites saved Markdown.

Frontend libraries (Inter, EasyMDE, CodeMirror, Lucide, DOMPurify, the PDF viewer) are local files under `frontend/vendor/`. Node is not a runtime dependency. Docker does not run npm. To rebuild the PDF viewer after changing `tools/pdf-viewer/`:

```bash
cd tools/pdf-viewer
npm ci
npm run build
```

That writes `frontend/vendor/prks-pdf-viewer/` (EmbedPDF 2.15.0 + React 18.3.1, bundled). React is not part of the PRKS UI; it exists only inside that file. The PDF fixture is served by the same test-only server as the sanitizer fixture: open the printed `tests/browser/pdf_viewer.html` URL. It must report PASS with no jsDelivr / Google Fonts / unpkg requests.

The sanitizer-boundary browser fixture is not served by the app. From the repo root:

```bash
python tests/browser/serve.py
```

Open the printed `127.0.0.1` URL (and the `?dompurify=absent` / `?dompurify=unsupported` variants). The fixture must report PASS. Do not use production `data/` or a live `PRKS_STORAGE` tree for this check.
