"""``prks admin bootstrap`` (``docs/postgresql-architecture.md`` §9.2, §12; L-ID-1 #626).

Run as ``python -m backend.domain.identity.admin bootstrap …`` until the
``prks`` entry point exists (L-D-1). Setup is two separate steps, so the
operator never holds a token that is already spent:

1. ``bootstrap issue-token --root ROOT`` stores a new one-time token (24 h by
   default; ``--ttl-hours``) and delivers it exactly once:

   - with ``PRKS_BOOTSTRAP_TOKEN_FILE`` set, the token is the one the
     operator already wrote to that file (``chmod 600``); it is registered,
     never printed, and issuing it again is a no-op;
   - otherwise, on a terminal, the new token is printed to that terminal;
   - otherwise (no terminal), it is written to ``bootstrap-token`` in the
     PRKS configuration directory, ``0600``, outside the data root, and only
     the path is printed. An existing file there is never overwritten.

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
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from typing import Optional, TextIO

from backend.db.config import load_database_config
from backend.db.database import Database
from backend.db.errors import DatabaseConfigError
from backend.domain.identity import bootstrap_tokens, passwords, root_pairing
from backend.domain.identity.bootstrap_tokens import BootstrapToken
from backend.domain.identity.errors import RootPairingRefused
from backend.storage.errors import StorageRootError
from backend.storage.root_binding import acquire_shared_root_lease

TOKEN_FILE_ENV = "PRKS_BOOTSTRAP_TOKEN_FILE"

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2

DatabaseFactory = Callable[[Mapping[str, str]], Database]
Prompt = Callable[[str], str]


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
        print(f"bootstrap: {text}", file=self.stderr)


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


def _issue(args: argparse.Namespace, environ: Mapping[str, str], db: Database, io: _Io) -> int:
    if not args.ttl_hours > 0:
        io.refuse("--ttl-hours must be positive.")
        return EXIT_USAGE
    ttl = timedelta(hours=args.ttl_hours)
    root = _real_root(args.root)
    supplied = _env_token(environ)
    if supplied is not None:
        issued = bootstrap_tokens.issue_token(db, ttl=ttl, token=supplied)
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
    path = bootstrap_tokens.default_token_file(environ)
    if os.path.lexists(path):
        raise RootPairingRefused(
            "token_file_exists",
            "A bootstrap token file already exists; use it, or remove it to issue a new one.",
        )
    issued = bootstrap_tokens.issue_token(db, ttl=ttl)
    bootstrap_tokens.write_token_file(issued.token, path, data_root=root)
    io.say(f"Wrote the one-time bootstrap token to {path} (mode 0600).")
    io.say(f"It expires {issued.expires_at.isoformat(timespec='seconds')}.")
    io.say(f"Next: {TOKEN_FILE_ENV}={path} prks admin bootstrap complete --root ROOT --login NAME")
    return EXIT_OK


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


def _complete(args: argparse.Namespace, environ: Mapping[str, str], db: Database, io: _Io) -> int:
    root = _real_root(args.root)
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
        raise RootPairingRefused(exc.reason, "The data root is in use by a maintenance tool.") from None
    try:
        result = root_pairing.bootstrap(
            db, owner, token=token, root=root, lease=lease, testing=bool(args.testing)
        )
    finally:
        lease.release()
    default_file = bootstrap_tokens.default_token_file(environ)
    removed = bootstrap_tokens.remove_token_file_if(default_file, token)
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


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
    prompt: Optional[Prompt] = None,
    open_database: DatabaseFactory = _open_database,
) -> int:
    env = os.environ if environ is None else environ
    io = _Io(stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr, prompt)
    try:
        args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_OK
    try:
        db = open_database(env)
    except DatabaseConfigError as exc:
        io.refuse(exc.message)
        return EXIT_USAGE
    try:
        with db:
            # Once ready, every bootstrap command refuses before it reads a
            # secret or touches a file.
            bootstrap_tokens.refuse_when_ready(db)
            if args.step == "issue-token":
                return _issue(args, env, db, io)
            return _complete(args, env, db, io)
    except RootPairingRefused as exc:
        io.refuse(exc.message)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
