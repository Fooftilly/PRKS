# Evidence-first verification

The repository's existing AGENTS.md and scoped contracts remain authoritative. This workflow applies the engineering approach discussed in [All Code Is Slop (Except Mine)](https://www.youtube.com/watch?v=O_zcc6Xl12g) to PRKS's existing tools. It does not grant new work, production access or merge permission.

## Turn recurring corrections into enforceable constraints

When a recurring review or audit finding is confirmed, first locate its owner and existing coverage. Use the strongest proportionate mechanism:

| Mechanism | Existing PRKS home |
| --- | --- |
| Make the invalid operation unavailable | Canonical domain, storage, sync and frontend-owner APIs; DTOs and types |
| Reject a forbidden source pattern | Ruff, Pyright, ESLint or scripts/check_invariants.py; use existing gate tests |
| Prove semantic behavior | Existing Python, PostgreSQL, Node or Vitest tests |
| Explore event ordering and state invariants | Existing bounded Hypothesis / fast-check tests, explicit finite event schedules |
| Prove user-visible behavior | Existing Python Playwright E2E and synthetic fixtures, per tests/e2e/AGENTS.md |
| Explain judgment or an external constraint | Scoped documentation, module docstrings, skills |

Prefer changing the authoritative API over creating a second wrapper. Add a static rule only for a clear forbidden pattern with an approved alternative; test accepted and rejected examples. Measure the signal and cost of new gates; do not change required check names or introduce broad style bans. An invariant that depends on data or timing usually belongs in semantic tests rather than a source-pattern heuristic.

Keep meaningful comments until an equivalent contract is enforced; retain explanations of non-obvious why, external requirements, public APIs and tool directives. Do not count comment deletion as proof.

## Verification for a feature

Use the current feature and owner documentation as the map (docs/wiki/, DESIGN.md, docs/agent-context/sync-map.md, docs/e2e-coverage-maps.md). Do not create a second global feature registry. For the changed behavior record:

- entry point, state owner and authoritative storage;
- observable success, refusal, cancellation and relevant failure/recovery paths;
- actual commands and assertions, tested commit, test tier, and evidence gaps;
- synthetic data only, with the disposable PostgreSQL/test storage required by the scoped policy.

Test deterministic contracts below the browser boundary; use a real browser for focus, layout, navigation, IndexedDB or service-worker lifecycle, and other inherently browser-visible behaviors. Use observable readiness and controlled promises, not arbitrary sleeps or retries. Preserve existing gates. Review the implementation and evidence independently when possible.

## First bounded ordering check

The canonical ACK reconciliation contract is in frontend-app/src/sync/reconcile.ts, with existing example tests in reconcile.test.ts. The companion reconcile.order.test.ts enumerates both completion orders for two overlapping authoritative re-reads, with and without owner replacement at each possible position. It checks after each event that the older completion never writes into the newer owner and that a superseded owner cannot be repainted.

Run from frontend-app/: `npm test -- src/sync/reconcile.order.test.ts`. This is a bounded executable test of the real implementation, not a formal proof of the whole sync protocol. The existing browser and PostgreSQL tests still own their respective boundaries.

## Further work

Extend the existing #391 model/property testing roadmap and #651 test-effectiveness roadmap instead of opening a competing tracker. Consider a small formal specification only for a specific high-risk contract that bounded implementation tests cannot adequately explore (for example distributed durable-operation replay and revision ordering). State model assumptions, compare traces with production tests, and assess tooling/CI cost before adoption. Do not claim that a checked model proves implementation correctness without a demonstrated connection to the actual implementation.
