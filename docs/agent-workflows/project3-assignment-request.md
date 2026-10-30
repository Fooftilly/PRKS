# ChatGPT Project #3 assignment requests — comment-free transport

GitHub Project #3 remains the **source of truth** for Agent, Status and
Execution. `project3-agent-queue/agent-queue.json` remains a read-only published snapshot.

Unlike the owner-only workflow-dispatch UI, **this request mechanism is
usable from the currently connected ChatGPT GitHub plugin**, which can
`fetch_file` and `update_file` on a repository branch. No Project token,
manual workflow run, issue command or receipt comment is needed per assignment.

## Initial activation (after the PR is merged)

1. Keep the existing `project-automation` environment restricted to the
   `master` ref, with `PROJECT_SYNC_TOKEN` available as an environment
   secret. The existing repository variable
   `PROJECT3_ASSIGN_DISPATCH_MODE=apply` also gates the new writer.
2. **Once**, create the branch `project3-assignment-requests` from current
   `master` (not from a pull request). The file
   `project3-assignment-request.json` already exists on `master` with
   an inactive placeholder. The request branch is a data transport: do not
   merge it into master, add other files or make code changes on it.
3. **Before any request**, open Repository Settings → Rules → Rulesets →
   New branch ruleset. Name it `Project 3 assignment request branch` and
   configure:
   - Enforcement: **Active**.
   - Target branches: explicitly include only
     `project3-assignment-requests` (exact ref
     `refs/heads/project3-assignment-requests`).
   - Enable **Restrict deletions** and **Block force pushes**.
   - Leave bypass actors **empty**; normal fast-forward file updates from the
     authenticated GitHub plugin must remain possible.
   The privileged workflow checks the *live GitHub ruleset* on every request
   and refuses Project updates unless it finds both protections with active
   enforcement, an exact matching ref and no bypass actors. It does not
   install repository rules automatically: the current ChatGPT GitHub connector
   lacks repository-administration permissions. This step must be performed
   by a repository administrator. If other rules are added, verify they do
   not block ChatGPT's single-file fast-forward updates.
4. Submit a **check-only** update using the authenticated GitHub plugin, then
   verify both Actions runs: **Project 3 assignment request signal** and
   **Project 3 assignment request processor**. The processor summary must
   report `CHECK ONLY`, with no Project field changes.
5. Only then submit an explicitly approved `apply` request, verify the
   Project item, the processor log and the queue publisher's complete snapshot.
   If the signal does not start or the processor rejects authorization, leave
   #982 working while investigating rather than disabling safeguards.

## Agent protocol for each assignment

ChatGPT must first inspect the current open issue, its native **Blocked by**
relationships, accepted roadmap, active conflicting PRs, and the Project's
Agent/Status/Execution values. The read-only queue is a starting snapshot, not
approval to overwrite newer Project values. Never invent the two truthful
`roadmap_checked` and `overlap_checked` attestations.

Using the **connected GitHub plugin**:

1. Fetch `project3-assignment-request.json` on ref
   `project3-assignment-requests` and keep its **exact blob SHA**.
2. Replace the *entire* file using `update_file`, specifying
   `repository_full_name="Fooftilly/PRKS"`,
   `path="project3-assignment-request.json"`,
   `branch="project3-assignment-requests"`, the fetched `sha`,
   and the JSON command. This writes one commit to the request branch; it does
   **not** create a pull request or issue comment.
3. Read the **signal** and then the **processor** Actions runs. A successful
   file update is *not* proof that Project fields changed. Inspect the
   processor's `APPLIED` summary and the live Project/queue state.
   If processing fails, stop; don't repeatedly resubmit the same command.

Example JSON (replace **all** before-values with live ones; numbers and
values are illustrative, not an automatic authorization):

```json
{
  "version": 1,
  "operation": "check",
  "items": [
    {
      "number": 636,
      "set": {"Agent": "Claude Code", "Status": "Ready", "Execution": "Agent"},
      "expected_before": {"Agent": "Claude Code", "Status": "Ready", "Execution": "Agent"},
      "reason": "Previously approved PostgreSQL Processing assignment; verified live blockers and overlap.",
      "roadmap_checked": true,
      "overlap_checked": true
    }
  ]
}
```

