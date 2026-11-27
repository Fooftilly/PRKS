"""``POST /api/client-errors`` wire shapes (#1431).

A browser error report is logged as metadata only
(``backend.runtime.log_safety.client_error_log_fields``): every field is
optional and a value the log does not take is replaced, never refused.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ClientErrorReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Any = Field(default=None, description="A known error kind; anything else is logged as client_error.")
    error_name: Any = Field(default=None, description="An error class name; anything else is logged as Error.")
    source: Any = Field(default=None, description="A known source or an asset basename; else unknown.")
    request_id: Any = Field(default=None, description="The failed request's id, when there was one.")
    line: Any = Field(default=None, description="Source line, 0 to 1,000,000; else left out.")
    column: Any = Field(default=None, description="Source column, 0 to 1,000,000; else left out.")
    http_status: Any = Field(default=None, description="An HTTP status, 100 to 599; else left out.")


class ClientErrorLogged(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(description="Always `logged`.")
    request_id: str = Field(description="This report's own request id, as logged.")
