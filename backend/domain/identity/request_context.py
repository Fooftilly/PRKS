"""Who a domain operation acts for (``docs/postgresql-architecture.md`` §9.4, §11.4; L-ID-1 #626).

A :class:`RequestContext` names the actor (a user, a job or the host
administrator), the Library acted on, the Library epoch the caller prepared
its change against, and, for a browser, the session and the claimed device.

Contexts are made only by the constructors below, each at a stated trust
boundary. Their identity fields come from server-side state, never from what
a client sends:

- :meth:`RequestContext.for_session` (a browser request): ``user_id``,
  ``library_id`` and ``session_id`` come from the session
  :func:`~backend.domain.identity.sessions.resolve_session` returned for the
  cookie. The client supplies only two claims: the device it says it is
  (checked by :func:`~backend.domain.identity.authorization.authorize`
  against the user's devices) and the Library epoch it loaded (checked by
  the command helper against ``prks.libraries.epoch``).
- :meth:`RequestContext.for_job` (a worker running a claimed job): the
  Library, the epoch and the lease come from the ``prks.jobs`` row, so a job
  enqueued before a restore carries the old epoch and its commands are
  refused, whatever the worker process believes.
- :meth:`RequestContext.for_admin` (the host admin CLI,
  ``python -m backend.domain.identity.admin``): trusted because only someone
  who operates the server can run it, after the root pairing start check. No
  HTTP path builds one.

No context carries a role or a capability: ``authorize()`` reads the current
membership inside the transaction of every protected operation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from backend.db.database import Database
from backend.domain.identity.sessions import ValidSession


class Source(Enum):
    """Where an operation comes from; set by the constructor, never by a client."""

    BROWSER = "browser"
    WORKER = "worker"
    ADMIN = "admin"


# Only the constructors of this module hold it, so a context cannot be
# assembled field by field from request values. Each context is sealed with
# the values it was issued with, so ``dataclasses.replace`` cannot change its
# source, actor, Library, epoch or claims either.
_ISSUER = object()


def parse_epoch(value: object) -> Optional[int]:
    """A Library epoch as a client sends it (an int, or the header's decimal text).

    ``None`` when it is missing or unusable; the command helper refuses a
    Library-scoped mutation without one (400 ``LIBRARY_EPOCH_REQUIRED``).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, str):
        text = value.strip()
        if text.isascii() and text.isdigit() and len(text) <= 18:
            number = int(text, 10)
            return number if number >= 1 else None
    return None


class DeviceClaimInvalid(ValueError):
    """The device id a client sent is not a UUID; it is refused, never ignored."""


def _parse_device(value: object) -> Optional[uuid.UUID]:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            pass
    raise DeviceClaimInvalid("The device id is not valid.")


@dataclass(frozen=True)
class RequestContext:
    """The actor, Library and epoch of one operation. Build it with a constructor."""

    source: Source
    user_id: Optional[str]
    library_id: Optional[str]
    # The Library epoch the caller acted on; ``None`` for reads, instance and
    # identity operations, and for a client that sent none.
    epoch: Optional[int]
    device_id: Optional[uuid.UUID] = None
    session_id: Optional[uuid.UUID] = None
    job_id: Optional[uuid.UUID] = None
    lease_id: Optional[uuid.UUID] = None
    _seal: object = field(default=None, repr=False, compare=False)

    def _issued_values(self) -> tuple[object, ...]:
        return (
            _ISSUER, self.source, self.user_id, self.library_id, self.epoch,
            self.device_id, self.session_id, self.job_id, self.lease_id,
        )

    def __post_init__(self) -> None:
        if self._seal != self._issued_values():
            raise TypeError("RequestContext is built by its for_session/for_job/for_admin constructors")

    @classmethod
    def _issue(cls, source: Source, user_id: Optional[str], library_id: Optional[str],
               epoch: Optional[int], *, device_id: Optional[uuid.UUID] = None,
               session_id: Optional[uuid.UUID] = None, job_id: Optional[uuid.UUID] = None,
               lease_id: Optional[uuid.UUID] = None) -> RequestContext:
        seal = (_ISSUER, source, user_id, library_id, epoch, device_id, session_id, job_id, lease_id)
        return cls(source, user_id, library_id, epoch, device_id, session_id, job_id, lease_id, seal)

    @classmethod
    def for_session(
        cls, session: ValidSession, *, epoch: object = None, device_id: object = None
    ) -> RequestContext:
        """A browser request. ``session`` must come from ``resolve_session``.

        ``epoch`` and ``device_id`` are the client's claims (the
        ``PRKS-Library-Epoch`` header and the sync device id); a device id
        that is not a UUID raises :class:`DeviceClaimInvalid`.
        """
        return cls._issue(
            Source.BROWSER,
            session.user_id,
            session.library_id,
            parse_epoch(epoch),
            device_id=_parse_device(device_id),
            session_id=session.session_id,
        )

    @classmethod
    def for_job(cls, db: Database, job_id: uuid.UUID, lease_id: uuid.UUID) -> Optional[RequestContext]:
        """A worker running job ``job_id`` under an unexpired ``lease_id``; ``None`` otherwise.

        Library and epoch are the job's own (``library_epoch`` was recorded at
        enqueue), never the worker's or the Library's current ones.
        """
        row = db.query(
            lambda tx: tx.execute(
                "SELECT library_id, library_epoch, created_by FROM prks.jobs "
                "WHERE id = %s AND lease_id = %s AND state = 'running' AND locked_until > now()",
                (job_id, lease_id),
            ).fetchone()
        )
        if row is None:
            return None
        return cls._issue(
            Source.WORKER,
            row["created_by"],
            row["library_id"],
            row["library_epoch"],
            job_id=job_id,
            lease_id=lease_id,
        )

    @classmethod
    def for_admin(cls, *, library_id: Optional[str] = None, epoch: object = None) -> RequestContext:
        """The host admin CLI, after the root pairing start check; never an HTTP request."""
        return cls._issue(Source.ADMIN, None, library_id, parse_epoch(epoch))
