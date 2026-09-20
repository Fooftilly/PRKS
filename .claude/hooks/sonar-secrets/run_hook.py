#!/usr/bin/env python3
"""Portable Claude Code secrets-scan hook launcher.

Invokes ``sonar hook <name>`` when the SonarQube CLI is on PATH; otherwise
exits 0 so contributors without Sonar are not blocked.

Uses Python 3 only — already required by PRKS — so the shared hooks do not
introduce Node or a shell-specific wrapper.

Usage (Claude settings exec form)::

    python3 …/run_hook.py claude-pre-tool-use
    python3 …/run_hook.py claude-prompt-submit
"""

from __future__ import annotations

import shutil
import subprocess
import sys

ALLOWED = frozenset({"claude-pre-tool-use", "claude-prompt-submit"})


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in ALLOWED:
        return 0
    if shutil.which("sonar") is None:
        return 0
    completed = subprocess.run(["sonar", "hook", argv[1]], check=False)
    return int(completed.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
