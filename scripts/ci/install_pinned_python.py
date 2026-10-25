#!/usr/bin/env python3
"""Install CI Python dependencies from the project's exact requirements pins.

Never install unpinned names or interpolate untrusted strings into a shell.
Each CI profile selects a reviewed subset of requirements-dev.txt alongside
all runtime requirements from requirements.txt.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.dependency_gate import read_requirements_pins  # noqa: E402

# Workflows select explicit, purpose-limited dev requirements.
DEV_PROFILES: dict[str, tuple[str, ...]] = {
    "runtime": (),
    "unit": ("openapi-core", "hypothesis", "markdown-it-py", "mdurl"),
    "docs": ("markdown-it-py", "mdurl"),
    "plan": ("playwright",),
    "e2e": ("playwright",),
    "postgres": ("psycopg", "psycopg-binary", "psycopg-pool", "argon2-cffi"),
}


def pins_for_profile(root: Path, profile: str) -> list[str]:
    """Return exact pins from authoritative files, failing on missing extras."""
    if profile not in DEV_PROFILES:
        raise ValueError(f"unknown install profile: {profile}")
    runtime = read_requirements_pins(root / "requirements.txt")
    dev = read_requirements_pins(root / "requirements-dev.txt")
    selected = dict(runtime) if profile not in ("plan", "docs") else {}
    for name in DEV_PROFILES[profile]:
        if name not in dev:
            raise ValueError(f"missing required development pin: {name}")
        if name in selected:
            raise ValueError(f"duplicate runtime/development pin: {name}")
        selected[name] = dev[name]
    if not selected:
        raise ValueError("empty CI dependency selection")
    return [f"{name}=={version}" for name, version in selected.items()]


def install(root: Path, profile: str) -> None:
    pins = pins_for_profile(root, profile)
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--isolated",
        "--disable-pip-version-check",
        "--only-binary=:all:",
        "--no-input",
        *pins,
    ]
    print("CI Python dependencies:", ", ".join(pins), flush=True)
    subprocess.run(cmd, cwd=root, check=True)  # noqa: S603 - fixed executable and exact pins


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=sorted(DEV_PROFILES))
    args = parser.parse_args(argv)
    install(ROOT, args.profile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
