"""Arguments and Stances, their targets and cited sources on PostgreSQL (L-R-1 #634).

The rules are SQLite's (``backend/argument_sync.py``,
``backend/research_network.py``), imported rather than copied: the field
rules (``canonical_wire``), the refusals of a citation or target list (code,
message and status, in SQLite's order), the scopes (``argument-field /
["A-…","field"]``, ``argument-sources / A-…`` and ``argument-targets / A-…``,
each list one ordered value) and their decision tables. Only a change
advances a scope, and construction advances none.

What moves:

- **A refused create leaves nothing.** As on SQLite, the Argument row comes
  first (its id is decided before its lists, so an id of another Library is
  refused as such), and a refusal of its sources or targets removes it
  again: SQLite rolls back to a savepoint, which the sync handler's own
  transaction here does not need.
- **Acyclicity of targets** needs the whole response graph, so a target
  change takes the Library's Argument target lock and checks the targets'
  descendants in one recursive query. A create needs no lock: nothing can
  target an Argument before it exists.
- **Named rows are held.** A command holds the Works it cites, and the
  Positions and Arguments it targets, ``FOR KEY SHARE`` until COMMIT, so a
  delete of one waits for the command and then finds the reference
  (``POSITION_IN_USE``, ``ARGUMENT_TARGETED``), or the command finds it
  gone. A Research Note writer holds the Arguments its markup names the
  same way (:func:`share_referenced`), so ``ARGUMENT_IN_USE`` sees every
  note committed before the delete.
- **Citations carry a Version** (``docs/work-identity-model.md`` §8.5). The
  list keeps SQLite's wire shape, ``[{work_id, pages}]``. A replaced entry
  matching exactly one stored row by ``(work_id, pages)`` keeps that row's
  Version; a new one with a pinpoint is pinned to the Work's only Version
  (SQLite's trigger pins the primary, which is the only one a legacy
  client can reach). An entry whose Version the legacy shape cannot name,
  because it matches several stored rows or its Work has several Versions,
  refuses the whole list: ``argument_source_version_required``
  (``ARGUMENT_SOURCE_VERSION_REQUIRED``). A pinned citation follows its
  Version when the Version moves to another Work (``ON UPDATE CASCADE``).
- **A Work delete removes its citations** (:func:`release_work`) and
  advances ``argument-sources`` of each Argument that cited it. SQLite's
  cascade removes them without advancing, so a client holding the list
  could not see the change.
- **A deleted Argument** leaves every scope it owned one revision on, a
  never-edited one too, so a base observed before the delete is stale on an
  Argument created again under the id (SQLite leaves them as they were).
- **Orderings** SQLite leaves to storage (a list's equal ``order_index``,
  a Work's equally ordered Authors) are broken by id.

The lock order is the package's (``backend.domain.research``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, LiteralString, Optional, Sequence

from backend import argument_sync as rules
from backend import research_network as network
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.works import revisions
from backend.research_markup import ARGUMENT_ID_RE, parse_research_markup
from backend.research_network import ResearchError

FIELD_SCOPE_TYPE = rules.FIELD_SCOPE_TYPE
SOURCES_SCOPE_TYPE = rules.SOURCES_SCOPE_TYPE
TARGETS_SCOPE_TYPE = rules.TARGETS_SCOPE_TYPE

_NOT_FOUND = ("not_found", "Argument not found.", 404)
_VERSION_REQUIRED = ("argument_source_version_required",
                     "Choose which Version of the Work this citation cites.", 409)
_IN_USE = ("argument_in_use", "This Argument is still referenced in research notes.", 409)
_TARGETED = ("argument_targeted", "Another Argument or Stance still responds to this record.", 409)
_CYCLE = ("argument_cycle", "That target would create an Argument response cycle.", 409)

# SQLite's refusal vocabulary, and the one refusal only this backend makes.
_RULE_CODES = {**rules._RULE_CODES, _VERSION_REQUIRED[0]: "ARGUMENT_SOURCE_VERSION_REQUIRED"}
_RULE_STATUS = {**rules._RULE_STATUS, _VERSION_REQUIRED[0]: 409}

_TARGETS_LOCK_SQL: LiteralString = (
    "SELECT pg_advisory_xact_lock(hashtextextended('prks.argument_targets:' || %(library_id)s, 0))"
)
# SQLite's LOWER() folds ASCII letters only; an ASCII-only translate() under
# COLLATE "C" is the same order.
_LOWER_A: LiteralString = (
    "translate(a.name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') COLLATE \"C\""
)
_STAMPS: LiteralString = """
to_char(a.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
to_char(a.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at
"""
_ROW_SQL: LiteralString = (
    "SELECT a.id, a.name, a.kind, a.main_text," + _STAMPS
    + " FROM prks.arguments a WHERE a.library_id = %(library_id)s AND a.id = %(argument_id)s"
)
_ALL_SQL: LiteralString = (
    "SELECT a.id, a.name, a.kind," + _STAMPS + " FROM prks.arguments a"
    " WHERE a.library_id = %(library_id)s AND (%(kind)s::text IS NULL OR a.kind = %(kind)s)"
    " ORDER BY " + _LOWER_A + ", a.id COLLATE \"C\""
)
_VERDICTS_SQL: LiteralString = """
SELECT id, label, sort_order, enabled::int AS enabled FROM prks.argument_verdicts
WHERE enabled ORDER BY sort_order, id COLLATE "C"
"""
_ENABLED_VERDICTS_SQL: LiteralString = "SELECT id FROM prks.argument_verdicts WHERE enabled"
_LOCK_SQL: LiteralString = """
SELECT id FROM prks.arguments WHERE library_id = %(library_id)s AND id = %(argument_id)s FOR NO KEY UPDATE
"""
_LOCK_KEY_SQL: LiteralString = """
SELECT id FROM prks.arguments WHERE library_id = %(library_id)s AND id = %(argument_id)s FOR UPDATE
"""
_LOCK_MANY_SQL: LiteralString = """
SELECT id FROM prks.arguments WHERE library_id = %(library_id)s AND id = ANY(%(ids)s)
ORDER BY id COLLATE "C" FOR NO KEY UPDATE
"""
_SHARE_ARGUMENTS_SQL: LiteralString = """
SELECT id FROM prks.arguments WHERE library_id = %(library_id)s AND id = ANY(%(ids)s)
ORDER BY id COLLATE "C" FOR KEY SHARE
"""
_SHARE_POSITIONS_SQL: LiteralString = """
SELECT id FROM prks.positions WHERE library_id = %(library_id)s AND id = ANY(%(ids)s)
ORDER BY id COLLATE "C" FOR KEY SHARE
"""
_SHARE_WORKS_SQL: LiteralString = """
SELECT id FROM prks.works WHERE library_id = %(library_id)s AND id = ANY(%(ids)s)
ORDER BY id COLLATE "C" FOR KEY SHARE
"""
# How many Versions each Work has, and the one when it has one.
_VERSIONS_SQL: LiteralString = """
SELECT work_id, count(*) AS n, min(id) AS only_id FROM prks.manifestations
WHERE library_id = %(library_id)s AND work_id = ANY(%(ids)s) GROUP BY work_id
"""
_FIELDS_SQL: LiteralString = """
SELECT name, kind, main_text FROM prks.arguments WHERE library_id = %(library_id)s AND id = %(argument_id)s
"""
_INSERT_SQL: LiteralString = """
INSERT INTO prks.arguments (library_id, id, name, kind, main_text)
VALUES (%(library_id)s, %(argument_id)s, %(name)s, %(kind)s, %(main_text)s)
ON CONFLICT DO NOTHING
RETURNING id
"""
_SET_SQL: dict[str, LiteralString] = {
    "name": "UPDATE prks.arguments SET name = %(value)s, updated_at = now()"
            " WHERE library_id = %(library_id)s AND id = %(argument_id)s",
    "kind": "UPDATE prks.arguments SET kind = %(value)s, updated_at = now()"
            " WHERE library_id = %(library_id)s AND id = %(argument_id)s",
    "main_text": "UPDATE prks.arguments SET main_text = %(value)s, updated_at = now()"
                 " WHERE library_id = %(library_id)s AND id = %(argument_id)s",
}
_TOUCH_SQL: LiteralString = """
UPDATE prks.arguments SET updated_at = now() WHERE library_id = %(library_id)s AND id = %(argument_id)s
"""
_DELETE_SQL: LiteralString = "DELETE FROM prks.arguments WHERE library_id = %(library_id)s AND id = %(argument_id)s"

# -- sources ------------------------------------------------------------------
_SOURCE_ROWS_SQL: LiteralString = """
SELECT work_id, manifestation_id, pages FROM prks.argument_sources
WHERE library_id = %(library_id)s AND argument_id = %(argument_id)s
ORDER BY order_index, work_id COLLATE "C"
"""
_DELETE_SOURCES_SQL: LiteralString = """
DELETE FROM prks.argument_sources WHERE library_id = %(library_id)s AND argument_id = %(argument_id)s
"""
_INSERT_SOURCES_SQL: LiteralString = """
INSERT INTO prks.argument_sources (library_id, argument_id, order_index, work_id, manifestation_id, pages)
SELECT %(library_id)s, %(argument_id)s, s.i - 1, s.work_id, s.manifestation_id, s.pages
FROM unnest(%(work_ids)s::text[], %(manifestation_ids)s::text[], %(pages)s::text[])
     WITH ORDINALITY AS s(work_id, manifestation_id, pages, i)
"""
# ``_argument_sources``: Work title as the Work list shows it.
_SOURCES_SQL: LiteralString = """
SELECT s.argument_id, s.work_id, s.pages, s.order_index, COALESCE(m.title, w.title) AS work_title
FROM prks.argument_sources s
JOIN prks.works w ON w.library_id = s.library_id AND w.id = s.work_id
JOIN prks.manifestations m ON m.library_id = w.library_id AND m.id = w.primary_manifestation_id
WHERE s.library_id = %(library_id)s AND (%(argument_id)s::text IS NULL OR s.argument_id = %(argument_id)s)
ORDER BY s.argument_id COLLATE "C", s.order_index, COALESCE(m.title, w.title) COLLATE "C"
"""
_AUTHORS_SQL: LiteralString = """
SELECT r.work_id, p.id, p.first_name, p.last_name, r.role_type, r.credit_name, r.order_index
FROM prks.roles r
JOIN prks.works w ON w.library_id = r.library_id AND w.id = r.work_id
JOIN prks.persons p ON p.library_id = r.library_id AND p.id = r.person_id
WHERE r.library_id = %(library_id)s AND r.work_id = ANY(%(ids)s) AND r.role_type = 'Author'
  AND (r.manifestation_id IS NULL OR r.manifestation_id = w.primary_manifestation_id)
ORDER BY r.work_id COLLATE "C", r.order_index, p.last_name COLLATE "C", r.seq
"""
_CITING_SQL: LiteralString = """
SELECT DISTINCT argument_id FROM prks.argument_sources WHERE library_id = %(library_id)s AND work_id = %(work_id)s
"""
_RELEASE_WORK_SQL: LiteralString = """
DELETE FROM prks.argument_sources WHERE library_id = %(library_id)s AND work_id = %(work_id)s
RETURNING argument_id
"""

# -- targets ------------------------------------------------------------------
_TARGET_ROWS_SQL: LiteralString = """
SELECT type, id, verdict_id FROM (
  SELECT 'position' AS type, position_id AS id, verdict_id, order_index FROM prks.argument_target_positions
  WHERE library_id = %(library_id)s AND argument_id = %(argument_id)s
  UNION ALL
  SELECT 'argument' AS type, target_argument_id AS id, verdict_id, order_index FROM prks.argument_target_arguments
  WHERE library_id = %(library_id)s AND argument_id = %(argument_id)s
) t
ORDER BY order_index, type COLLATE "C", id COLLATE "C"
"""
_POSITION_TARGETS_SQL: LiteralString = """
SELECT t.argument_id, t.position_id AS id, t.verdict_id, t.order_index, v.label AS verdict_label, p.name
FROM prks.argument_target_positions t
JOIN prks.positions p ON p.library_id = t.library_id AND p.id = t.position_id
JOIN prks.argument_verdicts v ON v.id = t.verdict_id
WHERE t.library_id = %(library_id)s AND (%(argument_id)s::text IS NULL OR t.argument_id = %(argument_id)s)
ORDER BY t.argument_id COLLATE "C", t.order_index, t.position_id COLLATE "C"
"""
_ARGUMENT_TARGETS_SQL: LiteralString = """
SELECT t.argument_id, t.target_argument_id AS id, t.verdict_id, t.order_index, v.label AS verdict_label,
       a.name, a.kind
FROM prks.argument_target_arguments t
JOIN prks.arguments a ON a.library_id = t.library_id AND a.id = t.target_argument_id
JOIN prks.argument_verdicts v ON v.id = t.verdict_id
WHERE t.library_id = %(library_id)s AND (%(argument_id)s::text IS NULL OR t.argument_id = %(argument_id)s)
ORDER BY t.argument_id COLLATE "C", t.order_index, t.target_argument_id COLLATE "C"
"""
_RESPONSES_SQL: LiteralString = (
    "SELECT a.id, a.name, a.kind, t.verdict_id, v.label AS verdict_label"
    " FROM prks.argument_target_arguments t"
    " JOIN prks.arguments a ON a.library_id = t.library_id AND a.id = t.argument_id"
    " JOIN prks.argument_verdicts v ON v.id = t.verdict_id"
    " WHERE t.library_id = %(library_id)s AND t.target_argument_id = %(argument_id)s"
    " ORDER BY " + _LOWER_A + ", a.id COLLATE \"C\""
)
_RESPONSE_COUNTS_SQL: LiteralString = """
SELECT target_argument_id AS id, count(*) AS n FROM prks.argument_target_arguments
WHERE library_id = %(library_id)s GROUP BY target_argument_id
"""
_DELETE_TARGETS_SQL: tuple[LiteralString, ...] = (
    "DELETE FROM prks.argument_target_positions WHERE library_id = %(library_id)s AND argument_id = %(argument_id)s",
    "DELETE FROM prks.argument_target_arguments WHERE library_id = %(library_id)s AND argument_id = %(argument_id)s",
)
_INSERT_POSITION_TARGETS_SQL: LiteralString = """
INSERT INTO prks.argument_target_positions (library_id, argument_id, position_id, verdict_id, order_index)
SELECT %(library_id)s, %(argument_id)s, t.id, t.verdict_id, t.order_index
FROM unnest(%(ids)s::text[], %(verdicts)s::text[], %(orders)s::int[]) AS t(id, verdict_id, order_index)
"""
_INSERT_ARGUMENT_TARGETS_SQL: LiteralString = """
INSERT INTO prks.argument_target_arguments (library_id, argument_id, target_argument_id, verdict_id, order_index)
SELECT %(library_id)s, %(argument_id)s, t.id, t.verdict_id, t.order_index
FROM unnest(%(ids)s::text[], %(verdicts)s::text[], %(orders)s::int[]) AS t(id, verdict_id, order_index)
"""
# Whether the Argument is one of the new targets or something one of them
# answers, however indirectly.
_CYCLE_SQL: LiteralString = """
WITH RECURSIVE down(id) AS (
  SELECT unnest(%(target_ids)s::text[])
  UNION
  SELECT t.target_argument_id FROM prks.argument_target_arguments t JOIN down ON t.argument_id = down.id
  WHERE t.library_id = %(library_id)s
)
SELECT 1 AS found FROM down WHERE id = %(argument_id)s LIMIT 1
"""
_TARGETED_SQL: LiteralString = """
SELECT 1 AS found FROM prks.argument_target_arguments
WHERE library_id = %(library_id)s AND target_argument_id = %(argument_id)s LIMIT 1
"""

# -- notes --------------------------------------------------------------------
_RESEARCH_NOTES_SQL: LiteralString = """
SELECT body FROM prks.work_notes
WHERE library_id = %(library_id)s AND kind = 'research' AND strpos(body, '[[argument:') > 0
  AND strpos(body, %(argument_id)s) > 0
"""


class ForeignArgumentId(Exception):
    """The id is an Argument of another Library."""


@dataclass(frozen=True)
class Citation:
    work_id: str
    manifestation_id: Optional[str]
    pages: str


@dataclass(frozen=True)
class Target:
    type: str
    id: str
    verdict_id: str
    order_index: int


def _refuse(rule: tuple[str, str, int]) -> ResearchError:
    return ResearchError(*rule)


def _usable(entity_id: str) -> bool:
    # PostgreSQL text cannot hold NUL; no stored id does.
    return bool(entity_id) and "\x00" not in entity_id


def _found(scope: LibraryScope, sql: LiteralString, ids: Sequence[str]) -> set[str]:
    wanted = sorted({entity_id for entity_id in ids if _usable(entity_id)})
    if not wanted:
        return set()
    return {str(row["id"]) for row in scope.execute(sql, {"ids": wanted})}


def rule_result(error: ResearchError, result: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """``_rule_result``: a rule's refusal as a terminal result."""
    result["code"] = _RULE_CODES.get(error.code, "INVALID_REQUEST")
    return _RULE_STATUS.get(error.code, 409), result


# -- locks and small reads ------------------------------------------------------


def lock_targets(scope: LibraryScope) -> None:
    """Serialize this Library's target changes until the transaction ends."""
    scope.execute(_TARGETS_LOCK_SQL)


def lock(scope: LibraryScope, argument_id: str, *, key: bool = False) -> bool:
    """Hold the Argument a command changes (``FOR UPDATE`` when ``key``); whether it exists."""
    if not _usable(argument_id):
        return False
    return scope.execute(_LOCK_KEY_SQL if key else _LOCK_SQL, {"argument_id": argument_id}).fetchone() is not None


def share(scope: LibraryScope, argument_ids: Sequence[str]) -> set[str]:
    """Hold the named Arguments against a delete until COMMIT; the ids found."""
    return _found(scope, _SHARE_ARGUMENTS_SQL, argument_ids)


def share_works(scope: LibraryScope, raw_sources: Any) -> set[str]:
    """Hold the Works a citation list names against a delete until COMMIT; the ids found.

    Taken before the Argument row (the order a Work delete takes them in:
    the Work, then the Arguments citing it).
    """
    if not isinstance(raw_sources, list):
        return set()
    names = [raw["work_id"].strip() for raw in raw_sources
             if isinstance(raw, dict) and isinstance(raw.get("work_id"), str)]
    return _found(scope, _SHARE_WORKS_SQL, names)


def _fields(scope: LibraryScope, argument_id: str) -> Optional[dict[str, str]]:
    if not _usable(argument_id):
        return None
    found = scope.execute(_FIELDS_SQL, {"argument_id": argument_id}).fetchone()
    return None if found is None else {field: str(found[field]) for field in rules.FIELDS}


def _revision(scope: LibraryScope, scope_type: str, scope_id: str) -> int:
    return revisions.read(scope, scope_type, [scope_id])[scope_id]


def current_sources(scope: LibraryScope, argument_id: str) -> list[dict[str, str]]:
    """``current_sources``: the ordered citation list in the wire's shape."""
    return [{"work_id": citation.work_id, "pages": citation.pages} for citation in _citations(scope, argument_id)]


def _citations(scope: LibraryScope, argument_id: str) -> list[Citation]:
    return [Citation(str(row["work_id"]), row["manifestation_id"], str(row["pages"]))
            for row in scope.execute(_SOURCE_ROWS_SQL, {"argument_id": argument_id})]


def current_targets(scope: LibraryScope, argument_id: str) -> list[dict[str, str]]:
    """``current_targets``: the ordered target list over both tables, in the wire's shape."""
    return [{"type": str(row["type"]), "id": str(row["id"]), "verdict_id": str(row["verdict_id"])}
            for row in scope.execute(_TARGET_ROWS_SQL, {"argument_id": argument_id})]


def sync_state(scope: LibraryScope, argument_id: str) -> Optional[dict[str, Any]]:
    """``get_argument_state_on_conn``: the revisions of three fields and two lists, in one snapshot."""
    if _fields(scope, argument_id) is None:
        return None
    scopes = {field: rules.scope_key(argument_id, field) for field in sorted(rules.FIELDS)}
    found = revisions.read(scope, FIELD_SCOPE_TYPE, list(scopes.values()))
    return {
        "argument_id": argument_id,
        "fields": {field: {"revision": found[scope_id]} for field, scope_id in scopes.items()},
        "sources": {"revision": _revision(scope, SOURCES_SCOPE_TYPE, argument_id)},
        "targets": {"revision": _revision(scope, TARGETS_SCOPE_TYPE, argument_id)},
    }


# -- the two lists, decided ---------------------------------------------------------


def _clean_sources(scope: LibraryScope, argument_id: str, raw_sources: Any,
                   works: set[str]) -> list[Citation]:
    """``_replace_sources_on_conn``'s checks in its order; each entry with the Version it cites.

    ``works`` are the named Works found, held by :func:`share_works`.
    """
    if not isinstance(raw_sources, list):
        raise ResearchError("invalid_sources", "Sources must be a JSON array.")
    cleaned: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in raw_sources:
        if not isinstance(raw, dict):
            raise ResearchError("invalid_sources", "Each source must be an object.")
        work_id = raw.get("work_id")
        if not isinstance(work_id, str) or not work_id.strip():
            raise ResearchError("invalid_sources", "Each source needs a work_id.")
        work_id = work_id.strip()
        if work_id in seen:
            raise ResearchError("invalid_sources", "Duplicate source Work.")
        seen.add(work_id)
        if work_id not in works:
            raise ResearchError("work_not_found", "Source Work not found.", 404)
        cleaned.append((work_id, network._pages(raw.get("pages"))))
    return _pin(scope, argument_id, cleaned)


def _pin(scope: LibraryScope, argument_id: str, cleaned: list[tuple[str, str]]) -> list[Citation]:
    """The Version of each entry of a legacy list (§8.5); refuses one it cannot name."""
    stored: dict[tuple[str, str], list[Optional[str]]] = {}
    for citation in _citations(scope, argument_id):
        stored.setdefault((citation.work_id, citation.pages), []).append(citation.manifestation_id)
    versions = {str(row["work_id"]): (int(row["n"]), str(row["only_id"]))
                for row in scope.execute(_VERSIONS_SQL, {"ids": sorted({work for work, _ in cleaned})})}
    out = []
    for work_id, pages in cleaned:
        kept = stored.get((work_id, pages), [])
        if len(kept) > 1:
            raise _refuse(_VERSION_REQUIRED)
        if kept:
            out.append(Citation(work_id, kept[0], pages))
        elif not pages:
            out.append(Citation(work_id, None, pages))
        else:
            count, only = versions[work_id]
            if count != 1:
                raise _refuse(_VERSION_REQUIRED)
            out.append(Citation(work_id, only, pages))
    return out


def _clean_targets(scope: LibraryScope, argument_id: str, raw_targets: Any, *,
                   creating: bool) -> list[Target]:
    """``_replace_targets_on_conn``'s checks in its order, with the targets held.

    A create names no Argument that could answer it, so its only possible
    cycle is a target naming itself; a replacement holds the target lock.
    """
    if not isinstance(raw_targets, list):
        raise ResearchError("invalid_targets", "Targets must be a JSON array.")
    named: dict[str, list[str]] = {"position": [], "argument": []}
    for raw in raw_targets:
        if isinstance(raw, dict) and isinstance(raw.get("type"), str) and isinstance(raw.get("id"), str):
            named.setdefault(raw["type"], []).append(raw["id"].strip())
    positions = _found(scope, _SHARE_POSITIONS_SQL, named["position"])
    arguments = share(scope, [target for target in named["argument"] if target != argument_id])
    arguments.add(argument_id)  # locked, or (a create) just inserted
    verdicts = {str(row["id"]) for row in scope.execute_unscoped(_ENABLED_VERDICTS_SQL)}
    out: list[Target] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_targets):
        if not isinstance(raw, dict):
            raise ResearchError("invalid_targets", "Each target must be an object.")
        kind, target_id, verdict = raw.get("type"), raw.get("id"), raw.get("verdict_id")
        if not isinstance(kind, str) or kind not in rules.TARGET_TYPES:
            raise ResearchError("invalid_targets", "Target type must be position or argument.")
        if not isinstance(target_id, str) or not target_id.strip():
            raise ResearchError("invalid_targets", "Target id is required.")
        if not isinstance(verdict, str) or not verdict.strip():
            raise ResearchError("invalid_verdict", "Every target requires a verdict.")
        target_id, verdict = target_id.strip(), verdict.strip()
        if verdict not in verdicts:
            raise ResearchError("invalid_verdict", "Unknown verdict.")
        if kind == "position":
            if (kind, target_id) in seen:
                raise ResearchError("invalid_targets", "Duplicate Position target.")
            if target_id not in positions:
                raise ResearchError("position_not_found", "Position not found.", 404)
        else:
            if not ARGUMENT_ID_RE.fullmatch(target_id):
                raise ResearchError("invalid_targets", "Target Argument id is invalid.")
            if (kind, target_id) in seen:
                raise ResearchError("invalid_targets", "Duplicate Argument target.")
            if target_id not in arguments:
                raise ResearchError("argument_not_found", "Target Argument not found.", 404)
        seen.add((kind, target_id))
        out.append(Target(kind, target_id, verdict, index))
    answered = [target.id for target in out if target.type == "argument"]
    if argument_id in answered or (not creating and answered and scope.execute(
            _CYCLE_SQL, {"argument_id": argument_id, "target_ids": answered}).fetchone()):
        raise _refuse(_CYCLE)
    return out


def _write_sources(scope: LibraryScope, argument_id: str, citations: list[Citation]) -> None:
    scope.execute(_DELETE_SOURCES_SQL, {"argument_id": argument_id})
    if citations:
        scope.execute(_INSERT_SOURCES_SQL, {
            "argument_id": argument_id,
            "work_ids": [citation.work_id for citation in citations],
            "manifestation_ids": [citation.manifestation_id for citation in citations],
            "pages": [citation.pages for citation in citations],
        })


def _write_targets(scope: LibraryScope, argument_id: str, targets: list[Target]) -> None:
    for statement in _DELETE_TARGETS_SQL:
        scope.execute(statement, {"argument_id": argument_id})
    inserts: tuple[tuple[str, LiteralString], ...] = (("position", _INSERT_POSITION_TARGETS_SQL),
                                                      ("argument", _INSERT_ARGUMENT_TARGETS_SQL))
    for kind, statement in inserts:
        rows = [target for target in targets if target.type == kind]
        if rows:
            scope.execute(statement, {"argument_id": argument_id, "ids": [target.id for target in rows],
                                      "verdicts": [target.verdict_id for target in rows],
                                      "orders": [target.order_index for target in rows]})


# -- construction ---------------------------------------------------------------


def insert(scope: LibraryScope, argument_id: str, name: Any, kind: Any, main_text: Any = "",
           sources: Any = None, targets: Any = None) -> bool:
    """``insert_argument_on_conn``: the one construction primitive, no revision advanced.

    Refuses in SQLite's order (the fields, then the sources, then the
    targets) and then leaves nothing behind. Returns False when the id is
    already an Argument of this Library (a concurrent create of it committed
    first); that Argument is then held ``FOR KEY SHARE`` until COMMIT.
    Raises :class:`ForeignArgumentId` for an id of another Library.
    """
    values = {"argument_id": argument_id, "name": rules.canonical_wire("name", name),
              "kind": rules.canonical_wire("kind", kind), "main_text": rules.canonical_wire("main_text", main_text)}
    # The id is decided before the lists, so an id of another Library is
    # refused as such (unledgered), never answered with a list refusal.
    # Twice: the winner of a same-id race can be deleted between its COMMIT
    # and this hold, and then the id is free again.
    for _ in range(2):
        if scope.execute(_INSERT_SQL, values).fetchone() is not None:
            break
        if share(scope, [argument_id]):
            return False
    else:
        raise ForeignArgumentId(argument_id)
    try:
        if sources is not None:
            _write_sources(scope, argument_id,
                           _clean_sources(scope, argument_id, sources, share_works(scope, sources)))
        if targets is not None:
            _write_targets(scope, argument_id, _clean_targets(scope, argument_id, targets, creating=True))
    except ResearchError:
        scope.execute(_DELETE_SQL, {"argument_id": argument_id})
        raise
    return True


def apply_create(scope: LibraryScope, argument_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """``CREATE_ARGUMENT``'s ``apply_create``: a known id acknowledges, unchanged.

    Raises :class:`ForeignArgumentId` for an id of another Library.
    """
    result: dict[str, Any] = {"argument_id": argument_id}
    if share(scope, [argument_id]):
        result.update(code="ACKNOWLEDGED", changed=False)
        return 200, result
    try:
        created = insert(scope, argument_id, payload["name"], payload["kind"], payload["main_text"],
                         payload["sources"], payload["targets"])
    except ResearchError as error:
        return rule_result(error, result)
    result.update(code="ACKNOWLEDGED", changed=created)
    return 200, result


# -- fields -------------------------------------------------------------------------


def set_field(scope: LibraryScope, argument_id: str, field: str, value: Any) -> tuple[bool, int]:
    """``set_field_on_conn``: write one field and advance its revision together."""
    if field not in rules.FIELD_SET:
        raise ResearchError("invalid_field", "Not an editable Argument field.")
    desired = rules.canonical_wire(field, value)
    if not lock(scope, argument_id):
        raise _refuse(_NOT_FOUND)
    current = _fields(scope, argument_id)
    assert current is not None
    scope_id = rules.scope_key(argument_id, field)
    if current[field] == desired:
        return False, _revision(scope, FIELD_SCOPE_TYPE, scope_id)
    revisions.lock(scope, FIELD_SCOPE_TYPE, scope_id)
    scope.execute(_SET_SQL[field], {"argument_id": argument_id, "value": desired})
    return True, revisions.advance(scope, FIELD_SCOPE_TYPE, scope_id)


def apply_field_operation(scope: LibraryScope, argument_id: str, field: str, value: str,
                          base_revision: int) -> tuple[int, dict[str, Any]]:
    """``SET_ARGUMENT_FIELD``'s ``apply_field``: SQLite's decision table, result for result."""
    from backend import work_metadata_sync as meta

    desired = rules.canonical_wire(field, value)
    result: dict[str, Any] = {"argument_id": argument_id, "field": field}
    if not lock(scope, argument_id):
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    # Read, not ensured: the Argument lock serializes every writer of the
    # field, and a refused or converged operation must not create a scope.
    revision = _revision(scope, FIELD_SCOPE_TYPE, rules.scope_key(argument_id, field))
    fields = _fields(scope, argument_id)
    assert fields is not None
    current = fields[field]
    if base_revision > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 400, meta.fit_terminal_result(result, current, desired)
    if base_revision < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 409, meta.fit_terminal_result(result, current, desired)
    changed, after = set_field(scope, argument_id, field, desired)
    result.update(code="ACKNOWLEDGED", server_revision=after, changed=changed, value_omitted=True)
    return 200, result


# -- the citation list ----------------------------------------------------------------


def set_sources(scope: LibraryScope, argument_id: str, raw_sources: Any) -> tuple[bool, int]:
    """``set_sources_on_conn``: replace the whole list and advance its revision together.

    The Works it names are held before the Argument (:func:`share_works`).
    Every entry is checked even when the list looks unchanged: a list naming
    a deleted Work is refused, not accepted for looking equal.
    """
    works = share_works(scope, raw_sources)
    if not lock(scope, argument_id):
        raise _refuse(_NOT_FOUND)
    before = _citations(scope, argument_id)
    citations = _clean_sources(scope, argument_id, raw_sources, works)
    # An unchanged list keeps its pins (§8.5), so its rows are unchanged too.
    if [(c.work_id, c.pages) for c in citations] == [(c.work_id, c.pages) for c in before]:
        return False, _revision(scope, SOURCES_SCOPE_TYPE, argument_id)
    revisions.lock(scope, SOURCES_SCOPE_TYPE, argument_id)
    _write_sources(scope, argument_id, citations)
    scope.execute(_TOUCH_SQL, {"argument_id": argument_id})
    return True, revisions.advance(scope, SOURCES_SCOPE_TYPE, argument_id)


def apply_sources_operation(scope: LibraryScope, argument_id: str, desired: list[Any],
                            base_revision: int) -> tuple[int, dict[str, Any]]:
    """``SET_ARGUMENT_SOURCES``'s ``apply_sources``: an ordered list, so order is part of agreeing."""
    result: dict[str, Any] = {"argument_id": argument_id}
    share_works(scope, desired)  # before the Argument, as set_sources holds them
    if not lock(scope, argument_id):
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = _revision(scope, SOURCES_SCOPE_TYPE, argument_id)
    present = current_sources(scope, argument_id)
    if base_revision > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_count=len(present), requested_count=len(desired))
        return 400, result
    if base_revision < revision and desired != present:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_count=len(present), requested_count=len(desired))
        return 409, result
    try:
        changed, after = set_sources(scope, argument_id, desired)
    except ResearchError as error:
        return rule_result(error, result)
    result.update(code="ACKNOWLEDGED", changed=changed, server_revision=after)
    return 200, result


