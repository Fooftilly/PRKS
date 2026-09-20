# Glossary

**Canonical data** — Research state that must be preserved and is authoritative, such as the main library database and managed research files.

**Derived data** — Rebuildable data such as thumbnail/search/reference indexes.

**Durable operation** — A local-first record of user intent stored durably in the browser before the change is reported as saved, then synchronized/reconciled with the server.

**Main** — The workspace pane/tab that owns the browser URL.

**Secondary pane** — A visible split-view pane beside/below Main. It has its own TabContext.

**Parked tab** — An open PRKS tab that is not currently mounted as a visible pane.

**TabContext** — Per-tab runtime context containing route/page root, async generation, and live resources such as viewers/editors/graphs.

**Work** — Primary research item: PDF/video/other supported research source plus metadata and relationships.

**Work source identity** — Aggregate that determines what source a Work actually represents; for video sources it cannot safely be modeled as unrelated independent columns.

**Research Note** — Work-associated research text that participates in the local-first notes model.

**Concept** — Structured research idea/category.

**Position** — Structured claim/position.

**Argument / Stance** — Structured argumentative relationship with sources/targets.

**Read projection / offline cache** — Disposable cached representation used to render selected content when the server is unreachable. It is not durable user intent.

**Request coordinator** — Short-lived online request coordination/cache. It is neither the offline read store nor the durable operation store.

**Materialization** — Producing/reconciling derived bytes/state from canonical structured data, such as applying the canonical annotation set to managed PDF bytes.

**Rollout status** — The living document \`docs/local-first-rollout-status.md\` that records which local-first families are currently durable/offline-capable.
