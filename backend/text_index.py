import hashlib
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from backend.db_manager import managed_pdf_filename, safe_pdf_path_under_dir
from backend.log_safety import safe_error_type, safe_log_id, safe_log_label
from backend.performance import span as perf_span
from backend.storage.config import StorageConfig


_MAX_EXTRACTED_CHARS = 2_000_000

TEXT_INDEX_SCHEMA_VERSION = 2
TEXT_EXTRACTOR_VERSION = 1

STATUS_INDEXED = "indexed"
STATUS_EMPTY = "empty"
STATUS_FAILED = "failed"
STATUS_LEGACY = "legacy"

_SUCCESS_STATUSES = frozenset({STATUS_INDEXED, STATUS_EMPTY})
_CURRENT_COLUMNS = frozenset(
    {
        "work_id",
        "extracted_text",
        "source_ref_hash",
        "source_size",
        "source_mtime_ns",
        "extractor_version",
        "extraction_status",
        "truncated",
        "updated_at",
    }
)
_LEGACY_COLUMNS = frozenset({"work_id", "extracted_text", "updated_at"})
_TRIGGER_NAMES = frozenset(
    {
        "work_text_index_ai",
        "work_text_index_ad",
        "work_text_index_au",
    }
)

LOGGER = logging.getLogger("prks.text_index")


class _DerivedIndexUnusable(Exception):
    def __init__(self, reason: str):
        self.reason = reason


class PDFTextExtractorUnavailable(Exception):
    """PyMuPDF cannot be imported; extraction is globally unavailable."""


class PDFTextExtractionError(Exception):
    """Per-PDF extraction failed. ``reason`` is a privacy-safe error_type label."""

    def __init__(self, reason: str = "extraction_failed"):
        self.reason = reason or "extraction_failed"


@dataclass(frozen=True)
class PDFTextExtraction:
    text: str
    empty: bool
    truncated: bool


@dataclass(frozen=True)
class SourceFingerprint:
    source_ref_hash: str
    source_size: int
    source_mtime_ns: int
    extractor_version: int


@dataclass(frozen=True)
class TextIndexSyncResult:
    action: str
    status: str | None = None
    truncated: bool = False
    extracted: bool = False


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


def _source_ref_hash(file_path: str) -> str:
    return hashlib.sha256(file_path.encode("utf-8")).hexdigest()


def _normalize_sql(sql: str | None) -> str:
    return " ".join((sql or "").split()).lower()


def extractor_available() -> bool:
    try:
        import fitz  # noqa: F401
    except Exception:
        return False
    return True


def extract_pdf(
    pdf_path: str, max_chars: int = _MAX_EXTRACTED_CHARS
) -> PDFTextExtraction:
    try:
        import fitz
    except Exception:
        raise PDFTextExtractorUnavailable() from None
    parts: List[str] = []
    try:
        with fitz.open(pdf_path) as doc:
            for page in doc:
                parts.append(page.get_text("text") or "")
                if sum(len(p) for p in parts) >= max_chars:
                    break
    except PDFTextExtractorUnavailable:
        raise
    except Exception as exc:
        raise PDFTextExtractionError(safe_error_type(exc)) from None
    txt = "\n".join(parts)
    truncated = len(txt) > max_chars
    if truncated:
        txt = txt[:max_chars]
    if not txt.strip():
        return PDFTextExtraction(text="", empty=True, truncated=False)
    return PDFTextExtraction(text=txt, empty=False, truncated=truncated)


def _empty_summary(
    *, extractor_unavailable: bool = False, fts_rebuilt: bool = False
) -> Dict[str, Any]:
    return {
        "processed": 0,
        "indexed": 0,
        "updated": 0,
        "unchanged": 0,
        "empty": 0,
        "truncated": 0,
        "missing": 0,
        "failed": 0,
        "removed_orphans": 0,
        "fts_rebuilt": fts_rebuilt,
        "extractor_unavailable": extractor_unavailable,
        "skipped": 0,
    }


