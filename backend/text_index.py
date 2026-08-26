import os
import re
import sqlite3
from typing import Any, Dict, List

from backend.storage import paths


_MAX_EXTRACTED_CHARS = 2_000_000

_is_testing_env = paths.is_testing
_resolve_storage_root = paths.resolve_defaulted_storage_root
_resolve_index_db_path = paths.resolve_index_db_path
_resolve_pdfs_dir = paths.resolve_pdfs_dir


def _safe_pdf_path(filename: str) -> str | None:
    root = os.path.realpath(_resolve_pdfs_dir())
    candidate = os.path.realpath(os.path.join(root, filename))
    if candidate == root or not candidate.startswith(root + os.sep):
        return None
    return candidate


def _search_tokens(raw: str) -> List[str]:
    q = (raw or "").strip().lower()
    if not q:
        return []
    q = re.sub(r"[-_]+", " ", q)
    q = re.sub(r"[^\w\s]", " ", q, flags=re.UNICODE)
    return [t for t in q.split() if t]


def _fts_prefix_clause(tokens: List[str]) -> str:
    parts: List[str] = []
    for tok in tokens:
        esc = tok.replace('"', '""')
        parts.append(f'"{esc}"*')
    return " ".join(parts)


class PRKSTextIndex:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or _resolve_index_db_path()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS work_text_index (
                    work_id TEXT PRIMARY KEY,
                    extracted_text TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS work_text_index_fts USING fts5(
                    extracted_text,
                    content='work_text_index',
                    content_rowid='rowid'
                );
                CREATE TRIGGER IF NOT EXISTS work_text_index_ai AFTER INSERT ON work_text_index BEGIN
                    INSERT INTO work_text_index_fts(rowid, extracted_text)
                    VALUES (new.rowid, new.extracted_text);
                END;
                CREATE TRIGGER IF NOT EXISTS work_text_index_ad AFTER DELETE ON work_text_index BEGIN
                    INSERT INTO work_text_index_fts(work_text_index_fts, rowid, extracted_text)
                    VALUES ('delete', old.rowid, old.extracted_text);
                END;
                CREATE TRIGGER IF NOT EXISTS work_text_index_au AFTER UPDATE ON work_text_index BEGIN
                    INSERT INTO work_text_index_fts(work_text_index_fts, rowid, extracted_text)
                    VALUES ('delete', old.rowid, old.extracted_text);
                    INSERT INTO work_text_index_fts(rowid, extracted_text)
                    VALUES (new.rowid, new.extracted_text);
                END;
                """
            )
            conn.commit()

    @staticmethod
    def extract_pdf_text(pdf_path: str, max_chars: int = _MAX_EXTRACTED_CHARS) -> str:
        try:
            import fitz  # PyMuPDF
        except Exception:
            return ""
        parts: List[str] = []
        try:
            with fitz.open(pdf_path) as doc:
                for page in doc:
                    parts.append(page.get_text("text") or "")
                    if sum(len(p) for p in parts) >= max_chars:
                        break
        except Exception:
            return ""
        txt = "\n".join(parts)
        if len(txt) > max_chars:
            txt = txt[:max_chars]
        return txt

    def upsert_from_pdf(self, work_id: str, pdf_path: str) -> int:
        work_id = (work_id or "").strip()
        if not work_id:
            return 0
        text = self.extract_pdf_text(pdf_path)
        return self.upsert_text(work_id, text)

    def upsert_text(self, work_id: str, extracted_text: str) -> int:
        work_id = (work_id or "").strip()
        if not work_id:
            return 0
        text = (extracted_text or "")[:_MAX_EXTRACTED_CHARS]
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO work_text_index (work_id, extracted_text, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(work_id) DO UPDATE SET
                    extracted_text = excluded.extracted_text,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (work_id, text),
            )
            conn.commit()
        return 1

    def remove_work(self, work_id: str) -> int:
        work_id = (work_id or "").strip()
        if not work_id:
            return 0
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM work_text_index WHERE work_id = ?", (work_id,))
            conn.commit()
            return cur.rowcount or 0

    def search_work_ids(self, term: str, limit: int = 2000) -> List[str]:
        tokens = _search_tokens(term)
        if not tokens:
            return []
        clause = _fts_prefix_clause(tokens)
        if not clause:
            return []
        lim = max(1, int(limit))
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    """
                    SELECT work_text_index.work_id AS work_id
                    FROM work_text_index
                    JOIN work_text_index_fts ON work_text_index.rowid = work_text_index_fts.rowid
                    WHERE work_text_index_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (clause, lim),
                ).fetchall()
            return [str(r["work_id"]) for r in rows if r["work_id"]]
        except sqlite3.OperationalError:
            return []

    def reindex_all(self, db: Any) -> Dict[str, int]:
        rows = db.execute_query(
            "SELECT id, file_path FROM works WHERE COALESCE(file_path,'') LIKE '/api/pdfs/%'",
            (),
        )
        processed = 0
        indexed = 0
        failed = 0
        for row in rows:
            processed += 1
            wid = (row.get("id") or "").strip()
            fp = (row.get("file_path") or "").strip()
            filename = fp.split("/")[-1] if fp.startswith("/api/pdfs/") else ""
            abs_path = _safe_pdf_path(filename) if filename else None
            if not abs_path or not os.path.exists(abs_path):
                failed += 1
                continue
            try:
                indexed += self.upsert_from_pdf(wid, abs_path)
            except Exception:
                failed += 1
        return {"processed": processed, "indexed": indexed, "failed": failed}


_TEXT_INDEX_SINGLETON: PRKSTextIndex | None = None


def get_text_index() -> PRKSTextIndex:
    global _TEXT_INDEX_SINGLETON
    if _TEXT_INDEX_SINGLETON is None:
        _TEXT_INDEX_SINGLETON = PRKSTextIndex()
    return _TEXT_INDEX_SINGLETON
