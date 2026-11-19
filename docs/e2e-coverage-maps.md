# E2E coverage maps

Per-family KEEP / SPLIT / MOVE decisions (rule in `tests/e2e/AGENTS.md`): which contract left Chromium, what proves it now, and what browser boundary stays. Before removing or weakening a browser scenario in one of these families, check its row here; add a row when you move a contract. Rows cite current tests; SQLite-era tests named in older records map to their replacements in `tests/sqlite_retirement/`.

## Work Source family

| Removed E2E | Replacement |
| --- | --- |
| four-column pending identity | Node `effectiveSourceOverlay` in `run_work_source_sync_selftest.js` |
| respelling same video not a conflict | `tests/pg/test_works_sync.py` `SourceSyncTests.test_a_change_is_one_revision_and_the_ack_states_the_stored_row`; `test_works_sync_parity.py` `test_sources` |

## Work-Tag

| Former browser scenario | Replacement fast coverage | Decision |
| --- | --- | --- |
| Coalescing across reload and repeated intent | `tests/browser/run_local_store_selftest.js` owns repeated intent and opposite cancel across a reopened store; `test_post_reload_opposite_edit_cancels_pending` keeps the thin remount → Manage tags boundary for both opposite UI directions (chip remove and picker re-add) | SPLIT |
| Lost response replays once with the same operation identity | `tests/browser/run_work_tag_sync_selftest.js` proves a transport loss leaves the original envelope pending and replays the exact same `op_id`/envelope; `tests/pg/test_tags_sync.py` `ReplayTests` proves server replay of an existing `op_id` is exact/idempotent | SPLIT → fast layers |

## Work-Open

| Former browser scenario | Replacement fast coverage | Decision |
| --- | --- | --- |
| Lost response applies the event once | `tests/browser/run_work_open_sync_selftest.js` proves a transport loss leaves the original envelope pending and replays the exact same `op_id`/envelope; `tests/pg/test_works_sync.py` `OpenSyncTests.test_the_ack_carries_the_recent_row_and_a_replay_answers_from_the_ledger` proves server replay is exact/idempotent | SPLIT → fast layers |
| Older event never overwrites a newer one (both arrival orders) | `tests/pg/test_works_sync.py` `OpenSyncTests.test_the_latest_event_wins_whatever_the_arrival_order_or_zone` and `ConcurrentOperationTests.test_opens_from_many_devices_keep_the_latest` | MOVE |
| Repeated opens of one Work are one event | Node `recording()` in `run_work_open_sync_selftest.js` | MOVE |
| Different Works keep their own events | Node `recording()` + `overlay()` | MOVE |
| Open event for a deleted Work is consumed, not parked | Node `terminal()` ENTITY_NOT_FOUND discard; `tests/pg/test_works_sync.py` `OpenSyncTests.test_malformed_envelopes_are_refused_unledgered_and_a_missing_work_is_terminal` | MOVE |

## Concepts durable — definition cancel + parent-set

| Former browser scenario | Replacement fast coverage | Decision |
| --- | --- | --- |
| Definition taken back before send leaves no intent | Node `theDefinitionCoalescesAndCancels` (store) **and** `theDefinitionCancelGoesThroughApiWrapper` — production `updateConcept` from `frontend/js/api.js` against fake IndexedDB (plus dirty-fields overlay asserts) | MOVE |
| Same parents (order-insensitive set) leave no intent | Node `theParentSetIsASet` (store) **and** `theParentSetCancelGoesThroughApiWrapper` — production `putConceptParents` from `frontend/js/api.js` | MOVE |

## Work-Notes

| Former browser scenario | Replacement fast coverage | Decision |
| --- | --- | --- |
| Editing back to the acknowledged body cancels the pending op | Node `coalescingFor(RESEARCH)` A→B→A plus `mutationTestAtoBtoA`; editor-path coverage via `durableSaveThroughObservedBaseCancels` and production `enqueuePathAtoBtoACancels` (`prksEnqueueWorkResearchNotesSave` with editor context → pending returns to zero); static enqueue/flush pins in `run_work_note_sync_selftest.js` / `test_frontend_work_note_sync.py` | MOVE |
| Private ACK does not fence Concepts | Node `reconciliation()` dispatches Private ACK through `prksNoteSyncHandler.reconcile` → `prksOfflineReconcilePrivateNote` (must not advance Concepts/Arguments/Graph generations; patches only `private_notes`) | MOVE |
| Stale research revision is a conflict (compact body privacy) | Node `handlerContract` rejects body-carrying conflicts; `tests/pg/test_works_mutations.py` `NoteDecisionTests.test_each_row_of_the_decision_table` and `tests/pg/test_works_sync.py` `NoteSyncTests.test_acknowledgements_and_conflicts_carry_sizes_never_prose` | SPLIT → fast layers for shape; thin Chromium retains reconnect park |

