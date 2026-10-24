# Native issue dependencies

PRKS records "cannot proceed until" prerequisites as GitHub's native **Blocked by / Blocking** issue relationships, not as labels or comments. The repository-owned writer is `.github/workflows/issue-dependency-writer.yml`, which runs `scripts/issue_dependency_writer.py` (#441).

## Who decides

Only a maintainer decides that one issue blocks another. Triage agents, AI reviewers and audits may *propose* a pair with evidence in an issue or discussion, but a proposal is not authorization, and nothing reads proposals automatically. Record a relationship only when the blocked issue **cannot meaningfully proceed** before the prerequisite. A related topic, shared files, a preferred order, roadmap overlap or an unaccepted candidate finding is not a blocker.

## Running it

Actions → **Issue dependency writer** → Run workflow, on the default branch:

| Input | Meaning |
|---|---|
| `blocked` | The issue that cannot proceed. |
| `blocking` | The prerequisite it is blocked by. |
| `justification` | Why it cannot proceed first, 10–1000 characters. It is kept in the run log and summary. |
| `mode` | `dry-run` (default) reports the change; `apply` writes it. |

Start with `dry-run`, read the job summary, then run the same inputs with `apply`.

Every run first runs a read-only check job. The write job runs only for `apply`, holds the only `issues: write` permission, and re-checks live state immediately before writing. Runs are serialized repository-wide, so two runs never race a check. The workflow uses `concurrency.queue: max`: up to 100 runs wait their turn instead of a newer dispatch replacing the pending one. GitHub does not document what happens past 100 pending runs, so if a dispatch does not appear in the run list, start it again; repeating a request is safe.

A request is refused, and nothing is written, when:

- the person who started or re-ran the run is not an allowed actor;
- the run is not from the default branch (the check job refuses before checking out any code, so the run fails with a `refused` record and the write job is skipped);
- either number is not a valid issue number, is missing, is a pull request or a closed issue, or was transferred to another repository (GitHub redirects it, and writing that number here would hit an unrelated issue);
- both numbers are the same;
- the blocking issue is already (transitively) blocked by the blocked issue, so the edge would close a cycle (the summary shows the path);
- the dependency graph above the blocking issue is larger than the writer will check (500 issues).

An edge that already exists is a no-op. If the write call errors but a re-read shows the edge on both issues, the run reports `converged`; otherwise it fails.

## What it never does

It only adds one relationship per run. It never removes or rewrites relationships, including ones people created by hand, never writes comments or labels, and never touches pull requests, Project #3 fields or merge state. To retire an obsolete relationship, remove it by hand on the issue.

## Audit

Each run prints one JSON record and a job summary with the actor, mode, pair, justification, the blocked issue's blocked-by list before and after, and the outcome (`dry-run`, `written`, `converged`, `no-op`, `refused` or `failed`). A request that cannot be parsed, such as a malformed number or an invalid allowed-actor login, still gets a `refused` record and job summary, with only the reason.

## Allowed actors and revoking

By default only the repository owner may run it. To allow others, set the repository variable `ISSUE_DEPENDENCY_WRITERS` to a comma-separated list of GitHub logins; that list replaces the default. Running any workflow already requires write access to the repository.

To revoke it, disable the workflow (Actions → Issue dependency writer → Disable workflow) or delete the file. Relationships it already wrote stay until removed by hand.
