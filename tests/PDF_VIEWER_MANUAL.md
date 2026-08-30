# PDF viewer manual checklist

How-to for a human pass over the PRKS PDF viewer. Automated KEEP coverage lives in `PDF_VIEWER_PARITY.md`. Run this after `python prks_app.py --testing` on a work with a real PDF.

Each item is a command. Check it off when the behavior matches.

## Open and navigate

- [ ] Open a PDF. Pages paint. Scroll is continuous and vertical. `[manual:pdf-open]`
- [ ] The work title and type badge sit in the PDF toolbar. No separate title row above the viewer. `[manual:toolbar-title]`
- [ ] Click the current page number. Type a valid page and press Enter. The viewer navigates. Invalid input does not navigate. Scrolling updates the field. `[manual:page-input]`
- [ ] Scroll rapidly. Pages stay readable. No blank stuck viewport. `[manual:rapid-scroll]`
- [ ] Last page of a previously opened work restores. `[manual:last-page-memory]`

## Zoom

- [ ] Zoom − and + change the percentage. `[manual:zoom-buttons]`
- [ ] Click the zoom percentage. Choose 150%. The page scales. `[manual:zoom-menu]`
- [ ] Ctrl/Cmd+wheel zooms without a leftover CSS scale on the page. `[manual:ctrl-wheel]`
- [ ] Fit width and Fit page are available from the zoom menu. Fit width is the initial default. `[manual:fit-commands]`

## Selection

- [ ] Select a short phrase. A compact popup offers Highlight, Underline, and Copy above the selection when there is room. `[manual:select-short]`
- [ ] Select across more than one line. The popup stays centered on the selection and inside the viewport. `[manual:select-multiline]`
- [ ] Start a selection, release the pointer outside the page. Selection ends. A second selection works. `[manual:select-outside-release]`
- [ ] Copy selected text with the popup Copy action. `[manual:copy-selected-text]`

## Markup

- [ ] Highlight from the toolbar and from the popup look the same. `[manual:highlight-both-paths]`
- [ ] Underline from the toolbar and from the popup look the same. `[manual:underline-both-paths]`

## Annotations

- [ ] Click a highlight. A small menu appears. The right-side comment editor does not open by itself. `[manual:annotation-click-menu]`
- [ ] Edit comment opens the PRKS editor. Save comment persists. `[manual:annotation-comment]`
- [ ] Delete from the on-PDF menu removes the markup. Undo brings it back if History still has the command. `[manual:annotation-delete]`
- [ ] Reload. Deleted markup stays gone. Remaining comments and `[[pdf:id]]` links still resolve. `[manual:annotation-persistence]`
- [ ] Sidebar shows label, page, Jump, Edit/Add comment, Copy link. Jump scrolls to the markup. `[manual:annotation-sidebar]`
- [ ] Existing non-PRKS annotations (links, widgets) render when the engine supports them. They do not get Delete. `[manual:embedded-annotations]`

## Pan

- [ ] Pan mode drags the viewport. Release outside the viewport stops the pan. Pointer mode can select again. `[manual:pan]`

## Wiki links and save

- [ ] Copy `[[pdf:id]]` from the sidebar. Opening that link from notes jumps to the annotation. `[manual:pdf-wiki-link]`
- [ ] After a create, update, or delete, the save indicator finishes. Reloading the work keeps the PDF and metadata in sync. `[manual:save-confirm]`

## Coarse pointer and narrow chrome

- [ ] On a touch or coarse pointer, selection can start and end. The popup appears. Highlight and Underline work. A second selection works. Pan can be entered and left. `[manual:touch-selection]`
- [ ] Narrow the PDF pane near 640px. Pointer, page, and zoom −/+ stay visible. Highlight, Underline, Undo, and Redo sit behind More tools. The toolbar is not a long horizontal scroller. `[manual:responsive-toolbar]`
