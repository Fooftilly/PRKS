# B5 browser performance / resource baselines (#454)

Regression baseline and resource-lifetime evidence for the finish-line Vue
cutover. **No pass/fail thresholds** are invented here — numbers are a
comparison point for later work.

Does **not** close #303 or #230. Does **not** mark B5 complete by itself.
Stacked on cleanup-only #453 (`cursor/b5-final-purge-228c`).

## Measurement identity

| Field | Value |
| --- | --- |
| Measured git HEAD | `59d65a2cafe5286e4fe063213d6530625ce20c6b` |
| App | `python prks_app.py --testing --port 46743` |
| Storage | temp `PRKS_STORAGE` (never repo `data/` / live production tree); recreate with the harness |
| Client harness | Playwright Chromium channel=`None`, viewport 1400×900, headless |
| Playwright browser version | `153.0.8010.12` |
| User agent | `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/153.0.8010.12 Safari/537.36` |
| OS | `Linux-6.18.44-fc-v70-x86_64-with-glibc2.39` |
| Python | `3.13.16` |
| CPU | `Intel(R) Xeon(R) Processor @ 2.80GHz` |
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
| Seed reused | False |

## Client scenario baselines (median ms unless noted)

| Scenario | Method | Result |
| --- | --- | --- |
| Initial client load | Navigation Timing on `/` | duration **510.2** ms; DCL **505.5** ms; FP **224.0** ms; FCP **224.0** ms; transfer 95728 |
| Route → Folder Library | leave to #/tags, prksNavigate, wait generation bump + route root | **81.1** ms; n=5; warmup dropped 1 |
| Route → Recent | leave to #/tags, prksNavigate, wait generation bump + route root | **40.2** ms; n=5; warmup dropped 1 |
| Route → Progress | leave to #/tags, prksNavigate, wait generation bump + route root | **105.9** ms; n=5; warmup dropped 1 |
| Route → People | leave to #/tags, prksNavigate, wait generation bump + route root | **32.7** ms; n=5; warmup dropped 1 |
| Route → Concepts | leave to #/tags, prksNavigate, wait generation bump + route root | **37.1** ms; n=5; warmup dropped 1 |
| Route → Graph chrome | leave to #/tags, prksNavigate, wait generation bump + route root | **97.7** ms; n=5; warmup dropped 1 |
| Route → Folder detail | leave to #/tags, prksNavigate, wait generation bump + route root | **70.5** ms; n=5; warmup dropped 1 |
| Tab switching | activateTab between parked Folders and Recent; settle on destination view root | **46.4** ms; n=6; warmup dropped 1 |
| Main/Secondary split open | prksNavigate(folder-detail, {target:'tile'}); settle on Secondary lastResolvedRoute folder-detail + view root | **141.6** ms; n=1 |
| Secondary nav while split | in-place prksNavigate(person-detail/concept-detail, {tabId: secondary}); people/concepts indexes are not tile-capable. Settle on Secondary lastResolvedRoute + view root | **52.9** ms; n=3; warmup dropped 1 |
| Large folder collection | leave to #/tags, prksNavigate, wait generation bump + route root | **181.3** ms; n=5; warmup dropped 1; 140 cards |
| Search `Batch` | leave to #/tags, prksNavigate, wait generation bump + route root | **113.1** ms; n=5; warmup dropped 1 |
| PDF open (cold) | first prksNavigate(Work A) until focused ctx work/pdf.workId match and a page under ctx.root | **736.4** ms; n=1 |
| PDF cold reopen after folders | reopen Work A after folders — cold route replacement, not warm resume | **602.3** ms; n=3; label: cold reopen |
| PDF warm resume (`prksResumeWarmTabContext`) | warm-park via activateTab(folders) then prksResumeWarmTabContext via activateTab(Work A) | **58.8** ms; n=5; warmup dropped 1 |
| PDF close (cold unmount) | navigate away to folders (cold unmount of current route) | **67.9** ms; n=3; warmup dropped 1 |
| Work A→B (dest work/pdf.workId) | hash A↔B until focused ctx work id + pdf.workId match dest and a page exists under ctx.root | **609.8** ms; n=6 |
| Research Notes mount | leave tags, open Work A until notes/EasyMDE settle | **639.7** ms; n=3; warmup dropped 1 |
| Research Graph mount (cy) | leave to #/tags, prksNavigate, wait generation bump + route root | **123.9** ms; n=5; warmup dropped 1 |

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
| `domNodes` | 3476 | 3476 | 0 |
| `canvases` | 0 | 0 | 0 |
| `pdfHosts` | 0 | 0 | 0 |
| `easyMde` | 0 | 0 | 0 |
| `researchGraphLive` | 0 | 0 | 0 |
| `cleanupCount` | 4 | 4 | 0 |
| `timerCount` | 0 | 0 | 0 |
| `thumbObserverPresent` | True | True | 0 |
| `thumbTrackedTargets` | 16 | 16 | 0 |
| `thumbTrackedConnected` | 16 | 16 | 0 |
| `thumbTrackedDisconnected` | 0 | 0 | 0 |
| `thumbObservingAttr` | 16 | 16 | 0 |
| `resizeObserverLive` | 3 | 3 | 0 |
| `intersectionObserverLive` | 1 | 1 | 0 |
| `mutationObserverLive` | 1 | 1 | 0 |
| `longLivedListenerLive` | 80 | 80 | 0 |

