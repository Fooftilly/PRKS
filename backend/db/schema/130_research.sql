-- Research family: Concepts with their aliases and hierarchy, Positions,
-- Arguments and Stances with their targets and cited sources, and the
-- derived mentions parsed from Research Notes (docs/postgresql-architecture.md
-- §6.5, §8.1, §8.4; docs/work-identity-model.md §8.5). L-R-1 (#634) owns
-- this file.
--
-- Every canonical table is library-scoped (PG6), except the global
-- `argument_verdicts` seed, and reaches its parent through a composite key
-- that includes `library_id`, as in 100_works.sql.
--
-- Revision scopes and deletes (§5.2, the §13.2 revision-ownership gate).
-- The scopes are SQLite's (backend/concept_sync.py, position_sync.py,
-- argument_sync.py), so a client's stored bases mean the same on both
-- backends:
--
-- - `concept-field / ["C-…","description"]`, `concept-identity / C-…` (the
--   name and the alias set, one decision) and `concept-parents / C-…`, which
--   an edge change advances on both of its Concepts;
-- - `position-field / ["P-…","field"]`;
-- - `argument-field / ["A-…","field"]`, `argument-sources / A-…` and
--   `argument-targets / A-…` (each a whole ordered list).
--
-- So no key into a row whose change advances another row's scope deletes it
-- by cascade. The listed exceptions are rows that only their owner's own
-- scopes describe, and whose owner's delete tombstones those scopes: a
-- Concept's aliases and key rows (`concept-identity`), and an Argument's
-- sources and targets (`argument-sources`, `argument-targets`).
-- Everything else is NO ACTION, and the command that deletes the parent
-- removes the children first and advances their scopes, or refuses:
--
-- - a Concept delete removes its parent and child edges, advancing
--   `concept-parents` of every Concept on the other end (SQLite's cascade
--   cannot advance them, so its command reads them first);
-- - a Position an Argument targets refuses its delete (POSITION_IN_USE), and
--   so does an Argument another Argument targets (ARGUMENT_TARGETED);
-- - a Work delete removes the citations of that Work, advancing each citing
--   Argument's `argument-sources`. SQLite's cascade drops them without
--   advancing the list, so a client holding it could not see the change.
--   Until the Research commands do that, a cited Work refuses its delete.
--
-- Concept names are compared by `prks.identity_key` (§8.1), which is
-- `normalize_concept_key` (backend/research_markup.py). A Concept's name and
-- the aliases of every Concept share one namespace, so they share one key
-- table, `concept_keys`, built as `tag_keys` is: two concurrent writers of
-- one key insert the same `(library_id, key)` and the second fails with
-- 23505, whether each is a name or an alias. Note markup resolves a
-- `[[concept:…]]` reference through that index, never a full scan.
--
-- Acyclicity of the Concept hierarchy and of Argument targets needs the
-- whole graph, so the commands check it under their locks; the schema
-- refuses only the one-edge cycle.
--
-- Text bounds are research_network.py's, in characters. Names are stored
-- canonical (`" ".join(raw.split())`; Concept names NFC first): trimmed,
-- with no whitespace but single U+0020 spaces, over the set `str.split` and
-- `prks.identity_key` share. No stored text holds the control characters
-- `_CONTROL_RE` refuses.
-- "" is the empty value of the long text fields; there is no NULL.

-- Concepts. `name` is `canonical_concept_name`; its key is unique through
-- `concept_keys`, so the name and every alias of every Concept are one
-- namespace per Library.
CREATE TABLE prks.concepts (
  library_id  text NOT NULL REFERENCES prks.libraries,
  id          text PRIMARY KEY CHECK (id ~ '^C-[0-9A-F]{32}$'),
  name        text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 160 AND name IS NFC NORMALIZED
                                   AND name = btrim(name) AND name !~ '[\x01-\x1f\x7f\x85\xA0\x1680\x2000-\x200A\x2028\x2029\x202F\x205F\x3000]|  '),
  name_key    text NOT NULL GENERATED ALWAYS AS (prks.identity_key(name)) STORED
              CHECK (length(name_key) > 0),
  key_kind    text NOT NULL DEFAULT 'name' CHECK (key_kind = 'name'),  -- for the key reference
  description text NOT NULL DEFAULT '' CHECK (char_length(description) <= 100000
                                              AND description !~ '[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]'),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT concepts_library_id_id_key UNIQUE (library_id, id)
);

