"""Command line entry for the PRKS review watcher.

Environment variables are the only input. Pull request text is fetched by the
GitHub client and is not interpolated into a shell command.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass

from clients import GitHubClient, UrllibTransport, XaiClient
from pr_review import ReviewConfig, run_review

DEFAULT_API_URL = "https://api.x.ai/v1/chat/completions"
DEFAULT_MODEL = "grok-4.6"
REASONING_LEVELS = {"low", "medium", "high", "xhigh"}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    repository: str
    number: int
    token: str
    api_key: str
    model: str
    reasoning: str
    api_url: str
    enabled: bool
    dry_run: bool
    force: bool
    review_forks: bool


def env_flag(env: dict, name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def settings_from_env(env: dict) -> Settings:
    repository = str(env.get("GITHUB_REPOSITORY") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ConfigError("GITHUB_REPOSITORY is not an owner/name")
    number_raw = str(env.get("PR_NUMBER") or "").strip()
    if not re.fullmatch(r"[1-9][0-9]{0,9}", number_raw):
        raise ConfigError("PR_NUMBER is not a positive integer")
    model = str(env.get("PRKS_REVIEW_MODEL") or DEFAULT_MODEL).strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", model):
        raise ConfigError("PRKS_REVIEW_MODEL is not a model id")
    reasoning = str(env.get("PRKS_REVIEW_REASONING") or "medium").strip().lower()
    if reasoning not in REASONING_LEVELS:
        reasoning = "medium"
    api_url = str(env.get("PRKS_REVIEW_API_URL") or DEFAULT_API_URL).strip()
    parsed = urllib.parse.urlparse(api_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ConfigError("PRKS_REVIEW_API_URL must be an https URL")
    token = str(env.get("GH_TOKEN") or env.get("GITHUB_TOKEN") or "").strip()
    return Settings(
        repository=repository,
        number=int(number_raw),
        token=token,
        api_key=str(env.get("XAI_API_KEY") or "").strip(),
        model=model,
        reasoning=reasoning,
        api_url=api_url,
        enabled=env_flag(env, "PRKS_REVIEW_ENABLED", True),
        dry_run=env_flag(env, "PRKS_REVIEW_DRY_RUN", False),
        force=env_flag(env, "PRKS_REVIEW_FORCE", False),
        review_forks=env_flag(env, "PRKS_REVIEW_FORKS", True),
    )


def write_summary(env: dict, text: str) -> None:
    path = env.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text + "\n")


def main(env: dict | None = None) -> int:
    source = dict(os.environ if env is None else env)
    try:
        settings = settings_from_env(source)
    except ConfigError as exc:
        print(f"[prks-review] {exc}", file=sys.stderr)
        return 1
    if not settings.enabled:
        write_summary(source, "PRKS review is disabled (PRKS_REVIEW_ENABLED).")
        print("[prks-review] skipped: disabled")
        return 0
    if not settings.token:
        print("[prks-review] misconfigured: missing GitHub token", file=sys.stderr)
        return 1
    if not settings.api_key and not settings.dry_run:
        write_summary(source, "PRKS review skipped: XAI_API_KEY is not configured.")
        print("[prks-review] skipped: model credential is not configured")
        return 0

    transport = UrllibTransport()
    github = GitHubClient(
        transport=transport,
        repository=settings.repository,
        token=settings.token,
        number=settings.number,
    )
    complete = None
    if settings.api_key:
        complete = XaiClient(
            transport=transport,
            api_key=settings.api_key,
            model=settings.model,
            reasoning=settings.reasoning,
            api_url=settings.api_url,
        ).complete
    secrets = tuple(
        value for value in (settings.token, settings.api_key) if len(value) >= 8
    )
    outcome = run_review(
        github,
        complete,
        ReviewConfig(
            enabled=settings.enabled,
            dry_run=settings.dry_run,
            force=settings.force,
            review_forks=settings.review_forks,
            secrets=secrets,
        ),
    )
    print(f"[prks-review] {outcome.status}: {outcome.reason}")
    if outcome.report is not None:
        print(json.dumps(outcome.report, ensure_ascii=True, sort_keys=True))
    if outcome.status == "unavailable":
        write_summary(
            source,
            f"PRKS review did not complete ({outcome.reason}). The revision was not recorded.",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
