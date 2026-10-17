# Third-party notices

The root [LICENSE](LICENSE) covers PRKS-authored code and documentation; it
does not supersede licenses held by upstream authors of dependencies, bundled
assets or other separately licensed files. Preserve the original notices in
redistributions and regenerated bundles. Version and file inventories are
tracked in [frontend/vendor/DEPENDENCY-MANIFEST.json](frontend/vendor/DEPENDENCY-MANIFEST.json)
and [dependency-inventory.json](dependency-inventory.json).

## Shipped frontend vendors

| Component | Notice file included with PRKS |
| --- | --- |
| CodeMirror 5 | [MIT license](frontend/vendor/codemirror/LICENSE) |
| Cytoscape.js | [MIT license](frontend/vendor/cytoscape/LICENSE) |
| DOMPurify | [Apache 2.0 license copy](frontend/vendor/dompurify/LICENSE) (upstream offers alternative licensing) |
| EasyMDE | [MIT license](frontend/vendor/easymde/LICENSE) |
| idb | [ISC license](frontend/vendor/idb/LICENSE) |
| Inter typeface | [SIL Open Font License 1.1](frontend/vendor/inter/LICENSE) |
| Lucide icons | [ISC license](frontend/vendor/lucide/LICENSE) |

The PRKS PDF viewer includes several upstream components, including EmbedPDF,
React, React DOM and PDFium. See its [dependency list](frontend/vendor/prks-pdf-viewer/THIRD_PARTY.md)
and [individual upstream license files](frontend/vendor/prks-pdf-viewer/LICENSES).
Generated JavaScript and WebAssembly are not relicensed by the PRKS root license.

## Packages installed separately

Runtime Python dependencies are pinned in [requirements.txt](requirements.txt):
PyMuPDF, Pillow and pydantic. They remain subject to their upstream licenses.
**PyMuPDF** is distributed under an AGPL option or commercial license; the
PRKS root license is not a commercial PyMuPDF license.

The shipped Vue browser bundle `frontend/vue/prks-vue.js` has accompanying [upstream license notices](frontend/vue/THIRD_PARTY_LICENSES.md), including copyright texts for Vue, TanStack Query, VueUse and Floating UI and full Apache-2.0 terms for the Atlassian drag-and-drop packages. Its [version-controlled source notices](frontend-app/THIRD_PARTY_LICENSES.md) are copied into `frontend/vue/` after each Vite build.

The Vue frontend, build tools, and PDF viewer source use dependencies declared
in their respective `package.json` and lock files under `frontend-app/`,
`tools/pdf-viewer/`, `tools/frontend-vendor/`, and `tools/research-graph/`.
Preserve any additional upstream license and copyright notices for every
actually distributed component, including transitive dependencies.

Do not remove or replace upstream notices while updating dependencies; consult
[Dependencies and Vendoring](docs/wiki/Dependencies-and-Vendoring.md) before
changing the shipped assets.

## Content and branding

User-provided PDFs, notes, images, bibliographic records and backups are not
licensed under PRKS's software license merely by being stored or processed in
PRKS. Distinct asset or branding rights, if applicable, remain with their
respective owners.