-- An alias is unique per Concept by key, and across the Library through
-- `concept_keys`. A rename keeps the old name as an alias (concept_sync).
CREATE TABLE prks.concept_aliases (
  library_id text NOT NULL,
  concept_id text NOT NULL,
  alias      text NOT NULL CHECK (char_length(alias) BETWEEN 1 AND 160 AND alias IS NFC NORMALIZED
                                  AND alias = btrim(alias) AND alias !~ '[\x01-\x1f\x7f\x85\xA0\x1680\x2000-\x200A\x2028\x2029\x202F\x205F\x3000]|  '),
  alias_key  text NOT NULL GENERATED ALWAYS AS (prks.identity_key(alias)) STORED
             CHECK (length(alias_key) > 0),
  key_kind   text NOT NULL DEFAULT 'alias' CHECK (key_kind = 'alias'),  -- for the key reference
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (library_id, concept_id, alias_key),
  CONSTRAINT concept_aliases_concept_fkey FOREIGN KEY (library_id, concept_id)
    REFERENCES prks.concepts (library_id, id) ON DELETE CASCADE
);

-- One key row per name and per alias, and one holder per key row:
--
-- - a name row has `alias_key` NULL; a Concept has at most one (partial
--   unique index), and its deferred key `(library_id, name_key, id, 'name')`
--   must find it, so its one name row carries its name's key;
-- - an alias row names its alias, which it follows by cascade, and the
--   alias's deferred key `(library_id, alias_key, concept_id, 'alias')` must
--   find it.
--
-- So a key row never outlives the name or alias that holds it, an alias can
-- never share a key with any Concept's name, its own included, and creating,
-- renaming or aliasing writes its key rows in the same transaction, checked
-- at COMMIT.
CREATE TABLE prks.concept_keys (
  library_id text NOT NULL,
  key        text NOT NULL CHECK (length(key) > 0),
  concept_id text NOT NULL,
  alias_key  text CHECK (alias_key = key),  -- NULL = the Concept's name
  kind       text NOT NULL GENERATED ALWAYS AS
             (CASE WHEN alias_key IS NULL THEN 'name' ELSE 'alias' END) STORED,
  PRIMARY KEY (library_id, key),
  CONSTRAINT concept_keys_ref_key UNIQUE (library_id, key, concept_id, kind),
  CONSTRAINT concept_keys_concept_fkey FOREIGN KEY (library_id, concept_id)
    REFERENCES prks.concepts (library_id, id) ON DELETE CASCADE,
  CONSTRAINT concept_keys_alias_fkey FOREIGN KEY (library_id, concept_id, alias_key)
    REFERENCES prks.concept_aliases (library_id, concept_id, alias_key) ON DELETE CASCADE
);
CREATE UNIQUE INDEX concept_keys_one_name ON prks.concept_keys (library_id, concept_id)
  WHERE alias_key IS NULL;

ALTER TABLE prks.concepts
  ADD CONSTRAINT concepts_name_key_fkey
    FOREIGN KEY (library_id, name_key, id, key_kind)
    REFERENCES prks.concept_keys (library_id, key, concept_id, kind)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE prks.concept_aliases
  ADD CONSTRAINT concept_aliases_alias_key_fkey
    FOREIGN KEY (library_id, alias_key, concept_id, key_kind)
    REFERENCES prks.concept_keys (library_id, key, concept_id, kind)
    DEFERRABLE INITIALLY DEFERRED;

-- The Concept hierarchy: a Concept may have several parents. An edge change
-- advances `concept-parents` of both Concepts, so neither key cascades.
CREATE TABLE prks.concept_parents (
  library_id        text NOT NULL,
  child_concept_id  text NOT NULL,
  parent_concept_id text NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (child_concept_id, parent_concept_id),
  CONSTRAINT concept_parents_check CHECK (child_concept_id <> parent_concept_id),
  CONSTRAINT concept_parents_child_fkey FOREIGN KEY (library_id, child_concept_id)
    REFERENCES prks.concepts (library_id, id),
  CONSTRAINT concept_parents_parent_fkey FOREIGN KEY (library_id, parent_concept_id)
    REFERENCES prks.concepts (library_id, id)
);
CREATE INDEX concept_parents_parent ON prks.concept_parents (library_id, parent_concept_id);

