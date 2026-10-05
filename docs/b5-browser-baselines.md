# B5 browser performance / resource baselines (#454)

Regression baseline and resource-lifetime evidence for the finish-line Vue
cutover. **No pass/fail thresholds** are invented here — numbers are a
comparison point for later work.

Does **not** close #303 or #230. Does **not** mark B5 complete by itself.
Stacked on cleanup-only #453 (`cursor/b5-final-purge-228c`).

## Measurement identity

| Field | Value |
| --- | --- |
| Measured git HEAD | `2467e1e2a2b4bff1e388cbbf8ae08af3d81dcf23` |
| App | `python prks_app.py --testing --port 46569` |
| Storage | temp `PRKS_STORAGE` (never repo `data/` / live production tree); recreate with the harness |
| Client harness | Playwright Chromium channel=`chrome`, viewport 1400×900, headless |
| Playwright browser version | `148.0.7778.96` |
| User agent | `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/148.0.0.0 Safari/537.36` |
| OS | `Linux-6.12.94+-x86_64-with-glibc2.39` |
| Python | `3.12.3` |
| CPU | `Intel(R) Xeon(R) Processor` |
| Timing | leave `#/tags`, then `performance.now()` until focused-ctx generation bump + route root |
| Server diagnostics | `GET /api/diagnostics/performance` after client scenarios |
| Leak probes | global live Resize/Intersection/MutationObserver; long-lived listeners on window/document/body/shell/tile/tab-root; `__prksResearchGraphLiveCount`; Work-card lazy-thumb tracked targets |
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
| Initial client load | Navigation Timing on `/` | duration **210.3** ms; DCL **206.6** ms; FP **56.0** ms; FCP **56.0** ms; transfer 95728 |
| Route → Folder Library | leave to #/tags, prksNavigate, wait generation bump + route root | **27.4** ms; n=5; warmup dropped 1 |
| Route → Recent | leave to #/tags, prksNavigate, wait generation bump + route root | **18.2** ms; n=5; warmup dropped 1 |
| Route → Progress | leave to #/tags, prksNavigate, wait generation bump + route root | **46.1** ms; n=5; warmup dropped 1 |
| Route → People | leave to #/tags, prksNavigate, wait generation bump + route root | **15.2** ms; n=5; warmup dropped 1 |
| Route → Concepts | leave to #/tags, prksNavigate, wait generation bump + route root | **15.5** ms; n=5; warmup dropped 1 |
| Route → Graph chrome | leave to #/tags, prksNavigate, wait generation bump + route root | **46.3** ms; n=5; warmup dropped 1 |
| Route → Folder detail | leave to #/tags, prksNavigate, wait generation bump + route root | **30.2** ms; n=5; warmup dropped 1 |
| Tab switching | activateTab between parked Folders and Recent; settle on destination view root | **18.9** ms; n=6; warmup dropped 1 |
| Main/Secondary split open | prksNavigate(folder-detail, {target:'tile'}); settle on Secondary lastResolvedRoute folder-detail + view root | **50.0** ms; n=1 |
| Secondary nav while split | in-place prksNavigate(person-detail/concept-detail, {tabId: secondary}); people/concepts indexes are not tile-capable. Settle on Secondary lastResolvedRoute + view root | **23.2** ms; n=3; warmup dropped 1 |
| Large folder collection | leave to #/tags, prksNavigate, wait generation bump + route root | **79.7** ms; n=5; warmup dropped 1; 140 cards |
| Search `Batch` | leave to #/tags, prksNavigate, wait generation bump + route root | **53.3** ms; n=5; warmup dropped 1 |
| PDF open (cold) | first prksNavigate(Work A) until focused ctx work/pdf.workId match and a page under ctx.root | **475.4** ms; n=1 |
| PDF cold reopen after folders | reopen Work A after folders — cold route replacement, not warm resume | **436.5** ms; n=3; label: cold reopen |
| PDF warm resume (`prksResumeWarmTabContext`) | warm-park via activateTab(folders) then prksResumeWarmTabContext via activateTab(Work A) | **24.0** ms; n=5; warmup dropped 1 |
| PDF close (cold unmount) | navigate away to folders (cold unmount of current route) | **29.8** ms; n=3; warmup dropped 1 |
| Work A→B (dest work/pdf.workId) | hash A↔B until focused ctx work id + pdf.workId match dest and a page exists under ctx.root | **432.1** ms; n=6 |
| Research Notes mount | leave tags, open Work A until notes/EasyMDE settle | **445.0** ms; n=3; warmup dropped 1 |
| Research Graph mount (cy) | leave to #/tags, prksNavigate, wait generation bump + route root | **23.8** ms; n=5; warmup dropped 1 |

