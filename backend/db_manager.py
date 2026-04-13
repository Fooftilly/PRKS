import sqlite3
import os
import re
import uuid
import json
import html
from collections import Counter, defaultdict
from urllib.parse import unquote
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

PRKS_BIBTEX_DOC_TYPES = frozenset({
    "article",
    "book",
    "booklet",
    "inbook",
    "incollection",
    "inproceedings",
    "proceedings",
    "manual",
    "mastersthesis",
    "phdthesis",
    "techreport",
    "unpublished",
    "misc",
    "online",
})

# Optional BibTeX/BibLaTeX lines (title + entry shell always exported).
PRKS_BIBTEX_EXPORT_FIELD_IDS: Tuple[str, ...] = (
    "author",
    "editor",
    "translator",
    "introduction",
    "foreword",
    "afterword",
    "year",
    "publisher",
    "location",
    "edition",
    "journal",
    "volume",
    "number",
    "pages",
    "isbn",
    "doi",
    "url",
    "abstract",
)
PRKS_BIBTEX_EXPORT_FIELDS_DEFAULT: Dict[str, bool] = {k: True for k in PRKS_BIBTEX_EXPORT_FIELD_IDS}


def _prks_parse_bibtex_export_fields_json(raw: str) -> Dict[str, bool]:
    """Load stored JSON; invalid or missing → all True. Unknown keys ignored."""
    out = dict(PRKS_BIBTEX_EXPORT_FIELDS_DEFAULT)
    if not raw or not str(raw).strip():
        return out
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return out
    if not isinstance(obj, dict):
        return out
    for k, v in obj.items():
        if k not in PRKS_BIBTEX_EXPORT_FIELD_IDS:
            continue
        if isinstance(v, bool):
            out[k] = v
    return out


def _prks_merge_bibtex_export_fields_patch(
    current: Dict[str, bool], patch: Dict[str, Any]
) -> Dict[str, bool]:
    merged = dict(current)
    for k, v in patch.items():
        if k not in PRKS_BIBTEX_EXPORT_FIELD_IDS:
            raise ValueError(f"unknown bibtex_export_fields key: {k}")
        if not isinstance(v, bool):
            raise ValueError(f"bibtex_export_fields.{k} must be boolean")
        merged[k] = v
    return merged


# List/search/tag/recent/folder APIs: omit text_content and private_notes (full row via get_work).
_PRKS_WORK_SUMMARY_COLUMNS: Tuple[str, ...] = (
    "id",
    "title",
    "status",
    "published_date",
    "abstract",
    "file_path",
    "source_kind",
    "source_url",
    "source_mime",
    "thumb_url",
    "provider",
    "provider_id",
    "urldate",
    "thumb_page",
    "author_text",
    "year",
    "publisher",
    "location",
    "edition",
    "journal",
    "volume",
    "issue",
    "pages",
    "isbn",
    "doi",
    "doc_type",
    "last_opened_at",
    "created_at",
    "updated_at",
)


def _prks_work_summary_select(alias: str) -> str:
    return ", ".join(f"{alias}.{c}" for c in _PRKS_WORK_SUMMARY_COLUMNS)


def _prks_work_summary_select_with_folder(alias: str) -> str:
    """Work summary columns plus folder_id (at most one folder per work in normal use)."""
    base = _prks_work_summary_select(alias)
    return f"{base}, (SELECT folder_id FROM folder_files WHERE work_id = {alias}.id LIMIT 1) AS folder_id"


def _prks_sql_first_linked_person_for_role(work_alias: str, role_type: str, column_alias: str) -> str:
    """Scalar subquery: display name of first linked person for role_type (BibTeX order)."""
    wid = f"{work_alias}.id"
    rt = (role_type or "").replace("'", "''")
    ca = (column_alias or "name").replace('"', "")
    return (
        "(SELECT TRIM(COALESCE(p.first_name,'') || ' ' || COALESCE(p.last_name,'')) "
        "FROM roles r "
        "JOIN persons p ON p.id = r.person_id "
        f"WHERE r.work_id = {wid} AND r.role_type = '{rt}' "
        "ORDER BY r.order_index ASC, r.rowid ASC "
        f"LIMIT 1) AS {ca}"
    )


def _prks_search_tokens(q: str) -> List[str]:
    """Split a user query into tokens for FTS/LIKE (hyphens become word breaks)."""
    if not q or not str(q).strip():
        return []
    s = re.sub(r"[-_]+", " ", str(q).strip())
    s = re.sub(r"\s+", " ", s).strip().lower()
    out: List[str] = []
    for raw in s.split():
        w = re.sub(r"[^\w]+", "", raw, flags=re.UNICODE)
        if w:
            out.append(w)
    return out


def _prks_escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _prks_fts_prefix_clause(tokens: List[str]) -> str:
    """Build an FTS5 MATCH string: prefix AND on each token; quotes avoid reserved-word issues."""
    parts: List[str] = []
    for t in tokens:
        safe = t.replace('"', "").replace("'", "")
        if not safe:
            continue
        parts.append(f'"{safe}"*')
    return " ".join(parts)


def normalize_doc_type(value: Any) -> str:
    """Map user/API input to a whitelisted BibLaTeX entry type; unknown → misc."""
    if value is None:
        return "misc"
    s = str(value).strip().lower()
    if s in PRKS_BIBTEX_DOC_TYPES:
        return s
    return "misc"


def _get_storage_root() -> Optional[str]:
    raw = os.environ.get("PRKS_STORAGE")
    if raw is None:
        return None
    root = str(raw).strip()
    if not root:
        return None
    if _is_testing_env() and (root == "/data" or root.startswith("/data/")):
        raise RuntimeError("PRKS_TESTING is set: refusing to use PRKS_STORAGE under /data")
    return root


def _resolve_pdfs_dir() -> str:
    storage_root = _get_storage_root()
    if storage_root:
        return os.path.join(storage_root, "pdfs")
    return default_local_pdfs_dir()


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _is_testing_env() -> bool:
    v = os.environ.get("PRKS_TESTING", "")
    return str(v).strip().lower() in ("1", "true", "yes")


def default_prks_db_path() -> str:
    """SQLite file under repo data/ or data_testing/ when PRKS_TESTING is set."""
    if _is_testing_env():
        return os.path.join(_REPO_ROOT, "data_testing", "prks_data_testing.db")
    return os.path.join(_REPO_ROOT, "data", "prks_data.db")


def default_local_pdfs_dir() -> str:
    """PDF directory when PRKS_STORAGE is unset (prod vs testing)."""
    if _is_testing_env():
        return os.path.join(_REPO_ROOT, "data_testing", "pdfs")
    return os.path.join(_REPO_ROOT, "data", "pdfs")


def safe_pdf_path_under_dir(pdfs_dir: str, url_last_segment: str) -> Optional[str]:
    """Resolve a single PDF basename under pdfs_dir; reject traversal and empty names."""
    if not url_last_segment or not str(url_last_segment).strip():
        return None
    name = os.path.basename(unquote(url_last_segment))
    if not name or name in (".", ".."):
        return None
    base = os.path.realpath(pdfs_dir)
    try:
        candidate = os.path.realpath(os.path.join(base, name))
    except OSError:
        return None
    if candidate != base and not candidate.startswith(base + os.sep):
        return None
    return candidate


def enrich_work_rows_pdf_file_size(rows: Optional[List[dict]]) -> None:
    """Set file_size_bytes on each row for on-disk PDFs under the PDF storage dir; else None."""
    if not rows:
        return
    pdfs_dir = _resolve_pdfs_dir()
    for row in rows:
        if not row or not isinstance(row, dict):
            continue
        fp = (row.get("file_path") or "").strip()
        if not fp.startswith("/api/pdfs/"):
            row["file_size_bytes"] = None
            continue
        seg = fp.split("/")[-1]
        path = safe_pdf_path_under_dir(pdfs_dir, seg)
        if not path or not os.path.isfile(path):
            row["file_size_bytes"] = None
            continue
        try:
            row["file_size_bytes"] = os.path.getsize(path)
        except OSError:
            row["file_size_bytes"] = None


