#!/usr/bin/env python3
"""Build a self-contained classic-script distribution without changing the source tree.

The raw checkout intentionally remains unbundled. Official Docker packaging
can produce an ordered, two-bundle frontend with its own service-worker cache
revision; other release formats may reuse the same offline-only build step.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import shutil
import tempfile

from .benchmark_classic_script_bundle import (
    BODY_URL,
    CLASSIC_PREFIX,
    HEAD_URL,
    _source_from_tag,
    build_candidate,
)

HEAD_BUNDLE = "/js/prks-classic-head.bundle.js"
BODY_BUNDLE = "/js/prks-classic-body.bundle.js"
BUNDLES = {"head": HEAD_BUNDLE, "body": BODY_BUNDLE}
_MANIFEST_START = "    const STATIC_PRECACHE_PATHS = [\n"
_MANIFEST_END = "    ];"
_SOURCE_ENTRY = re.compile(r"^\s*'(/[^']+)',\s*$")
_CACHE_DECLARATIONS = (
    "const SHELL_CACHE = 'prks-shell-' + DEPENDENCY_REVISION;",
    "const STATIC_CACHE = 'prks-static-' + DEPENDENCY_REVISION;",
)
_REVISION_DECLARATION = re.compile(
    r"const DEPENDENCY_REVISION = '[0-9a-f]{12}';"
)


def _classic_groups(html: str) -> dict[str, list[str]]:
    """Read exactly the source tags already verified by build_candidate()."""
    head_end = html.find("</head>")
    if head_end < 0:
        raise ValueError("missing head parser boundary")
    groups: dict[str, list[str]] = {"head": [], "body": []}
    offset = 0
    for line in html.splitlines(keepends=True):
        line_start = offset
        offset += len(line)
        stripped = line.strip()
        if not stripped.startswith("<script"):
            continue
        src = _source_from_tag(stripped, CLASSIC_PREFIX)
        if src:
            group = "head" if line_start < head_end else "body"
            if src in groups["head"] or src in groups["body"]:
                raise ValueError(f"duplicate classic source: {src}")
            groups[group].append(src)
    if not groups["head"] or not groups["body"]:
        raise ValueError("expected nonempty classic head and body groups")
    return groups


def _distribution_worker(
    worker: str, groups: dict[str, list[str]], revision: str,
) -> str:
    """Replace the exact startup precache inventory, preserving unrelated assets.

    The old individual classics remain on disk for lazy consumers and rollback,
    but they are no longer downloaded eagerly to install an offline shell.
    """
    if worker.count(_MANIFEST_START) != 1:
        raise ValueError("service worker precache declaration changed")
    before, tail = worker.split(_MANIFEST_START, 1)
    if _MANIFEST_END not in tail:
        raise ValueError("service worker precache closing boundary missing")
    entries, after = tail.split(_MANIFEST_END, 1)
    originals = {src: group for group, scripts in groups.items() for src in scripts}
    found: set[str] = set()
    inserted: set[str] = set()
    updated: list[str] = []
    for line in entries.splitlines(keepends=True):
        match = _SOURCE_ENTRY.fullmatch(line.strip())
        src = match.group(1) if match else None
        if src not in originals:
            updated.append(line)
            continue
        if src in found:
            raise ValueError(f"duplicated required service-worker path: {src}")
        found.add(src)
        group = originals[src]
        if group not in inserted:
            indent = line[:len(line) - len(line.lstrip())]
            updated.append(f"{indent}'{BUNDLES[group]}',\n")
            inserted.add(group)
    if found != set(originals) or inserted != set(BUNDLES):
        missing = sorted(set(originals) - found)
        raise ValueError(f"classic scripts missing from worker precache: {missing}")

    result = before + _MANIFEST_START + "".join(updated) + _MANIFEST_END + after
    if not _REVISION_DECLARATION.search(result) or len(_REVISION_DECLARATION.findall(result)) != 1:
        raise ValueError("unknown service worker dependency revision declaration")
    result = _REVISION_DECLARATION.sub(
        lambda m: m.group(0) + f"\n    const CLASSIC_BUNDLE_REVISION = '{revision}';",
        result, count=1,
    )
    for old in _CACHE_DECLARATIONS:
        if result.count(old) != 1:
            raise ValueError("service worker cache revision contract changed")
        result = result.replace(
            old,
            old.removesuffix(";") + " + '-' + CLASSIC_BUNDLE_REVISION;",
            1,
        )
    if "        '/js/'," not in result and "'/js/'" not in result:
        raise ValueError("service worker no longer recognizes /js/ static assets")
    return result


def render_assets(root: Path) -> tuple[str, dict[str, str], str]:
    """Generate and strictly validate all three coupled production artifacts."""
    frontend = root / "frontend"
    original = (frontend / "index.html").read_text(encoding="utf-8")
    candidate, benchmark_bundles, counts = build_candidate(root)
    groups = _classic_groups(original)
    if counts != {group: len(paths) for group, paths in groups.items()}:
        raise ValueError("benchmark candidate and source inventory disagree")
    if counts != {"head": 5, "body": 79}:
        raise ValueError(f"classic inventory has changed: {counts}; review before packaging")
    html = candidate.replace(HEAD_URL, HEAD_BUNDLE).replace(BODY_URL, BODY_BUNDLE)
    if HEAD_URL in html or BODY_URL in html:
        raise ValueError("temporary bundle references remain in production HTML")
    bundles = {
        HEAD_BUNDLE: benchmark_bundles[HEAD_URL].replace(
            "/* benchmark source:", "/* bundled source:"
        ),
        BODY_BUNDLE: benchmark_bundles[BODY_URL].replace(
            "/* benchmark source:", "/* bundled source:"
        ),
    }
    for url, text in bundles.items():
        if not text or url in original:
            raise ValueError(f"invalid or preexisting production bundle: {url}")
        if (frontend / url.lstrip("/")).exists():
            raise ValueError(f"bundle output collides with a source asset: {url}")
    fingerprint = hashlib.sha256()
    fingerprint.update(html.encode("utf-8"))
    for url in (HEAD_BUNDLE, BODY_BUNDLE):
        fingerprint.update(url.encode("ascii"))
        fingerprint.update(bundles[url].encode("utf-8"))
    revision = fingerprint.hexdigest()[:16]
    worker = _distribution_worker(
        (frontend / "sw.js").read_text(encoding="utf-8"), groups, revision
    )
    return html, bundles, worker


def build_distribution(root: Path, output: Path) -> None:
    """Publish atomically; a failed build leaves the original and target untouched."""
    root = root.resolve()
    frontend = (root / "frontend").resolve()
    output = output.resolve()
    if output == frontend or output.is_relative_to(frontend):
        raise ValueError("output must not overwrite the checked-out frontend")
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    html, bundles, worker = render_assets(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="prks-classic-stage-", dir=output.parent) as temp:
        staged = Path(temp) / "frontend"
        shutil.copytree(frontend, staged)
        (staged / "index.html").write_text(html, encoding="utf-8")
        (staged / "sw.js").write_text(worker, encoding="utf-8")
        for url, text in bundles.items():
            path = staged / url.lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        if any(not (staged / url.lstrip("/")).is_file() for url in bundles):
            raise ValueError("built frontend missing a required classic bundle")
        staged.rename(output)
    print(f"Built two classic bundles with versioned offline cache in {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build_distribution(args.root, args.output)


if __name__ == "__main__":
    main()
