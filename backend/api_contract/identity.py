"""Sign-in, session and first-run setup wire shapes (#1431).

Shape and JSON types only. Admission answers these paths before any route
runs (``backend/api/session.py``, ``backend/api/setup.py``); the login,
session and setup rules stay in ``backend/domain/identity``.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.api_contract.boundary import omittable

_FORBID = ConfigDict(extra="forbid")
# A request names the fields it sends; the handlers ignore keys they do not know.
_IGNORE = ConfigDict(extra="ignore")


class LoginRequest(BaseModel):
    model_config = _IGNORE

    login: str
    password: str


class SessionUser(BaseModel):
    model_config = _FORBID

    id: str
    display_name: str


class SessionLibrary(BaseModel):
    model_config = _FORBID

    id: str
    name: str
    role: str = Field(description="The user's role in the Library.")
    epoch: int = Field(description="The Library epoch a mutation sends as PRKS-Library-Epoch.")
    content: str = Field(description="Where the Library's content is kept (postgres).")


class SessionState(BaseModel):
    """``GET /api/session``: who is signed in, to which Library, until when."""

    model_config = _FORBID

    auth_mode: Literal["session", "loopback-owner"]
    instance_id: str
    user: SessionUser
    library: SessionLibrary
    expires_at: str = Field(description="ISO 8601, UTC.")


class SignedIn(BaseModel):
    model_config = _FORBID

    signed_in: Literal[True]
    session: SessionState


class SignedOut(BaseModel):
    model_config = _FORBID

    signed_out: Literal[True]


SetupStateName = Literal["unconfigured", "bootstrapping", "ready"]


class SetupState(BaseModel):
    """``GET /api/setup``: how far first-run setup is, and whether the storage folder may be chosen."""

    model_config = _FORBID

    state: SetupStateName
    storage: Literal["choosable", "fixed", "restart_required"]


class SetupRequest(BaseModel):
    model_config = _IGNORE

    token: str = Field(description="The bootstrap token the server printed.")
    login: str
    password: str
    display_name: Optional[str] = omittable("Left out or blank: the login.")
    library_name: Optional[str] = omittable("Left out or blank: Library.")


class SetupDone(BaseModel):
    model_config = _FORBID

    state: Literal["ready"]
    created: bool = Field(description="False when an earlier attempt had already completed setup.")
    signed_in: bool = Field(description="Whether the answer sets a session cookie for the owner.")


class StorageChoiceRequest(BaseModel):
    model_config = _IGNORE

    token: str = Field(description="The bootstrap token the server printed.")
    folder: str = Field(description="The storage folder: up to 4096 characters, no NUL.")


class StorageChosen(BaseModel):
    model_config = _FORBID

    folder: str
    created: bool
    restart_required: Literal[True]


class SetupRefusal(BaseModel):
    """A refused setup step: ``error``, and for a bad field or a refused pairing what was wrong."""

    model_config = _FORBID

    error: str
    field: str = omittable("The request field that was refused.")
    reason: str = omittable("Why the field or the pairing was refused.")
    message: str = omittable("A fixed explanation of a refused pairing or storage folder.")
