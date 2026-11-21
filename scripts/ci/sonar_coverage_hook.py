"""Install or verify the coverage.py start-up hook for the SonarCloud workflow.

The SonarCloud workflow (#1373) measures the unchanged ``python run_tests.py``
and ``scripts/test-postgres`` commands. Instead of wrapping them, it writes a
one-line ``.pth`` file into the active interpreter's site-packages, so every
Python process started with ``COVERAGE_PROCESS_START`` set records coverage
(the documented coverage.py subprocess mechanism). The hook does nothing when
that variable is unset. Never run this outside a disposable CI runner.
"""
from __future__ import annotations

import argparse
import sysconfig
from pathlib import Path

HOOK_NAME = "prks-coverage.pth"
HOOK_LINE = "import coverage; coverage.process_startup()\n"


def hook_path() -> Path:
    return Path(sysconfig.get_paths()["purelib"]) / HOOK_NAME


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "check"))
    args = parser.parse_args(argv)
    path = hook_path()
    if args.action == "install":
        path.write_text(HOOK_LINE, encoding="utf-8")
        print(f"Installed {path}")
        return 0
    ok = path.is_file() and path.read_text(encoding="utf-8") == HOOK_LINE
    print(f"{path}: {'ok' if ok else 'missing'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
