#!/usr/bin/env python3
"""Bounded Schemathesis scan of allowlisted MUTATING operations (fuzzing slice 2).

The read-only pilot (``schemathesis_pilot.py``) never sends a write. This scan
does, so it is held tighter than the pilot rather than looser:

* only a fresh ``AppServer --testing`` instance on loopback, on its own
  disposable PostgreSQL database seeded with synthetic data, is ever targeted;
* only the families in :data:`FAMILIES`, each with its exact operation
  inventory: a family whose published fragment gains or loses an operation, or
  differs from its Pydantic builder, stops the scan before any request;
* every request carries the instance's own Origin and Library epoch, so the
  generated input reaches the route instead of the admission refusal;
* no stateful sequences, no coverage phase, a fixed seed, a request rate limit
  and a process timeout. NUL is not generated: the server refuses it before
  routing (``tests/pg/test_http_request_fuzz_regressions.py``) with a 400 that
  several family read and delete operations do not document.

Output is sanitized exactly as the pilot's: no generated payloads, bodies or
URLs are printed in GitHub Actions.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api_contract.openapi import (  # noqa: E402
    positions_openapi_document,
    publishers_openapi_document,
    saved_views_openapi_document,
)
from scripts.ci.schemathesis_pilot import print_failure_diagnostics  # noqa: E402
from tests.e2e.harness import AppServer  # noqa: E402
from tests.ux_tour.fixtures import seed_ux_tour_library  # noqa: E402

SEED = 855
MAX_EXAMPLES = 50
CLI_TIMEOUT = 120
CHECKS = ("not_a_server_error,status_code_conformance,"
          "content_type_conformance,response_schema_conformance")
EPOCH_HEADER = "PRKS-Library-Epoch"


@dataclass(frozen=True)
class Family:
    name: str
    fragment: Path
    builder: Callable[[], dict[str, Any]]
    operations: frozenset[tuple[str, str]]


FAMILIES = (
    Family("positions", ROOT / "docs" / "api" / "openapi-positions.json", positions_openapi_document,
           frozenset({("get", "/api/positions"), ("post", "/api/positions"),
                      ("get", "/api/positions/{position_id}"), ("patch", "/api/positions/{position_id}"),
                      ("delete", "/api/positions/{position_id}"),
                      ("get", "/api/positions/{position_id}/sync-state")})),
    Family("saved-views", ROOT / "docs" / "api" / "openapi-saved-views.json", saved_views_openapi_document,
           frozenset({("get", "/api/saved-views"), ("post", "/api/saved-views"),
                      ("get", "/api/saved-views/{view_id}"), ("patch", "/api/saved-views/{view_id}"),
                      ("delete", "/api/saved-views/{view_id}")})),
    Family("publishers", ROOT / "docs" / "api" / "openapi-publishers.json", publishers_openapi_document,
           frozenset({("get", "/api/publishers"), ("post", "/api/publishers"),
                      ("delete", "/api/publishers/{publisher_id}"),
                      ("post", "/api/publishers/{publisher_id}/aliases"),
                      ("delete", "/api/publishers/{publisher_id}/aliases")})),
)
_METHODS = frozenset({"get", "put", "post", "delete", "patch", "head", "options", "trace"})


def operations(document: dict[str, Any]) -> frozenset[tuple[str, str]]:
    return frozenset((method, path) for path, item in document.get("paths", {}).items()
                     for method in item if method in _METHODS)


def checked_contract(family: Family) -> dict[str, Any]:
    """The family's fragment, refused on drift or an unreviewed operation."""
    checked_in = json.loads(family.fragment.read_text(encoding="utf-8"))
    generated = family.builder()
    if checked_in != generated:
        raise ValueError(f"{family.name} OpenAPI fragment differs from its builder")
    if operations(generated) != family.operations:
        raise ValueError(f"{family.name} operation inventory changed; review the allowlist")
    return generated


def _loopback_port(origin: str) -> int:
    parsed = urlsplit(origin)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("Schemathesis mutation scan requires an isolated loopback origin")
    return parsed.port


