"""Canonical Concepts, Positions, and Arguments/Stances.

Work↔Concept membership is never stored here. Note save parses
[[concept:...]] / [[argument:...]] markup, then creates/resolves Concepts
in the same transaction as works.text_content.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from backend.db_manager import PRKSDatabase
from backend.log_safety import safe_error_type, safe_log_id
from backend.research_markup import (
    ARGUMENT_ID_RE,
    canonical_concept_name,
    normalize_concept_key,
    parse_research_markup,
)

LOGGER = logging.getLogger("prks.research")

CONCEPT_NAME_MAX = 160
CONCEPT_ALIAS_MAX = 160
CONCEPT_DEFINITION_MAX = 100_000
POSITION_NAME_MAX = 240
POSITION_DESCRIPTION_MAX = 100_000
ARGUMENT_NAME_MAX = 200
ARGUMENT_TEXT_MAX = 100_000
ARGUMENT_PAGES_MAX = 100
ARGUMENT_KINDS = frozenset({"argument", "stance"})

_CONTROL_RE = __import__("re").compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ResearchError(ValueError):
    """Controlled research-network failure."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def _clean_text(raw, *, field: str, required: bool, max_len: int) -> str:
    if raw is None:
        if required:
            raise ResearchError("invalid_%s" % field, "%s is required." % field.capitalize())
        return ""
    if not isinstance(raw, str):
        raise ResearchError("invalid_%s" % field, "%s must be a string." % field.capitalize())
    if _CONTROL_RE.search(raw):
        raise ResearchError("invalid_%s" % field, "%s contains invalid characters." % field.capitalize())
    text = raw.strip() if field in ("name", "alias", "pages") else raw
    if field in ("name", "alias"):
        text = canonical_concept_name(text) if field != "pages" else text.strip()
    if required and not text.strip():
        raise ResearchError("invalid_%s" % field, "%s is required." % field.capitalize())
    if len(text) > max_len:
        raise ResearchError("invalid_%s" % field, "%s is too long." % field.capitalize())
    return text


def _concept_name(raw) -> str:
    if not isinstance(raw, str):
        raise ResearchError("invalid_name", "Name must be a string.")
    if _CONTROL_RE.search(raw):
        raise ResearchError("invalid_name", "Name contains invalid characters.")
    name = canonical_concept_name(raw)
    if not name:
        raise ResearchError("invalid_name", "Name is required.")
    if len(name) > CONCEPT_NAME_MAX:
        raise ResearchError("invalid_name", "Name is too long.")
    return name


def _plain_name(raw, *, max_len: int) -> str:
    if not isinstance(raw, str):
        raise ResearchError("invalid_name", "Name must be a string.")
    if _CONTROL_RE.search(raw):
        raise ResearchError("invalid_name", "Name contains invalid characters.")
    name = " ".join(raw.split())
    if not name:
        raise ResearchError("invalid_name", "Name is required.")
    if len(name) > max_len:
        raise ResearchError("invalid_name", "Name is too long.")
    return name


def _optional_markdown(raw, *, max_len: int) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ResearchError("invalid_text", "Text must be a string.")
    if _CONTROL_RE.search(raw):
        raise ResearchError("invalid_text", "Text contains invalid characters.")
    if len(raw) > max_len:
        raise ResearchError("invalid_text", "Text is too long.")
    return raw


def _pages(raw) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ResearchError("invalid_pages", "Pages must be a string.")
    if _CONTROL_RE.search(raw):
        raise ResearchError("invalid_pages", "Pages contain invalid characters.")
    text = raw.strip()
    if len(text) > ARGUMENT_PAGES_MAX:
        raise ResearchError("invalid_pages", "Pages is too long.")
    return text


def _row(d) -> dict:
    return dict(d)


def _fetchone(conn: sqlite3.Connection, sql: str, params: tuple = ()):
    return conn.execute(sql, params).fetchone()


def _fetchall(conn: sqlite3.Connection, sql: str, params: tuple = ()):
    return conn.execute(sql, params).fetchall()


# --- Concepts -----------------------------------------------------------------


