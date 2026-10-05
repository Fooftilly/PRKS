# B5 browser performance / resource baselines (#454)

Regression baseline and resource-lifetime evidence for the finish-line Vue
cutover. **No pass/fail thresholds** are invented here — numbers are a
comparison point for later work.

Does **not** close #303 or #230. Does **not** mark B5 complete by itself.
Stacked on cleanup-only #453 (`cursor/b5-final-purge-228c`).

## Measurement identity

| Field | Value |
| --- | --- |
| Measured git HEAD | `8988c69eb4c89d1a0fce1f9c66443565263da727` |
| App | `python prks_app.py --testing --port 59605` |
| Storage | temp `PRKS_STORAGE` (never repo `data/` / live production tree); recreate with the harness |
| Client harness | Playwright Chromium channel=`chrome`, viewport 1400×900, headless |
| Playwright browser version | `148.0.7778.96` |
| User agent | `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/148.0.0.0 Safari/537.36` |
| OS | `Linux-6.12.94+-x86_64-with-glibc2.39` |
| Python | `3.12.3` |
| CPU | `Intel(R) Xeon(R) Processor` |
| Timing | leave `#/tags`, then `performance.now()` until focused-ctx generation bump + route root |
| Server diagnostics | `GET /api/diagnostics/performance` after client scenarios |
| Leak probes | global live Resize/Intersection/MutationObserver; long-lived listeners on connected window/document/body/shell/tile/tab-root hosts; `__prksResearchGraphLiveCount`; Work-card lazy-thumb tracked targets |
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
| Initial client load | Navigation Timing on `/` | duration **205.1** ms; DCL **201.7** ms; FP **108.0** ms; FCP **108.0** ms; transfer 95728 |
| Route → Folder Library | leave to #/tags, prksNavigate, wait generation bump + route root | **25.9** ms; n=5; warmup dropped 1 |
| Route → Recent | leave to #/tags, prksNavigate, wait generation bump + route root | **17.9** ms; n=5; warmup dropped 1 |
| Route → Progress | leave to #/tags, prksNavigate, wait generation bump + route root | **44.1** ms; n=5; warmup dropped 1 |
| Route → People | leave to #/tags, prksNavigate, wait generation bump + route root | **15.0** ms; n=5; warmup dropped 1 |
| Route → Concepts | leave to #/tags, prksNavigate, wait generation bump + route root | **16.6** ms; n=5; warmup dropped 1 |
| Route → Graph chrome | leave to #/tags, prksNavigate, wait generation bump + route root | **45.7** ms; n=5; warmup dropped 1 |
| Route → Folder detail | leave to #/tags, prksNavigate, wait generation bump + route root | **29.9** ms; n=5; warmup dropped 1 |
| Tab switching | activateTab between parked Folders and Recent; settle on destination view root | **18.2** ms; n=6; warmup dropped 1 |
| Main/Secondary split open | prksNavigate(folder-detail, {target:'tile'}); settle on Secondary lastResolvedRoute folder-detail + view root | **47.8** ms; n=1 |
| Secondary nav while split | in-place prksNavigate(person-detail/concept-detail, {tabId: secondary}); people/concepts indexes are not tile-capable. Settle on Secondary lastResolvedRoute + view root | **22.3** ms; n=3; warmup dropped 1 |
| Large folder collection | leave to #/tags, prksNavigate, wait generation bump + route root | **74.4** ms; n=5; warmup dropped 1; 140 cards |
| Search `Batch` | leave to #/tags, prksNavigate, wait generation bump + route root | **51.1** ms; n=5; warmup dropped 1 |
| PDF open (cold) | first prksNavigate(Work A) until focused ctx work/pdf.workId match and a page under ctx.root | **460.4** ms; n=1 |
| PDF cold reopen after folders | reopen Work A after folders — cold route replacement, not warm resume | **432.7** ms; n=3; label: cold reopen |
| PDF warm resume (`prksResumeWarmTabContext`) | warm-park via activateTab(folders) then prksResumeWarmTabContext via activateTab(Work A) | **23.3** ms; n=5; warmup dropped 1 |
| PDF close (cold unmount) | navigate away to folders (cold unmount of current route) | **30.9** ms; n=3; warmup dropped 1 |
| Work A→B (dest work/pdf.workId) | hash A↔B until focused ctx work id + pdf.workId match dest and a page exists under ctx.root | **432.4** ms; n=6 |
| Research Notes mount | leave tags, open Work A until notes/EasyMDE settle | **430.2** ms; n=3; warmup dropped 1 |
| Research Graph mount (cy) | leave to #/tags, prksNavigate, wait generation bump + route root | **23.6** ms; n=5; warmup dropped 1 |

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

