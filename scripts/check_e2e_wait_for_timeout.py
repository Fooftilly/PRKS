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

Call detection uses ``ast.Call`` (so multiline / backslash-continued calls are
found; string literals are not). Exemption markers are accepted only from
Python ``COMMENT`` tokens.

Exemptions (narrow):

1. Adjacent marker with a non-empty reason in a comment on the same line or the
   previous non-blank line::

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
import io
import os
import re
import subprocess
import sys
import tokenize
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

# Well-known empty tree — used when a push has no predecessor SHA so the full
# tip is inspected once (greenfield / new-ref) rather than diffing HEAD^HEAD.
EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d6927f8d2765b5"

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
            f"  exemption: adjacent `{MARKER} <reason>` comment on the same or "
            f"previous non-blank line, or a reviewed PATH_ALLOWLIST entry in "
            f"scripts/check_e2e_wait_for_timeout.py."
        )


@dataclass(frozen=True)
class WaitSite:
    lineno: int
    snippet: str
    key: str


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
    (local dirty-tree check). CI for pull requests should pass
    ``github.event.pull_request.base.sha``. The empty-tree SHA is accepted as a
    synthetic baseline for greenfield pushes.
    """
    if explicit:
        raw = explicit
    else:
        raw = (os.environ.get("PRKS_E2E_WAIT_TIMEOUT_BASE") or "").strip() or "HEAD"
    safe = sanitize_git_revision(raw)
    if safe == EMPTY_TREE_SHA:
        return EMPTY_TREE_SHA
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


def iter_wait_sites(source: str) -> list[WaitSite]:
    """AST ``.wait_for_timeout(...)`` call sites (ignores strings/comments)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines()
    sites: list[WaitSite] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "wait_for_timeout":
            continue
        lineno = int(node.lineno)
        end = int(getattr(node, "end_lineno", None) or lineno)
        snippet = "\n".join(lines[lineno - 1 : end])
        try:
            key = ast.unparse(node)
        except Exception:
            key = snippet.strip()
        sites.append(WaitSite(lineno=lineno, snippet=snippet, key=key))
    sites.sort(key=lambda s: s.lineno)
    return sites


def iter_wait_sites_from_lines(lines: list[str]) -> list[WaitSite]:
    return iter_wait_sites("\n".join(lines) + ("\n" if lines else ""))


def _marker_reason_from_comment(comment_text: str) -> str | None:
    """Parse ``MARKER`` + reason from a tokenize COMMENT string (includes ``#``)."""
    idx = comment_text.find(MARKER)
    if idx < 0:
        return None
    reason = comment_text[idx + len(MARKER) :].strip()
    return reason or None


