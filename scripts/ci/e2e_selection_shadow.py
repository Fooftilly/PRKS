#!/usr/bin/env python3
"""Report what a coverage-map E2E selection would run for a pull request (shadow mode).

Advisory only: nothing here changes what the E2E gate runs. The script

1. finds the newest complete coverage map recorded on master by
   ``e2e-coverage-map.yml`` (one gzipped shard map per shard);
2. asks ``tests/e2e/coverage_map.select_for_diff`` what it would run for
   ``base...head``;
3. asks the real planner (``tests/e2e/run.py --ci-plan``) what the gate runs,
   in the gate's plan-time environment (``--plan-only``, before the E2E
   dependencies that test discovery needs are installed);
4. writes both to the job summary and to ``--output`` (JSON), so later runs
   can be compared with the gate's actual failures.

A missing or incomplete map is reported, not an error: the map is weekly
and its artifacts expire.

Usage::

    python scripts/ci/e2e_selection_shadow.py --base origin/master --plan-only --output plan.json
    python scripts/ci/e2e_selection_shadow.py --base origin/master --plan plan.json --output shadow.json
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ci.scheduled_report import API_VERSION, Client, GitHubClient  # noqa: E402

MAP_WORKFLOW = "e2e-coverage-map.yml"
SHARDS = 8
_MAP_NAME = re.compile(r"e2e-coverage-map-([1-9][0-9]*)\Z")
# A shard map is a few MiB gzipped; refuse anything far larger.
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _download(token: str, api_url: str, repo: str, artifact_id: int) -> bytes:
    """An artifact's zip. GitHub redirects to signed storage, which must not get the token."""
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/repos/{repo}/actions/artifacts/{artifact_id}/zip",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": API_VERSION, "User-Agent": "prks-e2e-selection-shadow"},
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        # The URL is https (api_url is checked by GitHubClient).
        with opener.open(request, timeout=30) as response:  # nosec B310
            return response.read(MAX_ARTIFACT_BYTES + 1)
    except urllib.error.HTTPError as error:
        location = error.headers.get("Location") if error.code in (301, 302, 303, 307, 308) else None
        if not location or not location.startswith("https://"):
            raise
    with urllib.request.urlopen(location, timeout=60) as response:  # nosec B310 - https checked above
        data = response.read(MAX_ARTIFACT_BYTES + 1)
    if len(data) > MAX_ARTIFACT_BYTES:
        raise RuntimeError(f"artifact {artifact_id} is larger than {MAX_ARTIFACT_BYTES} bytes")
    return data


def latest_map_artifacts(client: Client) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str]:
    """The newest successful master recording run and its shard map artifacts, or why there is none."""
    runs = (client.request(
        "GET", f"actions/workflows/{MAP_WORKFLOW}/runs?branch=master&status=success&per_page=10",
    ) or {}).get("workflow_runs", [])
    incomplete = ""
    for run in runs:
        if run.get("event") == "pull_request":
            continue
        artifacts = (client.request(
            "GET", f"actions/runs/{run['id']}/artifacts?per_page=100",
        ) or {}).get("artifacts", [])
        maps, shards = [], set()
        for artifact in artifacts:
            match = _MAP_NAME.match(str(artifact.get("name")))
            if match and not artifact.get("expired"):
                maps.append(artifact)
                shards.add(int(match.group(1)))
        if shards == set(range(1, SHARDS + 1)):
            return run, sorted(maps, key=lambda a: a["name"]), ""
        # An incomplete recording (a shard expired or deleted) falls back to an
        # older complete one; the newest one's gap is the reason if none is.
        incomplete = incomplete or (
            f"the newest master map ({run.get('html_url')}) has shards {sorted(shards)}, not 1-{SHARDS}")
    return None, [], incomplete or "no successful master recording run yet"


