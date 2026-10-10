# Project #3 contract

This is the checked-in record of how GitHub Project #3 ("PRKS Roadmap", owned by the `Fooftilly` account) is configured and automated, and of the rules automation must keep (#441). Project-side settings live in GitHub's UI, not in this repository, so this document is the reference for reasoning about them together with the checked-in workflows.

> **Status of this document: proposal.** Section 1 (existing configuration) is pending the read-only inventory. Everything marked **Proposed** needs the maintainer's approval of the specific configuration before anyone changes Project #3. Until then nothing in this document is applied, and no Project #3 field, status, workflow or view may be changed.

## Governance (always applies)

- Automation never merges a pull request, never enables auto-merge, and never reads green CI or a bot approval as merge authorization.
- Status is lifecycle only. It never encodes CI health, priority, category or who executes the work.
- Pushing commits after a "changes requested" review does not imply approval or move the item to Review.
- Merging a PR completes the PR item only. It never completes, closes or moves its parent, controller or roadmap issue.
- Controller and roadmap issues stay active until their own acceptance criteria are met, and are closed by a person.
- Where automation cannot know intent, a manually set Status wins. Automation never moves an item out of **Blocked** or **Done** without an explicitly approved policy.
- AI triage output never changes Status, fields or dependencies by itself.

## 1. Existing configuration

**Pending inventory.** The maintainer runs the read-only export in the #441 audit report, which lists fields with their options, views with their filters, and native workflows with their on/off state. Workflow settings (auto-add filter, target statuses, archive filter) come from screenshots, because the API does not expose them. This section will then record, verbatim:

| Item | Existing | Proposed change | Needs approval |
|---|---|---|---|
| Status options | *pending* | §2 | yes |
| Other custom fields | *pending* | none added except Execution (§3) | yes |
| Native workflows and their settings | *pending* | §5 | yes |
| Views and their filters | *pending* | §7 | yes |

Nothing in sections 2–8 assumes the current options match the proposed ones. Each proposed change will be mapped from what the inventory shows.

## 2. Lifecycle Status (provisionally approved)

| Status | Meaning |
|---|---|
| Inbox | Added, not triaged. |
| Ready | Triaged and actionable; can start now. |
| In Progress | Being worked on, including draft PRs. |
| Review | Waiting for a human review. |
| Changes requested | A review asked for changes; waiting on the author. |
| Blocked | Cannot proceed. Set by a person; a native "Blocked by" dependency is shown separately (§9). |
| Done | The item itself is complete: issue closed, PR merged. |

Migration from the existing options is proposed only after the inventory (§1).

## 3. Execution field (approved)

One new single-select field, **Execution**, with options **Human**, **Agent** and **Mixed**. It is set by hand at first. Execution says who does the work; it does not make an item ready. Agent Queue (§7) also requires a lifecycle Status and no unresolved prerequisite.

## 4. Classification comes from repository metadata

No Work type, Area or Priority Project fields. Views filter on the canonical repository metadata instead:

