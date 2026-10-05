# B5 browser performance / resource baselines (#454)

Regression baseline and resource-lifetime evidence for the finish-line Vue
cutover. **No pass/fail thresholds** are invented here — numbers are a
comparison point for later work.

Does **not** close #303 or #230. Does **not** mark B5 complete by itself.
Stacked on cleanup-only #453 (`cursor/b5-final-purge-228c`).

## Measurement identity

| Field | Value |
| --- | --- |
| Measured tip (stack base) | `420192ff` on `cursor/b5-final-purge-228c` |
| App | `python3 prks_app.py --testing --port 8070` |
| Storage | `/tmp/prks-b5-baselines-454` (never repo `data/` / live `PRKS_STORAGE`) |
| Client harness | Playwright Chromium `channel=chrome`, viewport 1400×900, headless |
| Client timing | `performance.now()` around hash / `prksNavigate` until settle selector |
| Server diagnostics | `GET /api/diagnostics/performance` after client scenarios |
| Client coordinator | `window.prksRequestCoordinatorSnapshot()` |
| Lifetime probes | DOM nodes, `<canvas>` count, PDF host count, EasyMDE count, `ctx.getResource` kinds |
| Privacy | Synthetic titles only (`Synthetic Work …`, `Synthetic Library`, …) |

## Testing-library shape

| Kind | Count / note |
| --- | --- |
| Works (browse) | 142 |
| Title-only batch | 120 in folder `Large Batch` |
| Tiny PDF batch | 20 in `Large Batch` |
| Named PDF works | `Synthetic Work A`, `Synthetic Work B` (3-page each) |
| Folders | `Synthetic Library` → child `Large Batch` |
| Persons | 2 |
| Concepts | 2 |
| Positions | 1 |
| Research notes | Work A notes with `[[concept:…]]` markup |
| Graph seed | 4 nodes / 2 edges at seed time |

## Client scenario baselines (median ms unless noted)

| Scenario | Method | Result |
| --- | --- | --- |
| Initial client load | Navigation Timing on `/` | duration **303** ms; DCL **292** ms; FP/FCP **196** ms; transfer ~96 KB |
| Route → Folder Library | hash `#/folders` ×5 | median **10.1** ms |
| Route → Recent | hash ×5 | **8.1** |
| Route → Progress | hash ×5 | **11.9** |
| Route → People | hash ×5 | **8.5** |
| Route → Concepts | hash ×5 | **8.8** |
| Route → Graph | hash ×5 | **6.1** (first paint of chrome; canvas mount below) |
| Route → Folder detail | hash `#/folders/<lib>` ×5 | **8.5** |
| Tab switching | workspace tab strip click after tile creates 2 tabs ×6 | median **186** ms |
| Main/Secondary split open | `prksNavigate(folder, {target:'tile'})` | **549** ms (2 tiles) |
| Secondary nav while split | `prksNavigate(..., {target:'tile'})` ×3 | median **348** ms |
| Large folder collection | `#/folders/<batch>` ×5 | median **7.1** ms; **140** cards visible |
| Search `Batch` | `#/search?q=Batch` ×5 | median **7.6** ms; **140** cards visible |
| PDF open | first `#/works/<A>` until PDF host | **845** ms |
| PDF resume | reopen after folders ×2 | median **842** ms |
| PDF close | navigate away to folders ×3 | median **36** ms |
| Work A→B | hash A↔B ×3 pairs | median **319** ms |
| Research Notes mount | work route until notes/EasyMDE settle ×3 | median **44** ms |
| Research Graph mount | `#/graph` until canvas ×5 | median **484** ms; canvas count **3** every mount (**growth 0**) |

Coordinator after large/search settle: queues idle (`activeReads=0`); peak active reads **3**; failed/aborted **0**.

## Repeated mount / resource lifetime

10 cycles: Large Batch folder → Work A → Graph → Recent, ending on `#/graph`.

| Probe | Pre | Post | Δ |
| --- | --- | --- | --- |
| DOM nodes | (same route) | | **0** |
| Canvases | | | **0** |
| PDF hosts | | | **0** |
| EasyMDE | | | **0** |
| `researchGraph` resources | 2 | 2 | **0** |

Coordinator queues idle after settle. **No unbounded accumulation** of canvases,
PDF hosts, or graph resources across remounts in these probes — evidence that
viewers/editors/listeners are released for the measured surfaces.

## Server diagnostics after client scenarios

Window after client harness (includes seed leftover where not reset mid-run):

- Requests: **655** total, **0** slow (`slow_threshold_ms` default)
- Notable route p50/p95 (ms): works detail 13.9/16.7; folders detail 8.9/9.4; search (during client) low single-digit when exercised; research-graph 2.1/3.5; thumbnail 5.6/109 (cold encode outliers)
- Spans with samples: `db` n=741 p50=1.2 p95=6.7; `research_graph_build` n=29 p50=1.8 p95=2.7; `thumbnail_render` n=13 p50=1.1; `thumbnail_encode` n=13 p50=19.0 p95=62.8
- Counters: thumbnail cache hits 15 / misses 13; `db_read` 741; `db_write` 0 during the client window

Empty-library server snapshot remains on #453 history and is **not** a substitute
for these client scenarios.

## Follow-ups

- No tightly coupled migration defect found that belongs in this measurement PR.
- Known unrelated Full E2E flake: #383 (private-reminder hide→re-tile).
- Broader slowness (e.g. thumbnail encode p95) is not turned into a threshold here;
  open a focused issue only if a later comparison shows a real regression.

## Raw harness output

Machine-local capture used for this write-up (not committed):  
`/tmp/prks-b5-baselines-454/browser-baselines.json`.