### Graph surface (`#/graph`)

| Probe | Pre | Post | Δ |
| --- | --- | --- | --- |
| `domNodes` | 1589 | 1589 | 0 |
| `canvases` | 3 | 3 | 0 |
| `pdfHosts` | 0 | 0 | 0 |
| `easyMde` | 0 | 0 | 0 |
| `researchGraphLive` | 1 | 1 | 0 |
| `cleanupCount` | 1 | 1 | 0 |
| `timerCount` | 0 | 0 | 0 |
| `thumbObserverPresent` | True | True | 0 |
| `thumbTrackedTargets` | 0 | 0 | 0 |
| `thumbTrackedConnected` | 0 | 0 | 0 |
| `thumbTrackedDisconnected` | 0 | 0 | 0 |
| `thumbObservingAttr` | 0 | 0 | 0 |
| `resizeObserverLive` | 4 | 4 | 0 |
| `intersectionObserverLive` | 1 | 1 | 0 |
| `mutationObserverLive` | 3 | 3 | 0 |
| `longLivedListenerLive` | 102 | 102 | 0 |

## Server diagnostics after client scenarios

```json
{
  "counters": {
    "db_read": 1005,
    "db_write": 284,
    "pdf_file_stat_files": 1534,
    "pdf_file_stat_rows": 9574,
    "thumbnail_cache_hits": 0,
    "thumbnail_cache_misses": 12
  },
  "measured_for_seconds": 49,
  "requests": {
    "response_bytes": 1003062,
    "slow": 4,
    "total": 877
  },
  "topRoutesByCount": [
    {
      "avg_ms": 19.7,
      "count": 142,
      "method": "POST",
      "p50_ms": 17.9,
      "p95_ms": 27.9,
      "route": "/api/works",
      "slow_count": 0
    },
    {
      "avg_ms": 4.0,
      "count": 72,
      "method": "GET",
      "p50_ms": 3.4,
      "p95_ms": 8.7,
      "route": "/api/works/:id/annotations-snapshot",
      "slow_count": 0
    },
    {
      "avg_ms": 2.7,
      "count": 72,
      "method": "GET",
      "p50_ms": 2.5,
      "p95_ms": 3.7,
      "route": "/api/works/:id/notes-state",
      "slow_count": 0
    },
    {
      "avg_ms": 0.8,
      "count": 72,
      "method": "GET",
      "p50_ms": 0.6,
      "p95_ms": 1.9,
      "route": "/api/pdfs/:pdf",
      "slow_count": 0
    },
    {
      "avg_ms": 8.4,
      "count": 45,
      "method": "GET",
      "p50_ms": 8.1,
      "p95_ms": 14.2,
      "route": "/api/tags",
      "slow_count": 0
    },
    {
      "avg_ms": 3.8,
      "count": 45,
      "method": "GET",
      "p50_ms": 3.4,
      "p95_ms": 6.0,
      "route": "/api/concepts",
      "slow_count": 0
    },
    {
      "avg_ms": 18.7,
      "count": 44,
      "method": "GET",
      "p50_ms": 18.0,
      "p95_ms": 28.0,
      "route": "/api/works",
      "slow_count": 0
    },
    {
      "avg_ms": 2.9,
      "count": 42,
      "method": "GET",
      "p50_ms": 2.7,
      "p95_ms": 4.6,
      "route": "/api/works/:id/people-state",
      "slow_count": 0
    }
  ]
}
```

Summarized subset (`counters`, `requests`, top 8 routes by count). Full `serverDiagnostics` is in `docs/b5-browser-baselines/browser-baselines.json`.

## Follow-ups

- #459 fixed the long-lived EventTarget listener growth on persistent hosts (page-enter `animationend` on tab roots; EasyMDE `document` keydown). A nonzero `longLivedListenerLive` Δ above is a regression to attribute.
- Known unrelated Full E2E flake: #383 (private-reminder hide→re-tile).
- Broader slowness is not turned into a threshold here.

## Raw harness output

Committed default capture: `docs/b5-browser-baselines/browser-baselines.json`.
Regenerate with `scripts/b5_browser_baselines.py` (this document is emitted from that JSON).
