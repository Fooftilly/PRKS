"""Select expensive workflow lint only for relevant GitHub Actions changes.

The job itself runs on every pull request so it can safely become a required
status check. GitHub checks out the synthetic two-parent PR merge commit; its
first parent is the target branch. Missing/inaccessible history fails OPEN
for linting (never silently skips lint).
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - only invokes fixed git subcommands without a shell
from pathlib import Path

WATCHED_PATHS = (".github/workflows/", "scripts/ci_workflow_lint_scope.py")


def should_lint(event: str, root: Path) -> bool:
    """True if lint must execute; non-PR events and uncertain diffs lint."""
    if event != "pull_request":
        return True

    parents = subprocess.run(  # nosec B603,B607 - fixed git argv, trusted runner PATH
        ["git", "rev-list", "--parents", "-n", "1", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    # Only compare against the first parent of GitHub's synthetic PR merge.
    # A detached PR head, shallow/missing history or unexpected topology must
    # not accidentally skip workflow lint.
    if parents.returncode or len(parents.stdout.split()) != 3:
        return True

    changed = subprocess.run(  # nosec B603,B607 - fixed git argv, trusted runner PATH
        ["git", "diff", "--quiet", "HEAD^1", "HEAD", "--", *WATCHED_PATHS],
        cwd=root,
        capture_output=True,
        check=False,
    )
    # 0 = no relevant changes, 1 = changed; other errors fail open for lint.
    return changed.returncode != 0


if __name__ == "__main__":
    print("true" if should_lint(os.environ.get("PRKS_EVENT_NAME", ""), Path.cwd()) else "false")
