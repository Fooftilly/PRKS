# PRKS PDF viewer

Maintainer-only Node island. Application runtime and Docker serve the files already in `frontend/vendor/prks-pdf-viewer/`. Do not run npm in the container.

Rebuild:

```bash
cd tools/pdf-viewer
npm ci
npm run build
```

Pins EmbedPDF **2.15.0** and React **18.3.1** / React DOM **18.3.1**. React exists only inside the generated bundle.

Open documents through DocumentManager (`initialDocuments`, `openDocumentUrl`, `openDocumentBuffer`). `engine.saveAsCopy()` is for export only.

`fontFallback` is `null` — no CDN latin/CJK font packs. Pan is included (`defaultMode: 'never'`). Tiling stays off until a fixture shows RenderLayer failing on a large or highly zoomed PDF.

Do not add `@embedpdf/snippet` or `@embedpdf/react-pdf-viewer`. Do not fork the EmbedPDF repository. Reviewed 2.15.0 plugin patches in `patches/` are allowed and applied by `postinstall` / `npm run build` (see `patches/README.md`). Whole-repo forks are not.

Viewer-shell KEEP / INTENTIONALLY_REMOVED / PRKS_REPLACEMENT lives in `tests/PDF_VIEWER_PARITY.md`. Manual UX checks live in `tests/PDF_VIEWER_MANUAL.md`. Highlight and underline colors come from `src/markup.ts` via `AnnotationPluginConfig.tools` and the selection popup. Application JS talks only through `PrksPdfViewerHandle`.

The zoom percentage is a PRKS menu (presets plus Fit width/page). Narrow panes hide Highlight/Underline/Undo/Redo behind More tools using a container query at 640px. Word snapping and selection handles are not restored. Documented SelectionScope cannot replace the active range.

Zoom presets and Fit width/page live in the viewer toolbar. `PrksPdfViewerHandle` keeps `zoomIn`, `zoomOut`, `fitWidth`, and `fitPage`. It does not expose `requestZoom`.

Ctrl/Cmd+wheel is a PRKS adapter (`WheelZoom` in `src/gestures.tsx`), not a zoom-plugin patch. EmbedPDF still owns `requestZoomBy` and layout; `enableWheel={false}` stays.

Real-mouse leave-page / leave-viewport checks live in `tests/browser/pointer_capture.py` (Playwright, not a runtime dependency, not collected by `python run_tests.py`). Synthetic events in `tests/browser/pdf_viewer.html` do not prove pointer capture.
