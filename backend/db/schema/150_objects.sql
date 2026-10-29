-- Immutable objects: cleanup claims and the restore runs that hold staged
-- objects (docs/postgresql-architecture.md §11.2, §11.4, PG14). L-A-1 (#635)
-- owns this file.
--
-- A stored object is named by its key, `namespace/name`, never by a path.
-- Its bytes never change: a rewrite writes a new key and moves the
-- catalogue to it. One data root serves every Library of the instance
-- (§6.2), so a key is referenced if ANY catalogue row names it: an Asset's
-- `storage_locator` or `source_locator` (namespace `asset-objects`), or a
-- live restore run's staged key, or a Person's cached portrait
-- (`portraits`, below).

-- One restore of a Library (§11.4). Only what the reference check needs:
-- whether the run is still live. Backup and restore (L-B-1) add the lease,
-- phase and error to this table with their own commands.
CREATE TABLE prks.restore_runs (
  id          uuid PRIMARY KEY DEFAULT uuidv7(),
  library_id  text NOT NULL REFERENCES prks.libraries ON DELETE CASCADE,
  state       text NOT NULL DEFAULT 'running' CHECK (state IN ('running', 'committed', 'failed')),
  started_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  CONSTRAINT restore_runs_finished_check CHECK ((state = 'running') = (finished_at IS NULL))
);
CREATE INDEX restore_runs_library ON prks.restore_runs (library_id);

-- The objects a restore run staged (R1). Each row protects its key from
-- cleanup while the run is `running`; a failed run protects nothing, and a
-- committed run's rows are deleted once the catalogue names the keys.
CREATE TABLE prks.restore_run_objects (
  run_id    uuid NOT NULL REFERENCES prks.restore_runs ON DELETE CASCADE,
  namespace text NOT NULL CHECK (namespace IN ('asset-objects', 'portraits')),
  name      text NOT NULL,
  CONSTRAINT restore_run_objects_name_check CHECK (
    name ~ '^[^/\\]+$' AND name !~ '^\s|\s$' AND name NOT IN ('.', '..') AND octet_length(name) <= 255
    AND left(name, 6) <> '.prks-' AND left(name, 12) <> '.linearized_'),
  PRIMARY KEY (run_id, namespace, name)
);
CREATE INDEX restore_run_objects_key ON prks.restore_run_objects (namespace, name);

-- A key that may be owed a deletion (backend/AGENTS.md, "Managed PDF
-- cleanup", with the §11.2 changes). Recorded in the transaction that stops
-- referencing the key (a Work delete, a rewrite), or by the orphan sweep for
-- a key nothing ever adopted. Settling re-checks the references under the
-- per-object lock and deletes the bytes only when none exist; a `hold`
-- claim (restore's replaced objects) is never settled automatically.
-- `library_id` is the Library whose catalogue released the key, NULL for a
-- sweep's orphan: settle takes that Library's objects lock, so a backup
-- copying the key finishes first (§11.3).
CREATE TABLE prks.object_cleanup_claims (
  namespace       text NOT NULL CHECK (namespace IN ('asset-objects', 'portraits')),
  name            text NOT NULL,
  library_id      text REFERENCES prks.libraries ON DELETE SET NULL,
  recorded_at     timestamptz NOT NULL DEFAULT now(),
  last_attempt_at timestamptz,
  hold            boolean NOT NULL DEFAULT false,
  CONSTRAINT object_cleanup_claims_name_check CHECK (
    name ~ '^[^/\\]+$' AND name !~ '^\s|\s$' AND name NOT IN ('.', '..') AND octet_length(name) <= 255
    AND left(name, 6) <> '.prks-' AND left(name, 12) <> '.linearized_'),
  PRIMARY KEY (namespace, name)
);
-- The retry pass: never-attempted claims first, then the least recently attempted.
CREATE INDEX object_cleanup_claims_due ON prks.object_cleanup_claims
  (last_attempt_at NULLS FIRST, recorded_at) WHERE NOT hold;
CREATE INDEX object_cleanup_claims_library ON prks.object_cleanup_claims (library_id)
  WHERE library_id IS NOT NULL;

-- The reference check reads both Asset locators by key, across Libraries
-- (`assets_storage_locator` is the Works schema's).
CREATE INDEX assets_source_locator ON prks.assets (source_locator)
  WHERE source_locator IS NOT NULL;

-- A Person's cached portrait (namespace `portraits`): the image its
-- `image_url` names, fetched and transcoded (backend/person_image.py), as an
-- immutable object (backend/domain/objects/portraits.py). The four columns
-- are set together or not at all, and only for the `image_url` the bytes
-- were made from: changing the URL clears them in the same transaction, so
-- a portrait never outlives its source. `image_url` stays the Person's
-- profile field; the locator is never exposed as one.
ALTER TABLE prks.persons
  ADD COLUMN portrait_locator    text,
  ADD COLUMN portrait_sha256     text,
  ADD COLUMN portrait_byte_size  bigint,
  ADD COLUMN portrait_source_url text,
  ADD CONSTRAINT persons_portrait_check CHECK (
    (portrait_locator IS NULL AND portrait_sha256 IS NULL AND portrait_byte_size IS NULL
     AND portrait_source_url IS NULL)
    OR (portrait_locator IS NOT NULL AND portrait_sha256 IS NOT NULL AND portrait_byte_size IS NOT NULL
        AND portrait_source_url IS NOT NULL
        AND portrait_sha256 ~ '^[0-9a-f]{64}$' AND portrait_byte_size > 0
        AND portrait_source_url = image_url AND image_url <> '')),
  ADD CONSTRAINT persons_portrait_locator_check CHECK (
    portrait_locator ~ '^[^/\\]+$' AND portrait_locator !~ '^\s|\s$' AND portrait_locator NOT IN ('.', '..')
    AND octet_length(portrait_locator) <= 255
    AND left(portrait_locator, 6) <> '.prks-' AND left(portrait_locator, 12) <> '.linearized_');
-- The reference check reads portraits by key, across Libraries.
CREATE INDEX persons_portrait_locator ON prks.persons (portrait_locator)
  WHERE portrait_locator IS NOT NULL;
