"""Run the Dafny pilot's mutants (#1472). Each MUST fail verification.

Each mutant is one exact text edit of a model file. An edit that does not
match exactly once is an error, so a model change cannot silently disarm it.
A mutant is killed only by a proof failure. A run out of resource, timed out
or stopped before verification is inconclusive and fails the harness.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404 - runs the pinned Dafny binary on a temp copy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "formal" / "dafny"
FILES = ("Reconcile.dfy", "Sequence.dfy")
VERIFICATION_ERROR = 4
SUMMARY = re.compile(
    r"^Dafny program verifier finished with \d+ verified, (\d+) errors?(.*)$", re.M
)


def verify(dafny: str, directory: Path) -> subprocess.CompletedProcess[str]:
    args = [
        dafny,
        "verify",
        "--allow-warnings",
        "--resource-limit",
        os.environ.get("PRKS_DAFNY_RLIMIT", "10000000"),
        *(str(directory / name) for name in FILES),
    ]
    return subprocess.run(
        args, capture_output=True, text=True, timeout=600, check=False
    )  # nosec B603


def classify(returncode: int, output: str) -> str:
    """Return "survived", "killed" or "inconclusive"."""
    if returncode == 0:
        return "survived"
    found = SUMMARY.search(output)
    if returncode != VERIFICATION_ERROR or found is None:
        return "inconclusive"
    if int(found.group(1)) == 0 or found.group(2).strip():
        return "inconclusive"
    return "killed"


def prepared(directory: Path, mutant: dict[str, str] | None) -> bool:
    for path in MODEL.glob("*.dfy"):
        shutil.copy(path, directory / path.name)
    if mutant is None:
        return True
    target = directory / mutant["file"]
    text = target.read_text(encoding="utf-8")
    if text.count(mutant["from"]) != 1:
        return False
    target.write_text(text.replace(mutant["from"], mutant["to"]), encoding="utf-8")
    return True


def main() -> int:
    dafny = os.environ.get("DAFNY", "dafny")
    mutants = json.loads((MODEL / "mutants.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        prepared(Path(tmp), None)
        baseline = verify(dafny, Path(tmp))
    if baseline.returncode != 0:
        print("ERROR the unmodified model does not verify:", file=sys.stderr)
        print(baseline.stdout + baseline.stderr, file=sys.stderr)
        return 2
    survivors: list[str] = []
    inconclusive: list[str] = []
    for mutant in mutants:
        with tempfile.TemporaryDirectory() as tmp:
            if not prepared(Path(tmp), mutant):
                print(
                    f"ERROR {mutant['name']}: edit does not match exactly once",
                    file=sys.stderr,
                )
                return 2
            result = verify(dafny, Path(tmp))
        outcome = classify(result.returncode, result.stdout + result.stderr)
        print(f"{outcome:<12} [{mutant['group']}] {mutant['name']}")
        if outcome == "survived":
            survivors.append(mutant["name"])
        elif outcome == "inconclusive":
            inconclusive.append(mutant["name"])
            print(result.stdout + result.stderr, file=sys.stderr)
    killed = len(mutants) - len(survivors) - len(inconclusive)
    print(
        f"{killed} of {len(mutants)} mutants killed, {len(inconclusive)} inconclusive"
    )
    if inconclusive:
        return 2
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
