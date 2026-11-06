"""``prks admin`` (``docs/postgresql-architecture.md`` §9.2, §9.3, §12; L-ID-1 #626).

Run as ``python -m backend.domain.identity.admin …`` until the ``prks``
entry point exists (L-D-1). Five commands:

``unlock-login LOGIN --root ROOT`` deletes the account-wide (``acct:``) row
of the login attempt limiter for the account named ``LOGIN`` (matched by its
identity key, as login does), so its owner can sign in from a new browser
while a guessing campaign keeps the limit tripped. Address and device rows
are left alone. An unknown login is refused. Like every data-changing
command it first runs the start check and bump against the bound data root
(§11.1) and refuses a database that is not paired with it.

``rebuild-derived --root ROOT [--library ID] [--only search|text|research]``
queues one ``derived.rebuild`` job per Library (every Library, or the one
named), after the same start check; a worker runs it with progress while
ordinary commands keep running (§8.6, :mod:`backend.domain.search.rebuild`).
The same rebuild already queued is reported, not queued twice.

``backup --root ROOT --library ID --out DIR`` writes a verified format-2
archive of one Library into ``DIR`` and ``restore --root ROOT --library ID
--archive FILE --owner LOGIN`` restores one in place of the Library's
contents, as that owner, from a private copy in the root's maintenance area
(§11.4, :mod:`backend.domain.maintenance`). Both run after the same start
check and refuse a root that still holds a SQLite catalogue.

``bootstrap`` is first-time setup, in two separate steps, so the operator
never holds a token that is already spent:

1. ``bootstrap issue-token --root ROOT`` stores a new one-time token (24 h by
   default; ``--ttl-hours``) and delivers it exactly once:

   - with ``PRKS_BOOTSTRAP_TOKEN_FILE`` set, the token is the one the
     operator already wrote to that file (``chmod 600``); it is registered,
     never printed, and issuing it again is a no-op;
   - otherwise, on a terminal, the new token is printed to that terminal;
   - otherwise (no terminal), it is written to ``bootstrap-token`` in the
     PRKS configuration directory, ``0600``, outside the data root, and only
     the path is printed. The file is published before the token is
     registered, so a failed write revokes nothing. An existing file there is
     never overwritten: a re-run registers the token it holds (a no-op while
     that token is outstanding) and refuses when it expired or was used.

   Issuing a token revokes any earlier unused one. Once setup is ready no
   token is issued.

2. ``bootstrap complete --root ROOT --login NAME`` reads the token
   (``PRKS_BOOTSTRAP_TOKEN_FILE``, else a hidden terminal prompt) and the
   owner password (``--password-file``, else a hidden prompt typed twice),
   and runs bootstrap's three steps. The token is consumed in the
   transaction that marks setup ready; a ``bootstrap-token`` file in the
   configuration directory holding it is then deleted, and nothing ever
   prints it again. Re-running ``complete`` after a crash resumes with the
   same token while it is unexpired; after setup is ready every bootstrap
   command refuses.

The data root must already be bound (it has ``prks-root.json``). Output is
fixed text: no token (outside step 1's terminal delivery), password,
connection string or path from the database ever reaches stdout, stderr or
a log.
"""

from __future__ import annotations

import argparse
import dataclasses
import getpass
import math
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from typing import Optional, TextIO

from backend.db.config import load_database_config
from backend.db.database import Database
from backend.db.errors import DatabaseConfigError, DatabaseError
from backend.domain.identity import bootstrap_tokens, login_limiter, passwords, root_pairing
from backend.domain.identity.bootstrap_tokens import BootstrapToken
from backend.domain.identity.errors import RootPairingRefused
from backend.domain.text_identity import UnsupportedTextError, identity_key
from backend.storage.errors import StorageRootError
from backend.storage.objects import StorageBackend
from backend.storage.root_binding import acquire_shared_root_lease
from backend.storage.root_marker import MARKER_FILENAME, binding_refusal, read_marker

