/**
 * Core work detail UI: research notes, wiki links, BibTeX, metadata.
 * PDF / EmbedPDF integration is in works-pdf.js (loaded via dynamic import when work.file_path is set).
 */

function prksEscapeHtmlLite(s) {
    if (typeof window.prksEscapeHtml === 'function') return window.prksEscapeHtml(s);
    if (s == null || s === '') return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/**
 * Markdown preview allows raw HTML via marked; a &lt;style&gt; block with @import not first
 * triggers Chrome "An @import rule was ignored…" and is an XSS footgun. Drop active document hooks.
 */
function prksSanitizeMarkdownPreviewHtml(html) {
    if (html == null || html === '') return '';
    try {
        const wrapped = '<div class="prks-md-preview-root">' + html + '</div>';
        const doc = new DOMParser().parseFromString(wrapped, 'text/html');
        const root = doc.body.querySelector('.prks-md-preview-root');
        if (!root) return html;
        root.querySelectorAll('script, style, link, meta, iframe, object, embed').forEach((el) => el.remove());
        return root.innerHTML;
    } catch (_e) {
        return '';
    }
}

function prksBuildWorkTitleLowerToIdMap(works) {
    const map = {};
    const sorted = [...(works || [])].sort((a, b) => String(a.id).localeCompare(String(b.id)));
    for (const w of sorted) {
        const k = (w.title || '').trim().toLowerCase();
        if (k && map[k] === undefined) map[k] = w.id;
    }
    return map;
}

/** Sorted list of { id, title } for [[…]] autocomplete in the research notes editor. */
function prksBuildWikiAutocompleteWorkList(works) {
    const rows = (works || [])
        .map((w) => ({ id: w.id, title: String(w.title || '').trim() }))
        .filter((w) => w.title);
    rows.sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: 'base' }));
    return rows;
}

const PRKS_WIKI_HINT_MAX = 50;

/** EasyMDE does not set window.CodeMirror; show-hint registers on the CDN global. Copy hint APIs onto the editor's bundled CodeMirror constructor. */
function prksEnsureEasyMDECodeMirrorHints(cm) {
    const internal = cm && cm.constructor;
    const globalCM = typeof window !== 'undefined' ? window.CodeMirror : undefined;
    if (!internal) return false;
    if (typeof internal.showHint === 'function' && typeof internal.prototype.showHint === 'function') {
        return true;
    }
    if (!globalCM || typeof globalCM.showHint !== 'function') return false;
    if (globalCM.prototype.showHint && !internal.prototype.showHint) {
        internal.prototype.showHint = globalCM.prototype.showHint;
    }
    if (globalCM.prototype.closeHint && !internal.prototype.closeHint) {
        internal.prototype.closeHint = globalCM.prototype.closeHint;
    }
    if (globalCM.showHint && !internal.showHint) {
        internal.showHint = globalCM.showHint;
    }
    return typeof internal.showHint === 'function' && typeof internal.prototype.showHint === 'function';
}

