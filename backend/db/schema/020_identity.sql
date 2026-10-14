-- Users, Libraries, memberships and sessions (docs/postgresql-architecture.md
-- §6.2, §9). L-ID-1 owns this file after PG-2 and adds bootstrap_tokens,
-- login_attempts and login_device_tokens with the behavior that uses them.

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
  token_sha256 bytea NOT NULL UNIQUE,  -- the cookie holds the token, the database its hash
  user_id      text NOT NULL,
  library_id   text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  last_used_at timestamptz NOT NULL DEFAULT now(),
  expires_at   timestamptz NOT NULL,
  revoked_at   timestamptz,
  FOREIGN KEY (library_id, user_id)    -- a session exists only while its membership does
    REFERENCES prks.library_memberships ON DELETE CASCADE
);
