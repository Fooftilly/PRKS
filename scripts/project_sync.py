#!/usr/bin/env python3
"""Keep a pull request's Project #3 Status in step with its review lifecycle.

Runs from ``.github/workflows/project-sync.yml`` on four ``pull_request_target``
actions and covers only the PR rows owned by ``project-sync`` in
``docs/agent-workflows/project-3.md`` section 6:

* ``opened``             -> In Progress (draft) or Review, from empty or Inbox
* ``ready_for_review``   -> Review, from empty, Inbox, Ready or In Progress
* ``converted_to_draft`` -> In Progress, from Review or Changes requested
* ``review_requested``   -> Review, from Changes requested

``converted_to_draft`` and ``review_requested`` also apply only when the
latest such event on the PR is newer than the latest changes-requested
review, so a delayed run never hides newer requested changes.

Any other current Status, Blocked and Done included, is left alone. The event
only says which rule to consider: the PR's draft and open state are re-read
from the API, so a stale or repeated event changes nothing. If the PR has no
Project #3 item yet, one is added with ``addProjectV2ItemById``, which returns
the existing item when native auto-add got there first. Only the PR itself is
ever added or written; a linked issue is never touched.

Dry-run is the default. Writes happen only when ``PROJECT_SYNC_MODE`` is
exactly ``apply``. Without ``PROJECT_SYNC_TOKEN`` the run reports that it is
not configured and succeeds without calling the API. The script never merges,
approves, or reads CI results. Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

GRAPHQL_URL = "https://api.github.com/graphql"
CONFIG_PATH = Path(__file__).resolve().parents[1] / ".github" / "project-sync.json"
STATUS_KEYS = ("inbox", "ready", "in_progress", "review", "changes_requested", "blocked", "done")
OPEN = "OPEN"

# action -> (target when the PR is a draft, target when it is not, allowed-from)
# A target of None means the event does not apply to the PR's current state.
# None in allowed-from is an item with no Status yet.
RULES: dict[str, tuple[Optional[str], Optional[str], frozenset[Optional[str]]]] = {
    "opened": ("in_progress", "review", frozenset({None, "inbox"})),
    "ready_for_review": (None, "review", frozenset({None, "inbox", "ready", "in_progress"})),
    "converted_to_draft": ("in_progress", None, frozenset({"review", "changes_requested"})),
    "review_requested": (None, "review", frozenset({"changes_requested"})),
}

_REPO_RE = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")


class Refused(Exception):
    """The event is outside what project-sync may act on."""


class ConfigError(Exception):
    """Project #3 or the config file does not match the expected names."""


class ApiError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class Config:
    owner: str
    project_number: int
    repository: str
    status_field: str
    statuses: Mapping[str, str]

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        raw = json.loads(path.read_text(encoding="utf-8"))
        statuses = raw.get("statuses", {})
        missing = [key for key in STATUS_KEYS if not isinstance(statuses.get(key), str)]
        if missing:
            raise ConfigError(f"{path.name} has no status name for: {', '.join(missing)}")
        if not _REPO_RE.match(str(raw.get("repository", ""))):
            raise ConfigError(f"{path.name} has an invalid repository")
        return cls(
            owner=str(raw["owner"]),
            project_number=int(raw["project_number"]),
            repository=str(raw["repository"]),
            status_field=str(raw["status_field"]),
            statuses={key: statuses[key] for key in STATUS_KEYS},
        )

    def key_of(self, name: Optional[str]) -> Optional[str]:
        """Map a Status option name to its key; unknown names never match a rule."""
        if name is None:
            return None
        for key, value in self.statuses.items():
            if value == name:
                return key
        return f"unknown:{name}"


@dataclass(frozen=True)
class Project:
    id: str
    field_id: str
    options: Mapping[str, str]  # option name -> option id


@dataclass(frozen=True)
class Item:
    id: str
    status: Optional[str]  # option name
    archived: bool = False


@dataclass(frozen=True)
class PullRequest:
    id: str
    state: str
    is_draft: bool
    item: Optional[Item]
    # ISO 8601 times of the latest review request, the latest conversion to
    # draft and the latest review that requested changes (dismissed reviews
    # excluded), or None.
    last_review_requested_at: Optional[str] = None
    last_converted_to_draft_at: Optional[str] = None
    last_changes_requested_at: Optional[str] = None


@dataclass(frozen=True)
class Event:
    action: str
    number: int
    repository: str


class ProjectApi(Protocol):
    def load_project(self, config: Config) -> Project: ...

    def load_pr(self, config: Config, number: int, project_id: str) -> PullRequest: ...

    def add_item(self, project_id: str, content_id: str) -> None: ...

    def set_status(self, project_id: str, item_id: str, field_id: str, option_id: str) -> None: ...


