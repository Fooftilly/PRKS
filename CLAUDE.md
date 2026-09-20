# PRKS Claude instructions

Before making any changes, read `AGENTS.md` completely and follow it as the
authoritative engineering and testing policy for this repository.

For any UI or interaction work, also read `DESIGN.md` before changing code.

Important PRKS rules:

- Never use production `data/` or a live `PRKS_STORAGE`.
- Default application runs must use `python prks_app.py --testing`.
- Do not run full browser E2E repeatedly while developing.
- Prefer unit/API/Node/static tests during implementation.
- Run affected/feature E2E after the implementation is complete.
- Run the full E2E gate at most once as final validation when appropriate.
- Never run the UX Interaction Tour unless explicitly requested.
- Do not push directly to `master`.
- Work on the task branch and create a PR when the task is complete.
- Do not merge the PR.