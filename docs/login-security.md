# Sign-in: attempt limits, login devices and sessions

PostgreSQL deployments only (L-ID-1, #626; design in
`docs/postgresql-architecture.md` §9.3). Nothing in the running SQLite
application uses this yet, and the HTTP routes (`POST /api/login`,
`/api/session`, sign-out) arrive in a later PR. Code:
`backend/domain/identity/login.py`, `login_limiter.py`, `sessions.py` and
`secret_tokens.py`.

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
year from when it was issued.

## Sessions

A session cookie `prks_session` (256-bit random token; `HttpOnly`,
`SameSite=Strict`, `Path=/`, `Secure` when the public origin is HTTPS) names
one row of `prks.sessions`, which keeps only the token's SHA-256. A session
ends after `PRKS_SESSION_IDLE_DAYS` without use, at sign-out, when its user is
disabled, or when its Library membership is removed.

The `sessions.prune` job (when the job runtime arrives) deletes expired and
revoked sessions, ended attempt windows and login-device tokens older than a
year.
