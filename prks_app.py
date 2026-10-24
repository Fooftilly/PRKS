#!/usr/bin/env python3
import argparse
import os
import sys

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


def build_parser():
    parser = argparse.ArgumentParser(description="PRKS — Personal Research Knowledge System")
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
            "(psycopg, psycopg-pool and argon2-cffi in requirements-dev.txt).",
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
    try:
        return start_web_identity(db, settings, root=os.path.realpath(config.root), testing=testing)
    except RootPairingRefused as exc:
        db.close()
        print(f"PRKS refuses to start: {exc.message}", file=sys.stderr)
        raise SystemExit(2) from None


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


if __name__ == "__main__":
    args = build_parser().parse_args()
    if args.testing:
        os.environ["PRKS_TESTING"] = "1"

    # Fail before storage/DB/migrations/server when the Python env is wrong.
    from backend.dependency_gate import ensure_runtime_or_exit

    ensure_runtime_or_exit()

    try:
        config = StorageConfig.from_env(cli_root=args.storage_root)
    except StorageRootError as exc:
        print(f"PRKS cannot select its storage root: {exc.message}", file=sys.stderr)
        raise SystemExit(2) from None
    # Before the root is opened: the PostgreSQL start check takes the root's
    # shared lease, which the single-process lease below excludes.
    auth = start_identity(args, config)
    jobs = start_jobs(auth)
    # Held until this process exits: the single-process lease (root.lock).
    bound_root = open_storage(config)
    # Every later open stays beneath the root that was validated and leased,
    # even if a root symlink is retargeted while the process runs.
    config = bound_root.anchor(config)
    recover_incomplete_restore(config)
    config = bind_storage(config)
    setup_logging(config)
    bound_root.log_binding()

    port = args.port if args.port is not None else (8070 if args.testing else PORT)
    run_server(port=port, host=args.host, auth=auth, jobs=jobs)
