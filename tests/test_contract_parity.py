"""Cross-runtime semantic contract parity tests.

These tests protect deliberate Python/JavaScript mirrors that cannot share a
runtime constant. They are intentionally about application contracts, not
generic textual duplication.
"""
from __future__ import annotations

import inspect
import re
import unittest
from pathlib import Path

from backend.db_manager import PRKSDatabase, PRKS_BIBTEX_EXPORT_FIELD_IDS
from backend.work_metadata_sync import WORK_STATUSES


ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# Slash after these tokens introduces a RegExp literal, not division.
_JS_REGEX_PREV = frozenset("=(,[{;:!&|?~+-*%^}<>")
_JS_REGEX_PREV_KEYWORDS = frozenset({
    "return", "typeof", "case", "do", "else", "in", "instanceof",
    "new", "delete", "void", "throw", "yield", "await",
})


def _js_line_comment_start(source: str, line_start: int, end: int) -> int:
    """Index of a real `//` comment start in [line_start, end), or -1.

    Skips string/template literals and block comments so `//` inside quotes
    is not treated as a line comment.
    """
    i = line_start
    while i < end:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < end else ""
        if ch in ("'", '"', "`"):
            i = min(_scan_js_quoted_end(source, i), end)
            continue
        if ch == "/" and nxt == "*":
            i = min(_scan_js_block_comment_end(source, i), end)
            continue
        if ch == "/" and nxt == "/":
            return i
        i += 1
    return -1


def _js_prev_non_comment(source: str, index: int) -> int:
    """Index of the nearest non-trivia char before index, or -1.

    Skips whitespace and block/line comments so RegExp context is taken from
    the preceding code token (e.g. `return /* note */ /"/g`).
    """
    j = index - 1
    while j >= 0:
        while j >= 0 and source[j] in " \t\r\n":
            j -= 1
        if j < 0:
            return -1
        if j >= 1 and source[j - 1] == "*" and source[j] == "/":
            # Walk back to the matching `/*`.
            k = j - 2
            while k >= 1:
                if source[k - 1] == "/" and source[k] == "*":
                    j = k - 2
                    break
                k -= 1
            else:
                return j
            continue
        # If this line has a real `//` comment before j, skip it.
        line_start = source.rfind("\n", 0, j + 1) + 1
        comment = _js_line_comment_start(source, line_start, j + 1)
        if comment != -1:
            j = comment - 1
            continue
        return j
    return -1


def _js_regex_allowed(source: str, index: int) -> bool:
    """True when `/` at index starts a RegExp literal rather than division."""
    j = _js_prev_non_comment(source, index)
    if j < 0:
        return True
    if source[j] in _JS_REGEX_PREV:
        return True
    k = j
    while k >= 0 and (source[k].isalnum() or source[k] in "_$"):
        k -= 1
    word = source[k + 1 : j + 1]
    # Property access like `obj.of / x` is division; bare keyword is RegExp.
    return word in _JS_REGEX_PREV_KEYWORDS and (k < 0 or source[k] != ".")


def _blank_keep_newlines(text: str) -> str:
    return "".join("\n" if c == "\n" else " " for c in text)


def _scan_js_quoted_end(source: str, start: int) -> int:
    """Return index just past a quote/template that begins at start."""
    quote = source[start]
    i = start + 1
    n = len(source)
    while i < n:
        c = source[i]
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if c == quote:
            return i + 1
        i += 1
    return n


def _scan_js_line_comment_end(source: str, start: int) -> int:
    i = start + 2
    n = len(source)
    while i < n and source[i] != "\n":
        i += 1
    return i


def _scan_js_block_comment_end(source: str, start: int) -> int:
    i = start + 2
    n = len(source)
    while i < n:
        if source[i] == "*" and i + 1 < n and source[i + 1] == "/":
            return i + 2
        i += 1
    return n


