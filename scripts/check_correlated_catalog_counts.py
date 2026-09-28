#!/usr/bin/env python3
"""Ratchet against new correlated per-row catalogue COUNT SQL (#192).

Catalogue/list reads keep reintroducing the per-row scalar aggregate shape::

    SELECT p.*,
           (SELECT COUNT(*) FROM playlist_items i WHERE i.playlist_id = p.id)
               AS item_count
    FROM playlists p

SQLite runs that inner COUNT once per outer row. The preferred direction is to
aggregate once (``GROUP BY`` in a CTE or joined derived table) and join it.

Heuristic (bounded; not a SQL parser):

1. Take string literals (plain, implicitly concatenated and f-strings, with
   interpolations blanked) from production Python: ``backend/**/*.py`` and
   ``prks_app.py``. Docstrings are skipped.
2. In each literal, find parenthesized ``(SELECT ...)`` subqueries whose own
   top-level select list calls a counting aggregate (``COUNT``, ``SUM`` or
   ``TOTAL``, including ``COUNT(DISTINCT ...)``).
3. Report the subquery when a qualifier (``x.col``) inside it is not declared
   by any of its own scopes (each nested ``(SELECT ...)`` resolves against its
   own top-level ``FROM`` / ``JOIN``, then outward) and either:

   - is declared by a ``FROM`` / ``JOIN`` of the *enclosing* ``SELECT``
     (a table, or a ``(SELECT ...) alias`` derived table); or
   - the literal is a bare projection *fragment* with no statement of its own
     (a helper returning ``"(SELECT COUNT(*) ... WHERE c.pid = {alias}.id)"``
     for a caller to splice into its SELECT). Its outer is ``fragment:<q>``.

   That is the strong evidence of per-row correlation; ``WHERE person_id = ?``
   or a ``GROUP BY`` derived table joined ``ON counts.parent_id = parent.id``
   does not qualify. ``UPDATE ... SET n = (SELECT COUNT ...)`` is out of scope.

Every hit has a structural identity: path, outer relation + alias, inner
relation(s), output alias, aggregate and a fingerprint of the normalized
subquery text (case/whitespace insensitive, SQL comments removed). Checked-in
debt lives in ``scripts/correlated_catalog_counts_allowlist.json`` with its
owning issue.

Rules (all evaluated against the working tree; ``--base`` supplies history):

- SQL-CATALOG-001: a hit with no matching allowlist entry fails.
- SQL-CATALOG-002: an allowlist entry that matches no current hit is stale
  and fails until the entry is removed, so the list only shrinks.
- SQL-CATALOG-003: an allowlist entry whose structural identity *minus path*
  (outer, inner, output alias, aggregate, fingerprint) did not exist in the
  production sources at ``--base`` fails. Occurrences are counted: each entry
  consumes one historical occurrence, so N entries need N base occurrences.
  Grandfathering therefore covers only debt that already existed; adding a
  new query with a new entry, copying grandfathered SQL, transplanting it
  under a different outer relation, or rewriting it and re-blessing it are all
  rejected. Moving an unchanged query to another file passes.
- SQL-CATALOG-004: the allowlist file is malformed.

Reviewed escape hatch for a deliberate non-catalogue use (diagnostics, a
single-row read the heuristic cannot see is bounded): put a SQL comment
``-- prks-allow-correlated-count: <reason>`` (or ``/* ... */``) directly in the
subquery, not in a nested one. Marker text in quoted values does not count.
The reason is required and the exemption covers only that subquery.

A base revision that cannot be inspected fails closed (exit 2).
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

ALLOWLIST_RELPATH = "scripts/correlated_catalog_counts_allowlist.json"
SCAN_PREFIX = "backend/"
SCAN_FILES = ("prks_app.py",)
MARKER = "prks-allow-correlated-count:"
AGGREGATES = frozenset({"count", "sum", "total"})

EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d6927f8d2765b5"
# HEAD, hex SHAs and simple ref names all fit this single charset rule; ranges
# and leading '-' are rejected separately in sanitize_git_revision().
_SAFE_GIT_REV_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
_FULL_SHA_RE = re.compile(r"\A[0-9a-fA-F]{40}\Z")

GUIDANCE = (
    "aggregate once with GROUP BY / a set-based CTE or joined aggregate "
    "instead of a scalar correlated COUNT per outer row, e.g. "
    "LEFT JOIN (SELECT parent_id, COUNT(*) AS n FROM child GROUP BY parent_id) "
    "counts ON counts.parent_id = parent.id"
)

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
# FROM/JOIN <table> [AS] <alias>; the optional alias must not be a keyword.
_RELATION_RE = re.compile(
    rf"\b(?:from|join)\s+(?:(?:main|temp)\.)?({_IDENT})"
    rf"(?:\s+(?:as\s+)?({_IDENT}))?",
    re.IGNORECASE,
)
# FROM/JOIN (<derived table>) [AS] <alias>. Only meaningful on _top_level()
# text, where the derived table's body is already blanked to spaces.
_DERIVED_RE = re.compile(
    rf"\b(?:from|join)\s*\(\s*\)\s*(?:as\s+)?({_IDENT})", re.IGNORECASE
)
# The same for a later FROM-list item; applied only inside a FROM list, since
# a select-list ", (SELECT ...) AS n" has the same blanked shape.
_COMMA_DERIVED_RE = re.compile(
    rf",\s*\(\s*\)\s*(?:as\s+)?({_IDENT})", re.IGNORECASE
)
# Later items of a comma-separated FROM list: ", <table> [AS] <alias>".
_COMMA_RELATION_RE = re.compile(
    rf",\s*(?:(?:main|temp)\.)?({_IDENT})(?:\s+(?:as\s+)?({_IDENT}))?",
    re.IGNORECASE,
)
# Where a FROM list ends (the next clause or join).
_FROM_LIST_END_RE = re.compile(
    r"\b(?:where|group|order|having|limit|window|union|except|intersect|"
    r"join|left|right|inner|cross|natural|full|on|using)\b",
    re.IGNORECASE,
)
_QUALIFIED_RE = re.compile(rf"\b({_IDENT})\s*\.\s*(?:{_IDENT}|\*)")
_AS_ALIAS_RE = re.compile(rf"\s*(?:as\s+)?({_IDENT})", re.IGNORECASE)
_SUBQUERY_START_RE = re.compile(r"\(\s*select\b", re.IGNORECASE)
_KEYWORDS = frozenset(
    """
    select from where join left right inner outer cross full natural on using
    group order by having limit offset union all except intersect as and or not
    in is null exists case when then else end with recursive values set distinct
    collate asc desc nocase between like glob escape window over partition
    """.split()
)


class DiscoveryError(RuntimeError):
    """Git/base/allowlist resolution failed; must not look like a clean pass."""


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    outer: str
    inner: str
    output_alias: str
    aggregate: str
    fingerprint: str
    snippet: str

    @property
    def identity(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.path,
            self.outer,
            self.inner,
            self.output_alias,
            self.aggregate,
            self.fingerprint,
        )


@dataclass(frozen=True)
class Finding:
    code: str
    location: str
    message: str

    def render(self) -> str:
        return f"{self.code} {self.location}: {self.message}"


# --------------------------------------------------------------------------
# SQL heuristics
# --------------------------------------------------------------------------


def _mask_sql(sql: str, comments: list[tuple[int, str]] | None = None) -> str:
    """Blank quoted strings and comments (same length) so structure is safe.

    When ``comments`` is given, every ``--`` / ``/* */`` comment is appended
    to it as ``(start_offset, text)``.
    """
    out = list(sql)
    i = 0
    n = len(sql)
    while i < n:
        if sql[i] in ("'", '"'):
            j = _quote_end(sql, i)
            _blank(out, i + 1, j)
            i = j + 1
            continue
        j = _comment_end(sql, i)
        if j < 0:
            i += 1
            continue
        if comments is not None:
            comments.append((i, sql[i:j]))
        _blank(out, i, j)
        i = j
    return "".join(out)


def _quote_end(sql: str, i: int) -> int:
    """Offset of the quote closing the literal opened at ``i`` (``''`` escapes)."""
    quote = sql[i]
    j = i + 1
    n = len(sql)
    while j < n:
        if sql[j] == quote:
            if j + 1 < n and sql[j + 1] == quote:
                j += 2
                continue
            return j
        j += 1
    return n


def _comment_end(sql: str, i: int) -> int:
    """End offset of a ``--`` / ``/* */`` comment starting at ``i``, else -1."""
    n = len(sql)
    if sql.startswith("--", i):
        j = sql.find("\n", i)
        return n if j < 0 else j
    if sql.startswith("/*", i):
        j = sql.find("*/", i + 2)
        return n if j < 0 else j + 2
    return -1


def _blank(chars: list[str], start: int, end: int) -> None:
    """Replace ``chars[start:end]`` with spaces, keeping newlines (offsets)."""
    for k in range(start, min(end, len(chars))):
        if chars[k] != "\n":
            chars[k] = " "


def _match_paren(masked: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(masked)):
        c = masked[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _top_level(masked: str) -> str:
    """Blank everything nested inside parentheses (keeps offsets)."""
    out = []
    depth = 0
    for c in masked:
        if c == "(":
            depth += 1
            out.append(c if depth == 1 else " ")
        elif c == ")":
            out.append(c if depth == 1 else " ")
            depth = max(0, depth - 1)
        else:
            out.append(c if depth == 0 or c == "\n" else " ")
    return "".join(out)


def _add_relation(names: dict[str, str], table: str, alias: str | None) -> None:
    table = table.lower()
    if table in _KEYWORDS:
        return
    alias = (alias or "").lower()
    if alias in _KEYWORDS:
        alias = ""
    # An aliased relation is only addressable by its alias (SQLite hides the
    # table name), so ``FROM folders c`` does not shadow ``folders.id``.
    names.setdefault(alias or table, f"{table} {alias}".strip())


def _from_list_tails(masked: str) -> list[str]:
    """Text after each top-level ``FROM`` up to its next clause keyword.

    Used to pick up the second and later items of ``FROM a x, b y``.
    """
    tails = []
    for m in re.finditer(r"\bfrom\b", masked, re.IGNORECASE):
        end = _FROM_LIST_END_RE.search(masked, m.end())
        tails.append(masked[m.end() : end.start() if end else len(masked)])
    return tails


def _declared(masked: str) -> dict[str, str]:
    """Map every FROM/JOIN qualifier (alias and table name) to ``table alias``."""
    names: dict[str, str] = {}
    for m in _RELATION_RE.finditer(masked):
        _add_relation(names, m.group(1), m.group(2))
    derived = [m.group(1) for m in _DERIVED_RE.finditer(masked)]
    for tail in _from_list_tails(masked):
        for m in _COMMA_RELATION_RE.finditer(tail):
            _add_relation(names, m.group(1), m.group(2))
        derived.extend(m.group(1) for m in _COMMA_DERIVED_RE.finditer(tail))
    for alias in derived:
        alias = alias.lower()
        if alias not in _KEYWORDS:
            names.setdefault(alias, f"(subquery) {alias}")
    return names


def _aggregate_in_select_list(body_masked: str) -> str | None:
    """Aggregate called in the subquery's own select list.

    The select list runs from ``SELECT`` to the subquery's first top-level
    ``FROM``. Aggregates wrapped in other expressions (``COALESCE(COUNT(*),
    0)``) count; ones inside a nested ``(SELECT ...)`` belong to that query.
    """
    inner = body_masked[1:-1]
    top = _top_level(inner)
    start = re.match(r"\s*select\b", top, re.IGNORECASE)
    if not start:
        return None
    end = re.search(r"\bfrom\b", top[start.end() :], re.IGNORECASE)
    stop = start.end() + end.start() if end else len(inner)
    chars = list(inner)
    for s, e in _nested_select_spans(inner):
        _blank(chars, s, e + 1)
    select_list = "".join(chars)[start.end() : stop]
    for fm in re.finditer(rf"\b({_IDENT})\s*\(", select_list):
        name = fm.group(1).lower()
        if name in AGGREGATES:
            return name.upper()
    return None


_STATEMENT_START_RE = re.compile(r"\s*(?:with|select)\b", re.IGNORECASE)
# Any of these at a literal's top level means it is (part of) a statement,
# not a bare projection fragment.
_STATEMENT_WORD_RE = re.compile(
    r"\b(?:select|from|update|insert|delete|replace|with|set|values|where)\b",
    re.IGNORECASE,
)


def _enclosing_span(masked: str, start: int, end: int) -> tuple[int, int]:
    """Innermost enclosing ``(SELECT ...)`` / ``(WITH ...)`` span, else the
    whole literal. Plain grouping parens such as ``WHERE NOT (...)`` are
    skipped so the subquery is attributed to the SELECT that owns it."""
    depth = 0
    for i in range(start - 1, -1, -1):
        c = masked[i]
        if c == ")":
            depth += 1
        elif c == "(":
            if depth == 0:
                if _STATEMENT_START_RE.match(masked, i + 1):
                    return i + 1, _match_paren(masked, i)
            else:
                depth -= 1
    return 0, len(masked)


def _normalize_sql_segment(text: str) -> str:
    # Whitespace is collapsed to single spaces first, so an optional single
    # space around punctuation is enough (and cannot backtrack).
    text = re.sub(r"\s+", " ", text.lower())
    return re.sub(r" ?([(),.=<>!*+/-]) ?", r"\1", text)


def _normalize(text: str) -> str:
    """Case/whitespace-insensitive SQL text; quoted values stay verbatim.

    ``kind = 'VISIBLE'`` vs ``'visible'`` can select different rows, so a
    change inside a quoted value must change the fingerprint.
    """
    parts: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        q = min((p for p in (text.find("'", i), text.find('"', i)) if p >= 0), default=n)
        parts.append(_normalize_sql_segment(text[i:q]))
        if q >= n:
            break
        end = _quote_end(text, q)
        parts.append(text[q : end + 1])
        i = end + 1
    return "".join(parts).strip()


def _nested_select_spans(text: str) -> list[tuple[int, int]]:
    """``(open, close)`` offsets of every ``(SELECT ...)`` inside ``text``."""
    spans = []
    for m in _SUBQUERY_START_RE.finditer(text):
        close = _match_paren(text, m.start())
        if close >= 0:
            spans.append((m.start(), close))
    return spans


def _has_marker(
    comments: list[tuple[int, str]],
    start: int,
    end: int,
    nested: list[tuple[int, int]],
) -> bool:
    """True when a SQL comment owned by ``[start, end]`` carries MARKER + reason.

    Comments inside a nested ``(SELECT ...)`` (absolute ``nested`` spans)
    belong to that nested query and do not exempt the enclosing aggregate.
    """
    for offset, text in comments:
        if not start <= offset <= end or MARKER not in text:
            continue
        if any(s <= offset <= e for s, e in nested):
            continue
        reason = text[text.find(MARKER) + len(MARKER) :]
        if reason.strip().rstrip("*/").strip():
            return True
    return False


def _unresolved_refs(body_masked: str) -> set[str]:
    """Qualifiers in the subquery that none of its own scopes declare.

    Each reference resolves against the innermost ``(SELECT ...)`` scope
    around it, then outward up to the aggregate subquery itself. Aliases are
    visible only at their own scope's top level, so ``EXISTS (SELECT 1 FROM
    works p ...)`` does not hide a sibling ``p.id`` that points outside.
    """
    inner_text = body_masked[1:-1]
    scopes: list[tuple[int, int, dict[str, str]]] = [
        (0, len(inner_text), _declared(_top_level(inner_text)))
    ]
    for s, e in _nested_select_spans(inner_text):
        scopes.append((s, e, _declared(_top_level(inner_text[s + 1 : e]))))
    unresolved: set[str] = set()
    for q in _QUALIFIED_RE.finditer(inner_text):
        name = q.group(1).lower()
        pos = q.start()
        if not any(s <= pos <= e and name in decl for s, e, decl in scopes):
            unresolved.add(name)
    return unresolved


def _outer_correlation(
    masked: str, open_idx: int, close: int, unresolved: set[str]
) -> tuple[list[str], str] | None:
    """``(correlated qualifiers, outer label)`` for the subquery, or None.

    None means the enclosing statement is out of scope (``UPDATE ... SET``
    and similar); an empty qualifier list means it is not correlated.
    """
    enc_start, enc_end = _enclosing_span(masked, open_idx, close)
    # Enclosing statement text with every nested (...) blanked, so sibling
    # subqueries do not contribute their own aliases to the outer scope.
    outer_text = _top_level(masked[enc_start:enc_end])
    if re.match(r"\s*(?:with\b.*?)?select\b", outer_text, re.IGNORECASE | re.DOTALL):
        outer = _declared(outer_text)
        correlated = sorted(r for r in unresolved if r in outer)
        return correlated, ", ".join(sorted({outer[r] for r in correlated}))
    if enc_start == 0 and not _STATEMENT_WORD_RE.search(outer_text):
        # A bare projection fragment for a caller to splice into its own
        # SELECT; any outward reference is the per-row correlation.
        correlated = sorted(unresolved)
        return correlated, "fragment:" + ", ".join(correlated)
    return None


def _output_alias(masked: str, close: int) -> str:
    after = _AS_ALIAS_RE.match(masked, close + 1)
    if after and after.group(1).lower() not in _KEYWORDS:
        return after.group(1).lower()
    return ""


def find_correlated_aggregates(sql: str) -> list[tuple[int, dict[str, str]]]:
    """Return ``(offset, facts)`` for each correlated aggregate subquery."""
    comments: list[tuple[int, str]] = []
    masked = _mask_sql(sql, comments)
    # Raw SQL with comments blanked: fingerprints ignore comment-only edits
    # but keep quoted values (which can change semantics).
    uncommented = list(sql)
    for offset, text in comments:
        _blank(uncommented, offset, offset + len(text))
    sql_uncommented = "".join(uncommented)
    results: list[tuple[int, dict[str, str]]] = []
    for m in _SUBQUERY_START_RE.finditer(masked):
        open_idx = m.start()
        close = _match_paren(masked, open_idx)
        if close < 0:
            continue
        body_masked = masked[open_idx : close + 1]
        body_raw = sql_uncommented[open_idx : close + 1]
        aggregate = _aggregate_in_select_list(body_masked)
        if aggregate is None:
            continue
        nested = [
            (open_idx + 1 + s, open_idx + 1 + e)
            for s, e in _nested_select_spans(body_masked[1:-1])
        ]
        if _has_marker(comments, open_idx, close, nested):
            continue
        resolved = _outer_correlation(
            masked, open_idx, close, _unresolved_refs(body_masked)
        )
        if not resolved or not resolved[0]:
            continue
        inner = _declared(_top_level(body_masked[1:-1]))
        inner_tables = sorted(
            {label.split(" ")[0] for label in inner.values()}
        )
        facts = {
            "outer": resolved[1],
            "inner": ", ".join(inner_tables),
            "output_alias": _output_alias(masked, close),
            "aggregate": aggregate,
            "fingerprint": hashlib.sha256(
                _normalize(body_raw).encode("utf-8")
            ).hexdigest()[:12],
            "snippet": re.sub(r"\s+", " ", body_raw.strip())[:160],
        }
        results.append((open_idx, facts))
    return results


# --------------------------------------------------------------------------
# Python literal extraction
# --------------------------------------------------------------------------


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def iter_sql_literals(source: str) -> list[tuple[int, str]]:
    """``(start_line, text)`` for string literals that mention SELECT."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    skip = _docstring_ids(tree)
    joined_parts: set[int] = set()
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            pieces = []
            for part in node.values:
                joined_parts.add(id(part))
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    pieces.append(part.value)
                else:
                    joined_parts.update(id(n) for n in ast.walk(part))
                    pieces.append("__expr__")
            text = "".join(pieces)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in skip or id(node) in joined_parts:
                continue
            text = node.value
        else:
            continue
        if re.search(r"\bselect\b", text, re.IGNORECASE):
            out.append((int(node.lineno), text))
    out.sort(key=lambda item: item[0])
    return out