@dataclass
class Audit:
    pr: int
    action: str
    mode: str
    outcome: str = ""
    reason: str = ""
    status_before: Optional[str] = None
    status_after: Optional[str] = None
    item_added: bool = False
    steps: list[str] = field(default_factory=list)

    def record(self) -> dict[str, Any]:
        return {
            "pr": self.pr,
            "action": self.action,
            "mode": self.mode,
            "outcome": self.outcome,
            "reason": self.reason,
            "status_before": self.status_before,
            "status_after": self.status_after,
            "item_added": self.item_added,
            "steps": self.steps,
        }

    def summary_markdown(self) -> str:
        before = self.status_before or "(none)"
        after = self.status_after or "(none)"
        return (
            f"### Project sync: PR #{self.pr} `{self.action}` ({self.mode})\n\n"
            f"- Outcome: **{self.outcome}**\n"
            f"- Status: {before} → {after}\n"
            f"- Reason: {self.reason}\n"
        )


def _time(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


# Actions whose run must not override a newer changes-requested review, with
# the PullRequest attribute holding the time of the latest such event.
ORDERED_ACTIONS = {
    "review_requested": ("last_review_requested_at", "review request"),
    "converted_to_draft": ("last_converted_to_draft_at", "conversion to draft"),
}


def event_is_current(action: str, pr: PullRequest) -> tuple[bool, str]:
    """The event counts only if no changes were requested at or after it.

    A delayed run must not hide a newer changes-requested review, so with no
    recorded event, or an equal or newer review, it fails closed. Other
    actions are not ordered against reviews.
    """
    if action not in ORDERED_ACTIONS:
        return True, ""
    attribute, label = ORDERED_ACTIONS[action]
    happened = _time(getattr(pr, attribute))
    changes = _time(pr.last_changes_requested_at)
    if happened is None:
        return False, f"no {label} is recorded on the PR"
    if changes is not None and changes >= happened:
        return False, f"stale event: changes were requested after the latest {label}"
    return True, ""


def decide(config: Config, action: str, pr: PullRequest) -> tuple[Optional[str], str]:
    """Return the Status key to set, or None with the reason nothing applies."""
    draft_target, ready_target, allowed = RULES[action]
    target = draft_target if pr.is_draft else ready_target
    if target is None:
        state = "a draft" if pr.is_draft else "ready for review"
        return None, f"stale event: the PR is now {state}"
    current_event, why = event_is_current(action, pr)
    if not current_event:
        return None, why
    current = config.key_of(pr.item.status if pr.item else None)
    if current == target:
        return target, "already set"
    if current not in allowed:
        return None, f"current Status {pr.item.status if pr.item else None!r} is not one `{action}` may change"
    return target, f"`{action}` moves the PR to {config.statuses[target]}"


def _skip_reason(pr: PullRequest) -> Optional[str]:
    if pr.state != OPEN:
        return f"the PR is {pr.state.lower()}"
    if pr.item is not None and pr.item.archived:
        return "the PR's item is archived"
    return None


def _status_of(pr: PullRequest) -> Optional[str]:
    return pr.item.status if pr.item else None


def validate(event: Event, config: Config) -> None:
    if event.repository != config.repository:
        raise Refused(f"only pull requests in {config.repository} are synced")
    if event.action not in RULES:
        raise Refused(f"action {event.action!r} is not handled")
    if event.number < 1:
        raise Refused("invalid pull request number")


def run(event: Event, config: Config, api: ProjectApi, apply: bool) -> Audit:
    audit = Audit(pr=event.number, action=event.action, mode="apply" if apply else "dry-run")
    validate(event, config)
    project = api.load_project(config)
    missing = [name for name in config.statuses.values() if name not in project.options]
    if missing:
        raise ConfigError(f"Project Status has no option named: {', '.join(missing)}")

    pr = api.load_pr(config, event.number, project.id)
    audit.status_before = audit.status_after = _status_of(pr)
    skip = _skip_reason(pr)
    if skip:
        return _finish(audit, "skipped", skip)
    target, reason = decide(config, event.action, pr)
    if target is None:
        return _finish(audit, "skipped", reason)

    if pr.item is None:
        if not apply:
            return _finish(audit, "dry-run", f"would add the PR to the project; {reason}", config.statuses[target])
        api.add_item(project.id, pr.id)
        audit.item_added = True
        audit.steps.append("added item")
        pr = api.load_pr(config, event.number, project.id)
        if pr.item is None:
            return _finish(audit, "failed", "the added item is not visible")
        audit.status_after = _status_of(pr)
        if pr.state != OPEN and pr.item.status is None:
            # Closed or merged before the item existed, so the native Done
            # rule had nothing to update. Only an item with no Status is set.
            done = config.statuses["done"]
            api.set_status(project.id, pr.item.id, project.field_id, project.options[done])
            audit.steps.append(f"set {done}")
            return _finish(audit, "corrected", f"the PR was {pr.state.lower()} before its item was added", done)
        skip = _skip_reason(pr)
        if skip:
            return _finish(audit, "skipped", skip)
        target, reason = decide(config, event.action, pr)
        if target is None:
            return _finish(audit, "skipped", reason)

    assert pr.item is not None
    target_name = config.statuses[target]
    if pr.item.status == target_name:
        return _finish(audit, "unchanged", "already set", target_name)
    if not apply:
        return _finish(audit, "dry-run", f"would set {target_name}; {reason}", target_name)

    api.set_status(project.id, pr.item.id, project.field_id, project.options[target_name])
    audit.steps.append(f"set {target_name}")
    return _verify(audit, config, api, project, event, target_name, reason)


def _verify(
    audit: Audit, config: Config, api: ProjectApi, project: Project, event: Event, target_name: str, reason: str
) -> Audit:
    """Re-read after writing; repair a state change that landed during the write.

    A merge or close sets Done. For the ordered actions, a changes-requested
    review submitted during the write restores Changes requested.
    """
    after = api.load_pr(config, event.number, project.id)
    status = _status_of(after)
    if status == target_name and after.item is not None:
        repair: Optional[tuple[str, str]] = None
        if after.state != OPEN:
            repair = ("done", f"the PR was {after.state.lower()} during the write")
        elif not event_is_current(event.action, after)[0]:
            repair = ("changes_requested", "changes were requested during the write")
        if repair:
            name = config.statuses[repair[0]]
            api.set_status(project.id, after.item.id, project.field_id, project.options[name])
            audit.steps.append(f"set {name}")
            return _finish(audit, "corrected", repair[1], name)
    if status != target_name:
        return _finish(audit, "superseded", f"another writer set {status!r} after this run", status)
    return _finish(audit, "updated", reason, target_name)


def _finish(audit: Audit, outcome: str, reason: str, after: Optional[str] = None) -> Audit:
    audit.outcome = outcome
    audit.reason = reason
    if after is not None:
        audit.status_after = after
    return audit


# --- GitHub GraphQL ---------------------------------------------------------

PROJECT_QUERY = """
query($owner: String!, $number: Int!, $field: String!) {
  user(login: $owner) {
    projectV2(number: $number) {
      id
      field(name: $field) {
        __typename
        ... on ProjectV2SingleSelectField { id options { id name } }
      }
    }
  }
}
"""

PR_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $field: String!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      id
      state
      isDraft
      reviewRequests: timelineItems(last: 1, itemTypes: [REVIEW_REQUESTED_EVENT]) {
        nodes { ... on ReviewRequestedEvent { createdAt } }
      }
      convertedToDraft: timelineItems(last: 1, itemTypes: [CONVERT_TO_DRAFT_EVENT]) {
        nodes { ... on ConvertToDraftEvent { createdAt } }
      }
      changesRequested: reviews(last: 1, states: [CHANGES_REQUESTED]) {
        nodes { submittedAt }
      }
      projectItems(first: 50, includeArchived: true) {
        nodes {
          id
          isArchived
          project { id }
          fieldValueByName(name: $field) {
            __typename
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
        }
      }
    }
  }
}
"""

ADD_ITEM = """
mutation($project: ID!, $content: ID!) {
  addProjectV2ItemById(input: {projectId: $project, contentId: $content}) { item { id } }
}
"""

SET_STATUS = """
mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(
    input: {projectId: $project, itemId: $item, fieldId: $field, value: {singleSelectOptionId: $option}}
  ) { projectV2Item { id } }
}
"""

Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


def http_transport(token: str, url: str = GRAPHQL_URL) -> Transport:
    if not url.startswith("https://"):
        raise ApiError("the GraphQL URL must use https")

    def send(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "prks-project-sync",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310 - https only
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise ApiError(f"GraphQL HTTP {error.code}") from None
        except (urllib.error.URLError, ValueError) as error:
            raise ApiError(f"GraphQL request failed: {type(error).__name__}") from None
        if payload.get("errors"):
            messages = "; ".join(str(e.get("message", "")) for e in payload["errors"] if isinstance(e, dict))
            raise ApiError(f"GraphQL error: {messages or 'unknown'}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ApiError("GraphQL response has no data")
        return data

    return send


class GraphQLApi:
    def __init__(self, send: Transport) -> None:
        self._send = send

    def load_project(self, config: Config) -> Project:
        data = self._send(
            PROJECT_QUERY,
            {"owner": config.owner, "number": config.project_number, "field": config.status_field},
        )
        project = (data.get("user") or {}).get("projectV2")
        if not project:
            raise ConfigError(f"project {config.owner} #{config.project_number} is not visible to the token")
        status = project.get("field") or {}
        if status.get("__typename") != "ProjectV2SingleSelectField":
            raise ConfigError(f"project has no single-select field named {config.status_field!r}")
        options = {opt["name"]: opt["id"] for opt in status.get("options", [])}
        return Project(id=project["id"], field_id=status["id"], options=options)

    def load_pr(self, config: Config, number: int, project_id: str) -> PullRequest:
        owner, name = config.repository.split("/", 1)
        data = self._send(
            PR_QUERY, {"owner": owner, "name": name, "number": number, "field": config.status_field}
        )
        pr = (data.get("repository") or {}).get("pullRequest")
        if not pr:
            raise ApiError(f"pull request #{number} not found in {config.repository}")
        items = [
            node
            for node in (pr.get("projectItems") or {}).get("nodes", [])
            if node and (node.get("project") or {}).get("id") == project_id
        ]
        if len(items) > 1:
            raise ApiError(f"pull request #{number} has {len(items)} items in the project")
        item = None
        if items:
            value = items[0].get("fieldValueByName") or {}
            item = Item(
                id=items[0]["id"],
                status=value.get("name") if value.get("__typename") == "ProjectV2ItemFieldSingleSelectValue" else None,
                archived=bool(items[0].get("isArchived")),
            )
        requests = (pr.get("reviewRequests") or {}).get("nodes") or []
        drafts = (pr.get("convertedToDraft") or {}).get("nodes") or []
        changes = (pr.get("changesRequested") or {}).get("nodes") or []
        return PullRequest(
            id=pr["id"],
            state=str(pr["state"]),
            is_draft=bool(pr["isDraft"]),
            item=item,
            last_review_requested_at=(requests[-1] or {}).get("createdAt") if requests else None,
            last_converted_to_draft_at=(drafts[-1] or {}).get("createdAt") if drafts else None,
            last_changes_requested_at=(changes[-1] or {}).get("submittedAt") if changes else None,
        )

    def add_item(self, project_id: str, content_id: str) -> None:
        self._send(ADD_ITEM, {"project": project_id, "content": content_id})

    def set_status(self, project_id: str, item_id: str, field_id: str, option_id: str) -> None:
        self._send(SET_STATUS, {"project": project_id, "item": item_id, "field": field_id, "option": option_id})


# --- entry point ------------------------------------------------------------


def _parse_number(value: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]{0,9}", value or ""):
        raise Refused("invalid pull request number")
    return int(value)


def main(
    argv: Optional[Sequence[str]] = None,
    env: Optional[Mapping[str, str]] = None,
    api_factory: Optional[Callable[[str], ProjectApi]] = None,
    config_path: Path = CONFIG_PATH,
) -> int:
    del argv  # inputs come from the environment only
    env = os.environ if env is None else env
    apply = env.get("PROJECT_SYNC_MODE", "") == "apply"
    action = env.get("EVENT_ACTION", "")
    audit = Audit(pr=0, action=action, mode="apply" if apply else "dry-run")
    status = 0
    try:
        event = Event(action=action, number=_parse_number(env.get("PR_NUMBER", "")), repository=env.get("EVENT_REPOSITORY", ""))
        audit.pr = event.number
        config = Config.load(config_path)
        validate(event, config)
        token = env.get("PROJECT_SYNC_TOKEN", "")
        if not token:
            _finish(audit, "not-configured", "PROJECT_SYNC_TOKEN is not set; nothing was read or written")
        else:
            factory = api_factory or (lambda value: GraphQLApi(http_transport(value)))
            audit = run(event, config, factory(token), apply)
            if audit.outcome == "failed":
                status = 2
    except Refused as error:
        _finish(audit, "refused", str(error))
        status = 1
    except ConfigError as error:
        _finish(audit, "misconfigured", str(error))
        status = 1
    except ApiError as error:
        _finish(audit, "failed", error.detail)
        status = 2
    print(json.dumps(audit.record(), sort_keys=True))
    summary_path = env.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(audit.summary_markdown())
    return status


if __name__ == "__main__":
    sys.exit(main())
