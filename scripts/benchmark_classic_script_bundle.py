#!/usr/bin/env python3
"""Manual-only same-runner A/B for coalescing PRKS classic script requests.

This never changes the committed frontend. It creates two temporary classic
script bundles at the original parser-blocking head/body boundaries, preserving
script source order and deferring neither classics nor existing modules. It is
an experiment, not a production bundler: changes to document.currentScript,
per-file strict directives, debugging/source URLs, or module-relative paths
must be assessed separately before any production adoption.
"""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HEAD_URL = "/__e2e_classic_head_bundle.js"
BODY_URL = "/__e2e_classic_body_bundle.js"
CLASSIC = re.compile(r'<script src="(/[a-zA-Z0-9_./-]+)"></script>')
MODULE = re.compile(r'<script type="module" src="(/[a-zA-Z0-9_./-]+)"></script>')
SELECTION = (
    sys.executable, "tests/e2e/run.py", "--feature", "shell",
    "--feature", "work-create", "--jobs", "1",
    "--no-pointer-capture", "--profile",
)
PHASES = (
    ("Server startup", "server_start"),
    ("App readiness", "app_ready"),
    ("Page navigation", "app_navigation"),
    ("Response → DCL", "nav_dom_content_loaded"),
    ("Response → DOM interactive", "nav_until_interactive"),
    ("DOM interactive → DCL start", "nav_interactive_to_dcl"),
    ("DCL handlers", "nav_dcl_handlers"),
)


class _Scripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.location = "outside"
        self.scripts: list[tuple[str, list[tuple[str, str | None]]]] = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag in ("head", "body"):
            self.location = tag
        elif tag == "script":
            self.scripts.append((self.location, attrs))

    def handle_endtag(self, tag) -> None:
        if tag in ("head", "body"):
            self.location = "outside"


def _scripts(html: str) -> list[tuple[str, list[tuple[str, str | None]]]]:
    parser = _Scripts()
    parser.feed(html)
    parser.close()
    return parser.scripts


def _bundle_script(root: Path, paths: list[str]) -> str:
    chunks = []
    frontend = (root / "frontend").resolve()
    for src in paths:
        path = (frontend / src.lstrip("/")).resolve()
        if not path.is_relative_to(frontend) or not path.is_file():
            raise ValueError(f"script does not resolve under frontend: {src}")
        data = path.read_text(encoding="utf-8")
        # Separate trailing line comments and prevent expression adjacency.
        # The candidate deliberately keeps all top-level global bindings.
        chunks.append(f";\n/* benchmark source: {src} */\n{data}\n;\n")
    return "".join(chunks)


def build_candidate(root: Path) -> tuple[str, dict[str, str], dict[str, int]]:
    """Build the candidate in memory, rejecting unsupported HTML and assets."""
    original = (root / "frontend/index.html").read_text(encoding="utf-8")
    expected = []
    emitted = []
    outputs = []
    groups: dict[str, list[str]] = {"head": [], "body": []}
    head_end = original.find("</head>")
    if head_end < 0:
        raise ValueError("missing head/body script boundary")
    offset = 0
    for line in original.splitlines(keepends=True):
        script_offset = offset
        offset += len(line)
        stripped = line.strip()
        if not stripped.startswith("<script"):
            outputs.append(line)
            continue
        classic = CLASSIC.fullmatch(stripped)
        module = MODULE.fullmatch(stripped)
        if not classic and not module:
            raise ValueError("unsupported script markup in index.html")
        src = (classic or module).group(1)
        # A script's parser boundary must be independently known.
        location = "head" if script_offset < head_end else "body"
        if classic:
            expected.append((location, [("src", src)]))
            if location not in groups:
                raise ValueError("unexpected classic script placement")
            groups[location].append(src)
            if len(groups[location]) == 1:
                url = HEAD_URL if location == "head" else BODY_URL
                replacement = line[:len(line) - len(line.lstrip())] + f'<script src="{url}"></script>\n'
                outputs.append(replacement)
                emitted.append((location, [("src", url)]))
        else:
            expected.append((location, [("type", "module"), ("src", src)]))
            outputs.append(line)
            emitted.append((location, [("type", "module"), ("src", src)]))
    if not groups["head"] or not groups["body"]:
        raise ValueError("expected classic scripts at both parser boundaries")
    if _scripts(original) != expected:
        raise ValueError("original script tags differ from strict line inventory")
    candidate = "".join(outputs)
    if _scripts(candidate) != emitted:
        raise ValueError("candidate script ordering/attributes changed unexpectedly")
    bundles = {
        HEAD_URL: _bundle_script(root, groups["head"]),
        BODY_URL: _bundle_script(root, groups["body"]),
    }
    counts = {"head": len(groups["head"]), "body": len(groups["body"])}
    return candidate, bundles, counts


