#!/usr/bin/env python3
"""Seed an isolated PRKS instance and regenerate documentation screenshots."""
from __future__ import annotations

import argparse
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

for path in (ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from capture_demo_screenshots import capture  # noqa: E402
from seed_demo_library import seed  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as res:
                if 200 <= res.status < 500:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate PRKS documentation screenshots from isolated demo data."
    )
    parser.add_argument("--set", choices=("readme", "extra", "all"), default="all")
    args = parser.parse_args()

    SCRATCH.mkdir(parents=True, exist_ok=True)
    runtime = Path(tempfile.mkdtemp(prefix="runtime-", dir=SCRATCH))
    port = _free_port()
    origin = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env["PRKS_TESTING"] = "1"
    env["PRKS_STORAGE"] = str(runtime)
    env.pop("PRKS_FOR_PROCESSING_DIR", None)
    env["PRKS_LOG_FILE"] = str(runtime / "prks-errors.log")
    env["PYTHONUNBUFFERED"] = "1"

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
    )

    try:
        _wait(origin + "/api/works")
        seed(origin)
        capture(origin, args.set)
    except Exception as exc:
        print(f"Screenshot regeneration failed: {exc}", file=sys.stderr)
        return 1
    finally:
        _stop(proc)
        shutil.rmtree(runtime, ignore_errors=True)

    print("Documentation screenshots regenerated successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