class PRKSDatabase:
    def __init__(self, db_path: Optional[str] = None, schema_path: str = "backend/db_schema.sql"):
        if db_path is None:
            db_path = default_prks_db_path()
        self.db_path = db_path
        self.schema_path = schema_path
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)) or ".", exist_ok=True)
        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    @staticmethod
    def _migrate_tags_case_dedupe(conn: sqlite3.Connection) -> None:
        """Merge tags that differ only by letter case; keep earliest created_at then smallest id."""
        cur = conn.execute("SELECT id, name, created_at FROM tags")
        rows = cur.fetchall()
        groups: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
        for r in rows:
            rid, name, cat = r["id"], r["name"], r["created_at"]
            key = (name or "").strip().lower()
            if not key:
                continue
            groups[key].append((rid, name or "", str(cat or "")))
        for members in groups.values():
            if len(members) < 2:
                continue
            members.sort(key=lambda m: (m[2], m[0]))
            keeper = members[0][0]
            for loser_id, _n, _c in members[1:]:
                conn.execute(
                    "INSERT OR IGNORE INTO work_tags (work_id, tag_id) "
                    "SELECT work_id, ? FROM work_tags WHERE tag_id = ?",
                    (keeper, loser_id),
                )
                conn.execute("DELETE FROM work_tags WHERE tag_id = ?", (loser_id,))
                conn.execute(
                    "INSERT OR IGNORE INTO folder_tags (folder_id, tag_id) "
                    "SELECT folder_id, ? FROM folder_tags WHERE tag_id = ?",
                    (keeper, loser_id),
                )
                conn.execute("DELETE FROM folder_tags WHERE tag_id = ?", (loser_id,))
                conn.execute("DELETE FROM tags WHERE id = ?", (loser_id,))

    def init_db(self):
        """Initializes the database schema if it's new."""
        with self.get_connection() as conn:
            # Always run schema (IF NOT EXISTS prevents overrides)
            with open(self.schema_path, 'r', encoding='utf-8') as f:
                conn.executescript(f.read())
            # Run migrations for columns added after initial schema
            migrations = [
                "ALTER TABLE works ADD COLUMN author_text TEXT",
                "ALTER TABLE works ADD COLUMN year TEXT",
                "ALTER TABLE works ADD COLUMN publisher TEXT",
                "ALTER TABLE works ADD COLUMN journal TEXT",
                "ALTER TABLE works ADD COLUMN volume TEXT",
                "ALTER TABLE works ADD COLUMN issue TEXT",
                "ALTER TABLE works ADD COLUMN pages TEXT",
                "ALTER TABLE works ADD COLUMN isbn TEXT",
                "ALTER TABLE works ADD COLUMN doi TEXT",
                "ALTER TABLE works ADD COLUMN last_opened_at TIMESTAMP",
                "ALTER TABLE works ADD COLUMN updated_at TIMESTAMP",
                "ALTER TABLE persons ADD COLUMN image_url TEXT",
                "ALTER TABLE persons ADD COLUMN link_wikipedia TEXT",
                "ALTER TABLE persons ADD COLUMN link_stanford_encyclopedia TEXT",
                "ALTER TABLE persons ADD COLUMN link_iep TEXT",
                "ALTER TABLE persons ADD COLUMN links_other TEXT",
                "ALTER TABLE persons ADD COLUMN birth_date TEXT",
                "ALTER TABLE persons ADD COLUMN death_date TEXT",
                "ALTER TABLE works ADD COLUMN doc_type TEXT",
                "ALTER TABLE works ADD COLUMN private_notes TEXT",
                "ALTER TABLE works ADD COLUMN thumb_page INTEGER",
                "ALTER TABLE folders ADD COLUMN private_notes TEXT",
                "ALTER TABLE works ADD COLUMN source_kind TEXT",
                "ALTER TABLE works ADD COLUMN source_url TEXT",
                "ALTER TABLE works ADD COLUMN source_mime TEXT",
                "ALTER TABLE works ADD COLUMN thumb_url TEXT",
                "ALTER TABLE works ADD COLUMN provider TEXT",
                "ALTER TABLE works ADD COLUMN provider_id TEXT",
                "ALTER TABLE works ADD COLUMN urldate TEXT",
                "ALTER TABLE works ADD COLUMN edition TEXT",
                "ALTER TABLE works ADD COLUMN hide_pdf_link_annotations INTEGER DEFAULT 0",
                "ALTER TABLE works ADD COLUMN location TEXT",
                "ALTER TABLE folders ADD COLUMN parent_id TEXT",
                "ALTER TABLE playlists ADD COLUMN original_url TEXT",
            ]
            for sql in migrations:
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError as e:
                    if 'duplicate column' not in str(e).lower():
                        raise
            try:
                conn.execute(
                    "UPDATE works SET doc_type = 'article' WHERE doc_type IS NULL OR TRIM(doc_type) = ''"
                )
            except Exception:
                pass
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_person_groups_name_nocase "
                    "ON person_groups(name COLLATE NOCASE)"
                )
            except Exception:
                pass
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_playlist_items_work_unique "
                    "ON playlist_items(work_id)"
                )
            except Exception:
                pass
            self._migrate_tags_case_dedupe(conn)
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_name_nocase "
                    "ON tags(name COLLATE NOCASE)"
                )
            except Exception:
                pass
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS publishers (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_publishers_name_nocase
                        ON publishers(name COLLATE NOCASE);
                    CREATE TABLE IF NOT EXISTS publisher_aliases (
                        id TEXT PRIMARY KEY,
                        publisher_id TEXT NOT NULL,
                        alias TEXT NOT NULL,
                        FOREIGN KEY (publisher_id) REFERENCES publishers(id) ON DELETE CASCADE
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_publisher_aliases_alias_nocase
                        ON publisher_aliases(alias COLLATE NOCASE);
                    """
                )
            except Exception:
                pass
            # Performance and constraint indexes (idempotent: IF NOT EXISTS)
            try:
                conn.execute("DROP INDEX IF EXISTS idx_folders_title_nocase")
            except sqlite3.OperationalError:
                pass
            index_migrations = [
                "CREATE INDEX IF NOT EXISTS idx_roles_work_id ON roles(work_id)",
                "CREATE INDEX IF NOT EXISTS idx_roles_person_id ON roles(person_id)",
                "CREATE INDEX IF NOT EXISTS idx_annotations_work_id ON annotations(work_id)",
                "CREATE INDEX IF NOT EXISTS idx_arguments_work_id ON arguments(work_id)",
                "CREATE INDEX IF NOT EXISTS idx_playlist_items_playlist_id ON playlist_items(playlist_id)",
                "CREATE INDEX IF NOT EXISTS idx_works_last_opened_at ON works(last_opened_at)",
                "CREATE INDEX IF NOT EXISTS idx_folders_parent_id ON folders(parent_id)",
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_folders_parent_title_nocase "
                "ON folders(COALESCE(parent_id, ''), LOWER(TRIM(title)))",
            ]
            for sql in index_migrations:
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    pass
            # Record schema version so the DB file is auditable.
            _PRKS_SCHEMA_VERSION = 5
            existing_version = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if existing_version is None:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (_PRKS_SCHEMA_VERSION,))
            elif existing_version[0] < _PRKS_SCHEMA_VERSION:
                conn.execute("UPDATE schema_version SET version = ?", (_PRKS_SCHEMA_VERSION,))
            conn.commit()
        self._migrate_works_fts_author_text()

    def _migrate_works_fts_author_text(self) -> None:
        """Rebuild works_fts when an older DB has no author_text column in the FTS index."""
        with self.get_connection() as conn:
            try:
                rows = conn.execute("PRAGMA table_info(works_fts)").fetchall()
            except sqlite3.OperationalError:
                return
            cols = [r[1] for r in rows]
            if "author_text" in cols:
                return
            conn.executescript(
                """
                DROP TRIGGER IF EXISTS works_ai;
                DROP TRIGGER IF EXISTS works_ad;
                DROP TRIGGER IF EXISTS works_au;
                DROP TABLE IF EXISTS works_fts;
                """
            )
            conn.commit()
        fts_sql = """
            CREATE VIRTUAL TABLE works_fts USING fts5(
                title,
                abstract,
                text_content,
                author_text,
                content='works',
                content_rowid='rowid'
            );
            CREATE TRIGGER works_ai AFTER INSERT ON works BEGIN
              INSERT INTO works_fts(rowid, title, abstract, text_content, author_text)
              VALUES (new.rowid, new.title, new.abstract, new.text_content, COALESCE(new.author_text, ''));
            END;
            CREATE TRIGGER works_ad AFTER DELETE ON works BEGIN
              INSERT INTO works_fts(works_fts, rowid, title, abstract, text_content, author_text)
              VALUES ('delete', old.rowid, old.title, old.abstract, old.text_content, COALESCE(old.author_text, ''));
            END;
            CREATE TRIGGER works_au AFTER UPDATE ON works BEGIN
              INSERT INTO works_fts(works_fts, rowid, title, abstract, text_content, author_text)
              VALUES ('delete', old.rowid, old.title, old.abstract, old.text_content, COALESCE(old.author_text, ''));
              INSERT INTO works_fts(rowid, title, abstract, text_content, author_text)
              VALUES (new.rowid, new.title, new.abstract, new.text_content, COALESCE(new.author_text, ''));
            END;
            INSERT INTO works_fts(rowid, title, abstract, text_content, author_text)
            SELECT rowid, title, abstract, text_content, COALESCE(author_text, '') FROM works;
        """
        with self.get_connection() as conn:
            conn.executescript(fts_sql)
            conn.commit()

    def generate_id(self, prefix: str) -> str:
        """Generates a short, readable persistent unique ID, e.g., W-A1B2C3D4"""
        u_hex = str(uuid.uuid4().hex)
        return f"{prefix}-{u_hex[:8].upper()}"

    def execute_query(self, query: str, params: tuple = ()) -> List[dict]:
        with self.get_connection() as conn:
            cursor = conn.execute(query, params)
            q0 = query.strip().upper()
            if q0.startswith(("SELECT", "PRAGMA", "WITH")):
                return [dict(row) for row in cursor.fetchall()]
            conn.commit()
            return []

    # --- App settings (shared across all clients of this database) ---
    _PRKS_APP_SETTING_MAX_LEN = 500

    def get_app_settings_map(self) -> Dict[str, str]:
        rows = self.execute_query("SELECT key, value FROM app_settings", ())
        return {str(r["key"]): str(r["value"] or "") for r in (rows or [])}

    def get_app_settings_response(self) -> Dict[str, Any]:
        m = self.get_app_settings_map()
        bibtex_fields = _prks_parse_bibtex_export_fields_json(m.get("bibtex_export_fields", ""))
        return {
            "annotation_author": m.get("annotation_author", ""),
            "bibtex_export_fields": bibtex_fields,
        }

    def patch_app_settings(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise ValueError("invalid body")
        if "annotation_author" in data:
            raw = data.get("annotation_author")
            v = "" if raw is None else str(raw).strip()
            if len(v) > self._PRKS_APP_SETTING_MAX_LEN:
                raise ValueError("annotation_author too long")
            self.execute_query(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("annotation_author", v),
            )
        if "bibtex_export_fields" in data:
            raw_bf = data.get("bibtex_export_fields")
            if not isinstance(raw_bf, dict):
                raise ValueError("bibtex_export_fields must be an object")
            m = self.get_app_settings_map()
            current = _prks_parse_bibtex_export_fields_json(m.get("bibtex_export_fields", ""))
            merged = _prks_merge_bibtex_export_fields_patch(current, raw_bf)
            blob = json.dumps(merged, separators=(",", ":"), sort_keys=True)
            if len(blob) > self._PRKS_APP_SETTING_MAX_LEN:
                raise ValueError("bibtex_export_fields too long")
            self.execute_query(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("bibtex_export_fields", blob),
            )

    def _get_bibtex_export_profile(self) -> Dict[str, bool]:
        m = self.get_app_settings_map()
        return _prks_parse_bibtex_export_fields_json(m.get("bibtex_export_fields", ""))

    # --- Works ---
    def add_work(self, title: str, status: str = 'Not Started', abstract: str = "",
                 text_content: str = "", published_date: str = "", file_path: str = "",
                 author_text: str = "", year: str = "", publisher: str = "",
                 location: str = "",
                 edition: str = "",
                 journal: str = "", volume: str = "", issue: str = "",
                 pages: str = "", isbn: str = "", doi: str = "",
                 doc_type: str = "article",
                 source_kind: str = "",
                 source_url: str = "",
                 source_mime: str = "",
                 thumb_url: str = "",
                 provider: str = "",
                 provider_id: str = "",
                 urldate: str = "",
                 thumb_page=None,
                 private_notes: str = "") -> str:
        work_id = self.generate_id("W")
        dt = normalize_doc_type(doc_type)
        tp = thumb_page
        if tp is None or tp == "":
            tp_sql = None
        else:
            try:
                n = int(tp)
                tp_sql = n if n >= 1 else None
            except Exception:
                tp_sql = None
        pn = (private_notes or "").strip() or None
        query = """
        INSERT INTO works (
            id, title, status, abstract, text_content, published_date, file_path,
            author_text, year, publisher, location, edition, journal, volume, issue, pages, isbn, doi, doc_type,
            source_kind, source_url, source_mime, thumb_url, provider, provider_id, urldate,
            thumb_page, private_notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        self.execute_query(query, (work_id, title, status, abstract, text_content, published_date,
                                   file_path, author_text, year, publisher, location, edition, journal, volume,
                                   issue, pages, isbn, doi, dt,
                                   (source_kind or "").strip() or None,
                                   (source_url or "").strip() or None,
                                   (source_mime or "").strip() or None,
                                   (thumb_url or "").strip() or None,
                                   (provider or "").strip() or None,
                                   (provider_id or "").strip() or None,
                                   (urldate or "").strip() or None,
                                   tp_sql,
                                   pn))
        return work_id
    
    def get_all_works(self) -> List[dict]:
        sel = _prks_work_summary_select_with_folder("works")
        pa = _prks_sql_first_linked_person_for_role("works", "Author", "primary_author")
        pe = _prks_sql_first_linked_person_for_role("works", "Editor", "primary_editor")
        rows = list(self.execute_query(f"SELECT {sel}, {pa}, {pe} FROM works ORDER BY created_at DESC"))
        enrich_work_rows_pdf_file_size(rows)
        return rows

    def etag_works_catalog(self) -> str:
        r = self.execute_query("SELECT COUNT(*) AS c, COALESCE(MAX(updated_at), '') AS m FROM works")
        row = r[0] if r else {"c": 0, "m": ""}
        ff = self.execute_query("SELECT COUNT(*) AS c FROM folder_files")
        ffc = (ff[0] if ff else {"c": 0})["c"]
        return f'W/"prks-works-{row["c"]}-{row["m"]}-ff{ffc}"'

    def etag_graph(self) -> str:
        w = self.execute_query("SELECT COUNT(*) AS c, COALESCE(MAX(updated_at), '') AS m FROM works")
        wt = self.execute_query("SELECT COUNT(*) AS c FROM work_tags")
        wr = w[0] if w else {"c": 0, "m": ""}
        tr = wt[0] if wt else {"c": 0}
        return f'W/"prks-graph-{wr["c"]}-{wr["m"]}-{tr["c"]}"'

    def etag_folders_catalog(self) -> str:
        r = self.execute_query("SELECT COUNT(*) AS c, COALESCE(MAX(updated_at), '') AS m FROM folders")
        ff = self.execute_query("SELECT COUNT(*) AS c FROM folder_files")
        ft = self.execute_query("SELECT COUNT(*) AS c FROM folder_tags")
        row = r[0] if r else {"c": 0, "m": ""}
        f1 = ff[0] if ff else {"c": 0}
        f2 = ft[0] if ft else {"c": 0}
        return f'W/"prks-folders-{row["c"]}-{row["m"]}-{f1["c"]}-{f2["c"]}"'

    def etag_persons_catalog(self) -> str:
        r = self.execute_query("SELECT COUNT(*) AS c, COALESCE(MAX(updated_at), '') AS m FROM persons")
        gm = self.execute_query("SELECT COUNT(*) AS c FROM person_group_members")
        row = r[0] if r else {"c": 0, "m": ""}
        gr = gm[0] if gm else {"c": 0}
        return f'W/"prks-persons-{row["c"]}-{row["m"]}-{gr["c"]}"'

    def etag_person_groups_catalog(self) -> str:
        r = self.execute_query("SELECT COUNT(*) AS c, COALESCE(MAX(updated_at), '') AS m FROM person_groups")
        mm = self.execute_query("SELECT COUNT(*) AS c FROM person_group_members")
        row = r[0] if r else {"c": 0, "m": ""}
        mr = mm[0] if mm else {"c": 0}
        return f'W/"prks-pgroups-{row["c"]}-{row["m"]}-{mr["c"]}"'

    def etag_playlists_catalog(self) -> str:
        r = self.execute_query("SELECT COUNT(*) AS c, COALESCE(MAX(updated_at), '') AS m FROM playlists")
        it = self.execute_query("SELECT COUNT(*) AS c FROM playlist_items")
        row = r[0] if r else {"c": 0, "m": ""}
        ir = it[0] if it else {"c": 0}
        return f'W/"prks-playlists-{row["c"]}-{row["m"]}-{ir["c"]}"'

    def etag_tags_all(self) -> str:
        r = self.execute_query(
            """
            SELECT COUNT(*) AS c,
                   COALESCE(SUM(LENGTH(name)), 0) AS ln,
                   COALESCE(SUM(LENGTH(COALESCE(color, ''))), 0) AS lc
            FROM tags
            """
        )
        al = self.execute_query(
            """
            SELECT COUNT(*) AS c,
                   COALESCE(SUM(LENGTH(COALESCE(alias, ''))), 0) AS sla
            FROM tag_aliases
            """
        )
        wt = self.execute_query("SELECT COUNT(*) AS c FROM work_tags")
        row = r[0] if r else {"c": 0, "ln": 0, "lc": 0}
        ar = al[0] if al else {"c": 0, "sla": 0}
        tr = wt[0] if wt else {"c": 0}
        return (
            f'W/"prks-tags-{row["c"]}-{row["ln"]}-{row["lc"]}-{tr["c"]}-'
            f'{ar["c"]}-{ar["sla"]}"'
        )

    def etag_recent_works(self) -> str:
        """Revision for /api/recent (last_opened ordering can change without works.updated_at)."""
        r = self.execute_query(
            "SELECT COUNT(*) AS c, COALESCE(MAX(last_opened_at), '') AS m FROM works WHERE last_opened_at IS NOT NULL"
        )
        row = r[0] if r else {"c": 0, "m": ""}
        return f'W/"prks-recent-{row["c"]}-{row["m"]}"'

    def delete_work(self, work_id: str):
        # Retrieve file_path if we want to delete the physical file
        res = self.execute_query("SELECT file_path FROM works WHERE id = ?", (work_id,))
        if res and res[0].get('file_path'):
            fp = res[0].get('file_path') or ''
            if fp.startswith('/api/pdfs/'):
                filename = fp.split('/')[-1]
                abs_path = safe_pdf_path_under_dir(_resolve_pdfs_dir(), filename)
            else:
                abs_path = None
            try:
                if abs_path and os.path.exists(abs_path):
                    os.remove(abs_path)
            except Exception as e:
                print("Error deleting file:", e)
        tag_ids = [
            r["tag_id"]
            for r in self.execute_query(
                "SELECT DISTINCT tag_id FROM work_tags WHERE work_id = ?", (work_id,)
            )
        ]
        # Delete from DB (foreign keys cascade)
        self.execute_query("DELETE FROM works WHERE id = ?", (work_id,))
        for tid in tag_ids:
            self._prune_tag_if_unused(tid)

    def delete_empty_folder(self, folder_id: str):
        exists = self.execute_query("SELECT 1 FROM folders WHERE id = ?", (folder_id,))
        if not exists:
            raise ValueError("Folder not found.")
        count = self.execute_query("SELECT COUNT(*) as c FROM folder_files WHERE folder_id = ?", (folder_id,))
        if count and count[0]['c'] > 0:
            raise ValueError("Cannot delete folder: Folder is not empty. Please remove all files first.")
        child_count = self.execute_query(
            "SELECT COUNT(*) as c FROM folders WHERE parent_id = ?",
            (folder_id,),
        )
        if child_count and child_count[0]["c"] > 0:
            raise ValueError("Cannot delete folder: Folder has subfolders. Please move or delete them first.")
        tag_ids = [
            r["tag_id"]
            for r in self.execute_query(
                "SELECT DISTINCT tag_id FROM folder_tags WHERE folder_id = ?", (folder_id,)
            )
        ]
        self.execute_query("DELETE FROM folders WHERE id = ?", (folder_id,))
        for tid in tag_ids:
            self._prune_tag_if_unused(tid)

    def _search_works_fts_tokens(self, tokens: List[str]) -> List[dict]:
        clause = _prks_fts_prefix_clause(tokens)
        if not clause:
            return []
        try:
            return self.execute_query(
                """
                SELECT works.id FROM works
                JOIN works_fts ON works.rowid = works_fts.rowid
                WHERE works_fts MATCH ?
                ORDER BY rank
                """,
                (clause,),
            )
        except sqlite3.OperationalError:
            return []

    def _search_works_like_tokens(self, tokens: List[str]) -> List[dict]:
        if not tokens:
            return []
        blob = (
            "LOWER(COALESCE(works.title,'') || ' ' || COALESCE(works.author_text,'') || ' ' || "
            "COALESCE(works.abstract,'') || ' ' || COALESCE(works.text_content,''))"
        )
        conds: List[str] = []
        params: List[str] = []
        for t in tokens:
            esc = _prks_escape_like(t)
            conds.append(f"{blob} LIKE ? ESCAPE '\\'")
            params.append(f"%{esc}%")
        where_sql = " AND ".join(conds)
        sql = (
            f"SELECT DISTINCT works.id FROM works WHERE {where_sql} "
            "ORDER BY works.updated_at DESC, works.created_at DESC"
        )
        return self.execute_query(sql, tuple(params))

    def _search_works_linked_persons_substring(self, q_norm: str) -> List[dict]:
        if not q_norm.strip():
            return []
        needle = "%" + _prks_escape_like(q_norm.strip()) + "%"
        sql = """
        SELECT DISTINCT w.id FROM works w
        INNER JOIN roles r ON r.work_id = w.id
        INNER JOIN persons p ON p.id = r.person_id
        WHERE LOWER(TRIM(COALESCE(p.first_name,'') || ' ' || COALESCE(p.last_name,'')))
            LIKE ? ESCAPE '\\'
           OR LOWER(COALESCE(p.aliases,'')) LIKE ? ESCAPE '\\'
           OR LOWER(COALESCE(p.last_name,'')) LIKE ? ESCAPE '\\'
        ORDER BY w.updated_at DESC, w.created_at DESC
        """
        return self.execute_query(sql, (needle, needle, needle))

    def work_ids_matching_author(self, author: str) -> List[str]:
        a = (author or "").strip().lower()
        if not a:
            return []
        needle = "%" + _prks_escape_like(a) + "%"
        sql = """
        SELECT DISTINCT w.id AS id FROM works w
        WHERE LOWER(COALESCE(w.author_text,'')) LIKE ? ESCAPE '\\'
        UNION
        SELECT DISTINCT w.id AS id FROM works w
        INNER JOIN roles r ON r.work_id = w.id
        INNER JOIN persons p ON p.id = r.person_id
        WHERE LOWER(TRIM(COALESCE(p.first_name,'') || ' ' || COALESCE(p.last_name,'')))
            LIKE ? ESCAPE '\\'
           OR LOWER(COALESCE(p.aliases,'')) LIKE ? ESCAPE '\\'
           OR LOWER(COALESCE(p.last_name,'')) LIKE ? ESCAPE '\\'
        """
        rows = self.execute_query(sql, (needle, needle, needle, needle))
        return [r["id"] for r in rows]

    def work_ids_matching_publisher(self, pub: str) -> List[str]:
        """Works whose publisher field matches substring, or equals a label of a publisher row whose name/alias matches substring."""
        p = (pub or "").strip().lower()
        if not p:
            return []
        needle = "%" + _prks_escape_like(p) + "%"
        ids: set = set()
        for r in self.execute_query(
            "SELECT id FROM works WHERE LOWER(COALESCE(publisher,'')) LIKE ? ESCAPE '\\'",
            (needle,),
        ):
            ids.add(r["id"])
        prow = self.execute_query(
            """
            SELECT DISTINCT p.id FROM publishers p
            LEFT JOIN publisher_aliases pa ON pa.publisher_id = p.id
            WHERE LOWER(p.name) LIKE ? ESCAPE '\\'
               OR LOWER(COALESCE(pa.alias,'')) LIKE ? ESCAPE '\\'
            """,
            (needle, needle),
        )
        if not prow:
            return list(ids)
        pid_list = [r["id"] for r in prow]
        ph = ",".join("?" * len(pid_list))
        names = self.execute_query(
            f"SELECT id, name FROM publishers WHERE id IN ({ph})",
            tuple(pid_list),
        )
        name_by_id = {r["id"]: (r["name"] or "").strip() for r in names}
        ali = self.execute_query(
            f"SELECT publisher_id, alias FROM publisher_aliases WHERE publisher_id IN ({ph})",
            tuple(pid_list),
        )
        labels_by_pid: Dict[str, List[str]] = defaultdict(list)
        for pid in pid_list:
            nm = name_by_id.get(pid, "").strip()
            if nm:
                labels_by_pid[pid].append(nm)
        for r in ali:
            al = (r["alias"] or "").strip()
            if al:
                labels_by_pid[r["publisher_id"]].append(al)
        for _pid, labels in labels_by_pid.items():
            for lab in labels:
                if not lab:
                    continue
                for wr in self.execute_query(
                    """
                    SELECT id FROM works
                    WHERE TRIM(COALESCE(publisher,'')) != ''
                      AND LOWER(TRIM(publisher)) = LOWER(?)
                    """,
                    (lab,),
                ):
                    ids.add(wr["id"])
        return list(ids)

    def search_works(
        self,
        search_term: str,
        author_filter: str = "",
        publisher_filter: str = "",
    ) -> List[dict]:
        """
        Full library search: FTS5 (title, abstract, notes, author_text) plus LIKE fallback
        (handles hyphens vs spaces, e.g. Multi-Author vs multi author) and linked person names.
        Optional author_filter / publisher_filter restrict to matching works (AND when both set).
        """
        q = (search_term or "").strip()
        auth = (author_filter or "").strip()
        pub = (publisher_filter or "").strip()
        if not q and not auth and not pub:
            return []

        author_ids: Optional[set] = None
        if auth:
            author_ids = set(self.work_ids_matching_author(auth))
            if not author_ids:
                return []

        publisher_ids: Optional[set] = None
        if pub:
            publisher_ids = set(self.work_ids_matching_publisher(pub))
            if not publisher_ids:
                return []

        def passes_filters(wid: str) -> bool:
            if author_ids is not None and wid not in author_ids:
                return False
            if publisher_ids is not None and wid not in publisher_ids:
                return False
            return True

        seen: set = set()
        ordered_ids: List[str] = []

        def add_rows(rows: List[dict]) -> None:
            for row in rows:
                wid = row["id"]
                if wid in seen or not passes_filters(wid):
                    continue
                seen.add(wid)
                ordered_ids.append(wid)

        if q:
            tokens = _prks_search_tokens(q)
            if tokens:
                add_rows(self._search_works_fts_tokens(tokens))
                add_rows(self._search_works_like_tokens(tokens))
            q_blob = re.sub(r"[-_]+", " ", q).strip().lower()
            q_blob = re.sub(r"\s+", " ", q_blob)
            if q_blob:
                add_rows(self._search_works_linked_persons_substring(q_blob))
        else:
            ids_set: Optional[set] = None
            if author_ids is not None:
                ids_set = set(author_ids)
            if publisher_ids is not None:
                ids_set = publisher_ids if ids_set is None else ids_set & publisher_ids
            if ids_set is None or not ids_set:
                return []
            id_list = list(ids_set)
            ph = ",".join("?" * len(id_list))
            wsel = _prks_work_summary_select_with_folder("works")
            pa = _prks_sql_first_linked_person_for_role("works", "Author", "primary_author")
            pe = _prks_sql_first_linked_person_for_role("works", "Editor", "primary_editor")
            rows = list(
                self.execute_query(
                    f"SELECT {wsel}, {pa}, {pe} FROM works WHERE id IN ({ph}) ORDER BY updated_at DESC, created_at DESC",
                    tuple(id_list),
                )
            )
            enrich_work_rows_pdf_file_size(rows)
            return rows

        if not ordered_ids:
            return []
        placeholders = ",".join("?" * len(ordered_ids))
        wsel = _prks_work_summary_select_with_folder("works")
        pa = _prks_sql_first_linked_person_for_role("works", "Author", "primary_author")
        pe = _prks_sql_first_linked_person_for_role("works", "Editor", "primary_editor")
        rows = list(
            self.execute_query(
                f"SELECT {wsel}, {pa}, {pe} FROM works WHERE id IN ({placeholders})",
                tuple(ordered_ids),
            )
        )
        enrich_work_rows_pdf_file_size(rows)
        by_id = {r["id"]: r for r in rows}
        return [by_id[i] for i in ordered_ids if i in by_id]

    def get_works_by_tag_name(self, tag_name: str) -> List[dict]:
        """Works tagged with the canonical tag matching this name or a tag alias (case-insensitive)."""
        name = (tag_name or "").strip()
        if not name:
            return []
        tid = self.resolve_tag_id_by_label(name)
        if not tid:
            return []
        wsel = _prks_work_summary_select("w")
        pa = _prks_sql_first_linked_person_for_role("w", "Author", "primary_author")
        pe = _prks_sql_first_linked_person_for_role("w", "Editor", "primary_editor")
        query = f"""
        SELECT DISTINCT {wsel}, {pa}, {pe} FROM works w
        JOIN work_tags wt ON w.id = wt.work_id
        WHERE wt.tag_id = ?
        ORDER BY w.created_at DESC
        """
        rows = list(self.execute_query(query, (tid,)))
        enrich_work_rows_pdf_file_size(rows)
        return rows

    def get_work(self, work_id: str) -> Optional[dict]:
        res = self.execute_query("SELECT * FROM works WHERE id = ?", (work_id,))
        if not res: return None
        work = res[0]
        work['roles'] = self.get_work_roles(work_id)
        work['arguments'] = self.execute_query("SELECT * FROM arguments WHERE work_id = ?", (work_id,))
        ann = self.get_work_annotations(work_id)
        work['annotations'] = ann
        work['tags'] = self.get_work_tags(work_id)
        try:
            prow = self.execute_query(
                """
                SELECT p.id AS playlist_id, p.title AS playlist_title
                FROM playlist_items i
                JOIN playlists p ON p.id = i.playlist_id
                WHERE i.work_id = ?
                LIMIT 1
                """,
                (work_id,),
            )
            if prow:
                work["playlist_id"] = prow[0].get("playlist_id")
                work["playlist_title"] = prow[0].get("playlist_title")
            else:
                work["playlist_id"] = None
                work["playlist_title"] = None
        except Exception:
            work["playlist_id"] = None
            work["playlist_title"] = None
        try:
            frow = self.execute_query(
                """
                SELECT f.id AS folder_id, f.title AS folder_title
                FROM folder_files ff
                JOIN folders f ON f.id = ff.folder_id
                WHERE ff.work_id = ?
                LIMIT 1
                """,
                (work_id,),
            )
            if frow:
                work["folder_id"] = frow[0].get("folder_id")
                work["folder_title"] = frow[0].get("folder_title")
            else:
                work["folder_id"] = None
                work["folder_title"] = None
        except Exception:
            work["folder_id"] = None
            work["folder_title"] = None
        if work.get('text_content'):
            work['html_content'] = self.resolve_wiki_links(work['text_content'])
        # Touch last_opened_at for Recent page
        self.execute_query(
            "UPDATE works SET last_opened_at = CURRENT_TIMESTAMP WHERE id = ?", (work_id,)
        )
        enrich_work_rows_pdf_file_size([work])
        return work

    def get_recent_works(self, limit: int = 30) -> List[dict]:
        sel = _prks_work_summary_select("works")
        pa = _prks_sql_first_linked_person_for_role("works", "Author", "primary_author")
        pe = _prks_sql_first_linked_person_for_role("works", "Editor", "primary_editor")
        rows = list(
            self.execute_query(
                f"SELECT {sel}, {pa}, {pe} FROM works WHERE last_opened_at IS NOT NULL ORDER BY last_opened_at DESC LIMIT ?",
                (limit,),
            )
        )
        enrich_work_rows_pdf_file_size(rows)
        return rows

    def update_work_metadata(self, work_id: str, fields: dict):
        """Update arbitrary metadata fields on a work."""
        allowed = {'title', 'status', 'abstract', 'published_date',
                   'author_text', 'year', 'publisher', 'location', 'edition', 'journal',
                   'volume', 'issue', 'pages', 'isbn', 'doi', 'text_content', 'doc_type',
                   'private_notes', 'thumb_page',
                   'source_kind', 'source_url', 'source_mime', 'thumb_url', 'provider', 'provider_id', 'urldate',
                   'file_path', 'hide_pdf_link_annotations'}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if 'hide_pdf_link_annotations' in updates:
            raw = updates['hide_pdf_link_annotations']
            if raw is True or raw == 1 or (isinstance(raw, str) and raw.strip().lower() in ('1', 'true', 'yes')):
                updates['hide_pdf_link_annotations'] = 1
            else:
                updates['hide_pdf_link_annotations'] = 0
        if 'doc_type' in updates:
            updates['doc_type'] = normalize_doc_type(updates['doc_type'])
        if 'thumb_page' in updates:
            raw = updates.get('thumb_page')
            if raw is None or raw == '':
                updates['thumb_page'] = None
            else:
                try:
                    n = int(raw)
                    updates['thumb_page'] = n if n >= 1 else None
                except Exception:
                    updates['thumb_page'] = None
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [work_id]
        self.execute_query(
            f"UPDATE works SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            tuple(values)
        )

    # --- Playlists (ordered collections of works, used for video courses) ---

    def add_playlist(self, title: str, description: str = "", original_url: str = "") -> str:
        t = (title or "").strip() or "Untitled playlist"
        d = (description or "").strip()
        u = (original_url or "").strip()
        pid = self.generate_id("PL")
        self.execute_query(
            "INSERT INTO playlists (id, title, description, original_url) VALUES (?, ?, ?, ?)",
            (pid, t, d, (u or None)),
        )
        return pid

    def update_playlist(self, playlist_id: str, fields: dict) -> None:
        allowed = {"title", "description", "original_url"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if "title" in updates:
            updates["title"] = (updates["title"] or "").strip() or "Untitled playlist"
        if "description" in updates:
            updates["description"] = (updates["description"] or "").strip()
        if "original_url" in updates:
            updates["original_url"] = (updates["original_url"] or "").strip() or None
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [playlist_id]
        self.execute_query(
            f"UPDATE playlists SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            tuple(values),
        )

    def delete_playlist(self, playlist_id: str) -> None:
        self.execute_query("DELETE FROM playlists WHERE id = ?", (playlist_id,))

    def get_all_playlists(self) -> List[dict]:
        rows = self.execute_query(
            """
            SELECT p.*,
                (SELECT COUNT(*) FROM playlist_items i WHERE i.playlist_id = p.id) AS item_count
            FROM playlists p
            ORDER BY p.updated_at DESC, p.created_at DESC
            """
        )
        return list(rows)

    def get_playlist(self, playlist_id: str) -> Optional[dict]:
        rows = self.execute_query("SELECT * FROM playlists WHERE id = ?", (playlist_id,))
        if not rows:
            return None
        p = dict(rows[0])
        wsel = _prks_work_summary_select("w")
        pa = _prks_sql_first_linked_person_for_role("w", "Author", "primary_author")
        pe = _prks_sql_first_linked_person_for_role("w", "Editor", "primary_editor")
        p["items"] = self.execute_query(
            """
            SELECT {wsel}, i.position, {pa}, {pe}
            FROM playlist_items i
            JOIN works w ON w.id = i.work_id
            WHERE i.playlist_id = ?
            ORDER BY i.position ASC, w.created_at ASC
            """.format(wsel=wsel, pa=pa, pe=pe),
            (playlist_id,),
        )
        enrich_work_rows_pdf_file_size(p["items"])
        return p

    def add_work_to_playlist(self, playlist_id: str, work_id: str, position: Optional[int] = None) -> None:
        with self.get_connection() as conn:
            ok_p = conn.execute("SELECT 1 FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
            ok_w = conn.execute("SELECT 1 FROM works WHERE id = ?", (work_id,)).fetchone()
            if not ok_p or not ok_w:
                raise ValueError("Playlist or work not found.")
            # Enforce one playlist per work: move if already in another playlist.
            conn.execute("DELETE FROM playlist_items WHERE work_id = ?", (work_id,))

            if position is None:
                row = conn.execute(
                    "SELECT COALESCE(MAX(position), -1) AS m FROM playlist_items WHERE playlist_id = ?",
                    (playlist_id,),
                ).fetchone()
                mx = int(row["m"]) if row else -1
                position = mx + 1
            conn.execute(
                """
                INSERT INTO playlist_items (playlist_id, work_id, position)
                VALUES (?, ?, ?)
                ON CONFLICT(playlist_id, work_id) DO UPDATE SET position=excluded.position
                """,
                (playlist_id, work_id, int(position)),
            )
            conn.execute(
                "UPDATE playlists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (playlist_id,),
            )
            conn.commit()

    def remove_work_from_playlist(self, playlist_id: str, work_id: str) -> None:
        self.execute_query(
            "DELETE FROM playlist_items WHERE playlist_id = ? AND work_id = ?",
            (playlist_id, work_id),
        )
        self.execute_query(
            "UPDATE playlists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (playlist_id,),
        )

    def reorder_playlist(self, playlist_id: str, work_ids: List[str]) -> None:
        if not work_ids:
            return
        with self.get_connection() as conn:
            ok_p = conn.execute("SELECT 1 FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
            if not ok_p:
                raise ValueError("Playlist not found.")
            # Keep only works that are currently in this playlist.
            cur = conn.execute(
                "SELECT work_id FROM playlist_items WHERE playlist_id = ?",
                (playlist_id,),
            ).fetchall()
            present = {r[0] for r in cur}
            order: List[str] = []
            seen = set()
            for wid in work_ids:
                if not wid or wid in seen or wid not in present:
                    continue
                seen.add(wid)
                order.append(wid)
            # Append the rest preserving existing relative order.
            remaining = [w for w in present if w not in seen]
            for wid in remaining:
                order.append(wid)
            for idx, wid in enumerate(order):
                conn.execute(
                    "UPDATE playlist_items SET position = ? WHERE playlist_id = ? AND work_id = ?",
                    (idx, playlist_id, wid),
                )
            conn.execute(
                "UPDATE playlists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (playlist_id,),
            )
            conn.commit()

    def get_work_annotations(self, work_id: str) -> str:
        """Fetches structured annotations and returns them as a JSON list string."""
        res = self.execute_query(
            "SELECT id, type, content, page_index, color, geometry_json, updated_at FROM annotations WHERE work_id = ? ORDER BY page_index ASC", 
            (work_id,)
        )
        # Map back to the keys the frontend expects
        out = []
        for row in res:
            item = {
                'id': row['id'],
                'type': row['type'],
                'contents': row['content'],
                'pageIndex': row['page_index'],
                'color': row['color'],
                'updated_at': row['updated_at']
            }
            # Unpack geometry
            try:
                geom = json.loads(row['geometry_json'] or '{}')
                item.update(geom)
            except (TypeError, json.JSONDecodeError):
                pass
            out.append(item)
        return json.dumps(out)

    def save_work_annotations(self, work_id: str, annotations_json: str):
        """Legacy placeholder for backward compatibility, redirects to sync."""
        try:
            items = json.loads(annotations_json)
        except (TypeError, json.JSONDecodeError):
            items = None
        if isinstance(items, list):
            self.sync_work_annotations(work_id, items)
        # Still update the blob table as a backup
        query = """
        INSERT INTO work_annotations (work_id, annotations_json, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(work_id) DO UPDATE SET
            annotations_json = excluded.annotations_json,
            updated_at = CURRENT_TIMESTAMP
        """
        self.execute_query(query, (work_id, annotations_json))

    def sync_work_annotations(self, work_id: str, items: List[dict]):
        """
        Synchronizes a list of annotations for a specific work.
        Deletes items not in the list, updates existing ones, and inserts new ones.
        """
        with self.get_connection() as conn:
            # 1. Get current IDs for this work
            cursor = conn.execute("SELECT id FROM annotations WHERE work_id = ?", (work_id,))
            existing_ids = {row['id'] for row in cursor.fetchall()}
            
            incoming_ids = set()
            for item in items:
                # Robust ID detection: PDF IDs are usually strings
                ann_id = str(item.get('id') or item.get('uuid') or item.get('annotationId') or item.get('_id') or '')
                if not ann_id: continue
                incoming_ids.add(ann_id)
                
                # Extract fields with Snipet V2 fallback logic
                a_type = str(item.get('type') or item.get('annotationType') or item.get('subtype') or 'highlight')
                content = str(item.get('contents') or item.get('content') or item.get('comment') or item.get('text') or item.get('body') or '')
                
                # Page index normalization
                p_idx = item.get('pageIndex')
                if p_idx is None: p_idx = item.get('page')
                if p_idx is None: p_idx = item.get('pageNumber')
                if p_idx is None: p_idx = item.get('page_index')
                try:
                    page = int(p_idx) if p_idx is not None else 0
                except (TypeError, ValueError):
                    page = 0

                color = str(item.get('color', ''))
                
                # Geometry: Snippet often has rects or quadPoints
                geom = json.dumps({
                    'rects': item.get('rects'),
                    'quadPoints': item.get('quadPoints'),
                    'rect': item.get('rect'),
                    'position': item.get('position')
                })
                
                if ann_id in existing_ids:
                    # Update
                    query = """
                    UPDATE annotations SET 
                        type = ?, content = ?, page_index = ?, color = ?, 
                        geometry_json = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """
                    conn.execute(query, (a_type, content, page, color, geom, ann_id))
                else:
                    # Insert
                    query = """
                    INSERT INTO annotations (id, work_id, type, content, page_index, color, geometry_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """
                    conn.execute(query, (ann_id, work_id, a_type, content, page, color, geom))
            
            # 3. Delete items or work context that are no longer present
            to_delete = existing_ids - incoming_ids
            for d_id in to_delete:
                conn.execute("DELETE FROM annotations WHERE id = ?", (d_id,))
            
            conn.commit()

    def resolve_wiki_links(self, text: str) -> str:
        if not text: return ""
        import re
        def replacer(match):
            raw = match.group(1).strip()
            res = self.execute_query("SELECT id, title as name FROM works WHERE id=? OR title=?", (raw, raw))
            if res:
                safe_name = html.escape(str(res[0]["name"] or ""), quote=True)
                safe_id = html.escape(str(res[0]["id"] or ""), quote=True)
                return f'<a href="#/works/{safe_id}" class="wiki-link" style="color:var(--accent); text-decoration:none;">{safe_name}</a>'
            res2 = self.execute_query("SELECT id, (first_name || ' ' || last_name) as name FROM persons WHERE id=? OR last_name=?", (raw, raw))
            if res2:
                safe_name = html.escape(str(res2[0]["name"] or ""), quote=True)
                safe_id = html.escape(str(res2[0]["id"] or ""), quote=True)
                return f'<a href="#/people/{safe_id}" class="wiki-link" style="color:var(--accent); text-decoration:none;">{safe_name}</a>'
            safe_raw = html.escape(raw, quote=True)
            return f'<span class="wiki-link-unresolved" style="color:#ef4444;">[[{safe_raw}]]</span>'
        return re.sub(r'\[\[(.*?)\]\]', replacer, text)

    # --- Folders ---
    def _folder_descendant_ids(self, folder_id: str) -> set:
        rows = self.execute_query(
            """
            WITH RECURSIVE sub(id) AS (
                SELECT id FROM folders WHERE parent_id = ?
                UNION ALL
                SELECT f.id FROM folders f JOIN sub ON f.parent_id = sub.id
            )
            SELECT id FROM sub
            """,
            (folder_id,),
        )
        return {r["id"] for r in rows}

    def _folder_title_taken(
        self,
        title: str,
        parent_id: Optional[str],
        exclude_folder_id: Optional[str] = None,
    ) -> bool:
        params: List[Any] = []
        where_parent = "parent_id IS NULL"
        if parent_id:
            where_parent = "parent_id = ?"
            params.append(parent_id)
        params.append(title)
        sql = (
            f"SELECT id FROM folders WHERE {where_parent} "
            "AND LOWER(TRIM(title)) = LOWER(?)"
        )
        rows = self.execute_query(sql, tuple(params))
        for row in rows:
            if exclude_folder_id and row["id"] == exclude_folder_id:
                continue
            return True
        return False

    def _normalize_folder_parent_id(
        self,
        raw_parent_id: Optional[str],
        current_folder_id: Optional[str] = None,
    ) -> Optional[str]:
        pid = None if raw_parent_id in (None, "", False) else str(raw_parent_id).strip()
        if not pid:
            return None
        if current_folder_id and pid == current_folder_id:
            raise ValueError("A folder cannot be its own parent.")
        exists = self.execute_query("SELECT 1 FROM folders WHERE id = ?", (pid,))
        if not exists:
            raise ValueError("Parent folder not found.")
        if current_folder_id:
            descendants = self._folder_descendant_ids(current_folder_id)
            if pid in descendants:
                raise ValueError("Cannot set parent to a subfolder (cycle).")
        return pid

    def add_folder(
        self,
        title: str,
        description: str = "",
        parent_id: Optional[str] = None,
    ) -> str:
        t = (title or "").strip() or "Untitled Folder"
        d = (description or "").strip()
        pid = self._normalize_folder_parent_id(parent_id)
        if self._folder_title_taken(t, pid):
            raise ValueError("A folder with this name already exists in this location.")
        folder_id = self.generate_id("F")
        query = "INSERT INTO folders (id, title, description, parent_id) VALUES (?, ?, ?, ?)"
        self.execute_query(query, (folder_id, t, d, pid))
        return folder_id

    def get_all_folders(self) -> List[dict]:
        return self.execute_query(
            """
            SELECT
                f.*,
                (SELECT COUNT(*) FROM folder_files ff WHERE ff.folder_id = f.id) AS work_count,
                (SELECT COUNT(*) FROM folders c WHERE c.parent_id = f.id) AS child_count
            FROM folders f
            ORDER BY f.title COLLATE NOCASE, f.created_at DESC
            """
        )

    def get_folder(self, folder_id: str) -> Optional[dict]:
        res = self.execute_query("SELECT * FROM folders WHERE id = ?", (folder_id,))
        if not res: return None
        folder = dict(res[0])
        child_rows = self.execute_query(
            """
            SELECT id, title, parent_id, description,
                (SELECT COUNT(*) FROM folder_files ff WHERE ff.folder_id = folders.id) AS work_count,
                (SELECT COUNT(*) FROM folders c WHERE c.parent_id = folders.id) AS child_count
            FROM folders
            WHERE parent_id = ?
            ORDER BY title COLLATE NOCASE
            """,
            (folder_id,),
        )
        folder["children"] = list(child_rows)
        if folder.get("parent_id"):
            parent_row = self.execute_query(
                "SELECT id, title FROM folders WHERE id = ?",
                (folder["parent_id"],),
            )
            folder["parent"] = dict(parent_row[0]) if parent_row else None
        else:
            folder["parent"] = None
        wsel = _prks_work_summary_select("w")
        pa = _prks_sql_first_linked_person_for_role("w", "Author", "primary_author")
        pe = _prks_sql_first_linked_person_for_role("w", "Editor", "primary_editor")
        query = f"""
        SELECT
            {wsel},
            {pa},
            {pe}
        FROM works w
        JOIN folder_files ff ON w.id = ff.work_id
        WHERE ff.folder_id = ?
        ORDER BY w.created_at DESC
        """
        folder["works"] = list(self.execute_query(query, (folder_id,)))
        enrich_work_rows_pdf_file_size(folder["works"])
        folder['tags'] = self.get_folder_tags(folder_id)
        return folder

    def update_folder_metadata(self, folder_id: str, fields: dict):
        """Update editable folder fields including hierarchy metadata."""
        allowed = {"title", "description", "private_notes", "parent_id"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        exists = self.execute_query("SELECT id FROM folders WHERE id = ?", (folder_id,))
        if not exists:
            raise ValueError("Folder not found.")
        if "parent_id" in updates:
            updates["parent_id"] = self._normalize_folder_parent_id(updates["parent_id"], folder_id)
        final_title = None
        final_parent = None
        if "title" in updates:
            final_title = (updates["title"] or "").strip() or "Untitled Folder"
            updates["title"] = final_title
        if "parent_id" in updates:
            final_parent = updates["parent_id"]
        if final_title is not None or final_parent is not None:
            row = self.execute_query("SELECT title, parent_id FROM folders WHERE id = ?", (folder_id,))
            if not row:
                raise ValueError("Folder not found.")
            candidate_title = final_title if final_title is not None else (row[0]["title"] or "").strip()
            candidate_parent = final_parent if final_parent is not None else row[0]["parent_id"]
            if self._folder_title_taken(candidate_title, candidate_parent, exclude_folder_id=folder_id):
                raise ValueError("A folder with this name already exists in this location.")
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [folder_id]
        self.execute_query(
            f"UPDATE folders SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            tuple(values),
        )

    def add_work_to_folder(self, folder_id: str, work_id: str):
        """Attach a work to a folder. Fails if the work is already in a different folder."""
        fid = (folder_id or "").strip()
        if not fid:
            raise ValueError("folder_id is required")
        wid = (work_id or "").strip()
        if not wid:
            raise ValueError("work_id is required")
        existing = self.execute_query(
            "SELECT folder_id FROM folder_files WHERE work_id = ?",
            (wid,),
        )
        for row in existing:
            if row["folder_id"] != fid:
                raise ValueError("This file is already in another folder.")
        if any(row["folder_id"] == fid for row in existing):
            return
        self.execute_query(
            "INSERT INTO folder_files (folder_id, work_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
            (fid, wid),
        )

    def move_work_to_folder(self, work_id: str, folder_id: Optional[str]) -> None:
        """Remove folder membership, then optionally assign to one folder (assign / move / clear)."""
        wid = (work_id or "").strip()
        if not wid:
            raise ValueError("work_id is required")
        raw = folder_id
        if raw is None:
            fid: Optional[str] = None
        else:
            fid = str(raw).strip() or None
        conn = self.get_connection()
        try:
            conn.execute("DELETE FROM folder_files WHERE work_id = ?", (wid,))
            if fid:
                chk = conn.execute("SELECT id FROM folders WHERE id = ?", (fid,)).fetchone()
                if not chk:
                    conn.rollback()
                    raise ValueError("Folder not found.")
                conn.execute(
                    "INSERT INTO folder_files (folder_id, work_id) VALUES (?, ?)",
                    (fid, wid),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_related_folders_for_work(self, work_id: str) -> List[dict]:
        # Find folders containing ANY work that shares an Author/Reviewer/etc. with THIS work
        query = """
        SELECT DISTINCT f.* FROM folders f
        JOIN folder_files ff ON f.id = ff.folder_id
        JOIN roles r1 ON ff.work_id = r1.work_id
        JOIN roles r2 ON r1.person_id = r2.person_id
        WHERE r2.work_id = ? AND ff.work_id != ?
        """
        return list(self.execute_query(query, (work_id, work_id)))

    # --- Persons ---
    def add_person(
        self,
        first_name: str,
        last_name: str,
        aliases: str = "",
        about: str = "",
        image_url: str = "",
        link_wikipedia: str = "",
        link_stanford_encyclopedia: str = "",
        link_iep: str = "",
        links_other: str = "",
        birth_date: str = "",
        death_date: str = "",
    ) -> str:
        person_id = self.generate_id("P")
        query = """
        INSERT INTO persons (id, first_name, last_name, aliases, about,
            image_url, link_wikipedia, link_stanford_encyclopedia, link_iep, links_other,
            birth_date, death_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        self.execute_query(
            query,
            (
                person_id,
                first_name,
                last_name,
                aliases,
                about,
                image_url,
                link_wikipedia,
                link_stanford_encyclopedia,
                link_iep,
                links_other,
                birth_date,
                death_date,
            ),
        )
        return person_id

    def update_person_metadata(self, person_id: str, fields: dict):
        allowed = {
            "first_name",
            "last_name",
            "aliases",
            "about",
            "image_url",
            "link_wikipedia",
            "link_stanford_encyclopedia",
            "link_iep",
            "links_other",
            "birth_date",
            "death_date",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [person_id]
        self.execute_query(
            f"UPDATE persons SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            tuple(values),
        )
    
    def get_all_persons(self) -> List[dict]:
        query = """
        SELECT p.*, (
            SELECT GROUP_CONCAT(DISTINCT r.role_type)
            FROM roles r WHERE r.person_id = p.id
        ) AS _roles_concat
        FROM persons p ORDER BY last_name ASC
        """
        rows = self.execute_query(query, ())
        for row in rows:
            raw = row.pop("_roles_concat", None)
            row["assigned_roles"] = (
                [x.strip() for x in raw.split(",") if x.strip()] if raw else []
            )
        self._attach_person_groups_batch(rows)
        return rows

    def get_person(self, person_id: str) -> Optional[dict]:
        res = self.execute_query("SELECT * FROM persons WHERE id = ?", (person_id,))
        if not res: return None
        person = res[0]
        pa = _prks_sql_first_linked_person_for_role("w", "Author", "primary_author")
        pe = _prks_sql_first_linked_person_for_role("w", "Editor", "primary_editor")
        query = f"""
        SELECT w.*, r.role_type, r.order_index, {pa}, {pe}
        FROM roles r
        JOIN works w ON r.work_id = w.id
        WHERE r.person_id = ?
        ORDER BY r.order_index ASC, r.rowid ASC
        """
        person["works"] = list(self.execute_query(query, (person_id,)))
        enrich_work_rows_pdf_file_size(person["works"])
        person["groups"] = self.get_groups_for_person(person_id)
        return person

    def _attach_person_groups_batch(self, rows: List[dict]) -> None:
        if not rows:
            return
        ids = [r["id"] for r in rows]
        ph = ",".join("?" * len(ids))
        q = f"""
        SELECT m.person_id, g.id AS group_id, g.name AS group_name
        FROM person_group_members m
        JOIN person_groups g ON g.id = m.group_id
        WHERE m.person_id IN ({ph})
        """
        memb = self.execute_query(q, tuple(ids))
        by_p: Dict[str, List[dict]] = defaultdict(list)
        for m in memb:
            by_p[m["person_id"]].append({"id": m["group_id"], "name": m["group_name"]})
        for r in rows:
            r["groups"] = by_p.get(r["id"], [])

    def get_groups_for_person(self, person_id: str) -> List[dict]:
        q = """
        SELECT g.id, g.name, g.parent_id
        FROM person_groups g
        JOIN person_group_members m ON m.group_id = g.id
        WHERE m.person_id = ?
        ORDER BY g.name COLLATE NOCASE
        """
        return list(self.execute_query(q, (person_id,)))

    def _person_group_descendant_ids(self, group_id: str) -> set:
        """Strict descendants of group_id (not including group_id)."""
        rows = self.execute_query(
            """
            WITH RECURSIVE sub(id) AS (
                SELECT id FROM person_groups WHERE parent_id = ?
                UNION ALL
                SELECT g.id FROM person_groups g JOIN sub ON g.parent_id = sub.id
            )
            SELECT id FROM sub
            """,
            (group_id,),
        )
        return {r["id"] for r in rows}

    def _person_group_id_for_name_insensitive(self, name: str) -> Optional[str]:
        n = (name or "").strip()
        if not n:
            return None
        row = self.execute_query(
            "SELECT id FROM person_groups WHERE LOWER(name) = LOWER(?) LIMIT 1", (n,)
        )
        return row[0]["id"] if row else None

    def _assert_group_name_free(
        self, name: str, exclude_group_id: Optional[str] = None
    ) -> None:
        found = self._person_group_id_for_name_insensitive(name)
        if found and found != exclude_group_id:
            raise ValueError("A group with this name already exists.")

    def resolve_or_create_parent_group_by_name(
        self, parent_name: str, for_child_group_id: Optional[str] = None
    ) -> str:
        """Existing group id (case-insensitive) or new top-level group id."""
        pn = (parent_name or "").strip()
        if not pn:
            raise ValueError("Parent name is required.")
        row = self.execute_query(
            "SELECT id FROM person_groups WHERE LOWER(name) = LOWER(?) LIMIT 1", (pn,)
        )
        if row:
            found = row[0]["id"]
            if for_child_group_id and found == for_child_group_id:
                raise ValueError("A group cannot be its own parent.")
            return found
        return self.add_person_group(pn, None, "")

    def add_person_group(
        self, name: str, parent_id: Optional[str] = None, description: str = ""
    ) -> str:
        n = (name or "").strip()
        if not n:
            raise ValueError("Group name is required.")
        self._assert_group_name_free(n)
        if parent_id:
            ok = self.execute_query("SELECT 1 FROM person_groups WHERE id = ?", (parent_id,))
            if not ok:
                raise ValueError("Parent group not found.")
        gid = self.generate_id("PG")
        self.execute_query(
            """
            INSERT INTO person_groups (id, name, parent_id, description)
            VALUES (?, ?, ?, ?)
            """,
            (gid, n, parent_id or None, (description or "").strip()),
        )
        return gid

    def add_person_group_with_parent_options(
        self,
        name: str,
        parent_id: Optional[str] = None,
        parent_name: Optional[str] = None,
        description: str = "",
    ) -> str:
        """Create a group; parent from parent_id, else resolve/create from parent_name (top-level)."""
        n = (name or "").strip()
        if not n:
            raise ValueError("Group name is required.")
        pid: Optional[str] = None
        if parent_id:
            pid = parent_id
            ok = self.execute_query("SELECT 1 FROM person_groups WHERE id = ?", (pid,))
            if not ok:
                raise ValueError("Parent group not found.")
        else:
            pn = (parent_name or "").strip() if parent_name is not None else ""
            if pn:
                if pn.lower() == n.lower():
                    raise ValueError("Parent group cannot have the same name as the new group.")
                pid = self.resolve_or_create_parent_group_by_name(pn, None)
        return self.add_person_group(n, pid, description)

    def update_person_group(self, group_id: str, fields: dict) -> None:
        allowed = {"name", "parent_id", "description"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        if "parent_id" in updates:
            raw_p = updates["parent_id"]
            new_parent = None if raw_p in (None, "", False) else raw_p
            updates["parent_id"] = new_parent
            if new_parent == group_id:
                raise ValueError("A group cannot be its own parent.")
            if new_parent:
                ok = self.execute_query(
                    "SELECT 1 FROM person_groups WHERE id = ?", (new_parent,)
                )
                if not ok:
                    raise ValueError("Parent group not found.")
                desc = self._person_group_descendant_ids(group_id)
                if new_parent in desc:
                    raise ValueError("Cannot set parent to a subgroup (cycle).")
        if "name" in updates:
            updates["name"] = (updates["name"] or "").strip()
            if not updates["name"]:
                raise ValueError("Group name is required.")
            self._assert_group_name_free(updates["name"], exclude_group_id=group_id)
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [group_id]
        self.execute_query(
            f"UPDATE person_groups SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            tuple(vals),
        )

    def delete_person_group(self, group_id: str) -> None:
        row = self.execute_query(
            "SELECT parent_id FROM person_groups WHERE id = ?", (group_id,)
        )
        if not row:
            raise ValueError("Group not found.")
        parent = row[0]["parent_id"]
        self.execute_query(
            "UPDATE person_groups SET parent_id = ? WHERE parent_id = ?",
            (parent, group_id),
        )
        self.execute_query("DELETE FROM person_groups WHERE id = ?", (group_id,))

    def get_all_person_groups(self) -> List[dict]:
        q = """
        SELECT g.*,
            (SELECT COUNT(*) FROM person_group_members m WHERE m.group_id = g.id) AS member_count,
            (SELECT COUNT(*) FROM person_groups c WHERE c.parent_id = g.id) AS child_count
        FROM person_groups g
        ORDER BY g.name COLLATE NOCASE
        """
        return list(self.execute_query(q, ()))

    def get_person_group(self, group_id: str) -> Optional[dict]:
        res = self.execute_query("SELECT * FROM person_groups WHERE id = ?", (group_id,))
        if not res:
            return None
        g = dict(res[0])
        g["member_count"] = self.execute_query(
            "SELECT COUNT(*) AS c FROM person_group_members WHERE group_id = ?",
            (group_id,),
        )[0]["c"]
        g["children"] = self.execute_query(
            """
            SELECT pg.id, pg.name, pg.parent_id, pg.description,
                (SELECT COUNT(*) FROM person_group_members m WHERE m.group_id = pg.id) AS member_count
            FROM person_groups pg WHERE pg.parent_id = ?
            ORDER BY pg.name COLLATE NOCASE
            """,
            (group_id,),
        )
        member_rows = self.execute_query(
            """
            SELECT p.*, (
                SELECT GROUP_CONCAT(DISTINCT r.role_type)
                FROM roles r WHERE r.person_id = p.id
            ) AS _roles_concat
            FROM persons p
            JOIN person_group_members m ON m.person_id = p.id
            WHERE m.group_id = ?
            ORDER BY p.last_name COLLATE NOCASE, p.first_name COLLATE NOCASE
            """,
            (group_id,),
        )
        for row in member_rows:
            raw = row.pop("_roles_concat", None)
            row["assigned_roles"] = (
                [x.strip() for x in raw.split(",") if x.strip()] if raw else []
            )
        g["members"] = member_rows
        self._attach_person_groups_batch(g["members"])
        if g.get("parent_id"):
            prow = self.execute_query(
                "SELECT id, name FROM person_groups WHERE id = ?",
                (g["parent_id"],),
            )
            g["parent"] = dict(prow[0]) if prow else None
        else:
            g["parent"] = None
        return g

    def add_person_to_group(self, person_id: str, group_id: str) -> None:
        ok_p = self.execute_query("SELECT 1 FROM persons WHERE id = ?", (person_id,))
        ok_g = self.execute_query("SELECT 1 FROM person_groups WHERE id = ?", (group_id,))
        if not ok_p or not ok_g:
            raise ValueError("Person or group not found.")
        self.execute_query(
            """
            INSERT OR IGNORE INTO person_group_members (person_id, group_id)
            VALUES (?, ?)
            """,
            (person_id, group_id),
        )

    def remove_person_from_group(self, person_id: str, group_id: str) -> None:
        self.execute_query(
            "DELETE FROM person_group_members WHERE person_id = ? AND group_id = ?",
            (person_id, group_id),
        )

    def set_person_group_memberships(self, person_id: str, group_ids: List[str]) -> None:
        ok = self.execute_query("SELECT 1 FROM persons WHERE id = ?", (person_id,))
        if not ok:
            raise ValueError("Person not found.")
        seen = set()
        clean: List[str] = []
        for gid in group_ids or []:
            if not gid or gid in seen:
                continue
            seen.add(gid)
            gr = self.execute_query("SELECT 1 FROM person_groups WHERE id = ?", (gid,))
            if not gr:
                raise ValueError(f"Unknown group id: {gid}")
            clean.append(gid)
        with self.get_connection() as conn:
            conn.execute("DELETE FROM person_group_members WHERE person_id = ?", (person_id,))
            for gid in clean:
                conn.execute(
                    """
                    INSERT INTO person_group_members (person_id, group_id)
                    VALUES (?, ?)
                    """,
                    (person_id, gid),
                )
            conn.commit()

    # --- Roles (Linking) ---
    def add_role(self, person_id: str, work_id: str, role_type: str, order_index: int = 0):
        query = """
        INSERT INTO roles (person_id, work_id, role_type, order_index)
        VALUES (?, ?, ?, ?)
        """
        self.execute_query(query, (person_id, work_id, role_type, order_index))

    def next_role_order_index(self, work_id: str) -> int:
        """Next order_index for a new role on this work (append after existing links)."""
        rows = self.execute_query(
            "SELECT COALESCE(MAX(order_index), -1) AS m FROM roles WHERE work_id = ?",
            (work_id,),
        )
        if not rows:
            return 0
        return int(rows[0]["m"]) + 1

    def get_work_roles(self, work_id: str) -> List[dict]:
        query = """
        SELECT p.*, r.role_type, r.order_index 
        FROM roles r
        JOIN persons p ON r.person_id = p.id
        WHERE r.work_id = ?
        ORDER BY r.order_index ASC, r.rowid ASC
        """
        return self.execute_query(query, (work_id,))

    def delete_work_role(self, work_id: str, person_id: str, role_type: str, order_index: int) -> bool:
        """Remove one role row (composite PK). Returns True if a row was deleted."""
        with self.get_connection() as conn:
            cur = conn.execute(
                """
                DELETE FROM roles
                WHERE work_id = ? AND person_id = ? AND role_type = ? AND order_index = ?
                """,
                (work_id, person_id, role_type, int(order_index)),
            )
            conn.commit()
            return cur.rowcount > 0

    # --- Concepts & Arguments ---
    def add_concept(self, name: str, description: str = "") -> str:
        concept_id = self.generate_id("C")
        query = "INSERT INTO concepts (id, name, description) VALUES (?, ?, ?)"
        self.execute_query(query, (concept_id, name, description))
        return concept_id

    def add_argument(self, work_id: str, premise: str, conclusion: str) -> str:
        arg_id = self.generate_id("A")
        query = "INSERT INTO arguments (id, work_id, premise, conclusion) VALUES (?, ?, ?, ?)"
        self.execute_query(query, (arg_id, work_id, premise, conclusion))
        return arg_id

    # --- Tags ---
    def resolve_tag_id_by_label(self, label: str) -> Optional[str]:
        """Map a canonical name or alias (any casing) to canonical tag id."""
        name = (label or "").strip()
        if not name:
            return None
        row = self.execute_query(
            "SELECT id FROM tags WHERE LOWER(name) = LOWER(?) LIMIT 1", (name,)
        )
        if row:
            return row[0]["id"]
        row = self.execute_query(
            "SELECT tag_id FROM tag_aliases WHERE LOWER(alias) = LOWER(?) LIMIT 1", (name,)
        )
        if row:
            return row[0]["tag_id"]
        return None

    def _tag_alias_map(self) -> Dict[str, List[str]]:
        rows = self.execute_query(
            "SELECT tag_id, alias FROM tag_aliases ORDER BY LOWER(alias) ASC"
        )
        m: Dict[str, List[str]] = defaultdict(list)
        for r in rows:
            m[r["tag_id"]].append(r["alias"])
        return dict(m)

    def _enrich_tag_rows_with_aliases(self, rows: List[dict]) -> None:
        amap = self._tag_alias_map()
        for r in rows:
            r["aliases"] = list(amap.get(r["id"], []))

    def add_tag(self, name: str, color: str = "#6d6cf7") -> Dict[str, Any]:
        raw = (name or "").strip()
        if not raw:
            raise ValueError("tag name is empty")
        existing_id = self.resolve_tag_id_by_label(raw)
        if existing_id:
            self.execute_query(
                "UPDATE tags SET color = ? WHERE id = ?", (color, existing_id)
            )
            row = self.execute_query(
                "SELECT id, name, color FROM tags WHERE id = ?", (existing_id,)
            )
            r = row[0]
            return {
                "id": r["id"],
                "name": r["name"],
                "color": r["color"],
                "existed": True,
            }
        tag_id = self.generate_id("T")
        self.execute_query(
            "INSERT INTO tags (id, name, color) VALUES (?, ?, ?)", (tag_id, raw, color)
        )
        return {"id": tag_id, "name": raw, "color": color, "existed": False}

    def add_tag_alias(self, tag_id: str, alias: str) -> None:
        al = (alias or "").strip()
        if not al:
            raise ValueError("alias is empty")
        trow = self.execute_query("SELECT id, name FROM tags WHERE id = ?", (tag_id,))
        if not trow:
            raise ValueError("tag not found")
        canon = (trow[0]["name"] or "").strip()
        if canon.lower() == al.lower():
            raise ValueError("alias matches canonical tag name")
        other = self.execute_query(
            "SELECT id FROM tags WHERE LOWER(name) = LOWER(?) AND id != ?",
            (al, tag_id),
        )
        if other:
            raise ValueError("alias conflicts with another tag name")
        taken = self.execute_query(
            "SELECT tag_id FROM tag_aliases WHERE LOWER(alias) = LOWER(?)",
            (al,),
        )
        if taken:
            if taken[0]["tag_id"] == tag_id:
                return
            raise ValueError("alias already used")
        aid = self.generate_id("L")
        self.execute_query(
            "INSERT INTO tag_aliases (id, tag_id, alias) VALUES (?, ?, ?)",
            (aid, tag_id, al),
        )

    def merge_tags_into(self, source_tag_id: str, target_tag_id: str) -> Dict[str, Any]:
        """Move all links from source tag to target, drop source row, add source name as alias of target."""
        source = (source_tag_id or "").strip()
        target = (target_tag_id or "").strip()
        if not source or not target:
            raise ValueError("source_tag_id and target_tag_id are required")
        if source == target:
            raise ValueError("cannot merge a tag into itself")

        with self.get_connection() as conn:
            srow = conn.execute("SELECT id, name FROM tags WHERE id = ?", (source,)).fetchone()
            trow = conn.execute("SELECT id, name FROM tags WHERE id = ?", (target,)).fetchone()
            if not srow or not trow:
                raise ValueError("tag not found")

            source_name = (srow["name"] or "").strip()
            target_name = (trow["name"] or "").strip()

            conn.execute(
                "INSERT OR IGNORE INTO work_tags (work_id, tag_id) "
                "SELECT work_id, ? FROM work_tags WHERE tag_id = ?",
                (target, source),
            )
            conn.execute("DELETE FROM work_tags WHERE tag_id = ?", (source,))

            conn.execute(
                "INSERT OR IGNORE INTO folder_tags (folder_id, tag_id) "
                "SELECT folder_id, ? FROM folder_tags WHERE tag_id = ?",
                (target, source),
            )
            conn.execute("DELETE FROM folder_tags WHERE tag_id = ?", (source,))

            alias_rows = conn.execute(
                "SELECT id, alias FROM tag_aliases WHERE tag_id = ?", (source,)
            ).fetchall()
            for ar in alias_rows:
                aid = ar["id"]
                al = (ar["alias"] or "").strip()
                if not al:
                    conn.execute("DELETE FROM tag_aliases WHERE id = ?", (aid,))
                    continue
                if al.lower() == target_name.lower():
                    conn.execute("DELETE FROM tag_aliases WHERE id = ?", (aid,))
                    continue
                other = conn.execute(
                    "SELECT id FROM tag_aliases WHERE LOWER(alias) = LOWER(?) AND id != ?",
                    (al, aid),
                ).fetchone()
                if other:
                    conn.execute("DELETE FROM tag_aliases WHERE id = ?", (aid,))
                else:
                    conn.execute(
                        "UPDATE tag_aliases SET tag_id = ? WHERE id = ?",
                        (target, aid),
                    )

            conn.execute("DELETE FROM tags WHERE id = ?", (source,))

            if source_name and source_name.lower() != target_name.lower():
                exists = conn.execute(
                    """
                    SELECT 1 FROM tag_aliases
                    WHERE tag_id = ? AND LOWER(alias) = LOWER(?)
                    LIMIT 1
                    """,
                    (target, source_name),
                ).fetchone()
                if not exists:
                    new_aid = self.generate_id("L")
                    try:
                        conn.execute(
                            "INSERT INTO tag_aliases (id, tag_id, alias) VALUES (?, ?, ?)",
                            (new_aid, target, source_name),
                        )
                    except sqlite3.IntegrityError:
                        pass

            conn.commit()

        return {"canonical_tag_id": target, "canonical_name": target_name}

    def delete_tag_alias(self, tag_id: str, alias: str) -> bool:
        al = (alias or "").strip()
        if not al:
            return False
        with self.get_connection() as conn:
            c = conn.execute(
                "DELETE FROM tag_aliases WHERE tag_id = ? AND LOWER(alias) = LOWER(?)",
                (tag_id, al),
            )
            conn.commit()
            return c.rowcount > 0

    def add_publisher(self, name: str) -> Dict[str, Any]:
        raw = (name or "").strip()
        if not raw:
            raise ValueError("publisher name is empty")
        existing = self.execute_query(
            "SELECT id, name FROM publishers WHERE LOWER(name) = LOWER(?)", (raw,)
        )
        if existing:
            return {
                "id": existing[0]["id"],
                "name": existing[0]["name"],
                "existed": True,
            }
        pid = self.generate_id("R")
        self.execute_query(
            "INSERT INTO publishers (id, name) VALUES (?, ?)", (pid, raw)
        )
        return {"id": pid, "name": raw, "existed": False}

    def add_publisher_alias(self, publisher_id: str, alias: str) -> None:
        al = (alias or "").strip()
        if not al:
            raise ValueError("alias is empty")
        prow = self.execute_query(
            "SELECT id, name FROM publishers WHERE id = ?", (publisher_id,)
        )
        if not prow:
            raise ValueError("publisher not found")
        canon = (prow[0]["name"] or "").strip()
        if canon.lower() == al.lower():
            raise ValueError("alias matches canonical publisher name")
        other = self.execute_query(
            "SELECT id FROM publishers WHERE LOWER(name) = LOWER(?) AND id != ?",
            (al, publisher_id),
        )
        if other:
            raise ValueError("alias conflicts with another publisher name")
        taken = self.execute_query(
            "SELECT publisher_id FROM publisher_aliases WHERE LOWER(alias) = LOWER(?)",
            (al,),
        )
        if taken:
            if taken[0]["publisher_id"] == publisher_id:
                return
            raise ValueError("alias already used")
        aid = self.generate_id("M")
        self.execute_query(
            "INSERT INTO publisher_aliases (id, publisher_id, alias) VALUES (?, ?, ?)",
            (aid, publisher_id, al),
        )

    def delete_publisher_alias(self, publisher_id: str, alias: str) -> bool:
        al = (alias or "").strip()
        if not al:
            return False
        with self.get_connection() as conn:
            c = conn.execute(
                "DELETE FROM publisher_aliases WHERE publisher_id = ? AND LOWER(alias) = LOWER(?)",
                (publisher_id, al),
            )
            conn.commit()
            return c.rowcount > 0

    def delete_publisher(self, publisher_id: str) -> None:
        self.execute_query("DELETE FROM publishers WHERE id = ?", (publisher_id,))

    def get_publishers_in_use(self) -> List[dict]:
        """Publishers that have at least one alias or at least one work matching name/alias (nocase)."""
        rows = self.execute_query(
            "SELECT id, name, created_at FROM publishers ORDER BY LOWER(name) ASC"
        )
        out: List[dict] = []
        for r in rows:
            pid = r["id"]
            arows = self.execute_query(
                "SELECT alias FROM publisher_aliases WHERE publisher_id = ? ORDER BY LOWER(alias) ASC",
                (pid,),
            )
            aliases = [x["alias"] for x in arows if (x["alias"] or "").strip()]
            name = (r["name"] or "").strip()
            labels = [name] + aliases if name else list(aliases)
            cleaned = [x.strip() for x in labels if x and x.strip()]
            work_count = 0
            if cleaned:
                lows = [x.lower() for x in cleaned]
                ph = ",".join("?" * len(lows))
                wc = self.execute_query(
                    f"""
                    SELECT COUNT(DISTINCT id) AS c FROM works
                    WHERE TRIM(COALESCE(publisher,'')) != ''
                      AND LOWER(TRIM(publisher)) IN ({ph})
                    """,
                    tuple(lows),
                )
                work_count = int(wc[0]["c"]) if wc else 0
            out.append(
                {
                    "id": pid,
                    "name": name,
                    "aliases": aliases,
                    "work_count": work_count,
                }
            )
        return out

    def get_all_tags(self) -> List[dict]:
        rows = self.execute_query("SELECT * FROM tags ORDER BY name ASC")
        self._enrich_tag_rows_with_aliases(rows)
        return rows

    def get_tags_in_use(self) -> List[dict]:
        query = """
        SELECT
            t.*,
            (SELECT COUNT(*) FROM work_tags wt WHERE wt.tag_id = t.id) AS work_count,
            (SELECT COUNT(*) FROM folder_tags ft WHERE ft.tag_id = t.id) AS folder_count
        FROM tags t
        WHERE EXISTS (SELECT 1 FROM work_tags wt WHERE wt.tag_id = t.id)
           OR EXISTS (SELECT 1 FROM folder_tags ft WHERE ft.tag_id = t.id)
        ORDER BY t.name ASC
        """
        rows = list(self.execute_query(query, ()))
        self._enrich_tag_rows_with_aliases(rows)
        return rows

    def get_recent_tags_in_use(self, limit: int = 8) -> List[dict]:
        """Tags in use, ordered by latest activity (opened work, folder update, or tag creation)."""
        lim = max(1, min(int(limit), 50))
        query = """
        SELECT t.id, t.name, t.color, t.created_at,
            (SELECT MAX(w.last_opened_at) FROM works w
             INNER JOIN work_tags wt ON w.id = wt.work_id WHERE wt.tag_id = t.id) AS last_open,
            (SELECT MAX(f.updated_at) FROM folders f
             INNER JOIN folder_tags ft ON f.id = ft.folder_id WHERE ft.tag_id = t.id) AS last_folder
        FROM tags t
        WHERE EXISTS (SELECT 1 FROM work_tags wt WHERE wt.tag_id = t.id)
           OR EXISTS (SELECT 1 FROM folder_tags ft WHERE ft.tag_id = t.id)
        """

        def _ts(val: Any) -> float:
            if val is None or val == "":
                return 0.0
            s = str(val).strip().replace("T", " ")[:19]
            try:
                return datetime.fromisoformat(s).timestamp()
            except ValueError:
                try:
                    return datetime.strptime(s[:10], "%Y-%m-%d").timestamp()
                except ValueError:
                    return 0.0

        def rec_key(row: dict) -> float:
            return max(_ts(row["last_open"]), _ts(row["last_folder"]), _ts(row["created_at"]))

        rows = list(self.execute_query(query, ()))
        rows.sort(key=rec_key, reverse=True)
        out: List[dict] = []
        for r in rows[:lim]:
            out.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "color": r["color"],
                    "created_at": r["created_at"],
                }
            )
        self._enrich_tag_rows_with_aliases(out)
        return out

    @staticmethod
    def _parse_wiki_link_inner(inner: str) -> Tuple[str, str]:
        inner = inner.strip()
        if "|" in inner:
            target, alias = inner.split("|", 1)
            return target.strip(), alias.strip() or target.strip()
        return inner, inner

    @staticmethod
    def _graph_norm_title(s: str) -> str:
        """Lowercase + collapse internal whitespace for title / link matching."""
        return " ".join((s or "").strip().lower().split())

    def build_graph_data(self) -> Dict[str, Any]:
        """Nodes = works; edges = resolved wiki links, co-cited unresolved [[links]], shared tags."""
        works = list(
            self.execute_query("SELECT id, title, text_content, abstract, doc_type FROM works")
        )
        title_lower_to_id: Dict[str, str] = {}
        for w in sorted(works, key=lambda r: r["id"]):
            t = self._graph_norm_title(w.get("title") or "")
            if t and t not in title_lower_to_id:
                title_lower_to_id[t] = w["id"]

        title_norm_counts = Counter(
            self._graph_norm_title((w.get("title") or "Untitled").strip() or "Untitled") for w in works
        )
        nodes: List[dict] = []
        for w in works:
            raw_title = (w.get("title") or "Untitled").strip() or "Untitled"
            nt = self._graph_norm_title(raw_title)
            label = raw_title
            if title_norm_counts.get(nt, 0) > 1:
                label = f"{raw_title}\n{w['id']}"
            dt = normalize_doc_type(w.get("doc_type"))
            nodes.append({"id": w["id"], "label": label, "doc_type": dt, "group": dt})
        node_ids = {w["id"] for w in works}
        edges: List[dict] = []
        seen: set = set()

        def add_edge(src: str, tgt: str, kind: str) -> None:
            if src == tgt or src not in node_ids or tgt not in node_ids:
                return
            # Wiki links are directional (from = file containing [[link]], to = linked file).
            # Co-citation and shared tags are symmetric; dedupe by unordered pair.
            if kind == "wiki":
                key = (src, tgt, kind)
            else:
                key = tuple(sorted((src, tgt))) + (kind,)
            if key in seen:
                return
            seen.add(key)
            edges.append({"from": src, "to": tgt, "kind": kind})

        wiki_re = re.compile(r"\[\[([^\]]+)\]\]")
        unresolved_bucket: Dict[str, List[str]] = defaultdict(list)

        for w in works:
            wid = w["id"]
            blob = ((w.get("text_content") or "") + "\n" + (w.get("abstract") or ""))
            inners_seen: set = set()
            for m in wiki_re.finditer(blob):
                target_title, _alias = self._parse_wiki_link_inner(m.group(1))
                lk = self._graph_norm_title(target_title)
                if not lk or len(lk) < 2:
                    continue
                if lk in inners_seen:
                    continue
                inners_seen.add(lk)
                tid = title_lower_to_id.get(lk)
                if tid:
                    add_edge(wid, tid, "wiki")
                else:
                    unresolved_bucket[lk].append(wid)

        for _lk, wids in unresolved_bucket.items():
            uniq: List[str] = []
            for x in wids:
                if x not in uniq:
                    uniq.append(x)
            if len(uniq) < 2:
                continue
            for i in range(len(uniq)):
                for j in range(i + 1, len(uniq)):
                    add_edge(uniq[i], uniq[j], "wiki_cocite")

        pair_rows = self.execute_query(
            """
            SELECT wt1.work_id AS a, wt2.work_id AS b
            FROM work_tags wt1
            JOIN work_tags wt2 ON wt1.tag_id = wt2.tag_id AND wt1.work_id < wt2.work_id
            """
        )
        for row in pair_rows:
            add_edge(row["a"], row["b"], "shared_tag")

        return {"nodes": nodes, "edges": edges}

    def delete_tag(self, tag_id: str):
        self.execute_query("DELETE FROM tags WHERE id = ?", (tag_id,))

    def _prune_tag_if_unused(self, tag_id: str) -> None:
        """Remove tag row (and aliases via FK) when nothing links to it."""
        if not tag_id:
            return
        row = self.execute_query(
            """
            SELECT (
                EXISTS(SELECT 1 FROM work_tags WHERE tag_id = ?)
                OR EXISTS(SELECT 1 FROM folder_tags WHERE tag_id = ?)
            ) AS in_use
            """,
            (tag_id, tag_id),
        )
        if row and row[0].get("in_use"):
            return
        self.delete_tag(tag_id)

    def add_tag_to_work(self, work_id: str, tag_id: str):
        self.execute_query("INSERT INTO work_tags (work_id, tag_id) VALUES (?, ?) ON CONFLICT DO NOTHING", (work_id, tag_id))

    def remove_tag_from_work(self, work_id: str, tag_id: str):
        self.execute_query("DELETE FROM work_tags WHERE work_id = ? AND tag_id = ?", (work_id, tag_id))
        self._prune_tag_if_unused(tag_id)

    def add_tag_to_folder(self, folder_id: str, tag_id: str):
        self.execute_query("INSERT INTO folder_tags (folder_id, tag_id) VALUES (?, ?) ON CONFLICT DO NOTHING", (folder_id, tag_id))

    def remove_tag_from_folder(self, folder_id: str, tag_id: str):
        self.execute_query("DELETE FROM folder_tags WHERE folder_id = ? AND tag_id = ?", (folder_id, tag_id))
        self._prune_tag_if_unused(tag_id)

    def get_work_tags(self, work_id: str) -> List[dict]:
        query = """
        SELECT t.* FROM tags t
        JOIN work_tags wt ON t.id = wt.tag_id
        WHERE wt.work_id = ?
        """
        return self.execute_query(query, (work_id,))

    def get_folder_tags(self, folder_id: str) -> List[dict]:
        query = """
        SELECT t.* FROM tags t
        JOIN folder_tags ft ON t.id = ft.tag_id
        WHERE ft.folder_id = ?
        """
        return self.execute_query(query, (folder_id,))

    # --- BibLaTeX Generation ---
    @staticmethod
    def _format_biblatex_location(raw: Any) -> Optional[str]:
        """Join place names for BibLaTeX list field (; separated → ' and ')."""
        if raw is None:
            return None
        s = str(raw).strip()
        if not s:
            return None
        parts: List[str] = []
        for seg in re.split(r"\s*;\s*", s):
            seg = re.sub(r"\s+", " ", seg.strip())
            if seg:
                parts.append(seg)
        if not parts:
            return None
        return " and ".join(parts)

    @staticmethod
    def _format_biblatex_person_name(last_name: Any, first_name: Any) -> Optional[str]:
        """BibTeX/BibLaTeX author fragment: 'Family, Given' when both exist; else single name, no stray comma."""
        ln = str(last_name or "").strip()
        fn = str(first_name or "").strip()
        if ln and fn:
            return f"{ln}, {fn}"
        if ln:
            return ln
        if fn:
            return fn
        return None

    @staticmethod
    def _biblatex_cite_key_part_from_person(p: dict) -> str:
        """First segment for cite key: prefer family name, else given / mononym."""
        ln = str(p.get("last_name") or "").strip()
        fn = str(p.get("first_name") or "").strip()
        if ln:
            return ln.split()[0] if ln.split() else ln
        if fn:
            return fn.split()[0] if fn.split() else fn
        return "Unknown"

    def _biblatex_names_for_role(self, roles: List[dict], role_type: str) -> List[str]:
        out: List[str] = []
        for p in roles:
            if p.get("role_type") != role_type:
                continue
            nm = self._format_biblatex_person_name(p.get("last_name"), p.get("first_name"))
            if nm:
                out.append(nm)
        return out

    @staticmethod
    def _bibtex_escape_field(value: Any) -> str:
        """Escape characters that have special meaning inside BibTeX braces."""
        s = str(value) if value is not None else ""
        # Percent signs introduce BibTeX comments inside fields; braces must be balanced.
        s = s.replace('%', r'\%')
        s = s.replace('{', r'\{')
        s = s.replace('}', r'\}')
        return s

    def generate_bibtex(self, work_id: str) -> str:
        work_res = self.execute_query("SELECT * FROM works WHERE id = ?", (work_id,))
        if not work_res: return ""
        work = work_res[0]

        roles = self.get_work_roles(work_id)
        # Roles sorted by order_index, then rowid (stable order when order_index ties).
        linked_authors = self._biblatex_names_for_role(roles, "Author")
        editors = self._biblatex_names_for_role(roles, "Editor")
        translators = self._biblatex_names_for_role(roles, "Translator")
        introductions = self._biblatex_names_for_role(roles, "Introduction")
        forewords = self._biblatex_names_for_role(roles, "Foreword")
        afterwords = self._biblatex_names_for_role(roles, "Afterword")

        if linked_authors:
            author_str = " and ".join(linked_authors)
        else:
            author_str = None

        # Year: prefer dedicated year field, fall back to published_date prefix
        year = work.get('year') or (work.get('published_date', '')[:4] if work.get('published_date') else None)

        first_author_person = next((p for p in roles if p["role_type"] == "Author"), None)
        first_author_last = (
            self._biblatex_cite_key_part_from_person(first_author_person)
            if first_author_person
            else "Unknown"
        )
        cite_key = f"{first_author_last}{year or 'UnknownYear'}"

        raw_dt = work.get("doc_type")
        if raw_dt is not None and str(raw_dt).strip() != "":
            entry_type = normalize_doc_type(raw_dt)
        else:
            entry_type = (
                "article"
                if work.get("journal")
                else "book"
                if work.get("publisher")
                else "misc"
            )

        esc = self._bibtex_escape_field
        bf = self._get_bibtex_export_profile()

        def _export(key: str) -> bool:
            return bool(bf.get(key, True))

        bibtex = f"@{entry_type}{{{cite_key},\n"
        bibtex += f"  title = {{{esc(work['title'])}}},\n"
        if author_str and _export("author"):
            bibtex += f"  author = {{{esc(author_str)}}},\n"
        if editors and _export("editor"):
            bibtex += f"  editor = {{{esc(' and '.join(editors))}}},\n"
        if translators and _export("translator"):
            bibtex += f"  translator = {{{esc(' and '.join(translators))}}},\n"
        if introductions and _export("introduction"):
            bibtex += f"  introduction = {{{esc(' and '.join(introductions))}}},\n"
        if forewords and _export("foreword"):
            bibtex += f"  foreword = {{{esc(' and '.join(forewords))}}},\n"
        if afterwords and _export("afterword"):
            bibtex += f"  afterword = {{{esc(' and '.join(afterwords))}}},\n"
        if year and _export("year"):
            bibtex += f"  year = {{{esc(year)}}},\n"
        if work.get('publisher') and _export("publisher"):
            bibtex += f"  publisher = {{{esc(work['publisher'])}}},\n"
        loc_fmt = self._format_biblatex_location(work.get("location"))
        if loc_fmt and _export("location"):
            bibtex += f"  location = {{{esc(loc_fmt)}}},\n"
        if work.get('edition') and _export("edition"):
            bibtex += f"  edition = {{{esc(work['edition'])}}},\n"
        if work.get('journal') and _export("journal"):
            bibtex += f"  journal = {{{esc(work['journal'])}}},\n"
        if work.get('volume') and _export("volume"):
            bibtex += f"  volume = {{{esc(work['volume'])}}},\n"
        if work.get('issue') and _export("number"):
            bibtex += f"  number = {{{esc(work['issue'])}}},\n"
        if work.get('pages') and _export("pages"):
            bibtex += f"  pages = {{{esc(work['pages'])}}},\n"
        if work.get('isbn') and _export("isbn"):
            bibtex += f"  isbn = {{{esc(work['isbn'])}}},\n"
        if work.get('doi') and _export("doi"):
            bibtex += f"  doi = {{{esc(work['doi'])}}},\n"
        # BibLaTeX: url + urldate for @online; optional url for other types (e.g. PDF saved from web).
        url = (work.get("source_url") or "").strip()
        if _export("url"):
            if entry_type == "online":
                if url:
                    bibtex += f"  url = {{{esc(url)}}},\n"
                # Accessed date should always match last edit time on the work.
                # `updated_at` is maintained by update_work_metadata().
                ua = (work.get("updated_at") or "").strip()
                urld = ua[:10] if len(ua) >= 10 else ""
                if urld:
                    bibtex += f"  urldate = {{{urld}}},\n"
            elif url:
                bibtex += f"  url = {{{esc(url)}}},\n"
                ua = (work.get("updated_at") or "").strip()
                urld = ua[:10] if len(ua) >= 10 else ""
                if urld:
                    bibtex += f"  urldate = {{{urld}}},\n"
        if work.get('abstract') and _export("abstract"):
            bibtex += f"  abstract = {{{esc(work['abstract'])}}},\n"
        bibtex += "}"
        return bibtex