def list_concepts(db: PRKSDatabase) -> List[dict]:
    with db.connection() as conn:
        rows = _fetchall(
            conn,
            """
            SELECT c.id, c.name, c.description, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM concept_parents p WHERE p.parent_concept_id = c.id)
                       AS subconcept_count
            FROM concepts c
            ORDER BY LOWER(c.name) ASC, c.id ASC
            """,
        )
        parent_rows = _fetchall(
            conn,
            """
            SELECT cp.child_concept_id AS child_id, p.id AS id, p.name AS name
            FROM concept_parents cp
            JOIN concepts p ON p.id = cp.parent_concept_id
            ORDER BY LOWER(p.name) ASC, p.id ASC
            """,
        )
        alias_rows = _fetchall(
            conn,
            """
            SELECT concept_id, alias
            FROM concept_aliases
            ORDER BY LOWER(alias) ASC
            """,
        )
    parents: Dict[str, List[dict]] = {}
    for r in parent_rows:
        parents.setdefault(r["child_id"], []).append({"id": r["id"], "name": r["name"]})
    aliases: Dict[str, List[str]] = {}
    for r in alias_rows:
        aliases.setdefault(r["concept_id"], []).append(r["alias"])
    out = []
    for r in rows:
        item = _row(r)
        item["parents"] = parents.get(r["id"], [])
        item["aliases"] = aliases.get(r["id"], [])
        item["subconcept_count"] = int(r["subconcept_count"] or 0)
        out.append(item)
    return out


def get_concept(db: PRKSDatabase, concept_id: str) -> Optional[dict]:
    cid = (concept_id or "").strip()
    if not cid:
        return None
    with db.connection() as conn:
        row = _fetchone(conn, "SELECT * FROM concepts WHERE id = ?", (cid,))
        if not row:
            return None
        item = _row(row)
        item["aliases"] = [
            r["alias"]
            for r in _fetchall(
                conn,
                "SELECT alias FROM concept_aliases WHERE concept_id = ? ORDER BY LOWER(alias) ASC",
                (cid,),
            )
        ]
        item["parents"] = [
            {"id": r["id"], "name": r["name"]}
            for r in _fetchall(
                conn,
                """
                SELECT p.id, p.name
                FROM concept_parents cp
                JOIN concepts p ON p.id = cp.parent_concept_id
                WHERE cp.child_concept_id = ?
                ORDER BY LOWER(p.name) ASC, p.id ASC
                """,
                (cid,),
            )
        ]
        item["children"] = [
            {"id": r["id"], "name": r["name"]}
            for r in _fetchall(
                conn,
                """
                SELECT c.id, c.name
                FROM concept_parents cp
                JOIN concepts c ON c.id = cp.child_concept_id
                WHERE cp.parent_concept_id = ?
                ORDER BY LOWER(c.name) ASC, c.id ASC
                """,
                (cid,),
            )
        ]
    return item


def _normalized_hits(conn: sqlite3.Connection, key: str) -> List[str]:
    ids = []
    seen = set()
    for r in _fetchall(conn, "SELECT id, name FROM concepts"):
        if normalize_concept_key(r["name"]) == key and r["id"] not in seen:
            seen.add(r["id"])
            ids.append(r["id"])
    for r in _fetchall(
        conn,
        "SELECT concept_id FROM concept_aliases WHERE normalized_alias = ?",
        (key,),
    ):
        if r["concept_id"] not in seen:
            seen.add(r["concept_id"])
            ids.append(r["concept_id"])
    return ids


def resolve_concept_key(conn: sqlite3.Connection, raw_name: str) -> Tuple[str, List[str]]:
    """Return ('ok'|'missing'|'ambiguous'|'invalid', [ids])."""
    name = canonical_concept_name(raw_name)
    if not name or len(name) > CONCEPT_NAME_MAX:
        return "invalid", []
    key = normalize_concept_key(name)
    ids = _normalized_hits(conn, key)
    if not ids:
        return "missing", []
    if len(ids) > 1:
        return "ambiguous", ids
    return "ok", ids


