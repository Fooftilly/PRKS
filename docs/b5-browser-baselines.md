# B5 browser performance / resource baselines (#454)

Regression baseline and resource-lifetime evidence for the finish-line Vue
cutover. **No pass/fail thresholds** are invented here — numbers are a
comparison point for later work.

Does **not** close #303 or #230. Does **not** mark B5 complete by itself.
Stacked on cleanup-only #453 (`cursor/b5-final-purge-228c`).

## Measurement identity

| Field | Value |
| --- | --- |
| Measured git HEAD | `12a2523bb6c739df3f25cde967f6f3fbc197a72a` |
| App | `python prks_app.py --testing --port 55231` |
| Storage | temp `PRKS_STORAGE` (never repo `data/` / live production tree); recreate with the harness |
| Client harness | Playwright Chromium channel=`chrome`, viewport 1400×900, headless |
| Playwright browser version | `148.0.7778.96` |
| User agent | `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/148.0.0.0 Safari/537.36` |
| OS | `Linux-6.12.94+-x86_64-with-glibc2.39` |
| Python | `3.12.3` |
| CPU | `Intel(R) Xeon(R) Processor` |
| Timing | leave `#/tags`, then `performance.now()` until focused-ctx generation bump + route root |
| Server diagnostics | `GET /api/diagnostics/performance` after client scenarios |
| Leak probes | global live Resize/Intersection/MutationObserver + EventTarget listener counts; `__prksResearchGraphLiveCount`; Work-card lazy-thumb tracked targets |
| Privacy | Synthetic titles only (`Synthetic Work …`, `Synthetic Library`, …) |

## Reproduce

```bash
python scripts/b5_browser_baselines.py --storage /tmp/prks-b5-baselines-454 \
  --output docs/b5-browser-baselines/browser-baselines.json \
  --markdown docs/b5-browser-baselines.md
```

Uses `python prks_app.py --testing` only. Seed procedure, settle predicates,
warmup policy, and raw samples live in the committed JSON next to this file.

## Testing-library shape

| Kind | Count / note |
| --- | --- |
| Works (browse) | 142 |
| Title-only batch | 120 in folder `Large Batch` |
| Tiny PDF batch | 20 in `Large Batch` |
| Named PDF works | `Synthetic Work A`, `Synthetic Work B` |
| Folders | Synthetic Library → child Large Batch |
| Persons | 2 |
| Concepts | 2 |
| Positions | 1 |
| Research notes | Work A notes with [[concept:Synthetic Concept One]] |
| Seed reused | True |

## Client scenario baselines (median ms unless noted)

| Scenario | Method | Result |
| --- | --- | --- |
| Initial client load | Navigation Timing on `/` | duration **202.2** ms; DCL **197.7** ms; FP **104.0** ms; FCP **104.0** ms; transfer 95728 |
| Route → Folder Library | leave to #/tags, prksNavigate, wait generation bump + route root | **30.4** ms; n=5; warmup dropped 1 |
| Route → Recent | leave to #/tags, prksNavigate, wait generation bump + route root | **18.2** ms; n=5; warmup dropped 1 |
| Route → Progress | leave to #/tags, prksNavigate, wait generation bump + route root | **47.0** ms; n=5; warmup dropped 1 |
| Route → People | leave to #/tags, prksNavigate, wait generation bump + route root | **16.5** ms; n=5; warmup dropped 1 |
| Route → Concepts | leave to #/tags, prksNavigate, wait generation bump + route root | **16.2** ms; n=5; warmup dropped 1 |
| Route → Graph chrome | leave to #/tags, prksNavigate, wait generation bump + route root | **26.7** ms; n=5; warmup dropped 1 |
| Route → Folder detail | leave to #/tags, prksNavigate, wait generation bump + route root | **32.6** ms; n=5; warmup dropped 1 |
| Tab switching | activateTab between parked Folders and Recent; settle on destination view root | **18.2** ms; n=6; warmup dropped 1 |
| Main/Secondary split open | prksNavigate(folder-detail, {target:'tile'}) from a single Main pane | **49.0** ms; n=1 |
| Secondary nav while split | prksNavigate(people/concepts, {target:'tile'}); settle on secondary route name + generation | **18.8** ms; n=3; warmup dropped 1 |
| Large folder collection | leave to #/tags, prksNavigate, wait generation bump + route root | **72.3** ms; n=5; warmup dropped 1; 140 cards |
| Search `Batch` | leave to #/tags, prksNavigate, wait generation bump + route root | **53.7** ms; n=5; warmup dropped 1 |
| PDF open (cold) | first prksNavigate(Work A) until focused ctx work/pdf.workId match and a page under ctx.root | **472.3** ms; n=1; initCounts {'createPrksPdfViewer': 1, 'createWorkPdfRuntime': 1, 'initPdfViewerForWork': 1} |
| PDF cold reopen after folders | reopen Work A after folders — cold route replacement, not warm resume | **431.4** ms; n=3; label: cold reopen |
| PDF warm resume (`prksResumeWarmTabContext`) | warm-park via activateTab(folders) then prksResumeWarmTabContext via activateTab(Work A) | **24.5** ms; n=5; warmup dropped 1 |
| PDF close (cold unmount) | navigate away to folders (cold unmount of current route) | **34.1** ms; n=3; warmup dropped 1 |
| Work A→B (dest work/pdf.workId) | hash A↔B until focused ctx work id + pdf.workId match dest and a page exists under ctx.root | **432.9** ms; n=6 |
| Research Notes mount | leave tags, open Work A until notes/EasyMDE settle | **442.3** ms; n=3; warmup dropped 1 |
| Research Graph mount (cy) | leave to #/tags, prksNavigate, wait generation bump + route root | **51.0** ms; n=5; warmup dropped 1 |

