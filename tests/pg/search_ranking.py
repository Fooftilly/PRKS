"""Before/after ranking report for keyword search (L-S-2 #638).

Loads :mod:`tests.pg.search_corpus` into a PostgreSQL Library, runs every
judged query through ``search_works``, and scores the order:

* nDCG@10, with gain ``2**grade - 1``: 1.0 means the best possible order;
* reciprocal rank of the first Work with the query's top grade;
* inversions: pairs of found Works where a lower-graded one is listed
  above a higher-graded one.

``tests/pg/search_ranking_baseline.json`` holds the order each query
returns on ``master``, as Work keys, and ``tests/pg/test_search_ranking.py``
pins the live order to it. A ranking change therefore updates that file in
the same PR, and the diff, together with this report, is the before/after
result that review approves.

Run against a disposable server (the same variable the PostgreSQL tests use)::

    PRKS_TEST_DATABASE_URL=... python -m tests.pg.search_ranking            # report
    PRKS_TEST_DATABASE_URL=... python -m tests.pg.search_ranking --compare  # with the baseline
    PRKS_TEST_DATABASE_URL=... python -m tests.pg.search_ranking --write-baseline
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Callable

from tests.pg.search_corpus import QUERIES, WORKS, JudgedQuery

BASELINE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "search_ranking_baseline.json")

_EDITED = ("UPDATE prks.works SET updated_at = timestamptz '2026-01-01 10:00:00+00'"
           " + make_interval(mins => %(minute)s), created_at = timestamptz '2026-01-01 10:00:00+00'"
           " + make_interval(mins => %(minute)s) WHERE id = %(work_id)s")


def load(library: Any, sql: Callable[[str, dict[str, Any]], Any]) -> dict[str, str]:
    """Create the corpus in ``library``; returns Work id by corpus key.

    Each Work is created and edited one minute after the previous one, so
    list order is recency order (:mod:`tests.pg.search_corpus`).
    """
    people: dict[tuple[str, str], str] = {}
    ids: dict[str, str] = {}
    for minute, work in enumerate(WORKS):
        work_id = library.add_work(work.title, abstract=work.abstract, text_content=work.notes,
                                   author_text=work.author_text)
        for name in work.people:
            if name not in people:
                people[name] = library.add_person(*name)
            library.add_role(people[name], work_id, "Author")
        sql(_EDITED, {"minute": minute, "work_id": work_id})
        ids[work.key] = work_id
    return ids


def ranked_keys(library: Any, ids: dict[str, str], text: str) -> list[str]:
    """The corpus keys ``search_works`` lists for ``text``, in order."""
    keys = {work_id: key for key, work_id in ids.items()}
    return [keys[row["id"]] for row in library.search_works(text)]


@dataclass(frozen=True)
class Score:
    ndcg10: float
    reciprocal_rank: float
    inversions: int
    missing: tuple[str, ...]


def score(query: JudgedQuery, keys: list[str]) -> Score:
    def gain(grade: int) -> float:
        return float(2 ** grade - 1)

    dcg = sum(gain(query.grades.get(key, 0)) / math.log2(i + 2) for i, key in enumerate(keys[:10]))
    ideal = sorted(query.grades.values(), reverse=True)[:10]
    idcg = sum(gain(grade) / math.log2(i + 2) for i, grade in enumerate(ideal))
    top = max(query.grades.values())
    rr = next((1.0 / (i + 1) for i, key in enumerate(keys) if query.grades.get(key, 0) == top), 0.0)
    graded = [query.grades.get(key, 0) for key in keys]
    inversions = sum(1 for i, upper in enumerate(graded) for lower in graded[i + 1:] if upper < lower)
    missing = tuple(sorted(key for key in query.grades if key not in keys))
    return Score(dcg / idcg if idcg else 0.0, rr, inversions, missing)


def run(library: Any, ids: dict[str, str]) -> dict[str, list[str]]:
    return {query.text: ranked_keys(library, ids, query.text) for query in QUERIES}


def read_baseline() -> dict[str, list[str]]:
    with open(BASELINE_PATH, encoding="utf-8") as handle:
        return json.load(handle)["orders"]


def write_baseline(orders: dict[str, list[str]]) -> None:
    payload = {
        "about": "Keyword search order on tests/pg/search_corpus.py, as corpus keys (L-S-2 #638)."
                 " Regenerate with: python -m tests.pg.search_ranking --write-baseline",
        "orders": orders,
    }
    with open(BASELINE_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _listed(query: JudgedQuery, keys: list[str]) -> str:
    return ", ".join(f"{key} ({query.grades.get(key, 0)})" for key in keys) or "nothing"


def report(orders: dict[str, list[str]], before: dict[str, list[str]] | None = None) -> str:
    """A Markdown report of ``orders``; with ``before``, each query shows both."""
    lines = ["# Keyword search ranking on the synthetic corpus", ""]
    head = "| Query | Lang | nDCG@10 | RR | Inversions |"
    if before is not None:
        head = "| Query | Lang | nDCG@10 before → after | RR before → after | Inversions before → after |"
    lines += [head, "| --- | --- | --- | --- | --- |"]
    totals = {"after": [0.0, 0.0, 0], "before": [0.0, 0.0, 0]}
    for query in QUERIES:
        now = score(query, orders[query.text])
        totals["after"][0] += now.ndcg10
        totals["after"][1] += now.reciprocal_rank
        totals["after"][2] += now.inversions
        if before is None:
            lines.append(f"| `{query.text}` | {query.language} | {now.ndcg10:.3f} | "
                         f"{now.reciprocal_rank:.2f} | {now.inversions} |")
            continue
        old = score(query, before.get(query.text, []))
        totals["before"][0] += old.ndcg10
        totals["before"][1] += old.reciprocal_rank
        totals["before"][2] += old.inversions
        lines.append(f"| `{query.text}` | {query.language} | {old.ndcg10:.3f} → {now.ndcg10:.3f} | "
                     f"{old.reciprocal_rank:.2f} → {now.reciprocal_rank:.2f} | "
                     f"{old.inversions} → {now.inversions} |")
    n = len(QUERIES)
    after = totals["after"]
    if before is None:
        lines.append(f"| **Mean / total** | | **{after[0] / n:.3f}** | **{after[1] / n:.2f}** | **{after[2]}** |")
    else:
        old_totals = totals["before"]
        lines.append(f"| **Mean / total** | | **{old_totals[0] / n:.3f} → {after[0] / n:.3f}** | "
                     f"**{old_totals[1] / n:.2f} → {after[1] / n:.2f}** | "
                     f"**{old_totals[2]} → {after[2]}** |")
    lines += ["", "Grades: 3 target, 2 relevant, 1 marginal, 0 not judged.", ""]
    for query in QUERIES:
        lines += [f"## `{query.text}` ({query.language})", "", query.why, ""]
        if before is not None:
            lines.append(f"- Before: {_listed(query, before.get(query.text, []))}")
            lines.append(f"- After: {_listed(query, orders[query.text])}")
        else:
            lines.append(f"- Order: {_listed(query, orders[query.text])}")
        missing = score(query, orders[query.text]).missing
        if missing:
            lines.append(f"- **Not found:** {', '.join(missing)}")
        lines.append("")
    return "\n".join(lines)


def _library() -> tuple[Any, Callable[[str, dict[str, Any]], Any], Callable[[], None]]:
    """A fresh harness database with one Library and a bound owner."""
    from backend.db.config import DatabaseConfig
    from backend.db.database import Database
    from tests.library_adapter import TestLibrary
    from backend.pg_facade import context as facade_context
    from backend.storage.config import StorageConfig
    from tests.pg.facade_context import provision_actor
    from tests.pg.harness import get_harness

    harness = get_harness()
    name = harness.create_database("ranking")
    db = Database(DatabaseConfig(conninfo=harness.conninfo(name), role="test", pool_min=0, pool_max=4))
    tmp = tempfile.mkdtemp(prefix="prks-pg-ranking-")
    storage = StorageConfig.for_testing(tmp)
    os.makedirs(storage.pdfs_dir, exist_ok=True)
    library = TestLibrary(db, storage=storage)
    token = facade_context.bind(provision_actor(db).context)

    def sql(query: str, params: dict[str, Any]) -> None:
        db.command(lambda tx: tx.execute(query, params))

    def close() -> None:
        facade_context.unbind(token)
        db.close()
        harness.drop_database(name)
        shutil.rmtree(tmp, True)

    return library, sql, close


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--compare", action="store_true", help="show the baseline order next to the live one")
    mode.add_argument("--write-baseline", action="store_true", help="store the live order as the baseline")
    args = parser.parse_args(argv)
    library, sql, close = _library()
    try:
        orders = run(library, load(library, sql))
    finally:
        close()
    if args.write_baseline:
        write_baseline(orders)
        print(f"wrote {os.path.relpath(BASELINE_PATH)}", file=sys.stderr)
        return 0
    print(report(orders, read_baseline() if args.compare else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
