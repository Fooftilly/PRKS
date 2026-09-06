# UX Tour coverage matrix

Maps every significant user-visible PRKS capability to where it is exercised.
The goal is **not** 100% UX-Tour coverage — it is that no important
user-facing feature exists without us knowing where it's exercised. A row
whose "UX scenario" column is `—` is not missing coverage; see its
"Additional coverage" column, or the "Explicitly excluded" section below for
why it stays out of the recorded tour.

Legend for "Additional coverage": **E2E** = `tests/e2e/test_app.py` (real
Chromium, fast, precise); **selftest** = a Node browser selftest under
`tests/browser/`; **API/unit** = a Python unit/API test under `tests/`.

| Surface | Interaction | UX scenario | Additional coverage |
| --- | --- | --- | --- |
| Shell | Full sidebar navigation (Folders/Recent/Views/Types/Playlists/Tags/Publishers/People/Research/Progress/Processing) | shell-navigation | E2E |
| Shell | Compact navigation rail (dense tiled shell) | shell-navigation | E2E |
| Shell | Research/Progress disclosure overlays (open, aria-expanded, no canvas resize) | shell-navigation | E2E |
| Shell | Command palette open/search/select | library | E2E |
| Shell | Command palette keyboard-only flow (Ctrl+K, arrows, Enter) | — | E2E |
| Shell | Settings modal open/close, category tabs | settings | E2E |
| Workspace | Open PDF Work as Main | workspace | E2E |
| Workspace | Open a Work as a new tab (parked → activated) | workspace | E2E |
| Workspace | Activate a tab from the tab strip | workspace | E2E |
| Workspace | Tile a parked tab beside Main | workspace | E2E |
| Workspace | Split Right via pane menu → command palette | workspace | E2E (dedicated geometry regression) |
| Workspace | Split Down via pane menu → command palette | workspace | E2E (dedicated geometry regression) |
| Workspace | Nested/recursive Secondary split tree | workspace | E2E, selftest (workspace-tree) |
| Workspace | Visible-pane cap (1 Main + 3 Secondary) | workspace (reaches cap) | E2E |
| Workspace | Resize root Main/Secondary split | workspace | E2E |
| Workspace | Resize a nested split | workspace | E2E |
| Workspace | Focus a specific pane (click-to-focus) | workspace | E2E |
| Workspace | Open/close tiled Details overlay; pane geometry unchanged | workspace | E2E |
| Workspace | Details overlay Close (×) control | — | E2E |
| Workspace | Make Main from the pane menu | workspace | E2E |
| Workspace | Hide split / Show split (tree preserved) | workspace | E2E |
| Workspace | Close a Secondary pane | workspace | E2E |
| Workspace | Warm PDF return (parked tab warm-suspends/resumes: same TabContext, same PDF resource, no refetch) | workspace | E2E |
| Workspace | Focus among visible tiled PDFs (no remount, no reload) | workspace | E2E |
| Workspace | Pane drag/drop (reorder, move, park) | — | E2E |
| Workspace | Workspace persistence across reload | — | E2E |
| Work/PDF | Open a managed PDF Work | work-pdf | E2E |
| Work/PDF | Edit Research Notes + autosave settlement | work-pdf | E2E |
| Work/PDF | Edit notes then switch tabs immediately (warm-suspension settlement) | work-pdf | E2E |
| Work/PDF | Markdown preview + `[[concept:...]]` wiki-link navigation | work-pdf | E2E |
| Work/PDF | Edit metadata, cancel a dirty edit (confirm modal) | work-pdf | E2E |
| Work/PDF | Edit metadata and save | work-pdf | E2E |
| Work/PDF | Manage relationships (link/unlink Person) | work-pdf | E2E |
| Work/PDF | Manage tags | work-pdf | E2E |
| Work/PDF | Annotations tab visit | work-pdf | E2E (create/select/delete annotation via real click-drag) |
| Work/PDF | PDF page navigation / zoom controls | — | E2E (`PdfPersistenceTests`) |
| Work/PDF | Copy annotation link | — | E2E |
| Library | Folder library view, tree navigation, search/filter | library | E2E |
| Library | Recently Added / File Types / Tags / Publishers / Saved Views pages | library, shell-navigation | E2E |
| Library | Create a PDF Work via the New File modal | library | E2E |
| Library | Create a YouTube Work via the New File modal | library | E2E |
| Library | Invalid/malformed YouTube URL validation | — | E2E/API |
| Research | Concept index search + Concept detail + parent/child relationships | research | E2E |
| Research | Position detail | research | E2E |
| Research | Argument/Stance detail + targets | research | E2E |
| Research | "View in graph" from Concept/Position/Argument/Person | research, people-groups | E2E |
| Graph | Graph Find | research | E2E |
| Graph | Filters disclosure | research | E2E |
| Graph | Legend disclosure | research | E2E |
| Graph | Select a Work/Concept node; inspector renders (no `doc-meta-card`) | research | E2E |
| Graph | Select an edge (`window.selectGraphEdge` — no DOM control exists for this) | research | E2E |
| Graph | Open the selected entity from the inspector | research | E2E |
| Graph | Close Details preserves Graph selection; reopening restores it | — | E2E |
| Graph | Clear selection vs. panel Close (distinct operations) | research | E2E |
| Graph | Fit / Reset layout | research | E2E |
| Graph | Right-panel ownership across a split workspace (focused Graph only) | — | E2E |
| People | People index, search, Person profile | people-groups | E2E |
| People | Edit profile and save | people-groups | E2E |
| People | Person Groups: hierarchy tree, search/filter | people-groups | E2E |
| People | Group detail: metadata edit vs. Manage Members mode separation | people-groups | E2E |
| People | Manage Members: add / remove (with confirm modal) | people-groups | E2E |
| People | Open a subgroup from its parent's detail page | people-groups | E2E |
| Organization | Playlists index, Playlist detail, open a Work from it | organization | E2E |
| Organization | Progress: all five status filters (Not Started/Planned/In Progress/Paused/Completed) | organization | E2E |
| Organization | Processing inbox page loads and is reachable | organization (page visit only) | — |
| Organization | Processing: detect metadata, import/save an actual file | — (needs a real watch-folder fixture; see below) | E2E/API |
| Settings | General: annotation author field | settings | E2E |
| Settings | Reading & layout: toggle a setting, restore it | settings | E2E |
| Settings | Export: toggle a BibTeX field, restore it | settings | E2E |
| Settings | Backup: create/download a backup | settings | E2E |
| Settings | Backup: restore a backup | — | E2E |
| Settings | Maintenance: rebuild PDF text index | settings | E2E |
| Settings | Maintenance: linearize existing PDFs | — | E2E |
| Settings | Diagnostics: lazy load (not until activated), then Refresh | settings | E2E |

