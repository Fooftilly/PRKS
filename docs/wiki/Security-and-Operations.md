# Security and Operations

PRKS is primarily a local/self-hosted application. Its default deployment assumptions are part of the security model.

## Network and browser security

PRKS is a single-user app with **no built-in authentication**. Direct runs bind **127.0.0.1** by default. Docker Compose publishes the host port on **127.0.0.1** by default. Reaching it from another machine requires an explicit `--host` or `PRKS_PUBLISH_HOST` override. Do that only on a trusted network.

Local browser use through `http://127.0.0.1:8080` or `http://localhost:8080` works without extra Host configuration. LAN access by IP literal (after `PRKS_PUBLISH_HOST=0.0.0.0`) also needs no `PRKS_TRUSTED_HOSTS` setting.

Custom LAN DNS names must be listed exactly:

```bash
PRKS_PUBLISH_HOST=0.0.0.0 \
PRKS_TRUSTED_HOSTS=prks.home.arpa \
docker compose up -d
```

Malformed `PRKS_TRUSTED_HOSTS` entries refuse to start the server. This variable is for extra DNS hostnames on direct HTTP/LAN access, not reverse-proxy or HTTPS termination.

The HTTP adapter validates `Host` on every request, rejects cross-origin state-changing `/api/` requests when `Origin` is supplied (`Origin: null` included), and requires `application/json` for JSON POST/PATCH bodies. Missing `Origin` remains allowed for local scripts and non-browser clients. PRKS does not send CORS headers and does not allow cross-origin API access.

These controls reduce accidental/cross-origin access and DNS-rebinding risk. They are not authentication. Public Internet exposure is still unsafe.

Research notes (`works.text_content`) are stored as raw Markdown, including literal `[[concept:Name]]` and `[[argument:A-ID|Label]]` markup. A preprocessor turns recognized references into internal hash links, then EasyMDE/Marked renders Markdown, then a pinned local DOMPurify allowlist sanitizes the preview (`frontend/vendor/dompurify`, `frontend/js/markdown-sanitize.js`). Markup inside code spans/fences or escaped as `\[[` is not a semantic reference. Arbitrary or active HTML is not a supported contract: unsafe tags, attributes, and URL schemes are stripped from the preview only. Sanitization never rewrites saved Markdown.

Frontend libraries (Inter, EasyMDE, CodeMirror, Lucide, DOMPurify, Cytoscape, the PDF viewer) are local files under `frontend/vendor/`. Node is not a runtime dependency. Docker does not run npm.

## Remote person images

Remote portrait fetching is constrained to reduce SSRF/file-ingestion risk. Direct public HTTP/HTTPS resources are required; private/local/link-local targets and redirects are refused, download/decode work is bounded, and accepted images are transcoded before caching.

## Logging and diagnostics

Operational logging and performance diagnostics are documented in [Configuration and Operations](Configuration-and-Operations.md). PRKS deliberately avoids treating increased log verbosity as permission to log research contents.

## Backup/restore

Restore stages and validates archives before replacing live data. Manual extraction over live storage bypasses those checks and is not the supported recovery procedure.

## Security reporting

Use [SECURITY.md](https://github.com/Fooftilly/PRKS/blob/master/SECURITY.md) for the current vulnerability-reporting process and scope.

Security reproductions should not be posted as ordinary UX/UI issues when they would disclose sensitive exploit details.

## Automated analysis

The repository includes CodeQL and static-analysis workflows. These complement, rather than replace, application-level invariants and security-specific tests.
