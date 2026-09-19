# Security Policy

## Supported Versions

PRKS does not currently publish versioned stable release branches. Security fixes are applied to the current `master` branch.

| Version | Supported |
| ------- | --------- |
| Current `master` | :white_check_mark: |
| Older commits, snapshots, and forks | :x: |

If you are running an older checkout, update to the latest supported revision before reporting an issue that may already have been fixed.

## Reporting a Vulnerability

Please report suspected security vulnerabilities privately.

The preferred reporting channel is GitHub's private vulnerability reporting for this repository:

1. Open the repository's **Security** tab.
2. Choose **Report a vulnerability**.
3. Include a concise description of the issue, affected component, reproduction conditions, and potential impact.

If private vulnerability reporting is not available, do not post exploit details, secrets, private data, or a full proof of concept in a public issue. Instead, open a minimal public issue stating that you need a private security-reporting channel, without disclosing sensitive technical details.

When reporting a vulnerability:

- Test only against systems and data you own or are explicitly authorized to test.
- Use the smallest reproduction necessary to demonstrate the problem.
- Do not include real research-library contents, credentials, private URLs, database contents, backups, or other sensitive data.
- Note the PRKS revision or commit you tested and whether you were running directly or with Docker.
- Describe any relevant deployment details, such as bind address, reverse proxy, or trusted-host configuration, without including secrets.

Maintainers will make a best effort to acknowledge a report within 7 days and provide a triage decision or meaningful status update within 14 days. Complex issues may require additional time. If a report is accepted, the goal is to coordinate remediation and disclosure so users have a reasonable opportunity to update before technical details are made public. If a report is declined, an explanation will be provided when practical.

Please do not publicly disclose an unresolved vulnerability before the maintainer has had a reasonable opportunity to investigate and prepare a fix.

## Security Scope

Reports are especially useful when they involve issues such as:

- unauthorized access to PRKS data beyond the documented deployment boundary;
- bypasses of Host, Origin, path, upload, or storage-root validation;
- path traversal or unintended filesystem access;
- server-side request forgery or unsafe remote-resource handling;
- arbitrary code execution or command execution;
- injection vulnerabilities;
- unsafe backup or restore behavior;
- exposure of private research content through logs, API responses, caches, or generated files;
- security boundary failures in PDF, image, archive, or other untrusted-input processing;
- vulnerabilities in bundled or runtime dependencies that materially affect PRKS.

PRKS is currently a single-user application with **no built-in authentication**. Direct runs bind to loopback by default, and Docker Compose publishes to host loopback by default. Exposing PRKS directly to an untrusted network or the public Internet without an appropriate access-control layer is not a supported deployment model. A report that only states that an intentionally exposed PRKS instance can be accessed without logging in is therefore not, by itself, considered a vulnerability.

A bypass of the documented network, Host, Origin, storage, or input-validation protections may still be a valid security issue and should be reported privately.

## Security Updates

Security fixes will normally be committed to the supported branch and may be accompanied by a GitHub Security Advisory when appropriate. Users should update to the latest supported revision after a security fix is published.

PRKS does not currently operate a bug bounty program or guarantee monetary rewards for vulnerability reports.
