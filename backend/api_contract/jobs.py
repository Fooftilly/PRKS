"""Job-diagnostics HTTP response DTOs (``docs/postgresql-architecture.md`` §12.4; L-J-1 #627).

Shape only. ``backend.jobs.embedded`` builds the snapshot from the embedded
worker's process-local counters and the queue's depth by kind. Counts, ages,
job kinds and stable reason codes only: never a payload, an error text, a
Library id or other Library metadata. Importing this module loads no
PostgreSQL driver.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class JobWorkerCounters(BaseModel):
    """The counters ``backend.jobs.diagnostics.WorkerStats`` always emits (zeros included)."""

    model_config = ConfigDict(extra="forbid")

    claimed: int = Field(..., ge=0)
    succeeded: int = Field(..., ge=0)
    retried: int = Field(..., ge=0)
    cancelled: int = Field(..., ge=0)
    failed: int = Field(..., ge=0)
    lost: int = Field(..., ge=0)
    released: int = Field(..., ge=0)
    reaped: int = Field(..., ge=0)
    errors: int = Field(..., ge=0)


class EmbeddedJobWorker(BaseModel):
    """The worker thread this web process runs (``PRKS_EMBEDDED_WORKER``)."""

    model_config = ConfigDict(extra="forbid")

    # starting: the thread has not run its start check yet; waiting: the
    # start check waits (``reason``); running: it claims; refused: the start
    # check refused for good (``reason``); stopped: it has stopped.
    state: Literal["starting", "waiting", "running", "stopping", "stopped", "refused"]
    reason: Optional[str] = Field(None, description="Stable reason code while waiting or refused.")
    counters: JobWorkerCounters


class JobQueueDepth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    state: Literal["queued", "running", "failed"]
    count: int = Field(..., ge=0)
    oldest_age_seconds: float = Field(..., ge=0)


class JobDiagnostics(BaseModel):
    """GET /api/diagnostics/jobs body (owner only; ``PRKS_AUTH`` runtime only)."""

    model_config = ConfigDict(extra="forbid")

    embedded_worker: Optional[EmbeddedJobWorker] = Field(
        ..., description="null when PRKS_EMBEDDED_WORKER=0 (a separate prks worker runs the jobs)."
    )
    queue: list[JobQueueDepth]
