# Security and Operations

PRKS is primarily a local/self-hosted application. Its default deployment assumptions are part of the security model.

## Network and browser security

Every PRKS web process authenticates requests. `PRKS_AUTH` chooses how:

- `session` (the default): every request needs a signed-in session. Accounts sign in with a password; the owner and Library are created with [admin bootstrap](https://github.com/Fooftilly/PRKS/blob/master/docs/admin-bootstrap.md). Attempt limits, login devices and sessions are described in [login security](https://github.com/Fooftilly/PRKS/blob/master/docs/login-security.md).
- `loopback-owner` (the default of `--testing`): every request acts as the testing database's single owner. It is refused outside testing mode and unless the server is bound to a loopback address.

Direct runs bind **127.0.0.1** by default. Docker Compose publishes the host port on **127.0.0.1** by default. Reaching it from another machine requires an explicit `--host` or `PRKS_PUBLISH_HOST` override. Sign-in protects the Library, but PRKS serves plain HTTP: do not expose it beyond a trusted network without an HTTPS reverse proxy in front of it.

Local browser use through `http://127.0.0.1:8080` or `http://localhost:8080` works without extra Host configuration. LAN access by IP literal (after `PRKS_PUBLISH_HOST=0.0.0.0`) also needs no `PRKS_TRUSTED_HOSTS` setting.

Custom LAN DNS names must be listed exactly:

```bash
PRKS_PUBLISH_HOST=0.0.0.0 \
PRKS_TRUSTED_HOSTS=prks.home.arpa \
docker compose up -d
```

Malformed `PRKS_TRUSTED_HOSTS` entries refuse to start the server. This variable is for extra DNS hostnames on direct HTTP/LAN access. Behind an HTTPS reverse proxy, set `PRKS_PUBLIC_ORIGIN` instead; its host is accepted without listing it here.

The HTTP adapter validates `Host` on every request and requires `application/json` for JSON POST/PATCH bodies. Every state-changing `/api/` request, sign-in included, must come from the PRKS page itself: a `Sec-Fetch-Site` header, when sent, must be `same-origin`, and `Origin` must be the expected origin (`Origin: null` is refused) or, when there is no `Origin`, `Sec-Fetch-Site: same-origin` must be present. A script or other non-browser client that sends neither is refused. The expected origin is `PRKS_PUBLIC_ORIGIN` when set, otherwise the request's own `http` origin ([login security](https://github.com/Fooftilly/PRKS/blob/master/docs/login-security.md), "Cross-site requests"). PRKS does not send CORS headers and does not allow cross-origin API access.

These controls reduce accidental/cross-origin access and DNS-rebinding risk; they complement sign-in rather than replace it. Exposure to the public Internet without HTTPS is still unsafe.

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
