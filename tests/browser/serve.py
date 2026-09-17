#!/usr/bin/env python3
"""Test-only static server for browser fixtures.

Binds literal 127.0.0.1 on an ephemeral port. Serves the repository root
read-only so fixtures can load production files under frontend/.
Not used by the production PRKS server. Do not bind all interfaces.
"""
import http.server
import os
import sys

HOST = "127.0.0.1"


class FixtureHandler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".wasm": "application/wasm",
        ".mjs": "text/javascript",
    }

    # Production CSS/JS use app-root paths (/vendor, /css). Map them onto
    # frontend/ when this fixture server is rooted at the repository.
    _APP_ROOT_PREFIXES = (
        "/vendor/",
        "/css/",
        "/js/",
        "/icons/",
        "/logo.svg",
        "/favicon.svg",
    )

    def translate_path(self, path):
        mapped = path
        for prefix in self._APP_ROOT_PREFIXES:
            if path == prefix.rstrip("/") or path.startswith(prefix):
                mapped = "/frontend" + path
                break
        return super().translate_path(mapped)


def main() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
    os.chdir(repo_root)
    httpd = http.server.HTTPServer((HOST, 0), FixtureHandler)
    port = httpd.server_address[1]
    origin = f"http://{HOST}:{port}"
    print(f"{origin}/tests/browser/markdown_security.html", flush=True)
    print(origin + "/tests/browser/markdown_security.html?dompurify=absent", flush=True)
    print(origin + "/tests/browser/markdown_security.html?dompurify=unsupported", flush=True)
    print(f"{origin}/tests/browser/pdf_viewer.html", flush=True)
    print(f"{origin}/tests/browser/pdf_annotation_fidelity.html", flush=True)
    print(f"{origin}/tests/browser/navigation.html", flush=True)
    print(f"{origin}/tests/browser/design_system.html?theme=light", flush=True)
    print(f"{origin}/tests/browser/design_system.html?theme=dark", flush=True)
    print("Browser fixtures (test-only, 127.0.0.1). Ctrl-C to stop.", file=sys.stderr, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
