# PRKS Claude instructions

Follow the repository-root `AGENTS.md` and any nested `AGENTS.md` that applies to files being changed. Do not reload unrelated scoped instructions.

For UI or interaction work, `DESIGN.md` is authoritative. Read the relevant sections for the affected component rather than loading the full document by default.

Important PRKS rules:

- Never use production `data/` or a live `PRKS_STORAGE`.
- Default application runs must use `python prks_app.py --testing`.
- Prefer unit/API/Node/static tests while implementing.
- Run affected/feature E2E once the implementation is coherent; run the full E2E gate only as final validation when appropriate.
- Never run the UX Interaction Tour unless explicitly requested.
- Do not push directly to `master`.
- Work on the task branch and create a PR when the task is complete.
- Do not merge the PR.
