"""E2E coverage map: which browser tests execute which source lines (#740 follow-up).

A recording run (``PRKS_E2E_COVERAGE_MAP_DIR`` set, see
``.github/workflows/e2e-coverage-map.yml``) records, per test:

- the Python lines its PRKS server executed (``backend/**``, ``prks_app.py``),
  by running the server under coverage.py; and
- the classic frontend lines its pages executed (``frontend/js/**``), from
  Chromium's precise coverage. A line counts when the innermost function
  around it ran. Coverage is taken before each ``goto``/``reload``/history
  navigation and before a context closes, because a new document discards
  the old one's counters; and
- the Vue source lines (``frontend-app/src/**``) behind the parts of the
  bundle ``frontend/vue/prks-vue.js`` that ran, when the recording run built
  a hidden source map of that same bundle (``PRKS_E2E_VUE_SOURCE_MAP``).

``build`` merges the per-shard records into one map: for each file, runs of
lines with the set of tests that executed them. A selector can then pick the
tests for a diff. The map is advisory data (shadow mode): the E2E gate does
not read it.

Not covered, by design: vendored scripts, CSS (including ``<style>`` in a
``.vue`` file) and HTML, service and web workers, and app-side reloads that
no test call started. Records hold repository paths, line
numbers and unittest ids only: never URLs with data, titles or bodies.
"""

from __future__ import annotations

import argparse
import bisect
import gzip
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[2]
from tests.e2e.policy import COVERAGE_MAP_ENV as ENV
FORMAT = 1
# The served path prefix and the repository directory it maps to.
_JS_PREFIX = "/js/"
_JS_ROOT = "frontend/js/"
_PY_ROOTS = ("backend/",)
_PY_FILES = ("prks_app.py",)
# The Vue bundle, where it is served, and the sources its source map names.
_VUE_BUNDLE = "frontend/vue/prks-vue.js"
_VUE_URL = "/vue/prks-vue.js"
_VUE_ROOT = "frontend-app/src/"
_VUE_EXTENSIONS = (".ts", ".vue", ".js")
_NOT_SHIPPED = tuple(f".{kind}.{ext}" for kind in ("test", "spec", "stories") for ext in ("ts", "js", "vue"))
VUE_SOURCE_MAP_ENV = "PRKS_E2E_VUE_SOURCE_MAP"
# Build outputs that static-analysis.yml rebuilds from the sources and diffs
# against the commit: a change to them is explained by the source change.
_DERIVED = (
    "frontend/vue/prks-vue.js",
    "frontend/vue/BUILD-MANIFEST.json",
    "frontend/vue/THIRD_PARTY_LICENSES.md",
    "frontend/vendor/DEPENDENCY-MANIFEST.json",
)
# sw.js is derived only where the build rewrites it: this one line.
_SW = "frontend/sw.js"
_SW_REVISION = re.compile(r"^[+-]\s*const DEPENDENCY_REVISION = '[0-9a-f]+';\s*$")

_LOCK = threading.Lock()
_SESSIONS: dict[Any, Any] = {}
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_SOURCE_LINES: dict[str, tuple[str, list[int]]] = {}


def enabled() -> bool:
    return bool(os.environ.get(ENV))