def _scan_js_regex_literal(source: str, start: int) -> int:
    """Return index just past a RegExp literal that begins at start (`/`)."""
    i = start + 1
    n = len(source)
    in_class = False
    while i < n:
        c = source[i]
        if c == "\n":
            return start + 1
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if in_class:
            in_class = c != "]"
            i += 1
            continue
        if c == "[":
            in_class = True
            i += 1
            continue
        if c == "/":
            i += 1
            while i < n and source[i].isalpha():
                i += 1
            return i
        i += 1
    return start + 1


def _emit_optional_blank(out: list[str], chunk: str, blank: bool) -> None:
    out.append(_blank_keep_newlines(chunk) if blank else chunk)


def _emit_string_region(out: list[str], chunk: str, blank_strings: bool) -> None:
    if not blank_strings:
        out.append(chunk)
        return
    if len(chunk) < 2:
        out.append(_blank_keep_newlines(chunk))
        return
    out.append(chunk[0])
    out.append(_blank_keep_newlines(chunk[1:-1]))
    out.append(chunk[-1])


def _scan_js_regions(source: str, *, blank_comments: bool, blank_strings: bool) -> str:
    """Rewrite JS source with optional comment/string blanking.

    Comments are blanked (newlines kept). String/template interiors can be
    blanked so declaration searches never match prose inside quotes. RegExp
    literals are recognized so `/\"/g` does not open a string. Offsets are
    preserved so spans remain usable on a sibling rewrite that keeps strings.
    """
    out: list[str] = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if ch in ("'", '"', "`"):
            end = _scan_js_quoted_end(source, i)
            _emit_string_region(out, source[i:end], blank_strings)
            i = end
            continue
        if ch == "/" and nxt == "/":
            end = _scan_js_line_comment_end(source, i)
            _emit_optional_blank(out, source[i:end], blank_comments)
            i = end
            continue
        if ch == "/" and nxt == "*":
            end = _scan_js_block_comment_end(source, i)
            _emit_optional_blank(out, source[i:end], blank_comments)
            i = end
            continue
        if ch == "/" and _js_regex_allowed(source, i):
            end = _scan_js_regex_literal(source, i)
            out.append(source[i:end])
            i = end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def active_js_source(source: str) -> str:
    """Comment-free JavaScript with string contents preserved."""
    return _scan_js_regions(source, blank_comments=True, blank_strings=False)


def declaration_search_surface(source: str) -> str:
    """Comment-free JavaScript with string interiors blanked for matching."""
    return _scan_js_regions(source, blank_comments=True, blank_strings=True)


def _require_exactly_one_span(
    source: str,
    pattern: str,
    label: str,
    *,
    flags: int = 0,
) -> re.Match[str]:
    """Find exactly one active declaration; return a match into comment-free source."""
    active = active_js_source(source)
    surface = declaration_search_surface(source)
    spans = [m.span() for m in re.finditer(pattern, surface, flags)]
    if not spans:
        raise AssertionError(f"could not find active JavaScript {label}")
    if len(spans) != 1:
        raise AssertionError(
            f"expected exactly one active JavaScript {label}, found {len(spans)}"
        )
    start, end = spans[0]
    match = re.match(pattern, active[start:end], flags)
    if match is None:
        raise AssertionError(f"active JavaScript {label} failed to reparse")
    return match


def js_string_array(source: str, name: str) -> tuple[str, ...]:
    pattern = (
        r"const\s+" + re.escape(name) +
        r"\s*=\s*(?:Object\.freeze\(\s*)?\[(.*?)\]\s*\)?\s*;"
    )
    match = _require_exactly_one_span(
        source, pattern, f"array {name}", flags=re.S,
    )
    return tuple(re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)))


def js_true_object_keys(source: str, name: str) -> tuple[str, ...]:
    match = _require_exactly_one_span(
        source,
        r"const\s+" + re.escape(name) + r"\s*=\s*\{(.*?)\}\s*;",
        f"object {name}",
        flags=re.S,
    )
    keys: list[str] = []
    for quoted, bare in re.findall(
        r"^\s*(?:['\"]([^'\"]+)['\"]|([A-Za-z_$][\w$-]*))\s*:\s*true\s*,?\s*$",
        match.group(1),
        re.M,
    ):
        keys.append(quoted or bare)
    if not keys:
        raise AssertionError(f"could not parse JavaScript object {name}")
    return tuple(keys)


