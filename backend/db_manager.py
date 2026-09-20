import sqlite3
import os
import re
import uuid
import json
import hashlib
import html
import shutil
import logging
from collections import Counter, defaultdict
from contextlib import contextmanager
from urllib.parse import unquote
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
from backend import (folder_sync, folder_tag_sync, person_group_sync, person_metadata_sync,
                     person_sync, playlist_sync, tag_sync, work_metadata_sync, work_open_sync,
                     work_role_sync, work_source_sync, work_tag_sync)
from backend.db_migrations import LATEST_SCHEMA_VERSION, ensure_database_schema
from backend.entity_ids import generate as generate_entity_id, is_distributed
from backend.log_safety import safe_error_type, safe_log_label
from backend.pdf_annotations import (
    WorkAnnotationError,
    annotations_semantically_equal,
    normalize_annotation_list,
    parse_annotations_json,
    reconstruct_annotation,
    round_trip_annotation,
)
from backend import pdf_annotation_sync
from backend.pdf_linearize import maybe_linearize_pdf_in_place
from backend.performance import (
    classify_sql_write,
    clock_ns,
    record_counter,
    record_db_call,
    record_span,
    span as perf_span,
)
from backend.storage import paths
from backend.storage.config import StorageConfig

LOGGER = logging.getLogger("prks.db")

# Current schema ceiling. Restore refuses backups newer than this.
PRKS_SCHEMA_VERSION = LATEST_SCHEMA_VERSION

# One definition, in the module that decides whether a field value is valid.
PRKS_BIBTEX_DOC_TYPES = work_metadata_sync.DOC_TYPE_SET

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

# One definition, in the module that decides whether a field value is valid at
# all. PATCH, the bulk action and the synchronization handler all answer the
# same question, so they must consult the same list.
PRKS_WORK_STATUSES: Tuple[str, ...] = work_metadata_sync.WORK_STATUSES
PRKS_WORK_STATUS_SET = work_metadata_sync.WORK_STATUS_SET
PRKS_BULK_WORK_ACTIONS = frozenset({"set_status", "move_folder", "add_tags", "remove_tags"})
PRKS_BULK_WORK_MAX = 500


class BulkWorkError(ValueError):
    """Controlled bulk-organization failure. http_status is 400 or 404."""

    def __init__(self, message: str, http_status: int = 400):
        super().__init__(message)
        self.http_status = http_status


class SavedViewError(ValueError):
    """Controlled Saved View failure. http_status is 400, 404, or 409."""

    def __init__(self, message: str, http_status: int = 400):
        super().__init__(message)
        self.http_status = http_status


PRKS_SAVED_VIEW_MAX = 100
PRKS_SAVED_VIEW_NAME_MAX = 80
PRKS_SAVED_VIEW_Q_MAX = 500
PRKS_SAVED_VIEW_FIELD_MAX = 200
_SAVED_VIEW_MODES = frozenset({"all", "advanced", "tag"})
_SAVED_VIEW_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _saved_view_has_controls(value: str) -> bool:
    return bool(_SAVED_VIEW_CONTROL_RE.search(value))


def normalize_saved_view_name(raw) -> str:
    if not isinstance(raw, str):
        raise SavedViewError("Name must be a string.")
    name = raw.strip()
    if not name:
        raise SavedViewError("Name is required.")
    if _saved_view_has_controls(name):
        raise SavedViewError("Name contains invalid characters.")
    if len(name) > PRKS_SAVED_VIEW_NAME_MAX:
        raise SavedViewError("Name is too long.")
    return name


def normalize_saved_view_search(search) -> Dict[str, str]:
    if not isinstance(search, dict):
        raise SavedViewError("Search definition is required.")
    for key in ("mode", "q", "tag", "author", "publisher"):
        if key not in search:
            raise SavedViewError("Search definition is incomplete.")
    mode_raw = search.get("mode")
    if not isinstance(mode_raw, str):
        raise SavedViewError("Invalid search mode.")
    mode = mode_raw.strip()
    if mode not in _SAVED_VIEW_MODES:
        raise SavedViewError("Invalid search mode.")

    def _field(key: str, max_len: int) -> str:
        value = search.get(key)
        if not isinstance(value, str):
            raise SavedViewError("Search fields must be strings.")
        text = value.strip()
        if _saved_view_has_controls(text):
            raise SavedViewError("Search definition contains invalid characters.")
        if len(text) > max_len:
            raise SavedViewError("Search field is too long.")
        return text

    q = _field("q", PRKS_SAVED_VIEW_Q_MAX)
    tag = _field("tag", PRKS_SAVED_VIEW_FIELD_MAX)
    author = _field("author", PRKS_SAVED_VIEW_FIELD_MAX)
    publisher = _field("publisher", PRKS_SAVED_VIEW_FIELD_MAX)
    if mode == "all":
        if not q or tag or author or publisher:
            raise SavedViewError("All-fields views require a query and no other filters.")
    elif mode == "advanced":
        if tag:
            raise SavedViewError("Advanced views cannot include a tag.")
        if not (q or author or publisher):
            raise SavedViewError("Advanced views require keywords, author, or publisher.")
    else:
        if not tag or q:
            raise SavedViewError("Tag views require a tag and no keyword query.")
    return {
        "mode": mode,
        "q": q,
        "tag": tag,
        "author": author,
        "publisher": publisher,
    }


def saved_view_api_row(row: dict) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "search": {
            "mode": row["mode"],
            "q": row["search_q"],
            "tag": row["search_tag"],
            "author": row["search_author"],
            "publisher": row["search_publisher"],
        },
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


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


# Compact browse projection. Deliberately NOT the full work summary: the browse
# routes (#/progress, #/types, #/types/:type, #/recent, Recently Added) render
# shared Work cards plus a short per-route subtitle, and nothing there needs the
# whole abstract, the bibliographic block (journal/volume/issue/pages/isbn/doi),
# or the timestamps a card never shows. Carrying the full abstract measured at
# ~59.5% of the /api/works payload while only #/progress reads it -- and only
# its first 100 characters. See AGENTS.md, "Offline browse catalogs".
_PRKS_WORK_BROWSE_COLUMNS: Tuple[str, ...] = (
    "id",
    "title",
    "status",
    "doc_type",
    "file_path",
    "source_kind",
    "source_url",
    "thumb_url",
    "thumb_page",
    "author_text",
    "year",
    "published_date",
    # Video identity, so an effective browse row can represent a coherent
    # source on its own. Without these a row carries the URL but not the id
    # that outranks it, and a pending source change could not be shown here
    # without contradicting itself.
    "provider",
    "provider_id",
)

# Length of the excerpt #/progress renders under each card.
_PRKS_ABSTRACT_EXCERPT_LEN = 100


def _prks_work_browse_select(alias: str, *, abstract_excerpt: bool = True) -> str:
    """Browse columns, with `abstract` replaced by a bounded excerpt."""
    cols = [f"{alias}.{c}" for c in _PRKS_WORK_BROWSE_COLUMNS]
    if abstract_excerpt:
        # SUBSTR is bounded server-side so the excerpt cannot carry the whole
        # abstract to a client that only renders 100 characters of it.
        cols.append(
            f"SUBSTR(COALESCE({alias}.abstract, ''), 1, {_PRKS_ABSTRACT_EXCERPT_LEN}) AS abstract_excerpt"
        )
    return ", ".join(cols)


def _prks_work_summary_select(alias: str) -> str:
    return ", ".join(f"{alias}.{c}" for c in _PRKS_WORK_SUMMARY_COLUMNS)


def _prks_work_summary_select_with_folder(alias: str) -> str:
    """Work summary columns plus folder_id (at most one folder per work in normal use)."""
    base = _prks_work_summary_select(alias)
    return f"{base}, (SELECT folder_id FROM folder_files WHERE work_id = {alias}.id LIMIT 1) AS folder_id"


def _prks_sql_role_display_name_expr(person_alias: str = "p", role_alias: str = "r") -> str:
    """Per-link credit_name when set, else canonical person name."""
    return (
        f"TRIM(COALESCE(NULLIF(TRIM({role_alias}.credit_name), ''), "
        f"TRIM(COALESCE({person_alias}.first_name,'') || ' ' || COALESCE({person_alias}.last_name,''))))"
    )


def _prks_sql_first_linked_person_for_role(work_alias: str, role_type: str, column_alias: str) -> str:
    """Scalar subquery: display name of first linked person for role_type (BibTeX order)."""
    wid = f"{work_alias}.id"
    rt = (role_type or "").replace("'", "''")
    ca = (column_alias or "name").replace('"', "")
    disp = _prks_sql_role_display_name_expr("p", "r")
    return (
        f"(SELECT {disp} "
        "FROM roles r "
        "JOIN persons p ON p.id = r.person_id "
        f"WHERE r.work_id = {wid} AND r.role_type = '{rt}' "
        "ORDER BY r.order_index ASC, r.rowid ASC "
        f"LIMIT 1) AS {ca}"
    )


def _prks_sql_linked_authors_concat(work_alias: str, column_alias: str = "linked_authors") -> str:
    """Scalar subquery: all Author display names, comma-separated, same order as roles."""
    wid = f"{work_alias}.id"
    ca = (column_alias or "linked_authors").replace('"', "")
    disp = _prks_sql_role_display_name_expr("p", "r")
    return (
        f"(SELECT GROUP_CONCAT({disp}, ', ' "
        "ORDER BY r.order_index ASC, r.rowid ASC) "
        "FROM roles r JOIN persons p ON p.id = r.person_id "
        f"WHERE r.work_id = {wid} AND r.role_type = 'Author') AS {ca}"
    )


def _prks_sql_linked_people_json(work_alias: str, column_alias: str = "linked_people") -> str:
    """Scalar subquery: the Work's people as an ordered structured list.

    The three columns above are FLATTENED -- a comma-joined name string and two
    first-name scalars -- and flattening is lossy in exactly the way a local
    relationship overlay needs: a pending removal cannot be subtracted from
    "Ann Lee, Bo Ng" without guessing where one name ends, and names legitimately
    contain commas. Nor can a pending addition know whether an Author already
    remains, which is what decides between showing a linked name and falling
    back to `author_text`.

    So the row also carries the relationship itself: person id, role and the
    display name that link resolves to, in the same order the flattened columns
    use. Small -- a few tens of bytes per link -- and it is what lets the
    overlay recompute the flattened columns EXACTLY rather than approximately,
    so the existing credit helper keeps deciding what the user sees.
    """
    wid = f"{work_alias}.id"
    ca = (column_alias or "linked_people").replace('"', "")
    disp = _prks_sql_role_display_name_expr("p", "r")
    canonical = ("TRIM(COALESCE(p.first_name,'') || ' ' || COALESCE(p.last_name,''))")
    return (
        "(SELECT json_group_array(json_object("
        "'person_id', r.person_id, "
        "'role_type', r.role_type, "
        "'order_index', r.order_index, "
        # The Person's own name and the per-link override are carried
        # SEPARATELY, not just the resolved display name. Clearing an override
        # has to reveal the canonical name, and "Mark Twain" cannot be turned
        # back into "Samuel Clemens" -- so a row that promised to render the
        # credit would have had to go and find a Person cache to do it. A
        # browse row is self-sufficient for what it renders.
        "'canonical_name', " + canonical + ", "
        "'credit_name', COALESCE(NULLIF(TRIM(r.credit_name), ''), ''), "
        "'display_name', " + disp + ")) "
        "FROM (SELECT * FROM roles r2 "
        f"WHERE r2.work_id = {wid} ORDER BY r2.order_index ASC, r2.rowid ASC) r "
        f"JOIN persons p ON p.id = r.person_id) AS {ca}"
    )


def _prks_sql_work_summary_person_extras(work_alias: str) -> str:
    """Append to work-summary SELECTs: first author/editor, the full author list
    for cards, and the structured links the relationship overlay recomputes
    those from."""
    pa = _prks_sql_first_linked_person_for_role(work_alias, "Author", "primary_author")
    pe = _prks_sql_first_linked_person_for_role(work_alias, "Editor", "primary_editor")
    la = _prks_sql_linked_authors_concat(work_alias)
    lp = _prks_sql_linked_people_json(work_alias)
    return f"{pa}, {pe}, {la}, {lp}"


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


# The source kinds a Work may be created with. `source_kind` is not free text:
# the schema, the viewer and the source aggregate all describe exactly these
# two, and anything else falls through the runtime inference below into a
# branch it was never classified as. "web" with a YouTube URL reached the video
# viewer while the creation boundary treated it as not-a-video.
NEW_WORK_SOURCE_KINDS = ("pdf", "video")


def effective_source_kind(source_kind, source_url, file_path):
    """What a Work IS, by the same rule the runtime uses.

    Mirrors `prksInferWorkSourceKind()` in frontend/js/api.js. An explicit kind
    wins; otherwise a file makes it a PDF and a URL makes it a video. Creation
    has to classify a row the same way the product later reads it, or the two
    disagree about the same row -- which is how a Work with no kind, no file
    and a YouTube URL came to be shown as a Video, offered the Video source
    editor, and then refused by `SET_WORK_SOURCE` as
    UNSUPPORTED_SOURCE_TRANSITION because its stored `source_kind` was NULL.
    """
    kind = (source_kind or "").strip().lower()
    if kind in ("video", "pdf"):
        return kind
    if (file_path or "").strip():
        return "pdf"
    if (source_url or "").strip():
        return "video"
    return kind


def _canonical_new_source(source_kind, source_url, provider, provider_id, file_path):
    """The source columns a NEW Work may be created with, or a refusal.

    Creation is the other point where a source identity comes into existence.
    Bounding it only in the synchronization parser left the whole invariant
    bypassable: a Work could be created with `provider_id` naming video A while
    `source_url` named video B -- so the viewer, which reads `provider_id`
    first, played a video the row did not claim -- or with a 3000-character
    `provider_id` that no later conflict could report back, because
    `fit_terminal_result` cannot shorten an identity. The row would be accepted
    happily and become unresolvable months later.

    So the same canonical parser decides here, for every Work the product will
    treat as a video -- not only those whose caller said so. `provider` and
    `provider_id` are DERIVED from the URL, exactly as on the synchronization
    path, and `source_kind` is PERSISTED as "video" rather than left NULL: a
    row the UI calls a video and the aggregate calls something else is the
    disagreement this whole boundary exists to remove.

    A contradictory pair is REFUSED rather than repaired. A caller passing
    video B's URL with video A's id has a bug, and silently rewriting it to B
    would hide the bug while making the next one harder to find -- the caller
    would go on believing it had asserted an identity.

    Nothing here creates a source revision: construction is not mutation, and a
    Work begins at revision 0 like every other scope.
    """
    declared = (source_kind or "").strip().lower()
    if declared and declared not in NEW_WORK_SOURCE_KINDS:
        raise ValueError(
            "%s is not a source kind; use one of %s"
            % (declared, ", ".join(NEW_WORK_SOURCE_KINDS)))
    url = (source_url or "").strip()
    supplied_provider = (provider or "").strip().lower()
    supplied_id = (provider_id or "").strip()
    kind = effective_source_kind(declared, url, file_path)

    if kind != "video":
        # These columns ARE video identity. On anything else they would be a
        # claim about a video this Work is not. A PDF's `source_url` is
        # PROVENANCE -- where the file came from -- and stays untouched even
        # when it happens to be YouTube-shaped.
        if supplied_provider or supplied_id:
            raise ValueError(
                "provider and provider_id belong to a video source; this Work is "
                "not one")
        # The EFFECTIVE kind, canonically spelled -- so a new row always states
        # what it is rather than leaving every reader to infer it. Storing
        # "PDF", or NULL for a file-backed Work, means the canonical
        # representation lives in each reader instead of in the column, and a
        # reader that forgets sees a kind matching nothing.
        #
        # `kind` is "" only when there is no source at all: no declared kind, no
        # file and no URL. That Work genuinely has no source classification, and
        # inventing one would be a claim about a file it does not have.
        return (kind or None), source_url, provider, provider_id

    canonical = work_source_sync.canonical_source({"kind": "video", "url": url})
    if canonical is None:
        raise ValueError(
            "a video Work needs a supported video URL: %s is not one"
            % (url[:120] or "(empty)"))
    if supplied_provider and supplied_provider != canonical["provider"]:
        raise ValueError(
            "provider %s contradicts the source URL, which is %s"
            % (supplied_provider, canonical["provider"]))
    if supplied_id and supplied_id != canonical["provider_id"]:
        raise ValueError(
            "provider_id does not identify the video this source URL names")
    return (canonical["source_kind"], canonical["source_url"],
            canonical["provider"], canonical["provider_id"])


