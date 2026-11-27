# Project #3 assignment dispatch (comment-free write path)

Project #3 remains the sole authority for **Agent**, **Status**, and **Execution**.
`agent-queue.json` is a validated, read-only ownership snapshot. This workflow changes
Project V2 fields **directly** through authenticated GraphQL, then requests a
queue publication. It never writes command or receipt comments to issue #982.

This is an Actions-native writer for the maintainer and for agents with
authenticated **GitHub Actions workflow-dispatch permission**. The ChatGPT GitHub connector does not expose workflow-dispatch or direct Project
V2 mutations; it uses the verified authenticated request-branch relay described
in `docs/agent-workflows/project3-assignment-request.md`.

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

## Implementation Order commands

The same workflow accepts an order-only command. It changes only the NUMBER field `Implementation Order`, through the reviewed editor in `project3-triage.md`:

```json
{
  "version": 1,
  "kind": "implementation-order",
  "operation": "check",
  "items": [
    {"number": 815, "order": 3, "expected_before": null,
     "reason": "Ranked after the PostgreSQL slice; reviewed roadmap and open PRs.",
     "roadmap_checked": true, "overlap_checked": true}
  ]
}
```

Each row MUST have exactly these keys. `expected_before` is the live rank or `null`. The rules for `order` are in `project3-triage.md`. A batch has 1–10 rows.

Approval is bound to the exact batch:

1. Run the command with `"operation": "check"`. The run summary prints `order digest <sha256>`. The digest covers `kind`, `items` (including `expected_before`), the live field node ID, each live Project item ID and each issue's labels, update time and open blockers. Check and apply of one batch have the same digest only while those items and issues are unchanged.
2. The maintainer reviews the batch and sets the digest in the protected environment:

   ```bash
   gh variable set PROJECT3_ORDER_APPROVED_SHA256 --repo Fooftilly/PRKS \
     --env project-automation --body '<digest from the check run>'
   ```

3. Run the same batch with `"operation": "apply"`. It writes only when `PROJECT3_ORDER_WRITE_MODE` is `apply` and the digest equals `PROJECT3_ORDER_APPROVED_SHA256`. Another value refuses the run before any write.

`PROJECT3_ORDER_WRITE_MODE` is off by default and separate from `PROJECT3_ASSIGN_DISPATCH_MODE`. Neither mode enables the other. Agents MUST NOT set either variable or the digest: a successful check, an agent proposal or an owner-attributed commit is not human review. To turn order writes off, delete `PROJECT3_ORDER_WRITE_MODE`. Delete the digest after the apply.

An approved digest is applied at most once. Before the first write, the job reads the commit statuses of master commit `126d25fb6f50ef9bd00cfad52065461eca993c7e` and refuses a digest that already has the context `prks/project3-order-approval/<digest>`. It then writes that context as `pending` with its `statuses: write` token, and `success` or `failure` at the end. Both writers share this claim and the `project3-assignment-mutations` concurrency group. A reused approval is refused even when the ranks have returned to their reviewed values. A new apply needs a new check and a new digest. When the batch and its live state are unchanged, the check prints the used digest again. Then change the `reason` text, for example by adding the date, run the check again and approve the new digest. A run that fails after a write reports **POSSIBLY PARTIAL** and refreshes the agent queue and the planning snapshot.

### Multi-batch plans

One approval can cover up to 30 checked batches. Each batch still has 1–10 rows and runs in its own command.

1. Check each batch alone with the single-batch command above. Record each `order digest` and the `field` node ID from the summary.
2. Write a manifest. It lists the batches in run order. Each entry has the batch digest and its `[number, order]` pairs, in row order:

   ```json
   {
     "schema": "prks-project3-order-manifest/v1",
     "field": {"name": "Implementation Order", "id": "<field node ID>"},
     "batches": [
       {"digest": "<batch 1 order digest>", "items": [[815, 3], [509, 4]]},
       {"digest": "<batch 2 order digest>", "items": [[635, 5]]}
     ]
   }
   ```

   An issue or a rank MUST NOT occur twice in the manifest. A batch digest MUST NOT occur twice.