Warm-resume invariants (measured rows, not warmup):

```json
{
  "pdfRequestDeltaZero": true,
  "samePdfRuntime": true,
  "samePdfViewer": true,
  "sameTabContext": true,
  "viewerSetupTokenUnchanged": true,
  "workDetailGetDeltaZero": true
}
```

## Repeated mount / resource lifetime

10 cycles: Large Batch folder → Work A → Graph → Recent; Pre/Post taken on folder then graph after a generation-bumped paint

Global live Resize/Intersection/MutationObserver counts; long-lived EventTarget listeners on window/document/body/shell/tile/tab-root only (WeakRef map; once/abort release); __prksResearchGraphLiveCount; lazy-thumb tracked targets. Focused-runtime graph debug fields are not leak evidence. Listener accumulation on discarded route nodes is not counted.

These tables record **absolute** Pre/Post counts on the named surface after
a generation-bumped paint. Unchanged Δ is evidence only for the listed probes.
`longLivedListenerLive` counts registrations on `window` / `document` /
`document.body` / persistent shell, tile, and tab-root hosts, with `{once}` and
`AbortSignal` release. It is not a global add-minus-remove counter. Focused-runtime
graph `debug()` fields (`resizeObserverLive`, `chromeListenerCount`) describe the
*current* mount and are omitted from the leak table. Listener accumulation on
discarded route-owned nodes is outside this probe.

### Folder surface (Large Batch)