def scan_source(relpath: str, source: str) -> list[Hit]:
    hits: list[Hit] = []
    for start_line, text in iter_sql_literals(source):
        for offset, facts in find_correlated_aggregates(text):
            hits.append(
                Hit(
                    path=relpath,
                    line=start_line + text[:offset].count("\n"),
                    outer=facts["outer"],
                    inner=facts["inner"],
                    output_alias=facts["output_alias"],
                    aggregate=facts["aggregate"],
                    fingerprint=facts["fingerprint"],
                    snippet=facts["snippet"],
                )
            )
    return hits


def in_scope(relpath: str) -> bool:
    return relpath.endswith(".py") and (
        relpath.startswith(SCAN_PREFIX) or relpath in SCAN_FILES
    )


# --------------------------------------------------------------------------
# Git / working tree
# --------------------------------------------------------------------------


def _git(repo: Path, args: list[str], what: str) -> str:
    cmd = ["git", "-C", str(repo), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise DiscoveryError(
            f"{what} failed: could not run git ({exc.__class__.__name__})"
        ) from exc
    if proc.returncode != 0:
        detail = [ln.strip() for ln in (proc.stderr or "").splitlines() if ln.strip()]
        raise DiscoveryError(
            f"{what} failed: `git {' '.join(args)}` exited {proc.returncode}"
            + (f" — {detail[0]}" if detail else "")
        )
    return proc.stdout or ""


def sanitize_git_revision(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise DiscoveryError("invalid --base: empty revision")
    if value.startswith("-") or ".." in value:
        raise DiscoveryError(f"invalid --base {value!r}: not a single revision")
    matched = _SAFE_GIT_REV_RE.fullmatch(value)
    if matched is None:
        raise DiscoveryError(
            f"invalid --base {value!r}: only HEAD, a hex SHA, or a simple "
            "ref name is accepted"
        )
    return matched.group(0)


def resolve_base(repo: Path, explicit: str | None) -> str:
    raw = explicit or (os.environ.get("PRKS_DIFF_BASE") or "").strip() or "HEAD"
    safe = sanitize_git_revision(raw)
    if safe == EMPTY_TREE_SHA:
        return EMPTY_TREE_SHA
    out = _git(
        repo,
        ["rev-parse", "--verify", "--end-of-options", f"{safe}^{{commit}}"],
        f"base revision check vs {safe}",
    )
    sha = (out.strip().splitlines() or [""])[0].strip()
    if _FULL_SHA_RE.fullmatch(sha) is None:
        raise DiscoveryError(f"base revision {safe} did not resolve to a commit")
    return sha


def scan_working_tree(repo: Path) -> list[Hit]:
    raw = _git(
        repo,
        ["ls-files", "--cached", "--others", "--exclude-standard", "--", SCAN_PREFIX, *SCAN_FILES],
        "production source discovery",
    )
    hits: list[Hit] = []
    for rel in sorted(set(raw.splitlines())):
        rel = rel.strip()
        path = repo / rel
        if not in_scope(rel) or not path.is_file():
            continue
        hits.extend(scan_source(rel, path.read_text(encoding="utf-8")))
    return hits


def scan_revision(repo: Path, sha: str) -> list[Hit]:
    if sha == EMPTY_TREE_SHA:
        return []
    raw = _git(
        repo,
        ["ls-tree", "-r", "--name-only", "--end-of-options", sha, "--", SCAN_PREFIX, *SCAN_FILES],
        f"base source discovery at {sha}",
    )
    hits: list[Hit] = []
    for rel in raw.splitlines():
        rel = rel.strip()
        if not in_scope(rel):
            continue
        source = _git(repo, ["show", f"{sha}:{rel}"], f"read {rel} at {sha}")
        hits.extend(scan_source(rel, source))
    return hits


# --------------------------------------------------------------------------
# Allowlist + ratchet
# --------------------------------------------------------------------------

_ENTRY_KEYS = ("path", "outer", "inner", "output_alias", "aggregate", "fingerprint")


def load_allowlist(repo: Path) -> list[dict]:
    path = repo / ALLOWLIST_RELPATH
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DiscoveryError(f"{ALLOWLIST_RELPATH} is not valid JSON ({exc})") from exc
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise DiscoveryError(f"{ALLOWLIST_RELPATH} must be an object with an 'entries' list")
    return entries


def _validate_entry(index: int, entry: object) -> str | None:
    if not isinstance(entry, dict):
        return "entry is not an object"
    for key in _ENTRY_KEYS:
        if not isinstance(entry.get(key), str):
            return f"missing string field {key!r}"
    issue = entry.get("issue")
    if issue is None:
        reason = entry.get("untracked_reason")
        if not isinstance(reason, str) or not reason.strip():
            return (
                "'issue' is null: a temporarily untracked entry needs a "
                "non-empty 'untracked_reason' (file an owner issue and backfill)"
            )
    elif not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
        return "'issue' must be a positive integer (owning tracking issue) or null"
    elif "untracked_reason" in entry:
        return "'untracked_reason' is only allowed while 'issue' is null"
    if not re.fullmatch(r"[0-9a-f]{12}", entry["fingerprint"]):
        return "fingerprint must be 12 lowercase hex characters"
    return None


def untracked_entries(entries: list[dict]) -> list[dict]:
    """Valid entries still waiting for an owning issue (reported every run)."""
    return [
        e
        for i, e in enumerate(entries)
        if _validate_entry(i, e) is None and e.get("issue") is None
    ]


def evaluate(hits: list[Hit], entries: list[dict], base_hits: list[Hit]) -> list[Finding]:
    findings: list[Finding] = []
    valid: list[dict] = []
    for idx, entry in enumerate(entries):
        problem = _validate_entry(idx, entry)
        if problem:
            findings.append(
                Finding("SQL-CATALOG-004", f"{ALLOWLIST_RELPATH}[{idx}]", problem)
            )
        else:
            valid.append(entry)

    # Multiset over the structural identity minus path: each valid entry
    # consumes one historical occurrence. Copying grandfathered SQL, or moving
    # the same subquery under a different outer relation, cannot raise the
    # exempted count above what existed at the base; a file move still can.
    base_provenance: Counter[tuple[str, ...]] = Counter(
        h.identity[1:] for h in base_hits
    )
    allowed: Counter[tuple[str, ...]] = Counter()
    for entry in valid:
        key = tuple(entry[k] for k in _ENTRY_KEYS)
        allowed[key] += 1
        if base_provenance[key[1:]] > 0:
            base_provenance[key[1:]] -= 1
        else:
            findings.append(
                Finding(
                    "SQL-CATALOG-003",
                    f"{ALLOWLIST_RELPATH} ({entry['path']} {entry['output_alias'] or entry['inner']})",
                    "allowlist entry does not match a correlated aggregate that "
                    "already existed at the comparison base; grandfathering "
                    "covers only historical debt. Do not add or re-bless "
                    f"new/rewritten debt — {GUIDANCE}",
                )
            )

    for hit in hits:
        if allowed[hit.identity] > 0:
            allowed[hit.identity] -= 1
            continue
        findings.append(
            Finding(
                "SQL-CATALOG-001",
                f"{hit.path}:{hit.line}",
                f"new correlated {hit.aggregate} aggregate performs per-row "
                f"catalogue work (outer {hit.outer!r} -> inner {hit.inner!r}"
                + (f", AS {hit.output_alias}" if hit.output_alias else "")
                + f", fingerprint {hit.fingerprint}).\n"
                f"  snippet: {hit.snippet}\n"
                f"  fix: {GUIDANCE}.\n"
                f"  escape hatch (reviewed, non-catalogue only): "
                f"`-- {MARKER} <reason>` inside the subquery.",
            )
        )

    for key, remaining in sorted(allowed.items()):
        for _ in range(remaining):
            findings.append(
                Finding(
                    "SQL-CATALOG-002",
                    f"{ALLOWLIST_RELPATH} ({key[0]})",
                    "stale allowlist entry: no current correlated aggregate "
                    f"matches outer={key[1]!r} inner={key[2]!r} "
                    f"output_alias={key[3]!r} fingerprint={key[5]}. If the "
                    "query was fixed, remove the entry; if it was rewritten "
                    "into another correlated aggregate, fix it set-based "
                    "instead of re-blessing it.",
                )
            )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--base",
        default=None,
        help="comparison revision (default: $PRKS_DIFF_BASE or HEAD)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print every current hit as allowlist-shaped JSON and exit",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()

    try:
        hits = scan_working_tree(root)
        if args.list:
            print(json.dumps(
                [
                    {k: getattr(h, k) for k in _ENTRY_KEYS} | {"line": h.line}
                    for h in hits
                ],
                indent=2,
            ))
            return 0
        base = resolve_base(root, args.base)
        base_hits = scan_revision(root, base)
        entries = load_allowlist(root)
    except DiscoveryError as exc:
        print(f"Correlated catalogue COUNT check failed closed: {exc}", file=sys.stderr)
        return 2

    findings = evaluate(hits, entries, base_hits)
    for entry in untracked_entries(entries):
        print(
            f"UNTRACKED {entry['path']} (outer {entry['outer']!r} -> inner "
            f"{entry['inner']!r}, AS {entry['output_alias'] or '-'}): no owning "
            f"issue yet — {entry['untracked_reason']}"
        )
    if findings:
        for finding in findings:
            print(finding.render())
            print()
        print(
            f"Correlated catalogue COUNT check failed: {len(findings)} "
            f"finding(s) vs {base}"
        )
        return 1
    print(
        f"Correlated catalogue COUNT check: OK ({len(hits)} grandfathered "
        f"hit(s), none new vs {base})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
