-- Sync identity, revisions and the operation ledger (docs/postgresql-architecture.md
-- §6.2, §6.6). The durable-operation semantics stay those of
-- docs/local-first-sync.md; only the storage moves (PG18).

CREATE TABLE prks.devices (            -- client/sync identity, never authentication
  id         uuid PRIMARY KEY,         -- today's device_id
  user_id    text NOT NULL REFERENCES prks.users ON DELETE CASCADE,
  label      text,
  first_seen timestamptz NOT NULL DEFAULT now(),
  last_seen  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE prks.entity_revisions (
  library_id text   NOT NULL REFERENCES prks.libraries ON DELETE CASCADE,
  scope_type text   NOT NULL,
  scope_id   text   NOT NULL,          -- canonical JSON array text, unchanged format
  revision   bigint NOT NULL CHECK (revision >= 0),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (library_id, scope_type, scope_id)
);

CREATE TABLE prks.sync_operations (
  op_id          uuid PRIMARY KEY,
  library_id     text NOT NULL REFERENCES prks.libraries ON DELETE CASCADE,
  device_id      uuid NOT NULL REFERENCES prks.devices,
  user_id        text NOT NULL REFERENCES prks.users,
  operation_type text NOT NULL,
  entity_type    text NOT NULL,
  entity_id      text NOT NULL,
  request_sha256 bytea NOT NULL,
  status         text NOT NULL,
  http_status    smallint NOT NULL,
  result         jsonb NOT NULL,
  applied_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sync_operations_device ON prks.sync_operations (device_id, applied_at);
