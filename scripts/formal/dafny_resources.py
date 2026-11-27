"""Print the Dafny pilot's resource use per proof, largest first (#1472)."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def report(rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    columns = list(rows[0])
    resource = next(c for c in columns if "resource" in c.lower())
    name = next(
        c for c in columns if "displayname" in c.lower() or c.lower().endswith("name")
    )
    outcome = next((c for c in columns if "outcome" in c.lower()), None)
    rows.sort(key=lambda row: -int(row[resource] or 0))
    print(f"{len(rows)} proof obligations; largest resource counts:")
    for row in rows[:15]:
        print(
            f"{int(row[resource] or 0):>12}  {row[outcome] if outcome else ''}  {row[name]}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="CSV log from dafny verify --log-format")
    args = parser.parse_args()
    with args.log.open(encoding="utf-8", newline="") as handle:
        report(list(csv.DictReader(handle)))


if __name__ == "__main__":
    main()
