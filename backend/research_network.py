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
    cname = _concept_name(name)
    desc = _optional_markdown(description, max_len=CONCEPT_DEFINITION_MAX)
    own = conn is None
    if own:
        conn = db.get_connection()
    try:
        status, ids = resolve_concept_key(conn, cname)
        if status == "ok":
            raise ResearchError(
                "concept_exists",
                "A Concept with that name or alias already exists.",
                409,
            )
        if status == "ambiguous":
            raise ResearchError(
                "ambiguous_concept",
                "Multiple Concepts match that name.",
                409,
            )
        cid = db.generate_id("C")
        conn.execute(
            "INSERT INTO concepts (id, name, description) VALUES (?, ?, ?)",
            (cid, cname, desc),
        )
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
    cid = (concept_id or "").strip()
    if not cid:
        raise ResearchError("not_found", "Concept not found.", 404)
    with db.connection() as conn:
        row = _fetchone(conn, "SELECT * FROM concepts WHERE id = ?", (cid,))
        if not row:
            raise ResearchError("not_found", "Concept not found.", 404)
        old_name = row["name"]
        new_name = _concept_name(name) if name is not None else None
        new_desc = (
            _optional_markdown(description, max_len=CONCEPT_DEFINITION_MAX)
            if description is not None
            else None
        )
        if new_name is None and new_desc is None:
            raise ResearchError("nothing_to_update", "Nothing to update.")
        if new_name is not None and new_name != old_name:
            old_key = normalize_concept_key(old_name)
            new_key = normalize_concept_key(new_name)
            identity_changed = new_key != old_key
            if identity_changed:
                status, ids = resolve_concept_key(conn, new_name)
                if status == "ok" and ids[0] != cid:
                    raise ResearchError(
                        "concept_exists",
                        "A Concept with that name or alias already exists.",
                        409,
                    )
                if status == "ambiguous":
                    others = [i for i in ids if i != cid]
                    if others:
                        raise ResearchError(
                            "ambiguous_concept",
                            "Multiple Concepts match that name.",
                            409,
                        )
            conn.execute(
                "UPDATE concepts SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_name, cid),
            )
            if identity_changed:
                existing_alias = _fetchone(
                    conn,
                    "SELECT 1 FROM concept_aliases WHERE concept_id = ? AND normalized_alias = ?",
                    (cid, old_key),
                )
                clash = _fetchone(
                    conn,
                    "SELECT concept_id FROM concept_aliases WHERE normalized_alias = ?",
                    (old_key,),
                )
                if not existing_alias and (not clash or clash["concept_id"] == cid):
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO concept_aliases
                            (concept_id, alias, normalized_alias)
                        VALUES (?, ?, ?)
                        """,
                        (cid, old_name, old_key),
                    )
        if new_desc is not None:
            conn.execute(
                "UPDATE concepts SET description = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_desc, cid),
            )
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
    cid = (concept_id or "").strip()
    if not cid:
        raise ResearchError("not_found", "Concept not found.", 404)
    with db.connection() as conn:
        row = _fetchone(conn, "SELECT 1 FROM concepts WHERE id = ?", (cid,))
        if not row:
            raise ResearchError("not_found", "Concept not found.", 404)
        if _canonical_notes_reference_concept(conn, cid):
            raise ResearchError(
                "concept_in_use",
                "This Concept is still referenced in research notes. Remove or replace those references before deleting it.",
                409,
            )
        conn.execute("DELETE FROM concepts WHERE id = ?", (cid,))
    LOGGER.info("concept_deleted concept_id=%s", safe_log_id(cid))


def replace_concept_aliases(db: PRKSDatabase, concept_id: str, aliases) -> dict:
    cid = (concept_id or "").strip()
    if not isinstance(aliases, list):
        raise ResearchError("invalid_aliases", "Aliases must be a JSON array.")
    names = []
    seen_keys = set()
    for raw in aliases:
        alias = _concept_name(raw) if isinstance(raw, str) else None
        if alias is None:
            raise ResearchError("invalid_aliases", "Each alias must be a string.")
        key = normalize_concept_key(alias)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        names.append((alias, key))
    with db.connection() as conn:
        row = _fetchone(conn, "SELECT id, name FROM concepts WHERE id = ?", (cid,))
        if not row:
            raise ResearchError("not_found", "Concept not found.", 404)
        own_key = normalize_concept_key(row["name"])
        for alias, key in names:
            if key == own_key:
                continue
            hits = _normalized_hits(conn, key)
            others = [i for i in hits if i != cid]
            if others:
                raise ResearchError(
                    "alias_conflict",
                    "That search key already belongs to another Concept.",
                    409,
                )
        conn.execute("DELETE FROM concept_aliases WHERE concept_id = ?", (cid,))
        for alias, key in names:
            if key == own_key:
                continue
            conn.execute(
                """
                INSERT INTO concept_aliases (concept_id, alias, normalized_alias)
                VALUES (?, ?, ?)
                """,
                (cid, alias, key),
            )
        LOGGER.info("concept_aliases_changed concept_id=%s alias_count=%s", safe_log_id(cid), len(names))
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
    cid = (concept_id or "").strip()
    if not isinstance(parent_ids, list):
        raise ResearchError("invalid_parents", "Parents must be a JSON array.")
    pids = []
    seen = set()
    for raw in parent_ids:
        if not isinstance(raw, str) or not raw.strip():
            raise ResearchError("invalid_parents", "Each parent id must be a string.")
        pid = raw.strip()
        if pid in seen:
            continue
        seen.add(pid)
        pids.append(pid)
    with db.connection() as conn:
        row = _fetchone(conn, "SELECT 1 FROM concepts WHERE id = ?", (cid,))
        if not row:
            raise ResearchError("not_found", "Concept not found.", 404)
        for pid in pids:
            if not _fetchone(conn, "SELECT 1 FROM concepts WHERE id = ?", (pid,)):
                raise ResearchError("parent_not_found", "Parent Concept not found.", 404)
        if _parent_cycle(conn, cid, pids):
            raise ResearchError(
                "concept_cycle",
                "That parent would create a Concept hierarchy cycle.",
                409,
            )
        conn.execute("DELETE FROM concept_parents WHERE child_concept_id = ?", (cid,))
        for pid in pids:
            conn.execute(
                """
                INSERT INTO concept_parents (child_concept_id, parent_concept_id)
                VALUES (?, ?)
                """,
                (cid, pid),
            )
        LOGGER.info("concept_parents_changed concept_id=%s parent_count=%s", safe_log_id(cid), len(pids))
        return get_concept_on_conn(conn, cid)


def ensure_concepts_for_names(conn: sqlite3.Connection, db: PRKSDatabase, names: Iterable[str]) -> Dict[str, str]:
    """Resolve or create Concepts for canonical names. Map written-normalized key → id."""
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
        conn.execute(
            "INSERT INTO concepts (id, name, description) VALUES (?, ?, ?)",
            (cid, name, ""),
        )
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
    n = _plain_name(name, max_len=POSITION_NAME_MAX)
    d = _optional_markdown(description, max_len=POSITION_DESCRIPTION_MAX)
    pid = db.generate_id("P")
    db.execute_query(
        "INSERT INTO positions (id, name, description) VALUES (?, ?, ?)",
        (pid, n, d),
    )
    LOGGER.info("position_created position_id=%s", safe_log_id(pid))
    return get_position(db, pid)


def update_position(db: PRKSDatabase, position_id: str, *, name=None, description=None) -> dict:
    pid = (position_id or "").strip()
    if not pid:
        raise ResearchError("not_found", "Position not found.", 404)
    with db.connection() as conn:
        if not _fetchone(conn, "SELECT 1 FROM positions WHERE id = ?", (pid,)):
            raise ResearchError("not_found", "Position not found.", 404)
        if name is None and description is None:
            raise ResearchError("nothing_to_update", "Nothing to update.")
        if name is not None:
            conn.execute(
                "UPDATE positions SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (_plain_name(name, max_len=POSITION_NAME_MAX), pid),
            )
        if description is not None:
            conn.execute(
                "UPDATE positions SET description = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (_optional_markdown(description, max_len=POSITION_DESCRIPTION_MAX), pid),
            )
    LOGGER.info("position_updated position_id=%s", safe_log_id(pid))
    return get_position(db, pid)


def delete_position(db: PRKSDatabase, position_id: str) -> None:
    pid = (position_id or "").strip()
    if not pid:
        raise ResearchError("not_found", "Position not found.", 404)
    with db.connection() as conn:
        if not _fetchone(conn, "SELECT 1 FROM positions WHERE id = ?", (pid,)):
            raise ResearchError("not_found", "Position not found.", 404)
        used = _fetchone(
            conn,
            "SELECT 1 FROM argument_target_positions WHERE position_id = ? LIMIT 1",
            (pid,),
        )
        if used:
            raise ResearchError(
                "position_in_use",
                "This Position is still targeted by an Argument or Stance.",
                409,
            )
        conn.execute("DELETE FROM positions WHERE id = ?", (pid,))
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
    n = _plain_name(name, max_len=ARGUMENT_NAME_MAX)
    k = _validate_kind(kind)
    text = _optional_markdown(main_text, max_len=ARGUMENT_TEXT_MAX)
    aid = db.generate_id("A")
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO arguments (id, name, kind, main_text) VALUES (?, ?, ?, ?)",
            (aid, n, k, text),
        )
        if sources is not None:
            _replace_sources_on_conn(conn, aid, sources)
        if targets is not None:
            _replace_targets_on_conn(conn, aid, targets)
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
    aid = (argument_id or "").strip()
    if not aid:
        raise ResearchError("not_found", "Argument not found.", 404)
    with db.connection() as conn:
        if not _fetchone(conn, "SELECT 1 FROM arguments WHERE id = ?", (aid,)):
            raise ResearchError("not_found", "Argument not found.", 404)
        if name is None and kind is None and main_text is None:
            raise ResearchError("nothing_to_update", "Nothing to update.")
        if name is not None:
            conn.execute(
                "UPDATE arguments SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (_plain_name(name, max_len=ARGUMENT_NAME_MAX), aid),
            )
        if kind is not None:
            conn.execute(
                "UPDATE arguments SET kind = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (_validate_kind(kind), aid),
            )
        if main_text is not None:
            conn.execute(
                "UPDATE arguments SET main_text = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (_optional_markdown(main_text, max_len=ARGUMENT_TEXT_MAX), aid),
            )
        LOGGER.info("argument_updated argument_id=%s", safe_log_id(aid))
        return _argument_bundle(conn, aid)


def delete_argument(db: PRKSDatabase, argument_id: str) -> None:
    aid = (argument_id or "").strip()
    if not aid:
        raise ResearchError("not_found", "Argument not found.", 404)
    with db.connection() as conn:
        if not _fetchone(conn, "SELECT 1 FROM arguments WHERE id = ?", (aid,)):
            raise ResearchError("not_found", "Argument not found.", 404)
        if _canonical_notes_reference_argument(conn, aid):
            raise ResearchError(
                "argument_in_use",
                "This Argument is still referenced in research notes.",
                409,
            )
        targeted = _fetchone(
            conn,
            "SELECT 1 FROM argument_target_arguments WHERE target_argument_id = ? LIMIT 1",
            (aid,),
        )
        if targeted:
            raise ResearchError(
                "argument_targeted",
                "Another Argument or Stance still responds to this record.",
                409,
            )
        conn.execute("DELETE FROM arguments WHERE id = ?", (aid,))
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
    aid = (argument_id or "").strip()
    with db.connection() as conn:
        if not _fetchone(conn, "SELECT 1 FROM arguments WHERE id = ?", (aid,)):
            raise ResearchError("not_found", "Argument not found.", 404)
        _replace_sources_on_conn(conn, aid, sources)
        conn.execute(
            "UPDATE arguments SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (aid,),
        )
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
    aid = (argument_id or "").strip()
    with db.connection() as conn:
        if not _fetchone(conn, "SELECT 1 FROM arguments WHERE id = ?", (aid,)):
            raise ResearchError("not_found", "Argument not found.", 404)
        _replace_targets_on_conn(conn, aid, targets)
        conn.execute(
            "UPDATE arguments SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (aid,),
        )
        return _argument_bundle(conn, aid)


# --- Note save ----------------------------------------------------------------


def save_work_notes(db: PRKSDatabase, work_id: str, text_content) -> None:
    if not isinstance(text_content, str):
        raise ResearchError("invalid_text", "Research notes must be a string.")
    if _CONTROL_RE.search(text_content):
        raise ResearchError("invalid_text", "Research notes contain invalid characters.")
    wid = (work_id or "").strip()
    markup = parse_research_markup(text_content)
    with db.connection() as conn:
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
