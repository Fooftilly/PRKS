# Classic-script production distribution (Issue #736)

PRKS's source checkout retains its 84 individually ordered classic JavaScript
files (five in the HTML head and 79 in the body). This preserves simple
development, the existing dependency-manifest checks and the documented
source/reference assets. The **Docker image** now builds a separate frontend
distribution: two concatenated classic scripts at the same parser positions.
There is no Node/npm dependency in the published runtime image.

## Evidence and scope

Three same-runner ABBA experiments on 40 selected E2E tests each measured
**7.08%, 3.09%, and 6.56%** faster wall time with head/body bundling:
[original](https://github.com/Fooftilly/PRKS/actions/runs/39022151709),
[repeat 1](https://github.com/Fooftilly/PRKS/actions/runs/39030048632),
[repeat 2](https://github.com/Fooftilly/PRKS/actions/runs/39030056360).
The corrected diagnostic [six-shard full E2E canary](https://github.com/Fooftilly/PRKS/actions/runs/39028440166)
passed **837/837 tests**, including offline reload. These observations do
**not** directly measure real user page speed or the Docker distribution.
The dedicated **E2E Classic Production Distribution Gate** runs all six shards
against the actual generated files in this PR, not the diagnostic candidate.

## Build and deterministic output

From a checkout, run:

```sh
python -m scripts.build_classic_distribution --root . --output dist/frontend
```

The destination must not already exist and must not be within the source
`frontend/` tree. The generator never rewrites the checked-out source.
Its inputs are the same strict head/body inventory and candidate builder
used by the earlier benchmark. It validates 5 head and 79 body classic
sources and every matching required service-worker precache entry.
Unsupported/new/missing source markup or manifest drift **fails the build**.

The build copies `frontend/` into a staged directory, emits
`/js/prks-classic-head.bundle.js` and `/js/prks-classic-body.bundle.js`,
substitutes the two HTML script tags while leaving the existing module tags
at their original positions, and updates the **packaged** service worker to:

- precache both new bundles as required static files in place of the
  84 separately fetched originals, avoiding needless offline-install requests;
- keep the canonical vendor dependency revision unchanged, but append a
  16-hex SHA-256-derived `CLASSIC_BUNDLE_REVISION` to the shell and static
  cache identities, so new bundle/index bytes cannot reuse the old cache;
- retain the existing staging-cache install and promotion rules, static
  `/js/` eligibility, old-version retirement, and PDF cache isolation.

All 84 original JS source files remain available in the distribution for
non-shell consumers and rollback. The build uses a temporary sibling directory
and renames it into place only after all artifacts are generated; failures do
not modify the checkout or publish a partial output. No CDN requests or
runtime build tools are introduced.

## Docker activation and rollback

The multi-stage Docker build uses the generator by default. Run the existing
`./docker-build.sh` or `docker compose build` to build this distribution.
The final image includes the generated assets, not the builder scripts.

To restore the previous unbundled frontend without changing repository files,
rebuild with:

```sh
docker compose build --build-arg PRKS_CLASSIC_BUNDLES=0
docker compose up -d
```

Alternatively use `docker build --build-arg PRKS_CLASSIC_BUNDLES=0 -t prks:latest .`.
The build argument accepts only `0` or `1`. For source-checkout runs,
`python prks_app.py --testing` still serves the original unbundled frontend.
Keep a previous image tag for immediate operational rollback. User data,
PDF storage and database contents are unaffected by this packaging choice.

Service-worker updates still require online connectivity to download every
required new asset before installing; a failed required download rejects the
new install and leaves the older active worker in place. Offline tests cover
reopening the packaged candidate, but **mixed-version client tabs, upgrades
and downgrades require dedicated release verification** before general rollout.

## Validation before merge

```sh
python -m unittest tests.test_classic_distribution
python -m scripts.build_classic_distribution --root . --output dist/frontend
node --check dist/frontend/js/prks-classic-head.bundle.js
node --check dist/frontend/js/prks-classic-body.bundle.js
node --check dist/frontend/sw.js
```

The dedicated PR workflow runs structural service-worker tests and the full
E2E suite on the packaged assets. Check it **on the exact PR head**, plus the
normal required CI and review findings. The script assembler preserves raw
classic globals and source order, but still changes
`document.currentScript`/source URL attribution and per-file parsing; keep
these as explicit review risks. The user/maintainer approves every merge.
This distribution build coordinates with [#340](https://github.com/Fooftilly/PRKS/issues/340);
do not add another permanent artifact/dependency pipeline.
