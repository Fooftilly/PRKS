"""Work rules shared by every Library backend (PG-C2 #641).

Bulk Work requests, wiki-link rendering, new-Work source kinds, document
types, Work summary rows and the deleted-Work record. These are pure: no
store, no transaction. They moved here from ``backend/db_manager.py`` so the
PostgreSQL Library uses them without importing the SQLite one.
"""

from __future__ import annotations

import contextlib
import html
import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from backend import work_metadata_sync, work_source_sync
from backend.managed_pdf_names import managed_pdf_filename, safe_pdf_path_under_dir
from backend.performance import clock_ns, record_counter, record_span

PRKS_BULK_WORK_ACTIONS = frozenset({"set_status", "move_folder", "add_tags", "remove_tags"})


PRKS_BULK_WORK_MAX = 500


class BulkWorkError(ValueError):
    """Controlled bulk-organization failure. http_status is 400 or 404."""

    def __init__(self, message: str, http_status: int = 400):
        super().__init__(message)
        self.http_status = http_status


BULK_WORKS_MISSING = "One or more selected files no longer exist."


BULK_FOLDER_MISSING = "Folder not found."


BULK_TAGS_MISSING = "One or more selected tags no longer exist."


@dataclass(frozen=True)
class BulkWorkRequest:
    """A validated ``POST /api/works/bulk`` body: ids stripped, deduplicated, in request order."""

    action: str
    work_ids: List[str]
    status: Optional[str] = None
    folder_id: Optional[str] = None  # None or "" clears the Folder (move_folder only)
    tag_ids: Tuple[str, ...] = ()

    def result(self) -> Dict[str, Any]:
        return {
            "status": "updated",
            "action": self.action,
            "requested": len(self.work_ids),
            "updated": len(self.work_ids),
            "target_count": len(self.tag_ids) if self.tag_ids else 1,
        }


def _normalize_bulk_ids(raw, *, kind: str) -> List[str]:
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


def parse_bulk_work_request(data: Any) -> BulkWorkRequest:
    """Validate a bulk body before any database is touched (both backends).

    Raises ``BulkWorkError`` (400) with the message the API returns.
    """
    if not isinstance(data, dict):
        raise BulkWorkError("JSON object body required")
    action = data.get("action")
    if not isinstance(action, str) or action not in PRKS_BULK_WORK_ACTIONS:
        raise BulkWorkError("Unknown bulk action.")
    work_ids = _normalize_bulk_ids(data.get("work_ids"), kind="work")

    if action == "set_status":
        raw_status = data.get("status")
        if not isinstance(raw_status, str) or \
                not work_metadata_sync.is_valid_field_value("status", raw_status):
            raise BulkWorkError("Invalid status.")
        return BulkWorkRequest(action, work_ids, status=raw_status)
    if action == "move_folder":
        if "folder_id" not in data:
            raise BulkWorkError("folder_id is required.")
        raw_folder = data.get("folder_id")
        if raw_folder is None:
            return BulkWorkRequest(action, work_ids, folder_id=None)
        if not isinstance(raw_folder, str):
            raise BulkWorkError("folder_id must be a string or null.")
        stripped = raw_folder.strip()
        if not stripped:
            raise BulkWorkError("folder_id is invalid.")
        return BulkWorkRequest(action, work_ids, folder_id=stripped)
    tag_ids = _normalize_bulk_ids(data.get("tag_ids"), kind="tag")
    return BulkWorkRequest(action, work_ids, tag_ids=tuple(tag_ids))


def wiki_link_spans(text: str) -> Iterator[Tuple[int, int, str]]:
    r"""``(start, end, inner)`` of each ``[[inner]]``, left to right.

    Exactly the matches of ``re.finditer(r'\[\[(.*?)\]\]', text)``: a marker
    opens at the first ``[[``, closes at the first ``]]`` after it, and holds
    no newline. That pattern retries from every ``[[`` it cannot close, so a
    note of many unclosed ``[[`` took quadratic time on every Work detail
    read; this scan finds the same spans in one pass.
    """
    pos = 0
    close = -1
    while True:
        start = text.find("[[", pos)
        if start < 0:
            return
        if close < start + 2:
            close = text.find("]]", start + 2)
            if close < 0:
                return
        newline = text.find("\n", start + 2, close)
        if newline >= 0:
            # No ``[[`` before this newline closes either.
            pos = newline + 1
            continue
        yield start, close + 2, text[start + 2:close]
        pos = close + 2


def render_wiki_links(
    text: str,
    resolve: Callable[[List[str]], Dict[str, Tuple[str, Any, Any]]],
) -> str:
    """``text`` with each ``[[target]]`` rendered as a link or an unresolved marker.

    Shared by both backends. ``resolve`` maps the distinct, trimmed targets
    (in first-occurrence order) to ``(kind, id, name)`` for those it found,
    ``kind`` being ``"works"`` or ``"people"``; it is not called when the
    text has no marker. Each distinct target is looked up once, so the cost
    follows the number of different targets, not of markers.
    """
    if not text:
        return ""
    spans = list(wiki_link_spans(text))
    targets = list(dict.fromkeys(inner.strip() for _, _, inner in spans))
    if not targets:
        return text
    resolved = resolve(targets)

    def replacement(inner: str) -> str:
        raw = inner.strip()
        hit = resolved.get(raw)
        if hit:
            kind, target_id, name = hit
            safe_name = html.escape(str(name or ""), quote=True)
            safe_id = html.escape(str(target_id or ""), quote=True)
            return f'<a href="#/{kind}/{safe_id}" class="wiki-link" style="color:var(--accent); text-decoration:none;">{safe_name}</a>'
        safe_raw = html.escape(raw, quote=True)
        return f'<span class="wiki-link-unresolved" style="color:#ef4444;">[[{safe_raw}]]</span>'

    out: List[str] = []
    last = 0
    for start, end, inner in spans:
        out.append(text[last:start])
        out.append(replacement(inner))
        last = end
    out.append(text[last:])
    return "".join(out)


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
            # Ownership identity, not the last '/'-separated piece: reporting
            # the size of some other managed PDF for a malformed stored path is
            # the same silent redirect the serving route now refuses.
            filename = managed_pdf_filename(str(row.get("file_path") or ""))
            if not filename:
                row["file_size_bytes"] = None
                continue
            path = safe_pdf_path_under_dir(pdfs_dir, filename)
            if not path or not os.path.isfile(path):
                row["file_size_bytes"] = None
                continue
            try:
                row["file_size_bytes"] = os.path.getsize(path)
                stated += 1
            except OSError:
                row["file_size_bytes"] = None
    finally:
        # Telemetry never fails the read it measures.
        with contextlib.suppress(Exception):
            record_span("pdf_file_stats", clock_ns() - t0)
            if examined:
                record_counter("pdf_file_stat_rows", examined)
            if stated:
                record_counter("pdf_file_stat_files", stated)


@dataclass(frozen=True)
class DeletedWorkRecord:
    work_id: str
    file_path: str
    managed_pdf_still_referenced: bool
