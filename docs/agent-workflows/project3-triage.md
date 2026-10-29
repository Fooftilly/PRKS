# Reviewed batch issue triage — Project #3

Use `scripts/project3_triage.py` to replace repetitive Project #3 edits after a maintainer has examined the issues. This is an **explicitly approved batch editor**, not a scheduler or autonomous triage agent. It complements `scripts/agent-queue.sh` (read only), `project3-readiness.yml` (read-only snapshot) and `project-sync` (PR lifecycle). The existing Project Agent field remains the source of truth.

The tool only changes **existing PRKS issue items** in Project #3; it never adds items, edits PRs, changes GitHub labels, changes dependencies, or modifies Project settings. It accepts only the `Agent`, `Status` and `Execution` single-select fields. Every proposed edit requires a human-authored reason and an explicit declaration that its accepted roadmap and overlapping PRs were checked. **It does not automatically determine whether work is obsolete.** If uncertain, leave it unapproved.

## Authentication

From an authenticated PRKS clone, `gh auth login` with the classic `project` scope for the Project V2 GraphQL operations. The tool uses `gh auth token` to read the token into memory, or an existing `GH_TOKEN`, `GITHUB_TOKEN`, or `PROJECT3_TRIAGE_TOKEN` environment value. No new secret is required. Native blockers are read through GitHub's **GET** issue-dependencies REST endpoint; a read failure prevents changes.

## Example: one reviewed batch

Create `~/prks-triage-proposals.json` *outside the repository* (no PRKS production data), with only the fields that need changing:

```json
{
  "items": [
    {
      "number": 815,
      "set": {"Agent": "Cursor Cloud", "Status": "Ready", "Execution": "Agent"},
      "reason": "Confirmed frontend correctness issue; replacement roadmap and open PRs reviewed.",
      "roadmap_checked": true,
      "overlap_checked": true
    }
  ]
}
```

The input may have 1–25 issues; agent aliases such as `cursor`, `claude` and `codex` normalize to the live Project options (`Cursor Cloud`, `Claude Code`, `ChatGPT/Codex`). It does not infer execution or readiness from the Agent field. Setting all three fields is optional.

```bash
python scripts/project3_triage.py propose \
  --input ~/prks-triage-proposals.json \
  --out ~/prks-triage-plan.json
```

The `propose` command reads live Project/issue states, labels, and native open blockers, and writes an **unapproved** JSON plan with explicit before-values. It sends no mutation. Review the plan carefully, then change **only the intended rows** from `"approved": false` to `"approved": true`. Recheck target-architecture relevance and overlapping PRs as a maintainer decision, not an automatic inference.

```bash
python scripts/project3_triage.py check --plan ~/prks-triage-plan.json
# Copy the "Plan SHA-256" value from the check output:
python scripts/project3_triage.py apply --plan ~/prks-triage-plan.json \
  --plan-sha256 "<paste-exact-sha256-from-check>"
```

`check` reads GitHub but never writes. `apply` is disabled unless the supplied SHA-256 matches the **exact bytes** of the reviewed plan. Both fail closed if Project fields or issue state/labels/update time/blockers differ from the reviewed snapshot. The tool checks all rows before any mutations, then re-reads the item and issue immediately before each field write and verifies the field afterward.

There is no cross-item atomic transaction in GitHub Projects. If a failure occurs halfway through, earlier verified field updates may have been applied; the tool stops, reports the failure and refuses to replay the old plan if its expected-before values are stale. Inspect live state, prepare and approve a new plan for the remaining work. It never silently repairs a manual state change.

## Protected states and approvals

- Only the `Inbox`, `Backlog`, `Ready` and `In Progress` values may be written to Status; existing `Blocked` and `Done` issue statuses are never overwritten by this tool.
- To set an issue to Ready or In Progress, it must be open, have no open native Blocked-by prerequisites, not be an unaccepted candidate, and have a named Agent with Execution = Agent/Mixed.
- Candidate or unapproved findings can remain assigned to an agent while in Inbox, but the assignment is **not** authority to implement them.
- No automatic code change, agent launch, PR merge, issue closing, or label mutation occurs.

See `docs/agent-workflows/project-3.md` for the broader status/governance policy and `docs/agent-workflows/agent-queue.md` for actual agent pickup.
