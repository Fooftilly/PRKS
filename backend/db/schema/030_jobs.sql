-- The jobs table, which is also the internal outbox (docs/postgresql-architecture.md
-- §12.1, PG15). L-J-1 owns this file after PG-2 and builds the runtime
-- (claim, lease, retry, reaper, NOTIFY) on it.

CREATE TABLE prks.jobs (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  library_id    text REFERENCES prks.libraries ON DELETE CASCADE,
  library_epoch bigint,               -- libraries.epoch at enqueue; NULL iff library_id IS NULL
  kind          text NOT NULL,
  payload       jsonb NOT NULL DEFAULT '{}',
  dedupe_key    text,                 -- coalesces repeated enqueues of the same work
  singleton_key text,                 -- at most one running per key
  state         text NOT NULL DEFAULT 'queued'
                CHECK (state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
  priority      smallint NOT NULL DEFAULT 0,
  run_after     timestamptz NOT NULL DEFAULT now(),
  attempts      integer NOT NULL DEFAULT 0,
  max_attempts  integer NOT NULL DEFAULT 10,
  lease_id      uuid,                 -- minted on every claim; fences stale workers
  locked_by     text,
  locked_until  timestamptz,
  last_error    text,                 -- metadata-only, per the logging privacy rules
  progress      jsonb,
  created_by    text REFERENCES prks.users,
  created_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  CHECK ((library_id IS NULL) = (library_epoch IS NULL))
);
CREATE UNIQUE INDEX jobs_dedupe ON prks.jobs (kind, dedupe_key) WHERE state = 'queued';
CREATE UNIQUE INDEX jobs_singleton ON prks.jobs (singleton_key) WHERE state = 'running';
CREATE INDEX jobs_ready ON prks.jobs (priority DESC, run_after) WHERE state = 'queued';
CREATE INDEX jobs_expired ON prks.jobs (locked_until) WHERE state = 'running';