Warm-resume invariants (measured rows, not warmup):

```json
{
  "createRuntimeDeltaZero": true,
  "createViewerDeltaZero": true,
  "pdfRequestDeltaZero": true,
  "samePdfRuntime": true,
  "samePdfViewer": true,
  "sameTabContext": true,
  "viewerInitDeltaZero": true,
  "viewerSetupTokenUnchanged": true,
  "workDetailGetDeltaZero": true
}
```

## Repeated mount / resource lifetime

10 cycles: Large Batch folder → Work A → Graph → Recent; Pre/Post taken on folder then graph after a generation-bumped paint

Global live Resize/Intersection/MutationObserver and EventTarget listener counts; __prksResearchGraphLiveCount; lazy-thumb tracked targets. Focused-runtime debug fields are not leak evidence.

These tables record **absolute** Pre/Post counts on the named surface after
a generation-bumped paint. Unchanged Δ is evidence only for the listed probes.
It does **not** claim that every listener or observer in the process was
released. Focused-runtime graph `debug()` fields (`resizeObserverLive`,
`chromeListenerCount`) describe the *current* mount and are omitted from the
leak table. `eventListenerLive` is a page-wide add/remove net from wrapping
`EventTarget.prototype`; a rising count is recorded here but is **not** treated
as proof that route-owned listeners leaked, and a flat count would still not
prove that every listener was released.

### Folder surface (Large Batch)

| Probe | Pre | Post | Δ |
| --- | --- | --- | --- |
| `canvases` | 0 | 0 | 0 |
| `cleanupCount` | 4 | 4 | 0 |
| `domNodes` | 3476 | 3476 | 0 |
| `easyMde` | 0 | 0 | 0 |
| `eventListenerLive` | 6407 | 8530 | 2123 |
| `intersectionObserverLive` | 1 | 1 | 0 |
| `mutationObserverLive` | 1 | 1 | 0 |
| `pdfHosts` | 0 | 0 | 0 |
| `researchGraphLive` | 0 | 0 | 0 |
| `resizeObserverLive` | 3 | 3 | 0 |
| `thumbObserverPresent` | True | True | 0 |
| `thumbObservingAttr` | 14 | 14 | 0 |
| `thumbTrackedConnected` | 14 | 14 | 0 |
| `thumbTrackedDisconnected` | 0 | 0 | 0 |
| `thumbTrackedTargets` | 14 | 14 | 0 |
| `timerCount` | 0 | 0 | 0 |

### Graph surface (`#/graph`)

| Probe | Pre | Post | Δ |
| --- | --- | --- | --- |
| `canvases` | 3 | 3 | 0 |
| `cleanupCount` | 1 | 1 | 0 |
| `domNodes` | 1589 | 1589 | 0 |
| `easyMde` | 0 | 0 | 0 |
| `eventListenerLive` | 6497 | 8620 | 2123 |
| `intersectionObserverLive` | 1 | 1 | 0 |
| `mutationObserverLive` | 3 | 3 | 0 |
| `pdfHosts` | 0 | 0 | 0 |
| `researchGraphLive` | 1 | 1 | 0 |
| `resizeObserverLive` | 4 | 4 | 0 |
| `thumbObserverPresent` | True | True | 0 |
| `thumbObservingAttr` | 0 | 0 | 0 |
| `thumbTrackedConnected` | 0 | 0 | 0 |
| `thumbTrackedDisconnected` | 0 | 0 | 0 |
| `thumbTrackedTargets` | 0 | 0 | 0 |
| `timerCount` | 0 | 0 | 0 |

## Server diagnostics after client scenarios