Global live Resize/Intersection/MutationObserver counts; long-lived EventTarget listeners on window/document/body/shell/tile/tab-root only (WeakRef map; once/abort release; disconnected tile/tab-root hosts are dropped). __prksResearchGraphLiveCount; lazy-thumb tracked targets. Focused-runtime graph debug fields are not leak evidence.

These tables record **absolute** Pre/Post counts on the named surface after
a generation-bumped paint. Unchanged Δ is evidence only for the listed probes.
`longLivedListenerLive` counts registrations on `window` / `document` /
`document.body` / persistent shell, tile, and tab-root hosts, with `{once}` and
`AbortSignal` release. Detached tile/tab-root hosts (`!isConnected`) are dropped,
so the count is live hosts only. It is not a global add-minus-remove counter.
Focused-runtime graph `debug()` fields (`resizeObserverLive`, `chromeListenerCount`)
describe the *current* mount and are omitted from the leak table.

### Folder surface (Large Batch)

| Probe | Pre | Post | Δ |
| --- | --- | --- | --- |
| `canvases` | 0 | 0 | 0 |
| `cleanupCount` | 4 | 4 | 0 |
| `domNodes` | 3476 | 3476 | 0 |
| `easyMde` | 0 | 0 | 0 |
| `intersectionObserverLive` | 1 | 1 | 0 |
| `longLivedListenerLive` | 154 | 206 | 52 |
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
| `longLivedListenerLive` | 177 | 229 | 52 |
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
  "requests": {
    "response_bytes": 1001059,
    "slow": 0,
    "total": 729
  },
  "topRoutesByCount": [
    {
      "avg_ms": 1.6,
      "count": 72,
      "method": "GET",
      "p50_ms": 1.5,
      "p95_ms": 2.4,
      "route": "/api/works/:id/annotations-snapshot",
      "slow_count": 0
    },
    {
      "avg_ms": 1.4,
      "count": 72,
      "method": "GET",
      "p50_ms": 1.4,
      "p95_ms": 1.4,
      "route": "/api/works/:id/notes-state",
      "slow_count": 0
    },
    {
      "avg_ms": 0.4,
      "count": 72,
      "method": "GET",
      "p50_ms": 0.3,
      "p95_ms": 0.9,
      "route": "/api/pdfs/:pdf",
      "slow_count": 0
    },
    {
      "avg_ms": 1.7,
      "count": 45,
      "method": "GET",
      "p50_ms": 1.7,
      "p95_ms": 1.8,
      "route": "/api/concepts",
      "slow_count": 0
    },
    {
      "avg_ms": 8.9,
      "count": 43,
      "method": "GET",
      "p50_ms": 8.7,
      "p95_ms": 10.1,
      "route": "/api/works",
      "slow_count": 0
    },
    {
      "avg_ms": 4.1,
      "count": 42,
      "method": "GET",
      "p50_ms": 4.0,
      "p95_ms": 5.7,
      "route": "/api/tags",
      "slow_count": 0
    },
    {
      "avg_ms": 1.4,
      "count": 42,
      "method": "GET",
      "p50_ms": 1.4,
      "p95_ms": 1.5,
      "route": "/api/works/:id/people-state",
      "slow_count": 0
    },
    {
      "avg_ms": 11.2,
      "count": 36,
      "method": "GET",
      "p50_ms": 11.4,
      "p95_ms": 12.3,
      "route": "/api/works/:id",
      "slow_count": 0
    }
  ]
}
```

Summarized subset (`counters`, `requests`, top 8 routes by count). Full `serverDiagnostics` is in `docs/b5-browser-baselines/browser-baselines.json`.

## Follow-ups

- Long-lived EventTarget listener growth on persistent hosts is tracked in #459.
- Known unrelated Full E2E flake: #383 (private-reminder hide→re-tile).
- Broader slowness is not turned into a threshold here.

## Raw harness output

Committed default capture: `docs/b5-browser-baselines/browser-baselines.json`.
Regenerate with `scripts/b5_browser_baselines.py` (this document is emitted from that JSON).
