# First-time setup: `prks admin bootstrap`

L-ID-1 (#626); design in `docs/postgresql-architecture.md` §9.2. Every new
PRKS instance needs it once: until setup completes, the web process waits and
serves only the browser setup page (below; a `--testing` run in
`loopback-owner` mode on a fresh testing database bootstraps its own owner
instead). Run the command as `python prks_app.py admin …`
(the same as `python -m backend.domain.identity.admin …`, used below).

Setup needs a **one-time bootstrap token**. Getting a token and using it are
two separate commands, so you never handle a token that is already used up:

1. `bootstrap issue-token` creates a token and gives it to you once.
2. `bootstrap complete` uses the token to create the owner account and the
   Library. Using it consumes it.

Browser setup (#612) uses the same tokens: `issue-token` gives you one, and
the browser uses it instead of `complete`.

## Browser setup

While setup is not complete, the web process serves a **setup phase** on its
normal address and port: the setup page for every page path, `GET`/`POST
/api/setup`, `/healthz` (200) and `/readyz` (503). Every other `/api/` route
answers 503 `setup_pending`; no Library is served. The process prints the
address and the exact `issue-token` command (with its `--root`) once.

1. Run `issue-token` (any of the ways below) and copy the token.
2. Open PRKS in a browser. Enter the token, then the owner's login, optional
   display name, password (twice) and the Library's name (default
   `Library`), review, and press **Finish setup**.
3. PRKS runs the same bootstrap as `complete`, signs that browser in as the
   owner, stops the setup phase and starts serving the Library on the same
   address. The page opens it once it answers.

Rules of the setup phase:

- Without a valid token nothing is written; the token is checked before the
  password is hashed. Ten refused tokens a minute make it answer 429 for the
  rest of that minute.
- The Host and cross-site request (CSRF) rules of every PRKS mutation apply.
  The bind address is the normal one: loopback unless you chose otherwise.
  Serve it on a network only over HTTPS, as for sign-in.
- One setup runs at a time; a second tab gets "already running". An
  interrupted setup resumes with the same or a new token, from the browser or
  `complete`.
- Once setup is complete nothing reopens it: `GET /api/setup` answers
  `ready` and `POST /api/setup` 409 `already_ready`, signed in or not. A new
  browser signs in; it never sees setup again.
- The used configuration-directory token file is deleted, as by `complete`.
- If the setup page cannot bind its address, the process says so and setup
  stays possible with `complete`.

## Choosing the Library folder before setup

The Library's files live in one data folder, paired with the database the
moment setup completes. Before that, and only when nothing on the host sets
the folder (no `--storage-root`, no `PRKS_STORAGE`), you may pick another
folder:

- **Command line:** `python prks_app.py storage choose PATH` (stop PRKS
  first; it refuses while a PRKS process runs on the current folder).
- **Setup page:** on the token step, open *Use a different folder for the
  Library's files*, enter the token and the folder, and press **Use this
  folder**. Setup then waits for a restart.

Either way PRKS validates the folder as a start would (an empty or new
folder, or an existing PRKS folder no database has claimed), records it in
the bootstrap file and uses it from the next start. Restart PRKS, then
finish setup with the same token. Refused, with nothing written: testing
mode, a folder the host sets, any choice once setup has claimed the current
folder, a folder that belongs to another database ("open another library" is
not supported: use a storage move or a new installation and a Library
restore), a folder another PRKS process holds, and every folder a start
would refuse. A process that read the old selection and starts after a
choice refuses to serve and asks to be started again.

## Before you start

- The database schema is applied (`python prks_app.py migrate`; a web
  process with `PRKS_AUTH` also applies it at start unless
  `PRKS_MIGRATE_ON_START=0`) and
  `PRKS_DATABASE_URL` or `PRKS_DATABASE_URL_FILE` names the database.
- The data root is already bound: it has a `prks-root.json`. Both commands
  check `--root` first and refuse a missing or unbound root before asking for
  any secret. A web process with `PRKS_AUTH` started on a new root binds it,
  then waits (serving only the setup page, `/readyz` 503) until setup is complete;
  run these commands meanwhile, beside it. Once setup is complete it runs its
  start check and starts serving.
- Both commands take `--root PATH`, the data root this database will own.
- On Windows, use the interactive (terminal) install. Token and password files
  are refused there, because their owner-only protection is an ACL these
  commands cannot check.

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
- If registering the token fails (for example when the database is
  unreachable), the file just written is deleted and no token is issued; run
  `issue-token` again.
- An existing file is never overwritten. Running `issue-token` again keeps it
  when this database issued its token: that is a no-op while the token is
  outstanding. If the file's token has expired or was used or replaced,
  `issue-token` refuses; delete the file and run it again.
- `issue-token` refuses a file whose token this database never issued. Several
  deployments can share one configuration directory, so such a file may be
  another deployment's, or belong to an `issue-token` run that is still
  registering it. If neither is so (a run was killed after writing it), delete
  the file and run `issue-token` again.
- Writing the file and the deletions after setup hold a lock on the
  configuration directory, so a deletion never removes a file another
  deployment wrote in the meantime.

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
