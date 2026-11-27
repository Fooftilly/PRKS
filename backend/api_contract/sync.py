"""``POST /api/sync/operations`` wire shapes (#1431; ``docs/postgresql-architecture.md`` §8.4, §11.4).

The envelope every durable operation shares (``backend/sync/envelope.py``)
and the ledgered result. A family's payload and its result fields belong to
the family's rules (``backend/domain/<area>/*_rules.py``), so they stay open
objects here; ``code`` is always present.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class SyncOperation(BaseModel):
    """One durable operation. Every key is required; ``normalize_envelope`` checks the values."""

    model_config = ConfigDict(extra="forbid")

    op_id: str = Field(description="A UUID the device minted for this operation.")
    device_id: str = Field(description="The device's UUID.")
    operation: str = Field(description="A registered operation name, e.g. `MARK_WORK_OPENED`.")
    entity_type: str = Field(description="The entity type the operation is registered with.")
    entity_id: str = Field(description="1-200 characters, no surrounding space.")
    payload: dict[str, Any] = Field(description="The family's payload.")
    base_revision: Optional[int] = Field(description="The revision the change was made at, or null.")
    occurred_at: str = Field(description="ISO 8601 instant with a time zone.")
    created_at: str = Field(description="ISO 8601 instant with a time zone.")
    depends_on: list[str] = Field(description="Up to 16 op_ids that must be ledgered first.")


class SyncResult(BaseModel):
    """The ledgered result: ``ACKNOWLEDGED`` with the family's fields, or a refusal code.

    Once an operation is ledgered, a retry with the same ``op_id`` and body answers
    the ledgered status and result. A refusal that is not ledgered (an unmet
    ``depends_on``, for one) may answer differently on a later retry.
    """

    model_config = ConfigDict(extra="allow")

    code: str = Field(description="`ACKNOWLEDGED`, or a refusal such as `REVISION_CONFLICT`, "
                                  "`INVALID_ENVELOPE`, `OP_ID_REUSE`, `UNSATISFIED_DEPENDENCY`.")
