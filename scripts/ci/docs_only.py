"""Conservative docs-only PR scope for required CI checks (#643).

Only A/M changes to an explicit audited set of documentation Markdown, or to an
explicit audited set of external analysis-tool config files, qualify. Missing/invalid baselines,
renames, deletes, workflow or test changes, and unreadable diffs fail closed to
ordinary full validation across the required CI workflows.

``--scope postgres`` is a wider, separate scope used only by the PostgreSQL
helper and backend parity jobs in the Test Gate. Those suites never read
repository Markdown (tests/pg, the parity modules and scripts/test-postgres
load Python, SQL and JSON fixtures only; the migration loader takes ``*.sql``
only), so A/M changes to any Markdown file cannot change their outcome. The
unit/API job, which does read Markdown as fixtures, keeps the narrow default.

**Stale event base.** ``pull_request.base.sha`` is the base tip when the PR
was last updated, but GitHub builds the ``refs/pull/N/merge`` checkout on the
current base tip. When master moved in between, ``base..HEAD`` also shows
master's newer commits, and a Markdown-only PR ran full CI. So when HEAD is
GitHub's merge of the PR head (two parents, the second being the event's
``pull_request.head.sha`` from ``GITHUB_EVENT_PATH``), the diff is anchored
to HEAD's first parent, the base the PR was actually merged onto, fetched if
the shallow checkout lacks it. Any other shape keeps the event base. The
Markdown validator (``check_docs_only``) resolves the same base through
:func:`effective_base`, so it never refuses a PR this classifier approved.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

# Documentation pages audited as documentation-only: no source file, test,
# script or workflow reads them (tests/test_ci_docs_only.py enforces this by
# name), so a change cannot alter any suite's outcome. Their relative links,
# anchors and fences are checked by the docs validator instead. README,
# licensing/agent guidance, agent rules and every docs page that a test reads
# as a fixture are absent and keep full validation; so are new pages.
AUDITED_DOC_FILES = frozenset({
    "docs/agent-workflows/agent-queue.md",
    "docs/agent-workflows/github-discussions.md",
    "docs/agent-workflows/project3-assignment-dispatch.md",
    "docs/agent-workflows/project3-assignment-request.md",
    "docs/agent-workflows/project3-triage.md",
    "docs/b5-browser-baselines/comparison-1033.md",
    "docs/classic-script-distribution.md",
    "docs/e2e-performance.md",
    "docs/job-worker.md",
    "docs/mutation-coordinator-stall-design.md",
    "docs/research/pragmatic-dnd-234.md",
    "docs/storage-architecture.md",
    "docs/wiki/Architecture.md",
    "docs/wiki/Configuration-and-Operations.md",
    "docs/wiki/Dependencies-and-Vendoring.md",
    "docs/wiki/Developer-Reference.md",
    "docs/wiki/Development-Workflow.md",
    "docs/wiki/Domain-Model.md",
    "docs/wiki/Getting-Started.md",
    "docs/wiki/Glossary.md",
    "docs/wiki/Offline-and-Sync.md",
    "docs/wiki/PDFs-and-Annotations.md",
    "docs/wiki/Security-and-Operations.md",
    "docs/wiki/Storage-Backup-and-Restore.md",
    "docs/wiki/Troubleshooting.md",
    "docs/wiki/_Sidebar.md",
})
# Root config files read only by external services (Codacy, PR-Agent,
# CodeRabbit). Audited: no test, script, runtime module or build step reads
# them, so a change cannot alter any CI suite's outcome. Mergify and Greptile
# configs (.mergify.yml, .greptile/*) are deliberately absent: they are not audited.
AUDITED_CONFIG_FILES = frozenset({
    ".codacy.yml",
    ".coderabbit.yaml",
    ".pr_agent.toml",
})
DEFAULT_SCOPE = "docs"
POSTGRES_SCOPE = "postgres"
SCOPES = (DEFAULT_SCOPE, POSTGRES_SCOPE)
_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")


def _is_plain_relative(path: str) -> bool:
    return bool(path) and not (
        path.startswith("/")
        or "\\" in path
        or any(part in (".", "..", "") for part in path.split("/"))
    )


def is_approved_markdown(path: str) -> bool:
    """An exact audited page; the set holds only plain paths and no AGENTS.md (tested)."""
    return path in AUDITED_DOC_FILES


def is_audited_config(path: str) -> bool:
    """An external analysis-tool config file that no CI suite reads."""
    return path in AUDITED_CONFIG_FILES


def is_postgres_irrelevant_markdown(path: str) -> bool:
    """Any plain Markdown file: the PostgreSQL suites never read one."""
    return _is_plain_relative(path) and path.endswith(".md")


def _approved_docs(path: str) -> bool:
    return is_approved_markdown(path) or is_audited_config(path)


def _approved_postgres(path: str) -> bool:
    return is_postgres_irrelevant_markdown(path) or is_audited_config(path)


def changed_documentation(
    root: Path, base: str, scope: str = DEFAULT_SCOPE
) -> tuple[str, ...] | None:
    """Return changed document paths iff this is a proven docs-only PR.

    The git diff is anchored to the immutable PR event base SHA. A merge
    checkout may be HEAD; comparing event base to HEAD still includes the PR.
    """
    if scope == DEFAULT_SCOPE:
        approved = _approved_docs
    elif scope == POSTGRES_SCOPE:
        approved = _approved_postgres
    else:
        return None
    if not _SHA.fullmatch(base):
        return None
    try:
        result = subprocess.run(  # nosec B603,B607 - fixed git argv, validated SHA
            ["git", "diff", "--no-ext-diff", "--name-status", "-z", base, "HEAD", "--"],
            cwd=root,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode or not result.stdout or not result.stdout.endswith(b"\0"):
        return None
    fields = result.stdout[:-1].split(b"\0")
    if len(fields) % 2:
        return None
    paths = []
    for index in range(0, len(fields), 2):
        # Only simple Added/Modified entries. Any R/C/D/T/U/other changes
        # are deliberately full-CI, even if their destination is Markdown.
        status = fields[index]
        if status not in (b"A", b"M"):
            return None
        path = os.fsdecode(fields[index + 1])
        if not approved(path):
            return None
        paths.append(path)
    return tuple(paths) if paths else None


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(  # nosec B603,B607 - fixed git argv, validated SHAs
            ["git", *args], cwd=root, capture_output=True, check=False)
    except OSError:
        return None


def _event_head_sha() -> str | None:
    """``pull_request.head.sha`` of the Actions event, or None outside a pull_request run."""
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            event = json.load(handle)
        head = event["pull_request"]["head"]["sha"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return head if isinstance(head, str) and _SHA.fullmatch(head) else None


def merge_base_parent(root: Path, base: str, head_sha: str | None) -> str:
    """The base to diff against: HEAD's first parent when HEAD is GitHub's merge of ``head_sha``.

    Falls back to ``base`` for any other HEAD, and when that parent cannot
    be read or fetched.
    """
    if head_sha is None or not _SHA.fullmatch(head_sha):
        return base
    shown = _git(root, "cat-file", "-p", "HEAD")
    if shown is None or shown.returncode:
        return base
    parents = [line.split()[1].decode("ascii", "replace")
               for line in shown.stdout.splitlines() if line.startswith(b"parent ")]
    if len(parents) != 2 or parents[1].lower() != head_sha.lower() or not _SHA.fullmatch(parents[0]):
        return base
    first = parents[0]
    present = _git(root, "cat-file", "-e", f"{first}^{{commit}}")
    if present is None or present.returncode:
        fetched = _git(root, "fetch", "--no-tags", "--quiet", "--depth=1", "origin", first)
        if fetched is None or fetched.returncode:
            return base
    return first


def effective_base(root: Path, base: str) -> str:
    """The base both entrypoints diff against: :func:`merge_base_parent` for the Actions event."""
    return merge_base_parent(root, base, _event_head_sha()) if _SHA.fullmatch(base) else base


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--scope", choices=SCOPES, default=DEFAULT_SCOPE)
    args = parser.parse_args()
    root = Path.cwd()
    paths = changed_documentation(root, effective_base(root, args.base), args.scope)
    print("true" if paths is not None else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
