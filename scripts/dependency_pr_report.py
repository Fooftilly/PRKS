#!/usr/bin/env python3
"""Read-only dependency impact report; never waives CI checks or approves a PR."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Each entry: area, source changes, validation, maintainer repair command.
RULES = (
    ("Python runtime", ("requirements.txt",),
     "Unit/API, dependency gate, backend parity and affected E2E",
     "python -m unittest tests.test_dependency_gate"),
    ("Python test/browser tools", ("requirements-dev.txt",),
     "Pinned dependency install and browser E2E where relevant",
     "python -m unittest tests.test_dependency_gate"),
    ("Vue app", ("frontend-app/package.json", "frontend-app/package-lock.json"),
     "Typecheck, Vitest, reproducible production assets and affected E2E",
     "bash scripts/refresh_dependency_artifacts.sh frontend-app"),
    ("Frontend vendor", ("tools/frontend-vendor/package.json", "tools/frontend-vendor/package-lock.json"),
     "Vendored asset reproducibility, dependency gate and browser E2E",
     "bash scripts/refresh_dependency_artifacts.sh frontend-vendor"),
    ("PDF viewer", ("tools/pdf-viewer/package.json", "tools/pdf-viewer/package-lock.json"),
     "EmbedPDF build/guard, vendor hashes and PDF/browser E2E",
     "bash scripts/refresh_dependency_artifacts.sh pdf-viewer"),
    ("Research graph", ("tools/research-graph/package.json", "tools/research-graph/package-lock.json"),
     "Cytoscape vendor build, dependency gate and graph/browser E2E",
     "bash scripts/refresh_dependency_artifacts.sh research-graph"),
    ("GitHub automation", (".github/workflows/", ".github/dependabot.yml"),
     "actionlint, ShellCheck, zizmor and affected workflow execution",
     "Inspect pinned actions and relevant workflow runs"),
    ("Docker", ("Dockerfile",),
     "Build production container and run its smoke checks",
     "Verify pinned base image and container smoke test"),
)
DERIVED = {
    "Vue app": ("frontend/vue/", "frontend-app/THIRD_PARTY_LICENSES.md",
                "frontend/vendor/DEPENDENCY-MANIFEST.json", "frontend/sw.js"),
    "Frontend vendor": ("frontend/vendor/", "frontend/sw.js"),
    "PDF viewer": ("frontend/vendor/prks-pdf-viewer/", "frontend/sw.js"),
    "Research graph": ("frontend/vendor/cytoscape/", "frontend/sw.js"),
}


def matches(path: str, pattern: str) -> bool:
    return path == pattern or (pattern.endswith("/") and path.startswith(pattern))


def classify(paths: set[str]) -> list[tuple[str, str, str, bool]]:
    """Select validation lanes and detect absence of potentially derived files."""
    results = []
    for name, sources, checks, command in RULES:
        if any(matches(path, p) for path in paths for p in sources):
            derived = DERIVED.get(name, ())
            present = any(matches(path, target) for path in paths for target in derived)
            results.append((name, checks, command, present))
    return results


def changed_paths(base_sha: str, root: Path = ROOT) -> set[str]:
    if re.fullmatch(r"[0-9a-fA-F]{40}", base_sha) is None:
        raise ValueError("invalid 40-character comparison SHA")
    result = subprocess.run(
        ["git", "diff", "--name-only", "-z", base_sha, "HEAD", "--"],
        cwd=root, capture_output=True, check=True,
    )
    return {part.decode("utf-8") for part in result.stdout.split(b"\\0") if part}


def render(paths: set[str]) -> str:
    lanes = classify(paths)
    rows = [
        "## Dependency PR impact (advisory)",
        "",
        "The following are validation suggestions, not proof of passing CI, "
        "security approval, or merge readiness.",
        "",
        "| Dependency area | Checks to inspect | Maintainer action |",
        "| --- | --- | --- |",
    ]
    if not lanes:
        rows.append("| No tracked dependency scope | Follow normal PR checks | None |")
    for name, checks, command, _ in lanes:
        rows.append(f"| {name} | {checks} | {command} |")
    absent = [name for name, _, _, has_output in lanes
              if name in DERIVED and not has_output]
    if absent:
        rows.extend([
            "",
            "**No derived assets in diff:** " + ", ".join(absent) + ". "
            "Run the corresponding build and compare outputs; some dev-only "
            "updates legitimately generate identical bytes.",
        ])
    rows.extend([
        "",
        f"Changed files: {len(paths)}. Required checks, review findings and "
        "explicit maintainer merge approval remain authoritative.",
    ])
    return "\\n".join(rows) + "\\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="40-character PR base SHA")
    args = parser.parse_args(argv)
    output = render(changed_paths(args.base))
    print(output)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
            stream.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
