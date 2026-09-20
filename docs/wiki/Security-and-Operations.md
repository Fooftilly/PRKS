# Security and Operations

PRKS is primarily a local/self-hosted application. Its default deployment assumptions matter to its security model.

## Network binding

The default process binds to `127.0.0.1:8080`, limiting access to the local machine.

Binding to `0.0.0.0` or otherwise publishing PRKS to a LAN/VPN changes the trust boundary.

PRKS currently has no application-level authentication. Do not expose it to untrusted networks without an access layer you control.

Docker Compose publishes host loopback by default even though the container process listens on all container interfaces.

## Sensitive research data

The library database, managed files, notes, annotations, and `.prks-backup` files can contain private research information. Store backups with the same care as the live library.

Browser-local workspace state is separate from server backups.

## Remote person images

Remote portrait fetching is constrained to reduce SSRF/file-ingestion risk. Direct public HTTP/HTTPS resources are required; private/local/link-local targets and redirects are refused, download/decode work is bounded, and accepted images are transcoded before caching.

## Logging

Logging is configured to avoid turning normal diagnostics into a second copy of sensitive research content. Persistent error logging and retention are configurable.

Use the privacy-safe logging helpers/policies already present instead of adding raw request/document dumps.

## Backup/restore

Restore stages and validates archives before replacing live data. Manual extraction over live storage bypasses those checks and is not the supported recovery procedure.

## Security reporting

Use [SECURITY.md](https://github.com/Fooftilly/PRKS/blob/master/SECURITY.md) for the current vulnerability-reporting process and scope.

Security reproductions should not be posted as ordinary UX/UI issues when they would disclose sensitive exploit details.

## Automated analysis

The repository includes CodeQL and static-analysis workflows. These complement, rather than replace, application-level invariants and security-specific tests.
