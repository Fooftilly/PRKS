"""The BibLaTeX entry formatter shared by every Library backend (PG-C2 #641).

``format_bibtex`` turns a citation record, its credits and the Library's
export profile into one entry. It reads no store, so equal inputs give
byte-identical text whichever backend read them
(``backend.domain.works.citation`` reads them on PostgreSQL). Moved from
``PRKSDatabase`` in ``backend/db_manager.py``.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

from backend.domain.works.rules import normalize_doc_type

# Characters a BibTeX entry key may keep: Unicode word characters, "-", "."
# and ":". Everything else either ends the key or is read as entry structure.
_CITE_KEY_UNSAFE_RE = re.compile(r"[^\w\-.:]")


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


def _bibtex_cite_key_part(text: Any) -> str:
    """``text`` with every character an entry key cannot safely hold removed.

    A key ends at whitespace or a comma, braces and quotes delimit the
    entry, and ``\\ # % ' ( ) = @ ~`` are read as structure by BibTeX,
    Biber or ``\\cite``. Unicode letters and digits (NFC, so a
    decomposed accent is kept), ``_``, ``-``, ``.`` and ``:`` remain.
    """
    return _CITE_KEY_UNSAFE_RE.sub("", unicodedata.normalize("NFC", str(text or "")))


def _cite_key_part_from_credit(credit: str) -> str:
    """Key part of one free-text Person credit: the family name of
    ``Family, Given``, else the last word of ``Given Family``; "" for none."""
    if "," in credit:
        words = credit.split(",", 1)[0].split()
        return _bibtex_cite_key_part(words[0]) if words else ""
    words = credit.split()
    return _bibtex_cite_key_part(words[-1]) if words else ""


def _biblatex_cite_key_part_from_person(p: dict) -> str:
    """First segment for cite key: prefer family name, else given / mononym, else
    the credit name the role exports under; "" when none is usable."""
    for name in (p.get("last_name"), p.get("first_name")):
        words = str(name or "").split()
        part = _bibtex_cite_key_part(words[0]) if words else ""
        if part:
            return part
    return _cite_key_part_from_credit(str(p.get("credit_name") or "").strip())


def _biblatex_role_name(p: dict) -> str:
    """The name a role row exports: its credit name, else the Person's; "" for none."""
    credit = (p.get("credit_name") or "").strip()
    if credit:
        return credit
    return _format_biblatex_person_name(p.get("last_name"), p.get("first_name")) or ""


def _biblatex_names_for_role(roles: List[dict], role_type: str) -> List[str]:
    out: List[str] = []
    for p in roles:
        if p.get("role_type") != role_type:
            continue
        nm = _biblatex_role_name(p)
        if nm:
            out.append(nm)
    return out


def _bibtex_escape_field(value: Any) -> str:
    """Escape characters that have special meaning inside BibTeX braces."""
    s = str(value) if value is not None else ""
    # Percent signs introduce BibTeX comments inside fields; braces must be balanced.
    s = s.replace('%', r'\%')
    # BibTeX counts a brace whatever precedes it, so \{ and \} are only
    # safe in pairs: a lone "}" would end the field and a lone "{" swallow
    # the rest of the entry. A brace without its partner is written as the
    # LaTeX symbol, whose own braces balance.
    opened: List[int] = []
    unpaired: set[int] = set()
    for index, char in enumerate(s):
        if char == "{":
            opened.append(index)
        elif char == "}":
            if opened:
                opened.pop()
            else:
                unpaired.add(index)
    unpaired.update(opened)
    out: List[str] = []
    for index, char in enumerate(s):
        if char not in "{}":
            out.append(char)
        elif index in unpaired:
            out.append(r"{\textbraceleft}" if char == "{" else r"{\textbraceright}")
        else:
            out.append("\\" + char)
    return "".join(out)


