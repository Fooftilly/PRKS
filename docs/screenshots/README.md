# Documentation screenshots

Committed screenshots must use only isolated testing data and synthetic/public-domain material. Never capture a real personal PRKS library.

## Regenerate

Install the maintainer/browser dependencies first:

```bash
python -m pip install -r requirements-dev.txt
```

Then run:

```bash
python scripts/update_demo_screenshots.py
```

The wrapper starts an isolated `--testing` PRKS server on a free loopback port, seeds the public-domain demo library, uses the repository-local Playwright Chromium policy, captures the screenshots, writes `manifest.json`, and shuts the temporary server down.

Use `--set readme`, `--set extra`, or `--set all` to select a capture group.

## Freshness

`manifest.json` records the Git commit whose UI was captured plus viewport/theme metadata and logical screenshot scenarios.

`scripts/check_screenshot_freshness.py` compares that revision with the current tree. It emits a warning when frontend or other screenshot-affecting sources changed after the capture. The check is intentionally advisory: rendering differences are not treated as brittle pixel-golden tests.

The existing screenshots predate manifest tracking, so the initial manifest has no `source_commit`. The first regeneration establishes it.
