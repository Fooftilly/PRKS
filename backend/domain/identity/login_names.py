"""Login names (§9.3; L-ID-1 #626): the one rule account creation and login share.

A login name is text of 1 to :data:`MAX_LOGIN_NAME_CHARS` characters whose
identity key (:func:`backend.domain.text_identity.identity_key`, the Python
twin of ``prks.identity_key``) exists and is not empty. Bootstrap refuses any
other name, so login can refuse one before touching the database without
locking out an account that exists.
"""

from __future__ import annotations

from typing import Optional

from backend.domain.text_identity import UnsupportedTextError, identity_key

MAX_LOGIN_NAME_CHARS = 256


def login_key(login_name: object) -> Optional[str]:
    """The identity key of a usable login name, or ``None``."""
    if not isinstance(login_name, str) or not 0 < len(login_name) <= MAX_LOGIN_NAME_CHARS:
        return None
    try:
        key = identity_key(login_name)
    except UnsupportedTextError:
        return None
    return key or None