function prksGetWikiLinkAutocompleteContext(cm) {
    const cur = cm.getCursor();
    const lineText = cm.getLine(cur.line);
    const before = lineText.slice(0, cur.ch);
    const m = before.match(/\[\[([^\]|]*)$/);
    if (!m) return null;
    const startCh = before.lastIndexOf('[[') + 2;
    const CM = cm.constructor;
    const from = CM.Pos(cur.line, startCh);
    const to = cur;
    return { from, to, query: m[1] || '' };
}

function prksFilterWorksForWikiHint(rows, query) {
    const ql = query.trim().toLowerCase();
    if (!ql) return rows.slice(0, PRKS_WIKI_HINT_MAX);
    const pref = [];
    const sub = [];
    for (const w of rows) {
        const tl = w.title.toLowerCase();
        if (tl.startsWith(ql)) pref.push(w);
        else if (tl.includes(ql)) sub.push(w);
        if (pref.length >= PRKS_WIKI_HINT_MAX) break;
    }
    if (pref.length >= PRKS_WIKI_HINT_MAX) return pref.slice(0, PRKS_WIKI_HINT_MAX);
    const need = PRKS_WIKI_HINT_MAX - pref.length;
    return pref.concat(sub.slice(0, need));
}

/** CodeMirror hint pick: replace query with title and close wiki link. */
function prksWikiLinkCompletionPick(cm, data, completion) {
    const from = completion.from != null ? completion.from : data.from;
    const to = completion.to != null ? completion.to : data.to;
    const title = typeof completion.text === 'string' ? completion.text : '';
    cm.replaceRange(title + ']]', from, to, 'complete');
}

function prksWikiLinkHint(cm) {
    const ctx = prksGetWikiLinkAutocompleteContext(cm);
    if (!ctx) return null;
    const rows = window.__prksWikiWorkList || [];
    const matches = prksFilterWorksForWikiHint(rows, ctx.query);
    if (matches.length === 0) return null;
    return {
        from: ctx.from,
        to: ctx.to,
        list: matches.map((w) => ({
            text: w.title,
            displayText: w.title,
            hint: prksWikiLinkCompletionPick,
        })),
    };
}

function prksAttachWikiLinkAutocomplete(cm) {
    const hintPatched = cm ? prksEnsureEasyMDECodeMirrorHints(cm) : false;
    const CM = cm && cm.constructor;
    if (!cm || !hintPatched || typeof CM.showHint !== 'function') {
        return;
    }

    cm.on('inputRead', function (editor, change) {
        if (!change) return;
        if (change.origin === 'setValue' || change.origin === 'complete') return;
        requestAnimationFrame(function () {
            if (prksGetPdfAnnLinkAutocompleteContext(editor)) {
                CM.showHint(editor, prksPdfAnnLinkHint, { completeSingle: false });
                return;
            }
            if (!prksGetWikiLinkAutocompleteContext(editor)) return;
            CM.showHint(editor, prksWikiLinkHint, { completeSingle: false });
        });
    });

    const keys = cm.getOption('extraKeys') || {};
    const openWikiHint = function (editor) {
        if (prksGetPdfAnnLinkAutocompleteContext(editor)) {
            CM.showHint(editor, prksPdfAnnLinkHint, { completeSingle: false });
            return;
        }
        if (prksGetWikiLinkAutocompleteContext(editor)) {
            CM.showHint(editor, prksWikiLinkHint, { completeSingle: false });
        }
    };
    cm.setOption(
        'extraKeys',
        Object.assign({}, keys, {
            'Ctrl-Space': openWikiHint,
        })
    );
}

/** [[target|label]] / [[target]] → internal links; matches graph wiki resolution (first id per lowercase title). */
function prksReplaceWikiMarkersWithLinks(plainText, titleLowerToId) {
    const map = titleLowerToId || {};
    return plainText.replace(/\[\[([^\]]+)\]\]/g, (full, inner) => {
        const trimmed = String(inner).trim();
        let target;
        let label;
        const pipe = trimmed.indexOf('|');
        if (pipe >= 0) {
            target = trimmed.slice(0, pipe).trim();
            label = trimmed.slice(pipe + 1).trim() || target;
        } else {
            target = label = trimmed;
        }
        const key = target.toLowerCase();
        const id = map[key];
        if (!id) {
            return (
                '<span class="wiki-link-unresolved" title="Unresolved link">' +
                prksEscapeHtmlLite('[[' + inner + ']]') +
                '</span>'
            );
        }
        return (
            '<a href="#/works/' +
            id +
            '" class="wiki-link-internal">' +
            prksEscapeHtmlLite(label) +
            '</a>'
        );
    });
}

function prksEscapeAttr(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/** Default preview label when [[pdf:id]] has no |label — uses annotation cache if available. */
function prksDefaultLabelForPdfAnnId(annId) {
    const rows = typeof window.prksGetPdfAnnotationHintList === 'function' ? window.prksGetPdfAnnotationHintList() : [];
    const sid = String(annId);
    for (const r of rows) {
        if (r.id === sid) return r.displayText;
    }
    return sid.length > 14 ? sid.slice(0, 10) + '…' : sid;
}

/**
 * [[pdf:annotationId]] / [[pdf:annotationId|label]] → link that jumps to that PDF annotation in the viewer (preview click).
 * Process before [[…]] work links so "pdf:…" is not treated as a work title.
 */
function prksReplacePdfAnnotationWikiMarkers(plainText) {
    if (plainText == null || plainText === '') return plainText;
    return plainText.replace(/\[\[pdf:([^\]|]+)(?:\|([^\]]*))?\]\]/g, (full, annIdRaw, labelRaw) => {
        const id = String(annIdRaw || '').trim();
        if (!id) return prksEscapeHtmlLite(full);
        let label = labelRaw != null ? String(labelRaw) : '';
        label = label.trim();
        if (!label) label = prksDefaultLabelForPdfAnnId(id);
        return (
            '<a href="#" class="wiki-link-pdf-ann" data-pdf-ann-id="' +
            prksEscapeAttr(id) +
            '">' +
            prksEscapeHtmlLite(label) +
            '</a>'
        );
    });
}