def _discard_index_files(db_path: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = db_path + suffix
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


class PRKSTextIndex:
    def __init__(
        self,
        db_path: str | None = None,
        *,
        storage: Optional[StorageConfig] = None,
    ):
        if storage is not None and db_path is not None:
            if db_path != storage.index_db_path:
                raise ValueError("db_path conflicts with storage.index_db_path")
            self.storage = storage
            self.db_path = db_path
        elif storage is not None:
            self.storage = storage
            self.db_path = storage.index_db_path
        elif db_path is not None:
            self.storage = StorageConfig.from_env()
            self.db_path = db_path
        else:
            self.storage = StorageConfig.from_env()
            self.db_path = self.storage.index_db_path
        self.pdfs_dir = self.storage.pdfs_dir
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._last_recovery_reason: str | None = None
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
                self._create_current_schema(conn)
                conn.commit()
                return
            if kind == "current":
                return
            if kind == "legacy":
                self._upgrade_legacy_schema(conn)
                conn.commit()
                return
            raise _DerivedIndexUnusable("schema_invalid")

    def _recreate(self, reason: str) -> None:
        self._last_recovery_reason = reason
        LOGGER.warning("text_index_recreated reason=%s", safe_log_label(reason))
        _discard_index_files(self.db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            self._create_current_schema(conn)
            conn.commit()

    def _classify_schema(self, conn: sqlite3.Connection) -> str:
        tables = self._user_tables(conn)
        if not tables:
            return "empty"
        if "work_text_index" not in tables:
            return "invalid"
        columns = self._table_columns(conn, "work_text_index")
        col_names = frozenset(columns)
        if col_names == _LEGACY_COLUMNS:
            return "legacy"
        if col_names != _CURRENT_COLUMNS:
            return "invalid"
        if not self._current_schema_valid(conn, columns):
            return "invalid"
        return "current"

    def _user_tables(self, conn: sqlite3.Connection) -> set[str]:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {str(r[0]) for r in rows}

    def _table_columns(self, conn: sqlite3.Connection, name: str) -> Dict[str, sqlite3.Row]:
        rows = conn.execute(f"PRAGMA table_info({name})").fetchall()
        return {str(r["name"]): r for r in rows}

    def _current_schema_valid(
        self, conn: sqlite3.Connection, columns: Dict[str, sqlite3.Row]
    ) -> bool:
        if columns["work_id"]["pk"] != 1:
            return False
        pk_cols = [name for name, row in columns.items() if row["pk"]]
        if pk_cols != ["work_id"]:
            return False
        if not columns["extracted_text"]["notnull"]:
            return False
        if not columns["extraction_status"]["notnull"]:
            return False
        if not columns["truncated"]["notnull"]:
            return False
        if "text_index_meta" not in self._user_tables(conn):
            return False
        version = self._meta_int(conn, "schema_version")
        if version != TEXT_INDEX_SCHEMA_VERSION:
            return False
        if not self._fts_definition_valid(conn):
            return False
        if not self._triggers_valid(conn):
            return False
        return True

    def _fts_definition_valid(self, conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'work_text_index_fts'"
        ).fetchone()
        if row is None:
            return False
        sql = _normalize_sql(row["sql"])
        if "using fts5" not in sql:
            return False
        if "extracted_text" not in sql:
            return False
        if not re.search(r"content\s*=\s*['\"]work_text_index['\"]", sql):
            return False
        if not re.search(r"content_rowid\s*=\s*['\"]rowid['\"]", sql):
            return False
        return True

    def _triggers_valid(self, conn: sqlite3.Connection) -> bool:
        rows = conn.execute(
            """
            SELECT name, sql FROM sqlite_master
            WHERE type = 'trigger' AND tbl_name = 'work_text_index'
            """
        ).fetchall()
        by_name = {str(r["name"]): _normalize_sql(r["sql"]) for r in rows}
        if set(by_name) != _TRIGGER_NAMES:
            return False
        ai = by_name["work_text_index_ai"]
        ad = by_name["work_text_index_ad"]
        au = by_name["work_text_index_au"]
        if "after insert" not in ai or "work_text_index_fts" not in ai:
            return False
        if "values ('delete'" in ai or 'values ("delete"' in ai:
            return False
        if "after delete" not in ad or "work_text_index_fts" not in ad:
            return False
        if "values ('delete'" not in ad and 'values ("delete"' not in ad:
            return False
        if "after update" not in au or "work_text_index_fts" not in au:
            return False
        if "values ('delete'" not in au and 'values ("delete"' not in au:
            return False
        if "new.extracted_text" not in au:
            return False
        return True

    def _meta_int(self, conn: sqlite3.Connection, key: str) -> int | None:
        try:
            row = conn.execute(
                "SELECT value FROM text_index_meta WHERE key = ?",
                (key,),
            ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return None

    def _set_meta(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            """
            INSERT INTO text_index_meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def _create_current_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE text_index_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE work_text_index (
                work_id TEXT PRIMARY KEY,
                extracted_text TEXT NOT NULL DEFAULT '',
                source_ref_hash TEXT,
                source_size INTEGER,
                source_mtime_ns INTEGER,
                extractor_version INTEGER,
                extraction_status TEXT NOT NULL,
                truncated INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE VIRTUAL TABLE work_text_index_fts USING fts5(
                extracted_text,
                content='work_text_index',
                content_rowid='rowid'
            );
            CREATE TRIGGER work_text_index_ai AFTER INSERT ON work_text_index BEGIN
                INSERT INTO work_text_index_fts(rowid, extracted_text)
                VALUES (new.rowid, new.extracted_text);
            END;
            CREATE TRIGGER work_text_index_ad AFTER DELETE ON work_text_index BEGIN
                INSERT INTO work_text_index_fts(work_text_index_fts, rowid, extracted_text)
                VALUES ('delete', old.rowid, old.extracted_text);
            END;
            CREATE TRIGGER work_text_index_au AFTER UPDATE ON work_text_index BEGIN
                INSERT INTO work_text_index_fts(work_text_index_fts, rowid, extracted_text)
                VALUES ('delete', old.rowid, old.extracted_text);
                INSERT INTO work_text_index_fts(rowid, extracted_text)
                VALUES (new.rowid, new.extracted_text);
            END;
            """
        )
        self._set_meta(conn, "schema_version", str(TEXT_INDEX_SCHEMA_VERSION))
        self._set_meta(conn, "extractor_version", str(TEXT_EXTRACTOR_VERSION))

    def _upgrade_legacy_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "ALTER TABLE work_text_index ADD COLUMN source_ref_hash TEXT"
        )
        conn.execute(
            "ALTER TABLE work_text_index ADD COLUMN source_size INTEGER"
        )
        conn.execute(
            "ALTER TABLE work_text_index ADD COLUMN source_mtime_ns INTEGER"
        )
        conn.execute(
            "ALTER TABLE work_text_index ADD COLUMN extractor_version INTEGER"
        )
        conn.execute(
            "ALTER TABLE work_text_index ADD COLUMN extraction_status TEXT NOT NULL DEFAULT 'legacy'"
        )
        conn.execute(
            "ALTER TABLE work_text_index ADD COLUMN truncated INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute(
            "UPDATE work_text_index SET extraction_status = ? WHERE extraction_status IS NULL OR extraction_status = ''",
            (STATUS_LEGACY,),
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS text_index_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._set_meta(conn, "schema_version", str(TEXT_INDEX_SCHEMA_VERSION))
        self._set_meta(conn, "extractor_version", str(TEXT_EXTRACTOR_VERSION))
        if not self._fts_definition_valid(conn) or not self._triggers_valid(conn):
            raise _DerivedIndexUnusable("schema_invalid")
        columns = self._table_columns(conn, "work_text_index")
        if not self._current_schema_valid(conn, columns):
            raise _DerivedIndexUnusable("schema_invalid")

    def _stat_source(self, abs_path: str) -> Tuple[int, int]:
        st = os.stat(abs_path)
        return int(st.st_size), int(st.st_mtime_ns)

    def _fingerprint(self, file_path: str, abs_path: str) -> SourceFingerprint:
        size, mtime_ns = self._stat_source(abs_path)
        return SourceFingerprint(
            source_ref_hash=_source_ref_hash(file_path),
            source_size=size,
            source_mtime_ns=mtime_ns,
            extractor_version=TEXT_EXTRACTOR_VERSION,
        )

    def _resolve_managed_source(self, file_path: str) -> Tuple[str, str | None, str]:
        fp = (file_path or "").strip()
        if not fp.startswith("/api/pdfs/"):
            return "none", None, fp
        filename = managed_pdf_filename(fp)
        if filename is None:
            return "unsafe", None, fp
        abs_path = safe_pdf_path_under_dir(self.pdfs_dir, filename)
        if abs_path is None:
            return "unsafe", None, fp
        if not os.path.isfile(abs_path):
            return "missing", abs_path, fp
        return "ok", abs_path, fp

    def _fetch_row(self, conn: sqlite3.Connection, work_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM work_text_index WHERE work_id = ?",
            (work_id,),
        ).fetchone()

    def _row_is_current(self, row: sqlite3.Row, fp: SourceFingerprint) -> bool:
        status = str(row["extraction_status"] or "")
        if status not in _SUCCESS_STATUSES:
            return False
        try:
            extractor = int(row["extractor_version"] or 0)
        except (TypeError, ValueError):
            return False
        if extractor != TEXT_EXTRACTOR_VERSION:
            return False
        if str(row["source_ref_hash"] or "") != fp.source_ref_hash:
            return False
        try:
            size = int(row["source_size"])
            mtime_ns = int(row["source_mtime_ns"])
        except (TypeError, ValueError):
            return False
        return size == fp.source_size and mtime_ns == fp.source_mtime_ns

    def _write_row(
        self,
        work_id: str,
        *,
        text: str,
        status: str,
        truncated: bool,
        fp: SourceFingerprint | None,
    ) -> None:
        stored = (text or "")[:_MAX_EXTRACTED_CHARS]
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO work_text_index (
                    work_id, extracted_text, source_ref_hash, source_size,
                    source_mtime_ns, extractor_version, extraction_status,
                    truncated, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(work_id) DO UPDATE SET
                    extracted_text = excluded.extracted_text,
                    source_ref_hash = excluded.source_ref_hash,
                    source_size = excluded.source_size,
                    source_mtime_ns = excluded.source_mtime_ns,
                    extractor_version = excluded.extractor_version,
                    extraction_status = excluded.extraction_status,
                    truncated = excluded.truncated,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    work_id,
                    stored,
                    None if fp is None else fp.source_ref_hash,
                    None if fp is None else fp.source_size,
                    None if fp is None else fp.source_mtime_ns,
                    None if fp is None else fp.extractor_version,
                    status,
                    1 if truncated else 0,
                ),
            )
            conn.commit()

    def _extract_stable(
        self, abs_path: str, file_path: str
    ) -> Tuple[PDFTextExtraction, SourceFingerprint]:
        last_reason = "source_changed"
        for _attempt in range(2):
            try:
                before_size, before_mtime = self._stat_source(abs_path)
            except OSError as exc:
                raise PDFTextExtractionError(safe_error_type(exc)) from None
            extraction = extract_pdf(abs_path)
            try:
                after_size, after_mtime = self._stat_source(abs_path)
            except OSError as exc:
                raise PDFTextExtractionError(safe_error_type(exc)) from None
            if before_size == after_size and before_mtime == after_mtime:
                return extraction, SourceFingerprint(
                    source_ref_hash=_source_ref_hash(file_path),
                    source_size=after_size,
                    source_mtime_ns=after_mtime,
                    extractor_version=TEXT_EXTRACTOR_VERSION,
                )
            last_reason = "source_changed"
        raise PDFTextExtractionError(last_reason)

    def _log_extract_failed(self, work_id: str, error_type: str) -> None:
        LOGGER.warning(
            "text_index_extract_failed work_id=%s error_type=%s",
            safe_log_id(work_id),
            safe_log_label(error_type, fallback="Exception"),
        )

    def sync_work(
        self,
        work_id: str,
        file_path: str,
        *,
        force: bool = False,
        allow_extract: bool = True,
    ) -> TextIndexSyncResult:
        work_id = (work_id or "").strip()
        if not work_id:
            return TextIndexSyncResult(action="invalid")
        kind, abs_path, fp_text = self._resolve_managed_source(file_path)
        if kind == "none":
            self.remove_work(work_id)
            return TextIndexSyncResult(action="removed")
        if kind == "unsafe":
            self.remove_work(work_id)
            return TextIndexSyncResult(action="invalid")
        if kind == "missing":
            self.remove_work(work_id)
            return TextIndexSyncResult(action="missing")
        assert abs_path is not None
        try:
            current_fp = self._fingerprint(fp_text, abs_path)
        except OSError:
            self.remove_work(work_id)
            return TextIndexSyncResult(action="missing")
        with self._conn() as conn:
            row = self._fetch_row(conn, work_id)
        if (
            not force
            and row is not None
            and self._row_is_current(row, current_fp)
        ):
            return TextIndexSyncResult(
                action="unchanged",
                status=str(row["extraction_status"] or ""),
                truncated=bool(row["truncated"]),
            )
        if not allow_extract:
            if row is not None and self._row_is_current(row, current_fp):
                return TextIndexSyncResult(
                    action="skipped",
                    status=str(row["extraction_status"] or ""),
                    truncated=bool(row["truncated"]),
                )
            if row is not None and str(row["extraction_status"] or "") in _SUCCESS_STATUSES:
                self._write_row(
                    work_id,
                    text="",
                    status=STATUS_FAILED,
                    truncated=False,
                    fp=current_fp,
                )
                return TextIndexSyncResult(
                    action="failed", status=STATUS_FAILED, extracted=True
                )
            return TextIndexSyncResult(action="skipped")
        try:
            extraction, stored_fp = self._extract_stable(abs_path, fp_text)
        except PDFTextExtractorUnavailable:
            self._write_row(
                work_id,
                text="",
                status=STATUS_FAILED,
                truncated=False,
                fp=current_fp,
            )
            self._log_extract_failed(work_id, "PDFTextExtractorUnavailable")
            return TextIndexSyncResult(
                action="failed", status=STATUS_FAILED, extracted=True
            )
        except PDFTextExtractionError as exc:
            self._write_row(
                work_id,
                text="",
                status=STATUS_FAILED,
                truncated=False,
                fp=current_fp,
            )
            self._log_extract_failed(work_id, exc.reason)
            return TextIndexSyncResult(
                action="failed", status=STATUS_FAILED, extracted=True
            )
        except Exception as exc:
            self._write_row(
                work_id,
                text="",
                status=STATUS_FAILED,
                truncated=False,
                fp=current_fp,
            )
            self._log_extract_failed(work_id, safe_error_type(exc))
            return TextIndexSyncResult(
                action="failed", status=STATUS_FAILED, extracted=True
            )
        status = STATUS_EMPTY if extraction.empty else STATUS_INDEXED
        self._write_row(
            work_id,
            text=extraction.text,
            status=status,
            truncated=extraction.truncated,
            fp=stored_fp,
        )
        return TextIndexSyncResult(
            action="empty" if extraction.empty else "indexed",
            status=status,
            truncated=extraction.truncated,
            extracted=True,
        )

    def upsert_text(self, work_id: str, extracted_text: str) -> int:
        """Derived-index/test utility. Rows are marked legacy and are not synchronized."""
        work_id = (work_id or "").strip()
        if not work_id:
            return 0
        text = (extracted_text or "")[:_MAX_EXTRACTED_CHARS]
        self._write_row(
            work_id,
            text=text,
            status=STATUS_LEGACY,
            truncated=False,
            fp=None,
        )
        return 1

    def remove_work(self, work_id: str) -> int:
        work_id = (work_id or "").strip()
        if not work_id:
            return 0
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM work_text_index WHERE work_id = ?", (work_id,)
            )
            conn.commit()
            return cur.rowcount or 0

    def search_work_ids(self, term: str, limit: int = 2000) -> List[str]:
        with perf_span("pdf_text_search"):
            return self._search_work_ids_inner(term, limit)

    def _search_work_ids_inner(self, term: str, limit: int) -> List[str]:
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
        except sqlite3.Error as exc:
            LOGGER.warning(
                "text_index_search_failed error_type=%s",
                safe_error_type(exc),
            )
            return []

    def _canonical_managed_works(self, db: Any) -> Dict[str, str]:
        rows = db.execute_query(
            "SELECT id, file_path FROM works WHERE COALESCE(file_path, '') LIKE '/api/pdfs/%'",
            (),
        )
        expected: Dict[str, str] = {}
        for row in rows:
            wid = (row.get("id") or "").strip()
            if not wid:
                continue
            expected[wid] = (row.get("file_path") or "").strip()
        return expected

    def _indexed_work_ids(self) -> List[str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT work_id FROM work_text_index").fetchall()
        return [str(r["work_id"]) for r in rows if r["work_id"]]

    def _remove_orphans(self, expected_ids: set[str]) -> int:
        removed = 0
        for work_id in self._indexed_work_ids():
            if work_id not in expected_ids:
                removed += self.remove_work(work_id)
        return removed

    def _fts_integrity_ok(self, conn: sqlite3.Connection) -> bool:
        try:
            conn.execute(
                "INSERT INTO work_text_index_fts(work_text_index_fts, rank) VALUES('integrity-check', 1)"
            )
            return True
        except sqlite3.Error:
            return False

    def _rebuild_fts(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO work_text_index_fts(work_text_index_fts) VALUES('rebuild')"
        )

    def _verify_or_rebuild_fts(self) -> bool:
        conn = self._conn()
        try:
            if self._fts_integrity_ok(conn):
                conn.commit()
                return False
            conn.rollback()
            try:
                self._rebuild_fts(conn)
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                raise
            if not self._fts_integrity_ok(conn):
                conn.rollback()
                raise sqlite3.DatabaseError("fts_unrecoverable")
            conn.commit()
            return True
        finally:
            conn.close()

    def reconcile_all(
        self,
        db: Any,
        *,
        force: bool = False,
        _recovered: bool = False,
    ) -> Dict[str, Any]:
        """Reconcile derived PDF text index with canonical managed PDFs.

        Counters:
        processed — canonical works whose file_path looks like a managed PDF
        indexed — rows currently synchronized (status indexed or empty)
        updated — rows extracted this pass (indexed or empty)
        unchanged — fingerprint match; extractor not called
        empty — current status=empty rows among processed works
        truncated — current truncated=1 rows among processed works
        missing — canonical managed path whose PDF file is absent
        failed — missing + extraction failures + unsafe/unreadable sources
        removed_orphans — index rows with no matching canonical managed PDF work
        fts_rebuilt — FTS repaired from work_text_index this pass
        """
        with perf_span("text_index_reconcile"):
            return self._reconcile_all_inner(
                db, force=force, _recovered=_recovered
            )

    def _reconcile_all_inner(
        self,
        db: Any,
        *,
        force: bool = False,
        _recovered: bool = False,
    ) -> Dict[str, Any]:
        summary = _empty_summary()
        expected = self._canonical_managed_works(db)
        summary["removed_orphans"] = self._remove_orphans(set(expected))
        summary["processed"] = len(expected)
        can_extract = extractor_available()
        if not can_extract:
            summary["extractor_unavailable"] = True
        for work_id, file_path in expected.items():
            result = self.sync_work(
                work_id,
                file_path,
                force=force,
                allow_extract=can_extract,
            )
            if result.action == "unchanged":
                summary["unchanged"] += 1
                summary["indexed"] += 1
                if result.status == STATUS_EMPTY:
                    summary["empty"] += 1
                if result.truncated:
                    summary["truncated"] += 1
            elif result.action == "indexed":
                summary["updated"] += 1
                summary["indexed"] += 1
                if result.truncated:
                    summary["truncated"] += 1
            elif result.action == "empty":
                summary["updated"] += 1
                summary["indexed"] += 1
                summary["empty"] += 1
            elif result.action == "missing":
                summary["missing"] += 1
                summary["failed"] += 1
            elif result.action == "failed":
                summary["failed"] += 1
            elif result.action == "invalid":
                summary["failed"] += 1
            elif result.action == "skipped":
                summary["skipped"] += 1
                if result.status in _SUCCESS_STATUSES:
                    summary["indexed"] += 1
                    if result.status == STATUS_EMPTY:
                        summary["empty"] += 1
                    if result.truncated:
                        summary["truncated"] += 1
        try:
            summary["fts_rebuilt"] = self._verify_or_rebuild_fts()
        except sqlite3.Error:
            if _recovered:
                LOGGER.warning(
                    "text_index_recreated reason=%s",
                    safe_log_label("fts_unrecoverable"),
                )
                return summary
            self._recreate("fts_unrecoverable")
            recovered = self.reconcile_all(db, force=force, _recovered=True)
            recovered["fts_rebuilt"] = True
            return recovered
        return summary

    def rebuild_all(self, db: Any) -> Dict[str, Any]:
        return self.reconcile_all(db, force=True)

    def reindex_all(self, db: Any, *, force: bool = False) -> Dict[str, Any]:
        return self.reconcile_all(db, force=force)


def reconcile_at_startup(db: Any, index: PRKSTextIndex | None = None) -> Dict[str, Any]:
    idx = index if index is not None else get_text_index()
    try:
        summary = idx.reconcile_all(db, force=False)
    except Exception as exc:
        LOGGER.warning(
            "text_index_reconcile_failed error_type=%s",
            safe_error_type(exc),
        )
        return _empty_summary()
    LOGGER.info(
        "text_index_reconciled processed=%s updated=%s unchanged=%s empty=%s missing=%s failed=%s removed_orphans=%s fts_rebuilt=%s",
        int(summary.get("processed") or 0),
        int(summary.get("updated") or 0),
        int(summary.get("unchanged") or 0),
        int(summary.get("empty") or 0),
        int(summary.get("missing") or 0),
        int(summary.get("failed") or 0),
        int(summary.get("removed_orphans") or 0),
        "true" if summary.get("fts_rebuilt") else "false",
    )
    if summary.get("extractor_unavailable"):
        LOGGER.warning(
            "text_index_reconcile_failed error_type=%s",
            "PDFTextExtractorUnavailable",
        )
    return summary


_TEXT_INDEX_SINGLETON: PRKSTextIndex | None = None


def get_text_index() -> PRKSTextIndex:
    if _TEXT_INDEX_SINGLETON is None:
        raise RuntimeError("text index is not bound; call replace_text_index() first")
    return _TEXT_INDEX_SINGLETON


def replace_text_index(index: PRKSTextIndex) -> PRKSTextIndex | None:
    global _TEXT_INDEX_SINGLETON
    previous = _TEXT_INDEX_SINGLETON
    _TEXT_INDEX_SINGLETON = index
    return previous


def reset_text_index() -> None:
    global _TEXT_INDEX_SINGLETON
    _TEXT_INDEX_SINGLETON = None
