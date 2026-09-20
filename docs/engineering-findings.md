# Engineering Audit Findings

GitHub Issues are the canonical store for PRKS engineering/audit findings.

Audit findings are observations about the current implementation: architecture debt,
maintainability risks, correctness concerns, workflow/tooling opportunities, performance
ideas, questionable implementations, or other engineering improvements discovered during
review. They are intentionally separate from the product roadmap.

## Agent rules

- Search open GitHub issues whose title starts with `[Audit Finding]` before proposing
  broad architecture, maintainability, tooling, or refactoring work.
- A finding marked **Candidate audit finding — not approved implementation work** is
  informational only. Do not implement it merely because it exists.
- Work on a finding only when the maintainer explicitly assigns/approves it or the current
  task explicitly names that finding/issue.
- Before acting on an older finding, re-verify it against current `master`. Code may have
  changed since its recorded `Last verified` commit.
- Prefer updating/closing an existing finding over creating a duplicate.
- Roadmap issues (`[Roadmap]`) describe planned product direction. Audit findings do not.
- If a finding conflicts with `AGENTS.md`, the existing engineering policy wins until the
  maintainer explicitly approves changing that policy.

## Finding identity and lifecycle

Each finding has a stable ID such as `EF-001` in its issue body and a title beginning
with `[Audit Finding]`.

Recommended lifecycle:

1. **Candidate** — recorded for consideration; not approved work.
2. **Accepted** — maintainer agrees the problem/direction is worth pursuing.
3. **In progress** — an implementation/research task has been explicitly assigned.
4. **Resolved** — fixed, no longer applicable, or intentionally superseded.
5. **Rejected** — reviewed and deliberately not pursued.

Until dedicated repository labels are added, the `[Audit Finding]` title prefix is the
canonical searchable taxonomy. Labels may later mirror the lifecycle without changing
this contract.

## Current findings

| ID | Issue | Area | Status |
| --- | --- | --- | --- |
| EF-001 | #66 — bounded retention/compaction for sync operation ledger | architecture / sync / storage | Candidate |
| EF-002 | #67 — reduce `backend/server.py` routing/orchestration concentration | architecture / maintainability | Candidate |
| EF-003 | #68 — continue decomposing `backend/db_manager.py` by domain | architecture / maintainability | Candidate |
| EF-004 | #69 — Pyright CI disables normal type checking | tooling / correctness | Candidate |
| EF-005 | #70 — remove misleading server import-time side effects | maintainability / startup | Candidate |

This table is a checkout-visible index, not the source of truth for discussion or status.
When GitHub is available, read the issue itself for the current state.
