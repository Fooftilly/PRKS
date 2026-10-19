-- Works family: Work, Manifestation, Asset and their supporting tables
-- (docs/postgresql-architecture.md §6.4, §6.5; docs/work-identity-model.md
-- §3, §4.1, §9.1). L-W-1 (#628) owns this file.
--
-- Every table is library-scoped (PG6): it has `library_id`, and every child
-- reaches its parent through a composite key that includes `library_id`, so
-- the database refuses a row that pairs one Library's child with another
-- Library's parent.
--
-- Ownership is a database fact (§4.1 of the #60 design), expressed with
-- composite foreign keys only; there are no triggers:
--
-- - `works.primary_manifestation_id` (required) and
--   `citation_manifestation_id` (optional) point at a Manifestation of the
--   same Work through `(library_id, pointer, id)` ->
--   `manifestations (library_id, id, work_id)`. Both are DEFERRABLE INITIALLY
--   DEFERRED: a Work and its first Manifestation are inserted in one
--   transaction and checked at COMMIT, and moving a pointer target to another
--   Work commits only if the pointer moved too.
-- - `manifestations.primary_asset_id` points at an Asset of the same
--   Manifestation, deferred the same way, so moving the only Asset out and
--   clearing the pointer commit in either order.
-- - `assets (manifestation_id, work_id)` follows its Manifestation by
--   ON UPDATE CASCADE, so a moved Manifestation carries its Assets' Work.
--
-- These replace the SQLite pointer, origin and retirement triggers, the
-- `work_retirement` marker and its CHECK(0) guard table: a Work whose primary
-- is missing, or names another Work's Manifestation, cannot commit. There is
-- no `origin_work_id` (no legacy rows or operations exist, §6.4 item 3) and
-- no `content_generation` (bytes are immutable per storage key, item 4).
-- `manifestation_credit_overrides` arrives with #60 Slice K, after 0.8.
--
-- Deleting a Work cascades to every row in this file that it owns. The
-- delete command tombstones the revision scopes of what the cascade removes
-- (§5.2); later lanes decide their own tables' actions.
--
-- Parent keys for later lanes: `roles` and `argument_sources` reference
-- `manifestations (library_id, id, work_id)`; `annotations` references
-- `assets (library_id, id, work_id)`.

CREATE TABLE prks.works (
  library_id text NOT NULL REFERENCES prks.libraries,
  id         text PRIMARY KEY CHECK (id ~ '^W-[0-9A-F]{32}$'),
  title      text NOT NULL,                -- the canonical title the Work is filed under
  abstract   text,                         -- a Manifestation may override it
  status     text NOT NULL DEFAULT 'Not Started'
             CHECK (status IN ('Planned', 'In Progress', 'Completed', 'Paused', 'Not Started')),
  author_text text,                        -- free-text credit (search, cards, video channel)
  hide_pdf_link_annotations boolean NOT NULL DEFAULT false,
  last_opened_at timestamptz,              -- a max-register (MARK_WORK_OPENED)
  primary_manifestation_id  text NOT NULL, -- deferred FK below
  citation_manifestation_id text,          -- NULL = cite the primary; deferred FK below
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT works_library_id_id_key UNIQUE (library_id, id)
);
CREATE INDEX works_library_created ON prks.works (library_id, created_at DESC, id);
CREATE INDEX works_library_last_opened ON prks.works (library_id, last_opened_at DESC, id)
  WHERE last_opened_at IS NOT NULL;

-- One citeable form of a Work (UI "Version"). NULL `title`/`abstract`
-- inherit the Work's; an empty override is refused.
CREATE TABLE prks.manifestations (
  library_id text NOT NULL,
  id         text PRIMARY KEY CHECK (id ~ '^MF-[0-9A-F]{32}$'),
  work_id    text NOT NULL,
  kind       text NOT NULL DEFAULT 'unspecified'
             CHECK (kind IN ('unspecified', 'preprint', 'accepted_manuscript', 'published', 'edition',
                             'translation', 'reprint', 'web_page', 'video', 'other')),
  title      text CHECK (length(title) > 0),
  subtitle   text,
  abstract   text CHECK (length(abstract) > 0),
  language   text,                         -- BCP 47
  doc_type   text,                         -- BibTeX entry type
  year       text,                         -- bibliographic dates stay text (§6.1)
  published_date text,
  edition    text,
  publisher  text,
  location   text,
  journal    text,
  volume     text,
  issue      text,
  pages      text,
  isbn       text,
  doi        text,
  url        text,                         -- citation URL
  urldate    text,
  primary_asset_id text,                   -- deferred FK below
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT manifestations_library_id_id_key UNIQUE (library_id, id),
  CONSTRAINT manifestations_library_id_id_work_id_key UNIQUE (library_id, id, work_id),
  CONSTRAINT manifestations_work_fkey FOREIGN KEY (library_id, work_id)
    REFERENCES prks.works (library_id, id) ON DELETE CASCADE
);
CREATE INDEX manifestations_work ON prks.manifestations (library_id, work_id);

-- One file or source lineage of one Manifestation (UI "File" or "Source").
-- Catalogue only in L-W-1: bytes, cleanup claims and materialization belong
-- to the objects lane (L-A-1, §11.2). A managed file names its immutable
-- storage key and that key's hash; a stream names no stored bytes.
CREATE TABLE prks.assets (
  library_id       text NOT NULL,
  id               text PRIMARY KEY CHECK (id ~ '^AS-[0-9A-F]{32}$'),
  manifestation_id text NOT NULL,
  work_id          text NOT NULL,          -- copy of the Manifestation's Work, kept by cascade
  kind             text NOT NULL CHECK (kind IN ('managed_file', 'external_stream')),
  role             text NOT NULL DEFAULT 'document'
                   CHECK (role IN ('document', 'snapshot', 'readable_text', 'attachment', 'other')),
  storage_locator  text CHECK (length(storage_locator) > 0),  -- working bytes; a key, never a path
  source_locator   text CHECK (length(source_locator) > 0),   -- pristine bytes, when kept
  provider         text,
  provider_id      text,
  url              text,
  media_type       text,
  byte_size        bigint CHECK (byte_size >= 0),
  ingest_sha256    text CHECK (ingest_sha256 ~ '^[0-9a-f]{64}$'),
  content_sha256   text CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  -- where the bytes came from; a stream has none, so it has no origin
  origin           text CHECK (origin IN ('upload', 'processing_import', 'adopted', 'web_capture')),
  origin_url       text,
  origin_ref       text,
  derived_from_asset_id text,
  supersedes_asset_id   text,
  state            text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'trashed')),
  thumb_page       integer CHECK (thumb_page >= 1),
  thumb_url        text,
  canonical_annotation_set_revision    bigint NOT NULL DEFAULT 0
    CHECK (canonical_annotation_set_revision >= 0),
  materialized_pdf_annotation_revision bigint NOT NULL DEFAULT 0
    CHECK (materialized_pdf_annotation_revision >= 0),
  captured_at      timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT assets_kind_storage_check CHECK (
    CASE kind
      WHEN 'managed_file' THEN storage_locator IS NOT NULL AND content_sha256 IS NOT NULL
                               AND origin IS NOT NULL
      ELSE storage_locator IS NULL AND source_locator IS NULL AND origin IS NULL
               AND byte_size IS NULL AND ingest_sha256 IS NULL AND content_sha256 IS NULL
               AND origin_url IS NULL AND origin_ref IS NULL
    END),
  CONSTRAINT assets_derived_from_check CHECK (derived_from_asset_id <> id),
  CONSTRAINT assets_supersedes_check CHECK (supersedes_asset_id <> id),
  CONSTRAINT assets_library_id_id_key UNIQUE (library_id, id),
  CONSTRAINT assets_library_id_id_manifestation_id_key UNIQUE (library_id, id, manifestation_id),
  CONSTRAINT assets_library_id_id_work_id_key UNIQUE (library_id, id, work_id),
  CONSTRAINT assets_manifestation_fkey FOREIGN KEY (library_id, manifestation_id, work_id)
    REFERENCES prks.manifestations (library_id, id, work_id) ON UPDATE CASCADE ON DELETE CASCADE,
  -- a separate Asset this one was made from (any Manifestation of the Library)
  CONSTRAINT assets_derived_from_fkey FOREIGN KEY (library_id, derived_from_asset_id)
    REFERENCES prks.assets (library_id, id) ON DELETE SET NULL (derived_from_asset_id),
  -- "this file replaces that one" within one Manifestation
  CONSTRAINT assets_supersedes_fkey FOREIGN KEY (library_id, supersedes_asset_id, manifestation_id)
    REFERENCES prks.assets (library_id, id, manifestation_id) ON DELETE SET NULL (supersedes_asset_id)
);
CREATE INDEX assets_manifestation ON prks.assets (library_id, manifestation_id, work_id);
CREATE INDEX assets_work ON prks.assets (library_id, work_id);
CREATE INDEX assets_derived_from ON prks.assets (library_id, derived_from_asset_id)
  WHERE derived_from_asset_id IS NOT NULL;
