#!/usr/bin/env python3
import argparse
import os
import sys
import time

_IMPORT_STARTED = time.perf_counter()

from backend.log_config import setup_logging
from backend.backup_restore import recover_incomplete_restore
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


ROLES = ("serve", "migrate", "worker", "admin")


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
        help="Use data_testing/prks_data_testing.db and data_testing/pdfs (separate from data/).",
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


def open_storage(config):
    """Validate, lease and mark the selected root; exit with a message on refusal."""
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
        return open_storage_root(config, config_file_path=config_file, defer_logs=True)
    except StorageRootError as exc:
        print(f"PRKS cannot open its storage root: {exc.message}", file=sys.stderr)
        raise SystemExit(2) from None


def start_identity(args, config, environ=None):
    """Authentication for this process (``PRKS_AUTH``), or ``None`` for the SQLite runtime.

    With ``PRKS_AUTH`` set, the PostgreSQL start check and bump run here,
    before the storage root is opened; any refusal exits with status 2.
    """
    env = os.environ if environ is None else environ
    if not (env.get("PRKS_AUTH") or "").strip():
        return None
    # Imported only when used, so the SQLite runtime never loads psycopg.
    try:
        import argon2  # noqa: F401 - checked here for a clear refusal
        import psycopg  # noqa: F401
    except ImportError:
        print(
            "PRKS cannot start authentication: PRKS_AUTH needs the PostgreSQL packages "
            "(psycopg, psycopg-pool and argon2-cffi in requirements.txt).",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    from backend.db.config import load_database_config
    from backend.db.database import Database
    from backend.db.errors import DatabaseConfigError
    from backend.domain.identity.errors import RootPairingRefused
    from backend.domain.identity.login_limiter import LimiterConfigError
    from backend.domain.identity.sessions import SessionConfigError
    from backend.domain.identity.web_start import (
        WebAuthConfigError,
        settings_from_env,
        start_web_identity,
    )

    testing = config.mode == "testing"
    try:
        settings = settings_from_env(
            env, testing=testing and bool(args.testing), host=args.host,
            parse_origin=parse_request_origin,
        )
    except (WebAuthConfigError, LimiterConfigError, SessionConfigError) as exc:
        print(f"PRKS cannot start authentication: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    if settings is None:
        return None
    try:
        db = Database(load_database_config(env, role="web"))
    except DatabaseConfigError as exc:
        print(f"PRKS cannot start authentication: {exc.message}", file=sys.stderr)
        raise SystemExit(2) from None
    migrate_on_start(db, env)
    try:
        return start_web_identity(db, settings, root=os.path.realpath(config.root), testing=testing)
    except RootPairingRefused as exc:
        db.close()
        print(f"PRKS refuses to start: {exc.message}", file=sys.stderr)
        raise SystemExit(2) from None


def migrate_on_start(db, environ):
    """``PRKS_MIGRATE_ON_START`` (§4.4): unset or ``1`` applies the schema here, ``0`` leaves it to ``migrate``.

    Compose sets ``0``: its one-shot ``migrate`` service is the only runner.
    The schema work never touches the root; the start check runs after it.
    Any refusal exits with status 2.
    """
    from backend.db.migrate import MigrationError, migrate

    value = (environ.get("PRKS_MIGRATE_ON_START") or "1").strip()
    if value not in ("0", "1"):
        db.close()
        print("PRKS cannot start: PRKS_MIGRATE_ON_START must be 0 or 1.", file=sys.stderr)
        raise SystemExit(2)
    if value == "0":
        return
    try:
        migrate(db.config.conninfo)
    except MigrationError as exc:
        db.close()
        print(f"PRKS cannot start: {exc.message}", file=sys.stderr)
        raise SystemExit(2) from None


def readiness_for(auth, root):
    """``GET /readyz`` of a ``PRKS_AUTH`` web process (``backend.readiness``); None for the SQLite runtime."""
    if auth is None:
        return None
    from backend.readiness import PostgresReadiness

    return PostgresReadiness(auth.db, instance_id=auth.instance_id, root=os.path.realpath(root))


def start_jobs(auth, environ=None):
    """The web process's jobs runtime (``backend.jobs.embedded``), or ``None`` for the SQLite runtime.

    Only with ``PRKS_AUTH`` (``auth`` from :func:`start_identity`, whose start
    check already ran): unless ``PRKS_EMBEDDED_WORKER`` is ``0`` it prepares
    one embedded worker, which ``run_server`` starts with the server and
    stops on shutdown. Any refusal exits with status 2.
    """
    if auth is None:
        return None
    env = os.environ if environ is None else environ
    # Imported only when used, so the SQLite runtime never loads psycopg.
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


def select_config(args):
    try:
        return StorageConfig.from_env(cli_root=args.storage_root)
    except StorageRootError as exc:
        print(f"PRKS cannot select its storage root: {exc.message}", file=sys.stderr)
        raise SystemExit(2) from None


def run_role(role, argv):
    """``migrate``, ``worker`` or ``admin``: the PostgreSQL roles beside ``serve`` (§12.2)."""
    if role == "admin":
        # The admin commands take their own --root (docs/admin-bootstrap.md).
        from backend.domain.identity import admin

        return admin.main(argv)
    args = build_role_parser(role).parse_args(argv)
    if args.testing:
        os.environ["PRKS_TESTING"] = "1"
    from backend.dependency_gate import ensure_runtime_or_exit

    ensure_runtime_or_exit()
    config = select_config(args)
    root = os.path.realpath(config.root)
    testing = config.mode == "testing"
    from backend import process_roles

    if role == "migrate":
        return process_roles.run_migrate(root=root, testing=testing)
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return process_roles.run_worker(root=root, testing=testing)


if __name__ == "__main__":
    from backend.startup_profile import phase as startup_phase, record_imports

    record_imports(_IMPORT_STARTED)
    role, role_argv = split_role(sys.argv[1:])
    if role != "serve":
        raise SystemExit(run_role(role, role_argv))
    args = build_parser().parse_args(role_argv)
    if args.testing:
        os.environ["PRKS_TESTING"] = "1"

    # Fail before storage/DB/migrations/server when the Python env is wrong.
    with startup_phase("startup_dependency_gate"):
        from backend.dependency_gate import ensure_runtime_or_exit

        ensure_runtime_or_exit()

    with startup_phase("startup_config"):
        config = select_config(args)
    # Before the root is opened: the PostgreSQL start check takes the root's
    # shared lease, which the single-process lease below excludes.
    with startup_phase("startup_identity"):
        auth = start_identity(args, config)
    with startup_phase("startup_jobs"):
        jobs = start_jobs(auth)
    # Held until this process exits: the single-process lease (root.lock).
    with startup_phase("startup_storage_open"):
        bound_root = open_storage(config)
    # Every later open stays beneath the root that was validated and leased,
    # even if a root symlink is retargeted while the process runs.
    config = bound_root.anchor(config)
    with startup_phase("startup_restore"):
        recover_incomplete_restore(config)
    with startup_phase("startup_bind"):
        config = bind_storage(config)
    with startup_phase("startup_logging"):
        setup_logging(config)
        bound_root.log_binding()
    if jobs is not None:
        # The embedded worker's object maintenance acts on the leased root only.
        with startup_phase("startup_worker_bind"):
            jobs.bind_root(config.root)

    port = args.port if args.port is not None else (8070 if args.testing else PORT)
    run_server(port=port, host=args.host, auth=auth, jobs=jobs, readiness=readiness_for(auth, config.root))