def normalize_doc_type(value: Any) -> str:
    """Map user/API input to a whitelisted BibLaTeX entry type; unknown → misc."""
    return work_metadata_sync.normalize_doc_type(value)


def prks_thumb_cache_safe_wid(work_id: str) -> str:
    """Sanitize work id for thumbnail filenames (must match server thumbnail handler)."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(work_id))


# Bump when thumbnail encode format changes (invalidates on-disk cache by filename).
PRKS_THUMB_CACHE_REV = 2


def prks_thumb_cache_stem(work_id: str, page: int) -> str:
    """Cache filename stem for one PDF work page thumbnail (no extension)."""
    safe = prks_thumb_cache_safe_wid(work_id)
    p = int(page) if page is not None else 1
    if p < 1:
        p = 1
    return f"{safe}_p{p}_v{PRKS_THUMB_CACHE_REV}"


def prks_person_image_cache_safe_id(person_id: str) -> str:
    """Sanitize person id for on-disk profile image cache filenames."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(person_id))


# Bump when portrait encode format changes (invalidates on-disk cache by filename).
PRKS_PERSON_IMAGE_CACHE_REV = 1


def prks_person_image_url_hash(image_url: str) -> str:
    """Stable short hash of normalized image_url for cache filenames."""
    norm = (image_url or "").strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]


def prks_person_image_cache_path(person_id: str, image_url: str, people_dir: str) -> str:
    """On-disk path for one person's compressed portrait (WebP)."""
    d = people_dir
    safe = prks_person_image_cache_safe_id(person_id)
    h = prks_person_image_url_hash(image_url)
    return os.path.join(
        d, f"{safe}_{h}_v{PRKS_PERSON_IMAGE_CACHE_REV}.webp"
    )


def prks_person_image_legacy_bin_path(person_id: str, people_dir: str) -> str:
    """Pre-compression cache file (raw bytes); migrated lazily to WebP."""
    d = people_dir
    safe = prks_person_image_cache_safe_id(person_id)
    return os.path.join(d, safe + ".bin")


def prks_delete_person_image_cache(person_id: str, people_dir: str) -> None:
    """Remove all cached portrait files for one person (best-effort)."""
    d = people_dir
    safe = prks_person_image_cache_safe_id(person_id)
    if not os.path.isdir(d):
        return
    legacy_bin = safe + ".bin"
    prefix = safe + "_"
    try:
        names = os.listdir(d)
    except OSError:
        return
    for fname in names:
        if fname != legacy_bin and not fname.startswith(prefix):
            continue
        try:
            os.remove(os.path.join(d, fname))
        except OSError:
            pass


_PRKS_UNCATEGORIZED_FOLDER_TITLE = "Uncategorized"


_PRKS_THUMB_CACHE_FINAL_RE = re.compile(
    r"^(.+)_p(\d+)_v(\d+)\.(webp|png|jpg|jpeg)$", re.IGNORECASE
)
_PRKS_THUMB_CACHE_TMP_RE = re.compile(
    r"^(.+)_p(\d+)_v(\d+)\.(webp|png|jpg|jpeg)\.tmp$", re.IGNORECASE
)
# Pre-rev-2 filenames (no _vN suffix); pruned when not in allowed v2 stems.
_PRKS_THUMB_CACHE_LEGACY_FINAL_RE = re.compile(
    r"^(.+)_p(\d+)\.(webp|png)$", re.IGNORECASE
)
_PRKS_THUMB_CACHE_LEGACY_TMP_RE = re.compile(
    r"^(.+)_p(\d+)\.(webp|png)\.tmp$", re.IGNORECASE
)


def prks_delete_pdf_thumbnails_for_work_id(work_id: str, thumbs_dir: str) -> tuple[str, ...]:
    """Remove cached PDF thumbnails for one work (best-effort). Returns paths that could not be removed."""
    safe = prks_thumb_cache_safe_wid(work_id)
    td = thumbs_dir
    if not os.path.isdir(td):
        return ()
    pat_final = re.compile(
        r"^" + re.escape(safe) + r"_p\d+(_v\d+)?\.(webp|png|jpg|jpeg)$", re.IGNORECASE
    )
    pat_tmp = re.compile(
        r"^" + re.escape(safe) + r"_p\d+(_v\d+)?\.(webp|png|jpg|jpeg)\.tmp$",
        re.IGNORECASE,
    )
    try:
        names = os.listdir(td)
    except OSError:
        return (td,)
    failed: list[str] = []
    for fname in names:
        if not pat_final.match(fname) and not pat_tmp.match(fname):
            continue
        path = os.path.join(td, fname)
        try:
            os.remove(path)
        except OSError:
            failed.append(path)
    return tuple(failed)


def prune_orphan_pdf_thumbnails(db: "PRKSDatabase") -> int:
    """Delete thumbnail files not referenced by any PDF work's thumb_page. Returns removal count."""
    rows = db.execute_query(
        "SELECT id, thumb_page FROM works WHERE file_path LIKE '/api/pdfs/%'"
    )
    allowed: set[str] = set()
    for row in rows or []:
        wid = row.get("id")
        if not wid:
            continue
        tp = row.get("thumb_page")
        try:
            page = int(tp) if tp is not None and str(tp).strip() != "" else 1
        except (TypeError, ValueError):
            page = 1
        if page < 1:
            page = 1
        allowed.add(prks_thumb_cache_stem(str(wid), page))
    td = db.storage.thumbs_dir
    if not os.path.isdir(td):
        return 0
    try:
        names = os.listdir(td)
    except OSError:
        return 0
    removed = 0
    for fname in names:
        stem: Optional[str] = None
        m = _PRKS_THUMB_CACHE_FINAL_RE.match(fname)
        if m:
            stem = f"{m.group(1)}_p{m.group(2)}_v{m.group(3)}"
        else:
            m = _PRKS_THUMB_CACHE_TMP_RE.match(fname)
            if m:
                stem = f"{m.group(1)}_p{m.group(2)}_v{m.group(3)}"
            else:
                m = _PRKS_THUMB_CACHE_LEGACY_FINAL_RE.match(fname)
                if m:
                    stem = f"{m.group(1)}_p{m.group(2)}"
                else:
                    m = _PRKS_THUMB_CACHE_LEGACY_TMP_RE.match(fname)
                    if m:
                        stem = f"{m.group(1)}_p{m.group(2)}"
        if stem is None or stem in allowed:
            continue
        try:
            os.remove(os.path.join(td, fname))
            removed += 1
        except OSError:
            pass
    return removed


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
    except (OSError, ValueError):
        return None
    if candidate != base and not candidate.startswith(base + os.sep):
        return None
    return candidate


def managed_pdf_filename(file_path: str) -> Optional[str]:
    """Return the filename only for an exact literal /api/pdfs/<filename> ownership path."""
    prefix = "/api/pdfs/"
    if not file_path.startswith(prefix):
        return None
    remainder = file_path[len(prefix):]
    if not remainder:
        return None
    if "/" in remainder or "\\" in remainder:
        return None
    if remainder in (".", ".."):
        return None
    if "\x00" in remainder:
        return None
    if unquote(remainder) != remainder:
        return None
    if prefix + remainder != file_path:
        return None
    return remainder


def referenced_managed_pdf_filename(file_path: str) -> Optional[str]:
    """Managed filename a stored file_path can resolve to, matching current serving identity."""
    fp = str(file_path or "").strip()
    if not fp.startswith("/api/pdfs/"):
        return None
    segment = fp.split("/")[-1]
    name = os.path.basename(unquote(segment))
    if not name or name in (".", ".."):
        return None
    return name


def safe_processing_path_under_dir(processing_dir: str, relative_path: str) -> Optional[str]:
    """Resolve a relative path under processing_dir; reject traversal and empty segments."""
    if not relative_path or not str(relative_path).strip():
        return None
    rel = str(relative_path).replace("\\", "/").strip().lstrip("/")
    if not rel:
        return None
    base = os.path.realpath(processing_dir)
    try:
        candidate = os.path.realpath(os.path.join(base, rel))
    except OSError:
        return None
    if candidate != base and not candidate.startswith(base + os.sep):
        return None
    return candidate


def prune_empty_processing_parent_dirs(processing_root: str, removed_inbox_file_abs: str) -> None:
    """Remove empty directories from parent of removed file up to processing_root (root kept)."""
    try:
        root = Path(processing_root).resolve()
        cur = Path(removed_inbox_file_abs).resolve().parent
    except OSError:
        return
    try:
        cur.relative_to(root)
    except ValueError:
        return
    while cur != root:
        try:
            if not cur.is_dir():
                break
            if any(cur.iterdir()):
                break
            parent = cur.parent
            cur.rmdir()
            cur = parent
        except OSError:
            break


def _processing_safe_dest_name(filename: str) -> str:
    safe = "".join(c for c in str(filename or "") if c.isalnum() or c in ".-_")
    safe = safe.strip("._")
    if not safe:
        safe = "file"
    if not safe.lower().endswith(".pdf"):
        safe += ".pdf"
    return safe


# Most filesystems cap a single name component at 255 bytes, and a name that
# overruns it fails at open() rather than at validation.
_MANAGED_PDF_NAME_MAX_BYTES = 255


def mint_managed_pdf_filename(original_name: str) -> str:
    """Mint a fresh managed PDF basename: ``<unix-seconds>_<8 hex>_<name>.pdf``.

    The random component is load-bearing, not decoration. A timestamp alone
    collides for two uploads of the same filename within one second, and the
    second write then replaced the first Work's bytes while both rows still
    pointed at that one path.

    ``original_name`` is advisory. It is sanitized for readability and never
    trusted for placement: callers still resolve the result through
    ``safe_pdf_path_under_dir()`` before touching the filesystem.
    """
    safe = _processing_safe_dest_name(os.path.basename(str(original_name or "")))
    prefix = f"{int(datetime.now().timestamp())}_{uuid.uuid4().hex[:8]}_"
    stem = safe[: -len(".pdf")] if safe.lower().endswith(".pdf") else safe
    budget = _MANAGED_PDF_NAME_MAX_BYTES - len(prefix.encode("utf-8")) - len(".pdf")
    if budget > 0:
        # Truncating bytes can split a multi-byte character; drop the partial tail.
        stem = stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore")
    return f"{prefix}{stem.strip('._') or 'file'}.pdf"


def _decode_linked_people(rows: Optional[List[dict]]) -> None:
    for row in rows or ():
        raw = row.get("linked_people")
        if isinstance(raw, str):
            try:
                row["linked_people"] = json.loads(raw)
            except (ValueError, TypeError):
                row["linked_people"] = []
        elif raw is None and "linked_people" in row:
            row["linked_people"] = []


def finish_work_summary_rows(rows: Optional[List[dict]], pdfs_dir: str) -> None:
    """Complete a work-summary row: on-disk PDF size, and structured links.

    `linked_people` arrives from SQLite as JSON TEXT. Decoded here rather than
    left for the client: a nested JSON string would make every consumer parse
    it again per row per render, and a row validator would have to accept a
    string where the shape is actually a list.
    """
    _decode_linked_people(rows)
    if not rows:
        return
    t0 = clock_ns()
    examined = 0
    stated = 0
    try:
        for row in rows:
            if not row or not isinstance(row, dict):
                continue
            examined += 1
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
                stated += 1
            except OSError:
                row["file_size_bytes"] = None
    finally:
        try:
            record_span("pdf_file_stats", clock_ns() - t0)
            if examined:
                record_counter("pdf_file_stat_rows", examined)
            if stated:
                record_counter("pdf_file_stat_files", stated)
        except Exception:
            pass


@dataclass(frozen=True)
class DeletedWorkRecord:
    work_id: str
    file_path: str
    managed_pdf_still_referenced: bool


def _person_wire(value):
    """A profile value in the SAME representation the sync path carries.

    A caller may hand PATCH a None where the durable queue always carries a
    string. Converting here means the two paths run one validator over one
    spelling and reach the same revision decision, rather than being two
    implementations that agree until they do not.
    """
    return "" if value is None else str(value)


