#!/usr/bin/env python3
"""PRKS dependency consistency gate (CLI).

Modes:
  --runtime         Validate the Python environment needed to start PRKS.
  --unit-contract   Runtime + openapi-core + Hypothesis (unit suite; no Playwright).
  --test            Runtime + Playwright (and other requirements-dev pins).
  --repo            Manifests, locks, vendor hashes, SW revision (offline; no npm install required).

Optional:
  --check-latest   Network freshness probe (never used by startup/tests/--repo).
  --write-manifest Regenerate frontend/vendor/DEPENDENCY-MANIFEST.json and SW revision.

Normal startup, tests, and --repo never contact external registries.

The repository consistency gate (vendor hashes, manifests, locks, the
service-worker revision, the Dockerfile and the inventory) lives here, because
it is tooling: it reads the whole repository and never runs in a served PRKS.
The runtime checks are ``backend.runtime.dependency_check``, which
``prks_app.py`` runs at startup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.runtime.dependency_check import (  # noqa: E402
    GateResult,
    RequirementsPinError,
    format_gate_report,
    load_inventory,
    python_min_version,
    read_requirements_pins,
    run_runtime_gate,
    run_test_gate,
    run_unit_contract_gate,
    runtime_requirement_pins,
    validate_requirements_file,
)

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

INVENTORY_PATH = REPO_ROOT / "dependency-inventory.json"
VENDOR_ROOT = REPO_ROOT / "frontend" / "vendor"
DEPENDENCY_MANIFEST_PATH = VENDOR_ROOT / "DEPENDENCY-MANIFEST.json"
SW_PATH = REPO_ROOT / "frontend" / "sw.js"
INDEX_HTML_PATH = REPO_ROOT / "frontend" / "index.html"

CDN_RUNTIME_MARKERS = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.jsdelivr.net",
    "unpkg.com",
    "cdnjs.cloudflare.com",
)

# Provenance URLs in VERSION files are allowed; production loaders must not load them.
CDN_LOADER_MARKERS = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.jsdelivr.net/npm/easymde",
    "cdn.jsdelivr.net/npm/codemirror",
    "cdn.jsdelivr.net/npm/lucide",
    "cdn.jsdelivr.net/npm/@embedpdf/snippet",
    "cdn.jsdelivr.net/npm/cytoscape",
    "unpkg.com",
)

NPM_ISLANDS = (
    {
        "name": "pdf-viewer",
        "dir": REPO_ROOT / "tools" / "pdf-viewer",
        "direct_keys": ("dependencies", "devDependencies"),
        "react_guard": True,
        "types_guard": True,
    },
    {
        "name": "research-graph",
        "dir": REPO_ROOT / "tools" / "research-graph",
        "direct_keys": ("dependencies", "devDependencies"),
        "react_guard": False,
        "types_guard": False,
    },
    {
        "name": "frontend-vendor",
        "dir": REPO_ROOT / "tools" / "frontend-vendor",
        "direct_keys": ("dependencies", "devDependencies"),
        "react_guard": False,
        "types_guard": False,
    },
    {
        "name": "frontend-app",
        "dir": REPO_ROOT / "frontend-app",
        "direct_keys": ("dependencies", "devDependencies"),
        "react_guard": False,
        "types_guard": False,
    },
)

#: The Citation Style Language files the citation formatter loads, under
#: ``frontend/vendor/`` (raw vendor, CC BY-SA 3.0), with their VERSION hash keys.
CSL_VENDOR_FILES = tuple(
    f"csl/{name}.csl"
    for name in (
        "apa", "chicago-author-date", "chicago-notes-bibliography", "modern-language-association",
        "harvard-cite-them-right", "ieee", "nlm-citation-sequence",
    )
) + ("csl/locales-en-US.xml",)


# Runtime vendor files that must appear in DEPENDENCY-MANIFEST and on disk.
REGISTERED_VENDOR_RUNTIME_FILES = (
    "inter/inter.css",
    "inter/InterVariable.woff2",
    "easymde/easymde.min.css",
    "easymde/easymde.min.js",
    "codemirror/codemirror.js",
    "codemirror/show-hint.js",
    "codemirror/show-hint.css",
    "dompurify/purify.min.js",
    "lucide/lucide.min.js",
    "cytoscape/cytoscape.min.js",
    "idb/idb.min.js",
    "citeproc/citeproc.js",
    "swagger-ui/swagger-ui-bundle.js",
    "swagger-ui/swagger-ui.css",
    *CSL_VENDOR_FILES,
    "prks-pdf-viewer/prks-pdf-viewer.js",
    "prks-pdf-viewer/prks-pdf-viewer.css",
    "prks-pdf-viewer/pdfium.wasm",
)


_SW_REV_RE = re.compile(
    r"const\s+DEPENDENCY_REVISION\s*=\s*['\"]([0-9a-fA-F]+)['\"]\s*;"
)
_SW_REV_ANY_RE = re.compile(
    r"const\s+DEPENDENCY_REVISION\s*=\s*['\"]([^'\"]+)['\"]\s*;"
)

# Inventory entries that are not derived from requirements*.txt / package.json /
# Dockerfile. Docker base image + apt packages come from the Dockerfile itself.
_INVENTORY_STRUCTURAL_NAMES = frozenset(
    {
        "inter",
        "idb",
        "csl",
        "python",
    }
)

# First-stage (or any) `FROM python:X.Y…` image tag.
_DOCKER_FROM_PYTHON_RE = re.compile(
    r"(?im)^\s*FROM\s+(?:--\S+\s+)*python:(\d+)\.(\d+)(?:[^\s]*)?"
)
# Collapse Dockerfile line continuations before apt token scans.
_DOCKER_LINE_CONT_RE = re.compile(r"\\\s*\n")
_DOCKER_APT_INSTALL_RE = re.compile(
    r"apt-get\s+install\b([^&\n|;]*)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# npm package.json ↔ lockfile
# ---------------------------------------------------------------------------


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def package_direct_pins(pkg: Mapping[str, Any]) -> dict[str, str]:
    pins: dict[str, str] = {}
    for key in ("dependencies", "devDependencies", "optionalDependencies"):
        section = pkg.get(key) or {}
        if isinstance(section, dict):
            for name, ver in section.items():
                pins[str(name)] = str(ver).lstrip("^~=")
    return pins


def lockfile_resolved_version(lock: Mapping[str, Any], package_name: str) -> str | None:
    packages = lock.get("packages")
    if isinstance(packages, dict):
        entry = packages.get(f"node_modules/{package_name}")
        if isinstance(entry, dict) and entry.get("version"):
            return str(entry["version"])
    # lockfileVersion 1 fallback
    deps = lock.get("dependencies")
    if isinstance(deps, dict):
        entry = deps.get(package_name)
        if isinstance(entry, dict) and entry.get("version"):
            return str(entry["version"]).split("-", 1)[0]
    return None


def validate_npm_island(island: Mapping[str, Any]) -> GateResult:
    result = GateResult(ok=True)
    directory = Path(island["dir"])
    pkg_path = directory / "package.json"
    lock_path = directory / "package-lock.json"
    name = island["name"]

    if not pkg_path.is_file():
        result.fail("missing_package_json", f"{name}: package.json missing", str(pkg_path))
        return result
    if not lock_path.is_file():
        result.fail("missing_lockfile", f"{name}: package-lock.json missing", str(lock_path))
        return result

    pkg = load_json(pkg_path)
    lock = load_json(lock_path)
    pins = package_direct_pins(pkg)

    for dep_name, want in pins.items():
        have = lockfile_resolved_version(lock, dep_name)
        if have is None:
            result.fail(
                "lock_missing_package",
                f"{name}: {dep_name}@{want} not resolved in lockfile",
                str(lock_path),
            )
        elif have != want:
            result.fail(
                "lock_version_mismatch",
                f"{name}: {dep_name} package.json={want} lockfile={have}",
                str(lock_path),
            )

    if island.get("react_guard"):
        react = pins.get("react")
        react_dom = pins.get("react-dom")
        if react and react_dom and react != react_dom:
            result.fail(
                "react_dom_mismatch",
                f"{name}: react ({react}) != react-dom ({react_dom})",
                str(pkg_path),
            )

    if island.get("types_guard"):
        types_react = pins.get("@types/react")
        types_dom = pins.get("@types/react-dom")
        # Major.minor of @types/react should match react's major.minor when both present.
        react = pins.get("react")
        if types_react and react:
            tr_mm = ".".join(types_react.split(".")[:2])
            r_mm = ".".join(react.split(".")[:2])
            if tr_mm != r_mm:
                result.fail(
                    "types_react_stack_mismatch",
                    f"{name}: @types/react {types_react} does not match react {react}",
                    str(pkg_path),
                )
        if types_react and types_dom:
            # @types/react-dom major should match @types/react major
            if types_react.split(".")[0] != types_dom.split(".")[0]:
                result.fail(
                    "types_dom_stack_mismatch",
                    f"{name}: @types/react {types_react} vs @types/react-dom {types_dom}",
                    str(pkg_path),
                )

    return result


def validate_all_npm_islands(repo_root: Path | None = None) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    for raw in NPM_ISLANDS:
        island = dict(raw)
        island["dir"] = root / Path(raw["dir"]).relative_to(REPO_ROOT)
        result.extend(validate_npm_island(island))
    return result


# ---------------------------------------------------------------------------
# Vendor VERSION / hash helpers
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_version_file(text: str) -> dict[str, str]:
    """Parse KEY: value / bare first-line version VERSION files."""
    data: dict[str, str] = {}
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return data
    first = lines[0].strip()
    if ":" not in first and " " not in first:
        data["version"] = first
    elif re.match(r"^[\d]", first) and ":" not in first.split()[0]:
        data["version"] = first.split()[0] if " " in first else first
    for line in lines:
        if ":" in line:
            key, _, val = line.partition(":")
            data[key.strip().lower()] = val.strip()
    # pdf-viewer VERSION uses "embedpdf 2.15.1" style
    for line in lines:
        parts = line.split()
        if len(parts) == 2 and parts[0] in ("embedpdf", "react", "react-dom", "fontFallback"):
            data[parts[0].lower()] = parts[1]
    return data


def validate_cytoscape_vendor(repo_root: Path | None = None) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    pkg = load_json(root / "tools" / "research-graph" / "package.json")
    want = str(pkg["dependencies"]["cytoscape"])
    version_path = root / "frontend" / "vendor" / "cytoscape" / "VERSION"
    js_path = root / "frontend" / "vendor" / "cytoscape" / "cytoscape.min.js"
    if not version_path.is_file():
        result.fail("missing_version", "cytoscape VERSION missing", str(version_path))
        return result
    if not js_path.is_file():
        result.fail("missing_asset", "cytoscape.min.js missing", str(js_path))
        return result
    meta = parse_version_file(version_path.read_text(encoding="utf-8"))
    ver = meta.get("version") or ""
    if ver != want:
        result.fail(
            "cytoscape_version_mismatch",
            f"cytoscape VERSION={ver} package.json={want}",
            str(version_path),
        )
    recorded = meta.get("sha256")
    actual = sha256_file(js_path)
    if not recorded:
        result.fail("cytoscape_missing_sha", "cytoscape VERSION has no sha256", str(version_path))
    elif recorded != actual:
        result.fail(
            "cytoscape_sha_mismatch",
            f"cytoscape.min.js sha256 {actual} != VERSION {recorded}",
            str(js_path),
        )
    text = version_path.read_text(encoding="utf-8")
    if "fetched:" in text.lower():
        result.fail(
            "nondeterministic_fetched",
            "cytoscape VERSION must not contain fetched: (nondeterministic)",
            str(version_path),
        )
    for marker in ("cdn.jsdelivr.net", "unpkg.com"):
        if marker in text:
            result.fail("cdn_in_version", f"cytoscape VERSION contains {marker}", str(version_path))
    return result


def validate_pdf_viewer_vendor(repo_root: Path | None = None) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    pkg = load_json(root / "tools" / "pdf-viewer" / "package.json")
    manifest_path = root / "frontend" / "vendor" / "prks-pdf-viewer" / "BUILD-MANIFEST.json"
    if not manifest_path.is_file():
        result.fail("missing_manifest", "BUILD-MANIFEST.json missing", str(manifest_path))
        return result
    manifest = load_json(manifest_path)
    if "builtAt" in manifest:
        result.fail(
            "nondeterministic_built_at",
            "BUILD-MANIFEST must not contain builtAt",
            str(manifest_path),
        )

    deps = pkg.get("dependencies") or {}
    embed_versions = {
        v for k, v in deps.items() if str(k).startswith("@embedpdf/")
    }
    if len(embed_versions) != 1:
        result.fail(
            "embedpdf_pin_spread",
            f"@embedpdf/* pins are not uniform: {sorted(embed_versions)}",
            str(root / "tools" / "pdf-viewer" / "package.json"),
        )
    embed = next(iter(embed_versions)) if embed_versions else None
    if embed and manifest.get("embedpdf") != embed:
        result.fail(
            "embedpdf_manifest_mismatch",
            f"BUILD-MANIFEST embedpdf={manifest.get('embedpdf')} package={embed}",
            str(manifest_path),
        )
    if manifest.get("react") != deps.get("react"):
        result.fail(
            "react_manifest_mismatch",
            f"BUILD-MANIFEST react={manifest.get('react')} package={deps.get('react')}",
            str(manifest_path),
        )
    if manifest.get("reactDom") != deps.get("react-dom"):
        result.fail(
            "react_dom_manifest_mismatch",
            f"BUILD-MANIFEST reactDom={manifest.get('reactDom')} package={deps.get('react-dom')}",
            str(manifest_path),
        )
    esbuild_pin = (pkg.get("devDependencies") or {}).get("esbuild")
    if esbuild_pin and manifest.get("esbuild") != esbuild_pin:
        result.fail(
            "esbuild_manifest_mismatch",
            f"BUILD-MANIFEST esbuild={manifest.get('esbuild')} package={esbuild_pin}",
            str(manifest_path),
        )

    out = manifest.get("outputSha256") or {}
    mapping = {
        "js": "prks-pdf-viewer.js",
        "css": "prks-pdf-viewer.css",
        "wasm": "pdfium.wasm",
    }
    vendor_dir = root / "frontend" / "vendor" / "prks-pdf-viewer"
    for key, filename in mapping.items():
        path = vendor_dir / filename
        if not path.is_file():
            result.fail("missing_asset", f"missing {filename}", str(path))
            continue
        actual = sha256_file(path)
        want = out.get(key)
        if not want:
            result.fail("missing_sha", f"BUILD-MANIFEST missing outputSha256.{key}", str(manifest_path))
        elif want != actual:
            result.fail(
                "pdf_sha_mismatch",
                f"{filename} sha256 {actual} != manifest {want}",
                str(path),
            )
    return result


def validate_frontend_vendor_island(repo_root: Path | None = None) -> GateResult:
    """package.json pins agree with VERSION files and on-disk hashes for npm vendors."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    pkg_path = root / "tools" / "frontend-vendor" / "package.json"
    if not pkg_path.is_file():
        result.fail("missing_frontend_vendor", "tools/frontend-vendor/package.json missing", str(pkg_path))
        return result
    pkg = load_json(pkg_path)
    deps = pkg.get("dependencies") or {}

    checks = [
        ("dompurify", "dompurify", ["purify.min.js"], "sha256"),
        ("easymde", "easymde", ["easymde.min.js", "easymde.min.css"], None),
        ("codemirror", "codemirror", ["codemirror.js", "show-hint.js", "show-hint.css"], None),
        ("lucide", "lucide", ["lucide.min.js"], "sha256"),
        ("citeproc", "citeproc", ["citeproc.js"], "sha256"),
        ("swagger-ui-dist", "swagger-ui", ["swagger-ui-bundle.js", "swagger-ui.css"], None),
    ]
    for dep_name, folder, files, primary_sha_key in checks:
        want = deps.get(dep_name)
        if not want:
            result.fail("missing_dep", f"frontend-vendor missing {dep_name}", str(pkg_path))
            continue
        version_path = root / "frontend" / "vendor" / folder / "VERSION"
        if not version_path.is_file():
            result.fail("missing_version", f"{folder}/VERSION missing", str(version_path))
            continue
        text = version_path.read_text(encoding="utf-8")
        if "fetched:" in text.lower():
            result.fail(
                "nondeterministic_fetched",
                f"{folder}/VERSION must not contain fetched:",
                str(version_path),
            )
        meta = parse_version_file(text)
        ver = meta.get("version") or ""
        if ver != want:
            result.fail(
                "vendor_version_mismatch",
                f"{folder} VERSION={ver} package.json={want}",
                str(version_path),
            )
        for filename in files:
            path = root / "frontend" / "vendor" / folder / filename
            if not path.is_file():
                result.fail("missing_asset", f"missing {folder}/{filename}", str(path))
                continue
            actual = sha256_file(path)
            # Prefer specific keys when present.
            recorded = None
            stem = filename.replace(".", "-")
            for key in (
                f"sha256-{filename}",
                f"sha256-{stem}",
                "sha256-js" if filename.endswith(".js") and "show-hint" not in filename else None,
                "sha256-css" if filename.endswith(".css") and "show-hint" not in filename else None,
                "sha256-lib" if filename == "codemirror.js" else None,
                "sha256-show-hint-js" if filename == "show-hint.js" else None,
                "sha256-show-hint-css" if filename == "show-hint.css" else None,
                "sha256" if primary_sha_key == "sha256" and filename.endswith(".js") else None,
            ):
                if key and key in meta:
                    recorded = meta[key]
                    break
            if recorded and recorded != actual:
                result.fail(
                    "vendor_sha_mismatch",
                    f"{folder}/{filename} sha256 {actual} != VERSION {recorded}",
                    str(path),
                )
            elif not recorded and primary_sha_key:
                # Require at least one hash for primary asset.
                if filename.endswith(".min.js") or filename == "purify.min.js":
                    result.fail(
                        "vendor_missing_sha",
                        f"{folder}/VERSION missing sha256 for {filename}",
                        str(version_path),
                    )
    return result