3. Run batch 1 as a plan command with `"operation": "check"`:

   ```json
   {"version": 1, "kind": "implementation-order-plan", "operation": "check",
    "manifest": {"...": "..."}, "batch": 1, "items": ["...rows of batch 1..."]}
   ```

   `items` are the same rows as the single-batch command. Their `[number, order]` pairs MUST equal the manifest entry. The summary ends with `manifest digest <sha256>`. The manifest digest covers the whole manifest, including each batch digest.
4. The maintainer reviews the manifest and sets the manifest digest as `PROJECT3_ORDER_APPROVED_SHA256`. A checked manifest is not approval.
5. Run each batch with `"operation": "apply"`, in manifest order, with the same manifest.

Each plan apply runs these steps in this order:

1. It refuses a manifest that is not the approved one.
2. It reads the claims once. It refuses the batch when the digest of this or a later batch was already used on either path, when an earlier batch has no `success` under this manifest, or when this or a later batch already started under it. So a plan with a used digest stops before its first write.
3. It claims the step `prks/project3-order-plan/<manifest digest>/<batch>` on the claim commit.
4. It recomputes the batch digest from live evidence. When the digest differs from the manifest entry, the step is marked `failure` and the manifest ends. A changed rank, item ID, field ID, label, update time or open blocker after the check causes this. The batch digest stays unused.
5. It claims `prks/project3-order-approval/<batch digest>`, applies the batch, and marks both claims `success` or `failure`.

Steps 1 and 2 write nothing. A check, a dry run and an unapproved or out-of-order apply never claim. So batches cannot be substituted, skipped, reordered or replayed. A single-batch approval cannot apply a batch of a plan, and the reverse. After a refused, failed or possibly partial batch, every later batch of that manifest is refused, also when the live values return to the checked ones. The run log names the failed batch and the blocked batches.

To recover after a failure:

1. Read the refreshed planning snapshot and compare it with the manifest. Batches before the failed one are done. The failed batch is possibly partial. Later batches are pending.
2. Check the remaining rows again as new batches. Use the live `expected_before` values. When the failed batch reached its batch claim, its digest is used: change its `reason`, for example by adding the date, to get a new digest. A batch refused by its live preflight keeps an unused digest.
3. Write a new manifest for the remaining batches and get a new approval. The new manifest digest MUST differ from the old one. When batch 1 failed and every recheck gives the old digests, the new manifest equals the old one and stays blocked: change the `reason` of one row and check that batch again.

