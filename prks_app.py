#!/usr/bin/env python3
import argparse
import os

from backend.log_config import setup_logging
from backend.backup_restore import recover_incomplete_restore
from backend.server import DEFAULT_HOST, PORT, bind_storage, normalize_listen_host, run_server
from backend.storage.config import StorageConfig


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
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    if args.testing:
        os.environ["PRKS_TESTING"] = "1"

    # Fail before storage/DB/migrations/server when the Python env is wrong.
    from backend.dependency_gate import ensure_runtime_or_exit

    ensure_runtime_or_exit()

    config = StorageConfig.from_env()
    recover_incomplete_restore(config)
    config = bind_storage(config)
    setup_logging(config)

    port = args.port if args.port is not None else (8070 if args.testing else PORT)
    run_server(port=port, host=args.host)