def _csl_sha_key(rel: str) -> str:
    """``csl/apa.csl`` -> ``sha256-apa-csl`` (VERSION keys are read lower-cased)."""
    return "sha256-" + rel.split("/", 1)[1].replace(".", "-").lower()


def validate_csl_vendor(repo_root: Path | None = None) -> GateResult:
    """Raw-vendor pin for the APA style and en-US locale: VERSION hashes match the files."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    version_path = root / "frontend" / "vendor" / "csl" / "VERSION"
    if not version_path.is_file():
        result.fail("missing_version", "csl VERSION missing", str(version_path))
        return result
    text = version_path.read_text(encoding="utf-8")
    if "fetched:" in text.lower():
        result.fail("nondeterministic_fetched", "csl VERSION must not contain fetched:", str(version_path))
    meta = parse_version_file(text)
    if not meta.get("version"):
        result.fail("csl_missing_version", "csl VERSION missing version", str(version_path))
    for rel in CSL_VENDOR_FILES:
        path = root / "frontend" / "vendor" / rel
        recorded = meta.get(_csl_sha_key(rel))
        if not path.is_file():
            result.fail("missing_asset", f"missing {rel}", str(path))
        elif not recorded:
            result.fail("csl_missing_sha", f"csl VERSION missing {_csl_sha_key(rel)}", str(version_path))
        elif recorded != sha256_file(path):
            result.fail("csl_sha_mismatch", f"{rel} sha256 {sha256_file(path)} != VERSION {recorded}", str(path))
    return result


def validate_inter_vendor(repo_root: Path | None = None) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    version_path = root / "frontend" / "vendor" / "inter" / "VERSION"
    woff = root / "frontend" / "vendor" / "inter" / "InterVariable.woff2"
    if not version_path.is_file():
        result.fail("missing_version", "inter VERSION missing", str(version_path))
        return result
    text = version_path.read_text(encoding="utf-8")
    if "fetched:" in text.lower():
        result.fail("nondeterministic_fetched", "inter VERSION must not contain fetched:", str(version_path))
    meta = parse_version_file(text)
    if not meta.get("version"):
        result.fail("inter_missing_version", "inter VERSION missing version", str(version_path))
    if not woff.is_file():
        result.fail("missing_asset", "InterVariable.woff2 missing", str(woff))
        return result
    recorded = meta.get("sha256")
    actual = sha256_file(woff)
    if recorded and recorded != actual:
        result.fail("inter_sha_mismatch", f"woff2 sha256 {actual} != VERSION {recorded}", str(woff))
    elif not recorded:
        result.fail("inter_missing_sha", "inter VERSION missing sha256", str(version_path))
    return result


def validate_idb_vendor(repo_root: Path | None = None) -> GateResult:
    """Raw-vendor pin for jakearchibald/idb (disposable offline-store plumbing)."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    version_path = root / "frontend" / "vendor" / "idb" / "VERSION"
    js_path = root / "frontend" / "vendor" / "idb" / "idb.min.js"
    if not version_path.is_file():
        result.fail("missing_version", "idb VERSION missing", str(version_path))
        return result
    text = version_path.read_text(encoding="utf-8")
    if "fetched:" in text.lower():
        result.fail("nondeterministic_fetched", "idb VERSION must not contain fetched:", str(version_path))
    meta = parse_version_file(text)
    if not meta.get("version"):
        result.fail("idb_missing_version", "idb VERSION missing version", str(version_path))
    if meta.get("npm") and meta["npm"] != f"idb@{meta.get('version')}":
        # Soft consistency: npm: idb@X should match the bare version line.
        if not str(meta.get("npm", "")).endswith("@" + str(meta.get("version", ""))):
            result.fail(
                "idb_version_mismatch",
                f"idb VERSION npm={meta.get('npm')} version={meta.get('version')}",
                str(version_path),
            )
    if not js_path.is_file():
        result.fail("missing_asset", "idb.min.js missing", str(js_path))
        return result
    recorded = meta.get("sha256")
    actual = sha256_file(js_path)
    if recorded and recorded != actual:
        result.fail("idb_sha_mismatch", f"idb.min.js sha256 {actual} != VERSION {recorded}", str(js_path))
    elif not recorded:
        result.fail("idb_missing_sha", "idb VERSION missing sha256", str(version_path))
    for marker in ("cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com"):
        if marker in text.lower():
            result.fail("cdn_in_version", f"idb VERSION contains {marker}", str(version_path))
    return result


