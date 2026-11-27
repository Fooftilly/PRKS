"""Print the Dafny pilot's resource use per proof, largest first (#1472)."""

from __future__ import annotations

import csv
import sys


def main(path: str) -> int:
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return 0
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
