# PRKS pull request review

`.github/workflows/pr-review.yml` reviews pull requests with the xAI API. The script, JSON contract, and finding ledger live in `tools/pr_review/`. Review policy lives in `tools/pr_review/criteria.md`, which is the file to edit when the checks should change.

The workflow does nothing until it is on the default branch. `pull_request_target` runs the workflow and Python from that branch, not from the pull request.

## What this watcher owns

It looks for PRKS-specific defects: correctness regressions, architecture boundaries, frontend/backend contract mismatches, offline and sync behavior, revision and tombstone mistakes, data integrity, security boundaries, performance-rule breaks, accessibility and interaction regressions, missing regression coverage, and accidental second implementations.

It also owns the finding ledger. Each open finding has a fingerprint. A later push is judged against that ledger: fixed findings are retired, a fix that reintroduces a defect is reported again, and an open finding is not posted a second time.

## What it leaves alone

These already run on pull requests and keep their jobs:

| Tool | Role |
| --- | --- |
| CodeRabbit (`.coderabbit.yaml`) | Walkthrough, file summaries, sequence diagrams, general incremental review, pre-merge checks |
| Fast Static Analysis | Ruff, Pyright, and ESLint bug checks |
| CodeQL | Security-extended queries for Python, JavaScript/TypeScript, and Actions |
| Suppress noisy review bot comments | Deletes the known Codex usage-limit comment and the empty Qodo "no issues" comment |

`tools/pr_review/criteria.md` tells the model to stay silent on style, lint, walkthroughs, and generic comments those tools already cover. When there is no new finding and nothing was resolved, the watcher posts no review. It only updates the ledger comment.

## Trigger events

`pull_request_target`:

- `opened`
- `reopened`
- `ready_for_review` (a draft that became ready)
- `synchronize` (new commits pushed to the pull request)

Drafts are skipped, including pushes to a draft. The base branch must be the repository default branch.

`workflow_dispatch` can review one pull request by number. Inputs:

- `pr_number`
- `dry_run` — call the model when a key is configured, print findings, post nothing
- `force` — review again even if this revision is already in the ledger

Rapid pushes share one concurrency group per pull request (`prks-review-<number>`) with `cancel-in-progress`. The cancelled run does not post. Before posting, the script reads the pull request again and stops if the head SHA moved, so a stale run cannot review an older revision after a newer push.

## Incremental re-review

The ledger stores the last reviewed head SHA.

- The first review, a forced review, or a review whose previous SHA is no longer in the pull request (a force-push) sends the full pull request diff.
- A later review whose previous SHA is still in the pull request sends the compare diff from that SHA to the new head.
- The full pull request diff is still parsed, because GitHub only accepts inline comments on lines in that diff. Added and context lines use head-file numbers, so an incremental finding can be attached there. A deleted line in an incremental compare is numbered against the previously reviewed file, not the pull request base, so that finding is written into the summary instead of an inline comment. Surrounding excerpts are fetched for the highest-risk changed files (sync, schema, offline, backend, frontend, and workflow paths), read-only, through the contents API at `refs/pull/<n>/head`.
- Previous open findings are included in the prompt. A resolution counts only when the model returns that finding's fingerprint and the delta actually touches the file. A forgotten finding stays open. A fingerprint that matches a fixed finding, on a file this delta touches, is posted again as a regression.
- Vendor blobs, screenshots, archives, and other ignored binary paths do not call the model. The ledger still advances so that revision is not fetched again.

## How reviewed revisions are tracked

One issue comment on the pull request, authored by `github-actions[bot]`, holds the ledger. The visible sentence is stable. The machine-readable JSON is in an HTML comment that starts with `prks-grok-review-state`. It records:

- `head_sha` — the revision last reviewed
- `findings` — fingerprint, path, line, side, severity, title, a short body, `open` or `fixed`, and the inline comment id when GitHub returned one

The same head SHA is not reviewed again unless `workflow_dispatch` is run with `force`. Before the comment is updated, the script re-reads it. If another run changed `head_sha`, this run does not overwrite the ledger. Editing the ledger can notify people watching the pull request; the visible sentence stays the same so the notification is not a second review.

Inline comments include `prks-finding: <fingerprint>`. Summary reviews include `prks-review-summary: <sha>`. Those markers are a second guard: if the ledger comment were lost, an existing fingerprint is not posted again. A resolved inline thread gets one reply, `prks-resolved: <fingerprint>`, and the historical comment stays on the old diff.

## Duplicate-comment prevention

- One fingerprint is one finding. The fingerprint is a hash of the path and the normalized title, so a line move or a reworded explanation does not create a second comment.
- Open fingerprints are not posted again. The ledger updates the line if the model still sees the issue.
- A review summary is posted only when a new finding was posted or a previous finding was resolved.
- At most eight new findings are posted on one revision. The rest wait for a later revision.
- The summary text is produced by the script from the ledger. The model's prose summary is not posted, so it cannot repeat CodeRabbit's walkthrough.
- `@mentions` in model text are broken before posting.

