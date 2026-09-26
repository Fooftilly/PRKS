#!/usr/bin/env python3
"""Diff-aware guard against new E2E ``page.wait_for_timeout`` calls (#189).

Historical ``wait_for_timeout`` debt under ``tests/e2e/`` must not fail unrelated
PRs. The checker compares wait call sites on touched paths (and paths removed
from the allowlist) against the comparison base:

- **New** unapproved calls fail.
- Calls that were **exempt in the base** (marker or path allowlist) but are
  no longer exempt fail — even when the sleep line itself is unchanged
  (exemption-removal ratchet).
- Historical unexempted calls on untouched paths, or still matched as
  base-unexempted on a touched path, pass.

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
import ast
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

E2E_PREFIX = "tests/e2e/"
CHECKER_RELPATH = "scripts/check_e2e_wait_for_timeout.py"
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

# CLI/env revision tokens become git argv (list form, not shell). Accept only
# HEAD, hex SHAs, and simple ref names — no whitespace, ranges, or option-like
# values — so argparse/env input cannot inject extra git arguments (S8705).
_SAFE_GIT_REV_RE = re.compile(
    r"\A(?:"
    r"HEAD"
    r"|[0-9a-fA-F]{7,40}"
    r"|[A-Za-z0-9][A-Za-z0-9._/-]*"
    r")\Z"
)
_FULL_SHA_RE = re.compile(r"\A[0-9a-fA-F]{40}\Z")


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


def sanitize_git_revision(raw: str) -> str:
    """Return a charset-validated git revision token, or raise DiscoveryError.

    ``re.fullmatch`` + returning the match group is the sanitizer boundary for
    CLI/env input before it is placed on a git argv list.
    """
    value = (raw or "").strip()
    if not value:
        raise DiscoveryError("invalid --base: empty revision")
    if value.startswith("-"):
        raise DiscoveryError(
            f"invalid --base {value!r}: a revision cannot start with '-' "
            "(git would read it as an option)"
        )
    if ".." in value:
        raise DiscoveryError(
            f"invalid --base {value!r}: ranges are not allowed "
            "(pass a single revision)"
        )
    matched = _SAFE_GIT_REV_RE.fullmatch(value)
    if matched is None:
        raise DiscoveryError(
            f"invalid --base {value!r}: only HEAD, a hex SHA, or a simple "
            "ref name is accepted"
        )
    return matched.group(0)


def resolve_base(repo: Path, explicit: str | None) -> str:
    """Pick the comparison revision and resolve it to a 40-char commit SHA.

    Precedence: ``--base`` / argv, ``PRKS_E2E_WAIT_TIMEOUT_BASE``, then ``HEAD``
    (local dirty-tree check). CI for pull requests should pass the PR base ref
    or its already-resolved SHA. Subsequent git diffs use only the hex SHA.
    """
    if explicit:
        raw = explicit
    else:
        raw = (os.environ.get("PRKS_E2E_WAIT_TIMEOUT_BASE") or "").strip() or "HEAD"
    safe = sanitize_git_revision(raw)
    # List argv (not shell). Pass only the charset-validated token — never
    # concatenate CLI input into a peel expression (keeps S8705 clear).
    # Branch tips / HEAD / commit SHAs resolve directly to a commit object id.
    out = _git(
        repo,
        ["rev-parse", "--verify", "--end-of-options", safe],
        f"base revision check vs {safe}",
    )
    sha = (out.strip().splitlines() or [""])[0].strip()
    if _FULL_SHA_RE.fullmatch(sha) is None:
        raise DiscoveryError(
            f"base revision check vs {safe} failed: rev-parse did not return "
            f"a 40-character commit SHA (got {sha!r})"
        )
    return sha


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


def path_is_allowlisted(relpath: str, allowlist: frozenset[str] | None = None) -> bool:
    return relpath in (PATH_ALLOWLIST if allowlist is None else allowlist)


def _is_e2e_python(path: str | None) -> bool:
    return bool(
        path
        and path.startswith(E2E_PREFIX)
        and path.endswith(".py")
    )


def normalize_wait_line(line: str) -> str:
    """Strip trailing comments/whitespace so same-line marker edits still match."""
    return line.split("#", 1)[0].rstrip().strip()


def iter_wait_sites(lines: list[str]) -> list[tuple[int, str]]:
    """Return ``(1-based lineno, raw line)`` for each wait_for_timeout call."""
    return [
        (i, line)
        for i, line in enumerate(lines, start=1)
        if line_has_wait_for_timeout(line)
    ]


def parse_path_allowlist_from_source(source: str) -> frozenset[str]:
    """Extract ``PATH_ALLOWLIST`` string entries from checker source via AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return frozenset()
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
            value = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            t0 = node.targets[0]
            if isinstance(t0, ast.Name):
                target = t0.id
                value = node.value
        if target != "PATH_ALLOWLIST" or value is None:
            continue
        if not isinstance(value, ast.Call):
            return frozenset()
        if not isinstance(value.func, ast.Name) or value.func.id != "frozenset":
            return frozenset()
        if not value.args:
            return frozenset()
        arg0 = value.args[0]
        if not isinstance(arg0, (ast.Set, ast.Tuple, ast.List)):
            return frozenset()
        out: set[str] = set()
        for elt in arg0.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                out.add(elt.value)
        return frozenset(out)
    return frozenset()


