"""Deployment-settings wire contract and read-only view (#612)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from openapi_core import OpenAPI
from openapi_core.testing import MockRequest, MockResponse

from backend.api import deployment as deployment_api
from backend.api.session import route_capability
from backend.api_contract.openapi import openapi_document
from backend.runtime.config_reference import Scope, in_scope
from backend.domain.identity.authorization import Capability
from backend.runtime.log_safety import safe_route

_ROOT = Path(__file__).resolve().parents[1]

_ENV = {
    "PRKS_DATABASE_URL": "postgresql://u:hunter2@db/prks",
    "PRKS_STORAGE": "/srv/secret-place/prks",
    "PRKS_TLS_CERT_FILE": "/etc/private/cert.pem",
    "PRKS_HTTP_THREADS": "8",
}


class DeploymentSettingsTests(unittest.TestCase):
    def test_the_body_validates_against_the_document(self) -> None:
        api = OpenAPI.from_dict(openapi_document())
        request = MockRequest(host_url="http://127.0.0.1", method="get", path="/api/deployment-settings")
        api.validate_request(request)
        data = json.dumps(deployment_api.body(_ENV)).encode()
        api.validate_response(request, MockResponse(data=data, status_code=200, content_type="application/json"))

    def test_every_deployment_setting_is_listed_and_nothing_else(self) -> None:
        keys = [row["key"] for row in deployment_api.body({})["settings"]]
        self.assertEqual(keys, [setting.key for setting in in_scope(Scope.DEPLOYMENT)])

    def test_no_secret_or_host_path_reaches_the_browser(self) -> None:
        body = deployment_api.body(_ENV)
        text = json.dumps(body)
        for leaked in ("hunter2", "secret-place", "/etc/private"):
            with self.subTest(leaked=leaked):
                self.assertNotIn(leaked, text)
        rows = {row["key"]: row for row in body["settings"]}
        self.assertEqual(rows["PRKS_DATABASE_URL"]["value"], "(set, hidden)")
        self.assertEqual(rows["PRKS_STORAGE"]["value"], "set on the host")
        self.assertEqual((rows["PRKS_HTTP_THREADS"]["value"], rows["PRKS_HTTP_THREADS"]["source"]), ("8", "environment"))
        self.assertEqual((rows["PRKS_AUTH"]["source"], rows["PRKS_AUTH"]["value"]),
                         ("default", rows["PRKS_AUTH"]["default"]))
        self.assertIs(body["editable_in_browser"], False)

    def test_the_storage_root_names_the_source_that_chose_it(self) -> None:
        for root_source, shown in (("cli", "command line"), ("config_file", "bootstrap file"), ("env", "environment")):
            with self.subTest(root_source=root_source):
                rows = {row["key"]: row for row in deployment_api.body({}, root_source)["settings"]}
                self.assertEqual((rows["PRKS_STORAGE"]["value"], rows["PRKS_STORAGE"]["source"]),
                                 ("set on the host", shown))
        rows = {row["key"]: row for row in deployment_api.body({}, "development_default")["settings"]}
        self.assertEqual(rows["PRKS_STORAGE"]["source"], "default")

    def test_only_the_owner_reads_it_and_nothing_writes_it(self) -> None:
        self.assertIs(route_capability("GET", deployment_api.PATH), Capability.ADMIN_LIBRARY)

    def test_routes_are_logged_as_themselves(self) -> None:
        for route in (deployment_api.PATH, "/api/openapi/deployment-settings.json"):
            with self.subTest(route=route):
                self.assertEqual(safe_route(route), route)


if __name__ == "__main__":
    unittest.main()
