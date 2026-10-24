-- Users, Libraries, memberships, sessions, the login attempt limiter,
-- login-device tokens and bootstrap tokens (docs/postgresql-architecture.md
-- §6.2, §9). L-ID-1 owns this file after PG-2.

CREATE TABLE prks.users (
  id            text PRIMARY KEY CHECK (id ~ '^U-[0-9A-F]{32}$'),
  login_name    text NOT NULL,
  -- Identity key of the login name (§8.1); unique, so two spellings of one
  -- name are one account.
  login_key     text NOT NULL GENERATED ALWAYS AS (prks.identity_key(login_name)) STORED
                UNIQUE CHECK (login_key <> ''),
  display_name  text NOT NULL,
  password_hash text,                  -- argon2id; NULL until set
  disabled_at   timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE prks.libraries (
  id              text PRIMARY KEY CHECK (id ~ '^LIB-[0-9A-F]{32}$'),
  name            text NOT NULL,
  maintenance     text CHECK (maintenance IN ('restore', 'rebuild')),
  maintenance_run uuid,                -- the restore/rebuild run that set it (§11.4)
  epoch           bigint NOT NULL DEFAULT 1 CHECK (epoch >= 1),  -- +1 on every restore (§11.4)
  search_folding  text,                -- folding of the last completed search+text rebuild (§8.6)
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE prks.library_memberships (
  library_id text NOT NULL REFERENCES prks.libraries ON DELETE CASCADE,
  user_id    text NOT NULL REFERENCES prks.users ON DELETE CASCADE,
  role       text NOT NULL CHECK (role IN ('owner', 'editor', 'reader')),
  PRIMARY KEY (library_id, user_id)
);

CREATE TABLE prks.sessions (
  id           uuid PRIMARY KEY DEFAULT uuidv7(),
  -- the cookie holds the token, the database its hash
  token_sha256 bytea NOT NULL UNIQUE CHECK (octet_length(token_sha256) = 32),
  user_id      text NOT NULL,
  library_id   text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  last_used_at timestamptz NOT NULL DEFAULT now(),
  expires_at   timestamptz NOT NULL,
  revoked_at   timestamptz,
  FOREIGN KEY (library_id, user_id)    -- a session exists only while its membership does
    REFERENCES prks.library_memberships ON DELETE CASCADE
);
CREATE INDEX sessions_user_id ON prks.sessions (user_id);
CREATE INDEX sessions_expires_at ON prks.sessions (expires_at);

-- Login attempt limiter (§9.3). One row per key: `acct:<login_key>`,
-- `ip:<address or IPv6 /64>` or `dev:<login-device token hash, hex>`. Each
-- attempt is counted by one upsert committed on its own, before any password
-- check; a window older than the configured length starts over at 1.
CREATE TABLE prks.login_attempts (
  key          text PRIMARY KEY CHECK (key ~ '^(acct|ip|dev):.+$'),
  attempts     integer NOT NULL CHECK (attempts >= 1),
  window_start timestamptz NOT NULL
);
CREATE INDEX login_attempts_window_start ON prks.login_attempts (window_start);

-- Known login devices (§9.3, the OWASP device-cookie pattern). The
-- `prks_login_device` cookie holds a 256-bit token; the database keeps only
-- its SHA-256. Every successful password login deletes the token it
-- presented and stores a new one, so a token is valid for one year from its
-- creation and until its next successful use. `last_used_at` records the
-- last attempt it gated; expired rows are deleted by `sessions.prune`.
CREATE TABLE prks.login_device_tokens (
  token_sha256 bytea PRIMARY KEY CHECK (octet_length(token_sha256) = 32),
  user_id      text NOT NULL REFERENCES prks.users ON DELETE CASCADE,
  created_at   timestamptz NOT NULL DEFAULT now(),
  last_used_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX login_device_tokens_user_id ON prks.login_device_tokens (user_id);
CREATE INDEX login_device_tokens_created_at ON prks.login_device_tokens (created_at);

-- One-time bootstrap tokens (§9.2). The database keeps only the SHA-256 of a
-- token. At most one token is outstanding (neither consumed nor revoked):
-- issuing a token revokes the one before it, and setup consumes it in the
-- transaction that marks the instance ready.
CREATE TABLE prks.bootstrap_tokens (
  id           uuid PRIMARY KEY DEFAULT uuidv7(),
  token_sha256 bytea NOT NULL UNIQUE CHECK (octet_length(token_sha256) = 32),
  created_at   timestamptz NOT NULL DEFAULT now(),
  expires_at   timestamptz NOT NULL,
  consumed_at  timestamptz,
  revoked_at   timestamptz,
  CHECK (expires_at > created_at),
  CHECK (consumed_at IS NULL OR revoked_at IS NULL)
);
CREATE UNIQUE INDEX bootstrap_tokens_one_outstanding ON prks.bootstrap_tokens ((true))
  WHERE consumed_at IS NULL AND revoked_at IS NULL;