def release_work(scope: LibraryScope, work_id: str) -> None:
    """Remove every citation of a Work being deleted; advance each citing Argument's list.

    Called by the Work delete after it holds the Work ``FOR UPDATE``, so no
    citation of it can be added meanwhile. The citing Arguments are locked
    in id order before their rows change, as every list writer locks its
    Argument first. A pinned citation names a Version of the Work, so it is
    among them. The positions left are not renumbered (as SQLite's cascade
    leaves them): the list keeps its order.
    """
    citing = [str(row["argument_id"]) for row in scope.execute(_CITING_SQL, {"work_id": work_id})]
    if not citing:
        return
    scope.execute(_LOCK_MANY_SQL, {"ids": sorted(citing)}).fetchall()
    changed = {str(row["argument_id"]) for row in scope.execute(_RELEASE_WORK_SQL, {"work_id": work_id})}
    advance_sources(scope, sorted(changed))


def advance_sources(scope: LibraryScope, argument_ids: Sequence[str]) -> None:
    """Advance ``argument-sources`` of each Argument whose citation rows a command changed."""
    for scope_type, scope_id in revisions.lock_many(scope, [(SOURCES_SCOPE_TYPE, argument_id)
                                                             for argument_id in argument_ids]):
        revisions.advance(scope, scope_type, scope_id)


