# PDF viewer parity

Reference for the PRKS headless PDF viewer. The baseline is the last pre-headless customized snippet viewer. Architecture stays:

```text
PRKS app → PrksPdfViewer adapter → PRKS React shell → EmbedPDF 2.15 plugins → PDFium
```

Do not return to `@embedpdf/snippet`. Do not replace EmbedPDF in this contract.

Classification:

- `KEEP` is retained user workflow. Coverage must name an explicit parity marker.
- `INTENTIONALLY_REMOVED` is snippet or generic EmbedPDF chrome we do not want.
- `PRKS_REPLACEMENT` is the same job done by a first-party PRKS control.

Coverage tokens use a prefix and a slug. KEEP coverage cannot be empty or `—`.

```text
browser:slug       data-parity="slug" in tests/browser/pdf_viewer.html
playwright:slug    # parity: slug in tests/browser/pointer_capture.py
python:slug        # parity: slug in tests/test_*.py
manual:slug        [manual:slug] in tests/PDF_VIEWER_MANUAL.md
```

Comma-separated tokens are allowed. Future viewer changes that keep, drop, or replace behavior must update this table in the same change.

## Matrix

| Capability | Classification | Coverage |
|---|---|---|
| PDF rendering | KEEP | browser:pdf-render |
| Continuous vertical scrolling | KEEP | browser:large-pdf-scroll |
| Fit-width default | KEEP | python:fit-width-default |
| Zoom in/out | KEEP | browser:zoom-controls |
| Pointer and text-selection mode | KEEP | browser:pointer-mode |
| Pan mode | KEEP | browser:pan-mode |
| Highlight | KEEP | browser:highlight-committed, browser:markup-highlight-equal, python:markup-source-parity |
| Underline | KEEP | browser:underline-committed, browser:markup-underline-equal, python:markup-source-parity |
| Undo | KEEP | browser:undo-redo |
| Redo | KEEP | browser:undo-redo |
| Selected-text Copy | KEEP | manual:copy-selected-text |
| Existing embedded annotation rendering | KEEP | manual:embedded-annotations |
| Annotation selection | KEEP | browser:annotation-select-menu |
| Annotation deletion | KEEP | browser:annotation-delete, python:delete-annotation-api, python:annotation-action-locks |
| Annotation sidebar | KEEP | manual:annotation-sidebar |
| Annotation comment editing | KEEP | browser:annotation-edit-comment, manual:annotation-comment, python:annotation-action-locks |
| `[[pdf:id]]` | KEEP | python:pdf-wiki-link |
| Jump to annotation | KEEP | browser:jump-to-annotation |
| Last-page memory | KEEP | manual:last-page-memory |
| Save modified PDF | KEEP | browser:save-copy |
| Annotation metadata persistence | KEEP | browser:annotation-delete-reload, manual:annotation-persistence |
| Save-confirm handshake | KEEP | manual:save-confirm |
| Coarse-pointer selection lifecycle | KEEP | playwright:selection-outside-release, playwright:pan-outside-release, playwright:no-native-drag, manual:touch-selection |
| Word snapping on coarse pointer | PRKS_REPLACEMENT | python:touch-selection-api-probe |
| Selection adjustment handles | PRKS_REPLACEMENT | python:touch-selection-api-probe |
| Zoom percentage control | KEEP | browser:zoom-menu, manual:zoom-menu |
| Fit width and fit page | KEEP | browser:zoom-menu, manual:fit-commands |
| Responsive toolbar | KEEP | python:responsive-toolbar, browser:responsive-toolbar, manual:responsive-toolbar |
| Selection release outside the page | KEEP | playwright:selection-outside-release, browser:selection-in-page |
| Pan release outside the viewport | KEEP | playwright:pan-outside-release |
| Native page ghost-drag suppressed | KEEP | browser:no-native-drag, playwright:no-native-drag |
| Ctrl/Cmd+wheel zoom | PRKS_REPLACEMENT | python:wheel-zoom |
| Generic EmbedPDF search UI | INTENTIONALLY_REMOVED | — |
| Viewer thumbnail/sidebar panel | INTENTIONALLY_REMOVED | — |
| Generic EmbedPDF comments panel | INTENTIONALLY_REMOVED | — |
| Annotation style picker | INTENTIONALLY_REMOVED | — |
| Links UI | INTENTIONALLY_REMOVED | — |
| Strikeout | INTENTIONALLY_REMOVED | — |
| Squiggly | INTENTIONALLY_REMOVED | — |
| Stamps | INTENTIONALLY_REMOVED | — |
| Free text | INTENTIONALLY_REMOVED | — |
| Ink/drawing | INTENTIONALLY_REMOVED | — |
| Shapes | INTENTIONALLY_REMOVED | — |
| Forms | INTENTIONALLY_REMOVED | — |
| Redaction | INTENTIONALLY_REMOVED | — |
| Generic document menu | INTENTIONALLY_REMOVED | — |
| Open/close document commands | INTENTIONALLY_REMOVED | — |
| Print | INTENTIONALLY_REMOVED | — |
| Generic export UI | INTENTIONALLY_REMOVED | — |
| Fullscreen | INTENTIONALLY_REMOVED | — |
| Generic annotation mode tabs | INTENTIONALLY_REMOVED | — |
| Rotate | INTENTIONALLY_REMOVED | — |
| Spread / two-page layout | INTENTIONALLY_REMOVED | — |
| Ctrl+F interception | INTENTIONALLY_REMOVED | — |
| Generic EmbedPDF zoom UI | INTENTIONALLY_REMOVED | — |

Zoom presets and Fit width/page live in the first-party toolbar. `PrksPdfViewerHandle` keeps `zoomIn`, `zoomOut`, `fitWidth`, and `fitPage`. It does not expose `requestZoom`.

## Rotate and Spread

Rotate and Spread are `INTENTIONALLY_REMOVED`. Neither plugin is registered. The app never calls them. PRKS reads papers in a continuous vertical fit-width flow. Restoring snippet chrome without that workflow would be feature expansion.

## Word snapping and handles

Word snapping and selection handles are `PRKS_REPLACEMENT`. The documented EmbedPDF 2.15 SelectionScope surface used by PRKS is `copyToClipboard`, `getSelectedText`, `getFormattedSelection`, `onSelectionChange`, and `onEndSelection`. That surface cannot replace the active selection range. `setSelection` / `getState` exist on the installed TypeScript interface and are not treated as supported public mutation APIs.

Do not restore `prks-selection-touch.js` or store actions. Do not add a selection-plugin patch for this gap. The coarse-pointer start/end/popup/Highlight lifecycle remains `KEEP`.

## Persistence

Deletion is a normal committed annotation mutation:

```text
deleteAnnotation()
→ committed delete
→ existing persistence queue
→ saveCopy()
→ POST PDF
→ getAnnotations()
→ POST metadata
→ save-confirm
```

There is no metadata-only delete path and no new backend PDF endpoint.
