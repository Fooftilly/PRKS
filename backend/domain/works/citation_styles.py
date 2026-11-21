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
"""

from __future__ import annotations

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