## Work-People

| Scenario | Fast coverage | Decision |
| --- | --- | --- |
| Linking then unlinking before send leaves no intent | Node `coalescing()` in `run_work_role_sync_selftest.js` owns absent→ADD→REMOVE / present→REMOVE→ADD empty-queue arithmetic | **SPLIT** — thin Chromium: role-modal link → optimistic chip → unlink confirm → empty list → reconnect transmits nothing (`test_linking_and_unlinking_before_it_sends_leaves_no_intent`) |

## Work metadata — Slice A (core protocol)

| Former browser scenario | Replacement fast coverage | Decision |
| --- | --- | --- |
| Repeated edits coalesce and returning to base cancels | Store `coalescing()` owns A→B→C / repeat-noop / edit-back-to-base; `editorCoalescing()` drives the real `prksSaveWorkMetadataFields` bib Save (DOM harness) so a never-sent pending field stays editable and subsequent Saves dirty against the displayed pending value | SPLIT |
| Lost response applies the edit once | Selftest `lostResponse()` proves transport loss leaves the original envelope pending and replays the exact same `op_id`/envelope; `tests/pg/test_works_metadata_sync.py` `ConflictRuleTests.test_a_reused_op_id_is_refused_and_changes_nothing` proves ledger idempotency / `OP_ID_REUSE`; `tests/pg/test_server_api_work_writes.py` `SyncReplayTests.test_a_metadata_operation_whose_response_was_lost_replays_at_revision_1` covers `/api/sync/operations` commit-then-lost-response replay with DOI revision staying 1 | SPLIT → fast layers |

## Work metadata — Slice B (abstract oversize refuse)

| Former browser scenario | Replacement fast coverage | Decision |
| --- | --- | --- |
| Oversize Abstract refused without touching durable state or the server | Selftest `installWorkMetadataEditorHarness` + `prksSaveWorkMetadataFields('bib')` with a spy store: paints the exact refusal status, retains the draft, never calls `saveWorkMetadataFields` / leaves the queue empty; plus limit helper + editor abort static + Python abstract limit | MOVE |

## Folder / Playlist already-proven runtime fences

| Former browser scenario | Replacement fast coverage | Decision |
| --- | --- | --- |
| Folder stale GET cannot repopulate after `markDomainChanged` | `tests/browser/run_offline_runtime_selftest.js` Folder list+entity stale block | MOVE |
| Folder cleanup failure stays domain-local | Same selftest Folder cleanup locality | MOVE |
| Work deletion / Work metadata mark helpers bump folders generation | Offline-runtime domain mark helpers + `run_work_metadata_sync_selftest.js` (vacuous Chromium helper-call E2Es) | MOVE |
| Browser scenario | Why Chromium | Fast layer still useful |
| --- | --- | --- |
| Work deletion via `.delete-work-btn` invalidates folders generation + `folders:index` | Real DELETE_WORK UI → ACK → `reconcileDeletedWork` folders fence | Node mark helpers do not replace the entrypoint |
| Work metadata editor `#save-work-identity-btn` reconciles playlist titles in place | Editor save path is distinct from playlist inline-rename | Node `embeddedReconciliation` owns ACK patch mechanics |

## Position / Playlist direct-API pilot

