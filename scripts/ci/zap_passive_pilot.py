#!/usr/bin/env python3
"""Passive-only OWASP ZAP pilot on a disposable PRKS server (#860). Advisory and manual.

Starts a disposable ``--testing`` PRKS server on loopback in session-auth mode
(its own database on the ``PRKS_TEST_DATABASE_URL`` PostgreSQL server, an owner
and one editor, synthetic UX-tour fixtures) and a pinned ZAP container in
daemon mode, switched to *safe* mode so it can neither spider nor attack. The
pilot itself then sends a fixed allowlist of requests through ZAP's proxy:
anonymous, signed in as the owner, and signed in as the editor. ZAP only
watches that traffic with its passive rules.

Afterwards it checks that:

- every request got the answer this allowlist expects (so a broken fixture or
  a weakened check is a problem, not a quiet pass);
- ZAP's history holds exactly the pilot's requests (ZAP sent none of its own);
- no Library content table changed.

The summary is bounded and privacy-safe: ZAP and PRKS versions, the image
digest, the tested route templates with their answers, and per passive rule
its id, name, risk, confidence, count and route templates. No dynamic or
private URL values, evidence, cookies or bodies (the fixed synthetic search
query stays in its route). Alerts are advisory and do not
fail the run; only the checks above do. Never targets anything but the
server it started.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ZAP 2.17.0 (ghcr.io/zaproxy/zaproxy:stable), pinned by digest.
ZAP_IMAGE = "ghcr.io/zaproxy/zaproxy@sha256:9624669db72f54d86c4b46a7064518bd9bf79d8c61a4c31befa9923489f328af"
ZAP_START_TIMEOUT = 180
PASSIVE_TIMEOUT = 120
REQUEST_TIMEOUT = 20
EDITOR = "editor"
SEARCH_TERM = "Graph"


@dataclass(frozen=True)
class Step:
    """One allowlisted request: who sends it, and the answer it must get."""

    caller: str  # "anonymous", "owner" or "editor"
    method: str
    template: str  # the route as reported; ``{work}`` is filled from the seed
    expect: int
    login: str | None = None  # POST /api/login as this account
    wrong_password: bool = False


STEPS: tuple[Step, ...] = (
    Step("anonymous", "GET", "/", 200),
    Step("anonymous", "GET", "/healthz", 200),
    Step("anonymous", "GET", "/api/session", 401),
    Step("anonymous", "GET", "/api/works", 401),
    Step("anonymous", "GET", "/api/works/{work}", 401),
    Step("anonymous", "POST", "/api/login", 401, login="owner", wrong_password=True),
    Step("owner", "POST", "/api/login", 200, login="owner"),
    Step("owner", "GET", "/", 200),
    Step("owner", "GET", "/api/session", 200),
    Step("owner", "GET", "/api/works", 200),
    Step("owner", "GET", "/api/works/{work}", 200),
    Step("owner", "GET", "/api/works/{work}/annotations", 200),
    Step("owner", "GET", "/api/tags", 200),
    Step("owner", "GET", "/api/persons", 200),
    Step("owner", "GET", "/api/publishers", 200),
    Step("owner", "GET", "/api/search?q=" + SEARCH_TERM, 200),
    Step("owner", "GET", "/api/settings", 200),
    Step("owner", "GET", "/api/research-graph", 200),
    Step("owner", "GET", "/api/works/unknown-work", 404),
    Step("editor", "POST", "/api/login", 200, login=EDITOR),
    Step("editor", "GET", "/api/works", 200),
    Step("editor", "GET", "/api/backups/download", 403),
)


def require_loopback(origin: str) -> int:
    """The port of an ``http://127.0.0.1:<port>`` origin; anything else is refused."""
    parsed = urllib.parse.urlsplit(origin)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("the ZAP pilot targets only its own loopback server")
    return parsed.port


def zap_command(name: str, port: int, api_key: str) -> list[str]:
    """``docker run`` for a daemon ZAP bound to loopback, with no add-on updates."""
    return [
        "docker", "run", "--detach", "--rm", "--name", name, "--network", "host",
        "--memory", "2g", "--cpus", "2", ZAP_IMAGE,
        "zap.sh", "-daemon", "-silent", "-host", "127.0.0.1", "-port", str(port),
        "-config", f"api.key={api_key}",
        "-config", "api.addrs.addr.name=127.0.0.1", "-config", "api.addrs.addr.regex=false",
        "-config", "database.recoverylog=false",
    ]