# ---------------------------------------------------------------------------
# DEPENDENCY-MANIFEST + SW revision
# ---------------------------------------------------------------------------


def build_dependency_manifest(repo_root: Path | None = None) -> dict[str, Any]:
    """Deterministic production-vendor manifest (no timestamps)."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    entries: list[dict[str, Any]] = []

    def add(name: str, version: str, files: list[str], source_category: str) -> None:
        runtime = []
        for rel in files:
            path = root / "frontend" / "vendor" / rel
            runtime.append(
                {
                    "path": f"/vendor/{rel.replace(os.sep, '/')}",
                    "sha256": sha256_file(path) if path.is_file() else None,
                }
            )
        entries.append(
            {
                "name": name,
                "version": version,
                "source_category": source_category,
                "runtime_files": runtime,
            }
        )

    # Derive versions from authoritative sources where possible.
    fv = load_json(root / "tools" / "frontend-vendor" / "package.json")["dependencies"]
    add("dompurify", fv["dompurify"], ["dompurify/purify.min.js"], "npm:tools/frontend-vendor")
    add(
        "easymde",
        fv["easymde"],
        ["easymde/easymde.min.js", "easymde/easymde.min.css"],
        "npm:tools/frontend-vendor",
    )
    add(
        "codemirror",
        fv["codemirror"],
        [
            "codemirror/codemirror.js",
            "codemirror/show-hint.js",
            "codemirror/show-hint.css",
        ],
        "npm:tools/frontend-vendor",
    )
    add("lucide", fv["lucide"], ["lucide/lucide.min.js"], "npm:tools/frontend-vendor")
    add("citeproc", fv["citeproc"], ["citeproc/citeproc.js"], "npm:tools/frontend-vendor")
    add(
        "swagger-ui",
        fv["swagger-ui-dist"],
        ["swagger-ui/swagger-ui-bundle.js", "swagger-ui/swagger-ui.css"],
        "npm:tools/frontend-vendor",
    )

    cy = load_json(root / "tools" / "research-graph" / "package.json")["dependencies"]["cytoscape"]
    add("cytoscape", cy, ["cytoscape/cytoscape.min.js"], "npm:tools/research-graph")

    inter_meta = parse_version_file(
        (root / "frontend" / "vendor" / "inter" / "VERSION").read_text(encoding="utf-8")
    )
    add(
        "inter",
        inter_meta.get("version", ""),
        ["inter/inter.css", "inter/InterVariable.woff2"],
        "raw-vendor",
    )

    idb_meta = parse_version_file(
        (root / "frontend" / "vendor" / "idb" / "VERSION").read_text(encoding="utf-8")
    )
    add(
        "idb",
        idb_meta.get("version", ""),
        ["idb/idb.min.js"],
        "raw-vendor",
    )

    csl_meta = parse_version_file(
        (root / "frontend" / "vendor" / "csl" / "VERSION").read_text(encoding="utf-8")
    )
    add(
        "csl",
        csl_meta.get("version", ""),
        list(CSL_VENDOR_FILES),
        "raw-vendor",
    )

    pdf_pkg = load_json(root / "tools" / "pdf-viewer" / "package.json")
    embed = pdf_pkg["dependencies"]["@embedpdf/core"]
    add(
        "prks-pdf-viewer",
        embed,
        [
            "prks-pdf-viewer/prks-pdf-viewer.js",
            "prks-pdf-viewer/prks-pdf-viewer.css",
            "prks-pdf-viewer/pdfium.wasm",
        ],
        "npm:tools/pdf-viewer",
    )

    vue_pkg_path = root / "frontend-app" / "package.json"
    if vue_pkg_path.is_file():
        vue_pkg = load_json(vue_pkg_path)
        vue_ver = str((vue_pkg.get("dependencies") or {}).get("vue") or "")
        vue_dir = root / "frontend" / "vue"
        vue_runtime = []
        js_name = "prks-vue.js"
        js_path = vue_dir / js_name
        if js_path.is_file():
            vue_runtime.append(
                {
                    "path": f"/vue/{js_name}",
                    "sha256": sha256_file(js_path),
                }
            )
        if vue_ver and vue_runtime:
            entries.append(
                {
                    "name": "prks-vue",
                    "version": vue_ver,
                    "source_category": "npm:frontend-app",
                    "runtime_files": vue_runtime,
                }
            )

    model_path = root / "frontend" / "js" / "workspace-model.js"
    if model_path.is_file():
        entries.append(
            {
                "name": "prks-workspace-model",
                "version": "1",
                "source_category": "frontend-app:workspace-model",
                "runtime_files": [
                    {
                        "path": "/js/workspace-model.js",
                        "sha256": sha256_file(model_path),
                    }
                ],
            }
        )

    work_route_path = root / "frontend" / "js" / "work-route-projection.js"
    if work_route_path.is_file():
        entries.append(
            {
                "name": "prks-work-route-projection",
                "version": "1",
                "source_category": "frontend-app:work-route-projection",
                "runtime_files": [
                    {
                        "path": "/js/work-route-projection.js",
                        "sha256": sha256_file(work_route_path),
                    }
                ],
            }
        )

    search_codec_path = root / "frontend" / "js" / "search-query-codec.js"
    if search_codec_path.is_file():
        entries.append(
            {
                "name": "prks-search-query-codec",
                "version": "1",
                "source_category": "frontend-app:search-query-codec",
                "runtime_files": [
                    {
                        "path": "/js/search-query-codec.js",
                        "sha256": sha256_file(search_codec_path),
                    }
                ],
            }
        )

    tab_leave_path = root / "frontend" / "js" / "tab-leave.js"
    if tab_leave_path.is_file():
        entries.append(
            {
                "name": "prks-tab-leave",
                "version": "1",
                "source_category": "frontend-app:tab-leave",
                "runtime_files": [
                    {
                        "path": "/js/tab-leave.js",
                        "sha256": sha256_file(tab_leave_path),
                    }
                ],
            }
        )

    owner_resource_path = root / "frontend" / "js" / "owner-resource.js"
    if owner_resource_path.is_file():
        entries.append(
            {
                "name": "prks-owner-resource",
                "version": "1",
                "source_category": "frontend-app:owner-resource",
                "runtime_files": [
                    {
                        "path": "/js/owner-resource.js",
                        "sha256": sha256_file(owner_resource_path),
                    }
                ],
            }
        )

    editor_recovery_path = root / "frontend" / "js" / "editor-recovery.js"
    if editor_recovery_path.is_file():
        entries.append(
            {
                "name": "prks-editor-recovery",
                "version": "1",
                "source_category": "frontend-app:editor-recovery",
                "runtime_files": [
                    {
                        "path": "/js/editor-recovery.js",
                        "sha256": sha256_file(editor_recovery_path),
                    }
                ],
            }
        )

    sync_reconcile_path = root / "frontend" / "js" / "sync-reconcile.js"
    if sync_reconcile_path.is_file():
        entries.append(
            {
                "name": "prks-sync-reconcile",
                "version": "1",
                "source_category": "frontend-app:sync-reconcile",
                "runtime_files": [
                    {
                        "path": "/js/sync-reconcile.js",
                        "sha256": sha256_file(sync_reconcile_path),
                    }
                ],
            }
        )

    route_model_path = root / "frontend" / "js" / "route-model.js"
    if route_model_path.is_file():
        entries.append(
            {
                "name": "prks-route-model",
                "version": "1",
                "source_category": "frontend-app:route-model",
                "runtime_files": [
                    {
                        "path": "/js/route-model.js",
                        "sha256": sha256_file(route_model_path),
                    }
                ],
            }
        )

    entries.sort(key=lambda e: e["name"])
    return {
        "schema_version": 1,
        "dependencies": entries,
    }


def dependency_manifest_revision(manifest: Mapping[str, Any] | None = None, repo_root: Path | None = None) -> str:
    """Short content revision derived from the dependency manifest."""
    if manifest is None:
        root = Path(repo_root) if repo_root is not None else REPO_ROOT
        path = root / "frontend" / "vendor" / "DEPENDENCY-MANIFEST.json"
        manifest = load_json(path)
    # Canonical JSON, sorted keys — stable across platforms.
    blob = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def write_dependency_manifest(repo_root: Path | None = None) -> Path:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    manifest = build_dependency_manifest(root)
    path = root / "frontend" / "vendor" / "DEPENDENCY-MANIFEST.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_sw_dependency_revision(sw_text: str) -> str | None:
    m = _SW_REV_RE.search(sw_text)
    return m.group(1) if m else None


def apply_sw_dependency_revision(sw_text: str, revision: str) -> str:
    if not _SW_REV_ANY_RE.search(sw_text):
        raise ValueError("sw.js missing DEPENDENCY_REVISION constant")
    return _SW_REV_ANY_RE.sub(
        f"const DEPENDENCY_REVISION = '{revision}';",
        sw_text,
        count=1,
    )


def write_sw_dependency_revision(repo_root: Path | None = None) -> str:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    revision = dependency_manifest_revision(repo_root=root)
    path = root / "frontend" / "sw.js"
    text = path.read_text(encoding="utf-8")
    path.write_text(apply_sw_dependency_revision(text, revision), encoding="utf-8")
    return revision


def validate_dependency_manifest(repo_root: Path | None = None) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    path = root / "frontend" / "vendor" / "DEPENDENCY-MANIFEST.json"
    if not path.is_file():
        result.fail("missing_dependency_manifest", "DEPENDENCY-MANIFEST.json missing", str(path))
        return result
    on_disk = load_json(path)
    expected = build_dependency_manifest(root)
    if on_disk != expected:
        result.fail(
            "dependency_manifest_stale",
            "DEPENDENCY-MANIFEST.json does not match rebuild from authoritative sources",
            str(path),
        )
    # No timestamps
    blob = path.read_text(encoding="utf-8")
    if "builtAt" in blob or "fetched" in blob.lower() or "generated_at" in blob.lower():
        result.fail(
            "dependency_manifest_nondeterministic",
            "DEPENDENCY-MANIFEST must not contain timestamps",
            str(path),
        )
    return result


def validate_sw_revision(repo_root: Path | None = None) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    sw_path = root / "frontend" / "sw.js"
    text = sw_path.read_text(encoding="utf-8")
    have = read_sw_dependency_revision(text)
    want = dependency_manifest_revision(repo_root=root)
    if have is None:
        result.fail("sw_missing_revision", "sw.js missing DEPENDENCY_REVISION", str(sw_path))
    elif have != want:
        result.fail(
            "sw_revision_mismatch",
            f"sw.js DEPENDENCY_REVISION={have} expected {want}",
            str(sw_path),
        )
    if not re.search(
        r"STATIC_CACHE\s*=\s*['\"]prks-static-['\"]\s*\+\s*DEPENDENCY_REVISION",
        text,
    ):
        result.fail(
            "sw_static_cache_not_revisioned",
            "STATIC_CACHE must incorporate DEPENDENCY_REVISION",
            str(sw_path),
        )
    if not re.search(
        r"SHELL_CACHE\s*=\s*['\"]prks-shell-['\"]\s*\+\s*DEPENDENCY_REVISION",
        text,
    ):
        result.fail(
            "sw_shell_cache_not_revisioned",
            "SHELL_CACHE must incorporate DEPENDENCY_REVISION",
            str(sw_path),
        )
    if "RETIRE_PREFIXES" not in text:
        result.fail("sw_missing_retire", "sw.js missing RETIRE_PREFIXES", str(sw_path))
    return result


# ---------------------------------------------------------------------------
# Vendor asset registration + CDN forbid
# ---------------------------------------------------------------------------


def iter_production_loader_files(repo_root: Path | None = None) -> Iterable[Path]:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    frontend = root / "frontend"
    yield frontend / "index.html"
    for sub in ("css", "js"):
        base = frontend / sub
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix in (".js", ".css") and path.is_file():
                yield path
    sw = frontend / "sw.js"
    if sw.is_file():
        yield sw


def collect_vendor_refs(repo_root: Path | None = None) -> set[str]:
    """Collect /vendor/... path references from production loaders + SW."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    refs: set[str] = set()
    pattern = re.compile(r"""["'`](/vendor/[^"'`\s?#]+)""")
    for path in iter_production_loader_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in pattern.finditer(text):
            refs.add(m.group(1).split("?")[0])
    # pdf-viewer-runtime default base
    runtime = root / "frontend" / "js" / "pdf-viewer-runtime.js"
    if runtime.is_file():
        text = runtime.read_text(encoding="utf-8", errors="replace")
        for m in pattern.finditer(text):
            refs.add(m.group(1).split("?")[0])
    return refs


