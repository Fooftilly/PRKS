# B5 comparison after the Vue consolidation (#1033)

This note compares two runs of `scripts/b5_browser_baselines.py`:

- The previous committed baseline, at `59d65a2`.
- The run at `02845f1`, which is master after #1324 retired `prksCreateForms`.

The new run replaced `docs/b5-browser-baselines.md` and
`browser-baselines.json` in the same change. This file is written by hand, so
the harness leaves it alone when it regenerates those two files.

Both runs used Playwright Chromium 153.0.8010.12, headless, at a 1400×900
viewport, on the same CPU class. Each timing is the harness median of five
samples, after it drops the warmup. These numbers come from one machine, so read
small differences (under about 10%) as noise.

## Timings

| Scenario | 59d65a2 (ms) | 02845f1 (ms) | Change |
| --- | ---: | ---: | ---: |
| `initialLoad` (duration) | 510 | 544 | +7% |
| `largeFolderCollection` | 181.3 | 120.3 | -34% |
| `pdfClose` | 67.9 | 57.4 | -15% |
| `pdfColdReopenAfterFolders` | 602.3 | 508.8 | -16% |
| `pdfOpenCold` | 736.4 | 642.9 | -13% |
| `pdfWarmResume` | 58.8 | 43.3 | -26% |
| `researchGraphMount` | 123.9 | 73.6 | -41% |
| `researchNotesMount` | 639.7 | 525.0 | -18% |
| `routeConcepts` | 37.1 | 32.5 | -12% |
| `routeFolderDetail` | 70.5 | 71.0 | +1% |
| `routeFolderLibrary` | 81.1 | 60.6 | -25% |
| `routeGraphChrome` | 97.7 | 33.4 | -66% |
| `routePeople` | 32.7 | 31.7 | -3% |
| `routeProgress` | 105.9 | 74.0 | -30% |
| `routeRecent` | 40.2 | 34.2 | -15% |
| `searchBatch` | 113.1 | 97.6 | -14% |
| `secondaryNavWhileSplit` | 52.9 | 51.3 | -3% |
| `splitOpen` | 141.6 | 127.8 | -10% |
| `tabSwitch` | 46.4 | 37.8 | -19% |
| `workAToB` | 609.8 | 538.3 | -12% |

No measured scenario got slower beyond noise. Initial load is 34 ms slower,
which is within run-to-run variation. The navigation transfer size fell from
95.7 kB to 13.0 kB because `index.html` shrank from 95 kB to 13 kB as markup
moved into Vue.

## Resource lifetimes (10 cycles: folder, Work, graph, Recent)

| Probe | 59d65a2 pre → post | 02845f1 pre → post |
| --- | --- | --- |
| Folder: DOM nodes | 3476 → 3476 | 3592 → 3592 |
| Folder: long-lived listeners | 80 → 80 | 90 → 90 |
| Folder: live MutationObservers | 1 → 1 | **49 → 71** |
| Graph: DOM nodes | 1589 → 1589 | 1706 → 1706 |
| Graph: long-lived listeners | 102 → 102 | 112 → 112 |
| Graph: live MutationObservers | 3 → 3 | **51 → 73** |
| Resize / Intersection observers | flat | flat |
| Graph runtimes, PDF hosts, timers, lazy thumbs | flat | flat |

The DOM node and listener counts are higher than before, but they stay flat
across the cycles. The extra nodes and listeners belong to the Vue dialogs that
are always mounted.

The live MutationObserver count is the one probe that grows across cycles: two
more per folder visit. A diagnostic run that recorded each observer's creation
stack traced all of the growth to the classic folder tag picker:

- `folder-tag-editor.js` `bindPicker` calls `prksBindComboboxKeys` every time
  the folder panel renders a fresh `#folder-tag-search` input.
- `combobox-keys.js` creates two MutationObservers for each new input and never
  disconnects them.

`combobox-keys.js` was added on 2026-10-31, after the previous baseline, so this
growth comes from that change and not from the Vue migration. The observers
watch elements that a re-render has already removed from the page, so they can
most likely be garbage-collected. This has not been proved with a heap snapshot,
and the probe counts them as live because nothing calls `disconnect()`. The
rest of the 49 at the start of the run is bounded and does not grow:

- 32 come from the same folder tag picker during the first folder visits.
- 14 come from the Vue create forms' comboboxes, which mount once.
- The others come from Cytoscape and the Playwright injected script.

Follow-up: disconnect the combobox observers when the picker's resource is
disposed (#1327).

## Server diagnostics

| Counter | 59d65a2 | 02845f1 |
| --- | ---: | ---: |
| `db_read` | 1005 | 2050 |
| `db_write` | 284 | 1819 |

The previous baseline ran on the SQLite library. This run uses PostgreSQL, with
epoch admission and the durable sync queue, so these server counters are not
comparable across the two runs. The new run also reports some requests under
`/api/:unknown`, because `backend/log_safety.py` `safe_route` does not yet name
the newer route families. That is a gap in the labels only; the requests
themselves succeeded.

One of the 24 `GET /api/research-graph` calls is counted as a 5xx. The server
log shows a `BrokenPipeError` in `send_json`: the harness navigated away while
the response was still being written, so the browser closed the connection.
The same thing happened in a second run. The graph query did not fail. The
server does count a client disconnect as an unhandled error, though.

## Harness fixes in this change

The harness no longer ran against current master. Its write requests were
refused with `origin_not_allowed` (403) and `LIBRARY_EPOCH_REQUIRED` (400). Now
`_http_json` sends an `Origin` header and the library epoch on every write, and
reads the epoch from `/api/session` the first time it needs one.

To reproduce, point `PRKS_DATABASE_URL` at a fresh database whose comment is
`prks-testing`.