def summarize_alerts(alerts: list[dict[str, Any]], templates: dict[str, str]) -> list[dict[str, Any]]:
    """One row per passive rule: id, name, risk, confidence, count and route templates only."""
    rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for alert in alerts:
        key = (str(alert.get("pluginId", "")), str(alert.get("alert", "")),
               str(alert.get("risk", "")), str(alert.get("confidence", "")))
        row = rows.setdefault(key, {"plugin_id": key[0], "name": key[1], "risk": key[2],
                                    "confidence": key[3], "count": 0, "routes": set()})
        row["count"] += 1
        row["routes"].add(templates.get(str(alert.get("url", "")), "(other)"))
    order = {"High": 0, "Medium": 1, "Low": 2, "Informational": 3}
    return [dict(row, routes=sorted(row["routes"]))
            for row in sorted(rows.values(), key=lambda row: (order.get(row["risk"], 4), row["plugin_id"]))]


def exchanged(messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(method, URL) of each history message that went to the server.

    ZAP's history also holds site-tree parent nodes (``/api`` for
    ``/api/works``), which were never sent and have no response status.
    """
    out = []
    for message in messages:
        request_line = str(message.get("requestHeader", "")).split("\r\n", 1)[0].split(" ")
        status_line = str(message.get("responseHeader", "")).split("\r\n", 1)[0].split(" ")
        if len(request_line) >= 2 and len(status_line) >= 2 and status_line[1] not in ("", "0"):
            out.append((request_line[0], request_line[1]))
    return out


class Zap:
    """The ZAP daemon's JSON API on loopback."""

    def __init__(self, port: int, api_key: str) -> None:
        self.port = port
        self.api_key = api_key

    def call(self, path: str, **params: str) -> dict[str, Any]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=REQUEST_TIMEOUT)
        try:
            query = urllib.parse.urlencode(params)
            conn.request("GET", f"/JSON/{path}/" + (f"?{query}" if query else ""),
                         headers={"X-ZAP-API-Key": self.api_key})
            response = conn.getresponse()
            body = response.read()
        finally:
            conn.close()
        if response.status != 200:
            raise RuntimeError(f"ZAP API {path} answered {response.status}")
        return json.loads(body)

    def wait_ready(self) -> str:
        deadline = time.monotonic() + ZAP_START_TIMEOUT
        while True:
            try:
                return str(self.call("core/view/version")["version"])
            except (OSError, RuntimeError, ValueError, http.client.HTTPException):
                if time.monotonic() > deadline:
                    raise RuntimeError("ZAP did not start") from None
                time.sleep(2)

    def wait_passive(self) -> bool:
        deadline = time.monotonic() + PASSIVE_TIMEOUT
        while int(self.call("pscan/view/recordsToScan")["recordsToScan"]):
            if time.monotonic() > deadline:
                return False
            time.sleep(1)
        return True


def send(zap_port: int, origin: str, step: Step, path: str, cookie: str | None,
         password: str) -> tuple[int, str | None]:
    """Send one allowlisted request through ZAP's proxy; answer its status and any session cookie."""
    port = require_loopback(origin)
    headers = {"Host": f"127.0.0.1:{port}", "Accept": "application/json, text/html"}
    body = None
    if step.login is not None:
        secret = "not the password" if step.wrong_password else password
        body = json.dumps({"login": step.login, "password": secret}).encode()
        headers.update({"Content-Type": "application/json", "Origin": origin})
    if cookie:
        headers["Cookie"] = cookie
    conn = http.client.HTTPConnection("127.0.0.1", zap_port, timeout=REQUEST_TIMEOUT)
    try:
        conn.request(step.method, origin + path, body=body, headers=headers)
        response = conn.getresponse()
        response.read()
        set_cookie = [value.split(";", 1)[0] for key, value in response.getheaders() if key.lower() == "set-cookie"]
    finally:
        conn.close()
    return response.status, "; ".join(set_cookie) or None


def fingerprint(conninfo: str, library_id: str) -> dict[str, tuple[int, str]]:
    """A digest of every Library content table's rows for the served Library."""
    import psycopg
    from psycopg import sql

    from backend.domain.maintenance import tables

    query = sql.SQL("SELECT count(*), md5(coalesce(string_agg(t::text, ',' ORDER BY t::text), ''))"
                    " FROM {} t WHERE t.library_id = %s")
    out = {}
    with psycopg.connect(conninfo, connect_timeout=10) as conn:
        for table in tables.BACKUP_CONTENT_TABLES:
            schema, name = table.split(".")
            row = conn.execute(query.format(sql.Identifier(schema, name)), (library_id,)).fetchone()
            out[table] = (int(row[0]), str(row[1])) if row else (0, "")
    return out