## Explicitly excluded from the recorded tour

These stay in lower-level tests on purpose — a browser recording should not
imitate unit testing:

- Malformed/adversarial HTTP and API inputs (invalid IDs, oversized payloads,
  wrong content types) — **API/unit**.
- SSRF and other security edge cases (URL scheme smuggling, path traversal,
  lookalike hostnames) — **API/unit**, `tests/test_e2e_isolation.py`.
- Database migration internals and schema-version bumps — **API/unit**.
- Backup corruption / malformed-backup-restore handling — **API/unit**.
- Browser unload / `beforeunload` edge cases requiring synthetic event setup —
  **E2E** (dedicated pending-annotation-sync tests).
- Real public YouTube playback or metadata content — never (the tour stubs
  both the server-side oEmbed fetch, via an `AppServer` extra-env HTTPS proxy
  override, and the browser-side oEmbed/embed requests, via `page.route()`;
  see `tests/ux_tour/test_tours.py::LibraryCreationTourTest`).
- Internal ranking/recommendation or search-scoring algorithms — **API/unit**.
- Processing-inbox file ingestion (detect → import) — needs a real
  `PRKS_FOR_PROCESSING_DIR` watch folder wired into a running server; v1 of
  the tour only visits the page. Extending `AppServer`/its fixtures to safely
  stage a real processing file, or building a dedicated E2E test for it, is
  future work — not silently skipped, tracked here.

## Notes on fixtures

`tests/ux_tour/fixtures.py::seed_ux_tour_library` extends
`tests.e2e.fixtures.seed_graph_context_library` with a folder hierarchy,
three Tags, a Publisher, a fourth Work (different progress states, a second
PDF), a Playlist, a second Person, a Person Group with a subgroup and
memberships, a Concept parent/child pair, and a Position with a supporting
Stance, plus a Saved View — enough that no tour lands on an empty-state-only
page. Every tour test method gets a **fresh** copy of this seed via its own
`AppServer`; none share a database.
