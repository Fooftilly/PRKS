"""Informational changed-executable-line coverage for the two #651 B1 pilots.

This is deliberately *not* total/PR-wide coverage: only lines in the explicitly
instrumented files count. Non-executable diff lines are excluded and unavailable
data is reported as unavailable, never as a fabricated 0% or 100%.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def added_lines(patch: str) -> dict[str, set[int]]:
    """Return new-side added line numbers from Git's zero-context diff."""
    result: dict[str, set[int]] = {}
    current: str | None = None
    for line in patch.splitlines():
        if line.startswith("+++ "):
            name = line[4:]
            current = name[2:] if name.startswith("b/") else None
        elif current is not None:
            match = HUNK.match(line)
            if match:
                start = int(match.group(1))
                count = int(match.group(2)) if match.group(2) is not None else 1
                if count:
                    result.setdefault(current, set()).update(range(start, start + count))
    return result


def _find_entry(report: dict, source: str, kind: str) -> dict | None:
    files = report["files"] if kind == "coveragepy" else {
        key: value for key, value in report.items() if key != "total"
    }
    matches = [
        value for key, value in files.items()
        if key.replace("\\", "/") == source
        or key.replace("\\", "/").endswith("/" + source)
    ]
    if len(matches) > 1:
        raise ValueError(f"Ambiguous coverage source: {source}")
    return matches[0] if matches else None


def executable_lines(entry: dict, kind: str) -> tuple[set[int], set[int]] | None:
    """Return (executable, covered) lines, or None if detail is unavailable."""
    if kind == "coveragepy":
        if "executed_lines" not in entry or "missing_lines" not in entry:
            return None
        covered = set(map(int, entry["executed_lines"]))
        missing = set(map(int, entry["missing_lines"]))
        return covered | missing, covered
    # Vitest's V8 provider uses Istanbul's JSON reporter. Unlike older
    # Istanbul formats, coverage-final.json generally has statementMap/s
    # but no precomputed line map. Combine statement hits on their start
    # lines, using max so an uncovered statement cannot erase a covered one.
    statements, hits = entry.get("statementMap"), entry.get("s")
    if isinstance(statements, dict) and isinstance(hits, dict) and statements:
        lines: dict[int, int] = {}
        for statement_id, location in statements.items():
            if statement_id not in hits:
                return None
            line = int(location["start"]["line"])
            lines[line] = max(lines.get(line, 0), int(hits[statement_id]))
        return set(lines), {line for line, count in lines.items() if count > 0}
    # Older Istanbul JSON representations may include line hits directly.
    lines = entry.get("l")
    if not isinstance(lines, dict) or not lines:
        return None
    return {int(n) for n in lines}, {int(n) for n, count in lines.items() if count > 0}


def changed_line_report(
    patch: str, report: dict, kind: str, sources: list[str]
) -> str:
    """Write a bounded Markdown summary without treating comments as misses."""
    touched = added_lines(patch)
    rows = [
        "### Changed executable lines (instrumented pilot modules only)",
        "",
        "| Module | Added executable lines covered |",
        "| --- | ---: |",
    ]
    covered_total = measured_total = 0
    unavailable = False
    for source in sources:
        added = touched.get(source, set())
        if not added:
            rows.append(f"| `{source}` | No added lines in this pilot |")
            continue
        entry = _find_entry(report, source, kind)
        detail = executable_lines(entry, kind) if entry is not None else None
        if detail is None:
            unavailable = True
            rows.append(f"| `{source}` | Unavailable (no line-level coverage data) |")
            continue
        executable, covered = detail
        eligible = added & executable
        hit = len(eligible & covered)
        covered_total += hit
        measured_total += len(eligible)
        if eligible:
            rows.append(f"| `{source}` | {hit}/{len(eligible)} ({hit / len(eligible):.0%}) |")
        else:
            rows.append(f"| `{source}` | No added executable lines |")
    overall = (
        "Unavailable for some changed pilot files"
        if unavailable else
        f"{covered_total}/{measured_total} ({covered_total / measured_total:.0%})"
        if measured_total else "No added executable lines in pilot scope"
    )
    rows.extend([
        f"| **Total (measurable pilot lines only)** | **{overall}** |",
        "",
        "Informational; no threshold. Counts only newly added executable lines in",
        "the selected pilot modules, not total PR coverage, and excludes comments,",
        "blank lines, deleted lines, uninstrumented modules and branch outcomes.",
        "An unavailable source is never silently counted as covered.",
        "",
    ])
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("coveragepy", "istanbul"), required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--diff", type=Path, required=True)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--summary-file", type=Path, required=True)
    args = parser.parse_args()
    patch = args.diff.read_text(encoding="utf-8")
    report = json.loads(args.report.read_text(encoding="utf-8"))
    summary = changed_line_report(patch, report, args.format, args.source)
    print(summary)
    with args.summary_file.open("a", encoding="utf-8") as output:
        output.write(summary)


if __name__ == "__main__":
    main()
