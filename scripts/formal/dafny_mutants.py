"""Run the Dafny pilot's mutants (#1472). Each MUST fail verification.

Each mutant is one exact text edit of a model file. An edit that does not
match exactly once is an error, so a model change cannot silently disarm it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 - runs the pinned Dafny binary on a temp copy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "formal" / "dafny"
ENTRY = "Sequence.dfy"


def verify(dafny: str, directory: Path) -> subprocess.CompletedProcess[str]:
    args = [dafny, "verify", "--allow-warnings", "--resource-limit", os.environ.get("PRKS_DAFNY_RLIMIT", "20000"),
            str(directory / ENTRY)]
    return subprocess.run(args, capture_output=True, text=True, timeout=600, check=False)  # nosec B603


def main() -> int:
    dafny = os.environ.get("DAFNY", "dafny")
    mutants = json.loads((MODEL / "mutants.json").read_text(encoding="utf-8"))
    survivors: list[str] = []
    for mutant in mutants:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for path in MODEL.glob("*.dfy"):
                shutil.copy(path, directory / path.name)
            target = directory / mutant["file"]
            text = target.read_text(encoding="utf-8")
            if text.count(mutant["from"]) != 1:
                print(f"ERROR {mutant['name']}: edit does not match exactly once", file=sys.stderr)
                return 2
            target.write_text(text.replace(mutant["from"], mutant["to"]), encoding="utf-8")
            result = verify(dafny, directory)
            killed = result.returncode != 0
            print(f"{'killed  ' if killed else 'SURVIVED'} [{mutant['group']}] {mutant['name']}")
            if not killed:
                survivors.append(mutant["name"])
    print(f"{len(mutants) - len(survivors)} of {len(mutants)} mutants killed")
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