# -- the target list ------------------------------------------------------------------


def set_targets(scope: LibraryScope, argument_id: str, raw_targets: Any) -> tuple[bool, int]:
    """``set_targets_on_conn``: replace the whole list and advance its revision together.

    Under the target lock, so acyclicity is decided on the graph every
    other target writer leaves.
    """
    lock_targets(scope)
    if not lock(scope, argument_id):
        raise _refuse(_NOT_FOUND)
    before = current_targets(scope, argument_id)
    chosen = _clean_targets(scope, argument_id, raw_targets, creating=False)
    after = [{"type": target.type, "id": target.id, "verdict_id": target.verdict_id} for target in chosen]
    if after == before:
        return False, _revision(scope, TARGETS_SCOPE_TYPE, argument_id)
    revisions.lock(scope, TARGETS_SCOPE_TYPE, argument_id)
    _write_targets(scope, argument_id, chosen)
    scope.execute(_TOUCH_SQL, {"argument_id": argument_id})
    return True, revisions.advance(scope, TARGETS_SCOPE_TYPE, argument_id)


def apply_targets_operation(scope: LibraryScope, argument_id: str, desired: list[Any],
                            base_revision: int) -> tuple[int, dict[str, Any]]:
    """``SET_ARGUMENT_TARGETS``'s ``apply_targets``: order and verdicts are part of the value."""
    result: dict[str, Any] = {"argument_id": argument_id}
    lock_targets(scope)
    if not lock(scope, argument_id):
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = _revision(scope, TARGETS_SCOPE_TYPE, argument_id)
    present = current_targets(scope, argument_id)
    if base_revision > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_count=len(present), requested_count=len(desired))
        return 400, result
    if base_revision < revision and desired != present:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_count=len(present), requested_count=len(desired))
        return 409, result
    try:
        changed, after = set_targets(scope, argument_id, desired)
    except ResearchError as error:
        return rule_result(error, result)
    result.update(code="ACKNOWLEDGED", changed=changed, server_revision=after)
    return 200, result