def comment_marker_reason(line: str) -> str | None:
    """Return exemption reason only when MARKER appears in a COMMENT token."""
    if MARKER not in line:
        return None
    try:
        tokens = tokenize.generate_tokens(io.StringIO(line).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                reason = _marker_reason_from_comment(tok.string)
                if reason is not None:
                    return reason
    except tokenize.TokenError:
        return None
    return None


def line_is_exempt(lines: list[str], lineno_1based: int) -> bool:
    """True when a COMMENT marker with reason is on the call or previous non-blank line."""
    if lineno_1based < 1 or lineno_1based > len(lines):
        return False
    if comment_marker_reason(lines[lineno_1based - 1]) is not None:
        return True
    for i in range(lineno_1based - 2, -1, -1):
        prev = lines[i]
        if not prev.strip():
            continue
        return comment_marker_reason(prev) is not None
    return False


def path_is_allowlisted(relpath: str, allowlist: frozenset[str] | None = None) -> bool:
    return relpath in (PATH_ALLOWLIST if allowlist is None else allowlist)


def _is_e2e_python(path: str | None) -> bool:
    return bool(
        path
        and path.startswith(E2E_PREFIX)
        and path.endswith(".py")
    )


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
    if sha == EMPTY_TREE_SHA:
        return frozenset()
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
    if sha == EMPTY_TREE_SHA:
        return None
    safe_path = _safe_repo_relpath(relpath)
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


def paths_from_name_status_z(raw: str) -> list[tuple[str, str | None]]:
    """Parse ``git diff --name-status -z`` into ``(path, rename_source_or_None)``.

    For renames/copies, ``path`` is the post-image and ``rename_source`` is the
    pre-image. Other statuses yield ``(path, None)``.
    """
    if not raw:
        return []
    parts = raw.split("\0")
    out: list[tuple[str, str | None]] = []
    i = 0
    n = len(parts)
    while i < n:
        status = parts[i]
        i += 1
        if not status:
            continue
        if i >= n:
            break
        first = parts[i]
        i += 1
        if not first:
            continue
        if status[0] in ("R", "C"):
            if i >= n:
                break
            second = parts[i]
            i += 1
            if second:
                out.append((second, first))
        else:
            out.append((first, None))
    return out


@dataclass(frozen=True)
class ChangedE2EPath:
    path: str
    """Path to read at base for historical matching (None ⇒ treat as brand-new)."""
    base_read_path: str | None


def list_changed_e2e_python(repo: Path, base_sha: str) -> list[ChangedE2EPath]:
    """E2E ``*.py`` changes vs ``base_sha``, with rename source tracking."""
    sha = sanitize_git_revision(base_sha)
    if sha == EMPTY_TREE_SHA:
        # Greenfield: every tracked E2E module is "new" against the empty tree.
        raw = _git(
            repo,
            ["ls-files", "--", E2E_PREFIX],
            "E2E tracked-path discovery vs empty tree",
        )
        paths = []
        for line in raw.splitlines():
            path = line.strip()
            if _is_e2e_python(path):
                paths.append(ChangedE2EPath(path=path, base_read_path=None))
    else:
        raw = _git(
            repo,
            [
                "diff",
                "--name-status",
                "-z",
                "--diff-filter=ACMR",
                "--end-of-options",
                sha,
                "--",
                E2E_PREFIX,
            ],
            f"E2E changed-path discovery vs {sha}",
        )
        # Also include renames *into* tests/e2e from outside (pre-image is not
        # under the pathspec, so name-status scoped to E2E_PREFIX can miss them).
        raw_all = _git(
            repo,
            [
                "diff",
                "--name-status",
                "-z",
                "--diff-filter=ACMR",
                "--end-of-options",
                sha,
                "--",
            ],
            f"full changed-path discovery vs {sha}",
        )
        by_path: dict[str, ChangedE2EPath] = {}
        for path, source in paths_from_name_status_z(raw):
            if not _is_e2e_python(path):
                continue
            by_path[path] = ChangedE2EPath(path=path, base_read_path=path)
        for path, source in paths_from_name_status_z(raw_all):
            if not _is_e2e_python(path):
                continue
            if source is None:
                by_path.setdefault(
                    path, ChangedE2EPath(path=path, base_read_path=path)
                )
                continue
            if _is_e2e_python(source):
                # Rename within E2E: match historical sites from the old path.
                by_path[path] = ChangedE2EPath(path=path, base_read_path=source)
            else:
                # Rename/copy into E2E from outside: entirely new to the policy.
                by_path[path] = ChangedE2EPath(path=path, base_read_path=None)
        paths = list(by_path.values())

    seen = {c.path for c in paths}
    for path in list_untracked_e2e_python(repo):
        if path not in seen:
            paths.append(ChangedE2EPath(path=path, base_read_path=None))
            seen.add(path)
    paths.sort(key=lambda c: c.path)
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
    """Return ``(unexempted, exempted)`` multisets of wait-call keys."""
    unexempted: Counter[str] = Counter()
    exempted: Counter[str] = Counter()
    if not base_lines:
        return unexempted, exempted
    for site in iter_wait_sites_from_lines(base_lines):
        if base_allowlisted or line_is_exempt(base_lines, site.lineno):
            exempted[site.key] += 1
        else:
            unexempted[site.key] += 1
    return unexempted, exempted


def collect_findings(
    repo: Path,
    base_sha: str,
    *,
    path_allowlist: frozenset[str] | None = None,
    base_path_allowlist: frozenset[str] | None = None,
) -> list[Finding]:
    """Return violations for new or newly-unexempted ``wait_for_timeout`` sites.

    ``base_sha`` must be a 40-character commit SHA from ``resolve_base`` (or the
    empty-tree SHA). Optional allowlist kwargs override module/base defaults.
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
    changed = list(list_changed_e2e_python(repo, sha))
    by_path = {c.path: c for c in changed}
    for rel in sorted(removed_allow):
        if _is_e2e_python(rel) and rel not in by_path:
            by_path[rel] = ChangedE2EPath(path=rel, base_read_path=rel)

    findings: list[Finding] = []
    for rel, changed_path in sorted(by_path.items()):
        abs_path = repo / rel
        if abs_path.is_file():
            current_text = abs_path.read_text(encoding="utf-8")
            current_lines = current_text.splitlines()
        else:
            continue
        if changed_path.base_read_path is None:
            base_lines = None
        else:
            base_lines = read_file_at_revision(
                repo, sha, changed_path.base_read_path
            )
        unexempted, exempted = _base_exempt_counters(
            base_lines,
            base_allowlisted=rel in base_allow,
        )
        currently_allowlisted = rel in current_allow

        for site in iter_wait_sites(current_text):
            if currently_allowlisted or line_is_exempt(current_lines, site.lineno):
                continue
            key = site.key
            if unexempted[key] > 0:
                unexempted[key] -= 1
                continue
            if exempted[key] > 0:
                exempted[key] -= 1
                findings.append(
                    Finding(
                        path=rel,
                        line=site.lineno,
                        snippet=site.snippet,
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
                    line=site.lineno,
                    snippet=site.snippet,
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
            "or HEAD). Pull-request CI should pass github.event.pull_request.base.sha."
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
