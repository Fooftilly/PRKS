"""Disposable derived research-reference index.

Canonical notes and Concept/Argument records live in prks_data.db.
This database only stores rebuildable mention offsets.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from backend.db_manager import PRKSDatabase
from backend.log_safety import safe_error_type, safe_log_id, safe_log_label
from backend.research_markup import normalize_concept_key, parse_research_markup
from backend.storage.config import StorageConfig

LOGGER = logging.getLogger("prks.research_index")

RESEARCH_INDEX_SCHEMA_VERSION = 1
_SNIPPET_RADIUS = 80

_CURRENT_TABLES = frozenset(
    {
        "research_index_meta",
        "work_note_state",
        "concept_mentions",
        "argument_mentions",
    }
)


class _DerivedIndexUnusable(Exception):
    def __init__(self, reason: str):
        self.reason = reason


def _discard_index_files(db_path: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = db_path + suffix
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class PRKSResearchIndex:
    def __init__(
        self,
        db_path: str | None = None,
        *,
        storage: Optional[StorageConfig] = None,
    ):
        if storage is not None and db_path is not None:
            if db_path != storage.research_index_db_path:
                raise ValueError("db_path conflicts with storage.research_index_db_path")
            self.storage = storage
            self.db_path = db_path
        elif storage is not None:
            self.storage = storage
            self.db_path = storage.research_index_db_path
        elif db_path is not None:
            self.storage = StorageConfig.from_env()
            self.db_path = db_path
        else:
            self.storage = StorageConfig.from_env()
            self.db_path = self.storage.research_index_db_path
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._open_or_recover()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _open_or_recover(self) -> None:
        try:
            self._prepare_schema()
            return
        except _DerivedIndexUnusable as exc:
            reason = exc.reason
        except sqlite3.DatabaseError:
            reason = "corrupt"
        except sqlite3.OperationalError:
            reason = "corrupt"
        self._recreate(reason)

    def _prepare_schema(self) -> None:
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            kind = self._classify_schema(conn)
            if kind == "empty":
                self._create_schema(conn)
                conn.commit()
                return
            if kind == "current":
                return
            raise _DerivedIndexUnusable("schema_invalid")

    def _recreate(self, reason: str) -> None:
        LOGGER.warning("research_index_recreated reason=%s", safe_log_label(reason))
        _discard_index_files(self.db_path)
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            self._create_schema(conn)
            conn.commit()

    def _user_tables(self, conn: sqlite3.Connection) -> set[str]:
        return {
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
            if r[0] and not str(r[0]).startswith("sqlite_")
        }

    def _classify_schema(self, conn: sqlite3.Connection) -> str:
        tables = self._user_tables(conn)
        if not tables:
            return "empty"
        if tables != _CURRENT_TABLES:
            return "invalid"
        row = conn.execute(
            "SELECT value FROM research_index_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            return "invalid"
        try:
            version = int(row[0])
        except (TypeError, ValueError):
            return "invalid"
        if version != RESEARCH_INDEX_SCHEMA_VERSION:
            return "invalid"
        return "current"

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE research_index_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE work_note_state (
                work_id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE concept_mentions (
                work_id TEXT NOT NULL,
                concept_id TEXT NOT NULL,
                start_offset INTEGER NOT NULL,
                end_offset INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE argument_mentions (
                work_id TEXT NOT NULL,
                argument_id TEXT NOT NULL,
                start_offset INTEGER NOT NULL,
                end_offset INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX idx_concept_mentions_concept ON concept_mentions(concept_id)"
        )
        conn.execute(
            "CREATE INDEX idx_concept_mentions_work ON concept_mentions(work_id)"
        )
        conn.execute(
            "CREATE INDEX idx_argument_mentions_argument ON argument_mentions(argument_id)"
        )
        conn.execute(
            "CREATE INDEX idx_argument_mentions_work ON argument_mentions(work_id)"
        )
        conn.execute(
            "INSERT INTO research_index_meta (key, value) VALUES ('schema_version', ?)",
            (str(RESEARCH_INDEX_SCHEMA_VERSION),),
        )

    def sync_work(self, work_id: str, text_content: str, db: PRKSDatabase) -> None:
        wid = (work_id or "").strip()
        if not wid:
            return
        digest = content_hash(text_content or "")
        markup = parse_research_markup(text_content or "")
        concept_ids = self._resolve_concept_ids(db, [r.name for r in markup.concept_refs])
        concept_rows = []
        for ref in markup.concept_refs:
            cid = concept_ids.get(normalize_concept_key(ref.name))
            if cid:
                concept_rows.append((wid, cid, ref.start, ref.end))
        argument_rows = [
            (wid, ref.argument_id, ref.start, ref.end) for ref in markup.argument_refs
        ]
        with self._conn() as conn:
            conn.execute("DELETE FROM concept_mentions WHERE work_id = ?", (wid,))
            conn.execute("DELETE FROM argument_mentions WHERE work_id = ?", (wid,))
            conn.execute(
                """
                INSERT INTO work_note_state (work_id, content_hash)
                VALUES (?, ?)
                ON CONFLICT(work_id) DO UPDATE SET content_hash = excluded.content_hash
                """,
                (wid, digest),
            )
            conn.executemany(
                """
                INSERT INTO concept_mentions (work_id, concept_id, start_offset, end_offset)
                VALUES (?, ?, ?, ?)
                """,
                concept_rows,
            )
            conn.executemany(
                """
                INSERT INTO argument_mentions (work_id, argument_id, start_offset, end_offset)
                VALUES (?, ?, ?, ?)
                """,
                argument_rows,
            )
            conn.commit()
        LOGGER.info(
            "research_reference_sync work_id=%s concept_count=%s argument_count=%s",
            safe_log_id(wid),
            len(concept_rows),
            len(argument_rows),
        )

    def _resolve_concept_ids(self, db: PRKSDatabase, names: List[str]) -> Dict[str, str]:
        from backend.research_network import resolve_concept_key

        out: Dict[str, str] = {}
        with db.get_connection() as conn:
            for name in names:
                key = normalize_concept_key(name)
                if key in out:
                    continue
                status, ids = resolve_concept_key(conn, name)
                if status == "ok":
                    out[key] = ids[0]
        return out

    def remove_work(self, work_id: str) -> None:
        wid = (work_id or "").strip()
        if not wid:
            return
        with self._conn() as conn:
            conn.execute("DELETE FROM concept_mentions WHERE work_id = ?", (wid,))
            conn.execute("DELETE FROM argument_mentions WHERE work_id = ?", (wid,))
            conn.execute("DELETE FROM work_note_state WHERE work_id = ?", (wid,))
            conn.commit()

    def mention_count_for_concept(self, concept_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM concept_mentions WHERE concept_id = ?",
                (concept_id,),
            ).fetchone()
        return int(row["c"] if row else 0)

    def mention_count_for_argument(self, argument_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM argument_mentions WHERE argument_id = ?",
                (argument_id,),
            ).fetchone()
        return int(row["c"] if row else 0)

    def concept_mention_counts(self) -> Dict[str, int]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT concept_id, COUNT(*) AS c FROM concept_mentions GROUP BY concept_id"
            ).fetchall()
        return {r["concept_id"]: int(r["c"]) for r in rows}

    def argument_mention_counts(self) -> Dict[str, int]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT argument_id, COUNT(*) AS c FROM argument_mentions GROUP BY argument_id"
            ).fetchall()
        return {r["argument_id"]: int(r["c"]) for r in rows}

    def work_research_refs(self, work_id: str, db: PRKSDatabase) -> Dict[str, List[dict]]:
        wid = (work_id or "").strip()
        concepts = []
        arguments = []
        with self._conn() as conn:
            c_rows = conn.execute(
                """
                SELECT DISTINCT concept_id FROM concept_mentions WHERE work_id = ?
                """,
                (wid,),
            ).fetchall()
            a_rows = conn.execute(
                """
                SELECT DISTINCT argument_id FROM argument_mentions WHERE work_id = ?
                """,
                (wid,),
            ).fetchall()
        if c_rows:
            ids = [r["concept_id"] for r in c_rows]
            ph = ",".join("?" * len(ids))
            name_rows = db.execute_query(
                f"SELECT id, name FROM concepts WHERE id IN ({ph})",
                tuple(ids),
            )
            by_id = {r["id"]: r["name"] for r in name_rows}
            alias_map: Dict[str, List[str]] = {}
            alias_rows = db.execute_query(
                f"SELECT concept_id, alias FROM concept_aliases WHERE concept_id IN ({ph})",
                tuple(ids),
            )
            for ar in alias_rows:
                alias_map.setdefault(ar["concept_id"], []).append(ar["alias"])
            for cid in ids:
                concepts.append(
                    {
                        "id": cid,
                        "name": by_id.get(cid, ""),
                        "aliases": alias_map.get(cid, []),
                    }
                )
        if a_rows:
            ids = [r["argument_id"] for r in a_rows]
            ph = ",".join("?" * len(ids))
            name_rows = db.execute_query(
                f"SELECT id, name FROM arguments WHERE id IN ({ph})",
                tuple(ids),
            )
            by_id = {r["id"]: r["name"] for r in name_rows}
            for aid in ids:
                arguments.append({"id": aid, "name": by_id.get(aid, "")})
        return {"concepts": concepts, "arguments": arguments}

    def concept_backlinks(self, concept_id: str, db: PRKSDatabase) -> List[dict]:
        cid = (concept_id or "").strip()
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT work_id, start_offset, end_offset
                FROM concept_mentions
                WHERE concept_id = ?
                ORDER BY work_id ASC, start_offset ASC
                """,
                (cid,),
            ).fetchall()
        return self._backlinks_from_rows(db, rows)

    def argument_backlinks(self, argument_id: str, db: PRKSDatabase) -> List[dict]:
        aid = (argument_id or "").strip()
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT work_id, start_offset, end_offset
                FROM argument_mentions
                WHERE argument_id = ?
                ORDER BY work_id ASC, start_offset ASC
                """,
                (aid,),
            ).fetchall()
        return self._backlinks_from_rows(db, rows)

    def _backlinks_from_rows(self, db: PRKSDatabase, rows) -> List[dict]:
        if not rows:
            return []
        work_ids = []
        seen = set()
        for r in rows:
            wid = r["work_id"]
            if wid not in seen:
                seen.add(wid)
                work_ids.append(wid)
        ph = ",".join("?" * len(work_ids))
        works = db.execute_query(
            f"SELECT id, title, text_content FROM works WHERE id IN ({ph})",
            tuple(work_ids),
        )
        by_id = {w["id"]: w for w in works}
        grouped: Dict[str, dict] = {}
        for r in rows:
            wid = r["work_id"]
            work = by_id.get(wid)
            if not work:
                continue
            bucket = grouped.setdefault(
                wid,
                {"work_id": wid, "title": work.get("title") or "", "occurrences": []},
            )
            text = work.get("text_content") or ""
            start = int(r["start_offset"])
            end = int(r["end_offset"])
            snippet = _snippet(text, start, end)
            bucket["occurrences"].append(
                {"start": start, "end": end, "snippet": snippet}
            )
        return [grouped[wid] for wid in work_ids if wid in grouped]

    def reconcile_all(self, db: PRKSDatabase) -> Dict[str, int]:
        summary = {
            "processed": 0,
            "updated": 0,
            "unchanged": 0,
            "removed_orphans": 0,
        }
        try:
            return self._reconcile_inner(db, summary)
        except (_DerivedIndexUnusable, sqlite3.DatabaseError, sqlite3.OperationalError) as exc:
            LOGGER.warning(
                "research_index_recreated reason=%s",
                safe_log_label(safe_error_type(exc)),
            )
            self._recreate("corrupt")
            return self._reconcile_inner(db, summary)

    def _reconcile_inner(self, db: PRKSDatabase, summary: Dict[str, int]) -> Dict[str, int]:
        works = db.execute_query("SELECT id, text_content FROM works")
        canonical = {w["id"]: w.get("text_content") or "" for w in works}
        with self._conn() as conn:
            state_rows = conn.execute("SELECT work_id, content_hash FROM work_note_state").fetchall()
        state = {r["work_id"]: r["content_hash"] for r in state_rows}
        for wid, text in canonical.items():
            summary["processed"] += 1
            digest = content_hash(text)
            if state.get(wid) == digest:
                summary["unchanged"] += 1
                continue
            self.sync_work(wid, text, db)
            summary["updated"] += 1
        for wid in list(state):
            if wid not in canonical:
                self.remove_work(wid)
                summary["removed_orphans"] += 1
        LOGGER.info(
            "research_index_reconciled processed=%s updated=%s unchanged=%s removed_orphans=%s",
            summary["processed"],
            summary["updated"],
            summary["unchanged"],
            summary["removed_orphans"],
        )
        return summary


def _snippet(text: str, start: int, end: int) -> str:
    if not text:
        return ""
    a = max(0, start - _SNIPPET_RADIUS)
    b = min(len(text), end + _SNIPPET_RADIUS)
    chunk = text[a:b].replace("\n", " ")
    if a > 0:
        chunk = "…" + chunk
    if b < len(text):
        chunk = chunk + "…"
    return chunk


_BOUND: PRKSResearchIndex | None = None


def get_research_index() -> PRKSResearchIndex:
    if _BOUND is None:
        raise RuntimeError("research index is not bound; call replace_research_index() first")
    return _BOUND


def replace_research_index(index: PRKSResearchIndex) -> PRKSResearchIndex | None:
    global _BOUND
    previous = _BOUND
    _BOUND = index
    return previous


def reset_research_index() -> None:
    global _BOUND
    _BOUND = None


def reconcile_research_index_at_startup(db: PRKSDatabase, index: PRKSResearchIndex) -> None:
    try:
        index.reconcile_all(db)
    except Exception as exc:
        LOGGER.warning(
            "research_index_reconcile_failed error_type=%s",
            safe_error_type(exc),
        )
