"""Pure safeguards for the bounded Schemathesis mutation scan (fuzzing slice 2)."""
from __future__ import annotations

import copy
import dataclasses
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.ci import schemathesis_mutations as scan


class SchemathesisMutationScanSafeguards(unittest.TestCase):
    def test_every_family_fragment_matches_its_builder_and_allowlist(self):
        for family in scan.FAMILIES:
            with self.subTest(family=family.name):
                document = scan.checked_contract(family)
                self.assertEqual(scan.operations(document), family.operations)

    def test_an_unreviewed_operation_stops_the_scan(self):
        family = scan.FAMILIES[0]
        with self.assertRaisesRegex(ValueError, "inventory"):
            scan.checked_contract(dataclasses.replace(family, operations=family.operations - {
                next(iter(family.operations))}))

    def test_a_fragment_that_drifted_from_its_builder_stops_the_scan(self):
        family = scan.FAMILIES[0]
        drifted = copy.deepcopy(family.builder())
        drifted["info"]["version"] = "wrong"
        with self.assertRaisesRegex(ValueError, "differs"):
            scan.checked_contract(dataclasses.replace(family, builder=lambda: drifted))

    def test_cli_is_bounded_seeded_and_carries_the_admission_headers(self):
        with tempfile.TemporaryDirectory() as folder:
            schema = Path(folder) / "family.json"
            schema.write_text("{}", encoding="utf-8")
            origin = "http://127.0.0.1:50271"
            args = scan.command(schema, origin, "7", "st")
        self.assertEqual(args[args.index("--phases") + 1], "examples,fuzzing")
        self.assertEqual(args[args.index("--seed") + 1], str(scan.SEED))
        self.assertEqual(args[args.index("--max-examples") + 1], str(scan.MAX_EXAMPLES))
        self.assertEqual(args[args.index("--workers") + 1], "1")
        self.assertEqual(args[args.index("--generation-allow-x00") + 1], "false")
        self.assertIn("--max-failures", args)
        self.assertIn("--rate-limit", args)
        self.assertEqual(args[args.index("--max-redirects") + 1], "0")
        headers = [args[i + 1] for i, arg in enumerate(args) if arg == "--header"]
        self.assertEqual(headers, [f"Origin: {origin}", f"{scan.EPOCH_HEADER}: 7"])
        self.assertNotIn("stateful", " ".join(args))
        self.assertNotIn("coverage", " ".join(args))
        self.assertLessEqual(scan.MAX_EXAMPLES, 50)
        self.assertLessEqual(scan.CLI_TIMEOUT, 120)

    def test_only_an_isolated_loopback_origin_is_targeted(self):
        with tempfile.TemporaryDirectory() as folder:
            schema = Path(folder) / "family.json"
            schema.write_text("{}", encoding="utf-8")
            for origin in ("https://127.0.0.1:5000", "http://localhost:5000", "http://example.com:80",
                           "http://127.0.0.1", "http://user@127.0.0.1:5000", "http://127.0.0.1:5000/api"):
                with self.subTest(origin=origin), self.assertRaisesRegex(ValueError, "loopback"):
                    scan.command(schema, origin, "1", "st")
            with self.assertRaisesRegex(ValueError, "counter"):
                scan.command(schema, "http://127.0.0.1:5000", "1\r\nX-Injected: 1", "st")
        with self.assertRaisesRegex(ValueError, "schema"):
            scan.command(Path(folder) / "gone.json", "http://127.0.0.1:5000", "1", "st")

    def test_refuses_a_configured_auth_and_raw_output_in_actions(self):
        with mock.patch.dict(os.environ, {"PRKS_AUTH": "loopback-owner"}, clear=False), \
                self.assertRaisesRegex(RuntimeError, "PRKS_AUTH"):
            scan.scan()
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}, clear=False), \
                self.assertRaisesRegex(RuntimeError, "forbidden"):
            scan.scan(debug_local=True)


if __name__ == "__main__":
    unittest.main()
