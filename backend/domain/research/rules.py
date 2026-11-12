"""Research Network validation shared by every Library backend (PG-C2 #641).

Field limits, the controlled ``ResearchError`` and the text, name and pages
rules Concepts, Positions and Arguments are checked by. Pure: no store.
Moved from ``backend/research_network.py``.
"""

from __future__ import annotations

import re

from backend.research_markup import canonical_concept_name

CONCEPT_NAME_MAX = 160
CONCEPT_ALIAS_MAX = 160
CONCEPT_DEFINITION_MAX = 100_000
POSITION_NAME_MAX = 240
POSITION_DESCRIPTION_MAX = 100_000
ARGUMENT_NAME_MAX = 200
ARGUMENT_TEXT_MAX = 100_000
ARGUMENT_PAGES_MAX = 100
ARGUMENT_KINDS = frozenset({"argument", "stance"})
# Characters of note text shown on each side of a mention in a backlink.
_SNIPPET_RADIUS = 80

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ResearchError(ValueError):
    """Controlled research-network failure."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def validate_kind(kind) -> str:
    """An Argument's kind, trimmed: ``argument`` or ``stance``."""
    if not isinstance(kind, str) or kind.strip() not in ARGUMENT_KINDS:
        raise ResearchError("invalid_kind", "Kind must be argument or stance.")
    return kind.strip()


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


def research_note_refusal(text_content) -> ResearchError:
    """The ``invalid_text`` refusal of a body ``work_note_sync.validate_text`` refused.

    Shared with the PostgreSQL facade's ``save_work_notes``, so both
    backends refuse a note with the same message.
    """
    if not isinstance(text_content, str):
        return ResearchError("invalid_text", "Research notes must be a string.")
    if _CONTROL_RE.search(text_content):
        return ResearchError("invalid_text", "Research notes contain invalid characters.")
    return ResearchError("invalid_text", "Research notes are too long.")


def note_snippet(text: str, start: int, end: int) -> str:
    """The text around a mention, as a backlink shows it (both Library backends)."""
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
