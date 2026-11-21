"""The citation styles PRKS formats with (Citation V1, #41).

A style id is the file name, without ``.csl``, of a style PRKS ships under
``frontend/vendor/csl/`` (the Citation Style Language styles repository's
names). Formatting happens in the browser with citeproc-js; the server only
stores which style to use:

- the Library's default, the ``citation_style`` library setting
  (``backend.domain.settings.app_settings``), :data:`DEFAULT_CITATION_STYLE`
  until changed;
- a Work's override, the Work's ``citation_style`` field
  (``work_metadata_sync``), where the empty string means the Library's default.

``frontend-app/src/features/citations/styles.ts`` lists the same ids with
their labels and files; ``tests/test_csl_format.py`` keeps the two in step.

A Library may also import its own styles
(``backend.domain.works.custom_citation_styles``). Their ids are
``custom-`` and 32 hex digits, so they never collide with a shipped style.
Both places that name a style accept such an id. A Work's field checks only
the shape: an offline edit may reach PRKS after the style was deleted, and
a style that no longer exists reads as the Library's default wherever it is
used.
"""

from __future__ import annotations

import re

#: Every style id PRKS ships, in the order Settings lists them.
CITATION_STYLES: tuple[str, ...] = (
    "apa",
    "chicago-author-date",
    "chicago-notes-bibliography",
    "modern-language-association",
    "harvard-cite-them-right",
    "ieee",
    "nlm-citation-sequence",
)
CITATION_STYLE_SET = frozenset(CITATION_STYLES)
DEFAULT_CITATION_STYLE = "apa"

CUSTOM_STYLE_ID_RE = re.compile(r"^custom-[0-9a-f]{32}$")


def is_custom_style_id(value: object) -> bool:
    """Whether ``value`` has the shape of an imported style's id."""
    return isinstance(value, str) and CUSTOM_STYLE_ID_RE.match(value) is not None


class _WorkStyleValues(frozenset):
    """What a Work's ``citation_style`` may hold: ``''``, a shipped id, or an imported style's id.

    Iterating lists ``''`` and the shipped ids, the values every Library has.
    """

    def __contains__(self, value: object) -> bool:
        return frozenset.__contains__(self, value) or is_custom_style_id(value)


WORK_CITATION_STYLE_VALUES: frozenset[str] = _WorkStyleValues(CITATION_STYLE_SET | {""})
