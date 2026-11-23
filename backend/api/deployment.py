"""Deployment settings HTTP controller: ``GET /api/deployment-settings`` (#612).

Settings shows the Library owner how this PRKS was deployed: every
deployment setting, its effective value and whether it came from the
environment or the default. Read-only: the values are set on the host
(environment, Compose ``.env``, secret files) and need a restart, so no
route writes them. A secret or a host path shows only that it is set
(``backend.config_reference.browser_view``). ``backend/api/session.py``
admits only the owner (``ADMIN_LIBRARY``).
"""

from __future__ import annotations

import os
from typing import Any

PATH = "/api/deployment-settings"


# Which source selected the storage root (``backend.storage.resolver``), as
# the browser names it.
_ROOT_SOURCES = {
    "cli": "command line",
    "env": "environment",
    "config_file": "bootstrap file",
    "platform_default": "default",
    "development_default": "default",
}


def body(environ: Any = None, root_source: Any = None) -> dict[str, Any]:
    """The response body for the web process's environment.

    ``root_source`` is the bound storage root's source: the root may come
    from ``--storage-root`` or the bootstrap file rather than ``PRKS_STORAGE``.
    """
    from backend.api_contract.boundary import dump_response
    from backend.api_contract.deployment import DeploymentSettings
    from backend.config_reference import BY_KEY, Effective, browser_view

    rows = browser_view(os.environ if environ is None else environ)
    source = _ROOT_SOURCES.get(root_source or "")
    if source is not None and source != "default":
        rows = [
            Effective(row.key, "set on the host", source) if row.key == "PRKS_STORAGE" else row
            for row in rows
        ]
    payload = {
        "settings": [
            {
                "key": row.key,
                "summary": BY_KEY[row.key].summary,
                "value": row.shown,
                "source": row.source,
                "default": BY_KEY[row.key].default,
            }
            for row in rows
        ],
        "editable_in_browser": False,
    }
    result = dump_response(DeploymentSettings, payload)
    assert isinstance(result, dict)
    return result


def handle_get(handler: Any, path: str, root_source: Any = None) -> bool:
    if path != PATH:
        return False
    handler.send_json(200, body(root_source=root_source))
    return True
