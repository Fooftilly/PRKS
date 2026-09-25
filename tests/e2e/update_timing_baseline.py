#!/usr/bin/env python3
"""Regenerate ``tests/e2e/timing-baseline.json`` from representative measurements.

The committed baseline is coarse ``prefix.*`` scheduling metadata for fresh
checkouts (especially cloud agents). Machine-local exact timings in
``.tests/e2e-timings.json`` remain the authority on a given machine and always
override the baseline at schedule time.

This tool never reads ``.tests/e2e-timings.json`` by default. Pass one or more
``--from`` measurement files exported from representative CI / full-gate runs
(same JSON shape as the runner's exact timing history: unittest id → seconds).

Examples::

  # Dry-run: print the generated baseline to stdout
  python tests/e2e/update_timing_baseline.py \\
      --from /tmp/ci-e2e-timings.json

  # Write the committed file (review as an ordinary JSON diff)
  python tests/e2e/update_timing_baseline.py \\
      --from /tmp/ci-e2e-timings.json --write

  # Median across multiple representative runs
  python tests/e2e/update_timing_baseline.py \\
      --from run-a.json --from run-b.json --write

Wrapper::

  scripts/e2e update-timing-baseline --from PATH [--write]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from tests.e2e.sharding import (  # noqa: E402
    BASELINE_TIMINGS_PATH,
    TIMINGS_PATH,
    aggregate_timing_baseline,
    combine_measurement_timings,
    load_timings,
    save_timings,
)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tests/e2e/update_timing_baseline.py",
        description=(
            "Build committed coarse E2E timing-baseline.json from representative "
            "exact per-test measurements. Does not use machine-local "
            ".tests/e2e-timings.json unless you pass it explicitly via --from."
        ),
    )
    parser.add_argument(
        "--from",
        dest="sources",
        action="append",
        required=True,
        metavar="PATH",
        help=(
            "Exact timing JSON from a representative CI/full-gate run "
            "(repeatable; values are median-merged per test id). "
            "Refuse to treat the gitignored local timings path as the sole "
            "implicit source — pass it only when you mean to."
        ),
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write tests/e2e/timing-baseline.json (default: print JSON to stdout).",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        default=None,
        help="Alternate output path (implies writing a file; default with --write is the committed baseline).",
    )
    parser.add_argument(
        "--class-outlier-ratio",
        type=float,
        default=1.5,
        metavar="R",
        help="Emit module.Class.* when class median >= R * module median (default: 1.5).",
    )
    parser.add_argument(
        "--min-class-samples",
        type=int,
        default=3,
        metavar="N",
        help="Minimum tests in a class before a class-level prefix is considered (default: 3).",
    )
    return parser


def _repo_root() -> Path:
    return Path(_PROJECT_DIR)


def _resolve_source(raw: str, repo: Path) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    else:
        path = path.resolve()
    local_default = (repo / TIMINGS_PATH).resolve()
    if path == local_default:
        print(
            "note: %s is machine-local runner history, not a CI measurement export; "
            "prefer representative full-gate artifacts when refreshing the committed baseline"
            % TIMINGS_PATH,
            file=sys.stderr,
        )
    return path


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    repo = _repo_root()
    sources = []
    for raw in args.sources:
        path = _resolve_source(raw, repo)
        loaded = load_timings(path)
        if not loaded:
            print(
                "error: no usable exact timings in %s (missing, corrupt, or only prefix keys)"
                % path,
                file=sys.stderr,
            )
            return 2
        sources.append(loaded)

    combined = combine_measurement_timings(*sources)
    if not combined:
        print("error: no exact unittest timings remained after filtering", file=sys.stderr)
        return 2

    try:
        baseline = aggregate_timing_baseline(
            combined,
            class_outlier_ratio=args.class_outlier_ratio,
            min_class_samples=args.min_class_samples,
        )
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if not baseline:
        print("error: aggregation produced an empty baseline", file=sys.stderr)
        return 2

    write_path = None
    if args.output is not None:
        write_path = Path(args.output)
        if not write_path.is_absolute():
            write_path = (Path.cwd() / write_path).resolve()
    elif args.write:
        write_path = (repo / BASELINE_TIMINGS_PATH).resolve()

    if write_path is None:
        json.dump(baseline, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    if not save_timings(write_path, baseline):
        print("error: failed to write %s" % write_path, file=sys.stderr)
        return 1
    print(
        "wrote %d baseline prefixes to %s (from %d exact measurements)"
        % (len(baseline), write_path, len(combined)),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
