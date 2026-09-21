# Documentation screenshots

Committed screenshots must use only isolated testing data and synthetic/public-domain material. Never capture a real personal PRKS library.

## Regenerate

Install both the runtime dependency pin (Pillow for lossless PNG optimize, PyMuPDF for seeding) and the maintainer/browser pin (Playwright) first:

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
```

Commit any screenshot-affecting source edits (`frontend/`, capture/seed scripts, Playwright/Pillow pins, and the other paths listed in `scripts/check_screenshot_freshness.py`) before regenerating. The capture records `git rev-parse HEAD`, so uncommitted UI changes would otherwise be labeled with an older revision and look stale after you commit them. Generated files under `docs/screenshots/` may remain dirty during regeneration.

Then run:

```bash
python scripts/update_demo_screenshots.py
```

The wrapper starts an isolated `--testing` PRKS server on a free loopback port, confirms that child owns an empty `/api/works` response, seeds the public-domain demo library, uses the repository-local Playwright Chromium policy, stages screenshots before replacing committed PNGs, writes `manifest.json`, and shuts the temporary server down.

Each staged PNG is **losslessly** recompressed with the pinned Pillow dependency (`optimize=True`, `compress_level=9`) before promotion. The optimizer verifies dimensions and pixel values match the capture, keeps the original staged file if the rewrite fails or is not smaller, and never converts to JPEG/WebP or applies lossy processing. Committed documentation screenshots therefore arrive already optimized; Imgbot-style follow-up PRs are unnecessary for this path.

Use `--set readme`, `--set extra`, or `--set all` to select a capture group. Only `--set all` advances the directory-wide `source_commit`. Partial runs update per-file provenance for the regenerated images and leave untouched screenshots' prior revisions in place.

## Freshness

`manifest.json` records viewport/theme metadata, logical screenshot scenarios, capture revisions, and (after regeneration) lossless PNG optimizer metadata (`png_optimize`: Pillow version + lossless mode). The initial seed lists every expected screenshot file without a capture revision. After a full capture it also stores a directory-wide `source_commit`; each screenshot entry may carry its own `source_commit` so partial regenerations do not bless older images.

`scripts/check_screenshot_freshness.py` compares those revisions with the current tree and warns when any expected or on-disk screenshot lacks a capture revision. It emits a warning when frontend or other screenshot-affecting sources changed after a captured revision. The check is intentionally advisory: rendering differences are not treated as brittle pixel-golden tests.

The existing screenshots predate revision tracking; regenerate with `python scripts/update_demo_screenshots.py` to establish per-file revisions.