def _read_trial(path: Path, number: int) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Trial {number}: invalid or absent report: {exc}") from exc
    required = {
        "schema_version", "benchmark_modes", "workers", "tests",
        "failures", "errors", "skipped", "wall_seconds", "phase_seconds",
        "navigation_samples",
    }
    if not isinstance(data, dict) or set(data) != required:
        raise ValueError(f"Trial {number}: unexpected benchmark report schema")
    if data["schema_version"] != 2 or data["benchmark_modes"] != ["profile"]:
        raise ValueError(f"Trial {number}: benchmark flags/schema changed")
    if type(data["workers"]) is not int or data["workers"] != 1:
        raise ValueError(f"Trial {number}: worker count changed")
    if any(type(data[k]) is not int for k in ("tests", "failures", "errors", "skipped")):
        raise ValueError(f"Trial {number}: invalid test counters")
    if data["tests"] < 1 or any(data[k] for k in ("failures", "errors", "skipped")):
        raise ValueError(f"Trial {number}: tests were incomplete or failed")
    if type(data["wall_seconds"]) not in (int, float) or not math.isfinite(data["wall_seconds"]) or data["wall_seconds"] <= 0:
        raise ValueError(f"Trial {number}: invalid wall time")
    phases = data["phase_seconds"]
    if not isinstance(phases, dict):
        raise ValueError(f"Trial {number}: missing phase map")
    for _, key in PHASES:
        value = phases.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"Trial {number}: invalid {key}")
    for parent, children in (
        ("app_ready", ("app_navigation", "app_sidebar_wait", "app_folder_wait", "app_route_wait")),
        ("nav_dom_content_loaded", ("nav_until_interactive", "nav_interactive_to_dcl", "nav_dcl_handlers")),
    ):
        if not math.isclose(phases[parent], sum(phases.get(x, 0) for x in children), rel_tol=0, abs_tol=0.01):
            raise ValueError(f"Trial {number}: invalid {parent} partition")
    counts = data["navigation_samples"]
    if (
        not isinstance(counts, dict) or set(counts) != {"attempted", "captured"}
        or any(type(counts[k]) is not int or counts[k] < 0 for k in counts)
        or counts["captured"] > counts["attempted"]
    ):
        raise ValueError(f"Trial {number}: invalid navigation sample coverage")
    return data