function prksGetPdfAnnLinkAutocompleteContext(cm) {
    const cur = cm.getCursor();
    const lineText = cm.getLine(cur.line);
    const before = lineText.slice(0, cur.ch);
    const m = before.match(/\[\[pdf:([^\]|]*)$/);
    if (!m) return null;
    const startCh = before.lastIndexOf('[[pdf:') + '[[pdf:'.length;
    const CM = cm.constructor;
    const from = CM.Pos(cur.line, startCh);
    const to = cur;
    return { from, to, query: m[1] || '' };
}

function prksFilterPdfAnnForHint(rows, query) {
    const ql = query.trim().toLowerCase();
    if (!ql) return rows.slice(0, PRKS_WIKI_HINT_MAX);
    const pref = [];
    const sub = [];
    for (const w of rows) {
        const idl = String(w.id || '').toLowerCase();
        const dl = String(w.displayText || '').toLowerCase();
        if (idl.startsWith(ql) || dl.startsWith(ql)) pref.push(w);
        else if (idl.includes(ql) || dl.includes(ql)) sub.push(w);
        if (pref.length >= PRKS_WIKI_HINT_MAX) break;
    }
    if (pref.length >= PRKS_WIKI_HINT_MAX) return pref.slice(0, PRKS_WIKI_HINT_MAX);
    const need = PRKS_WIKI_HINT_MAX - pref.length;
    return pref.concat(sub.slice(0, need));
}

function prksPdfAnnLinkCompletionPick(cm, data, completion) {
    const from = completion.from != null ? completion.from : data.from;
    const to = completion.to != null ? completion.to : data.to;
    const id = typeof completion.text === 'string' ? completion.text : '';
    const lab = (completion.displayLabel || '').trim();
    const suffix = lab ? id + '|' + lab + ']]' : id + ']]';
    cm.replaceRange(suffix, from, to, 'complete');
}

function prksPdfAnnLinkHint(cm) {
    const ctx = prksGetPdfAnnLinkAutocompleteContext(cm);
    if (!ctx) return null;
    const rows = typeof window.prksGetPdfAnnotationHintList === 'function' ? window.prksGetPdfAnnotationHintList() : [];
    const matches = prksFilterPdfAnnForHint(rows, ctx.query);
    if (matches.length === 0) return null;
    return {
        from: ctx.from,
        to: ctx.to,
        list: matches.map((w) => ({
            text: w.id,
            displayText: w.displayText,
            displayLabel: w.displayText,
            hint: prksPdfAnnLinkCompletionPick,
        })),
    };
}

async function deleteWork(w_id) {
    if (!confirm('Are you sure you want to delete this file?')) return;
    try {
        const res = await fetch('/api/works/' + encodeURIComponent(w_id), { method: 'DELETE' });
        if (!res.ok) {
            alert('Error deleting file!');
            return;
        }
        window.location.hash = '#/folders';
    } catch (_e) {
        alert('Error deleting file!');
    }
}

function prksRouteStale(routeGen) {
    return typeof routeGen === 'number' && routeGen !== window.__prksRouteGen;
}

async function renderWorkDetails(work, container, routeGen) {
    if (!work) {
        container.innerHTML = '<p class="prks-inline-message prks-inline-message--error">File not found.</p>';
        return;
    }
    let pdfModule = null;
    const inferredKind =
        typeof prksInferWorkSourceKind === 'function' ? prksInferWorkSourceKind(work) : '';

    if (inferredKind === 'pdf' && work.file_path) {
        pdfModule = await import('/js/components/works-pdf.js');
    }
    if (prksRouteStale(routeGen)) return;
    let videoModule = null;
    if (inferredKind === 'video') {
        videoModule = await import('/js/components/works-video.js');
    }
    if (prksRouteStale(routeGen)) return;

    let authorsStr = '';
    if (work.roles) {
        const authorsList = [];
        work.roles.forEach((r) => {
            if (r.role_type === 'Author') authorsList.push(r);
            else {
                const nm = `${prksEscapeHtmlLite(r.first_name)} ${prksEscapeHtmlLite(r.last_name)} (${prksEscapeHtmlLite(r.role_type)})`;
                authorsStr += `<span class="tag prks-person-chip" data-person-id="${prksEscapeAttr(String(r.id || ''))}">${nm}</span>`;
            }
        });
        if (authorsList.length > 0) {
            const authorChips = authorsList
                .map(
                    (a) =>
                        `<span class="tag author-tag prks-person-chip" data-person-id="${prksEscapeAttr(String(a.id || ''))}">👤 ${prksEscapeHtmlLite(a.first_name)} ${prksEscapeHtmlLite(a.last_name)}</span>`
                )
                .join(' ');
            authorsStr = authorChips + authorsStr;
        }
    }

    let leftPane = '';
    if (inferredKind === 'pdf') {
        leftPane = work.file_path
            ? `<div class="work-pdf-pane"><div id="pdf-viewer"></div></div>`
            : `<div class="work-pdf-pane work-pdf-pane--empty"><p class="work-pdf-empty">No PDF file attached.</p></div>`;
    } else if (inferredKind === 'video') {
        leftPane =
            videoModule && typeof window.renderVideoViewerPane === 'function'
                ? window.renderVideoViewerPane(work)
                : `<div class="work-pdf-pane work-pdf-pane--empty"><p class="work-pdf-empty">Video viewer unavailable.</p></div>`;
    } else {
        leftPane = `<div class="work-pdf-pane work-pdf-pane--empty"><p class="work-pdf-empty">No file attached.</p></div>`;
    }

    let mainContent = `
        <div class="work-main-column">
            <div class="work-workspace" data-work-id="${prksEscapeAttr(work.id)}">
                ${leftPane}
                <div class="work-split-handle" role="separator" aria-orientation="horizontal" aria-label="Resize between document and research notes" tabindex="0">
                    <span class="work-split-handle-grip" aria-hidden="true"></span>
                </div>
                <div class="work-notes-pane">
                    <div class="work-notes-pane-header">
                        <h3 class="work-notes-title">Research Notes</h3>
                        <div class="work-notes-pane-header-actions">
                            <button type="button" class="work-notes-toggle-btn" id="work-notes-collapse-btn" aria-expanded="true" aria-controls="work-notes-editor-region" aria-label="Collapse research notes editor" title="Collapse notes"><span class="work-notes-toggle-btn__icon" aria-hidden="true"><svg class="work-notes-toggle-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2.65" stroke-linecap="round" stroke-linejoin="round"><polyline points="6.5 13 12 19 17.5 13"/><polyline points="6.5 6 12 12 17.5 6"/></svg></span></button>
                            <div id="editor-status" class="work-editor-status"></div>
                        </div>
                    </div>
                    <div class="work-notes-editor-wrap" id="work-notes-editor-region">
                        <textarea id="research-notes-editor"></textarea>
                    </div>
                </div>
            </div>
        </div>
    `;

    window.currentWork = work;
    const workTitle = String((work && work.title) || '').trim();
    const headerTitle = workTitle ? prksEscapeHtmlLite(workTitle) : 'Document';

    container.innerHTML = `
        <div class="work-detail">
            <div class="page-header page-header--work">
                <div class="card-heading-row card-heading-row--wrap">
                    <h2 class="page-header--work-title">${headerTitle}</h2>
                    <span id="work-header-doc-type-slot">${typeof prksDocTypeBadgeHtml === 'function' ? prksDocTypeBadgeHtml(work.doc_type) : ''}</span>
                </div>
            </div>
            <div class="document-view document-view--work">
                ${mainContent}
            </div>
        </div>
    `;

    const notesTa = document.getElementById('research-notes-editor');
    if (notesTa) notesTa.value = work.text_content || '';
    container.querySelectorAll('.prks-person-chip').forEach((el) => {
        el.style.cursor = 'pointer';
        el.addEventListener('click', () => {
            const pid = el.getAttribute('data-person-id');
            if (pid) window.location.hash = '#/people/' + encodeURIComponent(pid);
        });
    });

    // Populate Right Panel
    updatePanelContent('details');


    const editBtn = document.getElementById('edit-metadata-btn');
    if (editBtn) {
        editBtn.onclick = () => toggleWorkMetaEdit(true);
    }

    if (work.file_path && pdfModule && !prksRouteStale(routeGen)) {
        pdfModule.initPdfViewerForWork(work);
    }

    setTimeout(async () => {
        if (prksRouteStale(routeGen)) return;
        const wsEarly = container.querySelector('.work-workspace');
        if (wsEarly) {
            const key = 'prks.workNotesCollapsed.' + work.id;
            const saved = localStorage.getItem(key);
            const defaultCollapsed =
                saved == null &&
                typeof prksIsSmallScreen === 'function' &&
                prksIsSmallScreen();
            const shouldCollapse = saved === '1' || defaultCollapsed;
            if (shouldCollapse) wsEarly.classList.add('work-workspace--notes-collapsed');
        }
        try {
            const works = await fetchWorks();
            if (prksRouteStale(routeGen)) return;
            window.__prksWikiTitleMap = prksBuildWorkTitleLowerToIdMap(works);
            window.__prksWikiWorkList = prksBuildWikiAutocompleteWorkList(works);
        } catch (_e) {
            if (prksRouteStale(routeGen)) return;
            window.__prksWikiTitleMap = {};
            window.__prksWikiWorkList = [];
        }
        if (prksRouteStale(routeGen)) return;
        initEasyMDE(work);
        setupWorkNotesSplitResize(work.id);
        setupWorkNotesCollapseToggle(work.id);
    }, 200);

    fetch('/api/works/' + encodeURIComponent(work.id) + '/related_folders')
        .then((r) => (r.ok ? r.json() : []))
        .then((related) => {
            if (prksRouteStale(routeGen)) return;
            const target = document.getElementById('related-folders-container');
            if (!target || !Array.isArray(related) || related.length === 0) return;
            target.replaceChildren();
            for (const f of related) {
                const span = document.createElement('span');
                span.className = 'tag';
                span.style.background = 'var(--accent)';
                span.style.color = 'white';
                span.style.cursor = 'pointer';
                span.textContent = '\uD83D\uDCC1 ' + String(f.title || '');
                const fid = String(f.id || '');
                span.addEventListener('click', () => {
                    window.location.hash = '#/folders/' + encodeURIComponent(fid);
                });
                target.appendChild(span);
            }
        })
        .catch((err) => console.error('related folders fetch failed', err));
}

function initEasyMDE(work) {
    const titleLowerToId = window.__prksWikiTitleMap || {};
    try {
        localStorage.removeItem('smde_work-notes-' + work.id);
    } catch (_e) {
        /* ignore */
    }
    const easyMDE = new EasyMDE({
        element: document.getElementById('research-notes-editor'),
        spellChecker: false,
        /* Server PATCH below is the source of truth; EasyMDE localStorage autosave would restore stale drafts after reload (autosave delay > PATCH delay). */
        autosave: { enabled: false },
        toolbar: ["bold", "italic", "heading", "|", "quote", "unordered-list", "ordered-list", "|", "link", "image", "|", "preview", "side-by-side", "fullscreen", "|", "guide"],
        status: ["lines", "words", "cursor"],
        minHeight: "120px",
        previewRender: (plainText) => {
            let t = prksReplacePdfAnnotationWikiMarkers(plainText);
            t = prksReplaceWikiMarkersWithLinks(t, titleLowerToId);
            return prksSanitizeMarkdownPreviewHtml(easyMDE.markdown(t));
        },
    });

    window.workNotesEasyMDE = easyMDE;
    prksAttachWikiLinkAutocomplete(easyMDE.codemirror);

    const wrap = document.querySelector('.work-notes-editor-wrap');
    if (wrap) {
        wrap.addEventListener(
            'click',
            (e) => {
                const pdfA = e.target.closest && e.target.closest('a.wiki-link-pdf-ann');
                if (pdfA) {
                    e.preventDefault();
                    const annId = pdfA.getAttribute('data-pdf-ann-id');
                    if (annId && typeof window.prksJumpToPdfAnnotationFromNotes === 'function') {
                        void window.prksJumpToPdfAnnotationFromNotes(annId);
                    }
                    return;
                }
                const a = e.target.closest && e.target.closest('a.wiki-link-internal');
                if (!a || !a.getAttribute('href')) return;
                const href = a.getAttribute('href');
                if (href.startsWith('#/works/')) {
                    e.preventDefault();
                    window.location.hash = href.slice(1);
                }
            },
            true
        );
        wrap.addEventListener(
            'auxclick',
            (e) => {
                const a = e.target && e.target.closest ? e.target.closest('a.wiki-link-internal') : null;
                if (!a) return;
                const href = a.getAttribute('href') || '';
                if (!href.startsWith('#/works/')) return;
                if (typeof prksMaybeOpenHashInNewTab === 'function') {
                    prksMaybeOpenHashInNewTab(e, href);
                }
            },
            true
        );
    }

    const notesChangeHandler = () => {
        const content = easyMDE.value();
        const statusEl = document.getElementById('editor-status');
        if (statusEl) statusEl.innerText = "Drafting...";

        clearTimeout(window.saveNotesTimeout);
        window.saveNotesTimeout = setTimeout(async () => {
            try {
                const res = await fetch('/api/works/' + encodeURIComponent(work.id), {
                    method: 'PATCH',
                    body: JSON.stringify({ text_content: content })
                });
                if (statusEl) {
                    statusEl.innerText = res.ok ? 'All changes saved' : 'Error saving changes';
                }
            } catch (e) {
                if (statusEl) statusEl.innerText = "Error saving changes";
            }
        }, 2000);
    };
    easyMDE.codemirror.on("change", notesChangeHandler);
    easyMDE.__notesChangeHandler = notesChangeHandler;
}

function prksDestroyWorkNotesEditor() {
    if (!window.workNotesEasyMDE) return;
    try {
        const cm = window.workNotesEasyMDE.codemirror;
        const handler = window.workNotesEasyMDE.__notesChangeHandler;
        if (cm && handler) {
            cm.off("change", handler);
        }
    } catch (_e) {}
    try {
        if (typeof window.workNotesEasyMDE.toTextArea === 'function') {
            window.workNotesEasyMDE.toTextArea();
        }
    } catch (_e) {}
    window.workNotesEasyMDE = null;
}

window.prksDestroyWorkNotesEditor = prksDestroyWorkNotesEditor;

function prksWorkNotesMobileSideActive() {
    return document.documentElement.classList.contains('prks-work-notes-mobile-side');
}

function prksReapplyWorkNotesSplitLayout() {
    const ws = document.querySelector('.work-workspace[data-work-id]');
    if (!ws) return;
    const workId = ws.getAttribute('data-work-id');
    if (!workId) return;
    const handle = ws.querySelector('.work-split-handle');
    if (!handle) return;

    if (prksWorkNotesMobileSideActive()) {
        const storageKeyW = 'prks.workNotesSideWidth.' + workId;
        const savedW = localStorage.getItem(storageKeyW);
        let initialW = 280;
        if (savedW) {
            const n = parseInt(savedW, 10);
            if (!Number.isNaN(n) && n >= 160 && n <= 600) initialW = n;
        }
        const clampW = prksClampWorkNotesSideWidth(ws, handle, initialW);
        ws.style.setProperty('--work-notes-width', clampW + 'px');
    } else {
        const storageKeyH = 'prks.workNotesHeight.' + workId;
        const savedH = localStorage.getItem(storageKeyH);
        let initialH = 320;
        if (savedH) {
            const n = parseInt(savedH, 10);
            if (!Number.isNaN(n) && n >= 160 && n <= 900) initialH = n;
        }
        const clampH = prksClampWorkNotesHeight(ws, handle, initialH);
        ws.style.setProperty('--work-notes-height', clampH + 'px');
    }

    handle.setAttribute('aria-orientation', prksWorkNotesMobileSideActive() ? 'vertical' : 'horizontal');
    handle.setAttribute(
        'aria-label',
        prksWorkNotesMobileSideActive()
            ? 'Drag to resize research notes panel width'
            : 'Drag to resize research notes panel height'
    );

    requestAnimationFrame(() => {
        if (window.workNotesEasyMDE && window.workNotesEasyMDE.codemirror) {
            window.workNotesEasyMDE.codemirror.refresh();
        }
    });
}

window.prksReapplyWorkNotesSplitLayout = prksReapplyWorkNotesSplitLayout;

function prksClampWorkNotesHeight(ws, handle, px) {
    const rect = ws.getBoundingClientRect();
    const handleH = handle.offsetHeight || 11;
    const minPdf = 120;
    const minNotes = 160;
    const maxH = Math.max(minNotes, rect.height - minPdf - handleH);
    return Math.max(minNotes, Math.min(maxH, px));
}

function prksClampWorkNotesSideWidth(ws, handle, px) {
    const rect = ws.getBoundingClientRect();
    const handleW = handle.offsetWidth || 16;
    const minPdf = 120;
    const minNotes = 160;
    const maxW = Math.max(minNotes, rect.width - minPdf - handleW);
    return Math.max(minNotes, Math.min(maxW, px));
}

function setupWorkNotesSplitResize(workId) {
    const ws = document.querySelector('.work-workspace[data-work-id="' + workId + '"]');
    const handle = ws && ws.querySelector('.work-split-handle');
    const notesPane = ws && ws.querySelector('.work-notes-pane');
    if (!ws || !handle || !notesPane) return;

    const storageKeyH = 'prks.workNotesHeight.' + workId;
    const storageKeyW = 'prks.workNotesSideWidth.' + workId;

    const savedH = localStorage.getItem(storageKeyH);
    let initialH = 320;
    if (savedH) {
        const n = parseInt(savedH, 10);
        if (!Number.isNaN(n) && n >= 160 && n <= 900) initialH = n;
    }
    const savedW = localStorage.getItem(storageKeyW);
    let initialW = 280;
    if (savedW) {
        const n = parseInt(savedW, 10);
        if (!Number.isNaN(n) && n >= 160 && n <= 600) initialW = n;
    }

    if (prksWorkNotesMobileSideActive()) {
        ws.style.setProperty('--work-notes-width', prksClampWorkNotesSideWidth(ws, handle, initialW) + 'px');
    } else {
        ws.style.setProperty('--work-notes-height', prksClampWorkNotesHeight(ws, handle, initialH) + 'px');
    }
    handle.setAttribute('aria-orientation', prksWorkNotesMobileSideActive() ? 'vertical' : 'horizontal');
    handle.setAttribute(
        'aria-label',
        prksWorkNotesMobileSideActive()
            ? 'Drag to resize research notes panel width'
            : 'Drag to resize research notes panel height'
    );

    function refreshNotesEditor() {
        if (window.workNotesEasyMDE && window.workNotesEasyMDE.codemirror) {
            window.workNotesEasyMDE.codemirror.refresh();
        }
    }

    handle.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        const side = document.documentElement.classList.contains('prks-work-notes-mobile-side');
        if (side) {
            let dragging = true;
            const startX = e.clientX;
            const startNw = notesPane.getBoundingClientRect().width;
            handle.classList.add('dragging');
            try {
                handle.setPointerCapture(e.pointerId);
            } catch (_err) {}

            function endDrag(ev) {
                if (!dragging) return;
                dragging = false;
                handle.classList.remove('dragging');
                document.removeEventListener('pointermove', onMove, true);
                document.removeEventListener('pointerup', endDrag, true);
                document.removeEventListener('pointercancel', endDrag, true);
                handle.removeEventListener('lostpointercapture', onLost);
                if (ev && typeof ev.pointerId === 'number') {
                    try {
                        handle.releasePointerCapture(ev.pointerId);
                    } catch (_err2) {}
                }
                const raw = ws.style.getPropertyValue('--work-notes-width').trim();
                const w = parseInt(raw, 10);
                if (!Number.isNaN(w)) localStorage.setItem(storageKeyW, String(w));
                refreshNotesEditor();
            }

            function onMove(ev) {
                if (!dragging) return;
                ev.preventDefault();
                const delta = ev.clientX - startX;
                const next = prksClampWorkNotesSideWidth(ws, handle, startNw - delta);
                ws.style.setProperty('--work-notes-width', next + 'px');
                refreshNotesEditor();
            }

            function onLost() {
                endDrag({});
            }

            handle.addEventListener('lostpointercapture', onLost);
            document.addEventListener('pointermove', onMove, true);
            document.addEventListener('pointerup', endDrag, true);
            document.addEventListener('pointercancel', endDrag, true);
        } else {
            let dragging = true;
            const startY = e.clientY;
            const startNh = notesPane.getBoundingClientRect().height;
            handle.classList.add('dragging');
            try {
                handle.setPointerCapture(e.pointerId);
            } catch (_err) {}

            function onMove(ev) {
                if (!dragging) return;
                const delta = ev.clientY - startY;
                const next = prksClampWorkNotesHeight(ws, handle, startNh - delta);
                ws.style.setProperty('--work-notes-height', next + 'px');
                refreshNotesEditor();
            }

            function onUp(ev) {
                if (!dragging) return;
                dragging = false;
                handle.classList.remove('dragging');
                document.removeEventListener('pointermove', onMove);
                document.removeEventListener('pointerup', onUp);
                if (ev.pointerId !== undefined) {
                    try {
                        handle.releasePointerCapture(ev.pointerId);
                    } catch (_err2) {}
                }
                const raw = ws.style.getPropertyValue('--work-notes-height').trim();
                const h = parseInt(raw, 10);
                if (!Number.isNaN(h)) localStorage.setItem(storageKeyH, String(h));
                refreshNotesEditor();
            }

            document.addEventListener('pointermove', onMove);
            document.addEventListener('pointerup', onUp);
        }
    });

    handle.addEventListener('keydown', (e) => {
        const step = e.shiftKey ? 24 : 10;
        const side = document.documentElement.classList.contains('prks-work-notes-mobile-side');
        if (side) {
            const raw = ws.style.getPropertyValue('--work-notes-width').trim();
            const cur = parseInt(raw, 10) || initialW;
            if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
                e.preventDefault();
                const delta = e.key === 'ArrowLeft' ? step : -step;
                const next = prksClampWorkNotesSideWidth(ws, handle, cur + delta);
                ws.style.setProperty('--work-notes-width', next + 'px');
                localStorage.setItem(storageKeyW, String(next));
                refreshNotesEditor();
            }
        } else {
            const raw = ws.style.getPropertyValue('--work-notes-height').trim();
            const cur = parseInt(raw, 10) || initialH;
            if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
                e.preventDefault();
                const delta = e.key === 'ArrowUp' ? step : -step;
                const next = prksClampWorkNotesHeight(ws, handle, cur + delta);
                ws.style.setProperty('--work-notes-height', next + 'px');
                localStorage.setItem(storageKeyH, String(next));
                refreshNotesEditor();
            }
        }
    });

    if (!window.__prksWorkNotesWinResizeBound) {
        window.__prksWorkNotesWinResizeBound = true;
        window.addEventListener('resize', () => {
            const active = document.querySelector('.work-workspace[data-work-id]');
            if (!active) return;
            const hEl = active.querySelector('.work-split-handle');
            const nEl = active.querySelector('.work-notes-pane');
            if (!hEl || !nEl) return;

            if (document.documentElement.classList.contains('prks-work-notes-mobile-side')) {
                const raw = active.style.getPropertyValue('--work-notes-width').trim();
                const cur = parseInt(raw, 10);
                if (Number.isNaN(cur)) return;
                const clamped = prksClampWorkNotesSideWidth(active, hEl, cur);
                active.style.setProperty('--work-notes-width', clamped + 'px');
            } else {
                const raw = active.style.getPropertyValue('--work-notes-height').trim();
                const cur = parseInt(raw, 10);
                if (Number.isNaN(cur)) return;
                const clamped = prksClampWorkNotesHeight(active, hEl, cur);
                active.style.setProperty('--work-notes-height', clamped + 'px');
            }
            if (window.workNotesEasyMDE && window.workNotesEasyMDE.codemirror) {
                window.workNotesEasyMDE.codemirror.refresh();
            }
        });
    }

    requestAnimationFrame(() => {
        refreshNotesEditor();
        const activeNotesPane = ws.querySelector('.work-notes-pane');
        if (!activeNotesPane) return;
        const nRect = activeNotesPane.getBoundingClientRect();
        const isOutOfViewport = nRect.bottom > window.innerHeight || nRect.top < 0;
        const collapsed = ws.classList.contains('work-workspace--notes-collapsed');
        const isSmall =
            typeof prksIsSmallScreen === 'function' &&
            prksIsSmallScreen();
        if (collapsed && isOutOfViewport && isSmall) {
            ws.classList.remove('work-workspace--notes-collapsed');
            try {
                localStorage.setItem('prks.workNotesCollapsed.' + workId, '0');
            } catch (_e) {}
            requestAnimationFrame(() => refreshNotesEditor());
        }
    });
}

