# Troubleshooting

This page provides first-response diagnosis. For exact current commands/configuration, also check the repository [README](https://github.com/Fooftilly/PRKS/blob/master/README.md).

## PRKS exits before startup

PRKS validates Python and exact runtime dependency pins before normal startup.

Check:

```bash
python --version
python -m pip install -r requirements.txt
```

Prefer the project virtual environment. On PEP 668 systems, create/use a venv rather than forcing a global pip install.

## Browser cannot reach PRKS

Confirm the process bind address and port.

Default local URL:

```text
http://127.0.0.1:8080
```

Testing mode normally uses port 8070.

If Docker is running, remember that container bind address and host publish address are different settings.

## LAN device cannot connect

The normal default is loopback only. LAN access requires an explicit non-loopback bind/publish configuration.

Before doing that, remember PRKS does not provide application-level authentication. Only expose it inside a trust boundary/access layer you intend.

## Search results are missing/stale

PDF text search uses a derived index. Use the Settings action to rebuild the PDF text index when needed.

Scanned/image-only PDFs may contain no extractable text because PRKS does not perform OCR.

## PDF annotations look out of sync

Structured annotation rows are canonical; PDF bytes are materialized output. Diagnose whether the annotation metadata operation succeeded and whether PDF materialization is stale rather than assuming the bytes are the source of truth.

## Offline change did not synchronize

Identify the domain and check whether that exact mutation family is currently durable in [docs/local-first-rollout-status.md](https://github.com/Fooftilly/PRKS/blob/master/docs/local-first-rollout-status.md).

If it is supported, distinguish:

- durable operation creation;
- local UI projection;
- server reachability;
- sync send/apply;
- acknowledgement/revision;
- conflict/reconciliation.

Do not use `navigator.onLine` as the sole diagnostic signal.

## Workspace page behaves differently in split view

Check whether code is using the active TabContext/root or assuming one global document route. Split view mounts multiple pages/resources at once.

## E2E is slow

Use targeted feature runs while iterating. For actual performance investigation, use the profiling/diagnostic mechanisms described in [docs/e2e-performance.md](https://github.com/Fooftilly/PRKS/blob/master/docs/e2e-performance.md) rather than weakening isolation.

## E2E hangs/intermittently stalls

Use runner diagnostics, one-worker reproduction, and last-failed/targeted selection. Browser-process recycling and stage diagnostics exist for known classes of test-infrastructure problems.

## Backup restore is rejected

Do not bypass validation by manually unpacking the archive into storage. Verify that the archive is a valid PRKS backup, is not corrupt/truncated, and is compatible with the running schema/software.

## Demo screenshot capture fails

The screenshot tooling expects a testing server and seeded synthetic/public-domain library. Run the demo seed before capture, and ensure the Playwright/Chrome assumptions of `scripts/capture_demo_screenshots.py` are satisfied.

Never point screenshot automation at the normal personal library.
