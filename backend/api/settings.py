"""Application settings HTTP controller: ``/api/settings`` (PG-C3 #642).

GET answers the Library's settings; PATCH applies the patch through ``backend/domain/settings/commands`` for the
admitted request's context, and answers the settings as they now are. A
refused key is ``400`` with its message. The commands open the transactions,
so authorization stays in ``library_query`` and ``library_command`` (a patch
needs ``ADMIN_LIBRARY``; ``backend/api/session.py`` also admits only that),
and a patch's maintenance and epoch fence in ``library_command``.

The commands are imported when a route runs: the served app loads no
PostgreSQL module at import time (``tests/test_e2e_policy.py``).
"""

from __future__ import annotations

from backend.api_contract.operation import Operation, body, error, ok
from backend.api_contract.settings import AppSettings, AppSettingsPatch
from backend.web.http import Request, Response, json_response
from backend.web.routing import route

PATH = "/api/settings"


def read(req: Request) -> Response:
    from backend.domain.settings import commands

    return json_response(req, 200, commands.response(*req.actor))


def patch(req: Request) -> Response:
    from backend.domain.settings import commands

    try:
        commands.patch(*req.actor, req.data)
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})
    return json_response(req, 200, commands.response(*req.actor))


ROUTES = (
    route("GET", PATH, read, contract=Operation(
        "getSettings", "The Library's settings", "settings",
        {200: ok(AppSettings, "Every setting, its default when unset.")})),
    route("PATCH", PATH, patch, contract=Operation(
        "updateSettings", "Change some of the Library's settings", "settings",
        {200: ok(AppSettings, "The settings as they now are."),
         400: error("A refused value; nothing was written.")},
        request=body(AppSettingsPatch, "Left out: nothing changes.", required=False),
        description="Every key is checked before any is written. Needs the Library owner.")),
)