| Probe | Pre | Post | Δ |
| --- | --- | --- | --- |
| `canvases` | 0 | 0 | 0 |
| `cleanupCount` | 4 | 4 | 0 |
| `domNodes` | 3476 | 3476 | 0 |
| `easyMde` | 0 | 0 | 0 |
| `intersectionObserverLive` | 1 | 1 | 0 |
| `longLivedListenerLive` | 292 | 344 | 52 |
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
| `intersectionObserverLive` | 1 | 1 | 0 |
| `longLivedListenerLive` | 315 | 367 | 52 |
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
    "db_read": 644,
    "db_write": 0,
    "pdf_file_stat_files": 1554,
    "pdf_file_stat_rows": 9594,
    "thumbnail_cache_hits": 17,
    "thumbnail_cache_misses": 0
  },
  "measured_for_seconds": 30,
  "process_started_at": 1791181458.2537045,
  "requests": {
    "response_bytes": 1001131,
    "slow": 0,
    "total": 729
  },
  "routes": [
    {
      "avg_db_ms": 1.7,
      "avg_ms": 6.2,
      "avg_response_bytes": null,
      "count": 17,
      "db_calls": 17,
      "db_calls_avg": 1.0,
      "max_ms": 20.1,
      "measured_db_share_percent": 26.8,
      "method": "GET",
      "p50_ms": 4.8,
      "p95_ms": 20.1,
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
      "max_ms": 14.8,
      "measured_db_share_percent": 88.7,
      "method": "GET",
      "p50_ms": 13.5,
      "p95_ms": 14.8,
      "route": "/api/search",
      "slow_count": 0,
      "status_4xx": 0,
      "status_5xx": 0
    },
    {
      "avg_db_ms": 0.0,
      "avg_ms": 9.2,
      "avg_response_bytes": 850,
      "count": 36,
      "db_calls": 0,
      "db_calls_avg": 0.0,
      "max_ms": 13.8,
      "measured_db_share_percent": 0.0,
      "method": "POST",
      "p50_ms": 8.7,
      "p95_ms": 12.9,
      "route": "/api/:unknown",
      "slow_count": 0,
      "status_4xx": 0,
      "status_5xx": 0
    },
    {
      "avg_db_ms": 10.4,
      "avg_ms": 11.2,
      "avg_response_bytes": 766,
      "count": 36,
      "db_calls": 312,
      "db_calls_avg": 8.7,
      "max_ms": 12.9,
      "measured_db_share_percent": 92.7,
      "method": "GET",
      "p50_ms": 11.4,
      "p95_ms": 12.0,
      "route": "/api/works/:id",
      "slow_count": 0,
      "status_4xx": 0,
      "status_5xx": 0
    },
    {
      "avg_db_ms": 6.2,
      "avg_ms": 7.2,
      "avg_response_bytes": 6161,
      "count": 26,
      "db_calls": 52,
      "db_calls_avg": 2.0,
      "max_ms": 9.3,
      "measured_db_share_percent": 86.4,
      "method": "GET",
      "p50_ms": 8.6,
      "p95_ms": 9.2,
      "route": "/api/folders/:id",
      "slow_count": 0,
      "status_4xx": 0,
      "status_5xx": 0
    },
    {
      "avg_db_ms": 6.8,
      "avg_ms": 8.8,
      "avg_response_bytes": 11491,
      "count": 43,
      "db_calls": 43,
      "db_calls_avg": 1.0,
      "max_ms": 17.8,
      "measured_db_share_percent": 77.0,
      "method": "GET",
      "p50_ms": 8.7,
      "p95_ms": 9.0,
      "route": "/api/works",
      "slow_count": 0,
      "status_4xx": 0,
      "status_5xx": 0
    },
    {
      "avg_db_ms": 4.9,
      "avg_ms": 5.2,
      "avg_response_bytes": 352,
      "count": 2,
      "db_calls": 2,
      "db_calls_avg": 1.0,
      "max_ms": 8.9,
      "measured_db_share_percent": 93.2,
      "method": "GET",
      "p50_ms": 1.6,
      "p95_ms": 8.9,
      "route": "/api/settings",
      "slow_count": 0,
      "status_4xx": 0,
      "status_5xx": 0
    },
    {
      "avg_db_ms": 3.7,
      "avg_ms": 4.0,
      "avg_response_bytes": 2,
      "count": 42,
      "db_calls": 84,
      "db_calls_avg": 2.0,
      "max_ms": 6.4,
      "measured_db_share_percent": 94.2,
      "method": "GET",
      "p50_ms": 4.0,
      "p95_ms": 5.4,
      "route": "/api/tags",
      "slow_count": 0,
      "status_4xx": 0,
      "status_5xx": 0
    },
    {
      "avg_db_ms": 0.0,
      "avg_ms": 3.6,
      "avg_response_bytes": 87,
      "count": 27,
      "db_calls": 0,
      "db_calls_avg": 0.0,
      "max_ms": 6.5,
      "measured_db_share_percent": 0.0,
      "method": "GET",
      "p50_ms": 4.1,
      "p95_ms": 5.2,
      "route": "/api/:unknown",
      "slow_count": 0,
      "status_4xx": 0,
      "status_5xx": 0
    },
    {
      "avg_db_ms": 2.2,
      "avg_ms": 2.5,
      "avg_response_bytes": 528,
      "count": 20,
      "db_calls": 20,
      "db_calls_avg"
```

## Follow-ups

- Long-lived EventTarget listener growth on persistent hosts is tracked in #459.
- Known unrelated Full E2E flake: #383 (private-reminder hide→re-tile).
- Broader slowness is not turned into a threshold here.

## Raw harness output

Committed default capture: `docs/b5-browser-baselines/browser-baselines.json`.
Regenerate with `scripts/b5_browser_baselines.py` (this document is emitted from that JSON).
