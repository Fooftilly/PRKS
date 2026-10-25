#!/usr/bin/env python3
"""Bounded read-only Schemathesis pilot on the REAL Publishers OpenAPI fragment.

Only a fresh AppServer --testing instance on loopback is ever targeted.
No mutating operations, production tokens, live library files or arbitrary URL.
"""
from __future__ import annotations

import copy
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api_contract.openapi import publishers_openapi_document  # noqa: E402
from backend.api_contract.publishers import PublisherInUse  # noqa: E402
from tests.e2e.harness import AppServer  # noqa: E402
from tests.ux_tour.fixtures import seed_ux_tour_library  # noqa: E402

FRAGMENT = ROOT / "docs" / "api" / "openapi-publishers.json"
METHOD = "GET"
ROUTE = "/api/publishers"
OPERATION = "listPublishersInUse"
SEED = 855
MAX_EXAMPLES = 25
MAX_SECONDS = 35
CLI_TIMEOUT = 110
SAFE_LINE = re.compile(r"[A-Za-z0-9 .,;:=_()!+/%-]{1,150}\Z")


def read_json(origin: str, path: str, *, host: str | None = None):
    """Only exact allowlisted paths on an unredirectable loopback socket."""
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
        or parsed.username or parsed.password or parsed.path or parsed.query
        or parsed.fragment or parsed.port is None
    ):
        raise ValueError("Schemathesis pilot requires isolated loopback origin")
    if path not in (
        "/api/openapi/publishers.json",
        "/api/openapi.json",
        "/api/publishers?used=1",
    ):
        raise ValueError("unexpected API probe route")
    connection = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=8)
    try:
        connection.request(
            "GET", path, headers={"Host": host or f"127.0.0.1:{parsed.port}"}
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def scoped_contract(checked_in: dict, live: dict) -> dict:
    """Reject drift/partial contracts and retain only the read-only route."""
    generated = publishers_openapi_document()
    if checked_in != generated or live != generated:
        raise ValueError("published Publishers OpenAPI fragment differs from runtime or builder")
    family = generated.get("paths", {}).get(ROUTE)
    if not isinstance(family, dict) or set(family) != {"get", "post"}:
        raise ValueError("Publishers operation inventory changed; review allowlist")
    get = family.get("get")
    if not isinstance(get, dict) or get.get("operationId") != OPERATION:
        raise ValueError("published GET Publishers operation changed")
    if "200" not in get.get("responses", {}):
        raise ValueError("published GET no longer documents success")
    scoped = copy.deepcopy(generated)
    scoped["paths"] = {ROUTE: {"get": copy.deepcopy(get)}}
    return scoped


def command(schema: Path, origin: str, binary: str) -> list[str]:
    if not schema.is_file():
        raise ValueError("missing local selected-fragment schema")
    if not origin.startswith("http://127.0.0.1:"):
        raise ValueError("Schemathesis base must be loopback")
    return [
        binary, "run", str(schema), "--url", origin,
        "--include-operation-id", OPERATION,
        "--include-method", METHOD,
        "--phases", "examples,coverage,fuzzing",
        "--checks", "not_a_server_error,status_code_conformance,"
                    "content_type_conformance,response_schema_conformance",
        "--mode", "all", "--max-examples", str(MAX_EXAMPLES),
        "--max-time", str(MAX_SECONDS), "--max-failures", "1",
        "--workers", "1", "--rate-limit", "10/s",
        "--seed", str(SEED), "--max-redirects", "0",
    ]


def scan() -> int:
    # Do not silently inherit credentials for an unrelated PG auth instance.
    # This first GET-only pilot does not claim session/capability coverage.
    if os.environ.get("PRKS_AUTH", "").strip():
        raise RuntimeError("Refusing PRKS_AUTH: no disposable PostgreSQL test identity")
    binary = shutil.which("st")
    if not binary:
        raise RuntimeError("Pinned Schemathesis CLI not installed")
    checked_in = json.loads(FRAGMENT.read_text(encoding="utf-8"))
    server = AppServer(seed_fn=seed_ux_tour_library)
    started = time.monotonic()
    try:
        server.start()
        status, live = read_json(server.origin, "/api/openapi/publishers.json")
        if status != 200:
            raise AssertionError("live Publishers OpenAPI returned non-200")
        scoped = scoped_contract(checked_in, live)
        _, default_openapi = read_json(server.origin, "/api/openapi.json")
        if default_openapi == checked_in:
            raise AssertionError("default OpenAPI alias unexpectedly includes Publishers")
        status, rows = read_json(server.origin, "/api/publishers?used=1")
        if status != 200 or not isinstance(rows, list) or not rows:
            raise AssertionError("seeded Publishers catalog is empty or malformed")
        for row in rows:
            PublisherInUse.model_validate(row)
        if not any(row["work_count"] > 0 for row in rows):
            raise AssertionError("seed lacks a Publisher attached to a Work")

        # Only check existing Host admission, not nonexistent PG session auth.
        denied, _ = read_json(
            server.origin, "/api/publishers?used=1", host="untrusted.invalid"
        )
        if denied != 421:
            raise AssertionError("foreign Host unexpectedly admitted")


        with tempfile.TemporaryDirectory(prefix="prks-schemathesis-schema-") as directory:
            schema = Path(directory) / "publishers-get-only.json"
            schema.write_text(json.dumps(scoped), encoding="utf-8")
            args = command(schema, server.origin, binary)
            result = subprocess.run(
                args, cwd=directory, capture_output=True, text=True,
                timeout=CLI_TIMEOUT, check=False,
            )
        elapsed = round(time.monotonic() - started, 2)
        # No generated curl commands, bodies, headers, or query strings in logs.
        print(
            f"[schemathesis] method={METHOD} route={ROUTE} operation={OPERATION} "
            f"seed={SEED} examples_cap={MAX_EXAMPLES} time_cap={MAX_SECONDS}s "
            f"elapsed={elapsed}s exit={result.returncode}"
        )
        selected = [
            line.strip() for line in result.stdout.splitlines()
            if SAFE_LINE.fullmatch(line.strip())
            and (line.strip().startswith("No issues") or line.strip().startswith("Test cases:"))
        ]
        for line in selected[:3]:
            print("[schemathesis] " + line)
        if result.returncode:
            # Categorize CLI argument, schema and check failures without
            # printing generated curl commands, HTTP payloads or raw URLs.
            output = (result.stdout + "\\n" + result.stderr).lower()
            for label, marker in (
                ("cli_option", "no such option"),
                ("cli_value", "invalid value"),
                ("schema_loading", "failed to load"),
                ("schema_validation", "invalid schema"),
                ("response_schema", "response_schema_conformance"),
                ("unexpected_status", "status_code_conformance"),
                ("http_server_error", "not_a_server_error"),
                ("network_error", "connection refused"),
                ("internal_cli_error", "internal error"),
                ("schema_parse_error", "failed to parse"),
                ("test_failure", "failures:"),
            ):
                if marker in output:
                    print("[schemathesis] failure_category=" + label)

            print(
                "[schemathesis] Re-run with seed=855 on a new isolated test server; "
                "no raw HTTP payload or generated URL is published in Actions."
            )
            return 1
        return 0
    finally:
        server.stop()


if __name__ == "__main__":
    try:
        raise SystemExit(scan())
    except (ValueError, RuntimeError, AssertionError, subprocess.TimeoutExpired) as exc:
        print(f"[schemathesis] Pilot could not complete: {type(exc).__name__}")
        raise SystemExit(1) from None
