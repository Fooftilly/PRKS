# Sign-in: attempt limits, login devices and sessions

PostgreSQL deployments only (L-ID-1, #626; design in
`docs/postgresql-architecture.md` §9.3). Sign-in is off unless `PRKS_AUTH` is
set (see "Turning sign-in on" below); the SQLite application without it is
unchanged. Code: `backend/domain/identity/login.py`, `login_limiter.py`,
`sessions.py`, `secret_tokens.py` and `web_start.py`, and the HTTP boundary
`backend/api/session.py`.

## Attempt limits

Every password login is counted before the password is checked. A login over
a limit is refused with HTTP 429 and a `Retry-After` header until its window
ends, without any password check. The window starts at the first attempt and
lasts 15 minutes by default.

| Counted under | When | Default limit |
| --- | --- | --- |
| the account (`acct:`) | a browser that is not a known login device for that account, from any address | 5 per window |
| the client address (`ip:`) | the same logins, by address; an IPv6 address counts by its `/64` | 20 per window |
| the login device (`dev:`) | a browser presenting a valid login-device cookie for the account it names | 5 per window |

A successful login clears the account or device count that gated it, never
the address count. A browser the owner has signed in from before is counted
only under its device, so a guessing campaign against the account never
locks the owner out of it.

Wrong passwords, unknown logins, disabled accounts and accounts without a
password all get the same answer (`invalid_credentials`) and cost the same
Argon2id work, so the answer never tells whether an account exists.

### Before changing the Argon2id parameters

The parameters are pinned in `backend/domain/identity/passwords.py`
(`PARAMETERS`, the RFC 9106 low-memory profile). Every stored hash today uses
them, so failed logins cost the same whether or not the account exists.