def summarize(paths: list[Path], sha: str) -> str:
    rows = [_read_trial(p, i) for i, p in enumerate(paths, 1)]
    if len(rows) != 4 or len({r["tests"] for r in rows}) != 1:
        raise ValueError("four matching selections are required")
    modes = ("baseline", "bundled", "bundled", "baseline")
    base = statistics.median(r["wall_seconds"] for m, r in zip(modes, rows) if m == "baseline")
    bundle = statistics.median(r["wall_seconds"] for m, r in zip(modes, rows) if m == "bundled")
    pct = (bundle - base) / base * 100
    out = [
        "## E2E classic-script coalescing A/B (manual only)", "",
        f"Commit: {sha[:12]} · {rows[0]['tests']} selected tests per trial · same runner",
        "", "| Trial | Variant | E2E wall time |", "|---|---|---:|",
    ]
    out += [
        f"| {i} | {m} | {r['wall_seconds']:.2f}s |"
        for i, (m, r) in enumerate(zip(modes, rows), 1)
    ]
    out += [
        "", f"Baseline median **{base:.2f}s**; bundled median **{bundle:.2f}s**.",
        f"Bundle change **{pct:+.2f}%** (negative is faster).", "",
    ]
    complete = (
        all(r["navigation_samples"]["captured"] == r["navigation_samples"]["attempted"] > 0 for r in rows)
        and len({r["navigation_samples"]["attempted"] for r in rows}) == 1
    )
    out.append(
        "Navigation samples: " + ", ".join(
            f"{i}: {r['navigation_samples']['captured']}/{r['navigation_samples']['attempted']}"
            for i, r in enumerate(rows, 1)
        )
    )
    out += [
        "", "| Profile phase (sum across selected tests) | Baseline median | Bundled median |",
        "|---|---:|---:|",
    ]
    for label, key in PHASES:
        if key.startswith("nav_") and not complete:
            continue
        medians = [
            statistics.median(
                r["phase_seconds"][key] for m, r in zip(modes, rows) if m == variant
            )
            for variant in ("baseline", "bundled")
        ]
        out.append(f"| {label} | {medians[0]:.2f}s | {medians[1]:.2f}s |")
    out += [
        "", "Phase subcomponents are nested in their parents; do not add them twice.",
        "Browser navigation metrics are reported only with complete, matching samples.",
        "The bundled candidate changes resource URLs, document.currentScript and",
        "potential per-file script semantics. Passing this 40-test experiment does",
        "NOT establish production equivalence, offline/SW safety or full-suite speed.",
        "Do not deploy bundles without further semantic review and the full six-shard gate.",
    ]
    return "\n".join(out) + "\n"


def run_benchmark(root: Path) -> None:
    frontend = root / "frontend"
    index = frontend / "index.html"
    original = index.read_bytes()
    candidate, bundles, counts = build_candidate(root)
    bundle_paths = {url: frontend / url.lstrip("/") for url in bundles}
    if any(path.exists() for path in bundle_paths.values()):
        raise ValueError("temporary bundle file already exists; refusing overwrite")
    if os.getenv("PRKS_E2E_PROFILE") != "1":
        raise ValueError("benchmark requires opt-in profiling")
    print(f"Classic script counts: head={counts['head']}, body={counts['body']}", flush=True)
    env = os.environ.copy()
    env["PRKS_E2E_ROUTE_FASTPATH"] = "0"
    with tempfile.TemporaryDirectory(prefix="prks-e2e-bundle-ab-") as tmp:
        reports = []
        try:
            for url, text in bundles.items():
                bundle_paths[url].write_text(text, encoding="utf-8")
                subprocess.run(["node", "--check", str(bundle_paths[url])], cwd=root, check=True)
            for trial in range(5):
                # Warm-up is baseline, unmeasured. Then ABBA in a single runner.
                bundled = trial in (2, 3)
                index.write_text(candidate if bundled else original.decode("utf-8"), encoding="utf-8")
                label = "warm-up" if trial == 0 else ("bundled" if bundled else "baseline")
                result_path = Path(tmp) / f"trial-{trial}.json"
                env["PRKS_E2E_BENCHMARK_RESULT"] = "" if trial == 0 else str(result_path)
                print(f"::group::Trial {trial} ({label})", flush=True)
                try:
                    subprocess.run(SELECTION, cwd=root, env=env, check=True)
                finally:
                    print("::endgroup::", flush=True)
                if trial:
                    reports.append(result_path)
            report = summarize(reports, os.environ.get("GITHUB_SHA", "unknown"))
            print(report, flush=True)
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                with open(summary, "a", encoding="utf-8") as handle:
                    handle.write(report)
        finally:
            index.write_bytes(original)
            for path in bundle_paths.values():
                path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="execute the manual 5-pass A/B")
    args = parser.parse_args()
    if not args.run:
        candidate, bundles, counts = build_candidate(ROOT)
        print(f"Prepared candidate in memory: {counts}, {len(candidate)} HTML bytes, "
              f"{sum(len(s) for s in bundles.values())} JS characters")
        return
    run_benchmark(ROOT)


if __name__ == "__main__":
    main()