def validate_vendor_registration(repo_root: Path | None = None) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    manifest_path = root / "frontend" / "vendor" / "DEPENDENCY-MANIFEST.json"
    if not manifest_path.is_file():
        result.fail("missing_dependency_manifest", "DEPENDENCY-MANIFEST.json missing", str(manifest_path))
        return result
    manifest = load_json(manifest_path)
    registered: set[str] = set()
    for dep in manifest.get("dependencies") or []:
        for rf in dep.get("runtime_files") or []:
            p = rf.get("path")
            if p:
                registered.add(p)

    # Every registered asset must exist.
    for rel in REGISTERED_VENDOR_RUNTIME_FILES:
        path = root / "frontend" / "vendor" / rel
        url = "/vendor/" + rel.replace(os.sep, "/")
        if not path.is_file():
            result.fail("missing_registered_asset", f"registered asset missing: {url}", str(path))
        if url not in registered:
            result.fail(
                "unregistered_required_asset",
                f"required vendor asset not in DEPENDENCY-MANIFEST: {url}",
            )

    # Production refs under /vendor/ must be registered (ignore licenses/VERSION/metadata).
    meta_names = {
        "VERSION",
        "LICENSE",
        "BUILD-MANIFEST.json",
        "DEPENDENCY-MANIFEST.json",
        "THIRD_PARTY.md",
    }
    refs = collect_vendor_refs(root)
    for ref in sorted(refs):
        # Directory bases like /vendor/prks-pdf-viewer/ are ok if children registered.
        if ref.endswith("/"):
            continue
        name = Path(ref).name
        if name in meta_names or "/LICENSES/" in ref:
            continue
        if not ref.startswith("/vendor/"):
            continue
        # Only third-party runtime files need registration.
        rel = ref[len("/vendor/") :]
        vendor_file = root / "frontend" / "vendor" / rel
        if vendor_file.is_file() and ref not in registered:
            # Allow only if it's a known metadata file already skipped.
            result.fail(
                "unregistered_vendor_ref",
                f"production code references unregistered vendor file: {ref}",
            )

    # No orphan third-party runtime files under vendor/.
    vendor_root = root / "frontend" / "vendor"
    runtime_suffixes = (".js", ".css", ".wasm", ".woff2", ".woff", ".ttf")
    for path in vendor_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(vendor_root).as_posix()
        if path.name in meta_names or "LICENSES" in path.parts:
            continue
        url = "/vendor/" + rel
        if path.suffix.lower() in runtime_suffixes and url not in registered:
            result.fail(
                "unregistered_vendor_file",
                f"vendor runtime file not in DEPENDENCY-MANIFEST: {url}",
                str(path),
            )
    return result


