"""Pure safeguards for the bounded read-only Locust load pilot (#859)."""
from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from scripts.ci.locust_pilot import (
    CLI_TIMEOUT, DURATION_SECONDS, GENERATOR_CPU_LIMIT, LOCUSTFILE, ROOT, ROUTES, SPAWN_RATE, USERS,
    command, compare, problems, require_loopback, summarize,
)

HEADER = ("Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time,"
          "Min Response Time,Max Response Time,Average Content Size,Requests/s,Failures/s,"
          "50%,66%,75%,80%,90%,95%,98%,99%,99.9%,99.99%,100%\n")


def stats_row(kind: str, name: str, requests: int, failures: int) -> str:
    return f"{kind},{name},{requests},{failures},10,11,2,40.5,100,3.25,0,10,11,12,13,15,18,20,25,40,40,40\n"


def numbers(requests: int = 10, failures: int = 0) -> dict:
    return {"requests": requests, "failures": failures, "p50_ms": 10.0, "p95_ms": 20.0, "max_ms": 40.0, "rps": 2.0}


class LocustPilotSafeguards(unittest.TestCase):
    def test_only_its_own_loopback_server_is_a_target(self):
        self.assertEqual(require_loopback("http://127.0.0.1:50271"), 50271)
        for origin in ("http://localhost:50271", "https://127.0.0.1:50271", "http://127.0.0.1",
                       "http://user@127.0.0.1:50271", "http://127.0.0.1:50271/api", "http://10.0.0.5:8080"):
            with self.subTest(origin=origin), self.assertRaisesRegex(ValueError, "loopback"):
                require_loopback(origin)
        with self.assertRaisesRegex(ValueError, "loopback"):
            command("locust", "http://example.org:80", "run")

    def test_cli_is_headless_and_bounded(self):
        args = command("locust", "http://127.0.0.1:50271", "run")
        self.assertIn("--headless", args)
        self.assertEqual(args[args.index("--users") + 1], str(USERS))
        self.assertEqual(args[args.index("--spawn-rate") + 1], str(SPAWN_RATE))
        self.assertEqual(args[args.index("--run-time") + 1], f"{DURATION_SECONDS}s")
        self.assertEqual(args[args.index("--host") + 1], "http://127.0.0.1:50271")
        self.assertEqual(args[args.index("-f") + 1], str(LOCUSTFILE))
        self.assertEqual(args[args.index("--exit-code-on-error") + 1], "1")
        self.assertLessEqual(USERS, 10)
        self.assertLessEqual(DURATION_SECONDS, 60)
        self.assertLess(DURATION_SECONDS, CLI_TIMEOUT)

    def test_the_workload_only_reads(self):
        tree = ast.parse(LOCUSTFILE.read_text(encoding="utf-8"))
        calls = {node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "client"}
        self.assertEqual(calls, {"get"})
        checks = [node for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_check"]
        self.assertEqual(len(checks), len(ROUTES))
        sent = {}
        for node in checks:
            name = node.args[1]
            self.assertIsInstance(name, ast.Constant)
            assert isinstance(name, ast.Constant)
            sent[f"GET {name.value}"] = ast.unparse(node.args[0])
        # Each label names the path it sends, so a route cannot quietly target another endpoint.
        self.assertEqual(sent, {
            "GET /api/works": "'/api/works'",
            "GET /api/works/:id": "f'/api/works/{work_id}'",
            "GET /api/search": "f'/api/search?q={SEARCH_TERM}'",
            "GET /api/tags": "'/api/tags'",
            "GET /api/research-graph": "'/api/research-graph'",
        })
        self.assertEqual(set(sent), set(ROUTES))

    def test_summary_reads_locust_stats_per_route(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "run_stats.csv"
            path.write_text(HEADER + stats_row("GET", "/api/works", 12, 1) + stats_row("", "Aggregated", 12, 1),
                            encoding="utf-8")
            routes = summarize(path)
        self.assertEqual(set(routes), {"GET /api/works", "total"})
        self.assertEqual(routes["GET /api/works"], {"requests": 12, "failures": 1, "p50_ms": 10.0,
                                                    "p95_ms": 18.0, "max_ms": 40.5, "rps": 3.25})

    def test_a_run_counts_only_when_every_route_ran_cleanly(self):
        clean = {route: numbers() for route in ROUTES} | {"total": numbers()}
        self.assertEqual(problems(clean), [])
        self.assertEqual(problems({**clean, ROUTES[0]: numbers(failures=3)}), [f"3 failed: {ROUTES[0]}"])
        missing = dict(clean)
        del missing[ROUTES[1]]
        self.assertEqual(problems(missing), [f"no requests: {ROUTES[1]}"])
        self.assertEqual(problems({**clean, ROUTES[2]: numbers(requests=0)}), [f"no requests: {ROUTES[2]}"])
        self.assertEqual(problems({**clean, "POST /api/works": numbers()}), ["unexpected routes: POST /api/works"])
        self.assertEqual(problems(clean, GENERATOR_CPU_LIMIT + 0.1),
                         [f"load generator saturated: {GENERATOR_CPU_LIMIT + 0.1:.2f} of one CPU"])

    def test_comparison_is_per_route_and_same_tier_only(self):
        run = {"pilot": "locust-readonly", "tier": "small", "routes": {ROUTES[0]: numbers()}}
        baseline = {"pilot": "locust-readonly", "tier": "small", "prks_version": "x",
                    "routes": {ROUTES[0]: {**numbers(), "p95_ms": 10.0, "rps": 4.0}}}
        self.assertEqual(compare(run, baseline),
                         {"baseline_prks_version": "x", "routes": {ROUTES[0]: {"p95": 2.0, "rps": 0.5}}})
        with self.assertRaisesRegex(ValueError, "same pilot and tier"):
            compare(run, {**baseline, "tier": "medium"})

    def test_workflow_is_manual_and_installs_only_its_pin(self):
        workflow = (ROOT / ".github" / "workflows" / "locust-readonly-pilot.yml").read_text(encoding="utf-8")
        triggers = workflow.split("\non:", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("workflow_dispatch:", triggers)
        self.assertNotIn("pull_request", triggers)
        self.assertNotIn("schedule", triggers)
        runs = [line.strip() for line in workflow.splitlines() if line.strip().startswith("run:")]
        self.assertEqual(runs[0], "run: python scripts/ci/install_pinned_python.py locust")
        installs = [line for line in workflow.splitlines()
                    if "pip install" in line or "install_pinned_python" in line or "pipx" in line]
        self.assertEqual([line.strip() for line in installs],
                         ["run: python scripts/ci/install_pinned_python.py locust"])
        self.assertIn("contents: read", workflow)


if __name__ == "__main__":
    unittest.main()