class PRKSDatabase:
    def __init__(
        self,
        db_path: Optional[str] = None,
        schema_path: str = "backend/db_schema.sql",
        *,
        storage: Optional[StorageConfig] = None,
    ):
        if storage is not None and db_path is not None:
            if db_path != storage.db_path:
                raise ValueError("db_path conflicts with storage.db_path")
            self.storage = storage
            self.db_path = db_path
        elif storage is not None:
            self.storage = storage
            self.db_path = storage.db_path
        elif db_path is not None:
            self.storage = StorageConfig.from_env()
            self.db_path = db_path
        else:
            self.storage = StorageConfig.from_env()
            self.db_path = paths.default_prks_db_path_for_mode(
                self.storage.mode == "testing"
            )
        self.schema_path = schema_path
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)) or ".", exist_ok=True)
        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    @contextmanager
    def connection(self):
        conn = self.get_connection()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def init_db(self):
        """Create or upgrade the database through the ordered migration system."""
        conn = self.get_connection()
        try:
            ensure_database_schema(conn, self.schema_path)
        finally:
            conn.close()

    def generate_id(self, prefix: str) -> str:
        """A collision-resistant persistent id: `{prefix}-` plus 32 UUID hex.

        Historical rows used eight hex characters. New ids never copy that
        scheme: offline clients mint the same strings SQLite will store.
        """
        return generate_entity_id(prefix)

    def execute_query(self, query: str, params: tuple = ()) -> List[dict]:
        t0 = clock_ns()
        write = classify_sql_write(query)
        try:
            with self.connection() as conn:
                cursor = conn.execute(query, params)
                q0 = query.strip().upper()
                if q0.startswith(("SELECT", "PRAGMA", "WITH")):
                    return [dict(row) for row in cursor.fetchall()]
                conn.commit()
                return []
        finally:
            try:
                record_db_call(clock_ns() - t0, write=write)
            except Exception:
                pass

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

    def get_saved_views(self) -> List[dict]:
        rows = self.execute_query(
            "SELECT * FROM saved_views ORDER BY LOWER(name) ASC, id ASC"
        )
        return [saved_view_api_row(r) for r in rows]

    def get_saved_view(self, view_id: str) -> Optional[dict]:
        vid = (view_id or "").strip()
        if not vid:
            return None
        rows = self.execute_query("SELECT * FROM saved_views WHERE id = ?", (vid,))
        if not rows:
            return None
        return saved_view_api_row(rows[0])

    def create_saved_view(self, name, search) -> dict:
        n = normalize_saved_view_name(name)
        fields = normalize_saved_view_search(search)
        vid = self.generate_id("SV")
        with self.connection() as conn:
            try:
                count_row = conn.execute("SELECT COUNT(*) FROM saved_views").fetchone()
                count = int(count_row[0] if count_row and count_row[0] is not None else 0)
                if count >= PRKS_SAVED_VIEW_MAX:
                    raise SavedViewError(
                        "The library already has the maximum number of Saved Views.",
                        409,
                    )
                clash = conn.execute(
                    "SELECT id FROM saved_views WHERE name = ? COLLATE NOCASE LIMIT 1",
                    (n,),
                ).fetchone()
                if clash:
                    raise SavedViewError(
                        "A Saved View with that name already exists.",
                        409,
                    )
                conn.execute(
                    """
                    INSERT INTO saved_views (
                        id, name, mode, search_q, search_tag, search_author, search_publisher
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        vid,
                        n,
                        fields["mode"],
                        fields["q"],
                        fields["tag"],
                        fields["author"],
                        fields["publisher"],
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM saved_views WHERE id = ?", (vid,)
                ).fetchone()
            except sqlite3.IntegrityError:
                raise SavedViewError(
                    "A Saved View with that name already exists.",
                    409,
                )
        return saved_view_api_row(dict(row))

    def update_saved_view(self, view_id: str, *, name=None, search=None) -> dict:
        vid = (view_id or "").strip()
        if not vid:
            raise SavedViewError("Saved View not found.", 404)
        if name is None and search is None:
            raise SavedViewError("Nothing to update.")
        new_name = normalize_saved_view_name(name) if name is not None else None
        new_search = normalize_saved_view_search(search) if search is not None else None
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM saved_views WHERE id = ?", (vid,)
            ).fetchone()
            if not row:
                raise SavedViewError("Saved View not found.", 404)
            if new_name is not None:
                clash = conn.execute(
                    """
                    SELECT id FROM saved_views
                    WHERE name = ? COLLATE NOCASE AND id != ?
                    LIMIT 1
                    """,
                    (new_name, vid),
                ).fetchone()
                if clash:
                    raise SavedViewError(
                        "A Saved View with that name already exists.",
                        409,
                    )
            try:
                if new_name is not None and new_search is not None:
                    conn.execute(
                        """
                        UPDATE saved_views
                        SET name = ?, mode = ?, search_q = ?, search_tag = ?,
                            search_author = ?, search_publisher = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (
                            new_name,
                            new_search["mode"],
                            new_search["q"],
                            new_search["tag"],
                            new_search["author"],
                            new_search["publisher"],
                            vid,
                        ),
                    )
                elif new_name is not None:
                    conn.execute(
                        """
                        UPDATE saved_views
                        SET name = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (new_name, vid),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE saved_views
                        SET mode = ?, search_q = ?, search_tag = ?,
                            search_author = ?, search_publisher = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (
                            new_search["mode"],
                            new_search["q"],
                            new_search["tag"],
                            new_search["author"],
                            new_search["publisher"],
                            vid,
                        ),
                    )
                row = conn.execute(
                    "SELECT * FROM saved_views WHERE id = ?", (vid,)
                ).fetchone()
            except sqlite3.IntegrityError:
                raise SavedViewError(
                    "A Saved View with that name already exists.",
                    409,
                )
        return saved_view_api_row(dict(row))

    def delete_saved_view(self, view_id: str) -> None:
        vid = (view_id or "").strip()
        if not vid:
            raise SavedViewError("Saved View not found.", 404)
        with self.connection() as conn:
            cur = conn.execute("DELETE FROM saved_views WHERE id = ?", (vid,))
            if cur.rowcount < 1:
                raise SavedViewError("Saved View not found.", 404)

    def _get_bibtex_export_profile(self) -> Dict[str, bool]:
        m = self.get_app_settings_map()
        return _prks_parse_bibtex_export_fields_json(m.get("bibtex_export_fields", ""))

    # --- Files for processing (staging inbox) ---
    _PROCESSING_STATUSES_ORDER = {"pending": 0, "missing": 1, "error": 2, "imported": 3}
    _PROCESSING_ROLE_TYPES = {
        "Author",
        "Editor",
        "Reviewer",
        "Translator",
        "Introduction",
        "Foreword",
        "Afterword",
    }

    def _processing_role_public(self, row: dict) -> dict:
        first = (row.get("first_name") or "").strip()
        last = (row.get("last_name") or "").strip()
        name = " ".join([x for x in (first, last) if x]).strip() or row.get("person_id") or "Unknown"
        return {
            "person_id": row.get("person_id"),
            "person_name": name,
            "role_type": row.get("role_type"),
            "order_index": row.get("order_index"),
        }

    def _processing_tag_public(self, row: dict) -> dict:
        return {
            "id": row.get("id"),
            "name": row.get("name"),
            "color": row.get("color"),
            "created_at": row.get("created_at"),
        }

    def _get_processing_roles(self, processing_file_id: str) -> List[dict]:
        rows = self.execute_query(
            """
            SELECT p.id AS person_id, p.first_name, p.last_name, r.role_type, r.order_index
            FROM processing_file_roles r
            JOIN persons p ON p.id = r.person_id
            WHERE r.processing_file_id = ?
            ORDER BY r.order_index ASC, r.rowid ASC
            """,
            (processing_file_id,),
        )
        return [self._processing_role_public(row) for row in rows]

    def _get_processing_tags(self, processing_file_id: str) -> List[dict]:
        rows = self.execute_query(
            """
            SELECT t.id, t.name, t.color, t.created_at
            FROM processing_file_tags pft
            JOIN tags t ON t.id = pft.tag_id
            WHERE pft.processing_file_id = ?
            ORDER BY LOWER(t.name) ASC, t.id ASC
            """,
            (processing_file_id,),
        )
        return [self._processing_tag_public(row) for row in rows]

    def _processing_roles_by_file_id(self, *, include_imported: bool) -> Dict[str, List[dict]]:
        sql = """
            SELECT
                r.processing_file_id,
                p.id AS person_id,
                p.first_name,
                p.last_name,
                r.role_type,
                r.order_index
            FROM processing_file_roles r
            JOIN processing_files pf ON pf.id = r.processing_file_id
            JOIN persons p ON p.id = r.person_id
        """
        params: tuple[Any, ...] = ()
        if not include_imported:
            sql += " WHERE pf.status != ?"
            params = ("imported",)
        sql += " ORDER BY r.processing_file_id, r.order_index ASC, r.rowid ASC"
        out: Dict[str, List[dict]] = defaultdict(list)
        for row in self.execute_query(sql, params):
            pfid = str(row.get("processing_file_id") or "")
            if pfid:
                out[pfid].append(self._processing_role_public(row))
        return out

    def _processing_tags_by_file_id(self, *, include_imported: bool) -> Dict[str, List[dict]]:
        sql = """
            SELECT
                pft.processing_file_id,
                t.id,
                t.name,
                t.color,
                t.created_at
            FROM processing_file_tags pft
            JOIN processing_files pf ON pf.id = pft.processing_file_id
            JOIN tags t ON t.id = pft.tag_id
        """
        params: tuple[Any, ...] = ()
        if not include_imported:
            sql += " WHERE pf.status != ?"
            params = ("imported",)
        sql += " ORDER BY pft.processing_file_id, LOWER(t.name) ASC, t.id ASC"
        out: Dict[str, List[dict]] = defaultdict(list)
        for row in self.execute_query(sql, params):
            pfid = str(row.get("processing_file_id") or "")
            if pfid:
                out[pfid].append(self._processing_tag_public(row))
        return out

    def _set_processing_tags(self, processing_file_id: str, tags: List[dict]) -> None:
        if not isinstance(tags, list):
            raise ValueError("tags must be an array.")
        tag_ids: List[str] = []
        seen = set()
        for item in tags:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("id") or item.get("tag_id") or "").strip()
            if not tid or tid in seen:
                continue
            exists = self.execute_query("SELECT 1 FROM tags WHERE id = ?", (tid,))
            if not exists:
                raise ValueError(f"Unknown tag id: {tid}")
            seen.add(tid)
            tag_ids.append(tid)
        with self.connection() as conn:
            conn.execute("DELETE FROM processing_file_tags WHERE processing_file_id = ?", (processing_file_id,))
            for tid in tag_ids:
                conn.execute(
                    """
                    INSERT INTO processing_file_tags (processing_file_id, tag_id)
                    VALUES (?, ?)
                    ON CONFLICT DO NOTHING
                    """,
                    (processing_file_id, tid),
                )
            conn.commit()

    def _set_processing_roles(self, processing_file_id: str, roles: List[dict]) -> None:
        if not isinstance(roles, list):
            raise ValueError("roles must be an array.")
        normalized: List[tuple[str, str, int]] = []
        seen = set()
        for idx, role in enumerate(roles):
            if not isinstance(role, dict):
                continue
            person_id = str(role.get("person_id") or "").strip()
            role_type = str(role.get("role_type") or "").strip()
            if not person_id or not role_type:
                continue
            if role_type not in self._PROCESSING_ROLE_TYPES:
                raise ValueError(f"Unsupported role_type: {role_type}")
            exists = self.execute_query("SELECT 1 FROM persons WHERE id = ?", (person_id,))
            if not exists:
                raise ValueError(f"Unknown person id: {person_id}")
            key = (person_id, role_type)
            if key in seen:
                continue
            seen.add(key)
            normalized.append((person_id, role_type, idx))
        with self.connection() as conn:
            conn.execute("DELETE FROM processing_file_roles WHERE processing_file_id = ?", (processing_file_id,))
            for person_id, role_type, order_index in normalized:
                conn.execute(
                    """
                    INSERT INTO processing_file_roles (processing_file_id, person_id, role_type, order_index)
                    VALUES (?, ?, ?, ?)
                    """,
                    (processing_file_id, person_id, role_type, int(order_index)),
                )
            conn.commit()

    def _processing_row_to_public(
        self,
        row: dict,
        *,
        roles: Optional[List[dict]] = None,
        tags: Optional[List[dict]] = None,
    ) -> dict:
        rel_path = (row.get("rel_path") or "").replace("\\", "/")
        folder_rel = os.path.dirname(rel_path).replace("\\", "/")
        if folder_rel in ("", "."):
            folder_rel = "/"
        abs_path = (row.get("abs_path") or "").strip()
        exists = os.path.isfile(abs_path) if abs_path else False
        return {
            "id": row.get("id"),
            "rel_path": rel_path,
            "filename": row.get("filename") or os.path.basename(rel_path),
            "folder": folder_rel,
            "status": row.get("status") or "pending",
            "last_error": row.get("last_error"),
            "imported_work_id": row.get("imported_work_id"),
            "imported_at": row.get("imported_at"),
            "discovered_at": row.get("discovered_at"),
            "updated_at": row.get("updated_at"),
            "exists": bool(exists),
            "title": row.get("title") or "",
            "status_draft": row.get("status_draft") or "Not Started",
            "published_date": row.get("published_date") or "",
            "abstract": row.get("abstract") or "",
            "source_url": row.get("source_url") or "",
            "author_text": row.get("author_text") or "",
            "year": row.get("year") or "",
            "publisher": row.get("publisher") or "",
            "location": row.get("location") or "",
            "edition": row.get("edition") or "",
            "journal": row.get("journal") or "",
            "volume": row.get("volume") or "",
            "issue": row.get("issue") or "",
            "pages": row.get("pages") or "",
            "isbn": row.get("isbn") or "",
            "doi": row.get("doi") or "",
            "doc_type": row.get("doc_type") or "article",
            "private_notes": row.get("private_notes") or "",
            "thumb_page": row.get("thumb_page"),
            "target_folder_id": row.get("target_folder_id") or "",
            "roles": list(roles) if roles is not None else [],
            "tags": list(tags) if tags is not None else [],
        }

    def _discover_processing_pdfs(self) -> List[tuple[str, str, str]]:
        root = self.storage.processing_dir
        os.makedirs(root, exist_ok=True)
        root_real = os.path.realpath(root)
        discovered: List[tuple[str, str, str]] = []
        for dirpath, _dirnames, filenames in os.walk(root_real):
            for filename in sorted(filenames):
                if not str(filename).lower().endswith(".pdf"):
                    continue
                abs_path = os.path.realpath(os.path.join(dirpath, filename))
                if abs_path != root_real and not abs_path.startswith(root_real + os.sep):
                    continue
                rel_path = os.path.relpath(abs_path, root_real).replace(os.sep, "/")
                discovered.append((rel_path, abs_path, filename))
        return discovered

    def _reconcile_processing_files_from_disk(self) -> None:
        discovered = self._discover_processing_pdfs()
        discovered_rel_paths = {rel_path for rel_path, _abs_path, _filename in discovered}
        with self.connection() as conn:
            existing_rows = conn.execute(
                "SELECT id, rel_path, abs_path, filename, status FROM processing_files"
            ).fetchall()
            existing_by_rel: Dict[str, sqlite3.Row] = {}
            for row in existing_rows:
                rel = str(row["rel_path"] or "").replace("\\", "/")
                if rel:
                    existing_by_rel[rel] = row
            inserts: List[tuple[str, str, str, str, str]] = []
            updates: List[tuple[str, str, str, str]] = []
            for rel_path, abs_path, filename in discovered:
                existing = existing_by_rel.get(rel_path)
                next_status = "pending"
                if existing is not None and str(existing["status"] or "") == "imported":
                    next_status = "imported"
                if existing is None:
                    inserts.append(
                        (self.generate_id("PF"), rel_path, abs_path, filename, next_status)
                    )
                else:
                    updates.append((abs_path, filename, next_status, rel_path))
            if inserts:
                conn.executemany(
                    """
                    INSERT INTO processing_files (
                        id, rel_path, abs_path, filename, status, last_error
                    )
                    VALUES (?, ?, ?, ?, ?, NULL)
                    """,
                    inserts,
                )
            if updates:
                conn.executemany(
                    """
                    UPDATE processing_files
                    SET abs_path = ?,
                        filename = ?,
                        status = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE rel_path = ?
                    """,
                    updates,
                )
            removed = [
                (rel,)
                for rel in existing_by_rel
                if rel not in discovered_rel_paths
            ]
            if removed:
                conn.executemany(
                    """
                    DELETE FROM processing_files
                    WHERE rel_path = ? AND status != 'imported'
                    """,
                    removed,
                )
            conn.commit()

    def scan_processing_files(self) -> List[dict]:
        with perf_span("processing_scan"):
            self._reconcile_processing_files_from_disk()
        return self.get_processing_files(include_imported=False)

    def get_processing_files(self, include_imported: bool = False) -> List[dict]:
        sql = "SELECT * FROM processing_files"
        params: tuple[Any, ...] = ()
        if not include_imported:
            sql += " WHERE status != ?"
            params = ("imported",)
        rows = list(self.execute_query(sql, params))
        rows.sort(
            key=lambda row: (
                self._PROCESSING_STATUSES_ORDER.get(str(row.get("status") or "pending"), 99),
                (row.get("rel_path") or "").lower(),
            )
        )
        roles_by = self._processing_roles_by_file_id(include_imported=include_imported)
        tags_by = self._processing_tags_by_file_id(include_imported=include_imported)
        out: List[dict] = []
        for row in rows:
            pfid = str(row.get("id") or "")
            out.append(
                self._processing_row_to_public(
                    row,
                    roles=roles_by.get(pfid, []),
                    tags=tags_by.get(pfid, []),
                )
            )
        return out

    def get_processing_file(self, processing_file_id: str) -> Optional[dict]:
        rows = self.execute_query("SELECT * FROM processing_files WHERE id = ?", (processing_file_id,))
        if not rows:
            return None
        pfid = str(rows[0].get("id") or processing_file_id)
        return self._processing_row_to_public(
            rows[0],
            roles=self._get_processing_roles(pfid),
            tags=self._get_processing_tags(pfid),
        )

    def get_processing_file_pdf_path(self, processing_file_id: str) -> Optional[str]:
        rows = self.execute_query(
            "SELECT rel_path, abs_path, status FROM processing_files WHERE id = ?",
            (processing_file_id,),
        )
        if not rows:
            return None
        row = rows[0]
        rel_path = (row.get("rel_path") or "").strip()
        if not rel_path.lower().endswith(".pdf"):
            return None
        processing_root = self.storage.processing_dir
        abs_path = safe_processing_path_under_dir(processing_root, rel_path)
        if not abs_path or not os.path.isfile(abs_path):
            return None
        return abs_path

    def update_processing_file(self, processing_file_id: str, fields: Dict[str, Any]) -> dict:
        current = self.execute_query("SELECT id FROM processing_files WHERE id = ?", (processing_file_id,))
        if not current:
            raise ValueError("Processing file not found.")
        if not isinstance(fields, dict):
            raise ValueError("JSON object body required.")
        allowed = {
            "title",
            "status_draft",
            "published_date",
            "abstract",
            "source_url",
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
            "private_notes",
            "thumb_page",
            "target_folder_id",
        }
        updates = dict(fields)
        incoming_roles = updates.pop("roles", None)
        incoming_tags = updates.pop("tags", None)
        if "status" in updates and "status_draft" not in updates:
            updates["status_draft"] = updates.pop("status")
        updates = {k: v for k, v in updates.items() if k in allowed}
        if "doc_type" in updates:
            updates["doc_type"] = normalize_doc_type(updates.get("doc_type"))
        if "status_draft" in updates:
            status_value = str(updates.get("status_draft") or "").strip()
            if status_value not in ("Planned", "In Progress", "Completed", "Paused", "Not Started"):
                raise ValueError("Invalid status_draft value.")
            updates["status_draft"] = status_value
        if "thumb_page" in updates:
            raw = updates.get("thumb_page")
            if raw is None or str(raw).strip() == "":
                updates["thumb_page"] = None
            else:
                try:
                    parsed = int(raw)
                except (TypeError, ValueError):
                    parsed = None
                updates["thumb_page"] = parsed if parsed and parsed >= 1 else None
        if "target_folder_id" in updates:
            tf_raw = updates.get("target_folder_id")
            if tf_raw is None or str(tf_raw).strip() == "":
                updates["target_folder_id"] = None
            else:
                tid = str(tf_raw).strip()
                if not self.get_folder(tid):
                    raise ValueError("Unknown folder.")
                updates["target_folder_id"] = tid
        for k in list(updates.keys()):
            if k in ("thumb_page", "target_folder_id"):
                continue
            if updates[k] is None:
                updates[k] = None
            else:
                updates[k] = str(updates[k]).strip()
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [processing_file_id]
            self.execute_query(
                f"""
                UPDATE processing_files
                SET {set_clause},
                    last_error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                tuple(values),
            )
        if incoming_roles is not None:
            self._set_processing_roles(processing_file_id, incoming_roles)
        if incoming_tags is not None:
            self._set_processing_tags(processing_file_id, incoming_tags)
        out = self.get_processing_file(processing_file_id)
        if not out:
            raise ValueError("Processing file not found.")
        return out

    def import_processing_file(self, processing_file_id: str) -> Dict[str, Any]:
        rows = self.execute_query("SELECT * FROM processing_files WHERE id = ?", (processing_file_id,))
        if not rows:
            raise ValueError("Processing file not found.")
        row = rows[0]
        if row.get("status") == "imported" and row.get("imported_work_id"):
            return {"processing_file_id": processing_file_id, "work_id": row.get("imported_work_id")}

        fid = str(row.get("target_folder_id") or "").strip() or None
        if fid and not self.get_folder(fid):
            raise ValueError("Unknown folder.")

        processing_root = self.storage.processing_dir
        os.makedirs(processing_root, exist_ok=True)
        source_abs = safe_processing_path_under_dir(processing_root, row.get("rel_path") or "")
        if not source_abs or not os.path.isfile(source_abs):
            msg = "Inbox file no longer present."
            self.execute_query("DELETE FROM processing_files WHERE id = ?", (processing_file_id,))
            raise ValueError(msg)

        if not str(source_abs).lower().endswith(".pdf"):
            msg = "Only PDF files can be imported from Files for Processing."
            self.execute_query(
                """
                UPDATE processing_files
                SET status = 'error', last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (msg, processing_file_id),
            )
            raise ValueError(msg)
        try:
            with open(source_abs, "rb") as fp:
                header = fp.read(5)
        except OSError as e:
            msg = f"Could not read source PDF: {e}"
            self.execute_query(
                """
                UPDATE processing_files
                SET status = 'error', last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (msg, processing_file_id),
            )
            raise ValueError(msg)
        if not header.startswith(b"%PDF-"):
            msg = "File does not look like a valid PDF (missing %PDF header)."
            self.execute_query(
                """
                UPDATE processing_files
                SET status = 'error', last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (msg, processing_file_id),
            )
            raise ValueError(msg)

        local_filename = mint_managed_pdf_filename(
            row.get("filename") or os.path.basename(source_abs)
        )
        pdfs_dir = self.storage.pdfs_dir
        os.makedirs(pdfs_dir, exist_ok=True)
        destination_abs = safe_pdf_path_under_dir(pdfs_dir, local_filename)
        if not destination_abs:
            msg = "Could not allocate safe destination path for PDF import."
            self.execute_query(
                """
                UPDATE processing_files
                SET status = 'error', last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (msg, processing_file_id),
            )
            raise ValueError(msg)

        # Copy first, remove inbox only after DB success. Moving before add_work could leave
        # inbox empty while no work row exists; retry then deletes the processing_files row.
        try:
            shutil.copy2(source_abs, destination_abs)
        except Exception as e:
            msg = f"Could not copy PDF into managed storage: {e}"
            self.execute_query(
                """
                UPDATE processing_files
                SET status = 'error', last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (msg, processing_file_id),
            )
            raise ValueError(msg)
        try:
            changed, reason = maybe_linearize_pdf_in_place(destination_abs, context="processing-import")
            LOGGER.info(
                "pdf_linearize_result context=processing-import changed=%s reason=%s",
                "true" if changed else "false",
                safe_log_label(reason),
            )
        except Exception as e:
            LOGGER.warning(
                "pdf_linearize_error context=processing-import error_type=%s",
                safe_error_type(e),
            )

        title = (row.get("title") or "").strip() or os.path.splitext(row.get("filename") or "Untitled")[0]
        status_draft = (row.get("status_draft") or "Not Started").strip() or "Not Started"
        work_id: Optional[str] = None
        try:
            work_id = self.add_work(
                title=title,
                status=status_draft,
                abstract=row.get("abstract") or "",
                published_date=row.get("published_date") or "",
                file_path=f"/api/pdfs/{local_filename}",
                author_text=row.get("author_text") or "",
                year=row.get("year") or "",
                publisher=row.get("publisher") or "",
                location=row.get("location") or "",
                edition=row.get("edition") or "",
                journal=row.get("journal") or "",
                volume=row.get("volume") or "",
                issue=row.get("issue") or "",
                pages=row.get("pages") or "",
                isbn=row.get("isbn") or "",
                doi=row.get("doi") or "",
                doc_type=row.get("doc_type") or "article",
                source_kind="pdf",
                source_url=row.get("source_url") or "",
                thumb_page=row.get("thumb_page"),
                private_notes=row.get("private_notes") or "",
            )
            for role in self._get_processing_roles(processing_file_id):
                person_id = str(role.get("person_id") or "").strip()
                role_type = str(role.get("role_type") or "").strip()
                if not person_id or not role_type:
                    continue
                if self.has_work_role(person_id, work_id, role_type):
                    continue
                try:
                    order_index = int(role.get("order_index") or 0)
                except (TypeError, ValueError):
                    order_index = 0
                # CONSTRUCTION: this Work was created moments ago in this same
                # import, and the importer is the authority on author order.
                try:
                    with self.connection() as conn:
                        conn.execute("BEGIN IMMEDIATE")
                        work_role_sync.insert_initial_role(
                            conn, work_id, person_id, role_type,
                            order_index=order_index)
                except ValueError:
                    continue
            for tag in self._get_processing_tags(processing_file_id):
                tag_id = str(tag.get("id") or "").strip()
                if tag_id:
                    self.add_tag_to_work(work_id, tag_id)
            uncategorized_id = self.ensure_default_uncategorized_folder_id()
            self.add_work_to_folder(fid if fid else uncategorized_id, work_id)
        except Exception as e:
            can_remove_destination = work_id is None
            if work_id:
                try:
                    self.delete_work_record(work_id)
                    can_remove_destination = True
                except Exception:
                    can_remove_destination = False
            if can_remove_destination:
                try:
                    os.remove(destination_abs)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
            msg = f"Failed to insert imported file into works table: {e}"
            self.execute_query(
                """
                UPDATE processing_files
                SET status = 'error', last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (msg, processing_file_id),
            )
            raise ValueError(msg)

        try:
            self.execute_query(
                """
                UPDATE processing_files
                SET status = 'imported',
                    imported_work_id = ?,
                    imported_at = CURRENT_TIMESTAMP,
                    last_error = NULL,
                    abs_path = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (work_id, destination_abs, processing_file_id),
            )
        except Exception as e:
            msg = f"Could not finalize import metadata: {e}"
            self.execute_query(
                """
                UPDATE processing_files
                SET status = 'error', last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (msg, processing_file_id),
            )
            raise ValueError(msg)

        try:
            os.remove(source_abs)
        except OSError:
            pass
        prune_empty_processing_parent_dirs(processing_root, source_abs)
        return {"processing_file_id": processing_file_id, "work_id": work_id}

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
        source_kind, source_url, provider, provider_id = _canonical_new_source(
            source_kind, source_url, provider, provider_id, file_path)
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
        pex = _prks_sql_work_summary_person_extras("works")
        rows = list(self.execute_query(f"SELECT {sel}, {pex} FROM works ORDER BY created_at DESC"))
        finish_work_summary_rows(rows, self.storage.pdfs_dir)
        return rows

    def etag_works_catalog(self) -> str:
        r = self.execute_query("SELECT COUNT(*) AS c, COALESCE(MAX(updated_at), '') AS m FROM works")
        row = r[0] if r else {"c": 0, "m": ""}
        ff = self.execute_query("SELECT COUNT(*) AS c FROM folder_files")
        ffc = (ff[0] if ff else {"c": 0})["c"]
        rr = self.execute_query("SELECT COUNT(*) AS c FROM roles")
        rc = (rr[0] if rr else {"c": 0})["c"]
        return f'W/"prks-works-{row["c"]}-{row["m"]}-ff{ffc}-r{rc}"'

    # ---- Browse projections ------------------------------------------------
    #
    # Three INDEPENDENT projections, deliberately not one catalog. A single
    # catalog carrying `last_opened_at` would mean that merely *opening* a Work
    # invalidates the browse cache for Progress, Types and Recently Added too --
    # a read invalidating unrelated read models. Splitting them keeps each
    # coherence domain proportional to what actually changed.

    def get_works_browse_catalog(self) -> List[dict]:
        """Complete Work catalog in the compact browse projection.

        Ordered deterministically so a cached copy and a fresh read agree:
        `title` is what every browse route sorts by locally, and `id` breaks
        ties that a title collation alone would leave unspecified.
        """
        sel = _prks_work_browse_select("works")
        pex = _prks_sql_work_summary_person_extras("works")
        rows = list(
            self.execute_query(
                f"SELECT {sel}, {pex} FROM works "
                "ORDER BY works.title COLLATE NOCASE ASC, works.id ASC"
            )
        )
        finish_work_summary_rows(rows, self.storage.pdfs_dir)
        return rows

    def get_recent_browse(self, limit: int = 30) -> List[dict]:
        """Top-N by `last_opened_at`, with an explicit tie-break.

        Open events are recorded at millisecond resolution
        (`work_open_sync.format_moment`), so two quick opens rarely tie any
        more -- but rows written before that, and two events inside the same
        millisecond, still can. Without a secondary key their order would be
        unspecified, which an offline projection could not reproduce
        faithfully, so `id` is the canonical tie-break here and everywhere the
        same list is derived. Mixed second- and millisecond-precision values
        sort chronologically against each other: a bare `12:00:00` is a prefix
        of `12:00:00.250`, so text ordering already reads it as `.000`.
        """
        sel = _prks_work_browse_select("works", abstract_excerpt=False)
        pex = _prks_sql_work_summary_person_extras("works")
        rows = list(
            self.execute_query(
                f"SELECT {sel}, works.last_opened_at, {pex} FROM works "
                "WHERE works.last_opened_at IS NOT NULL "
                "ORDER BY works.last_opened_at DESC, works.id ASC LIMIT ?",
                (limit,),
            )
        )
        finish_work_summary_rows(rows, self.storage.pdfs_dir)
        return rows

    def get_recently_added_browse(self, limit: int = 50) -> List[dict]:
        """Top-N by `created_at`, same deterministic tie-break as Recent.

        Carries `folder_id` and `publisher` because the Recently-added tab
        filters locally over them (and over the folder title it resolves from
        the cached hierarchy).
        """
        sel = _prks_work_browse_select("works", abstract_excerpt=False)
        pex = _prks_sql_work_summary_person_extras("works")
        folder = (
            "(SELECT folder_id FROM folder_files WHERE work_id = works.id LIMIT 1) AS folder_id"
        )
        rows = list(
            self.execute_query(
                f"SELECT {sel}, works.created_at, works.publisher, {folder}, {pex} FROM works "
                "ORDER BY works.created_at DESC, works.id ASC LIMIT ?",
                (limit,),
            )
        )
        finish_work_summary_rows(rows, self.storage.pdfs_dir)
        return rows

    @staticmethod
    def etag_for_representation(label: str, rows: List[dict]) -> str:
        """Weak ETag derived from the serialized response itself.

        The invariant is absolute: if the body can change, the ETag must
        change. Hand-maintained table-count/MAX(updated_at) probes have already
        shipped two classes of defect here -- a count that a *move* leaves
        identical, and `CURRENT_TIMESTAMP`'s one-second granularity hiding a
        same-second edit. Hashing the representation makes the invariant true
        by construction and cannot drift when a projection gains a field.
        """
        blob = json.dumps(rows, sort_keys=True, default=str, separators=(",", ":"))
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]
        return f'W/"prks-{label}-{len(rows)}-{digest}"'

    def etag_folders_catalog(self, rows: Optional[List[dict]] = None) -> str:
        """Weak ETag derived from the serialized catalog itself.

        The invariant this must satisfy is one-directional but absolute: if the
        `/api/folders` body can change, this value must change. A revision
        probe built from row *counts* plus MAX(updated_at) could not satisfy it:

        * moving a Work from folder A to B leaves the `folder_files` row count
          identical while both rows' `work_count` change, and
        * `CURRENT_TIMESTAMP` has one-second granularity, so MAX(updated_at)
          does not reliably move for a change made within the same second as
          the previous one.

        Either hole lets a stale catalog be revalidated as 304 and republished
        into the client's `folders:index` *after* its offline coherence domain
        was correctly invalidated. Hashing the payload makes the invariant true
        by construction, and cannot silently drift when a field is added to
        `get_all_folders()`. The catalog is one small query, so callers pass
        the rows they already built rather than running it twice.
        """
        data = self.get_all_folders() if rows is None else rows
        blob = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]
        return f'W/"prks-folders-{len(data)}-{digest}"'

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
        return self.etag_for_representation("tags", self.get_all_tags())

    def etag_recent_works(self) -> str:
        """Revision for /api/recent (last_opened ordering can change without works.updated_at)."""
        r = self.execute_query(
            "SELECT COUNT(*) AS c, COALESCE(MAX(last_opened_at), '') AS m FROM works WHERE last_opened_at IS NOT NULL"
        )
        row = r[0] if r else {"c": 0, "m": ""}
        return f'W/"prks-recent-{row["c"]}-{row["m"]}"'

    def etag_recently_added_works(self) -> str:
        """Revision for /api/recently-added (ordered by works.created_at)."""
        r = self.execute_query(
            "SELECT COUNT(*) AS c, COALESCE(MAX(created_at), '') AS m FROM works"
        )
        row = r[0] if r else {"c": 0, "m": ""}
        return f'W/"prks-recently-added-{row["c"]}-{row["m"]}"'

    def delete_work_record(self, work_id: str) -> Optional[DeletedWorkRecord]:
        from backend.work_lifecycle_sync import delete_work_record_on_conn

        with self.connection() as conn:
            return delete_work_record_on_conn(conn, work_id)

    def get_work_summaries_by_ids_ordered(self, work_ids: List[str]) -> List[dict]:
        ordered_ids = [str(wid).strip() for wid in (work_ids or []) if str(wid).strip()]
        if not ordered_ids:
            return []
        placeholders = ",".join("?" * len(ordered_ids))
        wsel = _prks_work_summary_select_with_folder("works")
        pex = _prks_sql_work_summary_person_extras("works")
        rows = list(
            self.execute_query(
                f"SELECT {wsel}, {pex} FROM works WHERE id IN ({placeholders})",
                tuple(ordered_ids),
            )
        )
        finish_work_summary_rows(rows, self.storage.pdfs_dir)
        by_id = {r["id"]: r for r in rows}
        return [by_id[i] for i in ordered_ids if i in by_id]

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
        # Deleting a folder removes the folder and its folder_tags rows (via
        # ON DELETE CASCADE). It deliberately does NOT touch Tag identity --
        # see delete_tag(). One statement, so atomic by autocommit.
        self.execute_query("DELETE FROM folders WHERE id = ?", (folder_id,))

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
            pex = _prks_sql_work_summary_person_extras("works")
            rows = list(
                self.execute_query(
                    f"SELECT {wsel}, {pex} FROM works WHERE id IN ({ph}) ORDER BY updated_at DESC, created_at DESC",
                    tuple(id_list),
                )
            )
            finish_work_summary_rows(rows, self.storage.pdfs_dir)
            return rows

        if not ordered_ids:
            return []
        placeholders = ",".join("?" * len(ordered_ids))
        wsel = _prks_work_summary_select_with_folder("works")
        pex = _prks_sql_work_summary_person_extras("works")
        rows = list(
            self.execute_query(
                f"SELECT {wsel}, {pex} FROM works WHERE id IN ({placeholders})",
                tuple(ordered_ids),
            )
        )
        finish_work_summary_rows(rows, self.storage.pdfs_dir)
        by_id = {r["id"]: r for r in rows}
        return [by_id[i] for i in ordered_ids if i in by_id]

    def search_works_any(self, term: str) -> List[dict]:
        """
        OR-mode search for single term across:
        - keyword index (FTS/LIKE/linked persons)
        - author match
        - publisher match (including aliases)
        """
        t = (term or "").strip()
        if not t:
            return []

        seen: set = set()
        ordered: List[dict] = []
        extra_ids: set = set()

        kw_rows = self.search_works(t, "", "")
        for r in kw_rows:
            wid = r.get("id")
            if not wid or wid in seen:
                continue
            seen.add(wid)
            ordered.append(r)

        try:
            extra_ids.update(self.work_ids_matching_author(t))
        except Exception:
            pass
        try:
            extra_ids.update(self.work_ids_matching_publisher(t))
        except Exception:
            pass

        extra_ids = {wid for wid in extra_ids if wid and wid not in seen}
        if not extra_ids:
            return ordered

        id_list = list(extra_ids)
        ph = ",".join("?" * len(id_list))
        wsel = _prks_work_summary_select_with_folder("works")
        pex = _prks_sql_work_summary_person_extras("works")
        rows = list(
            self.execute_query(
                f"SELECT {wsel}, {pex} FROM works WHERE id IN ({ph}) ORDER BY updated_at DESC, created_at DESC",
                tuple(id_list),
            )
        )
        finish_work_summary_rows(rows, self.storage.pdfs_dir)
        ordered.extend(rows)
        return ordered

    def get_works_by_tag_name(self, tag_name: str) -> List[dict]:
        """Works tagged with the canonical tag matching this name or a tag alias (case-insensitive)."""
        name = (tag_name or "").strip()
        if not name:
            return []
        tid = self.resolve_tag_id_by_label(name)
        if not tid:
            return []
        wsel = _prks_work_summary_select("w")
        pex = _prks_sql_work_summary_person_extras("w")
        query = f"""
        SELECT DISTINCT {wsel}, {pex} FROM works w
        JOIN work_tags wt ON w.id = wt.work_id
        WHERE wt.tag_id = ?
        ORDER BY w.created_at DESC
        """
        rows = list(self.execute_query(query, (tid,)))
        finish_work_summary_rows(rows, self.storage.pdfs_dir)
        return rows

    def get_work(self, work_id: str) -> Optional[dict]:
        res = self.execute_query("SELECT * FROM works WHERE id = ?", (work_id,))
        if not res: return None
        work = res[0]
        work['roles'] = self.get_work_roles(work_id)
        work['arguments'] = []
        work['research_refs'] = {"concepts": [], "arguments": []}
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
        # NOTE: this read is PURE. `last_opened_at` used to be stamped here,
        # which made GET /api/works/:id a hidden mutation -- every internal
        # refresh (tag edit, folder move, playlist change, role edit, metadata
        # save, notes save) silently reordered Recent, and left `recent:index`
        # eligible while the server representation had changed. Marking a Work
        # opened is now the explicit `mark_work_opened()` operation below.
        finish_work_summary_rows([work], self.storage.pdfs_dir)
        return work

    def mark_work_opened(self, work_id: str) -> bool:
        """Record that the user actually opened this Work.

        An explicit canonical operation rather than a side effect of reading,
        so that only genuine foreground navigation reorders Recent. This is
        also the shape a local-first outbox needs: MARK_WORK_OPENED(workId)
        can be queued, coalesced and synchronized independently of the
        metadata/tag/folder operations it used to be entangled with.
        """
        wid = (work_id or "").strip()
        if not wid:
            raise ValueError("work_id is required")
        # Server-now is this caller's event time; a synchronized
        # MARK_WORK_OPENED supplies the device's own normalized event time
        # instead. Both go through the same max-register, so the two entry
        # points can never give the column two different meanings.
        with self.connection() as conn:
            existed, _changed, _effective = work_open_sync.set_opened_at(
                conn, wid, work_open_sync.now_moment()
            )
        return existed

    def recent_item_on_conn(self, conn, work_id: str) -> Optional[dict]:
        """One Work in the exact `/api/recent` row projection.

        Shares the projection with `get_recent_browse()` so an acknowledged
        open event can be reconciled into a cached Recent list without the
        client rebuilding server-derived author and file fields itself.
        """
        sel = _prks_work_browse_select("works", abstract_excerpt=False)
        pex = _prks_sql_work_summary_person_extras("works")
        rows = [
            dict(row)
            for row in conn.execute(
                f"SELECT {sel}, works.last_opened_at, {pex} FROM works WHERE works.id = ?",
                (work_id,),
            ).fetchall()
        ]
        if not rows:
            return None
        finish_work_summary_rows(rows, self.storage.pdfs_dir)
        return rows[0]

    def get_recent_works(self, limit: int = 30) -> List[dict]:
        sel = _prks_work_summary_select("works")
        pex = _prks_sql_work_summary_person_extras("works")
        rows = list(
            self.execute_query(
                f"SELECT {sel}, {pex} FROM works WHERE last_opened_at IS NOT NULL ORDER BY last_opened_at DESC LIMIT ?",
                (limit,),
            )
        )
        finish_work_summary_rows(rows, self.storage.pdfs_dir)
        return rows

    def get_recently_added_works(self, limit: int = 50) -> List[dict]:
        sel = _prks_work_summary_select_with_folder("works")
        pex = _prks_sql_work_summary_person_extras("works")
        rows = list(
            self.execute_query(
                f"SELECT {sel}, {pex} FROM works ORDER BY works.created_at DESC LIMIT ?",
                (limit,),
            )
        )
        finish_work_summary_rows(rows, self.storage.pdfs_dir)
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
        # A Work's video source is an AGGREGATE with one revision, not a set of
        # independent columns. Letting PATCH write `provider_id` on its own
        # recreates exactly the contradiction SET_WORK_SOURCE exists to
        # prevent -- a row whose stored URL names video B while the viewer,
        # which reads `provider_id` first, plays video A -- and it does so
        # without advancing the source revision, so no other device can
        # discover it. `source_url` is already refused on a video Work by
        # FIELD_KIND_GUARDS; these are the columns that carry identity with it.
        #
        # This is a bound on EDITING AN EXISTING WORK. Creation and import
        # write these columns through `add_work`, where the whole identity is
        # established at once and there is no prior value to contradict.
        identity_writes = sorted(set(updates) & work_source_sync.SOURCE_AGGREGATE_COLUMNS)
        if identity_writes:
            raise ValueError(
                "%s cannot be set directly: a Work's source identity changes as one "
                "aggregate. Use the SET_WORK_SOURCE operation."
                % ", ".join(identity_writes))
        if 'hide_pdf_link_annotations' in updates:
            raw = updates['hide_pdf_link_annotations']
            if raw is True or raw == 1 or (isinstance(raw, str) and raw.strip().lower() in ('1', 'true', 'yes')):
                updates['hide_pdf_link_annotations'] = 1
            else:
                updates['hide_pdf_link_annotations'] = 0
        if 'doc_type' in updates:
            updates['doc_type'] = normalize_doc_type(updates['doc_type'])
        # `thumb_page` used to be normalized here, and that normalization
        # SILENTLY CLEARED anything it could not read: 0, -1, "abc" and a
        # fraction all became NULL, so a client asking for an impossible page
        # was told the field had been emptied on purpose. Its field codec now
        # owns the conversion for every path, and refuses what it cannot read
        # instead of guessing -- the same rule an uninterpretable Published
        # Date follows. See work_metadata_sync.FIELD_CODECS.
        if not updates:
            return
        # Revisions record CANONICAL history, not sync-endpoint history. An
        # ordinary online PATCH that changes a synchronized field has to
        # advance that field's revision, or an offline device holding the old
        # value has no way to discover it was overtaken -- and would overwrite
        # it believing itself current. Only fields whose canonical value
        # actually changes advance, and the whole edit commits as one
        # transaction so a value can never be stored without its revision.
        synced = {k: v for k, v in updates.items() if k in work_metadata_sync.SYNCED_FIELDS}
        note_updates = {k: updates[k] for k in ("private_notes",) if k in updates}
        plain = {k: v for k, v in updates.items() if k not in synced and k not in note_updates}
        # PATCH and the synchronization handler converge on ONE representation
        # before either validates or writes. A caller may hand this method a
        # native value -- an int for `thumb_page`, or None -- while the sync
        # path always carries the wire string; converting here means the two
        # paths run the same validator over the same spelling and reach the
        # same revision decision, instead of being two implementations that
        # agree until they do not.
        # VALIDATE FIRST, then canonicalize. The other order silently repairs
        # what it should refuse: `canonical_wire` maps an unreadable page to
        # "", so validating afterwards would see a well-formed clear and a
        # request for page 0 would be answered by emptying the field -- which
        # is the defect this path had before the codec existed.
        #
        # One rule, whichever path a value arrives by. If PATCH accepted an
        # Abstract the durable queue would refuse, the same edit would be
        # savable online and impossible offline -- exactly the split contract
        # moving a field to local-first is supposed to remove.
        for field, value in synced.items():
            if not work_metadata_sync.is_valid_field_value(field, value):
                raise ValueError("%s is not a valid value for %s" % (value, field)
                                 if field in work_metadata_sync.FIELD_ALLOWLISTS
                                    or field in work_metadata_sync.FIELD_CODECS
                                 else "%s exceeds the maximum supported length" % field)
        # Now that every value is known-good, put PATCH and the synchronization
        # handler on ONE representation before either writes.
        synced = {k: work_metadata_sync.canonical_wire(k, v) for k, v in synced.items()}
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # The same guard the synchronization handler applies: a field-scoped
            # write may not touch a video Work's source identity, whichever
            # path it arrives by.
            for field in synced:
                refusal = work_metadata_sync.guarded_field_refusal(conn, work_id, field)
                if refusal is not None:
                    raise ValueError(
                        "%s cannot be set on this Work: its source identity is not a "
                        "field-scoped value" % field)
            if plain:
                set_clause = ", ".join(f"{k} = ?" for k in plain)
                conn.execute(
                    f"UPDATE works SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    tuple(list(plain.values()) + [work_id]),
                )
            if "private_notes" in note_updates:
                # Lazy import avoids db_manager -> work_note_sync ->
                # research_network -> db_manager during module initialization.
                from backend import work_note_sync
                try:
                    work_note_sync.set_private_note_on_conn(
                        conn, work_id,
                        "" if note_updates["private_notes"] is None
                        else note_updates["private_notes"])
                except ValueError as exc:
                    raise ValueError(str(exc)) from exc
            for field, value in synced.items():
                work_metadata_sync.set_field_on_conn(conn, work_id, field, value)

    def get_work_notes_state(self, work_id: str) -> Optional[dict]:
        """The two whole-document revisions; values live on Work detail."""
        from backend import work_note_sync
        with self.connection() as conn:
            conn.execute("BEGIN")
            return work_note_sync.get_notes_state_on_conn(conn, work_id)

    def get_work_metadata_state(self, work_id: str) -> Optional[dict]:
        """Synchronization state for the supported Work fields.

        Deliberately its own endpoint rather than extra keys on the Work
        detail: revisions are synchronization bookkeeping, and every consumer
        of a Work would otherwise pay for them and re-cache on every change.
        """
        with self.connection() as conn:
            conn.execute("BEGIN")
            return work_metadata_sync.get_field_state_on_conn(conn, work_id)

    # --- Playlists (ordered collections of works, used for video courses) ---

    def add_playlist(self, title: str, description: str = "", original_url: str = "") -> str:
        pid = self.generate_id("PL")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # The same construction boundary the durable CREATE_PLAYLIST uses,
            # so the two paths cannot drift on what a legal playlist is.
            playlist_sync.insert_playlist_on_conn(conn, pid, title, description, original_url)
        return pid

    def update_playlist(self, playlist_id: str, fields: dict) -> None:
        """Edit a Playlist through the revision-aware boundary.

        Every write shares the durable `SET_PLAYLIST_FIELD` family's boundary,
        so a playlist can never change without its revision: an offline device
        holding the old value would otherwise have no way to discover it had
        been overtaken.
        """
        updates = {k: v for k, v in fields.items() if k in playlist_sync.FIELD_SET}
        if not updates:
            return
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute(
                    "SELECT 1 FROM playlists WHERE id = ?", (playlist_id,)).fetchone():
                raise ValueError("Playlist not found.")
            for field in playlist_sync.FIELDS:
                if field not in updates:
                    continue
                value = updates[field]
                playlist_sync.set_field_on_conn(
                    conn, playlist_id, field, "" if value in (None, False) else str(value))

    def delete_playlist(self, playlist_id: str) -> None:
        """Destruction, through the boundary that advances what it invalidates.

        Its items go with it -- they are memberships, not the videos themselves
        -- so every member Work's membership revision advances too: a device
        holding "this video is in that playlist" has to be able to discover it
        was overtaken.
        """
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            playlist_sync.delete_playlist_on_conn(conn, playlist_id)

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
        pex = _prks_sql_work_summary_person_extras("w")
        p["items"] = self.execute_query(
            """
            SELECT {wsel}, i.position, {pex}
            FROM playlist_items i
            JOIN works w ON w.id = i.work_id
            WHERE i.playlist_id = ?
            ORDER BY i.position ASC, w.created_at ASC
            """.format(wsel=wsel, pex=pex),
            (playlist_id,),
        )
        finish_work_summary_rows(p["items"], self.storage.pdfs_dir)
        return p

    def add_work_to_playlist(self, playlist_id: str, work_id: str, position: Optional[int] = None) -> None:
        """Put a Work in a Playlist, through the revision-aware boundary.

        A Work is in at most one playlist, so this is a SCALAR write on the
        WORK: the durable `SET_WORK_PLAYLIST` family shares this boundary, and
        without that an offline device holding the old playlist could never
        discover it had been overtaken.

        An explicit `position` is honoured as a second decision -- membership,
        then order -- because those are two revisions and the caller asked for
        both.
        """
        pid = (playlist_id or "").strip()
        wid = (work_id or "").strip()
        if not pid or not wid:
            raise ValueError("Playlist or work not found.")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM works WHERE id = ?", (wid,)).fetchone():
                raise ValueError("Playlist or work not found.")
            try:
                playlist_sync.set_work_playlist_on_conn(conn, wid, pid)
            except playlist_sync.PlaylistRuleError as error:
                raise ValueError("Playlist or work not found.") from error
            if position is None:
                return
            order = [w for w in playlist_sync.current_order(conn, pid) if w != wid]
            index = max(0, min(int(position), len(order)))
            order.insert(index, wid)
            playlist_sync.set_order_on_conn(conn, pid, order)

    def remove_work_from_playlist(self, playlist_id: str, work_id: str) -> None:
        """Membership removal and the Playlist timestamp bump are one write.

        Offline coherence rests on "a failed canonical request keeps the
        previous cache eligible", which is only sound if a failure really means
        nothing changed. Two auto-committing statements could otherwise drop the
        membership, fail the second write, and return an error the client would
        (correctly, by contract) treat as a no-op.

        Removing is the same SCALAR as adding, with `''` for the value -- so a
        video taken out of a playlist advances the same revision that putting it
        in did.
        """
        wid = (work_id or "").strip()
        pid = (playlist_id or "").strip()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if playlist_sync.current_work_playlist(conn, wid) != pid:
                return
            playlist_sync.set_work_playlist_on_conn(conn, wid, "")

    def reorder_playlist(self, playlist_id: str, work_ids: List[str]) -> None:
        """Rewrite the whole order, through the boundary the aggregate owns."""
        if not work_ids:
            return
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                playlist_sync.set_order_on_conn(conn, playlist_id, list(work_ids))
            except playlist_sync.PlaylistRuleError as error:
                raise ValueError("Playlist not found.") from error

    def get_position_sync_state(self, position_id: str) -> Optional[dict]:
        """Field revisions for one Position."""
        from backend import position_sync
        with self.connection() as conn:
            conn.execute("BEGIN")
            return position_sync.get_position_state_on_conn(conn, position_id)

    def get_argument_sync_state(self, argument_id: str) -> Optional[dict]:
        """Field revisions plus the two aggregates' revisions."""
        from backend import argument_sync
        with self.connection() as conn:
            conn.execute("BEGIN")
            return argument_sync.get_argument_state_on_conn(conn, argument_id)

    def get_concept_sync_state(self, concept_id: str) -> Optional[dict]:
        """Field revisions plus the two aggregates and their revisions."""
        from backend import concept_sync
        with self.connection() as conn:
            conn.execute("BEGIN")
            return concept_sync.get_concept_state_on_conn(conn, concept_id)

    def get_playlist_sync_state(self, playlist_id: str) -> Optional[dict]:
        """Field revisions and the order revision for one Playlist."""
        with self.connection() as conn:
            conn.execute("BEGIN")
            return playlist_sync.get_playlist_state_on_conn(conn, playlist_id)

    def get_work_playlist_state(self, work_id: str) -> Optional[dict]:
        """Which playlist a Work is in, and the revision that says so."""
        with self.connection() as conn:
            conn.execute("BEGIN")
            return playlist_sync.get_work_playlist_state_on_conn(conn, work_id)

    def get_work_annotations(self, work_id: str) -> str:
        """Reconstruct annotation JSON solely from canonical `annotations` rows."""
        res = self.execute_query(
            """
            SELECT id, type, content, page_index, color, geometry_json, updated_at
            FROM annotations
            WHERE work_id = ?
            ORDER BY page_index ASC, id ASC
            """,
            (work_id,),
        )
        return json.dumps([reconstruct_annotation(row) for row in res])

    def get_work_annotations_state(self, work_id: str):
        """Per-annotation sync revisions for one Work (durable-client hydration)."""
        with self.connection() as conn:
            conn.execute("BEGIN")
            return pdf_annotation_sync.get_annotations_state_on_conn(conn, work_id)

    def get_work_annotations_snapshot(self, work_id: str):
        """Coherent annotation items + revisions + materialization from one txn."""
        with self.connection() as conn:
            conn.execute("BEGIN")
            return pdf_annotation_sync.get_annotations_snapshot_on_conn(conn, work_id)

    def get_work_pdf_materialization(self, work_id: str):
        from backend import pdf_materialization

        with self.connection() as conn:
            conn.execute("BEGIN")
            return pdf_materialization.get_materialization_on_conn(conn, work_id)

    def mark_work_pdf_materialized(self, work_id: str, *, at_revision=None):
        from backend import pdf_materialization

        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rev = pdf_materialization.mark_pdf_materialized_on_conn(
                conn, work_id, at_revision=at_revision
            )
            conn.commit()
            return rev

    def accept_work_pdf_materialization_claim(self, work_id: str, claimed_revision):
        """Validate a durable materialization claim under BEGIN IMMEDIATE.

        Raises LookupError / ValueError(ANNOTATION_MATERIALIZATION_STALE).
        Returns the accepted canonical generation.
        """
        from backend import pdf_materialization

        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                accepted = pdf_materialization.accept_materialization_revision_on_conn(
                    conn, work_id, claimed_revision
                )
                conn.commit()
                return accepted
            except Exception:
                conn.rollback()
                raise

    def mark_work_pdf_materialized_if_claim_current(self, work_id: str, claimed_revision):
        """Re-validate claim then mark materialized in one IMMEDIATE transaction."""
        from backend import pdf_materialization

        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                accepted = pdf_materialization.accept_materialization_revision_on_conn(
                    conn, work_id, claimed_revision
                )
                rev = pdf_materialization.mark_pdf_materialized_on_conn(
                    conn, work_id, at_revision=accepted
                )
                conn.commit()
                return rev
            except Exception:
                conn.rollback()
                raise

    def adopt_byte_only_user_markup(self, work_id: str, viewer_items):
        """Insert canonical rows for viewer user markup missing from metadata.

        Does not delete metadata-only rows and never adopts Links/widgets.
        """
        from backend import pdf_annotation_adopt

        with self.connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                result = pdf_annotation_adopt.adopt_byte_only_user_markup_on_conn(
                    conn, work_id, viewer_items
                )
                conn.commit()
                return result
            except sqlite3.IntegrityError as exc:
                raise WorkAnnotationError(
                    "annotation_id_conflict",
                    "Annotation ID belongs to another Work.",
                    409,
                ) from exc

    def save_work_annotations(
        self,
        work_id: str,
        annotations_json: str,
        *,
        base_set_revision: Any = None,
    ):
        """Compat full-list replace (legacy online path only).

        Durable clients use CREATE/SET/DELETE_PDF_ANNOTATION. This replace path
        remains for temporary online-legacy support when the durable store is
        unavailable; it must not become a second product write path.

        ``base_set_revision`` is the viewer's acknowledged canonical annotation
        set revision. When provided, a mismatch with the current tip refuses the
        replace (``ANNOTATION_SET_STALE``) before any update/delete. Omit only
        for internal callers that already serialized against the tip; the HTTP
        adapter always passes the client value.

        Returns the canonical annotation-set revision after the replace (the
        generation this replacement produced, or the unchanged tip when the
        list was identical).
        """
        items = parse_annotations_json(annotations_json)
        return self.sync_work_annotations(
            work_id, items, base_set_revision=base_set_revision
        )

    def sync_work_annotations(
        self,
        work_id: str,
        items: List[dict],
        *,
        base_set_revision: Any = None,
    ) -> int:
        """Replace one Work's canonical annotations in a single transaction.

        Validates the complete incoming list before any delete/update/insert.
        Prefer per-annotation sync ops for product writes; full-list replace is
        compat/legacy only.

        Returns the canonical annotation-set revision after the replace.
        """
        with self.connection() as conn:
            try:
                return self._sync_work_annotations_on_conn(
                    conn, work_id, items, base_set_revision=base_set_revision
                )
            except sqlite3.IntegrityError as exc:
                raise WorkAnnotationError(
                    "annotation_id_conflict",
                    "Annotation ID belongs to another Work.",
                    409,
                ) from exc

    def _sync_work_annotations_on_conn(
        self,
        conn,
        work_id: str,
        items: List[dict],
        *,
        base_set_revision: Any = None,
    ) -> int:
        exists = conn.execute(
            "SELECT id FROM works WHERE id = ?",
            (work_id,),
        ).fetchone()
        if not exists:
            raise WorkAnnotationError("work_not_found", "Work not found.", 404)

        from backend import pdf_materialization

        mat = pdf_materialization.get_materialization_on_conn(conn, work_id)
        current_set_rev = (
            int(mat["canonical_annotation_set_revision"]) if mat else 0
        )
        if base_set_revision is not None:
            try:
                base = int(base_set_revision)
            except (TypeError, ValueError) as exc:
                raise WorkAnnotationError(
                    "malformed_annotation_payload",
                    "canonical_annotation_set_revision must be an integer.",
                    400,
                ) from exc
            if base != current_set_rev:
                raise WorkAnnotationError(
                    "ANNOTATION_SET_STALE",
                    "Annotation set revision is stale.",
                    409,
                )

        normalized = normalize_annotation_list(items)
        incoming_ids = [row["id"] for row in normalized]

        if incoming_ids:
            placeholders = ",".join("?" * len(incoming_ids))
            owned = conn.execute(
                f"SELECT id, work_id FROM annotations WHERE id IN ({placeholders})",
                incoming_ids,
            ).fetchall()
            for existing in owned:
                if existing["work_id"] != work_id:
                    raise WorkAnnotationError(
                        "annotation_id_conflict",
                        "Annotation ID belongs to another Work.",
                        409,
                    )

        current_rows = {
            row["id"]: row
            for row in conn.execute(
                """
                SELECT id, type, content, page_index, color, geometry_json, updated_at
                FROM annotations WHERE work_id = ?
                """,
                (work_id,),
            ).fetchall()
        }
        current_ids = set(current_rows)
        incoming_set = {row["id"] for row in normalized}

        any_set_changed = False
        for row in normalized:
            geom = json.dumps(row["geometry"], allow_nan=False)
            params = (
                row["type"],
                row["content"],
                row["page_index"],
                row["color"],
                geom,
            )
            desired = round_trip_annotation(
                {
                    "id": row["id"],
                    "type": row["type"],
                    "contents": row["content"],
                    "pageIndex": row["page_index"],
                    "color": row["color"],
                    **row["geometry"],
                }
            )
            if row["id"] in current_ids:
                before = reconstruct_annotation(current_rows[row["id"]])
                changed = not annotations_semantically_equal(before, desired)
                if changed:
                    any_set_changed = True
                    conn.execute(
                        """
                        UPDATE annotations SET
                            type = ?, content = ?, page_index = ?, color = ?,
                            geometry_json = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND work_id = ?
                        """,
                        params + (row["id"], work_id),
                    )
                    pdf_annotation_sync.advance_revision_if_changed_on_conn(
                        conn, work_id, row["id"], changed=True
                    )
            else:
                # Mirror CREATE_PDF_ANNOTATION: a prior durable DELETE (or any
                # mutation) leaves revision > 0 — never reinsert that id via
                # full-list replace.
                prior_rev = pdf_annotation_sync.get_revision(
                    conn, work_id, row["id"]
                )
                if prior_rev > 0:
                    raise WorkAnnotationError(
                        "ANNOTATION_ID_REUSED",
                        "Annotation ID was previously used and cannot be reinserted.",
                        409,
                    )
                any_set_changed = True
                # Construction: insert without advancing (revision stays 0).
                conn.execute(
                    """
                    INSERT INTO annotations
                        (id, work_id, type, content, page_index, color, geometry_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (row["id"], work_id) + params,
                )

        to_delete = current_ids - incoming_set
        for ann_id in sorted(to_delete):
            any_set_changed = True
            conn.execute(
                "DELETE FROM annotations WHERE work_id = ? AND id = ?",
                (work_id, ann_id),
            )
            pdf_annotation_sync.advance_revision_if_changed_on_conn(
                conn, work_id, ann_id, changed=True
            )
        if any_set_changed:
            return pdf_materialization.bump_canonical_annotation_set_on_conn(
                conn, work_id
            )
        return current_set_rev

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

    def ensure_default_uncategorized_folder_id(self) -> str:
        """Top-level folder titled exactly 'Uncategorized'; create if missing."""
        rows = self.execute_query(
            "SELECT id FROM folders WHERE parent_id IS NULL AND title = ? LIMIT 1",
            (_PRKS_UNCATEGORIZED_FOLDER_TITLE,),
        )
        if rows:
            return str(rows[0]["id"])
        return self.add_folder(_PRKS_UNCATEGORIZED_FOLDER_TITLE, "", None)

    def add_folder(
        self,
        title: str,
        description: str = "",
        parent_id: Optional[str] = None,
    ) -> str:
        pid = self._normalize_folder_parent_id(parent_id)
        folder_id = self.generate_id("F")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # The same construction boundary the durable CREATE_FOLDER uses, so
            # the two paths cannot drift on what a legal folder is.
            folder_sync.insert_folder_on_conn(conn, folder_id, title, description, pid or "")
        return folder_id

    def get_all_folders(self) -> List[dict]:
        return self.execute_query(
            """
            SELECT
                f.*,
                (SELECT COUNT(*) FROM folder_files ff WHERE ff.folder_id = f.id) AS work_count,
                (SELECT COUNT(*) FROM folders c WHERE c.parent_id = f.id) AS child_count
            FROM folders f
            WHERE NOT (
                f.parent_id IS NULL
                AND TRIM(f.title) = ?
                AND (SELECT COUNT(*) FROM folder_files ff WHERE ff.folder_id = f.id) = 0
                AND (SELECT COUNT(*) FROM folders c WHERE c.parent_id = f.id) = 0
            )
            ORDER BY f.title COLLATE NOCASE, f.created_at DESC
            """,
            (_PRKS_UNCATEGORIZED_FOLDER_TITLE,),
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
        pex = _prks_sql_work_summary_person_extras("w")
        query = f"""
        SELECT
            {wsel},
            {pex}
        FROM works w
        JOIN folder_files ff ON w.id = ff.work_id
        WHERE ff.folder_id = ?
        ORDER BY w.created_at DESC
        """
        folder["works"] = list(self.execute_query(query, (folder_id,)))
        finish_work_summary_rows(folder["works"], self.storage.pdfs_dir)
        folder['tags'] = self.get_folder_tags(folder_id)
        return folder

    def get_folder_work_ids(self, folder_id: str) -> List[str]:
        """IDs of the Works filed in this folder.

        Offline coherence uses this: a cached Work detail embeds `folder_title`,
        so renaming a folder stales exactly its members' Work snapshots. The
        canonical PATCH boundary reports them rather than making the client
        guess from whatever page happened to be focused.
        """
        rows = self.execute_query(
            "SELECT work_id FROM folder_files WHERE folder_id = ?", (folder_id,)
        )
        return [r["work_id"] for r in rows if r["work_id"]]

    def update_folder_metadata(self, folder_id: str, fields: dict):
        """Update editable folder fields, through the revision-aware writer.

        The ordinary PATCH and the durable `SET_FOLDER_FIELD` share one
        boundary, so a folder can never change without its revision: an offline
        device holding the old value would otherwise have no way to discover it
        had been overtaken.

        Fields are applied in a deliberate order. A title is unique WITHIN its
        parent, so a request that renames AND moves has to rename first -- the
        other order can collide with a sibling the folder is about to leave
        behind.
        """
        updates = {k: v for k, v in fields.items() if k in folder_sync.FIELD_SET}
        if not updates:
            return
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute(
                    "SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone():
                raise ValueError("Folder not found.")
            for field in ("title", "parent_id", "description", "private_notes"):
                if field not in updates:
                    continue
                value = updates[field]
                folder_sync.set_field_on_conn(
                    conn, folder_id, field, "" if value in (None, False) else str(value))

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
        """Assign / move / clear, through the revision-aware boundary.

        A Work is in at most one folder, so this is a SCALAR write: the durable
        `SET_WORK_FOLDER` family shares this boundary, and without that an
        offline device holding the old folder could never discover it had been
        overtaken.
        """
        wid = (work_id or "").strip()
        if not wid:
            raise ValueError("work_id is required")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            folder_sync.set_work_folder_on_conn(
                conn, wid, "" if folder_id is None else str(folder_id))

    def _normalize_bulk_ids(self, raw, *, kind: str) -> List[str]:
        if not isinstance(raw, list):
            raise BulkWorkError(f"{kind}_ids must be a JSON array.")
        if not raw:
            raise BulkWorkError("No files selected." if kind == "work" else "No tags selected.")
        if len(raw) > PRKS_BULK_WORK_MAX:
            raise BulkWorkError(
                "Too many files selected." if kind == "work" else "Too many tags selected."
            )
        out: List[str] = []
        seen = set()
        for item in raw:
            if not isinstance(item, str):
                raise BulkWorkError(f"{kind} IDs must be strings.")
            nid = item.strip()
            if not nid:
                raise BulkWorkError(f"{kind} IDs must be non-empty.")
            if nid in seen:
                continue
            seen.add(nid)
            out.append(nid)
        if not out:
            raise BulkWorkError("No files selected." if kind == "work" else "No tags selected.")
        return out

    def _bulk_ids_exist(self, conn, table: str, ids: List[str]) -> bool:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id FROM {table} WHERE id IN ({placeholders})",
            tuple(ids),
        ).fetchall()
        found = {row["id"] for row in rows}
        return found == set(ids)

    def bulk_update_works(self, data: dict) -> Dict[str, Any]:
        """Atomic organization of many works. One connection, one transaction.

        ``updated`` is the number of selected works processed, not relationship
        rows inserted or deleted. Tag add/remove are idempotent.
        """
        if not isinstance(data, dict):
            raise BulkWorkError("JSON object body required")
        action = data.get("action")
        if not isinstance(action, str) or action not in PRKS_BULK_WORK_ACTIONS:
            raise BulkWorkError("Unknown bulk action.")
        work_ids = self._normalize_bulk_ids(data.get("work_ids"), kind="work")

        status: Optional[str] = None
        folder_id: Optional[str] = None
        tag_ids: List[str] = []
        target_count = 1

        if action == "set_status":
            raw_status = data.get("status")
            if not isinstance(raw_status, str) or \
                    not work_metadata_sync.is_valid_field_value("status", raw_status):
                raise BulkWorkError("Invalid status.")
            status = raw_status
        elif action == "move_folder":
            if "folder_id" not in data:
                raise BulkWorkError("folder_id is required.")
            raw_folder = data.get("folder_id")
            if raw_folder is None:
                folder_id = None
            elif isinstance(raw_folder, str):
                stripped = raw_folder.strip()
                if not stripped:
                    raise BulkWorkError("folder_id is invalid.")
                folder_id = stripped
            else:
                raise BulkWorkError("folder_id must be a string or null.")
        else:
            tag_ids = self._normalize_bulk_ids(data.get("tag_ids"), kind="tag")
            target_count = len(tag_ids)

        conn = self.get_connection()
        try:
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")
            if not self._bulk_ids_exist(conn, "works", work_ids):
                raise BulkWorkError("One or more selected files no longer exist.", 404)
            if action == "move_folder" and folder_id:
                row = conn.execute("SELECT id FROM folders WHERE id = ?", (folder_id,)).fetchone()
                if not row:
                    raise BulkWorkError("Folder not found.", 404)
            if action in ("add_tags", "remove_tags"):
                if not self._bulk_ids_exist(conn, "tags", tag_ids):
                    raise BulkWorkError("One or more selected tags no longer exist.", 404)

            if action == "set_status":
                # The SAME revision-aware boundary PATCH and the sync handler
                # use, inside this transaction -- exactly like the Tag branch
                # below. A bulk action that wrote the column directly would
                # change the value while leaving the revision where it was, so
                # a device holding the pre-bulk Status would reconnect,
                # compare equal revisions, believe itself current and
                # overwrite the newer value. Revisions record CANONICAL
                # history; there is no such thing as a mutation path exempt
                # from it. Only Works whose Status actually changes advance.
                for wid in work_ids:
                    work_metadata_sync.set_field_on_conn(conn, wid, "status", status)
            elif action == "move_folder":
                conn.executemany(
                    "DELETE FROM folder_files WHERE work_id = ?",
                    [(wid,) for wid in work_ids],
                )
                if folder_id:
                    conn.executemany(
                        "INSERT INTO folder_files (folder_id, work_id) VALUES (?, ?)",
                        [(folder_id, wid) for wid in work_ids],
                    )
            else:
                for wid in work_ids:
                    for tid in tag_ids:
                        work_tag_sync.set_state(conn, wid, tid, action == "add_tags")

            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

        return {
            "status": "updated",
            "action": action,
            "requested": len(work_ids),
            "updated": len(work_ids),
            "target_count": target_count,
        }

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
    def insert_person_on_conn(
        self,
        conn,
        person_id: str,
        first_name: str = "",
        last_name: str = "",
        aliases: str = "",
        about: str = "",
        image_url: str = "",
        link_wikipedia: str = "",
        link_stanford_encyclopedia: str = "",
        link_iep: str = "",
        links_other: str = "",
        birth_date: str = "",
        death_date: str = "",
    ) -> None:
        conn.execute(
            """
            INSERT INTO persons (id, first_name, last_name, aliases, about,
                image_url, link_wikipedia, link_stanford_encyclopedia, link_iep, links_other,
                birth_date, death_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                person_id,
                first_name,
                last_name if last_name is not None else "",
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
        person_id: str | None = None,
    ) -> str:
        if person_id is None:
            person_id = self.generate_id("P")
        elif not is_distributed(person_id, "P"):
            raise ValueError("invalid person id")
        with self.connection() as conn:
            self.insert_person_on_conn(
                conn,
                person_id,
                first_name=first_name,
                last_name=last_name,
                aliases=aliases,
                about=about,
                image_url=image_url,
                link_wikipedia=link_wikipedia,
                link_stanford_encyclopedia=link_stanford_encyclopedia,
                link_iep=link_iep,
                links_other=links_other,
                birth_date=birth_date,
                death_date=death_date,
            )
        return person_id

    PERSON_METADATA_FIELDS = frozenset(
        {
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
    )

    def update_person_metadata(self, person_id: str, fields: dict):
        """Write profile fields through the SAME boundary the sync handler uses.

        Revisions record CANONICAL history, not sync-endpoint history. An
        ordinary online PATCH that changes a profile field has to advance that
        field's revision, or an offline device holding the old value has no way
        to discover it was overtaken -- and would overwrite it believing itself
        current. Only fields whose value actually changes advance, and the whole
        edit commits as one transaction so a value can never be stored without
        its revision.
        """
        updates = {k: v for k, v in fields.items() if k in self.PERSON_METADATA_FIELDS}
        if not updates:
            return
        # One rule, whichever path a value arrives by. If PATCH accepted an
        # image_url the durable queue would refuse, the same edit would be
        # savable online and impossible offline -- the split contract that
        # moving a field to local-first exists to remove.
        for field, value in updates.items():
            if not person_metadata_sync.is_valid_field_value(field, _person_wire(value)):
                raise ValueError("%s is not a valid value for %s" % (value, field))
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for field, value in updates.items():
                person_metadata_sync.set_field_on_conn(conn, person_id, field, _person_wire(value))

    def append_person_alias_if_new(self, person_id: str, alias: str) -> bool:
        """Append alias to persons.aliases when not already present (case-insensitive)."""
        a = (alias or "").strip()
        if not a:
            return False
        rows = self.execute_query("SELECT aliases FROM persons WHERE id = ?", (person_id,))
        if not rows:
            return False
        existing = (rows[0].get("aliases") or "").strip()
        parts = [x.strip() for x in existing.split(",") if x.strip()]
        if any(p.lower() == a.lower() for p in parts):
            return False
        canonical_rows = self.execute_query(
            "SELECT first_name, last_name FROM persons WHERE id = ?", (person_id,)
        )
        if canonical_rows:
            fn = (canonical_rows[0].get("first_name") or "").strip()
            ln = (canonical_rows[0].get("last_name") or "").strip()
            canonical = f"{fn} {ln}".strip() if fn or ln else ""
            if canonical and canonical.lower() == a.lower():
                return False
        parts.append(a)
        self.execute_query(
            "UPDATE persons SET aliases = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (", ".join(parts), person_id),
        )
        return True
    
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
        pex = _prks_sql_work_summary_person_extras("w")
        query = f"""
        SELECT w.*, r.role_type, r.order_index, r.credit_name, {pex}
        FROM roles r
        JOIN works w ON r.work_id = w.id
        WHERE r.person_id = ?
        ORDER BY r.order_index ASC, r.rowid ASC
        """
        person["works"] = list(self.execute_query(query, (person_id,)))
        finish_work_summary_rows(person["works"], self.storage.pdfs_dir)
        person["groups"] = self.get_groups_for_person(person_id)
        return person

    def delete_person_if_unlinked(self, person_id: str) -> None:
        """The ordinary endpoint's deletion, through the shared boundary.

        The durable `DELETE_PERSON` family uses the same one, so a Person can
        never be removed without the memberships that named them advancing
        their own revisions.
        """
        pid = (person_id or "").strip()
        if not pid:
            raise ValueError("Person not found.")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM persons WHERE id = ?", (pid,)).fetchone() is None:
                raise ValueError("Person not found.")
            deleted, linked = person_sync.delete_person_on_conn(conn, pid)
            if linked:
                raise ValueError("Cannot delete person with linked works.")
            if not deleted:
                raise ValueError("Person not found.")
        prks_delete_person_image_cache(pid, self.storage.people_dir)

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

    # Parent resolution and the descendant/cycle walk deliberately live only
    # inside the transaction-aware `_resolve_group_parent` / `_update_person_group`
    # helpers below. A standalone, auto-committing version of either is what let
    # a failed Group request leave an orphan typed parent behind, so do not
    # reintroduce one -- and do not move parent resolution back into server.py.

    def add_person_group(
        self, name: str, parent_id: Optional[str] = None, description: str = ""
    ) -> str:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self._insert_person_group(conn, name, parent_id, description)

    def add_person_group_with_parent_options(
        self,
        name: str,
        parent_id: Optional[str] = None,
        parent_name: Optional[str] = None,
        description: str = "",
    ) -> str:
        """Resolve/create the parent and child in one canonical transaction."""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            n = (name or "").strip()
            pn = (parent_name or "").strip()
            if not parent_id and pn and pn.lower() == n.lower():
                raise ValueError("Parent group cannot have the same name as the new group.")
            pid = parent_id or (self._resolve_group_parent(conn, pn) if pn else None)
            return self._insert_person_group(conn, n, pid, description)

    def _insert_person_group(self, conn, name, parent_id=None, description="") -> str:
        """Construction, through the same boundary the sync handler uses.

        Construction is not mutation: a group created with a name and a parent
        has not "changed" twice, so no field revision is advanced here and
        every field starts at 0.
        """
        return person_group_sync.insert_group_on_conn(
            conn, self.generate_id("PG"), name, parent_id or "", description or "")

    def _resolve_group_parent(self, conn, name) -> str:
        row = conn.execute(
            "SELECT id FROM person_groups WHERE LOWER(name) = LOWER(?) LIMIT 1", (name,)
        ).fetchone()
        return row["id"] if row else self._insert_person_group(conn, name)

    def update_person_group(self, group_id: str, fields: dict) -> None:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM person_groups WHERE id = ?", (group_id,)).fetchone():
                raise ValueError("Group not found.")
            fields = dict(fields)
            if "parent_name" in fields:
                raw = fields.pop("parent_name")
                pn = str(raw).strip() if raw is not None else ""
                fields["parent_id"] = self._resolve_group_parent(conn, pn) if pn else None
            self._update_person_group(conn, group_id, fields)

    def _update_person_group(self, conn, group_id, fields) -> None:
        """Every editable column, through the revision-aware field writer.

        The ordinary PATCH and the durable operation share one boundary, so a
        group can never change without its revision: an offline device holding
        the old value would otherwise have no way to discover it had been
        overtaken, and would overwrite a decision it never saw. Name
        uniqueness, parent existence and the cycle walk live there too, so the
        two paths cannot drift apart on what is legal.
        """
        for field in person_group_sync.FIELDS:
            if field not in fields:
                continue
            value = fields[field]
            person_group_sync.set_field_on_conn(
                conn, group_id, field, "" if value in (None, False) else str(value))

    def delete_person_group(self, group_id: str) -> None:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not person_group_sync.delete_group_on_conn(conn, group_id):
                raise ValueError("Group not found.")

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
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ok_p = conn.execute("SELECT 1 FROM persons WHERE id = ?", (person_id,)).fetchone()
            ok_g = conn.execute(
                "SELECT 1 FROM person_groups WHERE id = ?", (group_id,)).fetchone()
            if not ok_p or not ok_g:
                raise ValueError("Person or group not found.")
            person_group_sync.set_member_on_conn(conn, group_id, person_id, True)

    def remove_person_from_group(self, person_id: str, group_id: str) -> None:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            person_group_sync.set_member_on_conn(conn, group_id, person_id, False)

    def get_folder_sync_state(self, folder_id: str) -> Optional[dict]:
        """Field revisions for one folder."""
        with self.connection() as conn:
            conn.execute("BEGIN")
            return folder_sync.get_folder_state_on_conn(conn, folder_id)

    def get_work_folder_state(self, work_id: str) -> Optional[dict]:
        """Which folder a Work is in, and the revision that says so."""
        with self.connection() as conn:
            conn.execute("BEGIN")
            return folder_sync.get_work_folder_state_on_conn(conn, work_id)

    def get_person_group_sync_state(self, group_id: str) -> Optional[dict]:
        """Field revisions and membership revisions for one group."""
        with self.connection() as conn:
            conn.execute("BEGIN")
            return person_group_sync.get_group_state_on_conn(conn, group_id)

    def get_person_groups_state(self, person_id: str) -> Optional[dict]:
        """The same membership bookkeeping, keyed by PERSON.

        Membership is edited from both ends, and each end needs the revisions
        for the pairs it can change.
        """
        with self.connection() as conn:
            conn.execute("BEGIN")
            return person_group_sync.get_person_group_state_on_conn(conn, person_id)

    def get_person_metadata_state(self, person_id: str) -> Optional[dict]:
        """Synchronization state for the supported Person fields.

        Its own endpoint rather than extra keys on the Person detail, for the
        same reason the Work equivalent is: revisions are synchronization
        bookkeeping, and every consumer of a Person would otherwise pay for
        them and re-cache on every change.
        """
        with self.connection() as conn:
            conn.execute("BEGIN")
            return person_metadata_sync.get_field_state_on_conn(conn, person_id)

    def update_person_profile(self, person_id: str, fields: dict, group_ids=None) -> None:
        """Atomically update Person metadata and (optionally) group memberships.

        Applying metadata and memberships as two separate committed statements
        let an unknown group id return HTTP 400 *after* the metadata had already
        been written, so a failed request could still have changed canonical
        state. Offline coherence depends on the opposite guarantee -- a failed
        canonical request must leave the previous cache eligible -- so when
        `group_ids` is supplied both parts share one transaction and roll back
        together.
        """
        updates = {k: v for k, v in (fields or {}).items() if k in self.PERSON_METADATA_FIELDS}
        if group_ids is None:
            # Metadata-only PATCH keeps its original single-statement behavior.
            self.update_person_metadata(person_id, updates)
            return
        if not isinstance(group_ids, list):
            raise ValueError("group_ids must be a JSON array")
        for field, value in updates.items():
            if not person_metadata_sync.is_valid_field_value(field, _person_wire(value)):
                raise ValueError("%s is not a valid value for %s" % (value, field))
        with self.connection() as conn:
            if not conn.execute("SELECT 1 FROM persons WHERE id = ?", (person_id,)).fetchone():
                raise ValueError("Person not found.")
            seen = set()
            clean: List[str] = []
            for gid in group_ids:
                if not gid or gid in seen:
                    continue
                seen.add(gid)
                # Validate every group BEFORE any write, so a bad id cannot
                # leave half the profile updated.
                if not conn.execute(
                    "SELECT 1 FROM person_groups WHERE id = ?", (gid,)
                ).fetchone():
                    raise ValueError(f"Unknown group id: {gid}")
                clean.append(gid)
            for field, value in updates.items():
                # The same field writer as the metadata-only path and as the
                # synchronization handler, so memberships and metadata share
                # one transaction WITHOUT the profile half bypassing the
                # revision model that offline devices depend on.
                person_metadata_sync.set_field_on_conn(conn, person_id, field, _person_wire(value))
            self._replace_person_group_memberships(conn, person_id, clean)
            conn.commit()

    @staticmethod
    def _replace_person_group_memberships(conn, person_id, group_ids) -> None:
        """Replace the set by DIFFING it, not by deleting and reinserting.

        Every pair goes through the revision-aware boundary, and a pair that
        did not change advances nothing: a revision records the relationship
        actually changing, and inflating it would manufacture staleness for
        every device that already holds the current membership.
        """
        desired = set(group_ids)
        current = {row[0] for row in conn.execute(
            "SELECT group_id FROM person_group_members WHERE person_id = ?", (person_id,))}
        for gid in sorted(current - desired):
            person_group_sync.set_member_on_conn(conn, gid, person_id, False)
        for gid in sorted(desired - current):
            person_group_sync.set_member_on_conn(conn, gid, person_id, True)

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
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._replace_person_group_memberships(conn, person_id, clean)
            conn.commit()

    # --- Roles (Linking) ---
    def has_work_role(self, person_id: str, work_id: str, role_type: str) -> bool:
        rows = self.execute_query(
            """
            SELECT 1 FROM roles
            WHERE person_id = ? AND work_id = ? AND role_type = ?
            LIMIT 1
            """,
            (person_id, work_id, role_type),
        )
        return bool(rows)

    def add_role(
        self,
        person_id: str,
        work_id: str,
        role_type: str,
        order_index: int = 0,
        credit_name: str = "",
    ):
        work_role_sync.validate_role_type(role_type)
        if self.has_work_role(person_id, work_id, role_type):
            raise ValueError(
                f"This person is already linked to this file as {role_type}."
            )
        # MUTATION of an existing Work. One revision-aware boundary, whichever
        # path the change arrives by: a relationship written without advancing
        # its revision is invisible to every offline device, which would then
        # overwrite it believing itself current.
        #
        # `order_index` is deliberately ignored here and assigned by the server
        # as "append after what is already on this Work". A caller's index
        # would be a claim about placement it cannot coordinate with other
        # devices. Construction, where the caller IS the authority on author
        # order, goes through `insert_initial_role()` instead.
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            work_role_sync.set_role_state(conn, work_id, person_id, role_type, True,
                                          credit_name=credit_name)

    def insert_initial_role(self, work_id: str, person_id: str, role_type: str,
                            order_index: int = 0, credit_name: str = "") -> None:
        """A relationship a NEW Work is born with. Creates no revision."""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            work_role_sync.insert_initial_role(
                conn, work_id, person_id, role_type, order_index=order_index,
                credit_name=credit_name)

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
        SELECT p.*, r.role_type, r.order_index, r.credit_name
        FROM roles r
        JOIN persons p ON r.person_id = p.id
        WHERE r.work_id = ?
        ORDER BY r.order_index ASC, r.rowid ASC
        """
        return self.execute_query(query, (work_id,))

    def update_role_credit_name(
        self,
        work_id: str,
        person_id: str,
        role_type: str,
        order_index: int,
        credit_name: str,
    ) -> bool:
        """Update credit_name on one role row. Empty string clears override.

        `order_index` is accepted for body compatibility and deliberately NOT
        matched on: at most one row exists per `(person, work, role_type)`, and
        a caller passing a stale index would otherwise silently update nothing.
        The same revision-aware boundary the durable operation uses, so a
        credit change made here is visible to every offline device.

        Returns False when the triple does not exist, so PATCH keeps its 404
        rather than creating a relationship the caller did not ask to add.
        """
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if work_role_sync.current_state(conn, work_id, person_id, role_type) is None:
                return False
            work_role_sync.set_role_state(
                conn, work_id, person_id, role_type, True, credit_name=credit_name)
            return True

    def delete_work_role(self, work_id: str, person_id: str, role_type: str,
                         order_index: int = 0) -> bool:
        """Remove one Work-Person role. Returns True if a row was deleted.

        `order_index` is accepted for URL compatibility and deliberately NOT
        matched on: at most one row exists per `(person, work, role_type)`, and
        a caller passing a stale index would otherwise silently remove nothing.
        The same revision-aware boundary the durable operation uses, so a
        removal made here is visible to every offline device.
        """
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return work_role_sync.set_role_state(conn, work_id, person_id, role_type, False)

    # --- Concepts & Arguments ---
    def add_concept(self, name: str, description: str = "") -> str:
        concept_id = self.generate_id("C")
        query = "INSERT INTO concepts (id, name, description) VALUES (?, ?, ?)"
        self.execute_query(query, (concept_id, name, description))
        return concept_id

    def add_argument(self, work_id: str, premise: str, conclusion: str) -> str:
        from backend.db_migrations import _legacy_argument_main_text

        arg_id = self.generate_id("A")
        short = arg_id[2:8] if len(arg_id) > 2 else arg_id
        main_text = _legacy_argument_main_text(premise or "", conclusion or "")
        self.execute_query(
            "INSERT INTO arguments (id, name, kind, main_text) VALUES (?, ?, 'argument', ?)",
            (arg_id, "Argument " + short, main_text),
        )
        if work_id:
            self.execute_query(
                """
                INSERT INTO argument_sources (argument_id, work_id, pages, order_index)
                VALUES (?, ?, '', 0)
                """,
                (arg_id, work_id),
            )
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
        with self.connection() as conn:
            # The same construction boundary the durable CREATE_TAG uses, so a
            # Tag can never exist without the lifecycle row every later answer
            # about it is read from.
            tag_sync.insert_tag_on_conn(conn, tag_id, raw, color)
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

    def merge_tags_into_on_conn(self, conn, source_tag_id: str, target_tag_id: str) -> Dict[str, Any]:
        """Identity transform on the caller's transaction (sync + HTTP share this)."""
        source = (source_tag_id or "").strip()
        target = (target_tag_id or "").strip()
        if not source or not target:
            raise ValueError("source_tag_id and target_tag_id are required")
        if source == target:
            raise ValueError("cannot merge a tag into itself")

        srow = conn.execute("SELECT id, name FROM tags WHERE id = ?", (source,)).fetchone()
        trow = conn.execute("SELECT id, name FROM tags WHERE id = ?", (target,)).fetchone()
        if not srow or not trow:
            raise ValueError("tag not found")

        source_name = (srow["name"] or "").strip()
        target_name = (trow["name"] or "").strip()

        # Everything linked to the SOURCE has its rendered tag list change:
        # the source name disappears, whether or not the target was already
        # present. Entities linked only to the target are untouched.
        affected = self._entities_linked_to_tag_on_conn(conn, source)

        for wid in affected["affected_work_ids"]:
            work_tag_sync.set_state(conn, wid, target, True)
            work_tag_sync.set_state(conn, wid, source, False)
        for fid in affected["affected_folder_ids"]:
            folder_tag_sync.set_state(conn, fid, target, True)
            folder_tag_sync.set_state(conn, fid, source, False)
        conn.execute(
            "UPDATE sync_tag_lifecycle SET state = 'merged', target_tag_id = ?, "
            "changed_at = CURRENT_TIMESTAMP WHERE tag_id = ? OR "
            "(state = 'merged' AND target_tag_id = ?)",
            (target, source, source),
        )

        # Staged Processing Files reference Tags too. A merge means
        # "replace S with T everywhere", so these links move like the
        # others -- without this the source row is deleted below and
        # `processing_file_tags.tag_id ON DELETE CASCADE` destroys the
        # relationship, leaving the staged file with neither tag.
        conn.execute(
            "INSERT OR IGNORE INTO processing_file_tags (processing_file_id, tag_id) "
            "SELECT processing_file_id, ? FROM processing_file_tags WHERE tag_id = ?",
            (target, source),
        )
        conn.execute("DELETE FROM processing_file_tags WHERE tag_id = ?", (source,))

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

        return {"canonical_tag_id": target, "canonical_name": target_name, **affected}

    def merge_tags_into(self, source_tag_id: str, target_tag_id: str) -> Dict[str, Any]:
        """Move all links from source tag to target, drop source row, add source name as alias of target."""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = self.merge_tags_into_on_conn(conn, source_tag_id, target_tag_id)
            conn.commit()
        return result

    def delete_tag_alias(self, tag_id: str, alias: str) -> bool:
        al = (alias or "").strip()
        if not al:
            return False
        with self.connection() as conn:
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
        with self.connection() as conn:
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

    def _entities_linked_to_tag_on_conn(self, conn, tag_id: str) -> Dict[str, List[str]]:
        """Works and folders whose rendered tag list contains this tag.

        Offline coherence needs these from the canonical boundary: a cached
        Work detail embeds `work.tags[]` and a cached Folder detail
        `folder.tags[]`, so deleting or merging a tag stales exactly these
        entities. Collected from the server so the answer is right regardless
        of which route, tab or UI initiated the mutation -- and right even
        when the client never loaded those relationships.
        """
        works = [
            r["work_id"]
            for r in conn.execute(
                "SELECT work_id FROM work_tags WHERE tag_id = ?", (tag_id,)
            ).fetchall()
            if r["work_id"]
        ]
        folders = [
            r["folder_id"]
            for r in conn.execute(
                "SELECT folder_id FROM folder_tags WHERE tag_id = ?", (tag_id,)
            ).fetchall()
            if r["folder_id"]
        ]
        option_works = set(works)
        for row in conn.execute("SELECT scope_id FROM sync_entity_revisions WHERE scope_type = 'work-tag'"):
            pair = json.loads(row["scope_id"])
            if pair[1] == tag_id:
                option_works.add(pair[0])
        option_folders = set(folders)
        for row in conn.execute("SELECT scope_id FROM sync_entity_revisions WHERE scope_type = 'folder-tag'"):
            pair = json.loads(row["scope_id"])
            if pair[1] == tag_id:
                option_folders.add(pair[0])
        return {"affected_work_ids": works, "affected_folder_ids": folders,
                "affected_tag_options_work_ids": sorted(option_works),
                "affected_tag_options_folder_ids": sorted(option_folders)}

    def delete_tag(self, tag_id: str) -> Dict[str, Any]:
        """Explicitly destroy a Tag. Relationships cascade.

        **Tag identity is persistent.** This method and merge_tags_into() are
        the ONLY operations that may destroy or transform it. PRKS used to
        garbage-collect an "unused" Tag during ordinary relationship edits --
        removing a tag from a Work or Folder, deleting a Work or Folder, or a
        bulk tag removal -- which meant a Tag vanished because of an edit the
        user never framed as deleting it.

        That was wrong for two independent reasons. It made Tags temporary
        values whose existence depended on current usage, rather than a
        reusable vocabulary; and the "unused" test only consulted `work_tags`
        and `folder_tags`, never `processing_file_tags`, so a Tag still
        attached to a staged Processing File could be deleted and that
        relationship silently destroyed by the FK cascade.

        It is also hostile to synchronization: an offline device holding a Tag
        id would find it gone after another device merely removed the last
        relationship, turning ordinary edits into ENTITY_NOT_FOUND conflicts.

        Unused Tags now simply remain in the catalog. If cleanup is ever
        wanted it must be an explicit, user-initiated action (an "unused tags"
        list with its own delete), never garbage collection during an
        unrelated operation.
        """
        tid = (tag_id or "").strip()
        if not tid:
            raise ValueError("tag_id is required")
        # Collect BEFORE the delete: the FK cascade removes the link rows, so
        # afterwards there is nothing left to report.
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id FROM tags WHERE id = ?", (tid,)).fetchone()
            if not row:
                raise ValueError("tag not found")
            affected = self._entities_linked_to_tag_on_conn(conn, tid)
            for wid in affected["affected_work_ids"]:
                work_tag_sync.set_state(conn, wid, tid, False)
            for fid in affected["affected_folder_ids"]:
                folder_tag_sync.set_state(conn, fid, tid, False)
            conn.execute("UPDATE sync_tag_lifecycle SET state = 'deleted', target_tag_id = NULL, changed_at = CURRENT_TIMESTAMP WHERE tag_id = ?", (tid,))
            conn.execute("DELETE FROM tags WHERE id = ?", (tid,))
        return {"status": "deleted", **affected}

    def add_tag_to_work(self, work_id: str, tag_id: str):
        with self.connection() as conn:
            work_tag_sync.set_state(conn, work_id, tag_id, True)

    def remove_tag_from_work(self, work_id: str, tag_id: str):
        with self.connection() as conn:
            work_tag_sync.set_state(conn, work_id, tag_id, False)

    def get_work_tag_options(self, work_id: str):
        with self.connection() as conn:
            conn.execute("BEGIN")
            return work_tag_sync.tag_options(conn, work_id)

    def add_tag_to_folder(self, folder_id: str, tag_id: str):
        with self.connection() as conn:
            conn.execute("BEGIN")
            folder_tag_sync.set_state(conn, folder_id, tag_id, True)

    def remove_tag_from_folder(self, folder_id: str, tag_id: str):
        # Relationship only; see remove_tag_from_work.
        with self.connection() as conn:
            conn.execute("BEGIN")
            folder_tag_sync.set_state(conn, folder_id, tag_id, False)

    def get_folder_tag_options(self, folder_id: str):
        with self.connection() as conn:
            conn.execute("BEGIN")
            return folder_tag_sync.tag_options(conn, folder_id)

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
        # Plain str.split: the per-segment strip below already absorbs the
        # whitespace a r"\s*;\s*" pattern would have consumed, without its
        # super-linear backtracking on long whitespace runs.
        for seg in s.split(";"):
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
            credit = (p.get("credit_name") or "").strip()
            if credit:
                out.append(credit)
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
