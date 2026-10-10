# Dry-run summary (read-only, live Project #3, 2026-10-10)

Run with `scripts/project3_migrate.py <stage>` without `--apply`. No mutation was sent.

## migrate-existing

Counts: {'would-change': 41, 'held': 1, 'unchanged': 2}
Missing requirements (block --apply): field 'Execution' missing, field 'Roadmap Stage' missing

| Item | Outcome | Before | After | Detail |
|---|---|---|---|---|
| Issue#35 | would-change | {'Status': None} | {'Status': 'Inbox'} |  |
| Issue#38 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#39 | held |  |  | approved=false in manifest |
| Issue#40 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#41 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#42 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#43 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#44 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#45 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#46 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#47 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#48 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#49 | would-change | {'Status': 'Research / Design', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Research / Design'} | field not created yet: Roadmap Stage |
| Issue#50 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#51 | would-change | {'Status': 'Research / Design', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Research / Design'} | field not created yet: Roadmap Stage |
| Issue#52 | would-change | {'Status': 'Research / Design', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Research / Design'} | field not created yet: Roadmap Stage |
| Issue#53 | would-change | {'Status': 'Idea', 'Roadmap Stage': None} | {'Status': 'Inbox', 'Roadmap Stage': 'Idea'} | field not created yet: Roadmap Stage |
| Issue#57 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#58 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#59 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#60 | would-change | {'Status': 'Research / Design', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Research / Design'} | field not created yet: Roadmap Stage |
| Issue#61 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#62 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#75 | would-change | {'Status': None} | {'Status': 'Inbox'} |  |
| Issue#77 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#81 | would-change | {'Status': None} | {'Status': 'Inbox'} |  |
| Issue#82 | would-change | {'Status': None} | {'Status': 'Inbox'} |  |
| Issue#85 | unchanged | {} |  |  |
| Issue#87 | would-change | {'Status': None} | {'Status': 'Inbox'} |  |
| Issue#91 | unchanged | {} |  |  |
| Issue#102 | would-change | {'Status': None} | {'Status': 'Inbox'} |  |
| Issue#142 | would-change | {'Status': 'Research / Design', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Research / Design'} | field not created yet: Roadmap Stage |
| Issue#179 | would-change | {'Roadmap Stage': None} | {'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#310 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#314 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#315 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#316 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#317 | would-change | {'Status': 'Research / Design', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Research / Design'} | field not created yet: Roadmap Stage |
| Issue#318 | would-change | {'Status': 'Research / Design', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Research / Design'} | field not created yet: Roadmap Stage |
| Issue#391 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#423 | would-change | {'Status': 'Idea', 'Roadmap Stage': None} | {'Status': 'Inbox', 'Roadmap Stage': 'Idea'} | field not created yet: Roadmap Stage |
| Issue#431 | would-change | {'Status': 'Idea', 'Roadmap Stage': None} | {'Status': 'Inbox', 'Roadmap Stage': 'Idea'} | field not created yet: Roadmap Stage |
| Issue#446 | would-change | {'Status': 'Ready', 'Roadmap Stage': None} | {'Status': 'Backlog', 'Roadmap Stage': 'Planned'} | field not created yet: Roadmap Stage |
| Issue#472 | would-change | {'Status': 'Idea', 'Roadmap Stage': None} | {'Status': 'Inbox', 'Roadmap Stage': 'Idea'} | field not created yet: Roadmap Stage |

## backfill

Counts: {'would-change': 230}
Missing requirements (block --apply): field 'Execution' missing, field 'Roadmap Stage' missing

