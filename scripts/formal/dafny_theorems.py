"""Check the required-theorem manifest for the Dafny pilot (#1472).

Fails when a listed lemma is missing or (with --verified) did not pass, its statement (signature, requires and
ensures) changed, a lemma with preconditions has no witness lemma, or a model
file uses an escape hatch. `--write` records the current statements.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "formal" / "dafny"
FORBIDDEN = re.compile(
    r"\bassume\b|\{:axiom\}|\{:verify\s+false\}|\{:only\}|\bexpect\b"
)
LEMMA = re.compile(r"^\s*lemma\s+(?:\{:[^}]*\}\s*)*(\w+)")


def strip_comments(text: str) -> str:
    return re.sub(r"//[^\n]*", "", text)


def statements(path: Path) -> dict[str, str]:
    """Each lemma's text up to the line that opens its body, whitespace-normalized."""
    found: dict[str, str] = {}
    lines = strip_comments(path.read_text(encoding="utf-8")).splitlines()
    i = 0
    while i < len(lines):
        match = LEMMA.match(lines[i])
        if not match:
            i += 1
            continue
        header = []
        while i < len(lines) and not lines[i].strip().startswith("{"):
            header.append(lines[i])
            i += 1
        found[match.group(1)] = " ".join(" ".join(header).split())
    return found


def digest(statement: str) -> str:
    return hashlib.sha256(statement.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="record the current statements"
    )
    parser.add_argument(
        "--verified",
        type=Path,
        help="a Dafny CSV log: every required lemma MUST have passed in it",
    )
    parser.add_argument("--model", type=Path, default=MODEL, help=argparse.SUPPRESS)
    args = parser.parse_args()
    manifest_path = args.model / "theorems.json"

    current: dict[str, tuple[str, str]] = {}
    problems: list[str] = []
    for path in sorted(args.model.glob("*.dfy")):
        text = strip_comments(path.read_text(encoding="utf-8"))
        for hit in FORBIDDEN.finditer(text):
            problems.append(f"{path.name}: forbidden construct {hit.group(0)!r}")
        for name, body in statements(path).items():
            current[name] = (path.name, body)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    theorems = manifest["theorems"]
    if args.write:
        for name in theorems:
            file, body = current[name]
            theorems[name] = {"file": file, "sha256": digest(body)}
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        return 0

    witnesses = manifest.get("witnesses", {})
    for name, entry in theorems.items():
        if name not in current:
            problems.append(f"{name}: required lemma is missing")
            continue
        file, body = current[name]
        if file != entry["file"] or digest(body) != entry["sha256"]:
            problems.append(
                f"{name}: statement changed (spec changes need their own reviewed PR)"
            )
        if " requires " in f" {body} ":
            witness = witnesses.get(name)
            if witness is None:
                problems.append(f"{name}: has preconditions and no witness lemma")
            elif witness not in current:
                problems.append(f"{name}: witness {witness} is missing")
    if args.verified:
        with args.verified.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        name_col = next(c for c in rows[0] if "displayname" in c.lower())
        outcome_col = next(c for c in rows[0] if "outcome" in c.lower())
        passed = {
            row[name_col].split(" ")[0].rsplit(".", 1)[-1]
            for row in rows
            if row[outcome_col] == "Passed"
        }
        failed = {
            row[name_col].split(" ")[0].rsplit(".", 1)[-1]
            for row in rows
            if row[outcome_col] != "Passed"
        }
        for name in theorems:
            if name not in passed or name in failed:
                problems.append(f"{name}: not verified in {args.verified.name}")
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"{len(theorems)} required lemmas, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
