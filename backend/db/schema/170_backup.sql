-- Library backup format 2 and restore runs (docs/postgresql-architecture.md
-- §11.3, §11.4, PG19). L-B-1 (#639) owns this file.
--
-- Both run tables are operational: they are on the backup's excluded list
-- (backend/domain/maintenance/tables.py), so no backup exports them and no
-- restore deletes or replaces them.

-- The lease, phase and failure of a restore run. `restore_runs` itself is in
-- 150_objects.sql, because the object reference check needs to know whether a
-- run is live. A live run renews `locked_until` while it works; recovery
-- (§11.4, "Crash recovery") fails a run whose lease expired.
ALTER TABLE prks.restore_runs
  ADD COLUMN phase         text NOT NULL DEFAULT 'validate'
    CHECK (phase IN ('validate', 'stage', 'enter', 'drain', 'commit', 'release')),
  ADD COLUMN locked_until  timestamptz NOT NULL DEFAULT now() + interval '5 minutes',
  -- The SQLSTATE of a database failure (such as '53100' or '40001') or a
  -- PRKS reason code; `error_message` is safe to show the restoring owner.
  ADD COLUMN error_code    text CHECK (error_code ~ '^[A-Za-z0-9_]{1,64}$'),
  ADD COLUMN error_message text CHECK (octet_length(error_message) <= 2000),
  -- Only a failed run carries an error. PRKS always records one when it
  -- fails a run; the check does not demand it of other writers.
  ADD CONSTRAINT restore_runs_error_check CHECK (
    (error_code IS NULL OR state = 'failed') AND (error_message IS NULL OR error_code IS NOT NULL));

-- One backup of a Library (§11.3, §11.4). A running backup holds the
-- Library's objects lock shared on its own connection for its whole run;
-- this row is what R2 checks ("refused if a backup run is active") and what
-- recovery fails once its lease expires. The archive itself is a file in the
-- data root's maintenance directory, named by `id`; the row records what
-- verification proved about it.
CREATE TABLE prks.backup_runs (
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  library_id     text NOT NULL REFERENCES prks.libraries ON DELETE CASCADE,
  state          text NOT NULL DEFAULT 'running' CHECK (state IN ('running', 'published', 'failed')),
  started_at     timestamptz NOT NULL DEFAULT now(),
  locked_until   timestamptz NOT NULL DEFAULT now() + interval '5 minutes',
  finished_at    timestamptz,
  -- Set when the archive passed verification and was published.
  archive_bytes  bigint CHECK (archive_bytes >= 0),
  archive_sha256 text CHECK (archive_sha256 ~ '^[0-9a-f]{64}$'),
  error_code     text CHECK (error_code ~ '^[A-Za-z0-9_]{1,64}$'),
  error_message  text CHECK (octet_length(error_message) <= 2000),
  CONSTRAINT backup_runs_finished_check CHECK ((state = 'running') = (finished_at IS NULL)),
  CONSTRAINT backup_runs_published_check CHECK (
    (state = 'published') = (archive_bytes IS NOT NULL AND archive_sha256 IS NOT NULL)),
  CONSTRAINT backup_runs_error_check CHECK (
    (error_code IS NULL OR state = 'failed') AND (error_message IS NULL OR error_code IS NOT NULL))
);
CREATE INDEX backup_runs_library ON prks.backup_runs (library_id, started_at);
CREATE INDEX backup_runs_running ON prks.backup_runs (library_id) WHERE state = 'running';
