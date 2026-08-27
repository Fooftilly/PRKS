#!/usr/bin/env python3
"""Test-only static server for the Markdown sanitizer browser fixture.

Binds literal 127.0.0.1 on an ephemeral port. Serves the repository root
read-only so the fixture can load production files under frontend/.
Not used by the production PRKS server. Do not bind all interfaces.
"""
import http.server
import os
import sys

HOST = "127.0.0.1"


def main() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
    os.chdir(repo_root)
    httpd = http.server.HTTPServer((HOST, 0), http.server.SimpleHTTPRequestHandler)
    port = httpd.server_address[1]
    base = f"http://{HOST}:{port}/tests/browser/markdown_security.html"
    print(base, flush=True)
    print(base + "?dompurify=absent", flush=True)
    print(base + "?dompurify=unsupported", flush=True)
    print("Markdown security fixture (test-only, 127.0.0.1). Ctrl-C to stop.", file=sys.stderr, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