def js_bibtex_field_ids(source: str) -> tuple[str, ...]:
    match = _require_exactly_one_span(
        source,
        r"const\s+PRKS_BIBTEX_EXPORT_FIELD_DEFS\s*=\s*\[(.*?)\]\s*;",
        "BibTeX Settings field definitions",
        flags=re.S,
    )
    return tuple(re.findall(r"\[\s*['\"]([^'\"]+)['\"]\s*,", match.group(1)))


def js_recent_limit(source: str) -> int:
    match = _require_exactly_one_span(
        source,
        r"const\s+RECENT_LIMIT\s*=\s*(\d+)\s*;",
        "RECENT_LIMIT",
    )
    return int(match.group(1))


class ContractParityTests(unittest.TestCase):
    def test_commented_out_declarations_are_ignored(self):
        """Stale commented copies must not satisfy a protected contract."""
        poisoned = (
            "// const RECENT_LIMIT = 30;\n"
            "/* const RECENT_LIMIT = 30; */\n"
            "const esc = s.replace(/\"/g, '&quot;').replace(/'/g, '&#39;');\n"
            "const arrow = s => /\"/.test(s);\n"
            "function check(s) { return /'/g; }\n"
            "function noted(s) { return /* note */ /\"/g; }\n"
            "function pathy(s) { const path = 'root//child'; return /\"/g; }\n"
            "const RECENT_LIMIT = 25;\n"
            "const msg = 'const RECENT_LIMIT = 30;';\n"
        )
        self.assertEqual(js_recent_limit(poisoned), 25)

        with self.assertRaises(AssertionError) as cm:
            js_recent_limit(
                "// const RECENT_LIMIT = 30;\n"
                "const RECENT_LIMIT = 25;\n"
                "const RECENT_LIMIT = 40;\n"
            )
        self.assertIn("exactly one", str(cm.exception))

        with self.assertRaises(AssertionError) as cm:
            js_recent_limit("// const RECENT_LIMIT = 30;\n")
        self.assertIn("could not find", str(cm.exception))

    def test_work_status_registries_match_backend_contract(self):
        canonical = tuple(WORK_STATUSES)
        mirrors = {
            "work metadata sync": js_string_array(
                read("frontend/js/work-metadata-state.js"), "WORK_STATUSES"
            ),
            "navigation/progress links": js_string_array(
                read("frontend/js/navigation.js"), "PRKS_PROGRESS_STATUS_VALUES"
            ),
            "Progress page": js_string_array(
                read("frontend/js/components/progress.js"), "PRKS_PROGRESS_STATUSES"
            ),
        }
        for label, actual in mirrors.items():
            with self.subTest(label=label):
                self.assertEqual(actual, canonical)

    def test_bibtex_settings_fields_match_backend_accepted_fields(self):
        frontend_ids = js_bibtex_field_ids(read("frontend/js/app.js"))
        self.assertEqual(frontend_ids, tuple(PRKS_BIBTEX_EXPORT_FIELD_IDS))

    def test_tile_route_fallback_matches_navigation_policy(self):
        navigation = js_true_object_keys(
            read("frontend/js/navigation.js"), "PRKS_TILE_ROUTE_NAMES"
        )
        workspace_fallback = js_true_object_keys(
            read("frontend/js/workspace-tabs.js"), "TILE_ROUTE_NAMES"
        )
        # Runtime policy is key membership only; declaration order is not part
        # of the contract.
        self.assertEqual(frozenset(workspace_fallback), frozenset(navigation))

    def test_recent_projection_limit_matches_server_default(self):
        frontend_limit = js_recent_limit(read("frontend/js/work-open-state.js"))

        param = inspect.signature(PRKSDatabase.get_recent_browse).parameters["limit"]
        self.assertIsInstance(param.default, int)
        self.assertEqual(frontend_limit, param.default)


if __name__ == "__main__":
    unittest.main()