function setupWorkNotesCollapseToggle(workId) {
    const ws = document.querySelector('.work-workspace[data-work-id="' + workId + '"]');
    const btn = document.getElementById('work-notes-collapse-btn');
    if (!ws || !btn) return;

    const storageKey = 'prks.workNotesCollapsed.' + workId;

    function syncToggleUi() {
        const collapsed = ws.classList.contains('work-workspace--notes-collapsed');
        btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        const side = typeof prksWorkNotesMobileSideActive === 'function' && prksWorkNotesMobileSideActive();
        if (side) {
            btn.setAttribute(
                'aria-label',
                collapsed ? 'Expand research notes panel' : 'Collapse research notes panel'
            );
            btn.title = collapsed ? 'Expand notes' : 'Collapse notes';
        } else {
            btn.setAttribute(
                'aria-label',
                collapsed ? 'Expand research notes editor' : 'Collapse research notes editor'
            );
            btn.title = collapsed ? 'Expand notes' : 'Collapse notes';
        }
    }

    function setCollapsed(collapsed) {
        ws.classList.toggle('work-workspace--notes-collapsed', collapsed);
        localStorage.setItem(storageKey, collapsed ? '1' : '0');
        syncToggleUi();
        requestAnimationFrame(() => {
            if (window.workNotesEasyMDE && window.workNotesEasyMDE.codemirror) {
                window.workNotesEasyMDE.codemirror.refresh();
            }
        });
    }

    syncToggleUi();
    window.__prksWorkNotesCollapseSyncUi = syncToggleUi;
    btn.addEventListener('click', () => setCollapsed(!ws.classList.contains('work-workspace--notes-collapsed')));
}

