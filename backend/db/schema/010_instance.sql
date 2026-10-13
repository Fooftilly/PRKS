-- The instance row (docs/postgresql-architecture.md §6.2, §9.2, §11.1).
--
-- There is no row until bootstrap (L-ID-1) claims the data root: the runner
-- creates the table and never inserts, reads or compares it. The root claim,
-- the start check, the reconcile and the start-generation bump all belong to
-- backend/domain/identity/root_pairing.py (L-ID-1).

CREATE TABLE prks.instance (           -- at most one row
  id               text PRIMARY KEY CHECK (id ~ '^IN-[0-9A-F]{32}$'),
  storage_root_id  text NOT NULL CHECK (storage_root_id ~ '^SR-[0-9a-fA-F]{32}$'),
  setup_state      text NOT NULL CHECK (setup_state IN ('unconfigured', 'bootstrapping', 'ready')),
  testing          boolean NOT NULL,    -- a testing database is never served by a non-testing process
  start_generation bigint NOT NULL DEFAULT 0 CHECK (start_generation >= 0),
  start_token      text,                -- random, drawn by each bump and by the claim
  prev_start_token text,                -- the token the last bump replaced
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX instance_singleton ON prks.instance ((true));
