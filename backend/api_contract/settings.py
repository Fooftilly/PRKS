"""Application settings wire shapes: ``/api/settings`` (#1431).

Shape and JSON types only. Length, NUL, unknown citation styles and the
BibTeX profile's key and value checks stay in
``backend/domain/settings/app_settings.py``, which refuses them with a
``400`` and its own message.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, create_model

from backend.api_contract.boundary import omittable
from backend.domain.settings.rules import PRKS_BIBTEX_EXPORT_FIELD_IDS

_REQUIRED: dict[str, Any] = {name: (bool, ...) for name in PRKS_BIBTEX_EXPORT_FIELD_IDS}
_OMITTABLE: dict[str, Any] = {name: (bool, omittable()) for name in PRKS_BIBTEX_EXPORT_FIELD_IDS}

BibtexExportFields: type[BaseModel] = create_model(
    "BibtexExportFields",
    __config__=ConfigDict(extra="forbid"),
    __doc__="Which optional BibTeX lines are exported (the title and entry shell always are).",
    **_REQUIRED,
)

BibtexExportFieldsPatch: type[BaseModel] = create_model(
    "BibtexExportFieldsPatch",
    __config__=ConfigDict(extra="forbid"),
    __doc__="The BibTeX lines to turn on or off; the others keep their value.",
    **_OMITTABLE,
)


class AppSettings(BaseModel):
    """``GET``/``PATCH /api/settings``: the Library's settings, defaults filled in."""

    model_config = ConfigDict(extra="forbid")

    annotation_author: str = Field(description="The name PDF annotations are signed with; empty when unset.")
    bibtex_export_fields: BibtexExportFields  # type: ignore[valid-type]
    citation_style: str = Field(description="A shipped style id or an imported style's id.")


class AppSettingsPatch(BaseModel):
    """``PATCH /api/settings``: the keys sent change; the others are kept."""

    model_config = ConfigDict(extra="ignore")

    annotation_author: Any = Field(
        default=None, description="Null or empty clears it; another JSON value is converted to text and "
                                  "trimmed (at most 500 characters, no NUL).")
    bibtex_export_fields: BibtexExportFieldsPatch = omittable()  # type: ignore[valid-type]
    citation_style: str = omittable("A shipped style id or one this Library imported.")
