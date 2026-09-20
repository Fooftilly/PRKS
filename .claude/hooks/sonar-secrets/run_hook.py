#!/usr/bin/env python3
"""Run an optional Sonar secrets hook for Claude Code.

Invokes ``sonar hook <name>`` when the SonarQube CLI is on PATH; otherwise
exits 0. Platform-specific launchers (``run_hook.sh`` / ``run_hook.ps1``)
locate a Python 3 interpreter using the same preference order as PRKS
(``python3`` on POSIX, ``python`` / ``py -3`` on Windows).

A short project-scoped dedupe window prevents a double scan when Claude
registers both the bash and PowerShell launchers on Windows.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ALLOWED = frozenset({"claude-pre-tool-use", "claude-prompt-submit"})
_DEDUPE_SECONDS = 5.0


def _dedupe_path(hook: str) -> Path:
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    key = abs(hash((os.path.abspath(root), hook))) % (10**12)
    return Path(tempfile.gettempdir()) / f"prks-claude-sonar-hook-{key}.stamp"


def _already_ran(hook: str) -> bool:
    path = _dedupe_path(hook)
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age < _DEDUPE_SECONDS


def _mark_ran(hook: str) -> None:
    path = _dedupe_path(hook)
    try:
        path.write_text(str(time.time()), encoding="ascii")
    except OSError:
        pass


def main(argv: list[str]) -> int:
    """Entry point: validate hook name, soft-no-op without sonar, else run it."""
    if len(argv) < 2 or argv[1] not in ALLOWED:
        return 0
    hook = argv[1]
    if shutil.which("sonar") is None:
        return 0
    if _already_ran(hook):
        return 0
    _mark_ran(hook)
    completed = subprocess.run(["sonar", "hook", hook], check=False)
    return int(completed.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