def validate_no_cdn_in_loaders(repo_root: Path | None = None) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    for path in iter_production_loader_files(root):
        # Skip vendored third-party bundles themselves (may contain dead CDN template strings).
        try:
            path.relative_to(root / "frontend" / "vendor")
            continue
        except ValueError:
            pass
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in CDN_LOADER_MARKERS:
            if marker in text:
                result.fail(
                    "cdn_in_loader",
                    f"{path.relative_to(root)} contains banned CDN marker {marker}",
                    str(path),
                )
    return result


# ---------------------------------------------------------------------------
# Dockerfile (system / base image)
# ---------------------------------------------------------------------------


def dockerfile_python_base_version(text: str) -> tuple[int, int] | None:
    """Return ``(major, minor)`` from the first ``FROM python:X.Y…`` line."""
    match = _DOCKER_FROM_PYTHON_RE.search(text)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)))


def dockerfile_python_versions(text: str) -> list[tuple[int, int]]:
    """Return ``(major, minor)`` of every ``FROM python:X.Y…`` stage, in order."""
    return [
        (int(m.group(1)), int(m.group(2)))
        for m in _DOCKER_FROM_PYTHON_RE.finditer(text)
    ]


def dockerfile_apt_packages(text: str) -> list[str]:
    """Direct ``apt-get install`` package names, in first-seen order."""
    collapsed = _DOCKER_LINE_CONT_RE.sub(" ", text)
    packages: list[str] = []
    seen: set[str] = set()
    for match in _DOCKER_APT_INSTALL_RE.finditer(collapsed):
        for token in match.group(1).split():
            if token.startswith("-"):
                continue
            # Drop apt version/arch suffixes: pkg=1.2.3 / pkg:amd64
            name = token.split("=", 1)[0].split(":", 1)[0].strip()
            if not name or name in seen:
                continue
            seen.add(name)
            packages.append(name)
    return packages