def _out_dir() -> Path:
    path = Path(os.environ[ENV])
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write(test_id: str, kind: str, files: Mapping[str, Iterable[int]]) -> None:
    clean = {path: sorted(set(lines)) for path, lines in files.items() if lines}
    if not test_id or not clean:
        return
    line = json.dumps({"test": test_id, "kind": kind, "files": clean}, sort_keys=True)
    with _LOCK:
        with open(_out_dir() / f"records-{os.getpid()}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


RUN_STATUS = "run-status.json"


def write_run_status(*, selected: int, ran: int, failures: int, errors: int,
                     count_error: str | None, skip_error: str | None) -> None:
    """What the recording run finished with, for ``check-run`` (tests/e2e/run.py writes it last)."""
    status = {
        "selected": selected, "ran": ran, "failures": failures, "errors": errors,
        "count_error": count_error or "", "skip_error": skip_error or "",
    }
    (_out_dir() / RUN_STATUS).write_text(json.dumps(status, sort_keys=True) + "\n", encoding="utf-8")


def check_run(records: Path) -> tuple[bool, str]:
    """Whether a failed recording run may still pass: it ran every selected
    test, skipped none unexpectedly, and failed only through test failures
    or errors. No status (a watchdog or deadline exit) never passes."""
    try:
        status = json.loads((records / RUN_STATUS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "no run status: the shard did not reach its end"
    if status.get("count_error"):
        return False, str(status["count_error"])
    if status.get("skip_error"):
        return False, str(status["skip_error"])
    if status.get("ran") != status.get("selected"):
        return False, "ran %s of %s selected tests" % (status.get("ran"), status.get("selected"))
    if not (status.get("failures") or status.get("errors")):
        return False, "the run failed without a test failure"
    return True, "%s failures and %s errors under instrumentation; all %s tests ran" % (
        status["failures"], status["errors"], status["ran"])


# --- Python (the PRKS server) ----------------------------------------------


def server_argv(python: str, tmpdir: str) -> list[str]:
    """The interpreter prefix that runs prks_app.py under coverage.py."""
    rc = Path(tmpdir) / "coverage.rc"
    # coverage.py's "*" stops at a slash; "**/" spans directories.
    include = ", ".join(
        [f"{REPO / root}/**/*.py" for root in _PY_ROOTS] + [str(REPO / name) for name in _PY_FILES]
    )
    rc.write_text(
        "[run]\n"
        f"data_file = {Path(tmpdir) / 'server.coverage'}\n"
        f"include = {include}\n"
        "concurrency = thread\n"
        # Write the data when the harness stops the server with SIGTERM.
        "sigterm = true\n",
        encoding="utf-8",
    )
    return [python, "-m", "coverage", "run", "--rcfile", str(rc)]


def record_server(test_id: str, tmpdir: str) -> None:
    """Read the stopped server's coverage data and record it for ``test_id``."""
    data_file = Path(tmpdir) / "server.coverage"
    if not data_file.exists():
        return
    from coverage import CoverageData  # pyright: ignore[reportMissingImports]  # installed only by e2e-coverage-map.yml

    data = CoverageData(basename=str(data_file))
    data.read()
    files: dict[str, list[int]] = {}
    for measured in data.measured_files():
        try:
            rel = Path(measured).resolve().relative_to(REPO).as_posix()
        except ValueError:
            continue
        files[rel] = list(data.lines(measured) or ())
    _write(test_id, "py", files)


# --- Classic frontend (Chromium precise coverage) ----------------------------


def _source(rel: str) -> tuple[str, list[int]]:
    """The file's text and line starts, indexed like V8's offsets (UTF-16 units)."""
    cached = _SOURCE_LINES.get(rel)
    if cached is None:
        text = (REPO / rel).read_text(encoding="utf-8")
        # A character outside the BMP is two UTF-16 units: pad it to match.
        text = "".join(char if ord(char) <= 0xFFFF else char + "\uffff" for char in text)
        starts = [0]
        for index, char in enumerate(text):
            if char == "\n":
                starts.append(index + 1)
        cached = _SOURCE_LINES[rel] = (text, starts)
    return cached


def _line_of(starts: list[int], offset: int) -> int:
    lo, hi = 0, len(starts)
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if starts[mid] <= offset:
            lo = mid
        else:
            hi = mid
    return lo + 1


def _segments(functions: Sequence[Mapping[str, Any]]) -> list[tuple[int, int, int]]:
    """Split the script into pieces, each with the count of its innermost range.

    V8's ranges nest: a function's range holds its inner functions' ranges
    (and, in detailed mode, block ranges). A piece takes the count of the
    smallest range around it, so an uncalled callback only blanks its own
    characters, not the rest of the line that ran.
    """
    spans = sorted(
        (
            (int(rng["startOffset"]), int(rng["endOffset"]), int(rng.get("count") or 0))
            for function in functions
            for rng in function.get("ranges") or ()
        ),
        key=lambda span: (span[0], -span[1]),
    )
    pieces: list[tuple[int, int, int]] = []
    stack: list[tuple[int, int, int]] = []
    position = 0

    def emit(upto: int) -> None:
        nonlocal position
        if stack and upto > position:
            pieces.append((position, upto, stack[-1][2]))
        position = max(position, upto)

    for start, end, count in spans:
        while stack and stack[-1][1] <= start:
            emit(stack[-1][1])
            stack.pop()
        emit(start)
        stack.append((start, end, count))
    while stack:
        emit(stack[-1][1])
        stack.pop()
    return pieces


def executed_lines(functions: Sequence[Mapping[str, Any]], starts: list[int], text: str | None = None) -> set[int]:
    """Lines holding code that ran (precise coverage, innermost range wins).

    With ``text``, only non-whitespace characters count, so the indentation
    before an uncalled function does not mark its first line.
    """
    lines: set[int] = set()
    for start, end, count in _segments(functions):
        if count <= 0:
            continue
        if text is not None:
            chunk = text[start:end]
            stripped = chunk.lstrip()
            if not stripped:
                continue
            start += len(chunk) - len(stripped)
            end -= len(chunk) - len(chunk.rstrip())
        lines.update(range(_line_of(starts, start), _line_of(starts, max(start, end - 1)) + 1))
    return lines


_B64 = {char: index for index, char in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")}


def decode_mappings(mappings: str) -> list[tuple[int, int, int, int]]:
    """Source map v3 ``mappings`` as ``(generated line, generated column, source, original line)``, 0-based.

    A one-field segment starts generated code with no source: it is kept, with source -1.
    """
    out: list[tuple[int, int, int, int]] = []
    source = original_line = original_column = 0
    for generated_line, text in enumerate(mappings.split(";")):
        column = 0
        for segment in text.split(",") if text else ():
            values: list[int] = []
            value = shift = 0
            for char in segment:
                digit = _B64[char]
                value += (digit & 31) << shift
                if digit & 32:
                    shift += 5
                else:
                    values.append(-(value >> 1) if value & 1 else value >> 1)
                    value = shift = 0
            column += values[0]
            if len(values) == 1:
                out.append((generated_line, column, -1, -1))
            elif len(values) >= 4:
                source += values[1]
                original_line += values[2]
                original_column += values[3]
                out.append((generated_line, column, source, original_line))
    return out


class BundleMap:
    """Generated offsets of the Vue bundle -> the ``frontend-app/src`` line each came from."""

    def __init__(self, source_map: Mapping[str, Any], map_dir: Path, starts: list[int], repo: Path = REPO):
        names: list[str | None] = []
        for raw in source_map.get("sources") or ():
            try:
                rel = (map_dir / str(raw)).resolve().relative_to(repo.resolve()).as_posix()
            except (ValueError, OSError):
                rel = None
            names.append(rel if rel is not None and is_vue_source(rel) else None)
        # Each generated line starts unmapped: a mapping never carries over a line break.
        entries: list[tuple[int, int, str | None, int]] = [(start, 0, None, 0) for start in starts]
        self.code_lines: dict[str, set[int]] = {}
        for line, column, source, original in decode_mappings(str(source_map.get("mappings") or "")):
            if line >= len(starts) or source >= len(names):
                continue
            rel = names[source] if source >= 0 else None
            entries.append((starts[line] + column, 1, rel, original + 1))
            if rel is not None:
                self.code_lines.setdefault(rel, set()).add(original + 1)
        entries.sort(key=lambda entry: (entry[0], entry[1]))
        self.offsets: list[int] = [offset for offset, _order, _rel, _line in entries]
        self.targets: list[tuple[str | None, int]] = [(rel, line) for _offset, _order, rel, line in entries]

    def lines(self, pieces: Iterable[tuple[int, int, int]]) -> dict[str, set[int]]:
        """The source lines behind the executed ``pieces`` (start, end, count) of the bundle."""
        found: dict[str, set[int]] = {}
        for start, end, count in pieces:
            if count <= 0 or end <= start:
                continue
            # The mapping in effect at ``start``, and every one that begins before ``end``.
            first = max(0, bisect.bisect_right(self.offsets, start) - 1)
            last = bisect.bisect_left(self.offsets, end)
            for rel, line in self.targets[first:last]:
                if rel is not None:
                    found.setdefault(rel, set()).add(line)
        return found


_BUNDLE_MAP: list[BundleMap | None] = []


def load_bundle_map(path: str | os.PathLike[str] | None = None) -> BundleMap | None:
    """The Vue bundle's source map named by ``PRKS_E2E_VUE_SOURCE_MAP``, or None without one."""
    if path is None:
        if _BUNDLE_MAP:
            return _BUNDLE_MAP[0]
        path = os.environ.get(VUE_SOURCE_MAP_ENV) or None
        if path is None:
            _BUNDLE_MAP.append(None)
            return None
        _BUNDLE_MAP.append(load_bundle_map(path))
        return _BUNDLE_MAP[0]
    map_path = Path(path)
    with open(map_path, encoding="utf-8") as fh:
        source_map = json.load(fh)
    _text, starts = _source(_VUE_BUNDLE)
    return BundleMap(source_map, map_path.parent, starts)


def _is_vue_bundle(url: str, origin_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and parsed.hostname in origin_hosts and parsed.path == _VUE_URL


def _repo_path_for(url: str, origin_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in origin_hosts:
        return None
    if not parsed.path.startswith(_JS_PREFIX) or not parsed.path.endswith(".js"):
        return None
    rel = _JS_ROOT + parsed.path[len(_JS_PREFIX):]
    if ".." in rel.split("/") or not (REPO / rel).is_file():
        return None
    return rel


def _take(page) -> None:
    session = _SESSIONS.get(page)
    if session is None:
        return
    try:
        result = session.send("Profiler.takePreciseCoverage")["result"]
    except Exception:  # noqa: BLE001 - a closed or crashed page has nothing left to read
        return
    files: dict[str, set[int]] = {}
    for script in result:
        if _is_vue_bundle(script.get("url") or ""):
            bundle = load_bundle_map()
            if bundle is not None:
                for rel, lines in bundle.lines(_segments(script.get("functions") or [])).items():
                    files.setdefault(rel, set()).update(lines)
            continue
        rel = _repo_path_for(script.get("url") or "")
        if rel is None:
            continue
        text, starts = _source(rel)
        files.setdefault(rel, set()).update(executed_lines(script.get("functions") or [], starts, text))
    from tests.e2e.harness import get_e2e_heartbeat

    _write(get_e2e_heartbeat().get("test_id") or "", "js", files)


def _start(page) -> None:
    try:
        session = page.context.new_cdp_session(page)
        session.send("Profiler.enable")
        session.send("Profiler.startPreciseCoverage", {"callCount": False, "detailed": False})
    except Exception:  # noqa: BLE001 - not Chromium: nothing to record
        return
    _SESSIONS[page] = session
    # A crashed page never answers CDP again: a later take would hang.
    page.on("crash", lambda crashed: _SESSIONS.pop(crashed, None))


_INSTALLED = False


def install_browser_hooks() -> None:
    """Patch Playwright's sync API so every page records precise coverage (recording runs only)."""
    global _INSTALLED
    if _INSTALLED or not enabled():
        return
    from playwright.sync_api import BrowserContext, Page

    original_new_page = BrowserContext.new_page
    original_close = BrowserContext.close
    original_page_close = Page.close

    def new_page(self, *args, **kwargs):
        page = original_new_page(self, *args, **kwargs)
        _start(page)
        return page

    def close(self, *args, **kwargs):
        for page in list(self.pages):
            _take(page)
            _SESSIONS.pop(page, None)
        return original_close(self, *args, **kwargs)

    def page_close(self, *args, **kwargs):
        # A closed tab leaves self.pages, so the context close cannot read it.
        _take(self)
        result = original_page_close(self, *args, **kwargs)
        if self.is_closed():
            _SESSIONS.pop(self, None)
        return result

    Page.close = page_close  # type: ignore[method-assign]
    BrowserContext.new_page = new_page  # type: ignore[method-assign]
    BrowserContext.close = close  # type: ignore[method-assign]
    for name in ("goto", "reload", "go_back", "go_forward"):
        original = getattr(Page, name)

        def navigate(self, *args, __original=original, **kwargs):
            _take(self)
            return __original(self, *args, **kwargs)

        setattr(Page, name, navigate)
    _INSTALLED = True


# --- Building the map --------------------------------------------------------


def _read_records(paths: Iterable[Path]) -> tuple[list[str], dict[str, dict[int, set[str]]]]:
    tests: set[str] = set()
    lines: dict[str, dict[int, set[str]]] = {}
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                record = json.loads(raw)
                test = record["test"]
                tests.add(test)
                for rel, numbers in record["files"].items():
                    per_line = lines.setdefault(rel, {})
                    for number in numbers:
                        per_line.setdefault(int(number), set()).add(test)
    return sorted(tests), lines


def _spans(numbers: Iterable[int]) -> list[list[int]]:
    spans: list[list[int]] = []
    for number in sorted(set(numbers)):
        if spans and spans[-1][1] == number - 1:
            spans[-1][1] = number
        else:
            spans.append([number, number])
    return spans


def build_map(record_paths: Iterable[Path], *, commit: str, bundle: BundleMap | None = None) -> dict[str, Any]:
    """Merge records into ``{tests, sets, files: {path: [[first, last, set], ...]}}``.

    ``sets`` lists each distinct test set once (indexes into ``tests``); a file's
    runs are maximal spans of consecutive executed lines that share one set.
    With the Vue ``bundle`` map, ``code_lines`` lists, per Vue source, the
    spans of lines that produced bundle code, run or not: a changed line with
    no code of its own then selects through its neighbours.
    """
    tests, lines = _read_records(record_paths)
    index = {test: i for i, test in enumerate(tests)}
    set_ids: dict[tuple[int, ...], int] = {}
    files: dict[str, list[list[int]]] = {}
    for rel in sorted(lines):
        runs: list[list[int]] = []
        for number in sorted(lines[rel]):
            key = tuple(sorted(index[t] for t in lines[rel][number]))
            set_id = set_ids.setdefault(key, len(set_ids))
            if runs and runs[-1][1] == number - 1 and runs[-1][2] == set_id:
                runs[-1][1] = number
            else:
                runs.append([number, number, set_id])
        files[rel] = runs
    sets = [list(key) for key, _ in sorted(set_ids.items(), key=lambda item: item[1])]
    result: dict[str, Any] = {
        "format": FORMAT,
        "commit": commit,
        "tests": tests,
        "sets": sets,
        "files": files,
    }
    if bundle is not None:
        result["code_lines"] = {rel: _spans(lines) for rel, lines in sorted(bundle.code_lines.items())}
    return result


def tests_for_lines(coverage_map: Mapping[str, Any], rel: str, wanted: Iterable[int]) -> set[str]:
    """The tests that executed any of ``wanted`` lines of ``rel`` in the map."""
    runs = coverage_map["files"].get(rel) or ()
    tests = coverage_map["tests"]
    picked: set[int] = set()
    wanted = sorted(set(wanted))
    for first, last, set_id in runs:
        if any(first <= n <= last for n in wanted):
            picked.update(coverage_map["sets"][set_id])
    return {tests[i] for i in picked}


# --- Selecting tests for a diff (shadow mode) --------------------------------

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def is_mapped_kind(rel: str) -> bool:
    """Paths the recorder measures; every other path keeps the planner's rules."""
    return (
        (rel.startswith(_PY_ROOTS) and rel.endswith(".py"))
        or rel in _PY_FILES
        or (rel.startswith(_JS_ROOT) and rel.endswith(".js"))
        or is_vue_source(rel)
    )


def is_vue_source(rel: str) -> bool:
    return rel.startswith(_VUE_ROOT) and rel.endswith(_VUE_EXTENSIONS) and not rel.endswith(_NOT_SHIPPED)


def with_code_neighbours(wanted: Iterable[int], code: Iterable[Sequence[int]]) -> set[int]:
    """Each wanted line that produced no bundle code stands for the nearest code lines above and below.

    A source map names only the lines code came from: a changed prop default
    or a line inside a multi-line literal can have no mapping of its own.
    """
    lines = sorted({n for first, last in code for n in range(int(first), int(last) + 1)})
    if not lines:
        return set(wanted)
    known = set(lines)
    out: set[int] = set()
    for number in wanted:
        if number in known:
            out.add(number)
            continue
        at = bisect.bisect_left(lines, number)
        if at > 0:
            out.add(lines[at - 1])
        if at < len(lines):
            out.add(lines[at])
    return out


# Top-level blocks of a single-file component, in the order Vue's parser meets them:
# a script is skipped whole, a comment hides what it wraps (to the end of the file
# when unterminated), and a style block (paired, or a self-closing ``src`` import)
# is live CSS.
_SFC_BLOCK = re.compile(
    r"<script\b.*?</script\b[^>]*>|<!--(?:.*?-->|.*\Z)"
    r"""|(<style\b(?:"[^"]*"|'[^']*'|[^>"'])*?/\s*>|<style\b.*?</style\b[^>]*>)""",
    re.DOTALL | re.IGNORECASE,
)


def active_styles(text: str) -> list[str]:
    """The live ``<style>`` blocks of a single-file component, tags included."""
    return [m.group(1) for m in _SFC_BLOCK.finditer(text) if m.group(1)]


def _sw_only_revision(diff_text: str) -> bool:
    """Whether the diff only rewrites the revision value in place: one line replaced by one, at the same line."""
    hunks = parse_hunks(diff_text)
    if len(hunks) != 1 or hunks[0][1] != 1 or hunks[0][3] != 1 or hunks[0][0] != hunks[0][2]:
        return False
    changed = [line for line in diff_text.splitlines()
               if line[:1] in "+-" and not line.startswith(("+++", "---"))]
    return len(changed) == 2 and all(_SW_REVISION.match(line) for line in changed)


def parse_hunks(diff_text: str) -> list[tuple[int, int, int, int]]:
    """``(old_start, old_count, new_start, new_count)`` per ``-U0`` hunk."""
    hunks = []
    for line in diff_text.splitlines():
        match = _HUNK.match(line)
        if match:
            old_start, old_count, new_start, new_count = match.groups()
            hunks.append((
                int(old_start),
                1 if old_count is None else int(old_count),
                int(new_start),
                1 if new_count is None else int(new_count),
            ))
    return hunks


def touched_old_lines(hunks: Iterable[tuple[int, int, int, int]]) -> set[int]:
    """Old-side lines a diff changes; a pure insertion touches the lines on both sides of it."""
    lines: set[int] = set()
    for old_start, old_count, _new_start, _new_count in hunks:
        if old_count:
            lines.update(range(old_start, old_start + old_count))
        else:
            # "-L,0" inserts after old line L.
            lines.update((old_start, old_start + 1))
    return {line for line in lines if line > 0}


def translate_lines(lines: Iterable[int], hunks: Iterable[tuple[int, int, int, int]]) -> set[int]:
    """Map lines of a later version back to an earlier one (``hunks`` from ``diff earlier later``).

    A line inside a changed hunk maps to the whole earlier side of that hunk
    (or its neighbours for a pure deletion), so code edited since the map was
    recorded still selects the tests that ran the code it replaced.
    """
    hunks = sorted(hunks, key=lambda h: h[2])
    out: set[int] = set()
    for line in lines:
        shift = 0
        mapped = None
        for old_start, old_count, new_start, new_count in hunks:
            if new_count and new_start <= line < new_start + new_count:
                mapped = (
                    set(range(old_start, old_start + old_count)) if old_count else {old_start, old_start + 1}
                )
                break
            # A zero count means "after line N": the next untouched line is N + 1.
            old_next = old_start + old_count if old_count else old_start + 1
            new_next = new_start + new_count if new_count else new_start + 1
            if new_next <= line:
                shift = old_next - new_next
            else:
                break
        out.update(mapped if mapped is not None else {line + shift})
    return {line for line in out if line > 0}


def _git(repo: Path, *args: str) -> str:
    import subprocess

    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (args[0], proc.stderr.strip()[:200]))
    return proc.stdout


def _show(repo: Path, rev: str, rel: str) -> str:
    try:
        return _git(repo, "show", f"{rev}:{rel}")
    except RuntimeError:
        return ""  # added or deleted on that side


def _touches_style(repo: Path, rel: str, old_rev: str, new_rev: str) -> bool:
    # Compare the live CSS itself, so wrapping a block in a comment (or removing
    # one) counts, and a comment or insertion outside every block does not.
    return active_styles(_show(repo, old_rev, rel)) != active_styles(_show(repo, new_rev, rel))


def select_for_diff(
    maps: Sequence[Mapping[str, Any]], *, repo: Path, base: str, head: str, all_ids: Iterable[str]
) -> dict[str, Any]:
    """What a coverage-map selection would run for ``base...head`` (advisory only).

    Mapped files select the tests that executed their changed lines. Every
    other file keeps the planner's rule; one that would run the full suite
    makes the whole selection full. A mapped file the map has never seen
    (new, or never executed by any test) falls back to the planner too.
    ``maps`` are the shard maps of one recording run (disjoint tests).
    """
    from tests.e2e import policy

    all_ids = list(all_ids)
    known = set(all_ids)
    commits = {m["commit"] for m in maps}
    if len(commits) != 1:
        raise ValueError("coverage maps come from different commits: %s" % ", ".join(sorted(commits)))
    (map_commit,) = commits
    # The commit comes from a downloaded artifact: it reaches git only as a SHA.
    if not isinstance(map_commit, str) or not _SHA.match(map_commit):
        raise ValueError("coverage map commit is not a full SHA: %r" % (map_commit,))
    for ref in (base, head):
        if not ref or ref.startswith("-"):
            raise ValueError("not a revision: %r" % (ref,))
    merge_base = _git(repo, "merge-base", base, head).strip()
    # Renames count both sides, so a production file moved into docs is
    # still a production change (its old path diffs as a deletion).
    changed = list(dict.fromkeys(
        policy.paths_from_name_status_z(_git(repo, "diff", "--name-status", "-z", f"{base}...{head}", "--"))
    ))
    tests: set[str] = set()
    fallback: list[str] = []
    files: dict[str, Any] = {}
    for rel in changed:
        if rel in _DERIVED or (rel == _SW and _sw_only_revision(_git(repo, "diff", "-U0", f"{base}...{head}", "--", rel))):
            # Rebuilt from the sources and diffed in CI: the source change selects.
            files[rel] = {"rule": "derived"}
            continue
        if is_mapped_kind(rel) and any(rel in m["files"] for m in maps):
            pr_hunks = parse_hunks(_git(repo, "diff", "-U0", f"{base}...{head}", "--", rel))
            wanted = touched_old_lines(pr_hunks)
            if rel.endswith(".vue") and _touches_style(repo, rel, merge_base, head):
                # The map does not measure CSS: a style change keeps the planner's full run.
                files[rel] = {"rule": "full", "why": "vue-style"}
                fallback.append(rel)
                continue
            # The PR's old side is the merge base; the map was recorded at map_commit.
            if merge_base != map_commit:
                drift = parse_hunks(_git(repo, "diff", "-U0", map_commit, merge_base, "--", rel))
                wanted = translate_lines(wanted, drift)
            if is_vue_source(rel):
                code = [span for m in maps for span in (m.get("code_lines") or {}).get(rel, ())]
                wanted = with_code_neighbours(wanted, code)
            picked = set().union(*(tests_for_lines(m, rel, wanted) for m in maps)) & known
            tests |= picked
            files[rel] = {"rule": "coverage-map", "tests": len(picked)}
            continue
        if policy.frontend_browser_change_requires_full_ci([rel]):
            # Shipped browser code the map does not measure (CSS, Vue, HTML):
            # the CI planner runs the full suite for it, so this does too.
            files[rel] = {"rule": "full", "why": "frontend-full"}
            fallback.append(rel)
            continue
        classified = policy.classify_affected_path(rel)
        if classified["skip"]:
            files[rel] = {"rule": "skip"}
            continue
        if classified["ci_full"] or classified["unmapped"]:
            files[rel] = {"rule": "full", "why": "+".join(classified["rules"]) or "unmapped"}
            fallback.append(rel)
            continue
        feature_ids = set(policy.select_features(all_ids, list(classified["features"])))
        tests |= feature_ids
        files[rel] = {"rule": "features", "features": list(classified["features"]), "tests": len(feature_ids)}
    if fallback:
        return {"mode": "full", "tests": len(all_ids), "total": len(all_ids), "full_because": fallback, "files": files}
    if not tests and all(f["rule"] in ("skip", "derived") for f in files.values()):
        return {"mode": "skip", "tests": 0, "total": len(all_ids), "files": files}
    tests |= set(policy.select_smoke(all_ids))
    return {"mode": "subset", "tests": len(tests), "total": len(all_ids), "selected": sorted(tests), "files": files}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="merge record files into one gzipped map")
    build.add_argument("records", type=Path, help="directory searched recursively for records-*.jsonl")
    build.add_argument("--commit", required=True)
    build.add_argument("--output", type=Path, required=True)
    select = sub.add_parser("select", help="what the map would run for base...head (advisory)")
    select.add_argument("maps", type=Path, nargs="+", help="the shard maps of one recording run")
    select.add_argument("--base", required=True)
    select.add_argument("--head", default="HEAD")
    check = sub.add_parser("check-run", help="whether a failed recording run still counts (exit 0) or not")
    check.add_argument("records", type=Path, help="the recording directory")
    args = parser.parse_args(argv)
    if args.command == "check-run":
        ok, why = check_run(args.records)
        print(why)
        return 0 if ok else 1
    if args.command == "select":
        maps = []
        for path in args.maps:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                maps.append(json.load(fh))
        from tests.e2e.run import discover_test_ids

        result = select_for_diff(maps, repo=REPO, base=args.base, head=args.head, all_ids=discover_test_ids())
        result.pop("selected", None)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    paths = sorted(args.records.rglob("records-*.jsonl"))
    if not paths:
        print("no coverage records found", file=sys.stderr)
        return 1
    coverage_map = build_map(paths, commit=args.commit, bundle=load_bundle_map())
    with gzip.open(args.output, "wt", encoding="utf-8") as fh:
        json.dump(coverage_map, fh, sort_keys=True, separators=(",", ":"))
    lines = sum(last - first + 1 for runs in coverage_map["files"].values() for first, last, _ in runs)
    print(
        f"E2E coverage map: {len(coverage_map['tests'])} tests, {len(coverage_map['files'])} files, "
        f"{lines} executed lines, {len(coverage_map['sets'])} distinct test sets"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