def load_base_path_allowlist(repo: Path, base_sha: str) -> frozenset[str]:
    """PATH_ALLOWLIST as committed at ``base_sha``, or empty if the file is absent."""
    sha = sanitize_git_revision(base_sha)
    # ``git show`` with a missing path exits 128 — treat as empty allowlist.
    cmd = ["git", "-C", str(repo), "show", f"{sha}:{CHECKER_RELPATH}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise DiscoveryError(
            f"base allowlist load failed: could not run git show ({exc.__class__.__name__})"
        ) from exc
    if proc.returncode != 0:
        return frozenset()
    return parse_path_allowlist_from_source(proc.stdout or "")


def _safe_repo_relpath(relpath: str) -> str:
    """Reject path traversal before interpolating into ``git show`` pathspecs."""
    value = (relpath or "").strip().replace("\\", "/")
    if (
        not value
        or value.startswith("/")
        or value.startswith("../")
        or "/../" in value
        or value.endswith("/..")
        or "\0" in value
    ):
        raise DiscoveryError(f"unsafe repository relative path: {relpath!r}")
    return value


def read_file_at_revision(repo: Path, base_sha: str, relpath: str) -> list[str] | None:
    """Return lines of ``relpath`` at ``base_sha``, or None if it did not exist."""
    sha = sanitize_git_revision(base_sha)
    safe_path = _safe_repo_relpath(relpath)
    # sha is a validated 40-char hex; safe_path has no traversal segments.
    cmd = ["git", "-C", str(repo), "show", f"{sha}:{safe_path}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise DiscoveryError(
            f"read {safe_path} at {sha} failed: could not run git show "
            f"({exc.__class__.__name__})"
        ) from exc
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").splitlines()


def parse_unified_diff_added_waits(diff_text: str) -> list[tuple[str, int, str]]:
    """Return ``(path, new_lineno, line_text)`` for added wait_for_timeout lines.

    Kept for unit tests and as a documentation of the added-line shape; the
    main collector uses base/current site matching instead.
    """
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


def list_changed_e2e_python(repo: Path, base_sha: str) -> list[str]:
    """E2E ``*.py`` paths changed vs ``base_sha`` (name-only) plus untracked."""
    sha = sanitize_git_revision(base_sha)
    raw = _git(
        repo,
        [
            "diff",
            "--name-only",
            "--diff-filter=ACMR",
            "--end-of-options",
            sha,
            "--",
            E2E_PREFIX,
        ],
        f"E2E changed-path discovery vs {sha}",
    )
    paths: list[str] = []
    for line in raw.splitlines():
        path = line.strip()
        if _is_e2e_python(path) and path not in paths:
            paths.append(path)
    for path in list_untracked_e2e_python(repo):
        if path not in paths:
            paths.append(path)
    return paths


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


def _base_exempt_counters(
    base_lines: list[str] | None,
    *,
    base_allowlisted: bool,
) -> tuple[Counter[str], Counter[str]]:
    """Return ``(unexempted, exempted)`` multisets of normalized wait lines."""
    unexempted: Counter[str] = Counter()
    exempted: Counter[str] = Counter()
    if not base_lines:
        return unexempted, exempted
    for lineno, line in iter_wait_sites(base_lines):
        key = normalize_wait_line(line)
        if base_allowlisted or line_is_exempt(base_lines, lineno):
            exempted[key] += 1
        else:
            unexempted[key] += 1
    return unexempted, exempted


def collect_findings(
    repo: Path,
    base_sha: str,
    *,
    path_allowlist: frozenset[str] | None = None,
    base_path_allowlist: frozenset[str] | None = None,
) -> list[Finding]:
    """Return violations for new or newly-unexempted ``wait_for_timeout`` sites.

    ``base_sha`` must be a 40-character commit SHA from ``resolve_base``.
    Optional allowlist kwargs override module/base defaults (tests).
    """
    sha = sanitize_git_revision(base_sha)
    if _FULL_SHA_RE.fullmatch(sha) is None:
        raise DiscoveryError(
            f"collect_findings requires a 40-character commit SHA (got {sha!r})"
        )

    current_allow = (
        PATH_ALLOWLIST if path_allowlist is None else frozenset(path_allowlist)
    )
    if base_path_allowlist is None:
        base_allow = load_base_path_allowlist(repo, sha)
    else:
        base_allow = frozenset(base_path_allowlist)

    removed_allow = base_allow - current_allow
    paths: list[str] = list(list_changed_e2e_python(repo, sha))
    for rel in sorted(removed_allow):
        if _is_e2e_python(rel) and rel not in paths:
            paths.append(rel)

    findings: list[Finding] = []
    for rel in paths:
        abs_path = repo / rel
        if abs_path.is_file():
            current_lines = abs_path.read_text(encoding="utf-8").splitlines()
        else:
            # Deleted in the working tree — nothing to enforce on the tip.
            continue
        base_lines = read_file_at_revision(repo, sha, rel)
        unexempted, exempted = _base_exempt_counters(
            base_lines,
            base_allowlisted=rel in base_allow,
        )
        currently_allowlisted = rel in current_allow

        for lineno, line in iter_wait_sites(current_lines):
            if currently_allowlisted or line_is_exempt(current_lines, lineno):
                continue
            key = normalize_wait_line(line)
            if unexempted[key] > 0:
                unexempted[key] -= 1
                continue  # historical debt still unmatched
            if exempted[key] > 0:
                exempted[key] -= 1
                findings.append(
                    Finding(
                        path=rel,
                        line=lineno,
                        snippet=line,
                        reason=(
                            "page.wait_for_timeout(...) lost its approved "
                            "exemption (marker or PATH_ALLOWLIST) while the "
                            "call remains"
                        ),
                    )
                )
                continue
            findings.append(
                Finding(
                    path=rel,
                    line=lineno,
                    snippet=line,
                    reason=(
                        "new page.wait_for_timeout(...) under tests/e2e/ without "
                        "an approved exemption"
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