# -- delete -----------------------------------------------------------------------


def share_referenced(scope: LibraryScope, text: str) -> None:
    """Hold the Arguments a Research Note's markup names ``FOR KEY SHARE`` until COMMIT.

    Every writer of a Research Note calls it before the note is stored, so
    an Argument delete (``FOR UPDATE``) that overlaps the write waits for it
    and then finds the reference (``ARGUMENT_IN_USE``).
    """
    if "[[argument:" in text:
        share(scope, [ref.argument_id for ref in parse_research_markup(text).argument_refs])


def _referenced_by_notes(scope: LibraryScope, argument_id: str) -> bool:
    """``_canonical_notes_reference_argument``, read from the notes themselves, never the derived mentions."""
    return any(ref.argument_id == argument_id
               for note in scope.execute(_RESEARCH_NOTES_SQL, {"argument_id": argument_id})
               for ref in parse_research_markup(str(note["body"])).argument_refs)


def delete(scope: LibraryScope, argument_id: str) -> tuple[bool, Optional[str]]:
    """``delete_argument_on_conn``: remove the Argument, or refuse; ``(deleted, refusal_code)``.

    An Argument a Research Note names is ``ARGUMENT_IN_USE``, one another
    Argument answers ``ARGUMENT_TARGETED``. Otherwise every scope it owns
    advances once, and the Argument goes with its own lists.
    """
    if not lock(scope, argument_id, key=True):
        return False, None
    if _referenced_by_notes(scope, argument_id):
        return False, "ARGUMENT_IN_USE"
    if scope.execute(_TARGETED_SQL, {"argument_id": argument_id}).fetchone() is not None:
        return False, "ARGUMENT_TARGETED"
    owned = [(FIELD_SCOPE_TYPE, rules.scope_key(argument_id, field)) for field in rules.FIELDS]
    owned += [(SOURCES_SCOPE_TYPE, argument_id), (TARGETS_SCOPE_TYPE, argument_id)]
    # A scope no edit touched has no row and reads as revision 0, the base a
    # recreated Argument starts from: ensured here, it advances with the rest.
    for scope_type, scope_id in revisions.lock_many(scope, owned):
        revisions.advance(scope, scope_type, scope_id)
    scope.execute(_DELETE_SQL, {"argument_id": argument_id})
    return True, None


