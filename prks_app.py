#!/usr/bin/env python3
import argparse
import contextlib
import os
import re
import sys
import time

_IMPORT_STARTED = time.perf_counter()

from backend.log_config import setup_logging
from backend.server import (
    DEFAULT_HOST,
    PORT,
    bind_storage,
    normalize_listen_host,
    parse_request_origin,
    run_server,
)
from backend.storage.config import StorageConfig
from backend.storage.errors import StorageRootError
from backend.storage.resolver import bootstrap_config_path
from backend.storage.root_binding import open_storage_root


def parse_host(value):
    try:
        return normalize_listen_host(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


ROLES = ("serve", "migrate", "worker", "admin", "config")


def split_role(argv):
    """``(role, rest)``: the first argument names a role (§12.2); none means ``serve``."""
    if argv and argv[0] in ROLES:
        return argv[0], list(argv[1:])
    return "serve", list(argv)


def _add_root_arguments(parser):
    parser.add_argument(
        "--testing",
        action="store_true",
        help="A disposable testing instance on the testing storage root.",
    )
    parser.add_argument(
        "--storage-root",
        metavar="PATH",
        default=None,
        help="Storage root for this run (as for serve).",
    )


def build_role_parser(role):
    """The arguments of ``migrate`` and ``worker``: they act on the selected storage root."""
    descriptions = {
        "migrate": "Apply the PostgreSQL schema, then check and bump this start against the storage root.",
        "worker": "Run the PostgreSQL job worker on the storage root.",
    }
    parser = argparse.ArgumentParser(prog=f"prks_app.py {role}", description=descriptions[role])
    _add_root_arguments(parser)
    return parser


def build_parser():
    parser = argparse.ArgumentParser(
        description="PRKS — Personal Research Knowledge System",
        epilog=(
            "Roles: serve (the default), migrate, worker, admin. "
            "Run 'prks_app.py ROLE --help' for each."
        ),
    )
    parser.add_argument(
        "--testing",
        action="store_true",
        help=(
            "A disposable testing instance: the data_testing/ root and a testing PostgreSQL "
            "database (PRKS_DATABASE_URL, default: the scripts/dev-postgres server), "
            "signed in as its owner on loopback."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Port to bind the server to (default: {PORT}, or 8070 for --testing).",
    )
    parser.add_argument(
        "--host",
        type=parse_host,
        default=DEFAULT_HOST,
        help=f"Address to bind the server to (default: {DEFAULT_HOST}).",
    )
    parser.add_argument(
        "--storage-root",
        metavar="PATH",
        default=None,
        help=(
            "Storage root for this run. Takes precedence over PRKS_STORAGE and the "
            "bootstrap config file; nothing is persisted."
        ),
    )
    return parser


def open_storage(config, *, paired=False):
    """Validate, lease and mark the selected root; exit with a message on refusal.

    ``paired`` (a web process with ``PRKS_AUTH``, paired with PostgreSQL)
    holds the root's lease shared, beside ``worker`` and ``migrate``, and
    leaves the marker's pairing to the start check (L-D-1 #630).
    """
    config_file = None
    if config.mode != "testing":
        # Only used to check the root does not contain it (V11); testing mode
        # never consults the bootstrap file (storage-architecture §5.3).
        try:
            config_file = bootstrap_config_path(os.environ)
        except StorageRootError:
            # A higher-precedence source won, so this path was never used; an
            # unparseable value names no file that could sit inside the root.
            # (A config_file selection already validated it in from_env.)
            config_file = None
    try:
        # Logging is configured only after the root is open (its log file
        # lives there), so the open's warnings wait for log_binding().
        return open_storage_root(config, config_file_path=config_file, defer_logs=True, paired=paired)
    except StorageRootError as exc:
        print(f"PRKS cannot open its storage root: {exc.message}", file=sys.stderr)
        raise SystemExit(2) from None


def prepare_identity(args, config, environ=None):
    """``(database, settings)`` of this web process.

    Checks the authentication settings, opens the pool and applies or
    verifies the schema (``PRKS_MIGRATE_ON_START``), all before the storage
    root is touched; :func:`start_identity` then runs the start check once
    the root is open. A ``--testing`` process defaults ``PRKS_AUTH`` to
    ``loopback-owner`` and the database to the ``scripts/dev-postgres``
    server (:data:`backend.db.config.TESTING_DEFAULT_DATABASE_URL`), and any
    process on a testing root first proves its database is a testing one
    (:func:`refuse_untested_database`). Any refusal exits with status 2.
    """
    env = os.environ if environ is None else environ
    from backend.db.config import load_database_config, with_testing_default
    from backend.db.database import Database
    from backend.db.errors import DatabaseConfigError
    from backend.domain.identity.login_limiter import LimiterConfigError
    from backend.domain.identity.sessions import SessionConfigError
    from backend.domain.identity.web_start import WebAuthConfigError, settings_from_env

    testing = config.mode == "testing" and bool(args.testing)
    if testing:
        env = with_testing_default(env)
    try:
        settings = settings_from_env(
            env, testing=testing, host=args.host, parse_origin=parse_request_origin,
        )
    except (WebAuthConfigError, LimiterConfigError, SessionConfigError) as exc:
        print(f"PRKS cannot start authentication: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    try:
        db_config = load_database_config(env, role="web")
    except DatabaseConfigError as exc:
        print(f"PRKS cannot start: {exc.message}", file=sys.stderr)
        raise SystemExit(2) from None
    if config.mode == "testing":
        # A testing root never meets a real Library: prove the database is a
        # testing one before the schema step or a bootstrap writes to it.
        refuse_untested_database(db_config.conninfo)
    db = Database(db_config)
    migrate_on_start(db, env, testing=testing)
    return db, settings


def refuse_untested_database(conninfo):
    """Exit with status 2 unless ``conninfo`` is a testing database (``backend.db.testing_guard``).

    Every process on a testing root runs it before it writes to its database.
    """
    from backend.db.testing_guard import TestingDatabaseRefused, prove_testing_database

    try:
        prove_testing_database(conninfo)
    except TestingDatabaseRefused as exc:
        print(f"PRKS refuses to start: {exc.message}", file=sys.stderr)
        raise SystemExit(2) from None


class SetupWait:
    """Lets a first-run web process wait for setup instead of exiting (§11.1).

    Each call announces the wait once, then sleeps up to ``poll`` seconds and
    answers whether to check again: false once SIGTERM or SIGINT arrived
    while :meth:`watching`. The handlers only record the signal (see
    ``backend.jobs.worker.SignalStop``).
    """

    def __init__(self, poll=2.0, *, stderr=None, step=0.1):
        from backend.jobs.worker import SignalStop

        self.poll = poll
        self._step = step
        self._stderr = stderr
        self._signals = SignalStop()
        self._announced = False

    @property
    def stopped(self):
        return bool(self._signals.requested)

    @contextlib.contextmanager
    def watching(self):
        self._signals.install()
        try:
            yield self
        finally:
            self._signals.restore()

    def __call__(self):
        if not self._announced:
            self._announced = True
            print(
                "PRKS is waiting for setup to complete (python prks_app.py admin bootstrap; "
                "docs/admin-bootstrap.md).",
                file=self._stderr or sys.stderr,
            )
        deadline = time.monotonic() + self.poll
        while not self.stopped and time.monotonic() < deadline:
            time.sleep(self._step)
        return not self.stopped


def start_identity(prepared, bound_root, *, testing, wait=None):
    """The start check and bump of this web process, under ``bound_root``'s shared lease.

    ``prepared`` comes from :func:`prepare_identity`. While setup is not
    ``ready`` the process waits (``wait``, a :class:`SetupWait`); a stop
    requested meanwhile exits with status 0, and every refusal with 2.
    """
    from backend.domain.identity.errors import RootPairingRefused
    from backend.domain.identity.web_start import start_web_identity

    db, settings = prepared
    try:
        auth = start_web_identity(
            db, settings, root=bound_root.root_real, lease=bound_root.lease,
            testing=testing, wait_for_setup=wait,
        )
    except RootPairingRefused as exc:
        db.close()
        bound_root.release()
        if exc.reason == "setup_pending" and wait is not None and wait.stopped:
            print("PRKS stopped while waiting for setup.", file=sys.stderr)
            raise SystemExit(0) from None
        print(f"PRKS refuses to start: {exc.message}", file=sys.stderr)
        raise SystemExit(2) from None
    if wait is not None and wait.stopped:
        # A stop that arrived during the last check is not lost: the caller
        # still holds the handlers, so it was recorded, and nothing serves.
        db.close()
        bound_root.release()
        print("PRKS stopped while waiting for setup.", file=sys.stderr)
        raise SystemExit(0)
    return auth


def refuse_sqlite_catalogue(library_database, bound_root):
    """Refuse a data root that still holds a pre-0.8 SQLite catalogue (exit 2).

    Its PDFs and portraits are files the PostgreSQL catalogue does not name,
    so object maintenance would treat them as orphans
    (``backend.domain.objects.jobs``). Pre-1.0 PRKS does not import such a
    Library: use a new data root.
    """
    from backend.domain.objects.jobs import root_refusal

    if root_refusal(bound_root.root_real) is not None:
        library_database.close()
        bound_root.release()
        print(
            "PRKS refuses to start: this data root holds a pre-0.8 SQLite Library, which "
            "this version does not read. Move it aside and start on a new data root.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def migrate_on_start(db, environ, *, testing=False):
    """``PRKS_MIGRATE_ON_START`` (§4.4): unset or ``1`` applies the schema here, ``0`` leaves it to ``migrate``.

    With ``0`` the schema is still verified: a web process never starts on a
    schema other than its code's (§12.3).

    Compose sets ``0``: its one-shot ``migrate`` service is the only runner.
    The schema work never touches the root; the start check runs after it.
    Any refusal exits with status 2.
    """
    from backend.db.migrate import MigrationError, migrate, verify_schema

    value = (environ.get("PRKS_MIGRATE_ON_START") or "").strip() or "1"
    if value not in ("0", "1"):
        db.close()
        print("PRKS cannot start: PRKS_MIGRATE_ON_START must be 0 or 1.", file=sys.stderr)
        raise SystemExit(2)
    try:
        if value == "0":
            # The migrate role ran; this process still refuses a schema that
            # is not exactly its code's (pending, newer or changed files).
            verify_schema(db.config.conninfo)
        else:
            migrate(db.config.conninfo)
    except MigrationError as exc:
        db.close()
        message = exc.message
        if testing and exc.reason == "database_unavailable":
            message += " For --testing, start the local database with scripts/dev-postgres."
        print(f"PRKS cannot start: {message}", file=sys.stderr)
        raise SystemExit(2) from None


def readiness_for(auth, root):
    """``GET /readyz`` of the web process (``backend.readiness``)."""
    from backend.readiness import PostgresReadiness

    return PostgresReadiness(auth.db, instance_id=auth.instance_id, root=os.path.realpath(root))


def start_jobs(auth, environ=None):
    """The web process's jobs runtime (``backend.jobs.embedded``).

    ``auth`` comes from :func:`start_identity`, whose start check already
    ran: unless ``PRKS_EMBEDDED_WORKER`` is ``0`` it prepares one embedded
    worker, which ``run_server`` starts with the server and stops on
    shutdown (``None`` then). Any refusal exits with status 2.
    """
    env = os.environ if environ is None else environ
    from backend.db.errors import DatabaseConfigError, DatabaseError
    from backend.jobs.embedded import EmbeddedWorkerConfigError, web_jobs

    try:
        return web_jobs(auth.db, env)
    except EmbeddedWorkerConfigError as exc:
        print(f"PRKS cannot start its job worker: {exc}", file=sys.stderr)
    except DatabaseConfigError as exc:
        print(f"PRKS cannot start its job worker: {exc.message}", file=sys.stderr)
    except DatabaseError:
        print("PRKS cannot start its job worker: the database cannot be reached.", file=sys.stderr)
    auth.db.close()
    raise SystemExit(2)


def run_role(role, argv):
    """``migrate``, ``worker`` or ``admin``: the PostgreSQL roles beside ``serve`` (§12.2)."""
    # Every role fails before any import of the PostgreSQL packages when the
    # Python environment is wrong, as serve does.
    from backend.dependency_gate import ensure_runtime_or_exit

    ensure_runtime_or_exit()
    if role == "admin":
        # The admin commands take their own --root (docs/admin-bootstrap.md).
        from backend.domain.identity import admin

        return admin.main(argv)
    args = build_role_parser(role).parse_args(argv)
    if args.testing:
        enter_testing_mode()
    config = select_config(args)
    root = os.path.realpath(config.root)
    testing = config.mode == "testing"
    if testing:
        from backend.db.config import load_database_config
        from backend.db.errors import DatabaseConfigError

        try:
            conninfo = load_database_config(os.environ, role="web").conninfo
        except DatabaseConfigError:
            conninfo = None  # the role reports the configuration error itself
        if conninfo is not None:
            refuse_untested_database(conninfo)
    from backend import process_roles

    if role == "migrate":
        return process_roles.run_migrate(root=root, testing=testing)
    import logging

    from backend.log_config import stderr_log_level

    logging.basicConfig(level=stderr_log_level(), format="%(asctime)s %(levelname)s %(message)s")
    return process_roles.run_worker(root=root, testing=testing)


def enter_testing_mode(environ=None):
    """``--testing``: ``PRKS_TESTING=1`` and, unless one is named, the ``scripts/dev-postgres`` database.

    Every role and the embedded worker's jobs read this one environment, so
    the database is defaulted once, here, before anything opens it.
    """
    from backend.db.config import with_testing_default

    env = os.environ if environ is None else environ
    env["PRKS_TESTING"] = "1"
    env.update(with_testing_default(env))


def refuse_invalid_environment(environ=None):
    """Exit with status 2 while any setting is invalid or retired (``backend.config_reference``, #612).

    Every role runs it first, before the database, the storage root or the
    network: a deployment never runs on a value PRKS would otherwise replace
    with a default, nor with configuration written for an earlier release.
    """
    from backend.config_reference import problems

    found = problems(os.environ if environ is None else environ)
    if found:
        for problem in found:
            print(f"PRKS cannot start: {problem}", file=sys.stderr)
        print("Check the whole configuration with: python prks_app.py config check", file=sys.stderr)
        raise SystemExit(2)


def run_config(argv, environ=None, *, out=None):
    """``config check``: validate the configuration the way a start does, and show it (#612).

    Runs every parser a ``serve`` start runs before it touches anything
    (settings, storage selection, HTTP, authentication, database), without
    connecting, binding or opening the root. Prints each problem, then the
    effective deployment values and their source, secrets hidden. Exit 0
    when a start would accept the configuration, 2 otherwise.
    """
    from backend.config_reference import Effective, effective, problems

    parser = argparse.ArgumentParser(
        prog="prks_app.py config", description="Validate and show this deployment's configuration."
    )
    steps = parser.add_subparsers(dest="command", required=True)
    check = steps.add_parser("check", help="validate every setting and print the effective values")
    check.add_argument("--testing", action="store_true", help="check as a --testing run would")
    check.add_argument("--host", type=parse_host, default=DEFAULT_HOST, help="the address serve would bind")
    check.add_argument("--storage-root", metavar="PATH", help="the storage root serve would use")
    args = parser.parse_args(argv)
    env = dict(os.environ if environ is None else environ)
    if args.testing:
        env["PRKS_TESTING"] = "1"
    stdout = out or sys.stdout
    storage, delegated, invalid, unchecked = _delegated_checks(env, args)
    found = list(problems(env))
    found += [problem for problem in delegated if problem not in found]
    for problem in found:
        print(f"problem: {problem}", file=stdout)
    rows = effective(env, invalid, unchecked)
    if storage is not None:
        # The root a start would select: the CLI flag, PRKS_STORAGE, the
        # bootstrap file or a default, whichever wins (§5.2).
        rows = [Effective(row.key, storage.root, f"selected by {storage.root_source}")
                if row.key == "PRKS_STORAGE" else row for row in rows]
    width = max(len(row.key) for row in rows)
    for row in rows:
        print(f"{row.key.ljust(width)}  {row.shown}  ({row.source})", file=stdout)
    print("configuration: OK" if not found else f"configuration: {len(found)} problem(s)", file=stdout)
    return 0 if not found else 2


# The parser a start runs for each owner of a delegated setting (the admin
# token file is read only when a setup needs it, so it is shown as set).
_PARSER_OF_OWNER = {
    "backend.storage.config": "storage",
    "backend.storage.resolver": "storage",
    "backend.web.settings": "http",
    "backend.domain.identity.web_start": "auth",
    "backend.domain.identity.login_limiter": "auth",
    "backend.domain.identity.sessions": "auth",
    "backend.jobs.embedded": "worker",
    "backend.db.config": "database",
}
# Settings that are valid or invalid only together: each group is probed as one
# (a certificate needs its key; PRKS_STORAGE decides whether the bootstrap file
# is read at all).
_PROBE_GROUPS = (
    ("PRKS_TLS_CERT_FILE", "PRKS_TLS_KEY_FILE"),
    ("PRKS_STORAGE", "PRKS_CONFIG_FILE"),
)
# What a probe stands on, so a parser reaches the probed value instead of
# stopping at a problem of its own: a loopback address, and a database URL
# unless the probe is of a database source.
_PROBE_HOST = "127.0.0.1"
_PROBE_DATABASE = {"PRKS_DATABASE_URL": "postgresql://probe@127.0.0.1/probe"}
_DATABASE_SOURCES = frozenset({"PRKS_DATABASE_URL", "PRKS_DATABASE_URL_FILE"})


def _delegated_checks(environ, args):
    """``(storage, problems, invalid keys, unchecked keys)`` from the parsers a start runs.

    A parser stops at its first problem, so each set value that only its
    owner's parser checks is probed on the environment without the other
    such values (dependent values, such as a certificate and its key, as one
    group), on a loopback address and with a stand-in database URL so that
    the parser reaches it. A probe its parser accepts is valid; one that
    changes the parser's problem is invalid, and that problem is named; one
    that leaves the problem as it was could not be reached and is
    ``unchecked``. Refused
    and unchecked values are never echoed. A last pass on the whole
    environment adds the problems of combinations.
    """
    from backend.config_reference import BY_KEY, Scope, in_scope

    delegated = [
        setting.key for setting in in_scope(Scope.DEPLOYMENT)
        if setting.check is None and (environ.get(setting.key) or "").strip()
    ]
    groups, seen = [], set()
    for key in delegated:
        if key in seen:
            continue
        group = next((g for g in _PROBE_GROUPS if key in g), (key,))
        members = tuple(member for member in group if member in delegated)
        seen.update(members)
        groups.append(members)
    bare = {key: value for key, value in environ.items() if key not in delegated}
    probe_args = argparse.Namespace(**{**vars(args), "host": _PROBE_HOST})
    found, invalid, unchecked = [], set(), set()
    for group in groups:
        parser = _PARSER_OF_OWNER.get(BY_KEY[group[0]].owner)
        if parser is None:
            continue
        base = bare if _DATABASE_SOURCES.intersection(group) else {**_PROBE_DATABASE, **bare}
        baseline, _ = _parser_problems(base, probe_args)
        probe, _ = _parser_problems({**base, **{key: environ[key] for key in group}}, probe_args)
        if probe[parser] is None:
            continue
        if probe[parser] != baseline[parser]:
            invalid.update(group)
            if probe[parser] not in found:
                found.append(probe[parser])
        else:
            unchecked.update(group)
    combined, storage = _parser_problems(environ, args)
    found.extend(problem for problem in combined.values() if problem is not None and problem not in found)
    for group in groups:
        # A value the whole environment's pass names is refused there (a
        # conflict, or a value the probe could not reach), whatever its probe said.
        problem = combined.get(_PARSER_OF_OWNER.get(BY_KEY[group[0]].owner) or "")
        named = [key for key in group if problem is not None and re.search(rf"\b{key}\b", problem)]
        unchecked.difference_update(named)
        invalid.update(named)
    return storage, found, invalid, unchecked


def _parser_problems(environ, args):
    """``({parser: problem or None}, storage)``: each parser a start runs, in its own words (no value echoed)."""
    from backend.db.config import load_database_config, with_testing_default
    from backend.db.errors import DatabaseConfigError
    from backend.domain.identity.login_limiter import LimiterConfigError
    from backend.domain.identity.sessions import SessionConfigError
    from backend.domain.identity.web_start import WebAuthConfigError, settings_from_env
    from backend.jobs.embedded import EmbeddedWorkerConfigError, embedded_worker_enabled
    from backend.web.settings import HttpServerSettings, HttpSettingsError

    testing = bool(args.testing)
    found = dict.fromkeys(("storage", "http", "auth", "worker", "database"))
    storage = None
    try:
        # A set PRKS_FOR_PROCESSING_DIR is reported once, as a retired key.
        storage_env = {key: value for key, value in environ.items() if key != "PRKS_FOR_PROCESSING_DIR"}
        storage = StorageConfig.from_env(cli_root=args.storage_root, environ=storage_env)
    except StorageRootError as exc:
        found["storage"] = f"storage root: {exc.message}"
    try:
        HttpServerSettings.from_env(environ)
    except HttpSettingsError as exc:
        found["http"] = exc.message
    try:
        settings_from_env(environ, testing=testing, host=args.host, parse_origin=parse_request_origin)
    except (WebAuthConfigError, LimiterConfigError, SessionConfigError) as exc:
        found["auth"] = str(exc)
    try:
        embedded_worker_enabled(environ)
    except EmbeddedWorkerConfigError as exc:
        found["worker"] = str(exc)
    try:
        load_database_config(with_testing_default(environ) if testing else environ, role="web")
    except DatabaseConfigError as exc:
        found["database"] = exc.message
    return found, storage


def select_config(args):
    """The storage root of a ``migrate`` or ``worker`` role; serve selects its own inline below."""
    try:
        return StorageConfig.from_env(cli_root=args.storage_root)
    except StorageRootError as exc:
        print(f"PRKS cannot select its storage root: {exc.message}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    from backend.startup_profile import phase as startup_phase, record_imports

    record_imports(_IMPORT_STARTED)
    role, role_argv = split_role(sys.argv[1:])
    if role == "config":
        raise SystemExit(run_config(role_argv))
    refuse_invalid_environment()
    if role != "serve":
        raise SystemExit(run_role(role, role_argv))
    args = build_parser().parse_args(role_argv)
    if args.testing:
        enter_testing_mode()

    # Fail before storage/DB/migrations/server when the Python env is wrong.
    with startup_phase("startup_dependency_gate"):
        from backend.dependency_gate import ensure_runtime_or_exit

        ensure_runtime_or_exit()

    with startup_phase("startup_config"):
        try:
            config = StorageConfig.from_env(cli_root=args.storage_root)
        except StorageRootError as exc:
            print(f"PRKS cannot select its storage root: {exc.message}", file=sys.stderr)
            raise SystemExit(2) from None
        from backend.web.settings import HttpServerSettings, HttpSettingsError

        try:
            http_settings = HttpServerSettings.from_env(os.environ)
        except HttpSettingsError as exc:
            print(f"PRKS cannot start its HTTP server: {exc.message}", file=sys.stderr)
            raise SystemExit(2) from None
    # Authentication settings, the pool and the schema, before the root is
    # touched.
    with startup_phase("startup_identity_config"):
        identity = prepare_identity(args, config)
        library_database = identity[0]
    # Held until this process exits: root.lock shared, beside worker and
    # migrate (L-D-1 #630).
    with startup_phase("startup_storage_open"):
        bound_root = open_storage(config, paired=True)
        refuse_sqlite_catalogue(library_database, bound_root)
    # The start check and bump under that shared lease; a first run waits
    # here, serving nothing, until setup is ready (a --testing run on a fresh
    # database bootstraps its owner instead).
    with startup_phase("startup_identity"), SetupWait().watching() as wait:
        auth = start_identity(identity, bound_root, testing=config.mode == "testing", wait=wait)
    with startup_phase("startup_jobs"):
        jobs = start_jobs(auth)
    # Every later open stays beneath the root that was validated and leased,
    # even if a root symlink is retargeted while the process runs.
    config = bound_root.anchor(config)
    with startup_phase("startup_bind"):
        config = bind_storage(config, library_database=library_database)
    with startup_phase("startup_logging"):
        setup_logging(config)
        bound_root.log_binding()
    if jobs is not None:
        # The embedded worker's object maintenance acts on the leased root only.
        with startup_phase("startup_worker_bind"):
            jobs.bind_root(config.root)

    port = args.port if args.port is not None else (8070 if args.testing else PORT)
    try:
        run_server(port=port, host=args.host, auth=auth, jobs=jobs,
                   readiness=readiness_for(auth, config.root), http=http_settings)
    finally:
        # Close the web pool here rather than leave it to its finaliser at
        # interpreter exit, which joins the pool's threads late (on Python
        # 3.13 it held every stop for seconds).
        auth.db.close()
