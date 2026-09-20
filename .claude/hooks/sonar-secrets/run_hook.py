#!/usr/bin/env python3
"""Run an optional Sonar secrets hook for Claude Code.

Invokes ``sonar hook <name>`` when the SonarQube CLI is on PATH; otherwise
exits 0. Platform-specific launchers (``run_hook.sh`` / ``run_hook.ps1``)
locate a Python 3 interpreter using the same preference order as PRKS
(``python3`` on POSIX, ``python`` / ``py -3`` on Windows).

Claude may run both launchers in parallel for one event. This script reads
stdin once, claims an atomic request-scoped lock derived from a stable digest
of (project, hook, payload), and only the winner forwards that same payload
to ``sonar hook``.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ALLOWED = frozenset({"claude-pre-tool-use", "claude-prompt-submit"})
_STALE_LOCK_SECONDS = 120.0


def _project_root() -> str:
    return os.path.abspath(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def _request_digest(hook: str, payload: bytes) -> str:
    """Stable key: not Python's per-process ``hash()``."""
    root = _project_root().encode("utf-8", errors="surrogateescape")
    hook_b = hook.encode("utf-8")
    return hashlib.sha256(root + b"\0" + hook_b + b"\0" + payload).hexdigest()


def _lock_path(digest: str) -> Path:
    return Path(tempfile.gettempdir()) / f"prks-claude-sonar-hook-{digest}.lock"


def _try_claim(digest: str) -> bool:
    """Atomically claim this request. Only the winning process continues."""
    path = _lock_path(digest)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(path), flags)
    except FileExistsError:
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return False
        if age < _STALE_LOCK_SECONDS:
            return False
        try:
            path.unlink()
        except OSError:
            return False
        try:
            fd = os.open(str(path), flags)
        except FileExistsError:
            return False
    try:
        os.write(fd, str(os.getpid()).encode("ascii", errors="replace"))
    finally:
        os.close(fd)
    return True


def main(argv: list[str]) -> int:
    """Validate hook name, claim the request, soft-no-op or run sonar."""
    if len(argv) < 2 or argv[1] not in ALLOWED:
        return 0
    hook = argv[1]
    payload = sys.stdin.buffer.read()
    if shutil.which("sonar") is None:
        return 0
    digest = _request_digest(hook, payload)
    if not _try_claim(digest):
        return 0
    completed = subprocess.run(
        ["sonar", "hook", hook],
        input=payload,
        check=False,
    )
    return int(completed.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