| Former E2E (removed) | Fast replacement (production code under test) |
| --- | --- |
| `DurablePositionTests.test_an_edit_taken_back_before_it_is_sent_leaves_no_intent` | `run_position_sync_selftest.js`: real `api.js updatePosition` → `prksAcknowledgedPositionBase` → dirty fields → durable writer. The known revision 3 stays attached to A→B→A, which must leave zero `SET_POSITION_FIELD` rows. |
| `DurablePositionTests.test_deleting_a_position_created_offline_folds_the_whole_case_away` | Same Node selftest: real `api.js createPosition` / `deletePosition`, permanent distributed ID, queued create and empty final operation list. |
| `DurablePlaylistTests.test_a_rename_taken_back_before_it_is_sent_leaves_no_intent` | `run_playlist_sync_selftest.js`: real `components/playlists.js updatePlaylist`, a cached acknowledged title at revision 3, A→B→A, zero `SET_PLAYLIST_FIELD` operations. |
| `DurablePlaylistTests.test_a_second_drag_replaces_the_first_rather_than_racing_it` | Same Node selftest: real `reorderPlaylist` over acknowledged order at revision 2; first replacement is one ordered operation, return to original order has zero queued reorders. The browser drag event path is separately exercised by retained scenarios. |
| `DurablePlaylistTests.test_putting_a_video_back_where_it_started_leaves_no_intent` | Same Node selftest: real `addWorkToPlaylist` / `prksSetWorkPlaylist` using a cached Work playlist-state at revision 4; moving away and back leaves zero membership operations. |
| `DurablePlaylistTests.test_deleting_a_playlist_created_offline_folds_the_whole_case_away` | Same Node selftest: real `createPlaylist` / `deletePlaylistCanonical`; unsent create folds to zero queue rows. |

## Offline read-model validator matrix migration

| Removed Chromium case | Fast replacement |
| --- | --- |
| `OfflinePositionTests.test_position_shape_validators_reject_unusable_rows` | Position index/detail shape matrices, 18 asserted outcomes |
| `OfflineArgumentTests.test_argument_shape_validators_reject_unusable_rows` | Argument/Stance index/detail shape matrices, 45 asserted outcomes |
| `OfflinePeopleTests.test_people_validator_matches_renderer_scalar_contract` | People index/detail optional-string and Work year/date scalar matrix, 8 contract-wide Boolean assertions |
| `PlaylistsOfflineTests.test_predicates_protect_every_rendered_field` | Playlist index/detail scalar, embedded Work, position/count matrix, 31 asserted outcomes |

## Argument ACK cache-coherence

| Former E2E (removed) | Preserved browser coverage | Fast contract |
| --- | --- | --- |
| `OfflinePositionTests.test_argument_source_change_does_not_invalidate_positions` | The later full-stack Node ACK contract replaces the surviving source-only E2E; real browser failed-transport behavior remains in `OfflineArgumentCoherenceTests.test_failed_argument_mutations_retain_the_cache` | Real `prksOfflineReconcileArgumentSources` with unchanged and changed ACK responses; an unchanged ACK retains every cache, changed ACK sweeps Arguments and Graph but leaves Position/Concept/People snapshots and lists eligible |
| `OfflineArgumentCoherenceTests.test_argument_targets_invalidate_arguments_and_positions_only` | `OfflinePositionTests.test_argument_target_change_invalidates_the_position_domain` verifies server ACK, updated Position verdict, authoritative refetch and real offline reload; the full-stack Node source-ACK contract retains Argument cache-fencing integration | Real `prksOfflineReconcileArgumentTargets` ACK clears Arguments/Positions/Graph but preserves Concepts and People |

## Folder ACK cache-footprint

| Removed E2E | Fast production-owned assertion |
| --- | --- |
| `test_folder_create_invalidates_only_folders` | `reconcileCreatedFolder` inserts the canonical row into the existing cached Folder index and increments only the Folder generation; existing cached Works and six other read-model domains survive |
| `test_description_only_update_keeps_member_work_snapshots` | `reconcileFolderField` patches description into Folder index and detail while keeping both member and unrelated Work snapshots, preserving other domains |
| `test_reparent_invalidates_folders_but_no_work_snapshots` | `reconcileFolderField` updates the parent pointer in the Folder index, invalidates the now-stale Folder detail containing a parent object, and keeps existing Work snapshots and unrelated domains |

## Work-role and biography cache-domain helpers

