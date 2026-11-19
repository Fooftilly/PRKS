# B5 comparison after the Vue consolidation (#1033)

This note compares two runs of `scripts/b5_browser_baselines.py`:

- The previous committed baseline, at `59d65a2`.
- The run at `02845f1`, which is master after #1324 retired `prksCreateForms`.

The new run replaced `docs/b5-browser-baselines.md` and
`browser-baselines.json` in the same change. This file is written by hand, so
the harness leaves it alone when it regenerates those two files.

Both runs used Playwright Chromium 153.0.8010.12, headless, at a 1400×900
viewport, on the same CPU class. Each run happened once, on one machine, and
the harness takes a different number of samples for each scenario. The table
gives each scenario's sample count, its median and its range (min to max). The
harness drops a warmup sample before measuring where the table says so.

## Timings

| Scenario | Samples (warmup dropped) | 59d65a2 median (range), ms | 02845f1 median (range), ms | Change | Ranges overlap? |
| --- | --- | ---: | ---: | ---: | --- |
| `initialLoad` | 1 (0) | 510.2 | 544.3 | +7% | n/a (one sample) |
| `largeFolderCollection` | 5 (1) | 181.3 (165–215) | 120.3 (104–140) | -34% | no |
| `pdfClose` | 3 (1) | 67.9 (56–74) | 57.4 (49–65) | -15% | yes |
| `pdfColdReopenAfterFolders` | 3 (0) | 602.3 (585–672) | 508.8 (503–518) | -16% | no |
| `pdfOpenCold` | 1 (0) | 736.4 | 642.9 | -13% | n/a (one sample) |
| `pdfWarmResume` | 5 (1) | 58.8 (52–83) | 43.3 (30–60) | -26% | yes |
| `researchGraphMount` | 5 (1) | 123.9 (94–238) | 73.6 (62–86) | -41% | no |
| `researchNotesMount` | 3 (1) | 639.7 (620–720) | 525.0 (517–531) | -18% | no |
| `routeConcepts` | 5 (1) | 37.1 (35–42) | 32.5 (30–33) | -12% | no |
| `routeFolderDetail` | 5 (1) | 70.5 (67–81) | 71.0 (56–79) | +1% | yes |
| `routeFolderLibrary` | 5 (1) | 81.1 (58–92) | 60.6 (56–70) | -25% | yes |
| `routeGraphChrome` | 5 (1) | 97.7 (90–105) | 33.4 (28–49) | -66% | no |
| `routePeople` | 5 (1) | 32.7 (29–37) | 31.7 (28–44) | -3% | yes |
| `routeProgress` | 5 (1) | 105.9 (89–346) | 74.0 (70–257) | -30% | yes |
| `routeRecent` | 5 (1) | 40.2 (35–43) | 34.2 (33–43) | -15% | yes |
| `searchBatch` | 5 (1) | 113.1 (109–144) | 97.6 (82–102) | -14% | no |
| `secondaryNavWhileSplit` | 3 (1) | 52.9 (43–55) | 51.3 (46–56) | -3% | yes |
| `splitOpen` | 1 (0) | 141.6 | 127.8 | -10% | n/a (one sample) |
| `tabSwitch` | 6 (1) | 46.4 (35–54) | 37.8 (33–40) | -19% | yes |
| `workAToB` | 6 (0) | 609.8 (550–635) | 538.2 (498–570) | -12% | yes |

Read the rows by their sample count:

- **One sample** (`initialLoad`, `pdfOpenCold`, `splitOpen`): these are single
  observations. One run cannot estimate their variance, so they support no
  conclusion either way. That includes the +34 ms on `initialLoad`.
- **Three or more samples where the two ranges do not overlap**: every sample
  in the new run is faster than every sample in the old one. That holds for
  `largeFolderCollection`, `pdfColdReopenAfterFolders`, `researchGraphMount`,
  `researchNotesMount`, `routeConcepts`, `routeGraphChrome` and `searchBatch`.
  Three samples is still thin, so treat the three-sample rows as weaker
  evidence than the five-sample ones.
- **Ranges that overlap**: these show no clear change.

Among the scenarios with more than one sample, none shows a slowdown. The only
higher median is `routeFolderDetail` (+1%), and its ranges overlap. These
results do not show that the Vue migration is a speedup in general, and the
one-sample scenarios would need repeated runs before anyone draws a conclusion
from them.

The `initialLoad` navigation transfer size fell from 95.7 kB to 13.0 kB,
because `index.html` shrank from 95 kB to 13 kB as markup moved into Vue.

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