def derived_dockerfile_inventory_names(repo_root: Path | None = None) -> set[str]:
    """Inventory names implied by the Dockerfile (base image + apt packages)."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    path = root / "Dockerfile"
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8")
    names: set[str] = set()
    if dockerfile_python_base_version(text) is not None:
        names.add("python-base-image")
    names.update(dockerfile_apt_packages(text))
    return names


def validate_dockerfile(repo_root: Path | None = None) -> GateResult:
    """Fail closed when the Dockerfile drifts from python_min_version / inventory.

    A missing Dockerfile is OK only when the inventory does not claim any
    Dockerfile-backed entries. Version checks run whenever a Dockerfile exists.
    """
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    path = root / "Dockerfile"
    inv_path = root / "dependency-inventory.json"

    if not path.is_file():
        if inv_path.is_file():
            inv = load_inventory(root)
            claimed = [
                d.get("name")
                for d in (inv.get("dependencies") or [])
                if d.get("manifest") == "Dockerfile"
                or d.get("name") == "python-base-image"
            ]
            if claimed:
                result.fail(
                    "dockerfile_missing",
                    (
                        "Dockerfile missing but dependency-inventory.json still "
                        f"lists Dockerfile-backed entries: {', '.join(sorted(map(str, claimed)))}"
                    ),
                    str(path),
                )
        return result

    text = path.read_text(encoding="utf-8")
    image_ver = dockerfile_python_base_version(text)
    if image_ver is None:
        result.fail(
            "dockerfile_python_base",
            "Dockerfile has no FROM python:X.Y image tag",
            str(path),
        )
        return result

    if not inv_path.is_file():
        return result

    need = python_min_version(root)[:2]
    # Every Python stage (builder and runtime) runs exactly the Python that CI
    # tests (python_min_version), not merely a newer one.
    for ver in dockerfile_python_versions(text):
        if ver != need:
            result.fail(
                "dockerfile_python_version",
                (
                    f"Dockerfile FROM python:{ver[0]}.{ver[1]} does not match "
                    f"python_min_version {need[0]}.{need[1]}"
                ),
                str(path),
            )
    return result


# ---------------------------------------------------------------------------
# Hash-locked runtime install (Docker image)
# ---------------------------------------------------------------------------

RUNTIME_LOCK_FILE = "requirements-lock.txt"
RUNTIME_LOCK_COMMAND = "python scripts/lock_runtime_requirements.py"
_LOCK_ENTRY_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)==([^\s;]+)\s*(?:;([^-]*?))?((?:\s*--hash=sha256:[0-9a-f]{64})+)\s*$"
)
_LOCK_HASH_RE = re.compile(r"--hash=sha256:([0-9a-f]{64})")


def canonical_package_name(name: str) -> str:
    """PEP 503 normalised name (``Pillow`` and ``pillow`` are one package)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_runtime_lock(
    text: str, *, source: str = RUNTIME_LOCK_FILE
) -> dict[str, tuple[str, tuple[str, ...], str]]:
    """Parse a hashed lock: every entry is ``name==version`` with sha256 hashes.

    Each value is ``(version, hashes, marker)``; ``marker`` is the environment
    marker after ``;`` or ``""`` when the entry is unconditional.

    Backslash continuations are joined and full-line comments skipped. Any
    other line shape (range, include, option, missing hash) fails closed.
    """
    entries: dict[str, tuple[str, tuple[str, ...], str]] = {}
    logical: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not buffer and (not line or line.startswith("#")):
            continue
        if line.endswith("\\"):
            buffer += line[:-1].strip() + " "
            continue
        logical.append(buffer + line)
        buffer = ""
    if buffer:
        logical.append(buffer)
    for entry in logical:
        m = _LOCK_ENTRY_RE.match(entry)
        if not m:
            raise RequirementsPinError(
                f"{source}: entry must be name==version with sha256 hashes; got {entry[:120]!r}"
            )
        name = canonical_package_name(m.group(1))
        if name in entries:
            raise RequirementsPinError(f"{source}: duplicate entry for {name!r}")
        entries[name] = (m.group(2), tuple(_LOCK_HASH_RE.findall(m.group(4))), (m.group(3) or "").strip())
    return entries


def _requirement_files(instruction: str) -> list[str]:
    """The exact ``-r``/``--requirement`` file arguments of a shell command."""
    words = instruction.split()
    files: list[str] = []
    for i, word in enumerate(words):
        if word in ("-r", "--requirement") and i + 1 < len(words):
            files.append(words[i + 1])
        elif word.startswith("--requirement="):
            files.append(word.split("=", 1)[1])
        elif word.startswith("-r") and len(word) > 2:
            files.append(word[2:])
    return files


def dockerfile_instructions(text: str) -> list[str]:
    """Executable Dockerfile instructions, continuations joined, comments dropped.

    Docker ignores a line whose first non-blank character is ``#``, also
    inside a continued instruction, so a commented install never counts.
    """
    instructions: list[str] = []
    current: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            current.append(line[:-1].strip())
            continue
        current.append(line)
        instructions.append(" ".join(part for part in current if part))
        current = []
    if current:
        instructions.append(" ".join(part for part in current if part))
    return instructions


