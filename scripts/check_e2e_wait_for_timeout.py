#!/usr/bin/env python3
"""Diff-aware guard against new E2E ``page.wait_for_timeout`` calls (#189).

Historical ``wait_for_timeout`` debt under ``tests/e2e/`` must not fail unrelated
PRs. This checker inspects the unified diff against a comparison base and fails
only when a **new** call site is introduced without an explicit exemption.

Exemptions (narrow):

1. Adjacent marker with a non-empty reason on the same line or the previous
   non-blank line::

       # prks-allow-wait-for-timeout: absence window for no-request proof
       page.wait_for_timeout(250)

2. Tiny path allowlist below for known intentional timing helpers. Prefer the
   marker for one-off cases; keep this set empty unless a helper file is the
   documented home for elapsed-time assertions.

Failure output cites the PRKS E2E "No arbitrary sleeps" policy and points at
observable waits (locator / ``wait_for_function`` / ``wait_for_async`` / route
sync).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

E2E_PREFIX = "tests/e2e/"
MARKER = "prks-allow-wait-for-timeout:"

# Tiny allowlist of relative paths (posix) whose entire file may introduce
# ``wait_for_timeout`` without a per-call marker. Ratchet down; no globs.
PATH_ALLOWLIST: frozenset[str] = frozenset()

# Attribute receiver may be ``page``, ``self.page``, etc. — the antipattern is
# the method, not the local name.
_WAIT_ATTR_RE = re.compile(r"""\.wait_for_timeout\s*\(""")

_HUNK_HEADER_RE = re.compile(
    r"^@@\s+-(?P<old_start>\d+)(?:,(?P<old_count>\d+))?"
    r"\s+\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))?\s+@@"
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    snippet: str
    reason: str

    def render(self) -> str:
        return (
            f"E2E-WAIT-001 {self.path}:{self.line}: {self.reason}\n"
            f"  snippet: {self.snippet.strip()}\n"
            f"  policy: tests/e2e/AGENTS.md — \"No arbitrary sleeps in E2E\". "
            f"Do not add page.wait_for_timeout(...) for post-mutation readiness. "
            f"Wait on a locator, wait_for_function (sync predicate), "
            f"wait_for_async (async predicate), route/network sync, or other "
            f"observable application state instead.\n"
            f"  exemption: adjacent `{MARKER} <reason>` on the same or previous "
            f"non-blank line, or a reviewed PATH_ALLOWLIST entry in "
            f"scripts/check_e2e_wait_for_timeout.py."
        )


class DiscoveryError(RuntimeError):
    """Git/base resolution failed; must not look like a clean pass."""


def _git(repo: Path, args: list[str], what: str) -> str:
    cmd = ["git", "-C", str(repo), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise DiscoveryError(
            f"{what} failed: could not run `git {' '.join(args)}` "
            f"({exc.__class__.__name__})"
        ) from exc
    if proc.returncode != 0:
        detail = [
            line.strip()
            for line in (proc.stderr or "").splitlines()
            if line.strip()
        ]
        raise DiscoveryError(
            f"{what} failed: `git {' '.join(args)}` exited {proc.returncode}"
            + (f" — {detail[0]}" if detail else "")
        )
    return proc.stdout or ""


def resolve_base(repo: Path, explicit: str | None) -> str:
    """Pick the comparison revision for added-line detection.

    Precedence: ``--base`` / argv, ``PRKS_E2E_WAIT_TIMEOUT_BASE``, then ``HEAD``
    (local dirty-tree check). CI for pull requests should pass the PR base ref.
    """
    if explicit:
        base = explicit
    else:
        base = (os.environ.get("PRKS_E2E_WAIT_TIMEOUT_BASE") or "").strip() or "HEAD"
    if base.startswith("-"):
        raise DiscoveryError(
            f"invalid --base {base!r}: a revision cannot start with '-' "
            "(git would read it as an option)"
        )
    _git(
        repo,
        ["rev-parse", "--verify", f"{base}^{{commit}}"],
        f"base revision check vs {base}",
    )
    return base


def line_has_wait_for_timeout(line: str) -> bool:
    """True when a source line invokes ``.wait_for_timeout(`` outside a comment-only line.

    Full-line comments / docstrings that merely mention the name are ignored so
    policy docs inside ``tests/e2e/`` can discuss the antipattern.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    # Drop a trailing ``# ...`` comment before matching the call.
    code = line.split("#", 1)[0]
    return bool(_WAIT_ATTR_RE.search(code))


def _marker_reason(text: str) -> str | None:
    idx = text.find(MARKER)
    if idx < 0:
        return None
    reason = text[idx + len(MARKER) :].strip()
    return reason or None


def line_is_exempt(lines: list[str], lineno_1based: int) -> bool:
    """True when the call line or previous non-blank line carries a reasoned marker."""
    if lineno_1based < 1 or lineno_1based > len(lines):
        return False
    current = lines[lineno_1based - 1]
    if _marker_reason(current) is not None:
        return True
    for i in range(lineno_1based - 2, -1, -1):
        prev = lines[i]
        if not prev.strip():
            continue
        return _marker_reason(prev) is not None
    return False


def path_is_allowlisted(relpath: str) -> bool:
    return relpath in PATH_ALLOWLIST


def _is_e2e_python(path: str | None) -> bool:
    return bool(
        path
        and path.startswith(E2E_PREFIX)
        and path.endswith(".py")
    )


def parse_unified_diff_added_waits(diff_text: str) -> list[tuple[str, int, str]]:
    """Return ``(path, new_lineno, line_text)`` for added wait_for_timeout lines."""
    findings: list[tuple[str, int, str]] = []
    path: str | None = None
    new_lineno = 0
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            path = None
            continue
        if raw.startswith("+++ "):
            token = raw[4:].strip()
            if token == "/dev/null":
                path = None
            elif token.startswith("b/"):
                path = token[2:]
            else:
                path = token
            continue
        if raw.startswith("--- "):
            continue
        match = _HUNK_HEADER_RE.match(raw)
        if match:
            new_lineno = int(match.group("new_start"))
            continue
        if raw.startswith("\\"):  # "\ No newline at end of file"
            continue
        if raw.startswith("+"):
            content = raw[1:]
            if _is_e2e_python(path) and line_has_wait_for_timeout(content):
                assert path is not None
                findings.append((path, new_lineno, content))
            new_lineno += 1
        elif raw.startswith("-"):
            continue
        elif raw.startswith(" "):
            new_lineno += 1
    return findings


def list_untracked_e2e_python(repo: Path) -> list[str]:
    raw = _git(
        repo,
        ["ls-files", "--others", "--exclude-standard", "--", E2E_PREFIX],
        "untracked E2E discovery",
    )
    out: list[str] = []
    for line in raw.splitlines():
        path = line.strip()
        if path.endswith(".py") and path.startswith(E2E_PREFIX):
            out.append(path)
    return out


def collect_findings(repo: Path, base: str) -> list[Finding]:
    """Return violations for new unapproved ``wait_for_timeout`` call sites."""
    diff_text = _git(
        repo,
        [
            "diff",
            "-U0",
            "--diff-filter=ACMR",
            base,
            "--",
            E2E_PREFIX,
        ],
        f"E2E wait_for_timeout diff vs {base}",
    )
    # When base is not HEAD, also include local uncommitted changes vs HEAD so a
    # dirty tree on a PR branch is still enforced (mirrors e2e affected policy).
    if base != "HEAD":
        local = _git(
            repo,
            [
                "diff",
                "-U0",
                "--diff-filter=ACMR",
                "HEAD",
                "--",
                E2E_PREFIX,
            ],
            "E2E wait_for_timeout local diff vs HEAD",
        )
        if local:
            diff_text = diff_text + ("\n" if diff_text and not diff_text.endswith("\n") else "") + local

    candidates = parse_unified_diff_added_waits(diff_text)

    # Entire contents of untracked E2E modules are "new".
    file_cache: dict[str, list[str]] = {}
    for rel in list_untracked_e2e_python(repo):
        abs_path = repo / rel
        try:
            text = abs_path.read_text(encoding="utf-8")
        except OSError:
            continue
        lines = text.splitlines()
        file_cache[rel] = lines
        for i, line in enumerate(lines, start=1):
            if line_has_wait_for_timeout(line):
                candidates.append((rel, i, line))

    # Deduplicate identical (path, line) from base+HEAD double diff.
    seen: set[tuple[str, int]] = set()
    findings: list[Finding] = []
    for rel, lineno, snippet in candidates:
        key = (rel, lineno)
        if key in seen:
            continue
        seen.add(key)
        if path_is_allowlisted(rel):
            continue
        if rel not in file_cache:
            abs_path = repo / rel
            if abs_path.is_file():
                file_cache[rel] = abs_path.read_text(encoding="utf-8").splitlines()
            else:
                file_cache[rel] = []
        lines = file_cache[rel]
        if line_is_exempt(lines, lineno):
            continue
        findings.append(
            Finding(
                path=rel,
                line=lineno,
                snippet=snippet,
                reason=(
                    "new page.wait_for_timeout(...) under tests/e2e/ without an "
                    "approved exemption"
                ),
            )
        )
    findings.sort(key=lambda f: (f.path, f.line))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="repository root (defaults to this script's parent repository)",
    )
    parser.add_argument(
        "--base",
        default=None,
        help=(
            "git revision to diff against (default: $PRKS_E2E_WAIT_TIMEOUT_BASE "
            "or HEAD). Pull-request CI should pass the PR base ref."
        ),
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()

    try:
        base = resolve_base(root, args.base)
        findings = collect_findings(root, base)
    except DiscoveryError as exc:
        print(f"E2E wait_for_timeout check failed closed: {exc}", file=sys.stderr)
        return 2

    if findings:
        for finding in findings:
            print(finding.render())
            print()
        print(
            f"E2E wait_for_timeout check failed: {len(findings)} new "
            f"unapproved call site(s) vs {base}"
        )
        return 1

    print(f"E2E wait_for_timeout check: OK (no new unapproved sites vs {base})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
