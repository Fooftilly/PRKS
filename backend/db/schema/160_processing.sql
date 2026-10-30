-- Processing queue: PDFs uploaded into managed objects and staged as drafts
-- of the Works they will become (docs/postgresql-architecture.md §11.5,
-- decision 12). L-PR-1 (#636) owns this file.
--
-- There is no inbox directory, stored path, scanner or claim: the browser
-- uploads each PDF into a fresh `asset-objects` key, and an item exists
-- only once its bytes are durable and adopted (§11.2). `ready` is the only
-- live state. `imported` and `deleted` are terminal markers, kept forever:
-- they hold the upload key, so a replayed upload, import or delete answers
-- what happened instead of doing it again.
--
-- The key moves from the item to the Asset inside the import's transaction,
-- under its per-object lock, so the global reference check
-- (backend/domain/objects/lifecycle.py) sees one of the two at every moment.

CREATE TABLE prks.processing_items (
  library_id        text NOT NULL REFERENCES prks.libraries,
  id                text PRIMARY KEY CHECK (id ~ '^PI-[0-9A-F]{32}$'),
  -- Display only: never a path, never unique.
  original_filename text NOT NULL CHECK (octet_length(original_filename) <= 1024),
  -- The uploaded bytes while the item is `ready`.
  object_locator    text,
  sha256            text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  byte_size         bigint NOT NULL CHECK (byte_size > 0),
  status            text NOT NULL DEFAULT 'ready' CHECK (status IN ('ready', 'imported', 'deleted')),
  revision          bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
  -- The client's Idempotency-Key for the upload; reserved for good.
  upload_key        text NOT NULL CHECK (upload_key ~ '^[A-Za-z0-9._:-]{1,128}$'),
  uploaded_by       text REFERENCES prks.users ON DELETE SET NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  imported_at       timestamptz,
  imported_work_id  text,
  deleted_at        timestamptz,
  CONSTRAINT processing_items_library_id_id_key UNIQUE (library_id, id),
  -- The draft's key: a draft exists only while its item is `ready`.
  CONSTRAINT processing_items_library_id_id_status_key UNIQUE (library_id, id, status),
  CONSTRAINT processing_items_library_id_upload_key_key UNIQUE (library_id, upload_key),
  CONSTRAINT processing_items_state_check CHECK (CASE status
    WHEN 'ready' THEN object_locator IS NOT NULL AND imported_at IS NULL AND deleted_at IS NULL
                      AND imported_work_id IS NULL
    WHEN 'imported' THEN object_locator IS NULL AND imported_at IS NOT NULL AND deleted_at IS NULL
    ELSE object_locator IS NULL AND deleted_at IS NOT NULL AND imported_at IS NULL
         AND imported_work_id IS NULL
  END),
  CONSTRAINT processing_items_object_locator_check CHECK (
    object_locator ~ '^[^/\\]+$' AND object_locator !~ '^\s|\s$' AND object_locator NOT IN ('.', '..')
    AND octet_length(object_locator) <= 255
    AND left(object_locator, 6) <> '.prks-' AND left(object_locator, 12) <> '.linearized_'),
  -- The Work an import created. A Work merge re-points it
  -- (backend/domain/works/merge.py); a Work delete leaves the marker, which
  -- then answers IMPORTED_WORK_DELETED.
  CONSTRAINT processing_items_imported_work_fkey FOREIGN KEY (library_id, imported_work_id)
    REFERENCES prks.works (library_id, id) ON DELETE SET NULL (imported_work_id)
);
-- The reference check and adoption read items by key, across Libraries; a
-- fresh key is named by one item at most.
CREATE UNIQUE INDEX processing_items_object_locator ON prks.processing_items (object_locator)
  WHERE object_locator IS NOT NULL;
-- Duplicate warnings, and the queue's listing.
CREATE INDEX processing_items_sha256 ON prks.processing_items (library_id, sha256) WHERE status = 'ready';
CREATE INDEX processing_items_ready ON prks.processing_items (library_id, created_at) WHERE status = 'ready';
CREATE INDEX processing_items_imported_work ON prks.processing_items (library_id, imported_work_id)
  WHERE imported_work_id IS NOT NULL;

-- The Work as the user drafts it. "" is the empty value of the text fields.
-- `item_status` is always 'ready', so the key below refuses to move the item
-- out of `ready` while its draft exists: import and delete remove the draft
-- (with its roles and tags) first.
CREATE TABLE prks.processing_item_drafts (
  library_id       text NOT NULL,
  item_id          text PRIMARY KEY,
  item_status      text NOT NULL DEFAULT 'ready' CHECK (item_status = 'ready'),
  title            text NOT NULL DEFAULT '',
  status_draft     text NOT NULL DEFAULT 'Not Started'
                   CHECK (status_draft IN ('Planned', 'In Progress', 'Completed', 'Paused', 'Not Started')),
  published_date   text NOT NULL DEFAULT '',
  abstract         text NOT NULL DEFAULT '',
  source_url       text NOT NULL DEFAULT '',
  author_text      text NOT NULL DEFAULT '',
  year             text NOT NULL DEFAULT '',
  publisher        text NOT NULL DEFAULT '',
  location         text NOT NULL DEFAULT '',
  edition          text NOT NULL DEFAULT '',
  journal          text NOT NULL DEFAULT '',
  volume           text NOT NULL DEFAULT '',
  issue            text NOT NULL DEFAULT '',
  pages            text NOT NULL DEFAULT '',
  isbn             text NOT NULL DEFAULT '',
  doi              text NOT NULL DEFAULT '',
  doc_type         text NOT NULL DEFAULT 'article',
  private_notes    text NOT NULL DEFAULT '',
  thumb_page       integer CHECK (thumb_page >= 1),
  target_folder_id text,
  CONSTRAINT processing_item_drafts_library_id_item_id_key UNIQUE (library_id, item_id),
  CONSTRAINT processing_item_drafts_item_fkey FOREIGN KEY (library_id, item_id, item_status)
    REFERENCES prks.processing_items (library_id, id, status),
  -- A deleted target folder leaves the item for "Uncategorized".
  CONSTRAINT processing_item_drafts_target_folder_fkey FOREIGN KEY (library_id, target_folder_id)
    REFERENCES prks.folders (library_id, id) ON DELETE SET NULL (target_folder_id)
);
CREATE INDEX processing_item_drafts_target_folder ON prks.processing_item_drafts (library_id, target_folder_id)
  WHERE target_folder_id IS NOT NULL;

-- The people a draft names, in the order the user gave them. Ties on
-- `order_index` break on `seq`, as in `roles`. `Mentioned` is never drafted
-- (work_role_sync.PEOPLE_ROLE_TYPES).
CREATE TABLE prks.processing_item_roles (
  library_id  text    NOT NULL,
  item_id     text    NOT NULL,
  person_id   text    NOT NULL,
  role_type   text    NOT NULL
              CHECK (role_type IN ('Author', 'Editor', 'Reviewer', 'Translator',
                                   'Introduction', 'Foreword', 'Afterword')),
  order_index integer NOT NULL CHECK (order_index >= 0),
  seq         bigint  NOT NULL GENERATED BY DEFAULT AS IDENTITY,
  CONSTRAINT processing_item_roles_pkey PRIMARY KEY (item_id, person_id, role_type),
  CONSTRAINT processing_item_roles_seq_key UNIQUE (seq),
  CONSTRAINT processing_item_roles_draft_fkey FOREIGN KEY (library_id, item_id)
    REFERENCES prks.processing_item_drafts (library_id, item_id) ON DELETE CASCADE,
  CONSTRAINT processing_item_roles_person_fkey FOREIGN KEY (library_id, person_id)
    REFERENCES prks.persons (library_id, id) ON DELETE CASCADE
);
CREATE INDEX processing_item_roles_person ON prks.processing_item_roles (library_id, person_id);

CREATE TABLE prks.processing_item_tags (
  library_id text NOT NULL,
  item_id    text NOT NULL,
  tag_id     text NOT NULL,
  CONSTRAINT processing_item_tags_pkey PRIMARY KEY (item_id, tag_id),
  CONSTRAINT processing_item_tags_draft_fkey FOREIGN KEY (library_id, item_id)
    REFERENCES prks.processing_item_drafts (library_id, item_id) ON DELETE CASCADE,
  -- No cascade: a Tag delete removes these links and a merge moves them, as
  -- they do `work_tags`.
  CONSTRAINT processing_item_tags_tag_fkey FOREIGN KEY (library_id, tag_id)
    REFERENCES prks.tags (library_id, id)
);
CREATE INDEX processing_item_tags_tag ON prks.processing_item_tags (library_id, tag_id);
