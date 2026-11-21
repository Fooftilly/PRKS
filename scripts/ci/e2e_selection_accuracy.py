#!/usr/bin/env python3
"""Check the shadow E2E selection against what the gate actually caught (advisory).

``e2e-selection-shadow.yml`` records, for every pull request head, what a
coverage-map selection would have run. This script joins those records with
the **PRKS Full E2E Gate** runs on the same heads:

- a gate failure whose failed tests the map would **not** have run is a
  *miss*: the map would have hidden it (or it is a flake the map did not
  select; the miss comment asks to check which);
- every compared run also counts how many tests the gate ran against how many
  the map would have run.

Results go to the standing issue "E2E selection accuracy" (label
``e2e-selection-report``): one summary comment edited in place each run (no
notification) and one new comment per miss (which notifies), posted once.
Gate failure logs keep 7 days, so the window is the last 7 days.

Usage::

    python scripts/ci/e2e_selection_accuracy.py [--days 7] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ci import scheduled_report as sr  # noqa: E402
from scripts.ci.e2e_selection_shadow import MAX_ARTIFACT_BYTES, _download  # noqa: E402

GATE_WORKFLOW = "e2e-gate.yml"
SHADOW_WORKFLOW = "e2e-selection-shadow.yml"
SHADOW_ARTIFACT = "e2e-selection-shadow"
LABEL = sr.ACCURACY_LABEL
TITLE = "E2E selection accuracy"
_SHARD_LOG = re.compile(r"e2e-shard-([1-9][0-9]*)-of-([1-9][0-9]*)-log\Z")
_SUMMARY_MARKER = "<!-- e2e-selection-accuracy:summary -->"
_MISS_MARKER = "<!-- e2e-selection-miss:{run_id} -->"
_FOOTER = "\n\n---\n_Posted by `scripts/ci/e2e_selection_accuracy.py`._"
_TEST_ID = re.compile(r"tests\.e2e\.[\w.]+\Z")
_SHARD_JOB = re.compile(r"E2E \(([1-9][0-9]*)/([1-9][0-9]*)\)\Z")
# e2e-gate.yml's last job, which fails whenever a shard or pointer capture did.
_GATE_SUMMARY_JOB = "Full E2E gate"
_UPLOAD_SLACK = dt.timedelta(minutes=2)
_ROWS_SHOWN = 60

Download = Callable[[int], bytes]


def _member(zipped: bytes, name: str) -> dict[str, Any] | None:
    """One JSON file of an artifact zip, or None when the artifact does not hold it."""
    with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
        if name not in archive.namelist():
            return None
        if archive.getinfo(name).file_size > MAX_ARTIFACT_BYTES:
            raise RuntimeError(f"{name}: too large")
        data = json.loads(archive.read(name).decode("utf-8"))
    return data if isinstance(data, dict) else None


def _runs(client: sr.Client, workflow: str, query: str) -> list[dict[str, Any]]:
    """A workflow's runs for ``query``, newest first (at most 1000, GitHub's cap)."""
    runs: list[dict[str, Any]] = []
    for page in range(1, 11):
        batch = (client.request(
            "GET", f"actions/workflows/{workflow}/runs?{query}&per_page=100&page={page}",
        ) or {}).get("workflow_runs", [])
        runs += batch
        if len(batch) < 100:
            break
    return runs


def _since(when: dt.datetime) -> str:
    return "%3E%3D" + when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gate_runs(client: sr.Client, since: dt.datetime) -> list[dict[str, Any]]:
    """Completed pull request runs of the gate since ``since``, newest first."""
    runs = _runs(client, GATE_WORKFLOW, f"event=pull_request&status=completed&created={_since(since)}")
    return [r for r in runs if r.get("conclusion") in ("success", "failure")]


def shadow_runs(client: sr.Client, since: dt.datetime) -> dict[str, int]:
    """Head SHA -> the newest successful shadow run on it (one listing for the whole window)."""
    found: dict[str, int] = {}
    for run in _runs(client, SHADOW_WORKFLOW, f"event=pull_request&status=success&created={_since(since)}"):
        found.setdefault(str(run.get("head_sha")), int(run["id"]))
    return found


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def clean_report(data: Any) -> dict[str, Any]:
    """Only the fields the report uses, each checked.

    A pull request's own code writes its shadow report, so nothing from it
    reaches an issue comment unchecked: modes come from fixed sets, counts are
    integers and test IDs match the E2E ID pattern. Raises ValueError otherwise.
    """
    if not isinstance(data, dict):
        raise ValueError("not an object")
    plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}
    gate_mode = plan.get("mode")
    if gate_mode not in ("full", "affected", "skip"):
        raise ValueError("unknown gate mode")
    selection = data.get("shadow")
    if selection is None:
        return {"gate_mode": gate_mode, "selection": None}
    if not isinstance(selection, dict) or selection.get("mode") not in ("full", "skip", "subset"):
        raise ValueError("unknown map mode")
    tests, total = _count(selection.get("tests")), _count(selection.get("total"))
    if tests is None or total is None:
        raise ValueError("bad test counts")
    selected = selection.get("selected") or []
    if not isinstance(selected, list) or not all(isinstance(t, str) and _TEST_ID.match(t) for t in selected):
        raise ValueError("bad selected test IDs")
    return {"gate_mode": gate_mode, "gate_test_count": _count(plan.get("test_count")),
            "selection": {"mode": selection["mode"], "tests": tests, "total": total, "selected": selected}}


def shadow_report(client: sr.Client, download: Download, run_id: int) -> dict[str, Any] | None:
    """The checked report a shadow run uploaded, or None when it has expired."""
    artifacts = (client.request("GET", f"actions/runs/{run_id}/artifacts?per_page=100") or {}).get("artifacts", [])
    for artifact in artifacts:
        if artifact.get("name") == SHADOW_ARTIFACT and not artifact.get("expired"):
            return clean_report(_member(download(int(artifact["id"])), f"{SHADOW_ARTIFACT}.json"))
    return None


def _time(value: Any) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _uploaded_by(artifact: dict[str, Any], job: dict[str, Any]) -> bool:
    """Whether ``artifact`` was uploaded while ``job`` ran (a rerun keeps the run's artifacts, so
    an earlier attempt's shard log must not stand in for this attempt's)."""
    created, started, ended = _time(artifact.get("created_at")), _time(job.get("started_at")), _time(
        job.get("completed_at"))
    return None not in (created, started, ended) and started <= created <= ended + _UPLOAD_SLACK  # type: ignore[operator]


def gate_failures(client: sr.Client, download: Download, run_id: int) -> tuple[set[str], bool]:
    """The test IDs a failed gate run's latest attempt reported, and whether that is every failure.

    Complete means each failed shard job of the latest attempt uploaded a log
    with test IDs during that job, and no other job failed (pointer capture, the
    plan) except the gate's own summary job, which fails because they did.
    """
    artifacts = [a for a in (client.request("GET", f"actions/runs/{run_id}/artifacts?per_page=100") or {}).get(
        "artifacts", []) if _SHARD_LOG.match(str(a.get("name"))) and not a.get("expired")]
    jobs = (client.request("GET", f"actions/runs/{run_id}/jobs?filter=latest&per_page=100") or {}).get("jobs", [])
    failed = [job for job in jobs if job.get("conclusion") in ("failure", "timed_out")]
    ids: set[str] = set()
    complete = bool(failed)
    for job in failed:
        name = str(job.get("name", ""))
        shard = _SHARD_JOB.match(name)
        if shard is None:
            if name != _GATE_SUMMARY_JOB:
                complete = False
            continue
        log_name = f"e2e-shard-{shard.group(1)}-of-{shard.group(2)}-log"
        found: list[str] = []
        for artifact in artifacts:
            if artifact.get("name") == log_name and _uploaded_by(artifact, job):
                data = _member(download(int(artifact["id"])), "e2e-last-failed.json")
                found = [t for t in (data or {}).get("test_ids", []) if isinstance(t, str) and _TEST_ID.match(t)]
                break
        if not found:
            complete = False
        ids.update(found)
    return ids, complete


def _title(run: dict[str, Any]) -> str:
    """The pull request a run belongs to, for people: its number when GitHub still links it, else its title."""
    pulls = run.get("pull_requests") or []
    if pulls and pulls[0].get("number"):
        return f"#{pulls[0]['number']}"
    # Pull request titles are anyone's text: no table breaks, markup, links or @mentions.
    title = re.sub(r"[|`\[\]<>()@#*_~!\\\r\n]", " ", str(run.get("display_title") or "?"))
    title = re.sub(r"\s+", " ", title).strip() or "?"
    return title if len(title) <= 50 else title[:49] + "…"


def compare(run: dict[str, Any], shadow: dict[str, Any] | None, failed: set[str], complete: bool) -> dict[str, Any]:
    row: dict[str, Any] = {
        "run_id": run["id"], "run_url": run.get("html_url", ""), "head_sha": run.get("head_sha", ""),
        "title": _title(run), "conclusion": run["conclusion"],
        "failed": sorted(failed), "missed": [], "complete": complete,
    }
    if shadow is None or shadow.get("selection") is None:
        row["status"] = shadow.get("status", "no map") if shadow else "no shadow report"
        return row
    selection = shadow["selection"]
    row["gate_mode"] = shadow["gate_mode"]
    row["gate_tests"] = {"skip": 0, "affected": shadow.get("gate_test_count")}.get(shadow["gate_mode"],
                                                                                 selection["total"])
    row["map_mode"] = selection["mode"]
    row["map_tests"] = selection["tests"]
    if selection["mode"] == "full":
        chosen = None
    elif selection["mode"] == "skip":
        chosen = set()
    else:
        chosen = set(selection["selected"])
    row["missed"] = sorted(failed - chosen) if chosen is not None else []
    if run["conclusion"] == "failure" and not complete:
        row["status"] = "failed, test IDs incomplete"
    else:
        row["status"] = "miss" if row["missed"] else "ok"
    return row


def summary_comment(rows: list[dict[str, Any]], days: int, run_url: str) -> str:
    compared = [r for r in rows if "map_mode" in r]
    ran = sum(int(r["gate_tests"] or 0) for r in compared)
    would = sum(int(r["map_tests"] or 0) for r in compared)
    misses = [r for r in compared if r["missed"]]
    incomplete = [r for r in compared if r["status"] == "failed, test IDs incomplete"]
    lines = [
        _SUMMARY_MARKER,
        f"### Last {days} days (updated by {run_url})",
        "",
        f"- Gate runs on pull requests: {len(rows)}; with a coverage-map selection: {len(compared)} "
        "(the rest have no shadow report, or none with a complete map)",
        f"- Tests selected (planned, not timed): the gate {ran}; the map {would}",
        f"- Gate runs with a failure the map would not have run: **{len(misses)}**",
        f"- Failed gate runs whose failed tests are not all known: {len(incomplete)}",
    ]
    if compared:
        shown = "" if len(compared) <= _ROWS_SHOWN else f" (newest {_ROWS_SHOWN} of {len(compared)})"
        lines += ["", f"Compared runs{shown}:", "",
                  "| Pull request | Gate | Gate tests | Map | Map tests | Result |",
                  "| --- | --- | ---: | --- | ---: | --- |"]
        for r in compared[:_ROWS_SHOWN]:
            lines.append(
                f"| {r['title']} | [{r['conclusion']}]({r['run_url']}) | {r['gate_tests']} "
                f"| {r['map_mode']} | {r['map_tests']} | {r['status']} |")
    return "\n".join(lines) + _FOOTER


def miss_comment(row: dict[str, Any]) -> str:
    lines = [
        _MISS_MARKER.format(run_id=row["run_id"]),
        f"**The coverage map would not have run tests that failed in the gate on {row['title']}** "
        f"([run]({row['run_url']}), head `{str(row['head_sha'])[:12]}`, map mode {row.get('map_mode')}).",
        "",
        "If the failure was real, the map missed it: note why (an unmeasured file, a test that "
        "reaches code without running it, or a stale map). If it was a known flake, nothing was lost.",
        "",
        *(f"- `{test_id}`" for test_id in row["missed"][:30]),
    ]
    return "\n".join(lines) + _FOOTER


BODY = (
    "The daily **E2E selection accuracy (advisory)** workflow compares what the coverage map "
    "would have run (`e2e-selection-shadow.yml`) with what the Full E2E gate caught on the same "
    "pull request heads. The summary comment below is edited in place; a new comment, which "
    "notifies, appears once for each gate failure the map would not have run.\n\n"
    "The gate does not read the map. This is the evidence for deciding whether it should." + _FOOTER
)


def report(client: sr.Client, rows: list[dict[str, Any]], days: int, run_url: str) -> str:
    issue = sr._open_issue(client, LABEL, TITLE)
    if issue is None:
        # A closed report is reopened, not replaced: its comments are the record of misses already posted.
        issue = sr._find_issue(client, LABEL, TITLE, state="closed")
        if issue is not None:
            client.request("PATCH", f"issues/{issue['number']}", {"state": "open"})
        else:
            issue = sr._create_issue(client, LABEL, TITLE, BODY)
    number = int(issue["number"])
    ours = [c for c in sr._comments(client, number) if (c.get("user") or {}).get("login") == sr.REPORTER_LOGIN]
    posted = "\n".join(str(c.get("body", "")) for c in ours)
    new = 0
    for row in rows:
        if row["missed"] and _MISS_MARKER.format(run_id=row["run_id"]) not in posted:
            client.request("POST", f"issues/{number}/comments", {"body": miss_comment(row)})
            new += 1
    body = summary_comment(rows, days, run_url)
    sticky = next((c for c in ours if str(c.get("body", "")).startswith(_SUMMARY_MARKER)), None)
    if sticky is None:
        client.request("POST", f"issues/{number}/comments", {"body": body})
    else:
        client.request("PATCH", f"issues/comments/{sticky['id']}", {"body": body})
    return f"#{number}: {len(rows)} gate runs, {new} new misses"


def collect(client: sr.Client, download: Download, days: int, now: dt.datetime) -> list[dict[str, Any]]:
    since = now - dt.timedelta(days=days)
    shadows = shadow_runs(client, since)
    rows = []
    for run in gate_runs(client, since):
        shadow_run = shadows.get(str(run.get("head_sha")))
        shadow: dict[str, Any] | None = None
        if shadow_run:
            try:
                shadow = shadow_report(client, download, shadow_run)
            except (ValueError, RuntimeError, zipfile.BadZipFile, UnicodeDecodeError) as error:
                # One bad report (a pull request's code wrote it) never stops the others.
                shadow = {"selection": None, "status": f"unreadable shadow report ({type(error).__name__})"}
        failed, complete = set(), True
        # Failure logs matter only when there is a selection to check them against.
        if run["conclusion"] == "failure" and shadow and shadow.get("selection") is not None:
            try:
                failed, complete = gate_failures(client, download, int(run["id"]))
            except (ValueError, RuntimeError, zipfile.BadZipFile, UnicodeDecodeError):
                failed, complete = set(), False
        rows.append(compare(run, shadow, failed, complete))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    parser.add_argument("--dry-run", action="store_true", help="print the summary; write no issue")
    args = parser.parse_args(argv)
    token, repo = os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"]
    api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    client = sr.GitHubClient(token, repo, api_url)
    run_url = f"{server}/{repo}/actions/runs/{args.run_id}" if args.run_id else "a local run"
    rows = collect(client, lambda artifact_id: _download(token, api_url, repo, artifact_id), args.days,
                   dt.datetime.now(dt.timezone.utc))
    text = summary_comment(rows, args.days, run_url)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    if not args.dry_run:
        print(report(client, rows, args.days, run_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
