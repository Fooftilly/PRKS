#!/usr/bin/env python3
"""Regenerate requirements-lock.txt, the hashed lock the Docker image installs.

requirements.txt stays the authoritative list of direct runtime pins. This
script resolves it (direct and transitive packages) for CPython 3.12 on every
platform and records each distribution's sha256, so the image build runs
``pip install --require-hashes`` and two builds get byte-identical wheels.

Needs ``uv`` on PATH and network access to PyPI. The dependency gate
(``validate_runtime_lock``) fails when the lock and requirements.txt disagree.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = "requirements-lock.txt"
COMMAND = "python scripts/lock_runtime_requirements.py"


def uv_command(uv: str) -> list[str]:
    return [
        uv, "pip", "compile", "requirements.txt",
        "--generate-hashes",
        "--universal",
        "--python-version", "3.12",
        "--no-annotate",
        "--custom-compile-command", COMMAND,
        "--output-file", LOCK,
    ]


def main() -> int:
    uv = shutil.which("uv")
    if uv is None:
        print("uv is required: https://docs.astral.sh/uv/getting-started/installation/", file=sys.stderr)
        return 2
    subprocess.run(uv_command(uv), cwd=ROOT, check=True)  # noqa: S603 - fixed argv
    print(f"Wrote {LOCK}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