def format_bibtex(work: dict, roles: List[dict], export_fields: Dict[str, bool],
                  *, video: bool) -> str:
    """One BibLaTeX entry from a citation record, its credits and the export profile.

    Database-independent, so both backends export byte-identical text:
    ``work`` is the Work-shaped citation record (``citation_record``),
    ``roles`` the citation Manifestation's credits as Person rows with
    ``role_type`` and ``credit_name``, in role order, and
    ``export_fields`` the Library's ``bibtex_export_fields`` profile.

    ``video`` is True when the citation Manifestation's primary Asset is
    an external stream: the stored source identity, not an inference from
    ``source_kind``, ``source_url`` or ``file_path``. A PDF's
    ``source_url`` is provenance, and a fileless PDF reads back on
    PostgreSQL with no ``source_kind`` at all, so neither may make a Work
    a video here (#690).
    """
    # Roles sorted by order_index, then rowid (stable order when order_index ties).
    linked_authors = _biblatex_names_for_role(roles, "Author")
    editors = _biblatex_names_for_role(roles, "Editor")
    translators = _biblatex_names_for_role(roles, "Translator")
    introductions = _biblatex_names_for_role(roles, "Introduction")
    forewords = _biblatex_names_for_role(roles, "Foreword")
    afterwords = _biblatex_names_for_role(roles, "Afterword")

    # The visible Work credit line falls back to Author (text) when no
    # linked Author exists. Preserve that credit in copied citations too.
    text_credit = str(work.get("author_text") or "").strip()
    # On a video, Author (text) is a channel/organization name, not
    # semicolon-delimited Person names. BibLaTeX needs it braced as one.
    corporate_credit = bool(text_credit) and not linked_authors and video
    text_authors = (
        [text_credit] if corporate_credit
        else [name.strip() for name in text_credit.split(";") if name.strip()]
    )
    author_names = linked_authors or text_authors

    # Year: prefer dedicated year field, fall back to published_date prefix
    year = work.get('year') or (work.get('published_date', '')[:4] if work.get('published_date') else None)

    # The key follows the first credit the author field shows: an Author
    # row that renders no name is skipped there, so it is skipped here,
    # and with none left both fall back to Author (text).
    first_author_person = next(
        (p for p in roles if p["role_type"] == "Author" and _biblatex_role_name(p)), None)
    if first_author_person:
        first_author_last = _biblatex_cite_key_part_from_person(first_author_person) or "Unknown"
    elif text_authors:
        first_author_last = (
            _bibtex_cite_key_part(text_authors[0].split()[0]) if corporate_credit
            else _cite_key_part_from_credit(text_authors[0])
        ) or "Unknown"
    else:
        first_author_last = "Unknown"
    cite_key = first_author_last + (_bibtex_cite_key_part(year) or "UnknownYear")

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

    esc = _bibtex_escape_field

    def names(people: List[str]) -> str:
        # Each name on its own, so a brace in one cannot pair with a brace
        # in the next and hide the "and" between them.
        return " and ".join(esc(name) for name in people)
    bf = export_fields

    def _export(key: str) -> bool:
        return bool(bf.get(key, True))

    bibtex = f"@{entry_type}{{{cite_key},\n"
    bibtex += f"  title = {{{esc(work['title'])}}},\n"
    if author_names and _export("author"):
        author_value = names(author_names)
        if corporate_credit:
            author_value = "{" + author_value + "}"  # BibLaTeX corporate name
        bibtex += f"  author = {{{author_value}}},\n"
    if editors and _export("editor"):
        bibtex += f"  editor = {{{names(editors)}}},\n"
    if translators and _export("translator"):
        bibtex += f"  translator = {{{names(translators)}}},\n"
    if introductions and _export("introduction"):
        bibtex += f"  introduction = {{{names(introductions)}}},\n"
    if forewords and _export("foreword"):
        bibtex += f"  foreword = {{{names(forewords)}}},\n"
    if afterwords and _export("afterword"):
        bibtex += f"  afterword = {{{names(afterwords)}}},\n"
    if year and _export("year"):
        bibtex += f"  year = {{{esc(year)}}},\n"
    if work.get('publisher') and _export("publisher"):
        bibtex += f"  publisher = {{{esc(work['publisher'])}}},\n"
    loc_fmt = _format_biblatex_location(work.get("location"))
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
