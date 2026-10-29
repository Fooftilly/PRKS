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

for tool in gh jq; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing $tool (required for read-only Project #3 lookup)." >&2
    exit 1
  fi
done

# GitHub CLI reads the Project field values; no issue/Project mutation occurs.
project_json="$(gh project item-list 3 --owner Fooftilly --format json --limit 1000)"
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
echo 'Ownership only. Before coding, verify live issue state, candidate approval, blockers, roadmaps, and overlapping PRs.' >&2