-- Positions: claims that Arguments and Stances target. Names are not unique.
CREATE TABLE prks.positions (
  library_id  text NOT NULL REFERENCES prks.libraries,
  id          text PRIMARY KEY CHECK (id ~ '^P-[0-9A-F]{32}$'),
  name        text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 240 AND name = btrim(name)
                                   AND name !~ '[\x01-\x1f\x7f\x85\xA0\x1680\x2000-\x200A\x2028\x2029\x202F\x205F\x3000]|  '),
  description text NOT NULL DEFAULT '' CHECK (char_length(description) <= 100000
                                              AND description !~ '[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]'),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT positions_library_id_id_key UNIQUE (library_id, id)
);

-- Arguments and Stances: structured research records, not Work metadata.
CREATE TABLE prks.arguments (
  library_id text NOT NULL REFERENCES prks.libraries,
  id         text PRIMARY KEY CHECK (id ~ '^A-[0-9A-F]{32}$'),
  name       text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200 AND name = btrim(name)
                                  AND name !~ '[\x01-\x1f\x7f\x85\xA0\x1680\x2000-\x200A\x2028\x2029\x202F\x205F\x3000]|  '),
  kind       text NOT NULL CHECK (kind IN ('argument', 'stance')),
  main_text  text NOT NULL DEFAULT '' CHECK (char_length(main_text) <= 100000
                                             AND main_text !~ '[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]'),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT arguments_library_id_id_key UNIQUE (library_id, id)
);

-- The verdict vocabulary: a global seed no backup carries (§11.4). Its rows
-- are SQLite's, so an id means the same on both backends.
CREATE TABLE prks.argument_verdicts (
  id         text PRIMARY KEY CHECK (id ~ '^[a-z][a-z_]{0,31}$'),
  label      text NOT NULL CHECK (length(btrim(label)) > 0),
  sort_order integer NOT NULL,
  enabled    boolean NOT NULL DEFAULT true
);
INSERT INTO prks.argument_verdicts (id, label, sort_order) VALUES
  ('supports', 'Supports', 1),
  ('opposes', 'Opposes', 2),
  ('qualifies', 'Qualifies', 3),
  ('holds', 'Holds', 4);

-- An Argument's targets: one ordered list (`argument-targets / A-…`) over
-- two tables, ordered by the shared `order_index`, then type and id
-- (argument_sync.current_targets). The list follows its Argument; a target
-- refuses its own delete while it is targeted.
CREATE TABLE prks.argument_target_positions (
  library_id  text    NOT NULL,
  argument_id text    NOT NULL,
  position_id text    NOT NULL,
  verdict_id  text    NOT NULL REFERENCES prks.argument_verdicts,
  order_index integer NOT NULL DEFAULT 0 CHECK (order_index >= 0),
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (argument_id, position_id),
  CONSTRAINT argument_target_positions_argument_fkey FOREIGN KEY (library_id, argument_id)
    REFERENCES prks.arguments (library_id, id) ON DELETE CASCADE,
  CONSTRAINT argument_target_positions_position_fkey FOREIGN KEY (library_id, position_id)
    REFERENCES prks.positions (library_id, id)
);
CREATE INDEX argument_target_positions_position ON prks.argument_target_positions (library_id, position_id);

CREATE TABLE prks.argument_target_arguments (
  library_id         text    NOT NULL,
  argument_id        text    NOT NULL,
  target_argument_id text    NOT NULL,
  verdict_id         text    NOT NULL REFERENCES prks.argument_verdicts,
  order_index        integer NOT NULL DEFAULT 0 CHECK (order_index >= 0),
  created_at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (argument_id, target_argument_id),
  CONSTRAINT argument_target_arguments_check CHECK (argument_id <> target_argument_id),
  CONSTRAINT argument_target_arguments_argument_fkey FOREIGN KEY (library_id, argument_id)
    REFERENCES prks.arguments (library_id, id) ON DELETE CASCADE,
  CONSTRAINT argument_target_arguments_target_fkey FOREIGN KEY (library_id, target_argument_id)
    REFERENCES prks.arguments (library_id, id)
);
CREATE INDEX argument_target_arguments_target ON prks.argument_target_arguments (library_id, target_argument_id);

