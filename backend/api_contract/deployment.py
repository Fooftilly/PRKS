"""Deployment-settings HTTP response DTOs (#612).

Shape only. ``backend.config_reference.browser_view`` builds the rows from
the web process's environment. Read-only by design: deployment values come
from the host (environment, Compose ``.env``, secret files) and need a
restart; the browser can never change them. A secret or a host path shows
only that it is set. Importing this module loads no PostgreSQL driver.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DeploymentSetting(BaseModel):
    """One deployment setting as this web process read it at start."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., description="The environment variable, such as PRKS_HTTP_THREADS.")
    summary: str = Field(..., description="What it controls.")
    value: str = Field(
        ...,
        description=(
            "The effective value. For a default, the documented default; for a secret, "
            "'(set, hidden)'; for a path on the host, 'set on the host'."
        ),
    )
    source: Literal["environment", "default", "command line", "bootstrap file"] = Field(
        ..., description="Where the value came from; the storage root may also come from the command line or the bootstrap file."
    )
    default: str = Field(..., description="The documented default.")


class DeploymentSettings(BaseModel):
    """GET /api/deployment-settings body (Library owner only)."""

    model_config = ConfigDict(extra="forbid")

    settings: list[DeploymentSetting]
    editable_in_browser: Literal[False] = Field(
        False, description="Always false: these values are changed on the host and need a restart."
    )