def validate_runtime_lock(repo_root: Path | None = None) -> GateResult:
    """Fail closed when the Docker image's hashed lock drifts from requirements.txt.

    Every requirements.txt pin must appear in the lock at the same version,
    every lock entry must carry at least one sha256 hash, and the Dockerfile
    must install from the lock with ``--require-hashes``.
    """
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    docker = root / "Dockerfile"
    lock = root / RUNTIME_LOCK_FILE
    if not docker.is_file():
        return result
    hint = f"Regenerate with: {RUNTIME_LOCK_COMMAND}"
    if not lock.is_file():
        result.fail("runtime_lock_missing", f"{RUNTIME_LOCK_FILE} missing. {hint}", str(lock))
        return result
    try:
        entries = parse_runtime_lock(lock.read_text(encoding="utf-8"))
        pins = read_requirements_pins(root / "requirements.txt")
    except (OSError, RequirementsPinError) as exc:
        result.fail("runtime_lock_invalid", f"{exc}. {hint}", str(lock))
        return result
    for name, version in sorted(pins.items()):
        locked = entries.get(canonical_package_name(name))
        if locked is None:
            result.fail(
                "runtime_lock_stale",
                f"{name}=={version} from requirements.txt is not in {RUNTIME_LOCK_FILE}. {hint}",
                str(lock),
            )
        elif locked[0] != version:
            result.fail(
                "runtime_lock_stale",
                f"{RUNTIME_LOCK_FILE} locks {name}=={locked[0]} but requirements.txt pins {version}. {hint}",
                str(lock),
            )
        elif locked[2]:
            # A direct pin must install everywhere; a marker can switch it off
            # in the Linux image (conditional transitive entries stay valid).
            result.fail(
                "runtime_lock_stale",
                f"{RUNTIME_LOCK_FILE} locks direct pin {name} only when {locked[2]!r}. {hint}",
                str(lock),
            )
    if not any(
        instruction.split(maxsplit=1)[0].upper() == "RUN"
        and "pip install" in instruction
        and "--require-hashes" in instruction.split()
        and RUNTIME_LOCK_FILE in _requirement_files(instruction)
        for instruction in dockerfile_instructions(docker.read_text(encoding="utf-8"))
    ):
        result.fail(
            "runtime_lock_unused",
            f"Dockerfile must install Python packages with --require-hashes -r {RUNTIME_LOCK_FILE}",
            str(docker),
        )
    return result


# ---------------------------------------------------------------------------
# Inventory coverage
# ---------------------------------------------------------------------------


def derived_inventory_names(repo_root: Path | None = None) -> set[str]:
    """Names that must appear in dependency-inventory.json, from manifests.

    Sources: requirements*.txt exact pins, npm island direct dependencies
    (``@embedpdf/*`` collapsed), Dockerfile (``python-base-image`` + apt
    packages), plus structural entries (Inter, python).
    """
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    names: set[str] = set(_INVENTORY_STRUCTURAL_NAMES)
    names.update(derived_dockerfile_inventory_names(root))

    for rel in ("requirements.txt", "requirements-dev.txt"):
        path = root / rel
        if not path.is_file():
            continue
        # Fail closed is enforced separately; still derive what we can.
        try:
            names.update(read_requirements_pins(path))
        except RequirementsPinError:
            continue

    for raw in NPM_ISLANDS:
        island_dir = root / Path(raw["dir"]).relative_to(REPO_ROOT)
        pkg_path = island_dir / "package.json"
        if not pkg_path.is_file():
            continue
        pkg = load_json(pkg_path)
        for dep_name in package_direct_pins(pkg):
            if dep_name.startswith("@embedpdf/"):
                names.add("@embedpdf/*")
            else:
                names.add(dep_name)
    return names


def validate_inventory(repo_root: Path | None = None) -> GateResult:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)

    for rel in ("requirements.txt", "requirements-dev.txt"):
        req_result, _pins = validate_requirements_file(root / rel)
        result.extend(req_result)

    inv_path = root / "dependency-inventory.json"
    if not inv_path.is_file():
        result.fail(
            "inventory_missing_file",
            "dependency-inventory.json missing",
            str(inv_path),
        )
        return result

    result.extend(validate_dockerfile(root))

    inv = load_inventory(root)
    deps = inv.get("dependencies") or []
    declared = {d.get("name") for d in deps if d.get("name")}
    expected = derived_inventory_names(root)

    for name in sorted(expected - declared):
        result.fail(
            "inventory_missing",
            f"dependency-inventory.json missing {name} (required by authoritative manifests)",
        )
    for name in sorted(declared - expected):
        result.fail(
            "inventory_extra",
            f"dependency-inventory.json has {name} not present in authoritative manifests",
        )

    for dep in deps:
        auth = dep.get("authoritative_source")
        if not auth:
            result.fail(
                "inventory_no_source",
                f"{dep.get('name')}: missing authoritative_source",
            )
        if dep.get("exact_installed_version_check") and dep.get("scope") in (
            "runtime",
            "test",
        ):
            manifest = dep.get("manifest")
            if (
                manifest
                and not (root / manifest).is_file()
                and manifest != "dependency-inventory.json"
            ):
                result.fail(
                    "inventory_bad_manifest",
                    f"{dep.get('name')}: manifest {manifest} missing",
                )
    return result


VUE_BUNDLE_URL = "/vue/prks-vue.js"
VUE_BOOTSTRAP_MARKER = "data-prks-vue-bootstrap"
VUE_NODE_ENGINES = ">=24.15.0 <25"
_VUE_CDN_MARKERS = (
    "cdn.jsdelivr.net",
    "unpkg.com",
    "cdnjs.cloudflare.com",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
)


