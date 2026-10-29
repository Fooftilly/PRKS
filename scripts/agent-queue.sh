#!/usr/bin/env bash
# Read-only agent ownership view for PRKS Project #3.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo 'Usage: bash scripts/agent-queue.sh cursor|claude|chatgpt' >&2
  exit 2
fi
# Accept common spellings without creating extra Project Agent options.
# Normalize separators and case, then resolve to the canonical option.
agent_key="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's#[/_-]+# #g; s/[[:space:]]+/ /g; s/^ //; s/ $//')"
case "$agent_key" in
  cursor|'cursor cloud') role='Cursor Cloud' ;;
  claude|'claude code') role='Claude Code' ;;
  chatgpt|codex|'chatgpt codex') role='ChatGPT/Codex' ;;
  *) echo "Unknown agent: $1" >&2; exit 2 ;;
esac


# Prefer the live Project API when the caller has a valid project-scoped token.
# Managed cloud GitHub credentials often support git fetch but not gh GraphQL.
if command -v gh >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
  if project_json="$(gh project item-list 3 --owner Fooftilly --format json --limit 1000 2>/dev/null)"; then
    if direct_output="$(
      printf '%s\n' "$project_json" | jq -er --arg role "$role" '
        if (.items | type) != "array" or (.totalCount | type) != "number" then
          error("Incomplete Project #3 response: items or totalCount missing")
        elif .totalCount != (.items | length) then
          error("Incomplete Project #3 response: fetched items differ from totalCount")
        else
          (["Issue", "Status", "Execution", "Title", "URL"] | @tsv),
          (.items[]
            # Apply the same agent groups to Project values, including old aliases.
            | select(
                ((.agent // "" | ascii_downcase
                  | gsub("[[:space:]_/-]+"; " ") | sub("^ +"; "") | sub(" +$"; "")) as $value
                | if $role == "Cursor Cloud" then ($value == "cursor" or $value == "cursor cloud")
                  elif $role == "Claude Code" then ($value == "claude" or $value == "claude code")
                  else ($value == "chatgpt" or $value == "codex" or $value == "chatgpt codex")
                  end)
              )
            | select(.content.type == "Issue" and .content.repository == "Fooftilly/PRKS")
            | select((.content.number | type) == "number")
            | ["#\(.content.number)", (.status // "(unset)"),
               (.execution // "(unset)"), (.title // .content.title // ""),
               "https://github.com/Fooftilly/PRKS/issues/\(.content.number)"]
            | @tsv)
        end
      '
    )"; then
      printf '%s\n' "$direct_output"
      echo 'Ownership only. Before coding, verify live issue state, candidate approval, blockers, roadmaps, and overlapping PRs.' >&2
      exit 0
    fi
  fi
fi

# Read-only, credential-free fallback published by a protected master workflow.
# git fetch works through Claude's managed GitHub remote even when gh auth fails.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Missing python3 (required to validate the published queue).' >&2
  exit 1
fi
tmp="$(mktemp "${TMPDIR:-/tmp}/prks-agent-queue.XXXXXXXX")"
trap 'rm -f "$tmp"' EXIT

found=false
if command -v git >/dev/null 2>&1 && git -C "$repo_root" remote get-url origin >/dev/null 2>&1; then
  if git -C "$repo_root" fetch --quiet --no-tags --depth=1 origin refs/heads/project3-agent-queue 2>/dev/null \
     && git -C "$repo_root" show FETCH_HEAD:agent-queue.json > "$tmp" 2>/dev/null; then
    found=true
  fi
fi
if [[ "$found" != true ]] && command -v curl >/dev/null 2>&1; then
  if curl --fail --silent --show-error --location --connect-timeout 5 --max-time 20 \
    'https://raw.githubusercontent.com/Fooftilly/PRKS/project3-agent-queue/agent-queue.json' \
    -o "$tmp" 2>/dev/null; then
    found=true
  fi
fi
if [[ "$found" != true ]]; then
  echo 'Cannot read Project #3: gh authentication failed and the published queue is unreachable.' >&2
  echo 'Check GitHub remote access, or configure gh auth with Project read permission.' >&2
  exit 1
fi

python3 "$script_dir/project3_agent_queue.py" render --agent "$role" --snapshot "$tmp"