# -- the HTTP reads and commands ---------------------------------------------------


def verdicts(scope: LibraryScope) -> list[dict[str, Any]]:
    """``list_verdicts``: the enabled verdicts in their order."""
    return [dict(row) for row in scope.execute_unscoped(_VERDICTS_SQL)]


def _sources(scope: LibraryScope, argument_id: Optional[str]) -> dict[str, list[dict[str, Any]]]:
    rows = [dict(row) for row in scope.execute(_SOURCES_SQL, {"argument_id": argument_id})]
    authors: dict[str, list[dict[str, Any]]] = {}
    work_ids = sorted({row["work_id"] for row in rows})
    if work_ids:
        for row in scope.execute(_AUTHORS_SQL, {"ids": work_ids}):
            author = dict(row)
            authors.setdefault(str(author.pop("work_id")), []).append(author)
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        owner = str(row.pop("argument_id"))
        row["authors"] = authors.get(row["work_id"], [])
        out.setdefault(owner, []).append(row)
    return out


def _targets(scope: LibraryScope, argument_id: Optional[str]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    reads: tuple[tuple[str, LiteralString], ...] = (("position", _POSITION_TARGETS_SQL),
                                                    ("argument", _ARGUMENT_TARGETS_SQL))
    for kind, statement in reads:
        for row in scope.execute(statement, {"argument_id": argument_id}):
            item = dict(row)
            owner = str(item.pop("argument_id"))
            item["type"] = kind
            out.setdefault(owner, []).append(item)
    for items in out.values():
        items.sort(key=lambda item: int(item["order_index"]))
    return out


def detail(scope: LibraryScope, argument_id: str) -> Optional[dict[str, Any]]:
    """``_argument_bundle``: the row with its sources, targets and responses; None when absent."""
    if not _usable(argument_id):
        return None
    found = scope.execute(_ROW_SQL, {"argument_id": argument_id}).fetchone()
    if found is None:
        return None
    item = dict(found)
    item["sources"] = _sources(scope, argument_id).get(argument_id, [])
    item["targets"] = _targets(scope, argument_id).get(argument_id, [])
    item["responses"] = [dict(row) for row in scope.execute(_RESPONSES_SQL, {"argument_id": argument_id})]
    return item


def _detail_or_refuse(scope: LibraryScope, argument_id: str) -> dict[str, Any]:
    item = detail(scope, argument_id)
    if item is None:
        raise _refuse(_NOT_FOUND)
    return item


def catalog(scope: LibraryScope, kind: Optional[str] = None) -> list[dict[str, Any]]:
    """``list_arguments``: every Argument (or every one of ``kind``) by name, with its lists."""
    if kind and kind not in network.ARGUMENT_KINDS:
        raise ResearchError("invalid_kind", "Invalid Argument kind.")
    targets, sources = _targets(scope, None), _sources(scope, None)
    responses = {str(row["id"]): int(row["n"]) for row in scope.execute(_RESPONSE_COUNTS_SQL)}
    out = []
    for row in scope.execute(_ALL_SQL, {"kind": kind or None}):
        item = dict(row)
        item["targets"] = targets.get(item["id"], [])
        item["sources"] = sources.get(item["id"], [])
        item["response_count"] = responses.get(item["id"], 0)
        out.append(item)
    return out


def create(scope: LibraryScope, new_id: str, name: Any, kind: Any, main_text: Any = "",
           sources: Any = None, targets: Any = None) -> dict[str, Any]:
    """``create_argument``: construct under a fresh id; the detail."""
    if not insert(scope, new_id, name, kind, main_text, sources, targets):  # pragma: no cover - a fresh id
        raise RuntimeError("a generated argument id is taken")
    return _detail_or_refuse(scope, new_id)


def update(scope: LibraryScope, argument_id: str, name: Any = None, kind: Any = None,
           main_text: Any = None) -> dict[str, Any]:
    """``update_argument``: one field call per field supplied, each advancing its own scope."""
    aid = (argument_id or "").strip()
    if not aid or not lock(scope, aid):
        raise _refuse(_NOT_FOUND)
    if name is None and kind is None and main_text is None:
        raise ResearchError("nothing_to_update", "Nothing to update.")
    for field, value in (("name", name), ("kind", kind), ("main_text", main_text)):
        if value is not None:
            set_field(scope, aid, field, value)
    return _detail_or_refuse(scope, aid)


def replace_sources(scope: LibraryScope, argument_id: str, sources: Any) -> dict[str, Any]:
    """``replace_argument_sources``: the whole citation list."""
    aid = (argument_id or "").strip()
    set_sources(scope, aid, sources)
    return _detail_or_refuse(scope, aid)


def replace_targets(scope: LibraryScope, argument_id: str, targets: Any) -> dict[str, Any]:
    """``replace_argument_targets``: the whole target list."""
    aid = (argument_id or "").strip()
    set_targets(scope, aid, targets)
    return _detail_or_refuse(scope, aid)


def remove(scope: LibraryScope, argument_id: str) -> None:
    """``delete_argument``: SQLite's refusals."""
    aid = (argument_id or "").strip()
    deleted, refusal = delete(scope, aid)
    if refusal == "ARGUMENT_IN_USE":
        raise _refuse(_IN_USE)
    if refusal == "ARGUMENT_TARGETED":
        raise _refuse(_TARGETED)
    if not deleted:
        raise _refuse(_NOT_FOUND)
