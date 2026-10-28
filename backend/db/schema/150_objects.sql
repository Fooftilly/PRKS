-- Immutable objects: cleanup claims and the restore runs that hold staged
-- objects (docs/postgresql-architecture.md §11.2, §11.4, PG14). L-A-1 (#635)
-- owns this file.
--
-- A stored object is named by its key, `namespace/name`, never by a path.
-- Its bytes never change: a rewrite writes a new key and moves the
-- catalogue to it. One data root serves every Library of the instance
-- (§6.2), so a key is referenced if ANY catalogue row names it: an Asset's
-- `storage_locator` or `source_locator` (namespace `asset-objects`), or a
-- live restore run's staged key. Person portraits get their catalogue
-- column with the portrait migration; until then a `portraits` key is never
-- known to be unreferenced (backend/domain/objects).

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
