# Project #3 assignment dispatch (comment-free write path)

Project #3 remains the sole authority for **Agent**, **Status**, and **Execution**.
`agent-queue.json` is a validated, read-only ownership snapshot. This workflow changes
Project V2 fields **directly** through authenticated GraphQL, then requests a
queue publication. It never writes command or receipt comments to issue #982.

This is an Actions-native writer for the maintainer and for agents with
authenticated **GitHub Actions workflow-dispatch permission**. The current
ChatGPT Codex Connector GitHub integration can post issue comments, but does
**not** expose workflow-dispatch or direct Project V2 mutations. Do not retire
#982 while that limitation remains and ChatGPT still needs it.

## One-time setup after merging

1. Keep the existing GitHub Environment `project-automation` restricted to
   `master` and keep its environment-only `PROJECT_SYNC_TOKEN` classic `project`-scope
   credential. The dispatch does not put this secret in an agent session.
2. Leave `PROJECT3_ASSIGN_DISPATCH_MODE` unset initially. From the repository's
   Actions tab, select **Project 3 assignment dispatch**, choose branch
   `master`, and paste a check-only command. Or use `gh workflow run` (below).
   Confirm the job's **CHECK ONLY** summary and that Project fields did not change.
3. Enable writes explicitly, after that check:

   ```bash
   gh variable set PROJECT3_ASSIGN_DISPATCH_MODE --repo Fooftilly/PRKS --body apply
   ```

   Any other value means **dry-run**, including when the command says `apply`.
   To disable: `gh variable delete PROJECT3_ASSIGN_DISPATCH_MODE -R Fooftilly/PRKS`.
4. Run one explicitly approved, harmless apply with a current expected-before
   snapshot. Check the workflow result, live Project item, and refreshed
   `project3-agent-queue/agent-queue.json` snapshot independently.

## Submission protocol

Create a local JSON file **outside the repository** (for example,
`~/prks-project3-triage/dispatch.json`) with one to ten **existing PRKS
issues**. The following values are illustrative: replace *all* before-values
with the **current live Project #3 values** (including explicit JSON nulls)
before dispatching. A copied example is not evidence of a live review.

```json
{
  "version": 1,
  "operation": "check",
  "items": [
    {
      "number": 815,
      "set": {"Agent": "Cursor Cloud", "Status": "Ready", "Execution": "Agent"},
      "expected_before": {"Agent": null, "Status": "Inbox", "Execution": null},
      "reason": "Reviewed accepted roadmap, native blockers and current overlapping PRs.",
      "roadmap_checked": true,
      "overlap_checked": true
    }
  ]
}
```

Inspect each issue, PR overlap, and its GitHub Project fields before setting
the attestations true. The `expected_before` object must contain exactly **Agent,
Status, Execution**; the writer rejects a changed Project item or missing
snapshot. Existing `scripts/project3_triage.py propose` can collect a fresh
before-snapshot for an operator with local Project access.

Dispatch without putting the command in a GitHub issue:

```bash
gh workflow run project3-assignment-dispatch.yml \
  --repo Fooftilly/PRKS --ref master \
  --raw-field command="$(cat ~/prks-project3-triage/dispatch.json)"
gh run list -R Fooftilly/PRKS --workflow project3-assignment-dispatch.yml --limit 5
```

Run the **check** operation first. After reviewing the unchanged snapshot,
replace `"operation": "check"` with `"operation": "apply"` in the same JSON
and explicitly dispatch it. Do not edit the before-values to bypass a mismatch.

The authenticated GitHub user must be **Fooftilly** (immutable user ID
46610174). An Actions-enabled GitHub token and permission to dispatch workflows
are required, but the caller **does not need Project V2 scope**. The job
checks that the repository, Git ref, event, sender ID and first-run attempt
match, then reads the Project through the protected secret and reads
issues/dependencies with the job's scoped GITHUB_TOKEN.

## Safety and audit

The workflow uses exactly the same reviewed `project3_triage` preflight and
apply rules as #982: candidate acceptance, live native blockers, protected
statuses, explicit Agent/Execution for actionable work, issue update-time
and field drift checks, per-field verification, and explicit
`allow_reassignment: true` when replacing another named Agent.

No shell input interpolation, arbitrary code execution, issue mutation,
automatic agent launch, credential disclosure, or PR merge is permitted.
An old command cannot be replayed after its requested fields change because
the full before-snapshot must still match; rerunning the same Actions attempt
is refused. Identical no-op requests may pass without making any mutation.

Successful runs write their outcome and changed-field count to **the GitHub
Actions run summary and logs**, not issue comments. GitHub's Actions retention
policy controls the lifetime of these records. A failure after some writes
means **POSSIBLY PARTIAL**; stop, inspect Project #3 live state and native
blockers, and submit a newly reviewed command. There is no atomic multi-field
transaction in GitHub Projects.

Successful apply runs with actual changes dispatch the existing queue
publisher. Its output is a snapshot; only a successful publisher run with a
fresh complete file confirms the read-only cache is updated.

## Retirement criteria for #982

The legacy `project3-assignment-bridge.yml` remains active during rollout;
both writers serialize through the same Actions concurrency group. Retire
the #982 listener **only after** (a) an authenticated *apply* on master is
observed with verified Project fields and queue publication, (b) relevant
hosted agents, including ChatGPT, have an actually callable direct writer
(or the maintainer explicitly accepts manual dispatch for them), and
(c) remaining automation and docs have been migrated. No new comment
transport should be invented to replace it. The read-only data branch is not
a write endpoint.

See `docs/agent-workflows/project3-triage.md` for local reviewed batches and
`docs/agent-workflows/agent-queue.md` for read-only agent discovery.