-- An Argument's citations (docs/work-identity-model.md §8.5): one ordered
-- list (`argument-sources / A-…`) whose row key is the list position. A NULL
-- `manifestation_id` cites the Work in general; a pinned citation names a
-- Version of that same Work and follows it when the Version moves (ON UPDATE
-- CASCADE carries `work_id`). Only exact duplicates are refused: one
-- Argument may cite the Work in general and several of its Versions, each
-- with its own pinpoint. The Work key is NO ACTION (see the header), and a
-- pinned Version refuses its delete.
CREATE TABLE prks.argument_sources (
  library_id       text    NOT NULL,
  argument_id      text    NOT NULL,
  order_index      integer NOT NULL CHECK (order_index >= 0),
  work_id          text    NOT NULL,
  manifestation_id text,
  pages            text    NOT NULL DEFAULT '' CHECK (char_length(pages) <= 100 AND pages = btrim(pages)
                                                      AND pages !~ '[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]'),
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (argument_id, order_index),
  CONSTRAINT argument_sources_citation_key
    UNIQUE NULLS NOT DISTINCT (argument_id, work_id, manifestation_id, pages),
  CONSTRAINT argument_sources_argument_fkey FOREIGN KEY (library_id, argument_id)
    REFERENCES prks.arguments (library_id, id) ON DELETE CASCADE,
  CONSTRAINT argument_sources_work_fkey FOREIGN KEY (library_id, work_id)
    REFERENCES prks.works (library_id, id),
  CONSTRAINT argument_sources_manifestation_fkey FOREIGN KEY (library_id, manifestation_id, work_id)
    REFERENCES prks.manifestations (library_id, id, work_id) ON UPDATE CASCADE
);
CREATE INDEX argument_sources_work ON prks.argument_sources (library_id, work_id);
CREATE INDEX argument_sources_manifestation ON prks.argument_sources (library_id, manifestation_id)
  WHERE manifestation_id IS NOT NULL;

-- Derived research index (§8.4): Concept and Argument mentions parsed from a
-- Work's Research Note, and the hash of the note they were parsed from.
-- Rebuildable and excluded from backup. Only `research.reindex_work` writes
-- them, under the per-Work reindex lock, and it deletes a gone Work's rows
-- itself, so they carry no foreign key: a key would make a Work, Concept or
-- Argument delete wait on, or cascade into, an index the derived side owns.
-- They are never authority for a destructive mutation. Offsets are the
-- note's character offsets, as backend/research_markup.py reports them.
CREATE TABLE derived.work_note_state (
  library_id   text NOT NULL,
  work_id      text PRIMARY KEY,
  content_hash text NOT NULL CHECK (length(content_hash) > 0)
);
CREATE INDEX work_note_state_library ON derived.work_note_state (library_id, work_id);

CREATE TABLE derived.concept_mentions (
  library_id   text    NOT NULL,
  work_id      text    NOT NULL,
  concept_id   text    NOT NULL,
  start_offset integer NOT NULL,
  end_offset   integer NOT NULL,
  PRIMARY KEY (work_id, start_offset),
  CONSTRAINT concept_mentions_offsets_check CHECK (start_offset >= 0 AND end_offset > start_offset)
);
CREATE INDEX concept_mentions_concept ON derived.concept_mentions (library_id, concept_id);

CREATE TABLE derived.argument_mentions (
  library_id   text    NOT NULL,
  work_id      text    NOT NULL,
  argument_id  text    NOT NULL,
  start_offset integer NOT NULL,
  end_offset   integer NOT NULL,
  PRIMARY KEY (work_id, start_offset),
  CONSTRAINT argument_mentions_offsets_check CHECK (start_offset >= 0 AND end_offset > start_offset)
);
CREATE INDEX argument_mentions_argument ON derived.argument_mentions (library_id, argument_id);