Changing them is a known future risk, deferred on purpose (#677 review), not a
false positive. A stored hash keeps its old parameters until its account's
next successful login. A failed login for a missing account costs what the
new parameters cost. A wrong password against an account that has not been
rehashed yet would take a different time, which reveals that the account
exists. Rehashing at the next successful login does not close that gap.

A parameter change therefore requires, before it ships:

- a separate security review of the migration;
- tests that measure failed-login timing for accounts with old and new
  hashes and for missing accounts, and show they cannot be told apart.

### Settings

| Variable | Default | Range |
| --- | --- | --- |
| `PRKS_LOGIN_WINDOW_SECONDS` | `900` | 1 to 86400 |
| `PRKS_LOGIN_ACCOUNT_LIMIT` | `5` | 1 to 10000 |
| `PRKS_LOGIN_ADDRESS_LIMIT` | `20` | 1 to 10000 |
| `PRKS_LOGIN_DEVICE_LIMIT` | `5` | 1 to 10000 |
| `PRKS_TRUSTED_PROXIES` | empty | addresses or CIDR networks, comma- or space-separated |
| `PRKS_SESSION_IDLE_DAYS` | `30` | 1 to 365 |

An unusable value is refused when the settings are read, never replaced by a
default.

**Behind a reverse proxy**, list the proxy in `PRKS_TRUSTED_PROXIES`.
Otherwise every login counts under the proxy's address and 20 failed logins
from anyone block everyone. `X-Forwarded-For` is read only when the
connection comes from a listed proxy, walking the header from the right past
listed hops, so a client cannot pick its own address.

### Unlocking an account

When the account limit is tripped and the owner needs to sign in from a new
browser, run on the host:

```bash
python -m backend.domain.identity.admin unlock-login <login> --root /path/to/data-root
```

Like every command that changes data, it first checks that the database is
the one paired with that data root (and records a start), so it refuses a
copied, restored or foreign database. Add `--testing` for a disposable
testing instance.

It clears only that account's count; address and device counts stay. The
login is matched the way sign-in matches it (case and spacing do not matter).
An unknown login is refused with exit status 1.

## Login devices

A successful password login sets a `prks_login_device` cookie (256-bit random
token; `HttpOnly`, `SameSite=Strict`, `Path=/api/login`, `Secure` on HTTPS,
one year). The database keeps only its SHA-256. Each successful login replaces
it with a new one; a token is accepted only for its own account and for one
year from when it was issued. A token is replaced only once: when two logins
present it at the same time, only the first signs in. The other is refused
with `invalid_credentials` and nothing changes, as is a login whose token is
revoked while its password is checked; signing in again then counts as a new
browser.

## Sessions

A session cookie `prks_session` (256-bit random token; `HttpOnly`,
`SameSite=Strict`, `Path=/`, `Secure` when the public origin is HTTPS) names
one row of `prks.sessions`, which keeps only the token's SHA-256. A session
ends after `PRKS_SESSION_IDLE_DAYS` without use, at sign-out, when its user is
disabled, or when its Library membership is removed.

The `sessions.prune` job (when the job runtime arrives) deletes expired and
revoked sessions, ended attempt windows and login-device tokens older than a
year.

## Turning sign-in on

| Variable | Values |
| --- | --- |
| `PRKS_AUTH` | unset (no sign-in, the SQLite application as before), `session`, or `loopback-owner` |
| `PRKS_DATABASE_URL` / `PRKS_DATABASE_URL_FILE` | the PostgreSQL database; required by both modes |
| `PRKS_PUBLIC_ORIGIN` | the origin browsers use behind an HTTPS reverse proxy, such as `https://prks.example.org` |

Before it opens its storage root, the server runs the same start check as
every PostgreSQL process (`docs/postgresql-architecture.md` §11.1): it refuses
to start, with exit status 2, when the database belongs to another root, is a
stale copy, is a testing database started without `--testing` (or the
reverse), has no completed setup (`docs/admin-bootstrap.md`), or holds more than one
Library (the SQLite store under the root holds one Library's content). The check
holds the root's shared lease only while it runs; the server then takes the
exclusive single-process lease as before, so `admin unlock-login` (which needs
the shared lease) runs while the server is stopped.

**`session`.** Every `/api/` route except the three below needs a
`prks_session` cookie.

| Route | Answer |
| --- | --- |
| `POST /api/login` `{"login", "password"}` | 200 with both cookies and the session (as below); 401 `invalid_credentials`; 429 `too_many_attempts` with `Retry-After`; 503 `unavailable` |
| `GET /api/session` | 200 `{auth_mode, instance_id, user: {id, display_name}, library: {id, name, role, epoch}, expires_at}`, or 401 `not_authenticated` |
| `POST /api/logout` | 200; the session is revoked and its cookie cleared |

**`loopback-owner`** is for development and local testing. Every request acts
as the owner through an ordinary server-side session that never leaves the
process; there is no login or logout. The server refuses to start in this
mode unless it is a testing instance (`--testing`) bound to a loopback
address (`--host 127.0.0.1` or `--host ::1`), and it answers no peer that is
not a loopback address.

### What every other route needs

They are checked in this order, and the first failure answers:

1. A live session for the Library this server serves (401
   `not_authenticated`).
2. For a change to Library content, the `PRKS-Library-Epoch` header, exactly
   once, as a whole number from 1 to 9223372036854775807 (the largest
   PostgreSQL `bigint`) without sign, spaces or leading zeros (400
   `LIBRARY_EPOCH_REQUIRED`). Its format says nothing about the Library.
   Reads, browser error reports and sign-in routes take no epoch.
3. The route's capability under the current membership (403 `forbidden`):
   reading needs a reader, changing Library content an editor, backups and
   Library settings the owner. A changed role or a removed membership applies
   to the next request.
4. Only then, so that only an authorized user learns the Library's state:
   the Library not under maintenance (503 `MAINTENANCE`), and the header
   equal to the Library's current epoch (409 `LIBRARY_EPOCH_CHANGED`). A
   signed-out user or a reader sending a stale epoch gets 401 or 403, never
   409.

Admitted responses carry the Library's epoch in `PRKS-Library-Epoch`.

### What admission does not cover before the cutover

Until the SQLite-to-PostgreSQL cutover, a route's work still runs on the
SQLite store. Admission is a separate PostgreSQL transaction that authorizes
the request and checks the epoch, then **commits before** the SQLite route
runs. It is a gate in front of the route, not transactional authorization of
the route's write:

- A sign-out, a demotion, a removed membership or a disabled account that
  commits after admission does not stop a request already admitted; it
  applies from the next request. (In a PostgreSQL command, the same change
  waits for the write in flight.)
- The epoch fence covers only the moment of admission, and nothing on the
  SQLite side advances the epoch: today's SQLite backup restore does not
  change it, and the PostgreSQL restore that does (L-B-1) does not exist yet.
  Once Library commands run inside `library_command`, the fence holds until
  their commit.
- Admission does not check which rows a route touches. The SQLite store has
  one Library, so the server serves exactly one: it refuses to start with
  several, and a session for any other Library is nobody (401).
- The browser sends no device id, so device ownership is not part of HTTP
  admission yet.

None of this is a fault of the gate; it is why `PRKS_AUTH` stays opt-in and
off for production until the cutover (#614). Each Stage 2 lane moves its
family's commands into `library_command`, and the cutover's switch (PG-C2,
#641) removes the last SQLite route. From then on, every Library write runs
inside the transaction that authorizes it and checks its epoch.

### Cross-site requests

Every `/api/` change (sign-in and sign-out included) must come from the PRKS
page itself: `Sec-Fetch-Site`, when sent, must be `same-origin`, and the
`Origin` header must be the expected origin. Without an `Origin`,
`Sec-Fetch-Site: same-origin` is required. The expected origin is
`PRKS_PUBLIC_ORIGIN` when set (and then only it), otherwise
`http://<the Host the request was sent to>`. `X-Forwarded-Host`,
`X-Forwarded-Proto` and `Forwarded` are never read. Anything else gets 403
`origin_not_allowed` before the request is counted or authenticated.

Cookies are `Secure` when `PRKS_PUBLIC_ORIGIN` is an `https` origin. Its host
is accepted as a request host without listing it in `PRKS_TRUSTED_HOSTS`.