def create_concept(
    db: PRKSDatabase,
    name,
    description: str = "",
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    from backend import concept_sync

    own = conn is None
    if own:
        conn = db.get_connection()
    try:
        # The SAME construction boundary the durable `CREATE_CONCEPT` uses, so
        # the two paths cannot drift on what a legal Concept is: normalization,
        # the name-or-alias uniqueness rule, and the row shape are one
        # implementation. The id is still minted here, because that is what
        # distinguishes the two paths -- the ordinary API allocates, a durable
        # client brings its own.
        cid = db.generate_id("C")
        concept_sync.insert_concept_on_conn(conn, cid, name, description)
        if own:
            conn.commit()
        LOGGER.info("concept_created concept_id=%s", safe_log_id(cid))
        return get_concept_on_conn(conn, cid)
    finally:
        if own:
            conn.close()


def get_concept_on_conn(conn: sqlite3.Connection, concept_id: str) -> dict:
    row = _fetchone(conn, "SELECT * FROM concepts WHERE id = ?", (concept_id,))
    if not row:
        raise ResearchError("not_found", "Concept not found.", 404)
    item = _row(row)
    item["aliases"] = [
        r["alias"]
        for r in _fetchall(
            conn,
            "SELECT alias FROM concept_aliases WHERE concept_id = ? ORDER BY LOWER(alias) ASC",
            (concept_id,),
        )
    ]
    item["parents"] = [
        {"id": r["id"], "name": r["name"]}
        for r in _fetchall(
            conn,
            """
            SELECT p.id, p.name FROM concept_parents cp
            JOIN concepts p ON p.id = cp.parent_concept_id
            WHERE cp.child_concept_id = ?
            ORDER BY LOWER(p.name) ASC
            """,
            (concept_id,),
        )
    ]
    item["children"] = [
        {"id": r["id"], "name": r["name"]}
        for r in _fetchall(
            conn,
            """
            SELECT c.id, c.name FROM concept_parents cp
            JOIN concepts c ON c.id = cp.child_concept_id
            WHERE cp.parent_concept_id = ?
            ORDER BY LOWER(c.name) ASC
            """,
            (concept_id,),
        )
    ]
    return item


def update_concept(db: PRKSDatabase, concept_id: str, *, name=None, description=None) -> dict:
    """Edit a Concept through the revision-aware boundaries.

    A rename is not a field write: it keeps the old name reachable as an alias,
    so it goes through the IDENTITY boundary that owns both. The definition is
    an ordinary scalar and goes through the field boundary. Sharing them with
    the durable families is what makes an offline device able to discover that
    a value it was holding had been overtaken.
    """
    from backend import concept_sync

    cid = (concept_id or "").strip()
    if not cid:
        raise ResearchError("not_found", "Concept not found.", 404)
    if name is None and description is None:
        raise ResearchError("nothing_to_update", "Nothing to update.")
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        identity = concept_sync.current_identity(conn, cid)
        if identity is None:
            raise ResearchError("not_found", "Concept not found.", 404)
        if name is not None:
            # The alias set is carried through unchanged; the boundary adds the
            # old name to it when the identity actually moves.
            concept_sync.set_identity_on_conn(conn, cid, name, identity["aliases"])
        if description is not None:
            concept_sync.set_field_on_conn(conn, cid, "description", description)
        LOGGER.info("concept_updated concept_id=%s", safe_log_id(cid))
        return get_concept_on_conn(conn, cid)


def _canonical_notes_reference_concept(conn: sqlite3.Connection, concept_id: str) -> bool:
    for r in _fetchall(conn, "SELECT text_content FROM works"):
        text = r["text_content"] or ""
        if "[[concept:" not in text:
            continue
        markup = parse_research_markup(text)
        for ref in markup.concept_refs:
            status, ids = resolve_concept_key(conn, ref.name)
            if status == "ok" and ids and ids[0] == concept_id:
                return True
            if status == "ambiguous" and concept_id in ids:
                return True
    return False


def _canonical_notes_reference_argument(conn: sqlite3.Connection, argument_id: str) -> bool:
    for r in _fetchall(conn, "SELECT text_content FROM works"):
        text = r["text_content"] or ""
        if "[[argument:" not in text:
            continue
        markup = parse_research_markup(text)
        for ref in markup.argument_refs:
            if ref.argument_id == argument_id:
                return True
    return False


def delete_concept(db: PRKSDatabase, concept_id: str) -> None:
    """Destruction, through the boundary that advances what it invalidates.

    The protection is unchanged: a Concept still referenced by canonical
    research notes is refused rather than cascaded.
    """
    from backend import concept_sync

    cid = (concept_id or "").strip()
    if not cid:
        raise ResearchError("not_found", "Concept not found.", 404)
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _fetchone(conn, "SELECT 1 FROM concepts WHERE id = ?", (cid,)):
            raise ResearchError("not_found", "Concept not found.", 404)
        _deleted, refusal = concept_sync.delete_concept_on_conn(conn, cid)
        if refusal:
            raise ResearchError(
                "concept_in_use",
                "This Concept is still referenced in research notes. Remove or replace those references before deleting it.",
                409,
            )
    LOGGER.info("concept_deleted concept_id=%s", safe_log_id(cid))


def replace_concept_aliases(db: PRKSDatabase, concept_id: str, aliases) -> dict:
    """Rewrite a Concept's alias set, through the IDENTITY boundary.

    Aliases are half of what a Concept IS -- `resolve_concept_key` matches
    name-or-alias over one normalized space -- so they share a revision with the
    name rather than carrying one of their own.
    """
    from backend import concept_sync

    cid = (concept_id or "").strip()
    if not isinstance(aliases, list):
        raise ResearchError("invalid_aliases", "Aliases must be a JSON array.")
    for raw in aliases:
        if not isinstance(raw, str):
            raise ResearchError("invalid_aliases", "Each alias must be a string.")
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        identity = concept_sync.current_identity(conn, cid)
        if identity is None:
            raise ResearchError("not_found", "Concept not found.", 404)
        concept_sync.set_identity_on_conn(conn, cid, identity["name"], aliases)
        LOGGER.info("concept_aliases_changed concept_id=%s alias_count=%s",
                    safe_log_id(cid), len(aliases))
        return get_concept_on_conn(conn, cid)


def _parent_cycle(conn: sqlite3.Connection, child_id: str, parent_ids: Sequence[str]) -> bool:
    """True if adding parent_ids onto child would cycle."""
    if child_id in parent_ids:
        return True
    parents_of: Dict[str, List[str]] = {}
    for r in _fetchall(conn, "SELECT child_concept_id, parent_concept_id FROM concept_parents"):
        parents_of.setdefault(r["child_concept_id"], []).append(r["parent_concept_id"])
    proposed = list(parent_ids)
    stack = list(proposed)
    seen = set()
    while stack:
        nid = stack.pop()
        if nid == child_id:
            return True
        if nid in seen:
            continue
        seen.add(nid)
        stack.extend(parents_of.get(nid, ()))
    return False


def replace_concept_parents(db: PRKSDatabase, concept_id: str, parent_ids) -> dict:
    """Rewrite the whole parent set, through the boundary the aggregate owns."""
    from backend import concept_sync

    cid = (concept_id or "").strip()
    if not isinstance(parent_ids, list):
        raise ResearchError("invalid_parents", "Parents must be a JSON array.")
    for raw in parent_ids:
        if not isinstance(raw, str) or not raw.strip():
            raise ResearchError("invalid_parents", "Each parent id must be a string.")
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        concept_sync.set_parents_on_conn(conn, cid, parent_ids)
        LOGGER.info("concept_parents_changed concept_id=%s parent_count=%s",
                    safe_log_id(cid), len(parent_ids))
        return get_concept_on_conn(conn, cid)


def ensure_concepts_for_names(conn: sqlite3.Connection, db: PRKSDatabase, names: Iterable[str]) -> Dict[str, str]:
    """Resolve or create Concepts for canonical names. Map written-normalized key → id.

    A higher-level workflow -- resolution first, construction only for what is
    genuinely missing -- built on the shared row primitive so every Concept in
    the database was inserted under the same uniqueness rule, whoever asked for
    it. It runs inside the caller's note-save transaction and must stay that
    way: a Concept auto-created for a note that then fails to save would leave
    a vocabulary entry nothing references.
    """
    from backend import concept_sync

    resolved: Dict[str, str] = {}
    for raw in names:
        name = canonical_concept_name(raw)
        key = normalize_concept_key(name)
        if key in resolved:
            continue
        status, ids = resolve_concept_key(conn, name)
        if status == "ambiguous":
            raise ResearchError(
                "ambiguous_concept",
                "Multiple Concepts match a note reference.",
                409,
            )
        if status == "invalid":
            raise ResearchError("invalid_concept", "Concept reference is invalid.")
        if status == "ok":
            resolved[key] = ids[0]
            continue
        cid = db.generate_id("C")
        # The same construction primitive every other path uses. Safe here
        # because `save_work_notes` has already refused control characters in
        # the note body and `resolve_concept_key` has already answered
        # "invalid" for an over-long reference -- so nothing reachable through
        # markup can be a name the primitive would reject.
        concept_sync.insert_concept_on_conn(conn, cid, name, "")
        LOGGER.info("concept_created concept_id=%s", safe_log_id(cid))
        resolved[key] = cid
    return resolved


# --- Positions ----------------------------------------------------------------


def list_positions(db: PRKSDatabase) -> List[dict]:
    rows = db.execute_query(
        "SELECT * FROM positions ORDER BY LOWER(name) ASC, id ASC"
    )
    return [dict(r) for r in rows]


def get_position(db: PRKSDatabase, position_id: str) -> Optional[dict]:
    pid = (position_id or "").strip()
    if not pid:
        return None
    with db.connection() as conn:
        row = _fetchone(conn, "SELECT * FROM positions WHERE id = ?", (pid,))
        if not row:
            return None
        item = _row(row)
        item["arguments"] = _position_arguments(conn, pid)
        return item


def _position_arguments(conn: sqlite3.Connection, position_id: str) -> List[dict]:
    rows = _fetchall(
        conn,
        """
        SELECT a.id, a.name, a.kind, v.id AS verdict_id, v.label AS verdict_label
        FROM argument_target_positions t
        JOIN arguments a ON a.id = t.argument_id
        JOIN argument_verdicts v ON v.id = t.verdict_id
        WHERE t.position_id = ?
        ORDER BY v.sort_order ASC, LOWER(a.name) ASC, a.id ASC
        """,
        (position_id,),
    )
    return [_row(r) for r in rows]


def create_position(db: PRKSDatabase, name, description: str = "") -> dict:
    """Construct a Position through the boundary the durable family shares.

    The lazy import is required, not stylistic: `position_sync` reads its
    normalization rules from this module, so a top-level import here would be a
    cycle. Concepts needed the same treatment for the same reason.
    """
    from backend import position_sync

    pid = db.generate_id("P")
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        position_sync.insert_position_on_conn(conn, pid, name, description)
    LOGGER.info("position_created position_id=%s", safe_log_id(pid))
    return get_position(db, pid)


def update_position(db: PRKSDatabase, position_id: str, *, name=None, description=None) -> dict:
    """Edit a Position through the revision-aware boundary.

    One call per field actually supplied, so each field advances only its OWN
    revision -- that is what makes an unrelated description edit unable to
    conflict with a rename, and it is the property the durable family relies on.
    """
    from backend import position_sync

    pid = (position_id or "").strip()
    if not pid:
        raise ResearchError("not_found", "Position not found.", 404)
    if name is None and description is None:
        raise ResearchError("nothing_to_update", "Nothing to update.")
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _fetchone(conn, "SELECT 1 FROM positions WHERE id = ?", (pid,)):
            raise ResearchError("not_found", "Position not found.", 404)
        if name is not None:
            position_sync.set_field_on_conn(conn, pid, "name", name)
        if description is not None:
            position_sync.set_field_on_conn(conn, pid, "description", description)
    LOGGER.info("position_updated position_id=%s", safe_log_id(pid))
    return get_position(db, pid)


def delete_position(db: PRKSDatabase, position_id: str) -> None:
    """Destruction, through the boundary the durable family shares.

    The protection is unchanged: a Position an Argument still targets is
    refused, because deleting it would leave that Argument aimed at nothing.
    The shared primitive reports it as a code, which is translated back into
    the `ResearchError` contract this endpoint has always raised.
    """
    from backend import position_sync

    pid = (position_id or "").strip()
    if not pid:
        raise ResearchError("not_found", "Position not found.", 404)
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _fetchone(conn, "SELECT 1 FROM positions WHERE id = ?", (pid,)):
            raise ResearchError("not_found", "Position not found.", 404)
        _deleted, refusal = position_sync.delete_position_on_conn(conn, pid)
        if refusal == "POSITION_IN_USE":
            raise ResearchError(
                "position_in_use",
                "This Position is still targeted by an Argument or Stance.",
                409,
            )
    LOGGER.info("position_deleted position_id=%s", safe_log_id(pid))


# --- Arguments / Stances ------------------------------------------------------


def list_verdicts(db: PRKSDatabase) -> List[dict]:
    rows = db.execute_query(
        "SELECT * FROM argument_verdicts WHERE enabled = 1 ORDER BY sort_order ASC, id ASC"
    )
    return [dict(r) for r in rows]


def _argument_bundle(conn: sqlite3.Connection, arg_id: str) -> Optional[dict]:
    row = _fetchone(conn, "SELECT * FROM arguments WHERE id = ?", (arg_id,))
    if not row:
        return None
    item = _row(row)
    item["sources"] = _argument_sources(conn, arg_id)
    item["targets"] = _argument_targets(conn, arg_id)
    item["responses"] = _argument_responses(conn, arg_id)
    return item


def _argument_sources(conn: sqlite3.Connection, arg_id: str) -> List[dict]:
    rows = _fetchall(
        conn,
        """
        SELECT s.work_id, s.pages, s.order_index, w.title AS work_title
        FROM argument_sources s
        JOIN works w ON w.id = s.work_id
        WHERE s.argument_id = ?
        ORDER BY s.order_index ASC, w.title ASC
        """,
        (arg_id,),
    )
    out = []
    for r in rows:
        src = _row(r)
        src["authors"] = _work_authors(conn, r["work_id"])
        out.append(src)
    return out


def _work_authors(conn: sqlite3.Connection, work_id: str) -> List[dict]:
    rows = _fetchall(
        conn,
        """
        SELECT p.id, p.first_name, p.last_name, r.role_type, r.credit_name, r.order_index
        FROM roles r
        JOIN persons p ON p.id = r.person_id
        WHERE r.work_id = ? AND r.role_type = 'Author'
        ORDER BY r.order_index ASC, p.last_name ASC
        """,
        (work_id,),
    )
    return [_row(r) for r in rows]


def _argument_targets(conn: sqlite3.Connection, arg_id: str) -> List[dict]:
    out = []
    for r in _fetchall(
        conn,
        """
        SELECT t.position_id AS id, t.verdict_id, t.order_index,
               v.label AS verdict_label, p.name
        FROM argument_target_positions t
        JOIN positions p ON p.id = t.position_id
        JOIN argument_verdicts v ON v.id = t.verdict_id
        WHERE t.argument_id = ?
        ORDER BY t.order_index ASC
        """,
        (arg_id,),
    ):
        item = _row(r)
        item["type"] = "position"
        out.append(item)
    for r in _fetchall(
        conn,
        """
        SELECT t.target_argument_id AS id, t.verdict_id, t.order_index,
               v.label AS verdict_label, a.name, a.kind
        FROM argument_target_arguments t
        JOIN arguments a ON a.id = t.target_argument_id
        JOIN argument_verdicts v ON v.id = t.verdict_id
        WHERE t.argument_id = ?
        ORDER BY t.order_index ASC
        """,
        (arg_id,),
    ):
        item = _row(r)
        item["type"] = "argument"
        out.append(item)
    out.sort(key=lambda x: int(x.get("order_index") or 0))
    return out


def _argument_responses(conn: sqlite3.Connection, arg_id: str) -> List[dict]:
    rows = _fetchall(
        conn,
        """
        SELECT a.id, a.name, a.kind, t.verdict_id, v.label AS verdict_label
        FROM argument_target_arguments t
        JOIN arguments a ON a.id = t.argument_id
        JOIN argument_verdicts v ON v.id = t.verdict_id
        WHERE t.target_argument_id = ?
        ORDER BY LOWER(a.name) ASC, a.id ASC
        """,
        (arg_id,),
    )
    return [_row(r) for r in rows]


def list_arguments(db: PRKSDatabase, kind: Optional[str] = None) -> List[dict]:
    sql = "SELECT id, name, kind, created_at, updated_at FROM arguments"
    params: tuple = ()
    if kind:
        if kind not in ARGUMENT_KINDS:
            raise ResearchError("invalid_kind", "Invalid Argument kind.")
        sql += " WHERE kind = ?"
        params = (kind,)
    sql += " ORDER BY LOWER(name) ASC, id ASC"
    with db.connection() as conn:
        rows = _fetchall(conn, sql, params)
        out = []
        for r in rows:
            item = _row(r)
            item["targets"] = _argument_targets(conn, r["id"])
            item["sources"] = _argument_sources(conn, r["id"])
            item["response_count"] = len(_argument_responses(conn, r["id"]))
            out.append(item)
        return out


def get_argument(db: PRKSDatabase, argument_id: str) -> Optional[dict]:
    aid = (argument_id or "").strip()
    if not aid:
        return None
    with db.connection() as conn:
        return _argument_bundle(conn, aid)


def _validate_kind(kind) -> str:
    if not isinstance(kind, str) or kind.strip() not in ARGUMENT_KINDS:
        raise ResearchError("invalid_kind", "Kind must be argument or stance.")
    return kind.strip()


def create_argument(
    db: PRKSDatabase,
    *,
    name,
    kind,
    main_text: str = "",
    sources=None,
    targets=None,
) -> dict:
    """Construct an Argument through the boundary the durable family shares.

    Scalar columns, sources and targets stay in ONE transaction: this endpoint
    has never been able to leave a disconnected Argument behind, and the
    durable family inherits that rather than weakening it.

    The lazy import is required, not stylistic: `argument_sync` reads its
    normalization rules from this module, so a top-level import here would be a
    cycle. Concepts and Positions needed the same treatment for the same reason.
    """
    from backend import argument_sync

    aid = db.generate_id("A")
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        argument_sync.insert_argument_on_conn(conn, aid, name, kind, main_text,
                                              sources, targets)
        LOGGER.info("argument_created argument_id=%s", safe_log_id(aid))
        return _argument_bundle(conn, aid)


def update_argument(
    db: PRKSDatabase,
    argument_id: str,
    *,
    name=None,
    kind=None,
    main_text=None,
) -> dict:
    """Edit an Argument through the revision-aware boundary.

    One call per field actually supplied, so each field advances only its OWN
    revision -- that is what keeps a body edit from conflicting with a rename,
    and it is the property the durable family relies on.
    """
    from backend import argument_sync

    aid = (argument_id or "").strip()
    if not aid:
        raise ResearchError("not_found", "Argument not found.", 404)
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _fetchone(conn, "SELECT 1 FROM arguments WHERE id = ?", (aid,)):
            raise ResearchError("not_found", "Argument not found.", 404)
        if name is None and kind is None and main_text is None:
            raise ResearchError("nothing_to_update", "Nothing to update.")
        for field, value in (("name", name), ("kind", kind), ("main_text", main_text)):
            if value is not None:
                argument_sync.set_field_on_conn(conn, aid, field, value)
        LOGGER.info("argument_updated argument_id=%s", safe_log_id(aid))
        return _argument_bundle(conn, aid)


def delete_argument(db: PRKSDatabase, argument_id: str) -> None:
    """Destruction, through the boundary the durable family shares.

    Both protections are unchanged. The shared primitive reports them as codes,
    which are translated back into the `ResearchError` contract this endpoint
    has always raised.
    """
    from backend import argument_sync

    aid = (argument_id or "").strip()
    if not aid:
        raise ResearchError("not_found", "Argument not found.", 404)
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _fetchone(conn, "SELECT 1 FROM arguments WHERE id = ?", (aid,)):
            raise ResearchError("not_found", "Argument not found.", 404)
        _deleted, refusal = argument_sync.delete_argument_on_conn(conn, aid)
        if refusal == "ARGUMENT_IN_USE":
            raise ResearchError(
                "argument_in_use",
                "This Argument is still referenced in research notes.",
                409,
            )
        if refusal == "ARGUMENT_TARGETED":
            raise ResearchError(
                "argument_targeted",
                "Another Argument or Stance still responds to this record.",
                409,
            )
    LOGGER.info("argument_deleted argument_id=%s", safe_log_id(aid))


def _replace_sources_on_conn(conn: sqlite3.Connection, arg_id: str, sources) -> None:
    if not isinstance(sources, list):
        raise ResearchError("invalid_sources", "Sources must be a JSON array.")
    cleaned = []
    seen = set()
    for i, raw in enumerate(sources):
        if not isinstance(raw, dict):
            raise ResearchError("invalid_sources", "Each source must be an object.")
        wid = raw.get("work_id")
        if not isinstance(wid, str) or not wid.strip():
            raise ResearchError("invalid_sources", "Each source needs a work_id.")
        wid = wid.strip()
        if wid in seen:
            raise ResearchError("invalid_sources", "Duplicate source Work.")
        seen.add(wid)
        if not _fetchone(conn, "SELECT 1 FROM works WHERE id = ?", (wid,)):
            raise ResearchError("work_not_found", "Source Work not found.", 404)
        pages = _pages(raw.get("pages"))
        cleaned.append((wid, pages, i))
    conn.execute("DELETE FROM argument_sources WHERE argument_id = ?", (arg_id,))
    for wid, pages, order in cleaned:
        conn.execute(
            """
            INSERT INTO argument_sources (argument_id, work_id, pages, order_index)
            VALUES (?, ?, ?, ?)
            """,
            (arg_id, wid, pages, order),
        )
    LOGGER.info(
        "argument_source_changed argument_id=%s work_count=%s",
        safe_log_id(arg_id),
        len(cleaned),
    )


def replace_argument_sources(db: PRKSDatabase, argument_id: str, sources) -> dict:
    """Replace the citation list through the revision-aware boundary.

    An ONLINE replacement must advance the same revision an offline client
    measures its own edit against, or that client's conflict detection is
    detecting nothing.
    """
    from backend import argument_sync

    aid = (argument_id or "").strip()
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _fetchone(conn, "SELECT 1 FROM arguments WHERE id = ?", (aid,)):
            raise ResearchError("not_found", "Argument not found.", 404)
        argument_sync.set_sources_on_conn(conn, aid, sources)
        return _argument_bundle(conn, aid)


def _argument_target_cycle(conn: sqlite3.Connection, src_id: str, target_ids: Sequence[str]) -> bool:
    if src_id in target_ids:
        return True
    children: Dict[str, List[str]] = {}
    for r in _fetchall(
        conn,
        "SELECT argument_id, target_argument_id FROM argument_target_arguments",
    ):
        children.setdefault(r["argument_id"], []).append(r["target_argument_id"])
    stack = list(target_ids)
    seen = set()
    while stack:
        nid = stack.pop()
        if nid == src_id:
            return True
        if nid in seen:
            continue
        seen.add(nid)
        stack.extend(children.get(nid, ()))
    return False


def _replace_targets_on_conn(conn: sqlite3.Connection, arg_id: str, targets) -> None:
    if not isinstance(targets, list):
        raise ResearchError("invalid_targets", "Targets must be a JSON array.")
    pos_rows = []
    arg_rows = []
    arg_targets = []
    seen_pos = set()
    seen_arg = set()
    for i, raw in enumerate(targets):
        if not isinstance(raw, dict):
            raise ResearchError("invalid_targets", "Each target must be an object.")
        ttype = raw.get("type")
        tid = raw.get("id")
        verdict = raw.get("verdict_id")
        if not isinstance(ttype, str) or ttype not in ("position", "argument"):
            raise ResearchError("invalid_targets", "Target type must be position or argument.")
        if not isinstance(tid, str) or not tid.strip():
            raise ResearchError("invalid_targets", "Target id is required.")
        if not isinstance(verdict, str) or not verdict.strip():
            raise ResearchError("invalid_verdict", "Every target requires a verdict.")
        tid = tid.strip()
        verdict = verdict.strip()
        vrow = _fetchone(
            conn,
            "SELECT id FROM argument_verdicts WHERE id = ? AND enabled = 1",
            (verdict,),
        )
        if not vrow:
            raise ResearchError("invalid_verdict", "Unknown verdict.")
        if ttype == "position":
            if tid in seen_pos:
                raise ResearchError("invalid_targets", "Duplicate Position target.")
            seen_pos.add(tid)
            if not _fetchone(conn, "SELECT 1 FROM positions WHERE id = ?", (tid,)):
                raise ResearchError("position_not_found", "Position not found.", 404)
            pos_rows.append((tid, verdict, i))
        else:
            if not ARGUMENT_ID_RE.fullmatch(tid):
                raise ResearchError("invalid_targets", "Target Argument id is invalid.")
            if tid in seen_arg:
                raise ResearchError("invalid_targets", "Duplicate Argument target.")
            seen_arg.add(tid)
            if not _fetchone(conn, "SELECT 1 FROM arguments WHERE id = ?", (tid,)):
                raise ResearchError("argument_not_found", "Target Argument not found.", 404)
            arg_rows.append((tid, verdict, i))
            arg_targets.append(tid)
    if _argument_target_cycle(conn, arg_id, arg_targets):
        raise ResearchError(
            "argument_cycle",
            "That target would create an Argument response cycle.",
            409,
        )
    conn.execute("DELETE FROM argument_target_positions WHERE argument_id = ?", (arg_id,))
    conn.execute("DELETE FROM argument_target_arguments WHERE argument_id = ?", (arg_id,))
    for tid, verdict, order in pos_rows:
        conn.execute(
            """
            INSERT INTO argument_target_positions
                (argument_id, position_id, verdict_id, order_index)
            VALUES (?, ?, ?, ?)
            """,
            (arg_id, tid, verdict, order),
        )
    for tid, verdict, order in arg_rows:
        conn.execute(
            """
            INSERT INTO argument_target_arguments
                (argument_id, target_argument_id, verdict_id, order_index)
            VALUES (?, ?, ?, ?)
            """,
            (arg_id, tid, verdict, order),
        )


def replace_argument_targets(db: PRKSDatabase, argument_id: str, targets) -> dict:
    """Replace the whole target list through the revision-aware boundary.

    One list across both target tables, for the same reason the durable family
    treats it as one aggregate: it is a single decision the user made.
    """
    from backend import argument_sync

    aid = (argument_id or "").strip()
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _fetchone(conn, "SELECT 1 FROM arguments WHERE id = ?", (aid,)):
            raise ResearchError("not_found", "Argument not found.", 404)
        argument_sync.set_targets_on_conn(conn, aid, targets)
        return _argument_bundle(conn, aid)


# --- Note save ----------------------------------------------------------------


def save_work_notes_on_conn(conn, db: PRKSDatabase, work_id: str, text_content) -> None:
    """Canonical connection-aware Research Note write.

    Concept resolution/creation and the note body commit in the caller's one
    transaction. Revision ownership lives in ``work_note_sync``; this function
    owns research markup semantics only.
    """
    if not isinstance(text_content, str):
        raise ResearchError("invalid_text", "Research notes must be a string.")
    if _CONTROL_RE.search(text_content):
        raise ResearchError("invalid_text", "Research notes contain invalid characters.")
    wid = (work_id or "").strip()
    markup = parse_research_markup(text_content)
    if not _fetchone(conn, "SELECT 1 FROM works WHERE id = ?", (wid,)):
        raise ResearchError("not_found", "Work not found.", 404)
    names = [ref.name for ref in markup.concept_refs]
    ensure_concepts_for_names(conn, db, names)
    conn.execute(
        """
        UPDATE works SET text_content = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (text_content, wid),
    )


def save_work_notes(db: PRKSDatabase, work_id: str, text_content) -> None:
    """Public canonical note save, revision-aware for every caller."""
    from backend import work_note_sync
    work_note_sync.set_research_note(db, work_id, text_content)