| Removed browser test | Fast replacement contract |
| --- | --- |
| `FoldersOfflineTests.test_author_and_editor_roles_invalidate_folders_but_other_roles_do_not` | Author and Editor invalidate Folder hierarchy; Reviewer and other roles do not. Folder index/entity preservation and eviction are checked directly against the shipped offline runtime. |
| `FoldersOfflineTests.test_person_rename_invalidates_folders_but_biography_does_not` | Despite its name, the former test PATCHed only `about` (not a rename), then explicitly called `prksMarkPeopleDomainChanged`. Fast coverage proves People invalidation leaves Folder index/details untouched. Actual Person editing remains E2E. |
| `OfflinePeopleCoherenceTests.test_every_role_type_invalidates_people_but_only_author_invalidates_arguments` | Every role stales People and Person Groups; **only Author** also stales Arguments. Author/Editor additionally stale Folder Work-card credit and relevant browse caches; other roles do not. |

## Playlist field/reorder ACK read-model coherence

| Removed E2E | Replacement fast assertion |
| --- | --- |
| `test_a_description_edit_keeps_member_work_caches` | Description ACK patches both index and detail, advances the per-field sync base and keeps member Work snapshots |
| `test_a_title_edit_stales_member_work_snapshots_only` | One combined title+description wrapper edit yields two field-scoped operations and ACKs, patches the index/detail and invalidates exactly cached member Works, not an unrelated Work |
| `test_a_reorder_keeps_member_work_caches_and_reorders_the_detail` | Order ACK updates the cached detail's item order and order revision, fences stale index only, keeps member and unrelated Works |

## Retire raw-request People coherence probes

| Former E2E | What it actually exercised | Stronger coverage retained |
| --- | --- | --- |
| `OfflinePeopleCoherenceTests.test_creating_an_unassigned_group_leaves_people_eligible` | `prksRequest('POST /api/person-groups')`, a fixed 500 ms delay and a People cache-generation equality check. It bypassed `prksCreatePersonGroupDurably` and never checked the server ACK or Group read-model patch. | `test_person_groups_offline.py::test_group_acknowledgements_patch_the_caches_they_own` creates an unassigned Group **through the durable writer**, drains the real server ACK, verifies only the Person Group generation changes, checks the new cached Group list row, and also exercises rename, membership and deletion effects on People caches. |
| `OfflinePeopleCoherenceTests.test_failed_person_mutation_retains_the_people_cache` | A direct `PATCH /api/persons/{id}` was intercepted with HTTP 500; **the test itself** called `prksMarkPeopleDomainChanged()` conditionally on a successful raw response. This did not drive the real Person field writer or its ACK/refusal handler. It used a fixed 400 ms delay. | `test_person_edit_offline.py` retains real Chromium Person editor and durable-writer coverage for reconnect/ACK with no rollback, independently conflicted fields, and failed creation prerequisites. `tests/browser/run_person_metadata_sync_selftest.js` retains per-field durable envelope, ordering and retry/conflict contracts. |

## Single-contract moves

| Former browser scenario | Replacement fast coverage | Decision |
| --- | --- | --- |
| Private-reminder ACK proof must match Work and base (`test_private_reminder_ack_proof_must_match_work_and_base`) | `run_work_note_sync_selftest.js` calls the shipped `prksSaveWorkPrivateNoteForSession` (`committedPrivateAckProofIsExactAndBounded`) | MOVE |
| Private-reminder committed handoff excludes foreign and failed saves | `prksWorkPrivateNoteNeedsCommittedHandoff` (one owner, `frontend/js/work-notes-state.js`) guard matrix in the Node note selftest | MOVE |
| Argument sources invalidate Arguments but not Positions | `run_argument_ack_coherence_selftest.js` through `prksSetArgumentSourcesDurably` | MOVE |
| Position index search matches title and description; Argument filtered empty state names the active kind | `PositionsIndexRoute.vue` and `ArgumentsIndexRoute.vue` mounted through the Vue `session.test.ts` route bridge (Chromium keeps clear-search, kind-aware filtered search and Concept index search) | MOVE |
| Position relationship rows use the shared research row language; Argument detail counts and empty wording | `PositionDetailRoute.vue` and `ArgumentDetailRoute.vue` fixtures in `session.test.ts` (`PrksResearchRow`, `PrksResearchSectionHead`) | MOVE |
| Position code no longer applies its own Argument offline policy | `PositionDetailRoute.vue` fixture in `session.test.ts` checks the rendered href and absent guard attributes | MOVE |
