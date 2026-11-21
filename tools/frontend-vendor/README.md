# Maintainer-only ordinary frontend vendor pins (DOMPurify, EasyMDE, CodeMirror, Lucide, citeproc-js).

Not used at PRKS application runtime. Docker does not run npm.

```bash
cd tools/frontend-vendor
npm ci
npm run build
```

The citation formatter's styles and en-US locale (`frontend/vendor/csl/`)
are raw vendor files from the Citation Style Language styles and locales
repositories. To update them, replace the files with the upstream files at
a new commit, record that commit and the new hashes in `csl/VERSION`, then
run `python scripts/dependency_gate.py --write-manifest`.

Inter remains an explicitly managed raw vendor under `frontend/vendor/inter/`
(npm packaging would alter the CSS/woff2 contract). Rebuild Inter by updating
its `VERSION` + assets manually, then:

```bash
python scripts/dependency_gate.py --write-manifest
```

`npm ci` + `npm run build` with unchanged inputs must leave a clean git tree
for the files this island owns (no `fetched:` dates).
