#!/usr/bin/env python3
"""Seed an isolated PRKS instance and regenerate documentation screenshots."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SCRATCH = ROOT / "data_screenshots"
SERVER_START_ATTEMPTS = 5

for path in (ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from capture_demo_screenshots import capture  # noqa: E402
from seed_demo_library import seed  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _address_in_use(text: str) -> bool:
    markers = (
        "Address already in use",
        "Only one usage of each socket address",
        "EADDRINUSE",
    )
    return any(marker in text for marker in markers)


def _wait_for_child(url: str, proc: subprocess.Popen, timeout: float = 30.0) -> list:
    """Wait until the spawned child answers; fail if it exits or looks foreign."""
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        code = proc.poll()
        if code is not None:
            raise RuntimeError(
                f"PRKS exited before becoming ready at {url} (exit {code})"
            )
        try:
            with urllib.request.urlopen(url, timeout=2) as res:
                if not (200 <= res.status < 500):
                    last = RuntimeError(f"unexpected HTTP status {res.status}")
                    time.sleep(0.1)
                    continue
                body = res.read()
                data = json.loads(body.decode())
                if not isinstance(data, list):
                    raise RuntimeError(
                        f"Expected a JSON list from {url}; refusing to use this endpoint"
                    )
                return data
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last = exc
        time.sleep(0.1)
    raise RuntimeError(f"PRKS did not become ready at {url}: {last}")


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=8)


def _start_isolated_server(runtime: Path) -> tuple[subprocess.Popen, str, Path, Path]:
    """Spawn --testing PRKS on a free port; retry only genuine bind conflicts."""
    stdout_path = runtime / "server.stdout"
    stderr_path = runtime / "server.stderr"
    last_error: Exception | None = None

    for attempt in range(SERVER_START_ATTEMPTS):
        port = _free_port()
        origin = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env["PRKS_TESTING"] = "1"
        env["PRKS_STORAGE"] = str(runtime)
        env.pop("PRKS_FOR_PROCESSING_DIR", None)
        env["PRKS_LOG_FILE"] = str(runtime / "prks-errors.log")
        env["PYTHONUNBUFFERED"] = "1"

        stdout_handle = open(stdout_path, "w", encoding="utf-8")
        stderr_handle = open(stderr_path, "w", encoding="utf-8")
        proc = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "prks_app.py"),
                "--testing",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
        )
        try:
            works = _wait_for_child(origin + "/api/works", proc)
            # Fresh isolated storage must be empty before seeding. A non-empty
            # response means we reached someone else's library on this port.
            if works:
                raise RuntimeError(
                    "Refusing to seed: /api/works is not empty, so this endpoint "
                    "is not the isolated testing storage we just started."
                )
            stdout_handle.close()
            stderr_handle.close()
            return proc, origin, stdout_path, stderr_path
        except Exception as exc:
            last_error = exc
            out = ""
            err = ""
            try:
                stdout_handle.flush()
                stderr_handle.flush()
            except OSError:
                pass
            _stop(proc)
            stdout_handle.close()
            stderr_handle.close()
            try:
                out = stdout_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                out = ""
            try:
                err = stderr_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                err = ""
            retryable = (
                attempt + 1 < SERVER_START_ATTEMPTS and _address_in_use(out + err)
            )
            if retryable:
                continue
            raise RuntimeError(
                f"Isolated PRKS failed to start on {origin}: {exc}\n"
                f"stdout:\n{out[-2000:]}\nstderr:\n{err[-2000:]}"
            ) from exc

    raise RuntimeError(f"Isolated PRKS could not obtain a free port: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate PRKS documentation screenshots from isolated demo data."
    )
    parser.add_argument("--set", choices=("readme", "extra", "all"), default="all")
    args = parser.parse_args()

    SCRATCH.mkdir(parents=True, exist_ok=True)
    runtime = Path(tempfile.mkdtemp(prefix="runtime-", dir=SCRATCH))
    proc: subprocess.Popen | None = None

    try:
        proc, origin, _stdout_path, _stderr_path = _start_isolated_server(runtime)
        seed(origin)
        capture(origin, args.set)
    except Exception as exc:
        print(f"Screenshot regeneration failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if proc is not None:
            _stop(proc)
        shutil.rmtree(runtime, ignore_errors=True)

    print("Documentation screenshots regenerated successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
