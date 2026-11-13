"""Conservative docs-only PR scope for required CI checks (#643).

Only A/M changes to an explicit audited subset of Wiki Markdown qualify. Missing/invalid baselines,
renames, deletes, workflow or test changes, and unreadable diffs fail closed to
ordinary full validation across the required CI workflows.

``--scope postgres`` is a wider, separate scope used only by the PostgreSQL
helper and backend parity jobs in the Test Gate. Those suites never read
repository Markdown (tests/pg, the parity modules and scripts/test-postgres
load Python, SQL and JSON fixtures only; the migration loader takes ``*.sql``
only), so A/M changes to any Markdown file cannot change their outcome. The
unit/API job, which does read Markdown as fixtures, keeps the narrow default.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

# Only these four wiki pages have been audited as documentation-only. The
# Getting Started / Offline and Sync pages are not read as source or test
# fixtures; their relative links are covered by the docs validator.
# README, licensing/agent guidance and implementation architecture documents
# are read as fixtures by tests and must continue to run full validation.
AUDITED_DOC_FILES = frozenset({
    "docs/wiki/Getting-Started.md",
    "docs/wiki/Glossary.md",
    "docs/wiki/Offline-and-Sync.md",
    "docs/wiki/Troubleshooting.md",
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
    if (
        not _is_plain_relative(path)
        or path.rsplit("/", 1)[-1].lower() == "agents.md"
    ):
        return False
    return path in AUDITED_DOC_FILES


def is_postgres_irrelevant_markdown(path: str) -> bool:
    """Any plain Markdown file: the PostgreSQL suites never read one."""
    return _is_plain_relative(path) and path.endswith(".md")


def changed_documentation(
    root: Path, base: str, scope: str = DEFAULT_SCOPE
) -> tuple[str, ...] | None:
    """Return changed document paths iff this is a proven docs-only PR.

    The git diff is anchored to the immutable PR event base SHA. A merge
    checkout may be HEAD; comparing event base to HEAD still includes the PR.
    """
    if scope == DEFAULT_SCOPE:
        approved = is_approved_markdown
    elif scope == POSTGRES_SCOPE:
        approved = is_postgres_irrelevant_markdown
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--scope", choices=SCOPES, default=DEFAULT_SCOPE)
    args = parser.parse_args()
    paths = changed_documentation(Path.cwd(), args.base, args.scope)
    print("true" if paths is not None else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