def validate_vue_production(repo_root: Path | None = None) -> GateResult:
    """Committed Vue bundle matches the frontend-app pins and the shell loads it."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    pkg_path = root / "frontend-app" / "package.json"
    manifest_path = root / "frontend" / "vue" / "BUILD-MANIFEST.json"
    js_path = root / "frontend" / "vue" / "prks-vue.js"
    if not pkg_path.is_file():
        result.fail("missing_vue_package", "frontend-app/package.json missing", str(pkg_path))
        return result
    if not manifest_path.is_file():
        result.fail("missing_vue_manifest", "frontend/vue/BUILD-MANIFEST.json missing", str(manifest_path))
        return result
    if not js_path.is_file():
        result.fail("missing_vue_bundle", "frontend/vue/prks-vue.js missing", str(js_path))
        return result

    pkg = load_json(pkg_path)
    manifest = load_json(manifest_path)
    if "builtAt" in manifest or "timestamp" in manifest:
        result.fail(
            "nondeterministic_vue_manifest",
            "Vue BUILD-MANIFEST must not contain timestamps",
            str(manifest_path),
        )
    engines = str((pkg.get("engines") or {}).get("node") or "")
    if engines != VUE_NODE_ENGINES:
        result.fail(
            "vue_node_toolchain",
            f"frontend-app engines.node must be {VUE_NODE_ENGINES!r}",
            str(pkg_path),
        )
    lock_path = root / "frontend-app" / "package-lock.json"
    lock_engines = ""
    if lock_path.is_file():
        lock = load_json(lock_path)
        packages = lock.get("packages") if isinstance(lock, dict) else None
        root_pkg = packages.get("") if isinstance(packages, dict) else None
        if isinstance(root_pkg, dict):
            lock_engines = str((root_pkg.get("engines") or {}).get("node") or "")
    if lock_engines != VUE_NODE_ENGINES:
        result.fail(
            "vue_lock_engines",
            f"frontend-app lockfile engines.node must be {VUE_NODE_ENGINES!r}",
            str(lock_path),
        )

    deps = pkg.get("dependencies") or {}
    dev = pkg.get("devDependencies") or {}
    expected_pins = {
        "vue": deps.get("vue"),
        "tanstackVueQuery": deps.get("@tanstack/vue-query"),
        "vueUse": deps.get("@vueuse/core"),
        "vite": dev.get("vite"),
        "typescript": dev.get("typescript"),
        "vueTsc": dev.get("vue-tsc"),
        "vitest": dev.get("vitest"),
    }

    def nonempty_pin(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        return text or None

    for key, want in expected_pins.items():
        have = manifest.get(key)
        want_pin = nonempty_pin(want)
        have_pin = nonempty_pin(have)
        if want_pin is None or have_pin is None or have_pin != want_pin:
            result.fail(
                "vue_manifest_pin",
                f"BUILD-MANIFEST {key}={have!r} package.json={want!r}",
                str(manifest_path),
            )

    recorded = manifest.get("outputSha256") or {}
    if not isinstance(recorded, dict) or "prks-vue.js" not in recorded:
        result.fail(
            "vue_manifest_sha",
            "BUILD-MANIFEST outputSha256.prks-vue.js missing",
            str(manifest_path),
        )
    else:
        actual = sha256_file(js_path)
        if recorded.get("prks-vue.js") != actual:
            result.fail(
                "vue_bundle_sha",
                "prks-vue.js sha256 does not match BUILD-MANIFEST",
                str(js_path),
            )
    vue_dir = root / "frontend" / "vue"
    for css_path in sorted(vue_dir.rglob("*.css")):
        result.fail(
            "vue_css_not_inlined",
            "Vite CSS must be inlined into prks-vue.js; do not ship a separate stylesheet",
            str(css_path),
        )
    if isinstance(recorded, dict):
        for name in recorded:
            if str(name).endswith(".css"):
                result.fail(
                    "vue_css_not_inlined",
                    f"BUILD-MANIFEST lists {name}; CSS must be inlined into prks-vue.js",
                    str(manifest_path),
                )

    bundle = js_path.read_text(encoding="utf-8", errors="replace")
    if VUE_BOOTSTRAP_MARKER not in bundle:
        result.fail(
            "vue_bundle_marker",
            "prks-vue.js is missing the bootstrap marker",
            str(js_path),
        )
    if re.search(r"""from\s*["']vue["']""", bundle):
        result.fail(
            "vue_external_import",
            "prks-vue.js imports vue at runtime; the production bundle must include it",
            str(js_path),
        )
    for marker in _VUE_CDN_MARKERS:
        if marker in bundle:
            result.fail(
                "vue_cdn",
                f"prks-vue.js contains CDN marker {marker}",
                str(js_path),
            )
    if (root / "frontend" / "vue" / "index.html").is_file():
        result.fail(
            "vue_second_shell",
            "frontend/vue/index.html must not be shipped; the legacy shell loads the bundle",
            str(root / "frontend" / "vue" / "index.html"),
        )

    index = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    if 'id="prks-vue-root"' not in index:
        result.fail("vue_mount_missing", "index.html is missing #prks-vue-root", str(root / "frontend" / "index.html"))
    module_tag = re.search(
        r'<script\b[^>]*\bsrc="/vue/prks-vue\.js"[^>]*>',
        index,
    )
    if module_tag is None or 'type="module"' not in module_tag.group(0):
        result.fail(
            "vue_module_tag",
            "index.html must load /vue/prks-vue.js as a module script",
            str(root / "frontend" / "index.html"),
        )

    sw = (root / "frontend" / "sw.js").read_text(encoding="utf-8")
    if "'/vue/prks-vue.js'" not in sw:
        result.fail(
            "vue_sw_precache",
            "sw.js STATIC_PRECACHE_PATHS must include /vue/prks-vue.js",
            str(root / "frontend" / "sw.js"),
        )
    if "'/vue/'" not in sw:
        result.fail(
            "vue_sw_prefix",
            "sw.js STATIC_PATH_PREFIXES must include /vue/",
            str(root / "frontend" / "sw.js"),
        )
    return result


def run_repo_gate(repo_root: Path | None = None) -> GateResult:
    """Offline repository consistency. Does not require npm packages installed."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    result = GateResult(ok=True)
    result.extend(validate_inventory(root))
    result.extend(validate_all_npm_islands(root))
    result.extend(validate_cytoscape_vendor(root))
    result.extend(validate_pdf_viewer_vendor(root))
    result.extend(validate_frontend_vendor_island(root))
    result.extend(validate_inter_vendor(root))
    result.extend(validate_idb_vendor(root))
    result.extend(validate_runtime_lock(root))
    result.extend(validate_csl_vendor(root))
    result.extend(validate_dependency_manifest(root))
    result.extend(validate_sw_revision(root))
    result.extend(validate_vendor_registration(root))
    result.extend(validate_no_cdn_in_loaders(root))
    result.extend(validate_vue_production(root))
    return result




def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--runtime", action="store_true", help="Python runtime pins for starting PRKS")
    mode.add_argument(
        "--unit-contract",
        action="store_true",
        help="Runtime + openapi-core + Hypothesis (unit/API tests; no Playwright)",
    )
    mode.add_argument("--test", action="store_true", help="Runtime + test (Playwright) pins")
    mode.add_argument(
        "--repo",
        action="store_true",
        help="Repository consistency (offline; no npm install required)",
    )
    mode.add_argument(
        "--check-latest",
        action="store_true",
        help="Optional network freshness probe (not for startup/tests/--repo)",
    )
    mode.add_argument(
        "--write-manifest",
        action="store_true",
        help="Rewrite DEPENDENCY-MANIFEST.json and sw.js DEPENDENCY_REVISION",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root (default: detected)",
    )
    return parser


def check_latest(repo_root: Path) -> int:
    """Explicit network freshness helper. Distinguishes API failure vs updates."""
    try:
        import urllib.request
    except ImportError:
        print("urllib unavailable", file=sys.stderr)
        return 2

    # Only probe PyPI for runtime pins — never during startup.
    pins = runtime_requirement_pins(repo_root)
    updates = []
    api_failures = []
    for name, pinned in pins.items():
        url = f"https://pypi.org/pypi/{name}/json"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            latest = data.get("info", {}).get("version")
            if latest and latest != pinned:
                updates.append(f"{name}: pinned {pinned}, latest {latest}")
        except Exception as exc:
            api_failures.append(f"{name}: API failure ({type(exc).__name__})")

    if api_failures:
        print("Freshness check: API failures (could not determine updates):", file=sys.stderr)
        for line in api_failures:
            print("  " + line, file=sys.stderr)
        if updates:
            print("Known updates before failure:", file=sys.stderr)
            for line in updates:
                print("  " + line, file=sys.stderr)
        return 2
    if updates:
        print("Updates available:")
        for line in updates:
            print("  " + line)
        return 1
    print("Freshness check: all probed runtime pins match latest on PyPI")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()

    if args.write_manifest:
        path = write_dependency_manifest(root)
        rev = write_sw_dependency_revision(root)
        print(f"Wrote {path}")
        print(f"Updated sw.js DEPENDENCY_REVISION={rev}")
        return 0

    if args.check_latest:
        return check_latest(root)

    if args.runtime:
        result = run_runtime_gate(repo_root=root)
    elif args.unit_contract:
        result = run_unit_contract_gate(repo_root=root)
    elif args.test:
        result = run_test_gate(repo_root=root)
    else:
        result = run_repo_gate(repo_root=root)

    print(format_gate_report(result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
