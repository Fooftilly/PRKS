#!/usr/bin/env python3
import argparse
import os
import sys

from backend.log_config import setup_logging
from backend.backup_restore import recover_incomplete_restore
from backend.server import DEFAULT_HOST, PORT, bind_storage, normalize_listen_host, run_server
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
    run_server(port=port, host=args.host)