## Permissions and security

The workflow uses `pull_request_target` so a fork pull request can be reviewed with a secret without running the fork's code. The safe pattern is deliberate:

- The job checks out `github.event.repository.default_branch` only, with `persist-credentials: false`.
- It does not check out the pull request head, the merge commit, or `github.sha` from the pull request event.
- The token cannot push code. Workflow permissions are `contents: read`, `pull-requests: write`, and `issues: write`. `issues: write` is required to create and update the ledger comment.
- The model has no tools. Pull request text is sent as data. The script posts only structured findings after its own validation.
- The GitHub token and `XAI_API_KEY` are never inserted into the prompt. If either string appears in the prompt (for example because the diff contains it), the review is aborted and the revision is left unreviewed.
- The script does not execute model output or pull request contents.
- Pull request title, body, and diff are not interpolated into the shell. The workflow's `run` step is only `python3 tools/pr_review/main.py`.
- `PR_NUMBER` and the repository name must match a strict pattern before they are placed in an API path.
- Fork reviews are on by default, because the job never executes their code. Set the repository variable `PRKS_REVIEW_FORKS` to `false` to skip them.
- The model API URL must be `https`.

`pull_request` was not used. On a same-repository pull request that event runs the workflow from the merge commit, so a pull request could replace the review script and read the secret. `pull_request_target` keeps the script on the default branch. The cost of that choice is that this file reviews pull requests only after it has been merged.

## Secrets and configuration

| Name | Kind | Purpose |
| --- | --- | --- |
| `XAI_API_KEY` | Actions secret | Bearer token for the xAI chat-completions API. Required for a real review. |
| `PRKS_REVIEW_ENABLED` | Variable | `false` disables the watcher without deleting the workflow. Unset means enabled. |
| `PRKS_REVIEW_MODEL` | Variable | Model id. Default `grok-4.6`. |
| `PRKS_REVIEW_REASONING` | Variable | `low`, `medium`, `high`, or `xhigh`. Default `medium`. |
| `PRKS_REVIEW_FORKS` | Variable | `false` skips fork pull requests. Unset means review them. |
| `PRKS_REVIEW_API_URL` | Variable, optional | HTTPS endpoint. Default `https://api.x.ai/v1/chat/completions`. |
| `GITHUB_TOKEN` | Provided by Actions | Sent as `GH_TOKEN`. Not a repository secret you add. |

The label `prks-review-skip` skips one pull request.

The API request uses structured output (`response_format` JSON schema). It does not send temperature or other sampling fields that reasoning models reject. If the endpoint rejects `reasoning_effort`, the client retries once without that field.

## Failure behavior

The job exits successfully when the review service is unavailable, so a short outage does not turn red on every pull request. The revision is not recorded, and a later push or a `workflow_dispatch` retries it.

| Situation | Result |
| --- | --- |
| `XAI_API_KEY` unset | Warning in the log and the job summary. No comment. SHA not recorded. |
| Model HTTP 408/429/5xx | One retry, then the same warning. SHA not recorded. |
| Model returns unreadable JSON | One more model call, then the same warning. SHA not recorded. |
| GitHub is unavailable before a review is posted | Warning. SHA not recorded. |
| Head SHA changes while the model is running | No comment and no ledger update. The newer run reviews the new head. |
| Inline comment rejected because a line left the diff | The same text is posted in the review body. |
| Ledger update conflicts with another run | Posted comments are kept. The ledger is not overwritten. |

A blocking finding is labeled **Blocking** in the comment. The watcher does not request changes and does not fail the check. CodeRabbit is configured the same way (`request_changes_workflow: false`). Branch protection is unchanged.

Logs are counts, SHAs, paths counts, and HTTP status codes. Diffs, titles, notes, and credentials are not printed.

## Test it before relying on it

1. `python -m unittest tests.test_pr_review` covers span selection, the ledger, fingerprint dedupe, resolution, regressions, secret refusal, and the workflow's safety constraints.
2. Run actionlint on `.github/workflows/pr-review.yml` (the unit test does this when the `actionlint` binary is on `PATH`).
3. Merge the workflow to the default branch. Until then, `pull_request_target` will not run this file.
4. Add the `XAI_API_KEY` secret.
5. Run the workflow manually on a pull request with `dry_run` enabled. The log prints finding titles and paths and posts nothing. A dry run without the key still fetches the diff and prints the review span.
6. Run it again with `dry_run` off, or open a normal pull request. Confirm one ledger comment and inline comments only for new findings.
7. Push a follow-up commit. The same finding should not be repeated. A fixed finding should get a resolution reply rather than a second copy.
8. Add the label `prks-review-skip`, or set `PRKS_REVIEW_ENABLED` to `false`, to stop it.

Unit tests do not call GitHub or xAI. They use a fake pull request and a scripted HTTP transport.
