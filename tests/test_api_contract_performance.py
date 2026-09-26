"""Performance-diagnostics wire contract and the Vue client's lockstep keys."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from openapi_core import OpenAPI
from openapi_core.testing import MockRequest, MockResponse
from pydantic import ValidationError

from backend.api_contract.boundary import dump_response
from backend.api_contract.openapi import (
    PERFORMANCE_DIAGNOSTICS_CONTRACT_VERSION,
    performance_diagnostics_openapi_document,
)
from backend.api_contract.performance import (
    PerformanceCounters,
    PerformanceDiagnosticsReset,
    PerformanceRequestTotals,
    PerformanceRouteStat,
    PerformanceSnapshot,
    PerformanceSpanStat,
)
from backend.performance import COUNTER_NAMES, reset, snapshot

_ROOT = Path(__file__).resolve().parents[1]
_CLIENT = _ROOT / "frontend-app" / "src" / "api" / "performance-diagnostics.ts"
_SRC = _ROOT / "frontend-app" / "src"


def _ts_keys(name: str) -> set[str]:
    text = _CLIENT.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = \[(.*?)\] as const", text, re.S)
    if match is None:
        raise AssertionError(f"missing {name} in {_CLIENT}")
    return set(re.findall(r"'([^']+)'", match.group(1)))


class PerformanceDiagnosticsContractTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_live_snapshot_matches_the_response_model(self):
        body = dump_response(PerformanceSnapshot, snapshot())
        self.assertEqual(body["requests"]["total"], 0)
        self.assertEqual(body["routes"], [])
        self.assertEqual(set(body["counters"]), set(COUNTER_NAMES))

    def test_reset_body_is_exact(self):
        self.assertEqual(
            dump_response(PerformanceDiagnosticsReset, {"status": "reset"}),
            {"status": "reset"},
        )

    def test_unknown_snapshot_key_is_refused(self):
        raw = snapshot()
        raw["note"] = "secret"
        with self.assertRaises(ValidationError):
            PerformanceSnapshot.model_validate(raw)

    def test_typescript_keys_match_pydantic_fields(self):
        pairs = (
            ("PERFORMANCE_SNAPSHOT_KEYS", PerformanceSnapshot),
            ("PERFORMANCE_REQUEST_KEYS", PerformanceRequestTotals),
            ("PERFORMANCE_ROUTE_KEYS", PerformanceRouteStat),
            ("PERFORMANCE_SPAN_KEYS", PerformanceSpanStat),
            ("PERFORMANCE_COUNTER_KEYS", PerformanceCounters),
            ("PERFORMANCE_RESET_KEYS", PerformanceDiagnosticsReset),
        )
        for const_name, model in pairs:
            with self.subTest(const_name=const_name):
                self.assertEqual(_ts_keys(const_name), set(model.model_fields))
        self.assertEqual(set(PerformanceCounters.model_fields), set(COUNTER_NAMES))

    def test_openapi_document_version_matches_the_typescript_client(self):
        document = performance_diagnostics_openapi_document()
        self.assertEqual(document["openapi"], "3.1.0")
        self.assertEqual(document["info"]["version"], PERFORMANCE_DIAGNOSTICS_CONTRACT_VERSION)
        self.assertIn("/api/diagnostics/performance", document["paths"])
        self.assertIn("/api/diagnostics/performance/reset", document["paths"])
        client = _CLIENT.read_text(encoding="utf-8")
        self.assertIn(
            f"PERFORMANCE_DIAGNOSTICS_CONTRACT_VERSION = '{PERFORMANCE_DIAGNOSTICS_CONTRACT_VERSION}'",
            client,
        )
        api = OpenAPI.from_dict(document)
        body = dump_response(PerformanceSnapshot, snapshot())
        request = MockRequest(
            host_url="http://127.0.0.1",
            method="get",
            path="/api/diagnostics/performance",
        )
        response = MockResponse(
            data=json.dumps(body).encode(),
            status_code=200,
            content_type="application/json",
        )
        api.validate_request(request)
        api.validate_response(request, response)

    def test_vue_slice_does_not_persist_queries_or_add_other_frameworks(self):
        text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in _SRC.rglob("*")
            if path.suffix in {".ts", ".vue"}
        )
        self.assertNotIn("persistQueryClient", text)
        self.assertNotIn("@vueuse", text)
        self.assertNotIn("vue-router", text)
        self.assertNotIn("pinia", text.lower())
        package = json.loads((_ROOT / "frontend-app" / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(package["dependencies"]),
            {"vue", "@tanstack/vue-query"},
        )