# A real server's settings: the pilot's server must not inherit them. A public
# origin would also make every login fail its Origin check.
INHERITED_REFUSED = ("PRKS_AUTH", "PRKS_DATABASE_URL", "PRKS_PUBLIC_ORIGIN")


def summary_path(path: Path) -> Path:
    """A ``.json`` summary path inside the working directory; anything else is refused."""
    root = Path.cwd().resolve()
    resolved = (root / path).resolve()
    if resolved.suffix != ".json" or not resolved.is_relative_to(root):
        raise ValueError("summary paths must be .json files inside the working directory")
    return resolved


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run(out: Path | None) -> int:
    out = summary_path(out) if out is not None else None
    if any(os.environ.get(name, "").strip() for name in INHERITED_REFUSED):
        raise RuntimeError("refusing an inherited " + ", ".join(INHERITED_REFUSED) + ": the pilot uses only its own server")
    if not os.environ.get("PRKS_TEST_DATABASE_URL"):
        raise RuntimeError("PRKS_TEST_DATABASE_URL must name a disposable PostgreSQL server")
    if not shutil.which("docker"):
        raise RuntimeError("Docker is needed for the pinned ZAP image")
    from backend.domain.maintenance.format import PRKS_VERSION
    from tests.e2e.harness import AppServer
    from tests.e2e.postgres_identity import PASSWORD
    from tests.pg.harness import get_harness
    from tests.ux_tour.fixtures import seed_ux_tour_library

    started = time.monotonic()
    server = AppServer(seed_fn=seed_ux_tour_library, auth="session", members=(EDITOR,))
    zap_port, api_key, name = free_port(), secrets.token_hex(16), f"prks-zap-{secrets.token_hex(4)}"
    container = False
    try:
        # Inside the try: a seed failure after provisioning still drops the
        # database and the temporary storage root (AppServer.stop is safe on
        # a partial start).
        server.start()
        if server.library is None:
            raise RuntimeError("the pilot server did not start")
        require_loopback(server.origin)
        conninfo = get_harness().conninfo(server.library.database)
        before = fingerprint(conninfo, server.library.library_id)
        subprocess.run(zap_command(name, zap_port, api_key), check=True, capture_output=True, timeout=120)
        container = True
        zap = Zap(zap_port, api_key)
        version = zap.wait_ready()
        # Safe mode: no spider, no active scan, nothing ZAP could send by itself.
        zap.call("core/action/setMode", mode="safe")
        templates: dict[str, str] = {}
        answers, found = [], []
        cookies: dict[str, str | None] = {"anonymous": None}
        for step in STEPS:
            path = step.template.format(work=server.ids["work_a"])
            templates[server.origin + path] = step.template
            status, cookie = send(zap_port, server.origin, step, path, cookies.get(step.caller), PASSWORD)
            if step.login is not None and not step.wrong_password:
                cookies[step.caller] = cookie
            answers.append({"caller": step.caller, "method": step.method, "route": step.template,
                            "expected": step.expect, "status": status})
            if status != step.expect:
                found.append(f"{step.caller} {step.method} {step.template}: {status}, expected {step.expect}")
        passive_done = zap.wait_passive()
        if not passive_done:
            found.append("passive scan did not finish")
        sent = exchanged(zap.call("core/view/messages", baseurl=server.origin)["messages"])
        if sorted(sent) != sorted((step.method, server.origin + step.template.format(work=server.ids["work_a"]))
                                  for step in STEPS):
            found.append(f"ZAP history holds {len(sent)} exchanges, not exactly the pilot's {len(STEPS)}")
        alerts = zap.call("alert/view/alerts", baseurl=server.origin, start="0", count="1000")["alerts"]
        after = fingerprint(conninfo, server.library.library_id)
        changed = sorted(table for table in before if before[table] != after[table])
        if changed:
            found.append("Library content changed: " + ", ".join(changed))
    finally:
        try:
            if container:
                subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=60, check=False)
        finally:
            server.stop()
    summary = {
        "pilot": "zap-passive", "zap_version": version, "zap_image": ZAP_IMAGE, "prks_version": PRKS_VERSION,
        "mode": "safe, passive rules only", "requests": answers,
        "alerts": summarize_alerts(alerts, templates), "problems": found,
        "elapsed_s": round(time.monotonic() - started, 1),
    }
    text = json.dumps(summary, indent=1, sort_keys=True)
    if out is not None:
        out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if found else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="write the JSON summary here as well")
    options = parser.parse_args(argv)
    try:
        return run(options.out)
    except (ValueError, RuntimeError, OSError, KeyError, http.client.HTTPException,
            subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"[zap] pilot could not complete: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