```json
{
  "counters": {
    "db_read": 637,
    "db_write": 0,
    "pdf_file_stat_files": 1554,
    "pdf_file_stat_rows": 9594,
    "thumbnail_cache_hits": 17,
    "thumbnail_cache_misses": 0
  },
  "measured_for_seconds": 30,
  "process_started_at": 1791180431.1079862,
  "requests": {
    "response_bytes": 999691,
    "slow": 0,
    "total": 723
  },
  "routes": [
    {
      "avg_db_ms": 1.7,
      "avg_ms": 7.3,
      "avg_response_bytes": null,
      "count": 17,
      "db_calls": 17,
      "db_calls_avg": 1.0,
      "max_ms": 17.6,
      "measured_db_share_percent": 23.9,
      "method": "GET",
      "p50_ms": 5.9,
      "p95_ms": 17.6,
      "route": "/api/works/:id/thumbnail",
      "slow_count": 0,
      "status_4xx": 0,
      "status_5xx": 0
    },
    {
      "avg_db_ms": 12.1,
      "avg_ms": 13.7,
      "avg_response_bytes": 8361,
      "count": 6,
      "db_calls": 30,
      "db_calls_avg": 5.0,
      "max_ms": 14.2,
      "measured_db_share_percent": 88.3,
      "method": "GET",
      "p50_ms": 13.5,
      "p95_ms": 14.2,
      "route": "/api/search",
      "slow_count": 0,
      "status_4xx": 0,
      "status_5xx": 0
    },
    {
      "avg_db_ms": 0.0,
      "avg_ms": 9.4,
      "avg_response_bytes": 850,
      "count": 36,
      "db_calls": 0,
      "db_calls_avg": 0.0,
      "max_ms": 13.9,
      "measured_db_share_percent": 0.0,
      "method": "POST",
      "p50_ms": 8.9,
      "p95_ms": 12.8,
      "route": "/api/:unknown",
      "slow_count": 0,
      "status_4xx": 0,
      "status_5xx": 0
    },
    {
      "avg_db_ms": 10.6,
      "avg_ms": 11.5,
      "avg_response_bytes": 766,
      "count": 36,
      "db_calls": 312,
      "db_calls_avg": 8.7,
      "max_ms": 17.2,
      "measured_db_share_percent": 92.2,
      "method": "GET",
      "p50_ms": 11.6,
      "p95_ms": 12.4,
      "route": "/api/works/:id",
      "slow_count": 0,
      "status_4xx": 0,
      "status_5xx": 0
    },
    {
      "avg_db_ms": 7.0,
      "avg_ms": 9.0,
      "avg_response_bytes": 11491,
      "count": 43,
      "db_calls": 43,
      "db_calls_avg": 1.0,
      "max_ms": 17.9,
      "measured_db_share_percent": 77.0,
      "method": "GET",
      "p50_ms": 8.9,
      "p95_ms": 10.0,
      "route": "/api/works",
      "slow_count": 0,
      "status_4xx": 0,
      "status_5xx": 0
    },
    {
      "avg_db_ms": 6.4,
      "avg_ms": 7.4,
      "avg_response_bytes": 6160,
      "count": 26,
      "db_calls": 52,
      "db_calls_avg": 2.0,
      "max_ms": 9.8,
      "measured_db_share_percent": 86.1,
      "method": "GET",
      "p50_ms": 8.8,
      "p95_ms": 9.5,
      "route": "/api/folders/:id",
      "slow_count": 0,
      "status_4xx": 0,
      "status_5xx": 0
    },
    {
      "avg_db_ms": 3.8,
      "avg_ms": 4.0,
      "avg_response_bytes": 2,
      "count": 41,
      "db_calls": 82,
      "db_calls_avg": 2.0,
      "max_ms": 6.7,
      "measured_db_share_percent": 93.7,
      "method": "GET",
      "p50_ms": 4.1,
      "p95_ms": 5.9,
      "route": "/api/tags",
      "slow_count": 0,
      "status_4xx": 0,
      "status_5xx": 0
    },
    {
      "avg_db_ms": 0.0,
      "avg_ms": 3.6,
      "avg_response_bytes": 87,
      "count": 26,
      "db_calls": 0,
      "db_calls_avg": 0.0,
      "max_ms": 6.0,
      "measured_db_share_percent": 0.0,
      "method": "GET",
      "p50_ms": 3.5,
      "p95_ms": 5.5,
      "route": "/api/:unknown",
      "slow_count": 0,
      "status_4xx": 0,
      "status_5xx": 0
    },
    {
      "avg_db_ms": 2.2,
      "avg_ms": 2.5,
      "avg_response_bytes": 529,
      "count": 20,
      "db_calls": 20,
      "db_calls_avg": 1.0,
      "max_ms": 3.8,
      "measured_db_share_percent": 86.3,
      "method": "GET",
      "p50_ms": 2.4,
      "p95_ms": 2.9,
      "route": "/api/recent",
      "slow_count": 0,
      "status_4xx": 0,
      "status_5xx": 0
    },
    {
      "avg_db_ms": 0.0,
      "avg_ms": 1.7,
      "avg_response_bytes": 184,
      "count": 72,
      "db_calls": 0,
      "db_calls_avg":
```

## Follow-ups

- No tightly coupled migration defect is opened from this measurement pass.
- Known unrelated Full E2E flake: #383 (private-reminder hide→re-tile).
- Broader slowness is not turned into a threshold here.

## Raw harness output

Committed default capture: `docs/b5-browser-baselines/browser-baselines.json`.
Regenerate with `scripts/b5_browser_baselines.py` (this document is emitted from that JSON).