| Concept | Source |
|---|---|
| Priority | `priority:P1`, `priority:P2`, `priority:P3` labels |
| Area | `area:*` labels |
| Research | `research` label, applied by the Research / evaluation template |
| Kind | `bug`, `enhancement`, `roadmap`, `audit-finding`, `ux-finding` labels; Issue Types Task / Bug / Feature |
| Dependency updates | `dependencies` label (Dependabot's default) and `author:app/dependabot` |
| Parent / progress | native sub-issues, Parent issue and Sub-issue progress fields |
| Linked PRs, reviewers, milestone | native fields |

Research backfill: only the three `[Research]` issues (#181, #234, #246, all closed) carry the prefix. Labelling them is a separate, maintainer-approved step; nothing is relabelled in bulk.

## 5. Native Project workflows (Proposed)

Every setting is checked against its current configuration (§1) before it is changed.

| Workflow | Proposed setting | Why |
|---|---|---|
| Auto-add to project | On. Filter: all issues and PRs in `Fooftilly/PRKS`, including dependency PRs | Everything enters without manual adding. Dependency PRs are kept out of the ordinary views by their filters (§7), not by auto-add. |
| Item added to project | Status: Inbox | Default entry state. |
| Item closed | Status: Done | The item itself is complete. |
| Pull request merged | Status: Done | Applies to the PR item only. |
| Item reopened | Status: Ready | Reopened work is actionable again. Whether an explicitly set state should be preserved is checked against the native workflow's behavior first. |
| Code changes requested | Status: Changes requested | |
| Code review approved | **Off** (provisional) | A bot approval would move Status, and approval is not authorization. |
| Auto-close issue | **Off** (provisional) | Moving a controller issue to Done must not close it. |
| Pull request linked to issue | **Off** (provisional) | One slice PR must not move its controller issue. |
| Auto-archive items | **Off for issues** (§8) | Issues stay discoverable. |

## 6. Event → Status mapping

| Event | Item | New Status | Owner | Allowed only from |
|---|---|---|---|---|
| Issue or PR added | either | Inbox | native | (new item) |
| Triaged as actionable | issue | Ready | person | Inbox |
| Work starts | issue | In Progress | person | Ready |
| Draft PR opened | PR | In Progress | `project-sync` | empty, Inbox |
| PR marked ready for review | PR | Review | `project-sync` | empty, Inbox, Ready, In Progress |
| PR converted back to draft | PR | In Progress | `project-sync` | Review, Changes requested |
| Changes requested in a review | PR | Changes requested | native | (native rule) |
| Re-review explicitly requested | PR | Review | `project-sync` | Changes requested |
| New commits pushed | PR | no change | none | |
| CI result of any kind | either | no change | none | |
| PR merged | PR | Done | native | |
| Issue closed | issue | Done | native | |
| Issue reopened | issue | Ready | native | Done |
| Linked PR merged | its issue | no change | none | |

`project-sync` never writes an issue item, never moves an item out of Blocked or Done, and leaves any item whose current Status is not in its "allowed from" list unchanged, logging that it did so.

## 7. Views as filtered projections (Proposed)

Filters use GitHub Projects filter syntax. Exact field and option names follow §1 once it is filled in.

| View | Layout | Filter |
|---|---|---|
| Active Work | table | `is:open -label:dependencies status:Ready,"In Progress",Review,"Changes requested",Blocked` |
| Agent Queue | table, grouped by Status | `is:open is:issue execution:Agent,Mixed status:Ready,"In Progress" -label:dependencies` |
| Research Queue | table | `label:research is:open -status:Done` |
| Roadmap | roadmap | `label:roadmap`, with Sub-issue progress and milestone |
| Work Board | board by Status | `-label:dependencies` (Done items included and visible) |
| Timeline | roadmap by date fields | same items as Work Board |
| Maintenance (new) | table | `label:dependencies` |

**Dependency PRs:** they are added to Project #3 like any other PR and follow the same PR lifecycle (§6). Active Work, Agent Queue and Work Board exclude them with `-label:dependencies`, so they show up only in Maintenance. Automation never merges them.

Agent Queue separates ready work from work blocked by an unresolved prerequisite. A blocked-state filter qualifier in Project views is not yet verified. If it exists, Agent Queue adds it. Otherwise a read-only eligibility report lists Ready Agent/Mixed issues with and without open native blockers (§9), and the view shows both groups.

## 8. Archival

**Issues are never archived automatically.** Age, closure or Done alone never decides relevance. This covers roadmap and controller issues, completed issues still relevant to future work, issues that record architectural or implementation decisions or historical bugs and their fixes, and issues referenced by dependencies or roadmap work.

- Completed issues are hidden from Active Work and Agent Queue by the view filters in §7, not by archiving.
- A person may archive an issue manually when it is genuinely obsolete.

**Merged PRs (proposal for later evaluation, not enabled):** a native auto-archive filter scoped to merged PRs only, for example `is:pr is:merged updated:<@today-30d`. Whether the auto-archive filter can be restricted to PRs safely is checked before this is proposed for approval.

## 9. Native issue dependencies

Prerequisites are GitHub's native **Blocked by / Blocking** relationships, recorded only through the maintainer-dispatched writer described in `docs/agent-workflows/issue-dependencies.md`. Project #3 reads them as they are; nothing copies them into labels, comments or Status. A relationship never forces an item's Status backwards. Agent Queue and triage reports treat an issue with an open blocker as not ready (§7).

## 10. `project-sync` design (Proposed; not built yet)

A checked-in workflow, `.github/workflows/project-sync.yml`, covering only the four PR rows marked `project-sync` in §6.

- **Events:** `pull_request_target` (`opened`, `ready_for_review`, `converted_to_draft`, `review_requested`). Nothing checks out or runs pull-request code. The PR's number from the event is the only input; the PR's state is re-read from the API.
- **Scope:** PR items only. Field and option IDs are resolved at run time from names kept in one config file (`.github/project-sync.json`). If any name is missing, the run fails clearly.
- **Behavior:** applies the "allowed from" rules in §6, so a repeated run makes no change. Each run logs one audit record (PR, event, Status before and after, outcome) and a job summary.
- **Default:** dry-run. Writing is enabled only after the credential and the workflow have been reviewed.
- **Open question:** a draft PR may fire `opened` before auto-add has created its item. Either `project-sync` adds the PR item itself (an idempotent API call), or it skips with a log entry.

## 11. Credentials and permissions

| Automation | Identity | Permissions |
|---|---|---|
| Issue dependency writer | `GITHUB_TOKEN` | `issues: read` (plan job), `issues: write` (apply job only) |
| `project-sync` | `PROJECT_SYNC_TOKEN` repository Actions secret: a classic personal access token with only the `project` scope (`GITHUB_TOKEN` cannot reach Projects) | Project read/write. Repository data it reads is public. |

- The token has an expiration date. To rotate it, create a new token with the same single scope, replace the secret, then revoke the old token.
- To revoke it, delete the token in the account's developer settings and delete the secret. `project-sync` then fails closed without writing.
- The token never appears in repository files, workflow logs, PR comments or documentation. Only the job that needs it receives it, as an environment variable.
- A classic `project` scope reaches every Project the account owns. That is inherent to classic tokens. The workflow only addresses Project #3 by number.

## 12. Recovery, rollback and troubleshooting

- **Stop automation:** disable the workflow in Actions, or switch `project-sync` back to dry-run. Native workflows are switched off in Project → Workflows.
- **Undo a wrong transition:** set the Status back by hand. The audit record gives the previous value, and `project-sync` will not override a Status outside its "allowed from" list.
- **A field or option was renamed:** runs fail with the missing name. Update `.github/project-sync.json` or rename it back.
- **Dependency writer refused or failed:** see the job summary and `docs/agent-workflows/issue-dependencies.md`.
- **A token expired or was revoked:** `project-sync` fails without writing. Rotate it as in §11.
