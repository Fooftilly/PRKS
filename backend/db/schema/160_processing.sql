-- Processing family: the inbox's files staged for import, with the roles and
-- tags they will give the Work (docs/postgresql-architecture.md §6.5, §11.3,
-- §11.5). L-PR-1 (#636) owns this file.
--
-- No absolute path is stored. The inbox directory is configuration, and
-- `rel_path` names a file relative to it in the spelling discovery wrote
-- (`/`-separated, never rewritten), the file's identity.
--
-- A row is `pending`, `error` (the last import failed; `last_error` says
-- why) or `imported`. `imported` exists only between an import's COMMIT and
-- its removal of the source file (§11.3): the import records the hash of the
-- bytes it ingested (`import_sha256`), removes the file, then deletes the
-- row, all under the Library's inbox lock. A crash in between leaves the row
-- for `processing.scan`, which removes the file only when it still has that
-- hash.
--
-- `restore_hold` marks a row a restore brought back whose file it could not
-- vouch for (§11.4, "Held rows"): the scan never deletes a held row, and
-- clears the hold only when the file at its path has `restore_sha256`.
--
-- No row owns a revision scope: there is no sync family. So every key into
-- this family may cascade, except the Tag key: a Tag delete or merge removes
-- or moves its links itself (as it does `work_tags`), and the key refuses a
-- Tag delete that missed one.

CREATE TABLE prks.processing_files (
  library_id       text NOT NULL REFERENCES prks.libraries,
  id               text PRIMARY KEY CHECK (id ~ '^PF-[0-9A-F]{32}$'),
  rel_path         text NOT NULL CHECK (length(rel_path) > 0 AND left(rel_path, 1) NOT IN ('/', '\')),
  filename         text NOT NULL,
  status           text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'error', 'imported')),
  last_error       text,
  -- The import's provenance: the Work it created, when and from which bytes.
  imported_work_id text,
  imported_at      timestamptz,
  import_sha256    text CHECK (import_sha256 ~ '^[0-9a-f]{64}$'),
  restore_hold     boolean NOT NULL DEFAULT false,
  restore_sha256   text CHECK (restore_sha256 ~ '^[0-9a-f]{64}$'),
  discovered_at    timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  -- The Work's fields as staged. "" is the empty value of the text fields.
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
  CONSTRAINT processing_files_library_id_id_key UNIQUE (library_id, id),
  CONSTRAINT processing_files_library_id_rel_path_key UNIQUE (library_id, rel_path),
  CONSTRAINT processing_files_imported_check CHECK (
    (status = 'imported') = (import_sha256 IS NOT NULL AND imported_at IS NOT NULL)),
  CONSTRAINT processing_files_restore_check CHECK (restore_sha256 IS NULL OR restore_hold),
  -- A Work merge re-points it (backend/domain/works/merge.py); a Work delete
  -- only forgets the provenance.
  CONSTRAINT processing_files_imported_work_fkey FOREIGN KEY (library_id, imported_work_id)
    REFERENCES prks.works (library_id, id) ON DELETE SET NULL (imported_work_id),
  -- A deleted target folder leaves the file staged for "Uncategorized".
  CONSTRAINT processing_files_target_folder_fkey FOREIGN KEY (library_id, target_folder_id)
    REFERENCES prks.folders (library_id, id) ON DELETE SET NULL (target_folder_id)
);
CREATE INDEX processing_files_imported_work ON prks.processing_files (library_id, imported_work_id)
  WHERE imported_work_id IS NOT NULL;
CREATE INDEX processing_files_target_folder ON prks.processing_files (library_id, target_folder_id)
  WHERE target_folder_id IS NOT NULL;

-- The people a staged file names, in the order the user gave them. Ties on
-- `order_index` break on `seq`, as in `roles`. `Mentioned` is never staged
-- (work_role_sync.PEOPLE_ROLE_TYPES).
CREATE TABLE prks.processing_file_roles (
  library_id         text    NOT NULL,
  processing_file_id text    NOT NULL,
  person_id          text    NOT NULL,
  role_type          text    NOT NULL
                     CHECK (role_type IN ('Author', 'Editor', 'Reviewer', 'Translator',
                                          'Introduction', 'Foreword', 'Afterword')),
  order_index        integer NOT NULL CHECK (order_index >= 0),
  seq                bigint  NOT NULL GENERATED BY DEFAULT AS IDENTITY,
  CONSTRAINT processing_file_roles_pkey PRIMARY KEY (processing_file_id, person_id, role_type),
  CONSTRAINT processing_file_roles_seq_key UNIQUE (seq),
  CONSTRAINT processing_file_roles_file_fkey FOREIGN KEY (library_id, processing_file_id)
    REFERENCES prks.processing_files (library_id, id) ON DELETE CASCADE,
  CONSTRAINT processing_file_roles_person_fkey FOREIGN KEY (library_id, person_id)
    REFERENCES prks.persons (library_id, id) ON DELETE CASCADE
);
CREATE INDEX processing_file_roles_person ON prks.processing_file_roles (library_id, person_id);

CREATE TABLE prks.processing_file_tags (
  library_id         text NOT NULL,
  processing_file_id text NOT NULL,
  tag_id             text NOT NULL,
  CONSTRAINT processing_file_tags_pkey PRIMARY KEY (processing_file_id, tag_id),
  CONSTRAINT processing_file_tags_file_fkey FOREIGN KEY (library_id, processing_file_id)
    REFERENCES prks.processing_files (library_id, id) ON DELETE CASCADE,
  -- No cascade: a Tag delete removes these links and a merge moves them.
  CONSTRAINT processing_file_tags_tag_fkey FOREIGN KEY (library_id, tag_id)
    REFERENCES prks.tags (library_id, id)
);
CREATE INDEX processing_file_tags_tag ON prks.processing_file_tags (library_id, tag_id);
