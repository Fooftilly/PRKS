# First-time setup: `prks admin bootstrap`

PostgreSQL deployments only (L-ID-1, #626; design in
`docs/postgresql-architecture.md` §9.2). Nothing in the running SQLite
application uses this yet. Until the `prks` entry point exists, run the
command as `python -m backend.domain.identity.admin`.

Setup needs a **one-time bootstrap token**. Getting a token and using it are
two separate commands, so you never handle a token that is already used up:

1. `bootstrap issue-token` creates a token and gives it to you once.
2. `bootstrap complete` uses the token to create the owner account and the
   Library. Using it consumes it.

Browser first-run setup, when it arrives, uses the same tokens.

## Before you start

- The database schema is applied (`python -m backend.db.migrate`) and
  `PRKS_DATABASE_URL` or `PRKS_DATABASE_URL_FILE` names the database.
- The data root is already bound: it has a `prks-root.json`. Both commands
  check `--root` first and refuse a missing or unbound root before asking for
  any secret.
- Both commands take `--root PATH`, the data root this database will own.

## Interactive install (a terminal)

```
python -m backend.domain.identity.admin bootstrap issue-token --root /srv/prks
```

This prints the token **once**, with its expiry (24 hours by default;
`--ttl-hours` changes it, from one minute to 30 days). Nothing else stores or shows it.

```
python -m backend.domain.identity.admin bootstrap complete --root /srv/prks --login alice
```

This asks for the token and then the owner password twice. None of them is
shown as you type. Optional: `--display-name` (defaults to the login) and
`--library-name` (defaults to `Library`). When it finishes, the token is used
up and bootstrap is closed.

## Unattended install, no token known in advance

If `issue-token` runs without a terminal, it writes the token to
`bootstrap-token` in the PRKS configuration directory. That is
`$XDG_CONFIG_HOME/prks/bootstrap-token`, or `~/.config/prks/bootstrap-token`
when `XDG_CONFIG_HOME` is unset, or the directory of `PRKS_CONFIG_FILE` when
that is set.

- The file is mode `0600`. Only its path is printed.
- The file is refused inside the data root.
- The file is written before the token is registered. If writing it fails,
  nothing is revoked, and a token delivered earlier stays usable.
- An existing file is never overwritten. Running `issue-token` again keeps it
  and registers its token: that is a no-op while the token is outstanding, and
  it finishes a run that stopped after writing the file (for example when the
  database was unreachable). If the file's token has expired or was used or
  replaced, `issue-token` refuses; delete the file and run it again.

Then complete setup with that file and an owner-only password file:

```
PRKS_BOOTSTRAP_TOKEN_FILE=~/.config/prks/bootstrap-token \
  python -m backend.domain.identity.admin bootstrap complete \
  --root /srv/prks --login alice --password-file /run/secrets/prks_owner_password
```

After setup succeeds, `complete` deletes the configuration-directory token
file, because it holds a used-up token.

## Automated install, token chosen by the installer

`PRKS_BOOTSTRAP_TOKEN_FILE` is an **input**: a file you create yourself,
mode `0600`, holding a token of 43 to 128 characters from `A-Z a-z 0-9 - _`.
For example: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

```
export PRKS_BOOTSTRAP_TOKEN_FILE=/run/secrets/prks_bootstrap_token
python -m backend.domain.identity.admin bootstrap issue-token --root /srv/prks
python -m backend.domain.identity.admin bootstrap complete --root /srv/prks \
  --login alice --password-file /run/secrets/prks_owner_password
```

- `issue-token` registers that token and never prints it. Running it again is
  a no-op while the token is outstanding. If the token has expired or was used
  or replaced, it refuses and does not extend it: write a new token to the file
  and run `issue-token` again.
- `complete` consumes the token.
- PRKS does not delete your file. Delete it once setup is done; the token in it
  cannot be used again.

## Rules every path follows

- The database stores only the token's SHA-256, with an expiry.
- Only one token is outstanding at a time. Issuing a new one revokes the
  previous one, including one that a setup still in progress was using.
- Setup checks the token before it writes anything. It consumes the token in
  the same transaction that marks the instance `ready`. If setup fails before
  that point, the token stays usable until it expires.
- If setup is interrupted (a crash, a killed container), run `complete` again
  with the same token while it is still valid, or with a newly issued one. It
  resumes where it stopped.
- Once the instance is `ready`, every bootstrap command refuses, and no token
  is issued again. If a run was stopped after setup committed but before it
  deleted the configuration-directory token file, the next bootstrap command
  deletes that file while it refuses, but only when this database issued its
  token: another deployment sharing the directory keeps its file.
- Tokens and passwords never appear in output, logs or error messages. The one
  exception is the token printed once by `issue-token` on a terminal.
- Token and password files must be regular files readable only by their owner
  (`chmod 600`). A symbolic link is refused.
- Exit codes:

  | Code | Meaning |
  | --- | --- |
  | 0 | Done |
  | 1 | Refused, with the reason on stderr |
  | 2 | Usage or configuration error |
