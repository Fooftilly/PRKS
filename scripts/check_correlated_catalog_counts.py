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
3. Report the subquery only when it references a qualifier (``x.col``) that is
   declared by a ``FROM`` / ``JOIN`` of the *enclosing* ``SELECT`` and is not
   re-declared inside the subquery. That is the strong evidence of per-row
   correlation; ``WHERE person_id = ?`` or a ``GROUP BY`` derived table joined
   ``ON counts.parent_id = parent.id`` does not qualify.

Every hit has a structural identity: path, outer relation + alias, inner
relation(s), output alias, aggregate and a fingerprint of the normalized
subquery text (case/whitespace insensitive). Checked-in debt lives in
``scripts/correlated_catalog_counts_allowlist.json`` with its owning issue.

Rules (all evaluated against the working tree; ``--base`` supplies history):

- SQL-CATALOG-001: a hit with no matching allowlist entry fails.
- SQL-CATALOG-002: an allowlist entry that matches no current hit is stale
  and fails until the entry is removed, so the list only shrinks.
- SQL-CATALOG-003: an allowlist entry whose fingerprint does not exist in the
  production sources at ``--base`` fails. Grandfathering therefore covers only
  debt that already existed; adding a new query together with a new entry, or
  rewriting a grandfathered query into another correlated aggregate and
  re-blessing it, is rejected. Moving an unchanged query to another file keeps
  its fingerprint and passes.
- SQL-CATALOG-004: the allowlist file is malformed.

Reviewed escape hatch for a deliberate non-catalogue use (diagnostics, a
single-row read the heuristic cannot see is bounded): put a SQL comment
``-- prks-allow-correlated-count: <reason>`` inside the subquery. The reason is
required and the exemption covers only that subquery.

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


def _mask_sql(sql: str) -> str:
    """Blank quoted strings and comments (same length) so structure is safe."""
    out = list(sql)
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"'):
            j = i + 1
            while j < n:
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:
                        j += 2
                        continue
                    break
                j += 1
            for k in range(i + 1, min(j, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


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


def _declared(masked: str) -> dict[str, str]:
    """Map every FROM/JOIN qualifier (alias and table name) to ``table alias``."""
    names: dict[str, str] = {}
    for m in _RELATION_RE.finditer(masked):
        table = m.group(1).lower()
        if table in _KEYWORDS:
            continue
        alias = (m.group(2) or "").lower()
        if alias in _KEYWORDS:
            alias = ""
        # An aliased relation is only addressable by its alias (SQLite hides
        # the table name), so ``FROM folders c`` does not shadow ``folders.id``.
        names.setdefault(alias or table, f"{table} {alias}".strip())
    return names


def _aggregate_in_select_list(body_masked: str) -> str | None:
    """Aggregate called in the subquery's own top-level select list."""
    top = _top_level(body_masked[1:-1])
    m = re.match(r"\s*select\b(.*?)(?:\bfrom\b|$)", top, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    for fm in re.finditer(rf"\b({_IDENT})\s*\(", m.group(1)):
        name = fm.group(1).lower()
        if name in AGGREGATES:
            return name.upper()
    return None


_STATEMENT_START_RE = re.compile(r"\s*(?:with|select)\b", re.IGNORECASE)


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


def _normalize(text: str) -> str:
    # Whitespace is collapsed to single spaces first, so an optional single
    # space around punctuation is enough (and cannot backtrack).
    text = re.sub(r"\s+", " ", text.strip().lower())
    return re.sub(r" ?([(),=<>!*+/-]) ?", r"\1", text)


def find_correlated_aggregates(sql: str) -> list[tuple[int, dict[str, str]]]:
    """Return ``(offset, facts)`` for each correlated aggregate subquery."""
    masked = _mask_sql(sql)
    results: list[tuple[int, dict[str, str]]] = []
    for m in _SUBQUERY_START_RE.finditer(masked):
        open_idx = m.start()
        close = _match_paren(masked, open_idx)
        if close < 0:
            continue
        body_masked = masked[open_idx : close + 1]
        body_raw = sql[open_idx : close + 1]
        aggregate = _aggregate_in_select_list(body_masked)
        if aggregate is None:
            continue
        if MARKER in body_raw:
            idx = body_raw.find(MARKER)
            reason = body_raw[idx + len(MARKER) :].split("\n", 1)[0].strip()
            if reason:
                continue
        inner = _declared(body_masked)
        enc_start, enc_end = _enclosing_span(masked, open_idx, close)
        # Enclosing statement text with every nested (...) blanked, so sibling
        # subqueries do not contribute their own aliases to the outer scope.
        outer_text = _top_level(masked[enc_start:enc_end])
        # The enclosing statement must be a SELECT (skip UPDATE ... SET x =).
        if not re.match(r"\s*(?:with\b.*?)?select\b", outer_text, re.IGNORECASE | re.DOTALL):
            continue
        outer = _declared(outer_text)
        refs = {
            q.group(1).lower()
            for q in _QUALIFIED_RE.finditer(body_masked)
        }
        correlated = sorted(r for r in refs if r in outer and r not in inner)
        if not correlated:
            continue
        after = _AS_ALIAS_RE.match(masked, close + 1)
        output_alias = ""
        if after and after.group(1).lower() not in _KEYWORDS:
            output_alias = after.group(1).lower()
        inner_tables = sorted(
            {label.split(" ")[0] for label in inner.values()}
        )
        facts = {
            "outer": ", ".join(sorted({outer[r] for r in correlated})),
            "inner": ", ".join(inner_tables),
            "output_alias": output_alias,
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

    base_fingerprints = {h.fingerprint for h in base_hits}
    allowed: Counter[tuple[str, ...]] = Counter()
    for idx, entry in enumerate(valid):
        key = tuple(entry[k] for k in _ENTRY_KEYS)
        allowed[key] += 1
        if entry["fingerprint"] not in base_fingerprints:
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