def library_epoch(origin: str) -> str:
    """The epoch the instance answers a read with; mutations must name it."""
    connection = http.client.HTTPConnection("127.0.0.1", _loopback_port(origin), timeout=8)
    try:
        connection.request("GET", "/api/positions", headers={"Host": f"127.0.0.1:{_loopback_port(origin)}"})
        response = connection.getresponse()
        response.read()
        epoch = response.getheader(EPOCH_HEADER)
    finally:
        connection.close()
    if response.status != 200 or not epoch or not epoch.isdigit():
        raise AssertionError("instance did not answer a Library epoch")
    return epoch


def live_contract_matches(origin: str, family: Family, document: dict[str, Any]) -> None:
    port = _loopback_port(origin)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    try:
        connection.request("GET", f"/api/openapi/{family.name}.json", headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        live = json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()
    if response.status != 200 or live != document:
        raise AssertionError(f"live {family.name} OpenAPI differs from the checked-in fragment")


def command(schema: Path, origin: str, epoch: str, binary: str) -> list[str]:
    _loopback_port(origin)
    if not schema.is_file():
        raise ValueError("missing local family schema")
    if not epoch.isdigit():
        raise ValueError("Library epoch must be a counter")
    return [
        binary, "run", str(schema), "--url", origin,
        "--header", f"Origin: {origin}",
        "--header", f"{EPOCH_HEADER}: {epoch}",
        "--phases", "examples,fuzzing",
        "--checks", CHECKS,
        "--mode", "all", "--max-examples", str(MAX_EXAMPLES),
        "--generation-allow-x00", "false",
        "--max-failures", "1",
        "--workers", "1", "--rate-limit", "50/s",
        "--seed", str(SEED), "--max-redirects", "0",
    ]


def scan(*, debug_local: bool = False) -> int:
    if debug_local and os.environ.get("GITHUB_ACTIONS"):
        raise RuntimeError("Raw diagnostic output is forbidden in Actions")
    if os.environ.get("PRKS_AUTH", "").strip():
        raise RuntimeError("Refusing PRKS_AUTH: the scan only targets its own disposable instance")
    binary = shutil.which("st")
    if not binary:
        raise RuntimeError("Pinned Schemathesis CLI not installed")
    contracts = [(family, checked_contract(family)) for family in FAMILIES]
    server = AppServer(seed_fn=seed_ux_tour_library)
    started = time.monotonic()
    failed = False
    try:
        server.start()
        epoch = library_epoch(server.origin)
        # Every live fragment before the first write, not family by family.
        for family, document in contracts:
            live_contract_matches(server.origin, family, document)
        for family, document in contracts:
            with tempfile.TemporaryDirectory(prefix="prks-schemathesis-mutations-") as directory:
                schema = Path(directory) / f"{family.name}.json"
                schema.write_text(json.dumps(document), encoding="utf-8")
                result = subprocess.run(command(schema, server.origin, epoch, binary), cwd=directory,
                                        capture_output=True, text=True, timeout=CLI_TIMEOUT, check=False)
            print(f"[schemathesis] family={family.name} operations={len(family.operations)} seed={SEED} "
                  f"examples_cap={MAX_EXAMPLES} elapsed={time.monotonic() - started:.2f}s exit={result.returncode}")
            if result.returncode:
                failed = True
                print_failure_diagnostics(result, server)
                if debug_local:
                    print(result.stdout[:20000], file=sys.stderr)
                    print(result.stderr[:3000], file=sys.stderr)
        if failed:
            print(f"[schemathesis] Reproduce on disposable fixtures with seed={SEED}.")
            return 1
        print("[schemathesis] bounded mutation scan completed")
        return 0
    finally:
        server.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--debug-local", action="store_true",
        help="Show full synthetic CLI failure locally only (forbidden in GitHub Actions)",
    )
    options = parser.parse_args()
    try:
        raise SystemExit(scan(debug_local=options.debug_local))
    except (ValueError, RuntimeError, AssertionError, subprocess.TimeoutExpired) as exc:
        print(f"[schemathesis] Mutation scan could not complete: {type(exc).__name__}")
        raise SystemExit(1) from None
