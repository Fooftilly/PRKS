"""``prks_app.py`` roles (§12.2; L-D-1 #630): no role is ``serve``, which keeps today's flags."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from unittest.mock import patch

import prks_app

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SplitRoleTests(unittest.TestCase):
    def test_no_role_is_serve(self) -> None:
        self.assertEqual(prks_app.split_role([]), ("serve", []))
        self.assertEqual(prks_app.split_role(["--testing", "--port", "8070"]), ("serve", ["--testing", "--port", "8070"]))

    def test_named_roles(self) -> None:
        for role in ("serve", "migrate", "worker", "admin"):
            with self.subTest(role=role):
                self.assertEqual(prks_app.split_role([role, "--testing"]), (role, ["--testing"]))

    def test_a_role_is_only_the_first_argument(self) -> None:
        self.assertEqual(prks_app.split_role(["--testing", "migrate"]), ("serve", ["--testing", "migrate"]))

    def test_role_parsers_take_the_storage_root(self) -> None:
        for role in ("migrate", "worker"):
            with self.subTest(role=role):
                args = prks_app.build_role_parser(role).parse_args(["--testing", "--storage-root", "/x"])
                self.assertTrue(args.testing)
                self.assertEqual(args.storage_root, "/x")

    def test_serve_keeps_its_flags(self) -> None:
        args = prks_app.build_parser().parse_args(["--testing", "--port", "8070"])
        self.assertEqual(args.port, 8070)

    def test_every_role_passes_the_dependency_gate_first(self) -> None:
        class Gate(Exception):
            pass

        for role in ("migrate", "worker", "admin"):
            with self.subTest(role=role):
                with patch("backend.dependency_gate.ensure_runtime_or_exit", side_effect=Gate):
                    with self.assertRaises(Gate):
                        prks_app.run_role(role, ["--help"] if role != "admin" else ["bootstrap", "--help"])

    def test_migrate_without_a_database_url_exits_2(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PRKS_")}
        proc = subprocess.run(
            [sys.executable, "prks_app.py", "migrate", "--testing"],
            cwd=_ROOT, env=env, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("PRKS_DATABASE_URL", proc.stderr)


if __name__ == "__main__":
    unittest.main()
