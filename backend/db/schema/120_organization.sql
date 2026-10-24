-- Organization family: Tags and their aliases, Work and Folder tags,
-- Folders and their files, Playlists and their items, Saved Views, the Tag
-- lifecycle, and the settings split (docs/postgresql-architecture.md §6.5,
-- §8.1, §8.5). L-O-1 (#633) owns this file.
--
-- Every table is library-scoped (PG6), except `instance_settings`, and
-- reaches its parent through a composite key that includes `library_id`, as
-- in 100_works.sql and 110_people.sql.
--
-- Revision scopes and deletes (§5.2, the §13.2 revision-ownership gate).
-- The scopes are SQLite's (backend/*_sync.py), so a client's stored bases
-- mean the same on both backends:
--
-- - `work-tag / ["W-…","T-…"]` and `folder-tag / ["F-…","T-…"]`: one per
--   relationship (work_tag_sync, folder_tag_sync);
-- - `folder-field / ["F-…","field"]` and `work-folder / W-…` (folder_sync);
-- - `playlist-field / ["PL-…","field"]`, `playlist-order / PL-…` and
--   `work-playlist / W-…` (playlist_sync).
--
-- So no key into a row whose change advances a scope deletes it by cascade,
-- with one listed exception: a Work's tags go with their Work, because the
-- Work delete (backend/domain/works/commands.py) tombstones every scope whose
-- id starts with the Work's id, and a Work-Tag scope row exists whenever the
-- link does (adding the link advanced it), as for Work-scoped roles in
-- 110_people.sql. Everything else is NO ACTION, and the command that deletes
-- the parent removes the children first and advances their scopes:
--
-- - a Folder or Tag delete, or a Tag merge, removes or moves its Work and
--   Folder tags (advancing `work-tag` and `folder-tag`);
-- - a Folder delete is refused while it holds files or subfolders
--   (FOLDER_NOT_EMPTY, FOLDER_HAS_SUBFOLDERS), so the parent key is not SET
--   NULL as on SQLite, where it never fires either;
-- - a Playlist delete releases its Works (advancing `work-playlist`);
-- - a Work delete removes the Work from its folder and playlist. Filing at
--   CREATE_WORK is construction and writes no revision, so neither
--   `work-folder` nor the playlist's `playlist-order` need have a row; a
--   cascade would leave both behind un-advanced, which is why these keys
--   refuse the delete until the Work delete does it explicitly.
--
-- Aliases, key rows and settings own no scope and cascade from their owner.
--
-- Names are compared by `prks.identity_key` (§8.1), in generated key
-- columns. A Tag's name and the aliases of every Tag share one namespace, so
-- they share one key table, `tag_keys`, built exactly as `publisher_keys`:
-- two concurrent writers of one key insert the same `(library_id, key)` and
-- the second fails with 23505 (NAME_TAKEN), whether each is a name or an
-- alias. Folder titles are unique per parent, root folders included
-- (`NULLS NOT DISTINCT`, matching SQLite's `COALESCE(parent_id, '')` index),
-- and Saved View names per Library. Acyclicity of the folder tree needs the
-- whole tree, so the folder commands check it under their locks; the schema
-- refuses only a folder that is its own parent.

-- Tags. No rename and no colour editor exist (tag_sync), so a Tag's name is
-- fixed at creation and changes only by a merge, which deletes the source.
CREATE TABLE prks.tags (
  library_id text NOT NULL REFERENCES prks.libraries,
  id         text PRIMARY KEY CHECK (id ~ '^T-[0-9A-F]{32}$'),
  name       text NOT NULL CHECK (octet_length(name) <= 200),
  name_key   text NOT NULL GENERATED ALWAYS AS (prks.identity_key(name)) STORED
             CHECK (length(name_key) > 0),
  key_kind   text NOT NULL DEFAULT 'name' CHECK (key_kind = 'name'),  -- for the key reference
  color      text NOT NULL DEFAULT '#6d6cf7' CHECK (length(btrim(color)) > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT tags_library_id_id_key UNIQUE (library_id, id)
);

CREATE TABLE prks.tag_aliases (
  library_id text NOT NULL,
  id         text PRIMARY KEY CHECK (id ~ '^L-[0-9A-F]{32}$'),
  tag_id     text NOT NULL,
  alias      text NOT NULL CHECK (octet_length(alias) <= 200),
  alias_key  text NOT NULL GENERATED ALWAYS AS (prks.identity_key(alias)) STORED
             CHECK (length(alias_key) > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT tag_aliases_library_id_id_tag_id_key UNIQUE (library_id, id, tag_id),
  -- A merge moves the source's aliases to the target by updating `tag_id`.
  CONSTRAINT tag_aliases_tag_fkey FOREIGN KEY (library_id, tag_id)
    REFERENCES prks.tags (library_id, id) ON DELETE CASCADE
);
CREATE INDEX tag_aliases_tag ON prks.tag_aliases (library_id, tag_id);

-- One key row per name and per alias, and one holder per key row:
--
-- - a name row has `alias_id` NULL; a Tag has at most one (partial unique
--   index), and its deferred key `(library_id, name_key, id, 'name')` must
--   find it, so its one name row carries its name's key;
-- - an alias row names its alias, which it follows by cascade (a merge's
--   move of the alias carries the key row's `tag_id` with it), and the
--   alias's deferred key `(library_id, alias_key, tag_id, id)` must find it.
--
-- So a key row never outlives the name or alias that holds it, an alias can
-- never share a key with any Tag's name, its own included, and creating,
-- aliasing or merging writes its key rows in the same transaction, checked
-- at COMMIT.
CREATE TABLE prks.tag_keys (
  library_id text NOT NULL,
  key        text NOT NULL CHECK (length(key) > 0),
  tag_id     text NOT NULL,
  alias_id   text,                         -- NULL = the Tag's name
  kind       text NOT NULL GENERATED ALWAYS AS
             (CASE WHEN alias_id IS NULL THEN 'name' ELSE 'alias' END) STORED,
  PRIMARY KEY (library_id, key),
  CONSTRAINT tag_keys_name_ref_key UNIQUE (library_id, key, tag_id, kind),
  CONSTRAINT tag_keys_alias_ref_key UNIQUE (library_id, key, tag_id, alias_id),
  CONSTRAINT tag_keys_alias_id_key UNIQUE (alias_id),
  CONSTRAINT tag_keys_tag_fkey FOREIGN KEY (library_id, tag_id)
    REFERENCES prks.tags (library_id, id) ON DELETE CASCADE,
  CONSTRAINT tag_keys_alias_fkey FOREIGN KEY (library_id, alias_id, tag_id)
    REFERENCES prks.tag_aliases (library_id, id, tag_id) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE UNIQUE INDEX tag_keys_one_name ON prks.tag_keys (library_id, tag_id)
  WHERE alias_id IS NULL;

ALTER TABLE prks.tags
  ADD CONSTRAINT tags_name_key_fkey
    FOREIGN KEY (library_id, name_key, id, key_kind)
    REFERENCES prks.tag_keys (library_id, key, tag_id, kind)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE prks.tag_aliases
  ADD CONSTRAINT tag_aliases_alias_key_fkey
    FOREIGN KEY (library_id, alias_key, tag_id, id)
    REFERENCES prks.tag_keys (library_id, key, tag_id, alias_id)
    DEFERRABLE INITIALLY DEFERRED;

-- Tag identity outlives the Tag: a deleted Tag answers TAG_DELETED and a
-- merged one names its target (tag_sync, work_tag_sync), so the row has no
-- key to `tags`. A merge into a Tag that is merged later is flattened by the
-- merge command, as on SQLite. The target is a Tag of the same Library,
-- whose own lifecycle row therefore exists. A Tag cannot commit without its
-- lifecycle row (`tags_lifecycle_fkey`, deferred), the rule SQLite keeps in
-- `insert_tag_on_conn`.
CREATE TABLE prks.sync_tag_lifecycle (
  library_id    text NOT NULL REFERENCES prks.libraries,
  tag_id        text PRIMARY KEY CHECK (tag_id ~ '^T-[0-9A-F]{32}$'),
  state         text NOT NULL CHECK (state IN ('active', 'merged', 'deleted')),
  target_tag_id text,
  changed_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT sync_tag_lifecycle_library_id_tag_id_key UNIQUE (library_id, tag_id),
  CONSTRAINT sync_tag_lifecycle_target_check CHECK (
    (state = 'merged' AND target_tag_id IS NOT NULL AND target_tag_id <> tag_id)
    OR (state <> 'merged' AND target_tag_id IS NULL)),
  CONSTRAINT sync_tag_lifecycle_target_fkey FOREIGN KEY (library_id, target_tag_id)
    REFERENCES prks.sync_tag_lifecycle (library_id, tag_id)
);
CREATE INDEX sync_tag_lifecycle_target ON prks.sync_tag_lifecycle (library_id, target_tag_id)
  WHERE target_tag_id IS NOT NULL;

ALTER TABLE prks.tags
  ADD CONSTRAINT tags_lifecycle_fkey
    FOREIGN KEY (library_id, id) REFERENCES prks.sync_tag_lifecycle (library_id, tag_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE prks.work_tags (
  library_id text NOT NULL,
  work_id    text NOT NULL,
  tag_id     text NOT NULL,
  PRIMARY KEY (work_id, tag_id),
  -- Listed cascade: the Work delete tombstones every Work-Tag scope.
  CONSTRAINT work_tags_work_fkey FOREIGN KEY (library_id, work_id)
    REFERENCES prks.works (library_id, id) ON DELETE CASCADE,
  CONSTRAINT work_tags_tag_fkey FOREIGN KEY (library_id, tag_id)
    REFERENCES prks.tags (library_id, id)
);
CREATE INDEX work_tags_tag ON prks.work_tags (library_id, tag_id);

-- Folders. "" is the empty value of the text fields; there is no NULL, so an
-- unset field and a cleared one are one state (folder_sync compares them as
-- equal today). A title is never empty: the commands store DEFAULT_TITLE.
CREATE TABLE prks.folders (
  library_id    text NOT NULL REFERENCES prks.libraries,
  id            text PRIMARY KEY CHECK (id ~ '^F-[0-9A-F]{32}$'),
  title         text NOT NULL CHECK (octet_length(title) <= 200),
  title_key     text NOT NULL GENERATED ALWAYS AS (prks.identity_key(title)) STORED
                CHECK (length(title_key) > 0),
  description   text NOT NULL DEFAULT '' CHECK (octet_length(description) <= 4000),
  private_notes text NOT NULL DEFAULT '' CHECK (octet_length(private_notes) <= 4000),
  parent_id     text CHECK (parent_id <> id),   -- NULL = a root folder
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT folders_library_id_id_key UNIQUE (library_id, id),
  CONSTRAINT folders_library_id_parent_id_title_key_key
    UNIQUE NULLS NOT DISTINCT (library_id, parent_id, title_key),
  CONSTRAINT folders_parent_fkey FOREIGN KEY (library_id, parent_id)
    REFERENCES prks.folders (library_id, id)
);
CREATE INDEX folders_parent ON prks.folders (library_id, parent_id)
  WHERE parent_id IS NOT NULL;

-- Which folder a Work is in: at most one (I8, `work-folder` in
-- docs/work-identity-model.md), now a constraint rather than a rule only
-- `move_work_to_folder` keeps.
CREATE TABLE prks.folder_files (
  library_id text NOT NULL,
  work_id    text NOT NULL,
  folder_id  text NOT NULL,
  PRIMARY KEY (library_id, work_id),
  CONSTRAINT folder_files_work_fkey FOREIGN KEY (library_id, work_id)
    REFERENCES prks.works (library_id, id),
  CONSTRAINT folder_files_folder_fkey FOREIGN KEY (library_id, folder_id)
    REFERENCES prks.folders (library_id, id)
);
CREATE INDEX folder_files_folder ON prks.folder_files (library_id, folder_id);

CREATE TABLE prks.folder_tags (
  library_id text NOT NULL,
  folder_id  text NOT NULL,
  tag_id     text NOT NULL,
  PRIMARY KEY (folder_id, tag_id),
  CONSTRAINT folder_tags_folder_fkey FOREIGN KEY (library_id, folder_id)
    REFERENCES prks.folders (library_id, id),
  CONSTRAINT folder_tags_tag_fkey FOREIGN KEY (library_id, tag_id)
    REFERENCES prks.tags (library_id, id)
);
CREATE INDEX folder_tags_tag ON prks.folder_tags (library_id, tag_id);

-- Playlists: ordered collections of Works. Bounds are playlist_sync's.
CREATE TABLE prks.playlists (
  library_id   text NOT NULL REFERENCES prks.libraries,
  id           text PRIMARY KEY CHECK (id ~ '^PL-[0-9A-F]{32}$'),
  title        text NOT NULL CHECK (length(btrim(title)) > 0 AND octet_length(title) <= 300),
  description  text NOT NULL DEFAULT '' CHECK (octet_length(description) <= 4000),
  original_url text NOT NULL DEFAULT '' CHECK (octet_length(original_url) <= 4000),
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT playlists_library_id_id_key UNIQUE (library_id, id)
);

-- A Work is in at most one playlist (`work-playlist` is a field of the
-- Work). The order is `ORDER BY position, work_id`; positions are the
-- commands' to keep dense, as on SQLite.
CREATE TABLE prks.playlist_items (
  library_id  text    NOT NULL,
  work_id     text    NOT NULL,
  playlist_id text    NOT NULL,
  position    integer NOT NULL CHECK (position >= 0),
  PRIMARY KEY (library_id, work_id),
  CONSTRAINT playlist_items_work_fkey FOREIGN KEY (library_id, work_id)
    REFERENCES prks.works (library_id, id),
  CONSTRAINT playlist_items_playlist_fkey FOREIGN KEY (library_id, playlist_id)
    REFERENCES prks.playlists (library_id, id)
);
CREATE INDEX playlist_items_playlist ON prks.playlist_items (library_id, playlist_id, position, work_id);

-- Saved Views: named search definitions, re-run through the search service
-- and never stored as results (§8.5). The rules are
-- `normalize_saved_view_name` and `normalize_saved_view_search`
-- (backend/db_manager.py): a trimmed name of at most 80 characters without
-- control characters, unique by identity key, and one of three definition
-- shapes. The per-Library count limit is the create command's.
CREATE TABLE prks.saved_views (
  library_id       text NOT NULL REFERENCES prks.libraries,
  id               text PRIMARY KEY CHECK (id ~ '^SV-[0-9A-F]{32}$'),
  name             text NOT NULL CHECK (name = btrim(name) AND char_length(name) <= 80
                                        AND name !~ '[\x01-\x1f\x7f]'),
  name_key         text NOT NULL GENERATED ALWAYS AS (prks.identity_key(name)) STORED
                   CHECK (length(name_key) > 0),
  mode             text NOT NULL CHECK (mode IN ('all', 'advanced', 'tag')),
  search_q         text NOT NULL DEFAULT '' CHECK (char_length(search_q) <= 500),
  search_tag       text NOT NULL DEFAULT '' CHECK (char_length(search_tag) <= 200),
  search_author    text NOT NULL DEFAULT '' CHECK (char_length(search_author) <= 200),
  search_publisher text NOT NULL DEFAULT '' CHECK (char_length(search_publisher) <= 200),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT saved_views_library_id_name_key_key UNIQUE (library_id, name_key),
  CONSTRAINT saved_views_search_check CHECK (
    (search_q || search_tag || search_author || search_publisher) !~ '[\x01-\x1f\x7f]'
    AND search_q = btrim(search_q) AND search_tag = btrim(search_tag)
    AND search_author = btrim(search_author) AND search_publisher = btrim(search_publisher)),
  CONSTRAINT saved_views_definition_check CHECK (
    (mode = 'all' AND search_q <> '' AND search_tag = '' AND search_author = ''
     AND search_publisher = '')
    OR (mode = 'advanced' AND search_tag = ''
        AND (search_q <> '' OR search_author <> '' OR search_publisher <> ''))
    OR (mode = 'tag' AND search_tag <> '' AND search_q = ''))
);

-- Settings, split by scope (§6.5, #612). Which key belongs to which scope,
-- and each key's value rules, are the settings commands' (decided with
-- #612's configuration inventory), so the schema bounds only the key's
-- spelling. Values are text, as SQLite's `app_settings` stores them.
CREATE TABLE prks.library_settings (
  library_id text NOT NULL REFERENCES prks.libraries,
  key        text NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{0,63}$'),
  value      text NOT NULL DEFAULT '',
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (library_id, key)
);

CREATE TABLE prks.instance_settings (
  key        text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{0,63}$'),
  value      text NOT NULL DEFAULT '',
  updated_at timestamptz NOT NULL DEFAULT now()
);
