#!/usr/bin/env python3
"""Regenerate ``tests/e2e/timing-baseline.json`` from representative measurements.

The committed baseline is coarse ``prefix.*`` scheduling metadata for fresh
checkouts (especially cloud agents). Machine-local exact timings in
``.tests/e2e-timings.json`` remain the authority on a given machine and always
override the baseline at schedule time.

This tool never reads ``.tests/e2e-timings.json`` by default. Pass one or more
``--from`` measurement files exported from representative CI / full-gate runs
(same JSON shape as the runner's exact timing history: unittest id → seconds).

Writing the committed baseline fails closed unless the export covers every
current E2E module and every discovered exact test ID. Partial / experimental
generation requires ``--allow-partial`` or an alternate ``--output`` path.

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

  # Explicit partial experiment (never the default committed path)
  python tests/e2e/update_timing_baseline.py \\
      --from shard.json --output /tmp/partial-baseline.json

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
    assess_measurement_coverage,
    combine_measurement_timings,
    load_timings,
    save_timings,
)

_MISSING_LIST_LIMIT = 20


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tests/e2e/update_timing_baseline.py",
        description=(
            "Build committed coarse E2E timing-baseline.json from representative "
            "exact per-test measurements. Does not use machine-local "
            ".tests/e2e-timings.json unless you pass it explicitly via --from. "
            "Overwriting the committed baseline requires full-suite coverage "
            "unless --allow-partial is set."
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
        help=(
            "Alternate output path (implies writing a file; default with --write "
            "is the committed baseline). Alternate paths may be partial; the "
            "committed path still requires full coverage unless --allow-partial."
        ),
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            "Permit writing even when measurements miss discovered modules or "
            "exact test IDs. Required to overwrite the committed baseline from a "
            "partial export; alternate --output paths allow partial by default."
        ),
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


def _committed_baseline_path(repo: Path) -> Path:
    return (repo / BASELINE_TIMINGS_PATH).resolve()


def _discover_e2e_ids():
    """Load current suite IDs via the E2E runner discovery (sets PRKS_E2E)."""
    previous = os.environ.get("PRKS_E2E")
    os.environ["PRKS_E2E"] = "1"
    try:
        from tests.e2e.run import discover_test_ids

        return discover_test_ids()
    finally:
        if previous is None:
            os.environ.pop("PRKS_E2E", None)
        else:
            os.environ["PRKS_E2E"] = previous


def _format_missing(label, items):
    if not items:
        return ""
    shown = items[:_MISSING_LIST_LIMIT]
    more = len(items) - len(shown)
    body = ", ".join(shown)
    if more > 0:
        body += ", … (%d more)" % more
    return "missing %s (%d): %s" % (label, len(items), body)


def _report_coverage_gaps(missing_modules, missing_ids, *, fatal: bool) -> None:
    prefix = "error" if fatal else "warning"
    print(
        "%s: measurement export is incomplete relative to current E2E discovery"
        % prefix,
        file=sys.stderr,
    )
    modules_line = _format_missing("modules", missing_modules)
    if modules_line:
        print("  %s" % modules_line, file=sys.stderr)
    ids_line = _format_missing("exact test ids", missing_ids)
    if ids_line:
        print("  %s" % ids_line, file=sys.stderr)
    if fatal:
        print(
            "  refuse to overwrite the committed baseline from a partial export; "
            "use a full-gate timing artifact, or pass --allow-partial / "
            "--output <alternate> for experiments",
            file=sys.stderr,
        )


def main(argv=None, *, discover_ids=None) -> int:
    args = build_parser().parse_args(argv)
    repo = _repo_root()
    committed_path = _committed_baseline_path(repo)
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
        write_path = committed_path

    writing_committed = write_path is not None and write_path == committed_path
    # Committed overwrite fails closed on partial coverage unless --allow-partial.
    # Alternate --output and stdout dry-run may be partial (experiments).
    if writing_committed:
        try:
            discovered = list(
                discover_ids() if discover_ids is not None else _discover_e2e_ids()
            )
        except Exception as exc:  # noqa: BLE001 - fail closed for committed writes
            print(
                "error: could not discover current E2E tests for coverage check: %s"
                % exc,
                file=sys.stderr,
            )
            return 2
        if not discovered:
            print(
                "error: E2E discovery returned no test IDs; refusing committed baseline write",
                file=sys.stderr,
            )
            return 2
        missing_modules, missing_ids = assess_measurement_coverage(combined, discovered)
        if missing_modules or missing_ids:
            if not args.allow_partial:
                _report_coverage_gaps(missing_modules, missing_ids, fatal=True)
                return 2
            _report_coverage_gaps(missing_modules, missing_ids, fatal=False)

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
