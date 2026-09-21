# Documentation screenshots

Committed screenshots must use only isolated testing data and synthetic/public-domain material. Never capture a real personal PRKS library.

## Regenerate

Install the maintainer/browser dependencies first:

```bash
python -m pip install -r requirements-dev.txt
```

Commit any screenshot-affecting source edits (`frontend/`, capture/seed scripts, Playwright pin, and the other paths listed in `scripts/check_screenshot_freshness.py`) before regenerating. The capture records `git rev-parse HEAD`, so uncommitted UI changes would otherwise be labeled with an older revision and look stale after you commit them. Generated files under `docs/screenshots/` may remain dirty during regeneration.

Then run:

```bash
python scripts/update_demo_screenshots.py
```

The wrapper starts an isolated `--testing` PRKS server on a free loopback port, confirms that child owns an empty `/api/works` response, seeds the public-domain demo library, uses the repository-local Playwright Chromium policy, stages screenshots before replacing committed PNGs, writes `manifest.json`, and shuts the temporary server down.

Use `--set readme`, `--set extra`, or `--set all` to select a capture group. Only `--set all` advances the directory-wide `source_commit`. Partial runs update per-file provenance for the regenerated images and leave untouched screenshots' prior revisions in place.

## Freshness

`manifest.json` records viewport/theme metadata, logical screenshot scenarios, and capture revisions. After a full capture it also stores a directory-wide `source_commit`; each screenshot entry may carry its own `source_commit` so partial regenerations do not bless older images.

`scripts/check_screenshot_freshness.py` compares those revisions with the current tree. It emits a warning when frontend or other screenshot-affecting sources changed after a captured revision. The check is intentionally advisory: rendering differences are not treated as brittle pixel-golden tests.

The existing screenshots predate manifest tracking, so the initial manifest has no `source_commit`. The first full regeneration establishes it.
