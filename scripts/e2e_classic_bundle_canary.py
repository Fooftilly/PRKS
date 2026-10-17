#!/usr/bin/env python3
"""Manually exercise all six PRKS E2E shards with temporary classic bundles.

The candidate and assets exist only in the checkout of the selected Actions
job. This is *not* a production packaging or service-worker cache change.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .benchmark_classic_script_bundle import build_candidate, BODY_URL, HEAD_URL

SHARD_COUNT = 6
BUNDLE_URLS = (HEAD_URL, BODY_URL)
ROOT = Path(__file__).resolve().parents[1]


def run_canary(root: Path, shard_index: int) -> None:
    """Run one *complete-suite* external shard, restoring all files on exit."""
    if type(shard_index) is not int or not 1 <= shard_index <= SHARD_COUNT:
        raise ValueError("expected one shard index from 1 through 6")

    index = root / "frontend/index.html"
    original = index.read_bytes()
    candidate, bundles, counts = build_candidate(root)
    if set(bundles) != set(BUNDLE_URLS):
        raise ValueError("unexpected classic bundle asset set")
    if counts["head"] + counts["body"] < 2:
        raise ValueError("unexpectedly small classic script inventory")
    bundle_paths = {url: root / "frontend" / url.lstrip("/") for url in BUNDLE_URLS}
    if any(path.exists() for path in bundle_paths.values()):
        raise ValueError("temporary bundle already exists; refusing overwrite")
    node = shutil.which("node")
    if not node:
        raise ValueError("Node.js is required to validate the candidate")

    env = os.environ.copy()
    env["PRKS_E2E_ROUTE_FASTPATH"] = "0"
    env["PRKS_E2E_PROFILE"] = "0"
    env["PRKS_E2E_BENCHMARK_RESULT"] = ""
    env["PRKS_E2E_MODE"] = "full"
    env["PRKS_E2E"] = "1"
    runner = [
        sys.executable,
        "tests/e2e/run.py",
        "--jobs", "1",
        "--shard", f"{shard_index}/{SHARD_COUNT}",
        "--no-pointer-capture",
    ]
    try:
        for url, text in bundles.items():
            path = bundle_paths[url]
            path.write_text(text, encoding="utf-8")
            subprocess.run([node, "--check", str(path)], cwd=root, check=True)
        index.write_text(candidate, encoding="utf-8")
        print(
            f"Temporary classic bundles staged: head={counts['head']}, "
            f"body={counts['body']}. Running complete-suite shard "
            f"{shard_index}/{SHARD_COUNT}.",
            flush=True,
        )
        subprocess.run(runner, cwd=root, env=env, check=True)
    finally:
        index.write_bytes(original)
        for path in bundle_paths.values():
            path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=int, required=True, help="1-based shard index (1 through 6)")
    args = parser.parse_args()
    run_canary(ROOT, args.shard)


if __name__ == "__main__":
    main()