Use `"operation": "apply"` only for the maintainer's explicitly authorized
field edits. A check-only request never changes Project fields even if
the write mode is enabled. The full three-field `expected_before`
snapshot is required for every issue; a stale or replayed update fails
closed. In-progress or Blocked issues, unaccepted candidates and open
native blockers retain all existing triage protections. Reassigning away
from an existing Agent requires `"allow_reassignment": true` and explicit
authorization. One command can contain 1–10 existing issues.

## Security boundary

The signal workflow is an **unprivileged** branch-push notification with no
checkout, secrets or write token. The processor is a `workflow_run` event
executed from trusted `master`, in the protected environment.

Before reading the JSON, it independently verifies against live GitHub REST:
the owner Fooftilly's immutable ID (46610174), successful first-attempt
signal run, exact signal workflow path/event, repository, request branch and
SHA, that the request SHA is still the branch's tip, that GitHub attributes
the commit author and committer to that owner, and that the commit
changed **only** the existing request JSON file. It fetches that small file
at the verified *commit SHA*, decodes it as data and passes it to the
already-reviewed `project3_assignment_dispatch` validator and
`project3_triage` field/issue guards. It never checks out or executes
untrusted request-branch code, reads upstream artifacts, runs shell commands
from the request, or exposes the Project token to branch workflows.

The processor's `workflow_run` trigger has a narrowly scoped
`zizmor: ignore[dangerous-triggers]` annotation because the generic audit
cannot establish this specific trust boundary. This exception requires:
a trusted `master` checkout, the protected environment, fixed owner-ID
authorization, independent REST verification of the upstream run and exact
one-file pinned JSON commit, no upstream artifacts, no untrusted script or
PR checkout, and no interpolation of request data into a shell command.
If any requirement changes, remove the suppression and reassess security.

**One-time execution claim:** Before accessing Project V2 for a request, the
trusted master workflow reads the complete paginated GitHub commit-status
history for the request SHA and refuses any SHA already carrying the reserved
`prks/project3-assignment-request-claim` context. It then publishes a
`pending` status using its scoped `statuses:write` token, verifying the REST
response before invoking Project code, and appends `success` on completion.
These commit statuses are append-only and independent of the request branch:
a force push, restored commit, failed run or Project field values returning
to earlier values cannot authorize replaying the same commit. A failed/partial
run leaves a durable claim even if its result remains pending; review the
run and submit a new reviewed commit instead of retrying. All processors are
serialized with the dispatch and legacy bridge.

**GitHub App identity limitation:** GitHub's push/commit REST records expose
the GitHub user that caused the push and the commit author/committer, but
do not provide the originating App ID with the same authenticated semantics
as issue-comment `performed_via_github_app`. This transport verifies the
owner's immutable user ID, owner-attributed commit, protected request branch
and complete guarded command. It **cannot** restrict writes exclusively to
the ChatGPT Connector app. Any authenticated access acting as the repository
owner and permitted to push this branch would also be authorized, subject
to the same command, ruleset and replay checks. If strictly verifying the
ChatGPT App ID is a requirement, keep #982 or use a dedicated custom GitHub
App integration with a verifiable caller identity; do not claim this relay
has that property.

The three assignment writers (manual dispatch, legacy comment bridge, and
request branch) share the same serialized concurrency group. A failed
attempt could be partially applied; the processor still refreshes the
read-only queue, and logs a failed/possibly-partial audit in the Actions
run summary. There is no atomic GitHub Projects transaction.

## Issue #982 retirement

After a successful live ChatGPT-originated check **and** apply on this path,
with verified Project fields, queue publication and maintained safety checks,
the legacy comment bridge can be disabled in a **separate** cleanup PR. Retire
#982 only after confirming no other caller still relies on it. Keep the
local triage utility and manual dispatch path as independent fallbacks.
