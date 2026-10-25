"""Pure safeguards for the one-route/one-method Schemathesis pilot."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from backend.api_contract.openapi import publishers_openapi_document
from scripts.ci.schemathesis_pilot import (
    CLI_TIMEOUT, FRAGMENT, MAX_EXAMPLES, METHOD, OPERATION, ROUTE,
    command, read_json, scoped_contract,
)


class SchemathesisPilotContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generated = publishers_openapi_document()
        cls.checked = json.loads(FRAGMENT.read_text(encoding="utf-8"))

    def test_checked_in_published_contract_can_be_narrowed_without_mutations(self):
        selected = scoped_contract(self.checked, self.generated)
        self.assertEqual(set(selected["paths"]), {ROUTE})
        self.assertEqual(set(selected["paths"][ROUTE]), {"get"})
        self.assertEqual(selected["paths"][ROUTE]["get"]["operationId"], OPERATION)
        self.assertEqual(self.generated, publishers_openapi_document())
        self.assertIn("post", self.generated["paths"][ROUTE])
        self.assertIn("/api/publishers/{publisher_id}", self.generated["paths"])

    def test_drift_fails_before_any_http_fuzzing(self):
        drift = copy.deepcopy(self.generated)
        drift["info"]["version"] = "wrong"
        with self.assertRaisesRegex(ValueError, "differs"):
            scoped_contract(self.checked, drift)
        drift = copy.deepcopy(self.generated)
        drift["paths"][ROUTE]["get"]["operationId"] = "different"
        with self.assertRaisesRegex(ValueError, "differs"):
            scoped_contract(drift, drift)

    def test_cli_has_hard_method_operation_seed_and_request_caps(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "read-only.json"
            path.write_text("{}", encoding="utf-8")
            args = command(path, "http://127.0.0.1:50271", "st")
            self.assertIn("--include-operation-id", args)
            self.assertEqual(args[args.index("--include-operation-id") + 1], OPERATION)
            self.assertEqual(args[args.index("--include-method") + 1], METHOD)
            self.assertEqual(args[args.index("--max-examples") + 1], str(MAX_EXAMPLES))
            self.assertNotIn("--max-time", args)
            self.assertEqual(args[args.index("--phases") + 1], "examples,fuzzing")
            self.assertLessEqual(MAX_EXAMPLES, 25)
            self.assertLessEqual(CLI_TIMEOUT, 90)
            self.assertIn("--max-failures", args)
            self.assertIn("--max-redirects", args)
            self.assertIn("--seed", args)
            for forbidden in ("POST", "PATCH", "DELETE", "stateful", "coverage"):
                self.assertNotIn(forbidden, args)
            with self.assertRaisesRegex(ValueError, "loopback"):
                command(path, "https://localhost:9000", "st")

    def test_http_probes_reject_unlisted_paths_and_remote_origin(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            read_json("https://example.com", "/api/publishers?used=1")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            read_json("http://127.0.0.1:5000", "/api/works")


if __name__ == "__main__":
    unittest.main()