TOKEN_FILE_ENV = "PRKS_BOOTSTRAP_TOKEN_FILE"

# --ttl-hours bounds: at least one minute, at most 30 days.
MIN_TTL_HOURS = 1 / 60
MAX_TTL_HOURS = 30 * 24.0

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2

DatabaseFactory = Callable[[Mapping[str, str]], Database]
Prompt = Callable[[str], str]
Faults = Callable[[str], None]

# Fault-injection point for tests: the headless token file is published and
# its token is not registered yet.
FAULT_TOKEN_FILE_PUBLISHED = "issue.token_file_published"

_SPENT_TOKEN_REASONS = frozenset({"bootstrap_token_expired", "bootstrap_token_reused"})


def _no_faults(_point: str) -> None:
    return None


def _open_database(environ: Mapping[str, str]) -> Database:
    # Admin commands use the worker's small pool ceiling and open no idle
    # connection of their own.
    config = load_database_config(environ, role="worker")
    return Database(dataclasses.replace(config, pool_min=0, pool_max=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prks admin")
    commands = parser.add_subparsers(dest="command", required=True)
    boot = commands.add_parser("bootstrap", help="first-time setup of the owner and Library")
    steps = boot.add_subparsers(dest="step", required=True)

    issue = steps.add_parser("issue-token", help="issue the one-time bootstrap token")
    issue.add_argument("--root", required=True, help="the bound PRKS data root")
    issue.add_argument("--ttl-hours", type=float, default=24.0)

    complete = steps.add_parser("complete", help="create the owner and Library with the token")
    complete.add_argument("--root", required=True, help="the bound PRKS data root")
    complete.add_argument("--login", required=True)
    complete.add_argument("--display-name")
    complete.add_argument("--library-name", default="Library")
    complete.add_argument("--password-file", help="owner-only file holding the owner password")
    complete.add_argument("--testing", action="store_true", help="a disposable testing instance")

    unlock = commands.add_parser(
        "unlock-login", help="clear the account-wide login attempt limit of one account"
    )
    unlock.add_argument("login", help="the account's login name")
    unlock.add_argument("--root", required=True, help="the bound PRKS data root")
    unlock.add_argument("--testing", action="store_true", help="a disposable testing instance")

    rebuild = commands.add_parser(
        "rebuild-derived", help="queue the rebuild of the derived search, PDF text and research rows"
    )
    rebuild.add_argument("--root", required=True, help="the bound PRKS data root")
    rebuild.add_argument("--library", help="the id of one Library (default: every Library)")
    rebuild.add_argument("--only", choices=("search", "text", "research"), help="rebuild one scope only")
    rebuild.add_argument("--testing", action="store_true", help="a disposable testing instance")

    backup = commands.add_parser("backup", help="write a verified format-2 backup of one Library")
    backup.add_argument("--root", required=True, help="the bound PRKS data root")
    backup.add_argument("--library", required=True, help="the id of the Library")
    backup.add_argument("--out", required=True, help="an existing directory to write the archive into")
    backup.add_argument("--testing", action="store_true", help="a disposable testing instance")

    restore = commands.add_parser("restore", help="restore a format-2 backup in place of a Library's contents")
    restore.add_argument("--root", required=True, help="the bound PRKS data root")
    restore.add_argument("--library", required=True, help="the id of the Library to replace")
    restore.add_argument("--archive", required=True, help="the .prks-backup file")
    restore.add_argument("--owner", required=True, help="the login of an owner of the Library")
    restore.add_argument("--testing", action="store_true", help="a disposable testing instance")
    return parser


class _Io:
    def __init__(
        self,
        stdin: TextIO,
        stdout: TextIO,
        stderr: TextIO,
        prompt: Optional[Prompt],
    ) -> None:
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self._prompt = prompt
        self.command = "bootstrap"

    def terminal_out(self) -> bool:
        return _isatty(self.stdout)

    def can_prompt(self) -> bool:
        return self._prompt is not None or _isatty(self.stdin)

    def secret(self, label: str) -> str:
        if self._prompt is not None:
            return self._prompt(label)
        return getpass.getpass(label)

    def say(self, text: str) -> None:
        print(text, file=self.stdout)

    def refuse(self, text: str) -> None:
        print(f"{self.command}: {text}", file=self.stderr)


def _isatty(stream: TextIO) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


def _real_root(root: str) -> str:
    return os.path.realpath(os.path.abspath(root))


def _env_token(environ: Mapping[str, str]) -> Optional[BootstrapToken]:
    path = (environ.get(TOKEN_FILE_ENV) or "").strip()
    if not path:
        return None
    return bootstrap_tokens.read_token_file(path)


def _issue(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    db: Database,
    io: _Io,
    faults: Faults,
) -> int:
    # NaN, inf and huge values would escape as OverflowError, and a tiny one
    # rounds to a zero duration: only a whole number of seconds in range.
    hours = args.ttl_hours
    if not (math.isfinite(hours) and MIN_TTL_HOURS <= hours <= MAX_TTL_HOURS):
        io.refuse("--ttl-hours must be from one minute (1/60) to 30 days (720).")
        return EXIT_USAGE
    ttl = timedelta(seconds=round(hours * 3600))
    root = _real_root(args.root)
    _check_root_bound(root)
    supplied = _env_token(environ)
    if supplied is not None:
        try:
            issued = bootstrap_tokens.issue_token(db, ttl=ttl, token=supplied)
        except RootPairingRefused as exc:
            if exc.reason not in _SPENT_TOKEN_REASONS:
                raise
            raise RootPairingRefused(
                exc.reason,
                f"The token in {TOKEN_FILE_ENV} has expired or was already used or replaced; "
                "write a new token to that file and run issue-token again.",
            ) from None
        io.say(
            f"Registered the bootstrap token from {TOKEN_FILE_ENV}; it expires "
            f"{issued.expires_at.isoformat(timespec='seconds')}."
        )
        io.say("Next: prks admin bootstrap complete --root ROOT --login NAME")
        return EXIT_OK
    if io.terminal_out():
        issued = bootstrap_tokens.issue_token(db, ttl=ttl)
        io.say("One-time bootstrap token (shown once; it opens setup until it is used or expires):")
        io.say("")
        io.say(f"    {issued.token.reveal()}")
        io.say("")
        io.say(f"It expires {issued.expires_at.isoformat(timespec='seconds')}.")
        io.say("Next: prks admin bootstrap complete --root ROOT --login NAME (it asks for the token).")
        return EXIT_OK
    return _issue_to_file(bootstrap_tokens.default_token_file(environ), root, ttl, db, io, faults)


def _issue_to_file(
    path: str, root: str, ttl: timedelta, db: Database, io: _Io, faults: Faults
) -> int:
    """Headless delivery: the file is published before the token is registered.

    Creating the file without overwriting is the reservation, so only the
    process that published it registers a new token, and a delivery that
    fails never revokes the token an earlier run delivered. A file that
    already exists is never replaced. Its token is registered again only
    when this database issued it (a no-op while it is outstanding): the
    configuration directory can be shared by several deployments, so a file
    this database never issued is refused, never adopted. A registration
    that fails deletes the file it just published, so a rerun starts clean.
    """
    token = bootstrap_tokens.new_token()
    try:
        bootstrap_tokens.write_token_file(token, path, data_root=root)
    except RootPairingRefused as exc:
        if exc.reason == "token_file_not_durable":
            bootstrap_tokens.remove_token_file_if(path, token)
        if exc.reason != "token_file_exists":
            raise
        return _register_token_file(path, ttl, db, io)
    faults(FAULT_TOKEN_FILE_PUBLISHED)
    try:
        issued = bootstrap_tokens.issue_token(db, ttl=ttl, token=token)
    except RootPairingRefused as exc:
        # The token is not registered, so nothing can use it, and a rerun
        # would refuse the file as one this database never issued.
        bootstrap_tokens.remove_token_file_if(path, token)
        if exc.reason == "already_ready":
            raise
        raise RootPairingRefused(
            exc.reason,
            f"{exc.message} No token was issued; run issue-token again.",
            retryable=exc.retryable,
        ) from None
    io.say(f"Wrote the one-time bootstrap token to {path} (mode 0600).")
    _say_next(io, path, issued)
    return EXIT_OK


def _register_token_file(path: str, ttl: timedelta, db: Database, io: _Io) -> int:
    held = bootstrap_tokens.read_token_file(path)
    if not bootstrap_tokens.issued_here(db, held):
        raise RootPairingRefused(
            "token_file_foreign",
            f"The bootstrap token file {path} holds a token this database never issued: it "
            "belongs to another deployment sharing the configuration directory, or to an "
            "issue-token run that is still registering it or was interrupted. If none of "
            "these is so, delete the file and run issue-token again.",
        )
    try:
        issued = bootstrap_tokens.issue_token(db, ttl=ttl, token=held)
    except RootPairingRefused as exc:
        if exc.reason not in _SPENT_TOKEN_REASONS:
            raise
        raise RootPairingRefused(
            "token_file_stale",
            f"The bootstrap token file {path} holds a token that has expired or was already "
            "used or replaced; delete the file and run issue-token again.",
        ) from None
    io.say(f"The bootstrap token file {path} already exists; its token is ready to use.")
    _say_next(io, path, issued)
    return EXIT_OK


def _say_next(io: _Io, path: str, issued: bootstrap_tokens.IssuedToken) -> None:
    io.say(f"It expires {issued.expires_at.isoformat(timespec='seconds')}.")
    io.say(f"Next: {TOKEN_FILE_ENV}={path} prks admin bootstrap complete --root ROOT --login NAME")


def _read_password_file(path: str) -> str:
    data = bootstrap_tokens.read_owner_only_file(
        path, what="password", limit=passwords.MAX_PASSWORD_BYTES + 3
    )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise RootPairingRefused("password_file_unreadable", "The password file is not UTF-8.") from None
    # One trailing newline is the file's, not the password's.
    for ending in ("\r\n", "\n"):
        if text.endswith(ending):
            return text[: -len(ending)]
    return text


def _complete_token(environ: Mapping[str, str], io: _Io) -> BootstrapToken:
    supplied = _env_token(environ)
    if supplied is not None:
        return supplied
    if not io.can_prompt():
        raise RootPairingRefused(
            "bootstrap_token_missing",
            f"No bootstrap token: set {TOKEN_FILE_ENV} or run this on a terminal.",
        )
    return bootstrap_tokens.parse_token(io.secret("Bootstrap token: "))


def _complete_password(args: argparse.Namespace, io: _Io) -> str:
    if args.password_file:
        return _read_password_file(args.password_file)
    if not io.can_prompt():
        raise RootPairingRefused(
            "password_missing", "No owner password: pass --password-file or run this on a terminal."
        )
    first = io.secret("Owner password: ")
    second = io.secret("Owner password again: ")
    if first != second:
        raise RootPairingRefused("password_mismatch", "The two passwords differ.")
    return first


def _check_root_bound(root: str) -> None:
    """Refuse a missing, unbound or unbindable ``--root`` before any secret is asked for.

    The marker is read and judged by the same code ordinary startup uses, so
    a malformed, linked, retired, fenced or staging marker is refused here,
    before a token is issued or a password typed.
    """
    unbound = RootPairingRefused(
        "root_not_bound",
        f"The data root does not exist or is not bound (it has no {MARKER_FILENAME}); "
        "check --root.",
    )
    if not os.path.isdir(root):
        raise unbound
    try:
        marker = read_marker(root)
    except StorageRootError as exc:
        raise _unusable_root(exc.reason) from None
    if marker is None:
        raise unbound
    refusal = binding_refusal(marker)
    if refusal is not None:
        raise _unusable_root(refusal.reason)


def _unusable_root(reason: str) -> RootPairingRefused:
    return RootPairingRefused(reason, f"The data root cannot be used ({reason}); check --root.")


def _lease_refusal(exc: StorageRootError) -> RootPairingRefused:
    if exc.reason == "root_in_use":
        return RootPairingRefused(exc.reason, "The data root is in use by a maintenance tool.")
    if exc.reason == "root_not_writable":
        return RootPairingRefused(exc.reason, "The data root does not exist or is not writable.")
    return _unusable_root(exc.reason)


def _complete(args: argparse.Namespace, environ: Mapping[str, str], db: Database, io: _Io) -> int:
    root = _real_root(args.root)
    _check_root_bound(root)
    root_pairing.check_owner_login(args.login)  # before any secret is asked for
    token = _complete_token(environ, io)
    password = _complete_password(args, io)
    owner = root_pairing.OwnerSpec(
        login_name=args.login,
        display_name=args.display_name or args.login,
        library_name=args.library_name,
        password_hash=passwords.hash_password(password),
    )
    try:
        lease = acquire_shared_root_lease(root)
    except StorageRootError as exc:
        raise _lease_refusal(exc) from None
    try:
        result = root_pairing.bootstrap(
            db, owner, token=token, root=root, lease=lease, testing=bool(args.testing)
        )
    finally:
        lease.release()
    default_file = bootstrap_tokens.default_token_file(environ)
    removed = bootstrap_tokens.remove_token_file_if(default_file, token)
    # Setup is ready, so every token this database issued is dead, including
    # an older one whose temporary alias a crashed issuance left behind.
    try:
        bootstrap_tokens.sweep_temporary_token_files(
            os.path.dirname(default_file), lambda held: bootstrap_tokens.issued_here(db, held)
        )
    except RootPairingRefused:
        pass  # best effort; a later ready refusal sweeps again
    if result.created:
        io.say(f"Setup complete: owner {args.login!r}, Library {args.library_name!r}.")
    else:
        io.say("Setup was already completed by this token; nothing changed.")
    io.say("The bootstrap token is used up and bootstrap is now closed.")
    if removed:
        io.say(f"Deleted the used token file {default_file}.")
    if environ.get(TOKEN_FILE_ENV, "").strip():
        io.say(f"The token in {TOKEN_FILE_ENV} is used up; delete that file.")
    return EXIT_OK


def _remove_dead_token_file(environ: Mapping[str, str], db: Database, io: _Io) -> None:
    """Delete this instance's configuration-directory token file once setup is ready.

    No token can open setup after ``ready``, so a file a run left behind
    (killed after setup committed, before its own cleanup) only holds a dead
    secret. The directory can be shared by several deployments, so only a
    token this database issued is deleted. The operator's
    ``PRKS_BOOTSTRAP_TOKEN_FILE`` is never touched.
    """
    path = bootstrap_tokens.default_token_file(environ)

    def ours(token: BootstrapToken) -> bool:
        return bootstrap_tokens.issued_here(db, token)

    try:
        try:
            held: Optional[BootstrapToken] = bootstrap_tokens.read_token_file(path)
        except RootPairingRefused:
            held = None
        if held is not None and ours(held) and bootstrap_tokens.remove_token_file_if(path, held):
            io.say(f"Deleted the used token file {path}.")
        # Whoever owns the canonical file, this database's own temporary
        # leftovers are dead secrets too.
        bootstrap_tokens.sweep_temporary_token_files(os.path.dirname(path), ours)
    except RootPairingRefused:
        # Cleanup is best effort; the caller still refuses with already_ready.
        return


def _started_root(args: argparse.Namespace, db: Database, fn: Callable[[], int]) -> int:
    """Run ``fn`` after the start check and bump (§11.1) under the shared root lease.

    A data-changing admin command never changes a database that is not the
    one paired with this data root, nor one whose setup is not complete.
    """
    root = _real_root(args.root)
    _check_root_bound(root)
    try:
        lease = acquire_shared_root_lease(root)
    except StorageRootError as exc:
        raise _lease_refusal(exc) from None
    try:
        started = root_pairing.start_process(
            db, root=root, lease=lease, testing=bool(args.testing), bump=True
        )
        if started.status is not root_pairing.StartStatus.READY:
            raise RootPairingRefused("setup_pending", "Setup is not complete; there is nothing to change yet.")
        return fn()
    finally:
        lease.release()


def _unlock_login(args: argparse.Namespace, db: Database, io: _Io) -> int:
    try:
        identity_key(args.login)
    except UnsupportedTextError:  # no stored login holds such text
        io.refuse("No account has that login.")
        return EXIT_REFUSED
    unlocked: list[Optional[bool]] = []

    def unlock() -> int:
        try:
            unlocked.append(login_limiter.unlock_account(db, args.login))
        except DatabaseError:
            io.refuse("The database could not be reached; nothing was changed.")
            return EXIT_REFUSED
        return EXIT_OK

    try:
        code = _started_root(args, db, unlock)
    except RootPairingRefused as exc:
        if exc.reason == "setup_pending":
            raise RootPairingRefused(
                "setup_pending", "Setup is not complete; there is no account to unlock."
            ) from None
        raise
    if code != EXIT_OK:
        return code
    if unlocked[0] is None:
        io.refuse("No account has that login.")
        return EXIT_REFUSED
    if unlocked[0]:
        io.say("Cleared the account-wide login limit.")
    else:
        io.say("The account-wide login limit was not set; nothing to clear.")
    return EXIT_OK


def _libraries(n: int) -> str:
    return "1 Library" if n == 1 else f"{n} Libraries"


def _rebuild_derived(args: argparse.Namespace, db: Database, io: _Io) -> int:
    """Queue one ``derived.rebuild`` per Library (or the one ``--library``); the workers run it (§8.6)."""
    from backend.domain.search import rebuild

    scopes = rebuild.scope_key([args.only] if args.only else rebuild.SCOPES)

    def enqueue() -> int:
        try:
            libraries = [library_id for library_id in rebuild.library_ids(db)
                         if args.library is None or library_id == args.library]
        except DatabaseError:
            io.refuse("The database could not be reached; nothing was queued.")
            return EXIT_REFUSED
        if not libraries:
            io.refuse("No Library has that id." if args.library else "There is no Library to rebuild.")
            return EXIT_REFUSED
        outcomes: dict[Optional[rebuild.Requested], int] = {}
        for library_id in libraries:  # one transaction each: a failure does not stop the others
            try:
                outcome: Optional[rebuild.Requested] = rebuild.request_rebuild(db, library_id, scopes)
            except DatabaseError:
                outcome = None
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        what = ", ".join(scopes)
        if queued := outcomes.get(rebuild.Requested.QUEUED, 0):
            io.say(f"Queued the {what} rebuild for {_libraries(queued)}; the worker runs it.")
        if waiting := outcomes.get(rebuild.Requested.ALREADY_QUEUED, 0):
            io.say(f"{_libraries(waiting)} already had it queued.")
        refused = False
        if busy := outcomes.get(rebuild.Requested.UNAVAILABLE, 0):
            io.refuse(f"{_libraries(busy)} in maintenance got nothing queued; run it again afterwards.")
            refused = True
        if failed := outcomes.get(None, 0):
            io.refuse(f"Nothing was queued for {_libraries(failed)}: the database could not be reached.")
            refused = True
        return EXIT_REFUSED if refused else EXIT_OK

    return _started_root(args, db, enqueue)


def _maintenance_storage(root: str) -> StorageBackend:
    from backend.domain.objects.jobs import root_storage

    storage = root_storage(root)
    if storage is None:
        raise RootPairingRefused("sqlite_catalogue", "This data root still holds a SQLite catalogue; "
                                                     "its Library is not in PostgreSQL.")
    return storage


def _backup(args: argparse.Namespace, db: Database, io: _Io) -> int:
    """Write a verified backup of ``--library`` into ``--out`` (§11.4)."""
    from backend.domain.maintenance import backup
    from backend.domain.maintenance.format import PRKS_VERSION

    out = os.path.realpath(args.out)
    if not os.path.isdir(out):
        io.refuse("The --out directory does not exist.")
        return EXIT_USAGE

    def run() -> int:
        storage = _maintenance_storage(_real_root(args.root))
        try:
            result = backup.write_backup(db.config.conninfo, storage, args.library, out, prks_version=PRKS_VERSION)
        except ValueError:
            io.refuse("That is not a Library id.")
            return EXIT_REFUSED
        except backup.BackupFailed as exc:
            io.refuse(exc.message)
            return EXIT_REFUSED
        io.say(f"Wrote {os.path.basename(result.path)} ({result.size} bytes, sha256 {result.sha256}).")
        return EXIT_OK

    return _started_root(args, db, run)


_OWNER_ID_SQL = "SELECT id FROM prks.users WHERE login_key = prks.identity_key(%s)"


def _restore(args: argparse.Namespace, db: Database, io: _Io) -> int:
    """Restore ``--archive`` over ``--library`` as ``--owner`` (§11.4), from a private copy."""
    from backend.domain.maintenance import jobs as backup_jobs
    from backend.domain.maintenance import restore

    if not os.path.isfile(args.archive):
        io.refuse("The --archive file does not exist.")
        return EXIT_USAGE

    def run() -> int:
        root = _real_root(args.root)
        storage = _maintenance_storage(root)
        try:
            identity_key(args.owner)
            rows = db.query(lambda tx: tx.execute(_OWNER_ID_SQL, (args.owner,)).fetchall())
        except UnsupportedTextError:
            rows = []
        except DatabaseError:
            io.refuse("The database could not be reached; nothing was changed.")
            return EXIT_REFUSED
        if not rows:
            io.refuse("No account has that login.")
            return EXIT_REFUSED
        try:
            with backup_jobs.private_copy(args.archive, root) as private:
                result = restore.restore_library(db.config.conninfo, storage, private, args.library,
                                                 owner_user_id=rows[0]["id"],
                                                 staging_dir=backup_jobs.restore_dir(root))
        except restore.RestoreFailed as exc:
            io.refuse(f"The restore failed ({exc.reason}): {exc.message}")
            return EXIT_REFUSED
        except backup_jobs.CopyFailed as exc:
            io.refuse(f"The archive could not be copied into the data root ({exc.reason}); nothing was changed.")
            return EXIT_REFUSED
        io.say(f"Restored the Library (epoch {result.epoch}); {result.objects_held} replaced file(s) are held. "
               "The worker rebuilds search.")
        return EXIT_OK

    return _started_root(args, db, run)


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
    prompt: Optional[Prompt] = None,
    open_database: DatabaseFactory = _open_database,
    faults: Faults = _no_faults,
) -> int:
    env = os.environ if environ is None else environ
    io = _Io(stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr, prompt)
    try:
        args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_OK
    io.command = args.command
    try:
        db = open_database(env)
    except DatabaseConfigError as exc:
        io.refuse(exc.message)
        return EXIT_USAGE
    if args.command in ("unlock-login", "rebuild-derived", "backup", "restore"):
        run = {"unlock-login": _unlock_login, "rebuild-derived": _rebuild_derived, "backup": _backup,
               "restore": _restore}[args.command]
        try:
            with db:
                return run(args, db, io)
        except RootPairingRefused as exc:
            io.refuse(exc.message)
            return EXIT_REFUSED
    try:
        with db:
            # Once ready, every bootstrap command refuses before it reads a
            # secret; the only file it touches is a dead token file to delete.
            try:
                bootstrap_tokens.refuse_when_ready(db)
            except RootPairingRefused as exc:
                if exc.reason == "already_ready":
                    _remove_dead_token_file(env, db, io)
                raise
            if args.step == "issue-token":
                return _issue(args, env, db, io, faults)
            return _complete(args, env, db, io)
    except RootPairingRefused as exc:
        io.refuse(exc.message)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
