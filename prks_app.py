#!/usr/bin/env python3
import argparse
import os
import sys

# Must run before importing backend.server (module-level DB and paths).
if "--testing" in sys.argv:
    os.environ["PRKS_TESTING"] = "1"

from backend.server import run_server, PORT


if __name__ == "__main__":
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
        help=f"Port to bind the server to (default: 8070 with --testing, else {PORT}).",
    )
    args = parser.parse_args()
    if args.testing:
        os.environ["PRKS_TESTING"] = "1"

    port = args.port if args.port is not None else (8070 if args.testing else PORT)
    run_server(port=port)