CREATE INDEX assets_supersedes ON prks.assets (library_id, supersedes_asset_id, manifestation_id)
  WHERE supersedes_asset_id IS NOT NULL;
CREATE INDEX assets_storage_locator ON prks.assets (storage_locator)
  WHERE storage_locator IS NOT NULL;

-- The pointers, added once both ends exist. Deferred: checked at COMMIT.
ALTER TABLE prks.works
  ADD CONSTRAINT works_primary_manifestation_fkey
    FOREIGN KEY (library_id, primary_manifestation_id, id)
    REFERENCES prks.manifestations (library_id, id, work_id)
    DEFERRABLE INITIALLY DEFERRED,
  ADD CONSTRAINT works_citation_manifestation_fkey
    FOREIGN KEY (library_id, citation_manifestation_id, id)
    REFERENCES prks.manifestations (library_id, id, work_id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE prks.manifestations
  ADD CONSTRAINT manifestations_primary_asset_fkey
    FOREIGN KEY (library_id, primary_asset_id, id)
    REFERENCES prks.assets (library_id, id, manifestation_id)
    DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX works_primary_manifestation ON prks.works (library_id, primary_manifestation_id, id);
CREATE INDEX works_citation_manifestation ON prks.works (library_id, citation_manifestation_id, id)
  WHERE citation_manifestation_id IS NOT NULL;
CREATE INDEX manifestations_primary_asset ON prks.manifestations (library_id, primary_asset_id, id)
  WHERE primary_asset_id IS NOT NULL;

-- Typed links between Manifestations of the same Work. Both endpoints must
-- belong to the row's Work at COMMIT: a merge that moves both ends carries
-- `work_id` by cascade, and moving only one end fails.
CREATE TABLE prks.manifestation_relations (
  library_id text NOT NULL,
  work_id    text NOT NULL,
  from_id    text NOT NULL,
  to_id      text NOT NULL,
  relation   text NOT NULL CHECK (relation IN ('revision_of', 'published_version_of', 'new_edition_of',
                                               'translation_of', 'reprint_of')),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (from_id, to_id, relation),
  CONSTRAINT manifestation_relations_endpoints_check CHECK (from_id <> to_id),
  CONSTRAINT manifestation_relations_from_fkey FOREIGN KEY (library_id, from_id, work_id)
    REFERENCES prks.manifestations (library_id, id, work_id)
    ON UPDATE CASCADE ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT manifestation_relations_to_fkey FOREIGN KEY (library_id, to_id, work_id)
    REFERENCES prks.manifestations (library_id, id, work_id)
    ON UPDATE CASCADE ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX manifestation_relations_from ON prks.manifestation_relations (library_id, from_id, work_id);
CREATE INDEX manifestation_relations_to ON prks.manifestation_relations (library_id, to_id, work_id);

-- DOI, ISBN, arXiv ID and similar, with the normalized value duplicate
-- detection keys on (#60 §10.3).
CREATE TABLE prks.manifestation_identifiers (
  library_id       text NOT NULL,
  manifestation_id text NOT NULL,
  scheme           text NOT NULL CHECK (length(scheme) > 0),
  value            text NOT NULL,
  normalized       text NOT NULL CHECK (length(normalized) > 0),
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (manifestation_id, scheme, normalized),
  CONSTRAINT manifestation_identifiers_manifestation_fkey FOREIGN KEY (library_id, manifestation_id)
    REFERENCES prks.manifestations (library_id, id) ON DELETE CASCADE
);
CREATE INDEX manifestation_identifiers_lookup ON prks.manifestation_identifiers (library_id, scheme, normalized);

-- The Research Note and the Private Note of a Work, one row each, off the
-- hot Work row (§6.4 item 5). Each is its own revision scope.
CREATE TABLE prks.work_notes (
  library_id text NOT NULL,
  work_id    text NOT NULL,
  kind       text NOT NULL CHECK (kind IN ('research', 'private')),
  body       text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (work_id, kind),
  CONSTRAINT work_notes_work_fkey FOREIGN KEY (library_id, work_id)
    REFERENCES prks.works (library_id, id) ON DELETE CASCADE
);

-- Lifecycle and merge redirects of Works. Like `sync_tag_lifecycle` it
-- outlives the Work row, so it has no Work foreign key.
CREATE TABLE prks.sync_work_lifecycle (
  library_id     text NOT NULL REFERENCES prks.libraries,
  work_id        text PRIMARY KEY CHECK (work_id ~ '^W-[0-9A-F]{32}$'),
  state          text NOT NULL CHECK (state IN ('active', 'merged', 'deleted')),
  target_work_id text CHECK (target_work_id ~ '^W-[0-9A-F]{32}$'),
  changed_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT sync_work_lifecycle_target_check CHECK (
    (state = 'merged' AND target_work_id IS NOT NULL AND target_work_id <> work_id)
    OR (state <> 'merged' AND target_work_id IS NULL))
);
CREATE INDEX sync_work_lifecycle_library ON prks.sync_work_lifecycle (library_id);

-- "Not a duplicate" answers (#60 §10.5), on an ordered pair of entity IDs so
-- a decision survives unrelated edits. Canonical and backed up, with no
-- entity foreign key: a decision outlives the rows and is revisited only when
-- an ID is merged away.
CREATE TABLE prks.duplicate_decisions (
  library_id  text NOT NULL REFERENCES prks.libraries,
  entity_type text NOT NULL CHECK (entity_type IN ('work', 'manifestation', 'asset')),
  low_id      text NOT NULL,
  high_id     text NOT NULL,
  decision    text NOT NULL CHECK (decision IN ('not_duplicate')),
  decided_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (library_id, entity_type, low_id, high_id),
  CONSTRAINT duplicate_decisions_order_check CHECK (low_id < high_id COLLATE "C"),
  CONSTRAINT duplicate_decisions_ids_check CHECK (
    CASE entity_type
      WHEN 'work' THEN low_id ~ '^W-[0-9A-F]{32}$' AND high_id ~ '^W-[0-9A-F]{32}$'
      WHEN 'manifestation' THEN low_id ~ '^MF-[0-9A-F]{32}$' AND high_id ~ '^MF-[0-9A-F]{32}$'
      ELSE low_id ~ '^AS-[0-9A-F]{32}$' AND high_id ~ '^AS-[0-9A-F]{32}$'
    END)
);