let copyBibTeXResetTimer = null;

async function prksCopyBibTeXToClipboard(text) {
    const s = text == null ? '' : String(text);
    if (!s) return;
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        await navigator.clipboard.writeText(s);
        return 'navigator.clipboard';
    }
    const ta = document.createElement('textarea');
    ta.value = s;
    ta.setAttribute('readonly', 'readonly');
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    ta.style.top = '-9999px';
    document.body.appendChild(ta);
    let ok = false;
    try {
        ta.focus();
        ta.select();
        ok = !!document.execCommand('copy');
    } finally {
        document.body.removeChild(ta);
    }
    if (!ok) throw new Error('document.execCommand copy failed');
    return 'execCommand';
}

async function copyBibTeX(workId, btn) {
    if (!btn) return;
    const snapshotIfNeeded = () => {
        if (btn.dataset.copyBibtexOriginal == null) {
            btn.dataset.copyBibtexOriginal = btn.innerHTML;
            btn.dataset.copyBibtexStyle = btn.getAttribute('style') || '';
        }
    };
    const restore = () => {
        if (btn.dataset.copyBibtexOriginal != null) {
            btn.innerHTML = btn.dataset.copyBibtexOriginal;
            delete btn.dataset.copyBibtexOriginal;
        }
        if (btn.dataset.copyBibtexStyle != null) {
            btn.setAttribute('style', btn.dataset.copyBibtexStyle);
            delete btn.dataset.copyBibtexStyle;
        }
    };
    if (copyBibTeXResetTimer) {
        clearTimeout(copyBibTeXResetTimer);
        copyBibTeXResetTimer = null;
        restore();
    }
    try {
        const res = await fetch('/api/bibtex/' + encodeURIComponent(workId));
        if (!res.ok) throw new Error('bibtex fetch failed');
        const text = await res.text();
        await prksCopyBibTeXToClipboard(text);
        snapshotIfNeeded();
        btn.innerHTML = '✓ BibTex copied!';
        btn.style.color = '#16a34a';
        btn.style.borderColor = '#16a34a';
        copyBibTeXResetTimer = setTimeout(() => {
            restore();
            copyBibTeXResetTimer = null;
        }, 2500);
    } catch (e) {
        snapshotIfNeeded();
        btn.innerHTML = '✗ Copy failed';
        btn.style.color = '#ef4444';
        btn.style.borderColor = '#ef4444';
        copyBibTeXResetTimer = setTimeout(() => {
            restore();
            copyBibTeXResetTimer = null;
        }, 2500);
    }
}

/** Wire Copy BibTeX / Delete File in #panel-content (right column Details tab). */
function initWorkDetailRightPanelActions(work) {
    const panel = document.getElementById('panel-content');
    if (!panel || !work || !work.id) return;
    const copyBtn = panel.querySelector('.copy-bibtex-btn');
    if (copyBtn) {
        copyBtn.addEventListener('click', () => void copyBibTeX(work.id, copyBtn));
    }
    const delBtn = panel.querySelector('.delete-work-btn');
    if (delBtn) {
        delBtn.addEventListener('click', () => void deleteWork(work.id));
    }
}