To revoke a plan before a run starts, delete `PROJECT3_ORDER_WRITE_MODE` or change `PROJECT3_ORDER_APPROVED_SHA256`. A running job keeps the values it read at its start, so also write the revocation status (see [Full plans](#full-plans)). Batches that already ran stay applied.

Budget: one plan command MUST stay within 12,000 bytes. A full 30-batch manifest with 10 rows in the current batch uses about 9 KB when each reason has about 60 characters. Each apply reads the claim history once (at most 200 pages) and writes up to four statuses. Project reads and issue reads stay at the single-batch cost.

### Full plans

A full-plan command carries every batch, so one check and one apply cover the whole plan. It uses the manifest, digests and claims above.

1. Check the whole plan in one run:

   ```json
   {"version": 1, "kind": "implementation-order-full-plan", "operation": "check",
    "batches": [["...rows of batch 1..."], ["...rows of batch 2..."]]}
   ```

   The run reads each batch live, including a fresh read of each Project item, and writes nothing and claims nothing. The summary shows a table of every row (batch, issue, before, order) and one log line `ORDER MANIFEST <json>`. The outcome ends with `manifest digest <sha256>`. Each batch digest equals the single-batch check of that batch.
2. The maintainer reviews the request file and the table, then sets the manifest digest as `PROJECT3_ORDER_APPROVED_SHA256`.
3. Apply the same batches with the manifest from the check log:

   ```json
   {"version": 1, "kind": "implementation-order-full-plan", "operation": "apply",
    "manifest": {"...": "..."}, "batches": ["...same batches..."]}
   ```

The apply runs in one job:

1. It refuses a plan whose REST estimate does not fit, a manifest that is not the approved one, and the plan rules of [Multi-batch plans](#multi-batch-plans): used batch digests, started steps and a revocation status. Before it scans the claim history, it waits until the current rate-limit window has 201 requests.
2. It claims step 1 and checks every batch against its manifest digest before the first write. This check reads each Project item again, so a change after the job loaded the Project is found here. A stale later batch ends the manifest here, with no write.
3. For each batch in order, it waits for REST budget when needed, reads the revocation status, checks the batch again, claims the batch digest and applies the batch with the per-write guards. It then claims the next step and marks this batch and its step `success`. A stop during the next budget wait therefore always leaves the next step started.
4. Any refusal, failure, revocation, budget stop or cancellation marks the open claims `failure` and stops. The summary says how many batches are verified, whether the stopped batch is **FAILED / POSSIBLY PARTIAL** or refused before any write, and which batches were not applied. The workflow then refreshes the agent queue and the planning snapshot.

Recovery after a stopped full plan uses the steps of [Multi-batch plans](#multi-batch-plans). Check only the remaining rows as a new full plan and approve its new manifest digest.

**Revocation during a run.** Environment variables do not change inside a running job. To stop a running plan before its next batch, the maintainer writes this status. Any state works:

```bash
gh api repos/Fooftilly/PRKS/statuses/126d25fb6f50ef9bd00cfad52065461eca993c7e \
  -f state=failure -f context='prks/project3-order-revoke/<manifest digest>'
```

The job reads it before each batch. Every later apply of that manifest, by full plan or by step command, is refused. A revocation status can only stop work. To stop at once, cancel the workflow run. The step runs `exec python3`, so Python itself gets SIGINT, then SIGTERM. It marks the running batch `failure` and the refresh step still runs. A write in flight can land, so treat that batch as possibly partial.

**Limits.** These numbers come from the code and GitHub's documented limits.

| Limit | Value |
| --- | --- |
| Batches and rows | 1–30 batches of 1–10 rows: 300 issues |
| Request file (full plan) | 131,072 bytes. 300 rows with 60-character reasons: about 54 KB for check and 61 KB for apply |
| Owner dispatch input | 60,000 bytes (GitHub allows 65,535 characters for all inputs). Use the request branch for a larger plan |
| Other commands | 12,000 bytes, unchanged |
| Actions-token REST requests | About 2 per row for a check, 10 per row and 8 per batch for an apply, plus 200 for the claim history scan. A 300-row apply needs about 3,440 |
| Actions-token rate | 1,000 requests per hour, shared by every workflow in the repository |
| Job time | 360 minutes. A full plan stops at a batch boundary after 300 minutes |

Before the first claim, the apply reads `GET /rate_limit`, which does not count against the limit. It refuses a plan whose estimate does not fit before the 300-minute deadline. No batch starts after that deadline. Before each batch it waits for the next reset when fewer requests remain than the batch needs. A 300-row apply can take more than three hours when other workflows use the same token budget. Project reads use `PROJECT_SYNC_TOKEN` (about 4 GraphQL queries and 1 mutation per changed row). The claim history grows by 4 statuses per batch; at 200 pages the claim reader refuses to prove non-replay.

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

Successful apply runs with actual changes, and failed attempts that could have
partially changed Project fields, dispatch the existing queue publisher. Its output is a snapshot; only a successful publisher run with a
fresh complete file confirms the read-only cache is updated.

## Legacy bridge retirement

The comment-based #982 listener was removed after the ChatGPT-originated
request branch passed a check-only run and a guarded live apply on 2026-10-30.
The protected processor verified one Project field change for #629 and the
complete published queue recorded that field. Manual dispatch and the guarded
request-branch relay remain independent writers sharing the same serialized
concurrency group. The read-only queue is not a write endpoint.

See `docs/agent-workflows/project3-triage.md` for local reviewed batches and
`docs/agent-workflows/agent-queue.md` for read-only agent discovery.
