-- People family: Persons and their aliases, Work-Person roles, Person
-- Groups and their members, Publishers and their aliases
-- (docs/postgresql-architecture.md §6.5, §8.1). L-P-1 (#632) owns this file.
--
-- Every table is library-scoped (PG6) and reaches its parent through a
-- composite key that includes `library_id`, as in 100_works.sql.
--
-- Revision scopes and deletes (§5.2, the §13.2 revision-ownership gate).
-- Persons (`person-field`), Person Groups (`person-group-field`), group
-- members (`person-group-member`) and roles (`work-person-role`) own
-- revision scopes, so no key deletes them by cascade, with one listed
-- exception: a Work-scoped role goes with its Work, because its scope is
-- `work-person-role/[work_id, person_id, role_type]` and the Work delete
-- (`backend/domain/works/commands.py`) tombstones every scope whose id
-- starts with the Work's id. A Manifestation-scoped role's scope is
-- `manifestation-person-role/[manifestation_id, person_id, role_type]`
-- (docs/work-identity-model.md §12.3), which that prefix does not reach, so
-- its Manifestation key is NO ACTION: the command that deletes a
-- Manifestation deletes its roles and tombstones their scopes first
-- (L-P-1 PR 3 extends the Work delete). Everything else is NO ACTION: a
-- Person with roles cannot be deleted (today's PERSON_HAS_LINKS), a Person
-- or group delete removes its memberships explicitly and tombstones them,
-- and a group delete reparents its children (advancing their `parent_id`
-- scopes) before the row goes. Aliases and publishers own no scope and
-- cascade from their owner.
--
-- Names are compared by `prks.identity_key` (§8.1), in generated key
-- columns. Person Group names have one unique index. A publisher's name and
-- the aliases of every publisher share one namespace, so they share one key
-- table, `publisher_keys`: two concurrent writers of one key insert the same
-- `(library_id, key)` and the second fails with 23505 (NAME_TAKEN), whether
-- each is a name or an alias.

CREATE TABLE prks.persons (
  library_id text NOT NULL REFERENCES prks.libraries,
  id         text PRIMARY KEY CHECK (id ~ '^P-[0-9A-F]{32}$'),
  -- The profile fields. "" is the empty value; there is no NULL, so an unset
  -- field and a cleared one are one state (person_metadata_sync compares them
  -- as equal today).
  first_name text NOT NULL DEFAULT '',
  last_name  text NOT NULL DEFAULT '',
  about      text NOT NULL DEFAULT '',
  image_url  text NOT NULL DEFAULT '',     -- a normalized portrait URL; stored bytes are L-A-1's
  link_wikipedia             text NOT NULL DEFAULT '',
  link_stanford_encyclopedia text NOT NULL DEFAULT '',
  link_iep                   text NOT NULL DEFAULT '',
  links_other                text NOT NULL DEFAULT '',
  birth_date text NOT NULL DEFAULT '',     -- bibliographic dates stay text (§6.1)
  death_date text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT persons_library_id_id_key UNIQUE (library_id, id)
);
CREATE INDEX persons_library_last_name ON prks.persons (library_id, last_name, id);

-- A Person's aliases, one row each, in the order the profile lists them.
-- Today's `persons.aliases` text is split on commas by every reader, so an
-- alias with a comma could not be one alias; a row cannot hold one either,
-- and the profile's `aliases` value is the rows joined by ", ". One spelling
-- per Person: two aliases with one identity key are one alias.
CREATE TABLE prks.person_aliases (
  library_id text    NOT NULL,
  person_id  text    NOT NULL,
  position   integer NOT NULL CHECK (position >= 0),
  alias      text    NOT NULL CHECK (strpos(alias, ',') = 0),
  alias_key  text    NOT NULL GENERATED ALWAYS AS (prks.identity_key(alias)) STORED
             CHECK (length(alias_key) > 0),
  PRIMARY KEY (person_id, position) DEFERRABLE INITIALLY DEFERRED,  -- a rewrite renumbers
  CONSTRAINT person_aliases_person_id_alias_key_key UNIQUE (person_id, alias_key),
  CONSTRAINT person_aliases_person_fkey FOREIGN KEY (library_id, person_id)
    REFERENCES prks.persons (library_id, id) ON DELETE CASCADE
);
CREATE INDEX person_aliases_library_key ON prks.person_aliases (library_id, alias_key);

-- Work-Person roles. One row per (Work, Person, role, scope): NULL
-- `manifestation_id` is Work scope, and one Person may hold one role on
-- several Manifestations of a Work, such as translating two translations
-- (I9, docs/work-identity-model.md §7.2). `order_index` replaces the
-- `rowid` tiebreak: it is unique within the Work, so `ORDER BY order_index`
-- alone is a total order, and it is deferrable so a renumbering (a merge, or
-- construction from a list with ties) commits once the whole Work is
-- consistent. `credit_name` is the
-- printed name on this Work; NULL is "no override", so "" is not a second
-- spelling of it.
CREATE TABLE prks.roles (
  library_id  text    NOT NULL,
  work_id     text    NOT NULL,
  person_id   text    NOT NULL,
  role_type   text    NOT NULL
              CHECK (role_type IN ('Author', 'Editor', 'Reviewer', 'Mentioned', 'Translator',
                                   'Introduction', 'Foreword', 'Afterword')),
  order_index integer NOT NULL CHECK (order_index >= 0),
  credit_name text CHECK (length(credit_name) > 0 AND octet_length(credit_name) <= 500),
  manifestation_id text,                   -- NULL = the credit holds for the whole Work
  created_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT roles_work_id_person_id_role_type_manifestation_id_key
    UNIQUE NULLS NOT DISTINCT (work_id, person_id, role_type, manifestation_id),
  CONSTRAINT roles_work_id_order_index_key UNIQUE (work_id, order_index) DEFERRABLE INITIALLY DEFERRED,
  -- Listed cascade: the Work delete tombstones every Work-scoped role scope.
  CONSTRAINT roles_work_fkey FOREIGN KEY (library_id, work_id)
    REFERENCES prks.works (library_id, id) ON DELETE CASCADE,
  -- A role scoped to one Manifestation follows it to another Work (a merge
  -- moves it); deleting the Manifestation needs its roles deleted first.
  CONSTRAINT roles_manifestation_fkey FOREIGN KEY (library_id, manifestation_id, work_id)
    REFERENCES prks.manifestations (library_id, id, work_id) ON UPDATE CASCADE,
  CONSTRAINT roles_person_fkey FOREIGN KEY (library_id, person_id)
    REFERENCES prks.persons (library_id, id)
);
CREATE INDEX roles_person ON prks.roles (library_id, person_id);
CREATE INDEX roles_work ON prks.roles (library_id, work_id, order_index);
CREATE INDEX roles_manifestation ON prks.roles (library_id, manifestation_id, work_id)
  WHERE manifestation_id IS NOT NULL;

-- Person Groups: a hierarchy of labels. Acyclicity needs the whole tree, so
-- the group commands check it under their locks; the schema refuses only a
-- group that is its own parent. Bounds are person_group_sync's.
CREATE TABLE prks.person_groups (
  library_id  text NOT NULL REFERENCES prks.libraries,
  id          text PRIMARY KEY CHECK (id ~ '^PG-[0-9A-F]{32}$'),
  name        text NOT NULL CHECK (octet_length(name) <= 200),
  name_key    text NOT NULL GENERATED ALWAYS AS (prks.identity_key(name)) STORED
              CHECK (length(name_key) > 0),
  parent_id   text CHECK (parent_id <> id),
  description text NOT NULL DEFAULT '' CHECK (octet_length(description) <= 2000),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT person_groups_library_id_id_key UNIQUE (library_id, id),
  CONSTRAINT person_groups_library_id_name_key_key UNIQUE (library_id, name_key),
  CONSTRAINT person_groups_parent_fkey FOREIGN KEY (library_id, parent_id)
    REFERENCES prks.person_groups (library_id, id)
);
CREATE INDEX person_groups_parent ON prks.person_groups (library_id, parent_id)
  WHERE parent_id IS NOT NULL;

CREATE TABLE prks.person_group_members (
  library_id text NOT NULL,
  group_id   text NOT NULL,
  person_id  text NOT NULL,
  PRIMARY KEY (group_id, person_id),
  CONSTRAINT person_group_members_group_fkey FOREIGN KEY (library_id, group_id)
    REFERENCES prks.person_groups (library_id, id),
  CONSTRAINT person_group_members_person_fkey FOREIGN KEY (library_id, person_id)
    REFERENCES prks.persons (library_id, id)
);
CREATE INDEX person_group_members_person ON prks.person_group_members (library_id, person_id);

-- Publishers and their aliases. Each name and each alias holds exactly one
-- row of `publisher_keys`, and each key row is held by exactly one of them:
--
-- - a name row has `alias_id` NULL; a publisher has at most one (partial
--   unique index), and its deferred key `(library_id, name_key, id, 'name')`
--   must find it, so its one name row carries its current name's key;
-- - an alias row names its alias, which it follows by cascade, and the
--   alias's deferred key `(library_id, alias_key, publisher_id, id)` must find
--   it, so it carries that alias's key.
--
-- So a key row never outlives the name or alias that holds it, an alias can
-- never share a key with its own publisher's name (the namespace is one
-- index), and creating, renaming or aliasing writes its key row in the same
-- transaction, checked at COMMIT.
CREATE TABLE prks.publishers (
  library_id text NOT NULL REFERENCES prks.libraries,
  id         text PRIMARY KEY CHECK (id ~ '^R-[0-9A-F]{32}$'),
  name       text NOT NULL,
  name_key   text NOT NULL GENERATED ALWAYS AS (prks.identity_key(name)) STORED
             CHECK (length(name_key) > 0),
  key_kind   text NOT NULL DEFAULT 'name' CHECK (key_kind = 'name'),  -- for the key reference
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT publishers_library_id_id_key UNIQUE (library_id, id)
);

CREATE TABLE prks.publisher_aliases (
  library_id   text NOT NULL,
  id           text PRIMARY KEY CHECK (id ~ '^M-[0-9A-F]{32}$'),
  publisher_id text NOT NULL,
  alias        text NOT NULL,
  alias_key    text NOT NULL GENERATED ALWAYS AS (prks.identity_key(alias)) STORED
               CHECK (length(alias_key) > 0),
  created_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT publisher_aliases_library_id_id_publisher_id_key UNIQUE (library_id, id, publisher_id),
  CONSTRAINT publisher_aliases_publisher_fkey FOREIGN KEY (library_id, publisher_id)
    REFERENCES prks.publishers (library_id, id) ON DELETE CASCADE
);
CREATE INDEX publisher_aliases_publisher ON prks.publisher_aliases (library_id, publisher_id);

CREATE TABLE prks.publisher_keys (
  library_id   text NOT NULL,
  key          text NOT NULL CHECK (length(key) > 0),
  publisher_id text NOT NULL,
  alias_id     text,                       -- NULL = the publisher's name
  kind         text NOT NULL GENERATED ALWAYS AS
               (CASE WHEN alias_id IS NULL THEN 'name' ELSE 'alias' END) STORED,
  PRIMARY KEY (library_id, key),
  CONSTRAINT publisher_keys_name_ref_key UNIQUE (library_id, key, publisher_id, kind),
  CONSTRAINT publisher_keys_alias_ref_key UNIQUE (library_id, key, publisher_id, alias_id),
  CONSTRAINT publisher_keys_alias_id_key UNIQUE (alias_id),
  CONSTRAINT publisher_keys_publisher_fkey FOREIGN KEY (library_id, publisher_id)
    REFERENCES prks.publishers (library_id, id) ON DELETE CASCADE,
  CONSTRAINT publisher_keys_alias_fkey FOREIGN KEY (library_id, alias_id, publisher_id)
    REFERENCES prks.publisher_aliases (library_id, id, publisher_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX publisher_keys_one_name ON prks.publisher_keys (library_id, publisher_id)
  WHERE alias_id IS NULL;

ALTER TABLE prks.publishers
  ADD CONSTRAINT publishers_name_key_fkey
    FOREIGN KEY (library_id, name_key, id, key_kind)
    REFERENCES prks.publisher_keys (library_id, key, publisher_id, kind)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE prks.publisher_aliases
  ADD CONSTRAINT publisher_aliases_alias_key_fkey
    FOREIGN KEY (library_id, alias_key, publisher_id, id)
    REFERENCES prks.publisher_keys (library_id, key, publisher_id, alias_id)
    DEFERRABLE INITIALLY DEFERRED;