def read_map(zipped: bytes, name: str) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
        members = archive.namelist()
        expected = f"{name}.json.gz"
        if members != [expected]:
            raise RuntimeError(f"{name}: unexpected artifact contents {members!r}")
        info = archive.getinfo(expected)
        if info.file_size > MAX_ARTIFACT_BYTES:
            raise RuntimeError(f"{name}: map too large")
        raw = archive.read(expected)
    return json.loads(gzip.decompress(raw).decode("utf-8"))


def planner(base: str) -> dict[str, Any]:
    """What the E2E gate runs for this PR (the same call as e2e-gate.yml's plan step)."""
    env = {**os.environ, "PRKS_E2E": "1", "PRKS_E2E_CI_FRONTEND_FULL": "1"}
    env.pop("PRKS_E2E_CI_FORCE_FULL", None)
    out = subprocess.run(  # nosec B603 - fixed argv
        [sys.executable, "tests/e2e/run.py", "--ci-plan", "--base", base],
        cwd=ROOT, env=env, check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


def _rev(ref: str) -> str:
    return subprocess.run(  # nosec B603 B607 - fixed argv
        ["git", "rev-parse", ref], cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()


def summarize(shadow: dict[str, Any] | None, plan: dict[str, Any], why: str, map_url: str,
              base_sha: str = "") -> str:
    gate = plan.get("mode")
    gate_tests = plan.get("test_count") if gate == "affected" else (0 if gate == "skip" else "all")
    lines = ["## E2E selection (shadow)", "",
             "Advisory: the gate below is what ran; the coverage map changes nothing.",
             *([f"Both are against base `{base_sha[:12]}`; the gate fetched master separately, "
                "so compare its own plan if master moved in between."] if base_sha else []), "",
             f"- Gate plan: **{gate}**, {gate_tests} tests ({plan.get('reason', '')})"]
    if shadow is None:
        lines.append(f"- Coverage map: not available ({why})")
        return "\n".join(lines) + "\n"
    if shadow["mode"] == "full":
        detail = "full, because of " + ", ".join(f"`{p}`" for p in shadow.get("full_because", [])[:5])
    else:
        detail = f"{shadow['mode']}, {shadow['tests']} of {shadow['total']} tests"
    lines += [f"- Coverage map would run: **{detail}** (map: {map_url})", "",
              "| File | Rule | Tests |", "| --- | --- | --- |"]
    for rel, info in sorted(shadow.get("files", {}).items())[:50]:
        lines.append(f"| `{rel}` | {info['rule']}{' (' + info['why'] + ')' if 'why' in info else ''} "
                     f"| {info.get('tests', '')} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan-only", action="store_true",
                        help="write only the gate plan to --output (run in the gate's plan-time environment)")
    parser.add_argument("--plan", type=Path, help="a gate plan written earlier by --plan-only")
    args = parser.parse_args(argv)
    if args.plan_only:
        args.output.write_text(json.dumps(planner(args.base)), encoding="utf-8")
        return 0
    token, repo = os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"]
    api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    client = GitHubClient(token, repo, api_url)

    plan = json.loads(args.plan.read_text(encoding="utf-8")) if args.plan else planner(args.base)
    base_sha = _rev(args.base)
    run, artifacts, why = latest_map_artifacts(client)
    shadow = None
    map_url = ""
    if run is not None:
        from tests.e2e import coverage_map
        from tests.e2e.run import discover_test_ids, invalid_e2e_discovery_ids

        os.environ["PRKS_E2E"] = "1"
        all_ids = discover_test_ids()
        invalid = invalid_e2e_discovery_ids(all_ids)
        if invalid:
            # An import failure hides that module's tests: refuse a short inventory.
            why = "E2E discovery failed: " + ", ".join(invalid[:5])
        else:
            maps = [read_map(_download(token, api_url, repo, a["id"]), a["name"]) for a in artifacts]
            map_url = str(run.get("html_url"))
            shadow = coverage_map.select_for_diff(
                maps, repo=ROOT, base=args.base, head=args.head, all_ids=all_ids)
    report = {"plan": plan, "shadow": shadow, "map_run": map_url, "unavailable": why or None,
              "base_sha": base_sha}
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    text = summarize(shadow, plan, why, map_url, base_sha)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
