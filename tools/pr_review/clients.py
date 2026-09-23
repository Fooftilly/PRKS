"""GitHub and xAI clients for the PR review watcher.

Untrusted pull request contents are request bodies and response text. They are
never executed, and credentials are sent only as headers.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from pr_review import (
    FINDING_RE,
    LEDGER_AUTHOR,
    LEDGER_MARKER,
    RESOLVED_RE,
    LedgerConflict,
    LoadedLedger,
    Pull,
    ReviewServiceError,
    is_ignored_path,
    normalize_repo_path,
    parse_ledger,
)

GITHUB_API = "https://api.github.com"
GITHUB_ACCEPT = "application/vnd.github+json"
GITHUB_DIFF = "application/vnd.github.diff"
GITHUB_RAW = "application/vnd.github.raw"
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}

REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "findings", "resolved_fingerprints"],
    "properties": {
        "summary": {"type": "string"},
        "resolved_fingerprints": {
            "type": "array",
            "items": {"type": "string"},
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "line", "side", "severity", "title", "body"],
                "properties": {
                    "path": {"type": "string"},
                    "line": {"type": "integer"},
                    "side": {"type": "string", "enum": ["LEFT", "RIGHT"]},
                    "severity": {"type": "string", "enum": ["blocking", "non_blocking"]},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
            },
        },
    },
}


class UrllibTransport:
    def send(self, method, url, headers, body, timeout):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read(2_000_001), dict(response.headers)
        except urllib.error.HTTPError as exc:
            payload = exc.read(200_000) if exc.fp is not None else b""
            return exc.code, payload, dict(exc.headers or {})
        except urllib.error.URLError:
            raise ReviewServiceError("network") from None


def extract_message_content(payload: dict) -> str | None:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message") or {}
    if not isinstance(message, dict):
        return None
    if message.get("refusal"):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content or None
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        joined = "".join(parts)
        return joined or None
    return None


def comment_ids_from_review(payload: dict) -> dict[str, int]:
    found: dict[str, int] = {}
    comments = payload.get("comments") if isinstance(payload, dict) else None
    if not isinstance(comments, list):
        return found
    for comment in comments:
        if not isinstance(comment, dict) or not isinstance(comment.get("id"), int):
            continue
        match = FINDING_RE.search(str(comment.get("body") or ""))
        if match:
            found[match.group(1)] = comment["id"]
    return found


def pull_from_api(data: dict, number: int) -> Pull:
    head = data.get("head") if isinstance(data.get("head"), dict) else {}
    base = data.get("base") if isinstance(data.get("base"), dict) else {}
    head_sha = head.get("sha")
    base_sha = base.get("sha")
    if not isinstance(head_sha, str) or not isinstance(base_sha, str):
        raise ReviewServiceError("github")
    labels = []
    for label in data.get("labels") or []:
        if isinstance(label, dict) and isinstance(label.get("name"), str):
            labels.append(label["name"])
    head_repo = head.get("repo") if isinstance(head.get("repo"), dict) else {}
    base_repo = base.get("repo") if isinstance(base.get("repo"), dict) else {}
    head_name = head_repo.get("full_name")
    base_name = base_repo.get("full_name")
    same = bool(head_name and base_name and head_name == base_name)
    return Pull(
        number=number,
        head_sha=head_sha,
        base_sha=base_sha,
        draft=bool(data.get("draft")),
        labels=tuple(labels),
        title=str(data.get("title") or "")[:300],
        body=str(data.get("body") or "")[:4000],
        same_repository=same,
    )


class GitHubClient:
    def __init__(self, *, transport, repository: str, token: str, number: int, api: str = GITHUB_API):
        self.transport = transport
        self.repository = repository
        self.token = token
        self.number = number
        self.api = api.rstrip("/")
        self._loaded_id: int | None = None
        self._loaded_sha = ""
        self._loaded = False
        self._markers: tuple[set[str], set[str]] | None = None

    def get_pull(self) -> Pull:
        payload = self._json("GET", f"/repos/{self.repository}/pulls/{self.number}")
        if not isinstance(payload, dict):
            raise ReviewServiceError("github")
        return pull_from_api(payload, self.number)

    def commit_shas(self) -> list[str]:
        rows = self._pages(f"/repos/{self.repository}/pulls/{self.number}/commits", max_pages=10)
        return [row["sha"] for row in rows if isinstance(row, dict) and isinstance(row.get("sha"), str)]

    def pull_diff(self) -> str:
        return self._text("GET", f"/repos/{self.repository}/pulls/{self.number}", GITHUB_DIFF, timeout=60)

    def compare_diff(self, base: str, head: str) -> str | None:
        base_q = urllib.parse.quote(base, safe="")
        head_q = urllib.parse.quote(head, safe="")
        status, payload = self._raw(
            "GET",
            f"/repos/{self.repository}/compare/{base_q}...{head_q}",
            GITHUB_DIFF,
            timeout=60,
        )
        if status == 404:
            return None
        if status != 200:
            raise ReviewServiceError("github", status=status)
        return payload.decode("utf-8", errors="replace")

    def file_texts(self, paths: list[str]) -> dict[str, str]:
        found: dict[str, str] = {}
        for path in paths:
            clean = normalize_repo_path(path)
            if clean is None or is_ignored_path(clean):
                continue
            query = urllib.parse.urlencode({"ref": f"refs/pull/{self.number}/head"})
            quoted = urllib.parse.quote(clean, safe="/")
            try:
                status, payload = self._raw(
                    "GET",
                    f"/repos/{self.repository}/contents/{quoted}?{query}",
                    GITHUB_RAW,
                    timeout=20,
                )
            except ReviewServiceError:
                continue
            if status != 200 or len(payload) > 120_000:
                continue
            found[clean] = payload.decode("utf-8", errors="replace")
        return found

    def load_ledger(self) -> LoadedLedger | None:
        chosen = self._ledger_comment()
        self._loaded = True
        if chosen is None:
            self._loaded_id = None
            self._loaded_sha = ""
            return None
        state = parse_ledger(str(chosen.get("body") or ""))
        self._loaded_id = chosen["id"]
        self._loaded_sha = state["head_sha"] if state else ""
        return LoadedLedger(chosen["id"], state)

    def save_ledger(self, body: str) -> None:
        if not self._loaded:
            raise ReviewServiceError("github")
        current = self._ledger_comment()
        if self._loaded_id is None:
            if current is not None:
                raise LedgerConflict()
            self._json(
                "POST",
                f"/repos/{self.repository}/issues/{self.number}/comments",
                {"body": body},
            )
            return
        if current is None or current.get("id") != self._loaded_id:
            raise LedgerConflict()
        state = parse_ledger(str(current.get("body") or ""))
        current_sha = state["head_sha"] if state else ""
        if current_sha != self._loaded_sha:
            raise LedgerConflict()
        self._json(
            "PATCH",
            f"/repos/{self.repository}/issues/comments/{self._loaded_id}",
            {"body": body},
        )

    def existing_fingerprints(self) -> set[str]:
        return set(self._marker_sets()[0])

    def existing_resolved(self) -> set[str]:
        return set(self._marker_sets()[1])

    def create_review(self, commit_id: str, body: str, comments: list[dict]) -> dict[str, int]:
        posted = self._post_review(commit_id, body, comments)
        if posted is None and comments:
            combined = body.rstrip() + "\n\n" + "\n\n".join(comment["body"] for comment in comments)
            posted = self._post_review(commit_id, combined, [])
        if posted is None:
            raise ReviewServiceError("github", status=422)
        return comment_ids_from_review(posted)

    def reply(self, comment_id: int, body: str) -> None:
        self._json(
            "POST",
            f"/repos/{self.repository}/pulls/{self.number}/comments/{int(comment_id)}/replies",
            {"body": body},
        )

    def _post_review(self, commit_id: str, body: str, comments: list[dict]) -> dict | None:
        payload: dict = {"commit_id": commit_id, "event": "COMMENT", "body": body}
        if comments:
            payload["comments"] = [
                {
                    "path": comment["path"],
                    "line": comment["line"],
                    "side": comment["side"],
                    "body": comment["body"],
                    "subject_type": "line",
                }
                for comment in comments
            ]
        status, parsed = self._request(
            "POST",
            f"/repos/{self.repository}/pulls/{self.number}/reviews",
            body=payload,
        )
        if status == 422:
            return None
        if status not in (200, 201) or not isinstance(parsed, dict):
            raise ReviewServiceError("github", status=status)
        return parsed

    def _marker_sets(self) -> tuple[set[str], set[str]]:
        if self._markers is not None:
            return self._markers
        findings: set[str] = set()
        resolved: set[str] = set()
        paths = (
            f"/repos/{self.repository}/pulls/{self.number}/comments",
            f"/repos/{self.repository}/pulls/{self.number}/reviews",
        )
        for path in paths:
            for row in self._pages(path):
                if not isinstance(row, dict) or not isinstance(row.get("body"), str):
                    continue
                findings.update(FINDING_RE.findall(row["body"]))
                resolved.update(RESOLVED_RE.findall(row["body"]))
        self._markers = (findings, resolved)
        return self._markers

    def _ledger_comment(self) -> dict | None:
        chosen = None
        for comment in self._pages(f"/repos/{self.repository}/issues/{self.number}/comments"):
            if not isinstance(comment, dict) or not isinstance(comment.get("id"), int):
                continue
            user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
            body = comment.get("body")
            if user.get("login") != LEDGER_AUTHOR or not isinstance(body, str):
                continue
            if LEDGER_MARKER not in body:
                continue
            if chosen is None or comment["id"] > chosen["id"]:
                chosen = comment
        return chosen

    def _pages(self, path: str, max_pages: int = 5) -> list:
        rows: list = []
        for page in range(1, max_pages + 1):
            joiner = "&" if "?" in path else "?"
            payload = self._json("GET", f"{path}{joiner}per_page=100&page={page}")
            if not isinstance(payload, list):
                raise ReviewServiceError("github")
            rows.extend(payload)
            if len(payload) < 100:
                break
        return rows

    def _text(self, method: str, path: str, accept: str, timeout: int = 30) -> str:
        status, payload = self._raw(method, path, accept, timeout=timeout)
        if status != 200:
            raise ReviewServiceError("github", status=status)
        return payload.decode("utf-8", errors="replace")

    def _json(self, method: str, path: str, body: dict | None = None):
        status, parsed = self._request(method, path, body=body)
        if status not in (200, 201):
            raise ReviewServiceError("github", status=status)
        return parsed

    def _request(self, method: str, path: str, body: dict | None = None, accept: str = GITHUB_ACCEPT):
        status, payload = self._raw(method, path, accept, body=body)
        if status == 204 or not payload:
            return status, None
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError:
            if status == 422:
                return status, None
            raise ReviewServiceError("github", status=status) from None
        return status, parsed

    def _raw(self, method: str, path: str, accept: str, body: dict | None = None, timeout: int = 30):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": accept,
            "User-Agent": "prks-review",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, payload, _headers = self.transport.send(
            method, self.api + path, headers, data, timeout
        )
        return status, payload


class XaiClient:
    def __init__(
        self,
        *,
        transport,
        api_key: str,
        model: str,
        reasoning: str,
        api_url: str,
        sleep=time.sleep,
    ):
        self.transport = transport
        self.api_key = api_key
        self.model = model
        self.reasoning = reasoning
        self.api_url = api_url
        self.sleep = sleep

    def complete(self, system: str, user: str) -> str:
        status, payload = self._post(self._body(system, user, include_reasoning=True))
        if status == 400 and self.reasoning and b"reasoning_effort" in payload:
            status, payload = self._post(self._body(system, user, include_reasoning=False))
        elif status in RETRY_STATUSES:
            self.sleep(2)
            status, payload = self._post(self._body(system, user, include_reasoning=True))
        if status != 200:
            raise ReviewServiceError("model", status=status)
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError:
            raise ReviewServiceError("model", status=status) from None
        if not isinstance(parsed, dict):
            raise ReviewServiceError("model", status=status)
        content = extract_message_content(parsed)
        if not content:
            raise ReviewServiceError("model", status=status)
        return content

    def _body(self, system: str, user: str, *, include_reasoning: bool) -> bytes:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "prks_review",
                    "strict": True,
                    "schema": REVIEW_SCHEMA,
                },
            },
        }
        if include_reasoning and self.reasoning:
            payload["reasoning_effort"] = self.reasoning
        return json.dumps(payload).encode("utf-8")

    def _post(self, body: bytes) -> tuple[int, bytes]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "prks-review",
        }
        try:
            status, payload, _headers = self.transport.send(
                "POST", self.api_url, headers, body, 120
            )
        except ReviewServiceError:
            raise
        except Exception:
            raise ReviewServiceError("model") from None
        return status, payload
