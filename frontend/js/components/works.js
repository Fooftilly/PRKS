/**
 * Core work detail UI: research notes, wiki links, BibTeX, metadata.
 * PDF / EmbedPDF integration is in works-pdf.js (loaded via dynamic import when work.file_path is set).
 */

function prksEscapeHtmlLite(s) {
    if (typeof window.prksEscapeHtml === 'function') return window.prksEscapeHtml(s);
    if (s == null || s === '') return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
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
const PRKS_RESEARCH_DRAFT_MAX_COMMITTED = 64;
const prksWorkResearchDrafts = new Map();

function prksResearchDraftEntry(workId, text) {
    const id = String(workId || '');
    if (!id) return null;
    let entry = prksWorkResearchDrafts.get(id);
    if (!entry) {
        entry = {
            workId: id,
            text: String(text == null ? '' : text),
            editGeneration: 0,
            saveSequence: 0,
            latestSaveToken: 0,
            latestSaveEditGeneration: 0,
            settledSaveToken: 0,
            state: 'committed',
            saveError: false,
            promise: null,
            updatedAt: Date.now(),
        };
        prksWorkResearchDrafts.set(id, entry);
    }
    return entry;
}

function prksPruneResearchDrafts() {
    const committed = Array.from(prksWorkResearchDrafts.values())
        .filter((entry) => entry.state === 'committed' && !entry.promise)
        .sort((a, b) => a.updatedAt - b.updatedAt);
    while (committed.length > PRKS_RESEARCH_DRAFT_MAX_COMMITTED) {
        const old = committed.shift();
        if (old) prksWorkResearchDrafts.delete(old.workId);
    }
}

function prksResearchNotesTextForWork(workId, serverText) {
    const id = String(workId || '');
    const server = String(serverText == null ? '' : serverText);
    const entry = id ? prksWorkResearchDrafts.get(id) : null;
    if (!entry) return server;
    if (entry.state === 'committed' && !entry.promise && entry.text === server) {
        prksWorkResearchDrafts.delete(id);
        return server;
    }
    return entry.text;
}

function prksSyncResearchNotesState(notes, entry) {
    if (!notes || !entry) return;
    notes.editGeneration = entry.editGeneration;
    notes.saveSequence = entry.saveSequence;
    notes.latestSaveToken = entry.latestSaveToken;
    notes.latestSaveEditGeneration = entry.latestSaveEditGeneration;
    notes.settledSaveToken = entry.settledSaveToken;
    notes.pendingSave = entry.latestSaveToken > entry.settledSaveToken;
    notes.drafting = entry.state === 'drafting';
    notes.saveError = entry.state === 'error';
}

function prksSyncLiveResearchDraft(workId, entry) {
    if (!entry || typeof prksForEachLiveTabContext !== 'function') return;
    prksForEachLiveTabContext(function (ctx) {
        const work = ctx && ctx.getEntity ? ctx.getEntity('work') : null;
        if (!work || String(work.id) !== String(workId)) return;
        const notes = ctx.getResource ? ctx.getResource('workNotes') : null;
        prksSyncResearchNotesState(notes, entry);
        const status = ctx.query ? ctx.query('[data-prks-role="editor-status"]') : null;
        if (status) {
            status.innerText = entry.state === 'error'
                ? 'Error saving changes'
                : entry.state === 'saving'
                  ? 'Saving...'
                  : entry.state === 'drafting'
                    ? 'Drafting...'
                    : 'All changes saved';
        }
        if (ctx.tabId && typeof window.prksWorkspaceRefreshTabStatus === 'function') {
            window.prksWorkspaceRefreshTabStatus(ctx.tabId);
        }
    });
}

window.prksResearchNotesTextForWork = prksResearchNotesTextForWork;
window.prksResetResearchDraftsForTest = function () {
    prksWorkResearchDrafts.clear();
};

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
    const query = m[1] || '';
    if (/^(pdf:|concept:|argument:)/i.test(query)) return null;
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
    if (!prksWorkNotesMutationAllowed(prksHintOwnerCtx(cm), cm)) {
        if (typeof prksOfflineGuardMutation === 'function') prksOfflineGuardMutation();
        return;
    }
    const from = completion.from != null ? completion.from : data.from;
    const to = completion.to != null ? completion.to : data.to;
    const title = typeof completion.text === 'string' ? completion.text : '';
    cm.replaceRange(title + ']]', from, to, 'complete');
}

function prksHintOwnerCtx(cm) {
    if (cm && cm.__prksOwnerCtx) return cm.__prksOwnerCtx;
    return typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
}

function prksHintResource(cm, name) {
    const owner = prksHintOwnerCtx(cm);
    return owner && typeof owner.getResource === 'function' ? owner.getResource(name) : undefined;
}

function prksWikiLinkHint(cm) {
    const ctx = prksGetWikiLinkAutocompleteContext(cm);
    if (!ctx) return null;
    const rows = prksHintResource(cm, 'wikiWorkList') || [];
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

function prksAttachWikiLinkAutocomplete(cm, ownerCtx) {
    if (cm && ownerCtx) cm.__prksOwnerCtx = ownerCtx;
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
            if (prksGetConceptLinkAutocompleteContext(editor)) {
                CM.showHint(editor, prksConceptLinkHint, { completeSingle: false });
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
        if (prksGetConceptLinkAutocompleteContext(editor)) {
            CM.showHint(editor, prksConceptLinkHint, { completeSingle: false });
            return;
        }
        if (prksGetWikiLinkAutocompleteContext(editor)) {
            CM.showHint(editor, prksWikiLinkHint, { completeSingle: false });
        }
    };
    cm.setOption(
        'extraKeys',
        Object.assign({}, keys, {
            // Manual trigger removed (Ctrl-Space conflicts in many browsers / IME).
            // Keep autocomplete via typing `[[` and `[[pdf:` only.
        })
    );
}

/** [[target|label]] / [[target]] → internal links; matches graph wiki resolution (first id per lowercase title). */
function prksReplaceWikiMarkersWithLinks(plainText, titleLowerToId) {
    const map = titleLowerToId || {};
    return plainText.replace(/\[\[([^\]]+)\]\]/g, (full, inner) => {
        const trimmed = String(inner).trim();
        if (/^(concept:|argument:|pdf:)/i.test(trimmed)) return full;
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
    if (!prksWorkNotesMutationAllowed(prksHintOwnerCtx(cm), cm)) {
        if (typeof prksOfflineGuardMutation === 'function') prksOfflineGuardMutation();
        return;
    }
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
    const rows = typeof window.prksGetPdfAnnotationHintList === 'function' ? window.prksGetPdfAnnotationHintList(prksHintOwnerCtx(cm)) : [];
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

function prksGetConceptLinkAutocompleteContext(cm) {
    const cur = cm.getCursor();
    const lineText = cm.getLine(cur.line);
    const before = lineText.slice(0, cur.ch);
    const m = before.match(/\[\[concept:([^\]|]*)$/);
    if (!m) return null;
    const startCh = before.lastIndexOf('[[concept:') + '[[concept:'.length;
    const CM = cm.constructor;
    return { from: CM.Pos(cur.line, startCh), to: cur, query: m[1] || '' };
}

function prksConceptHintRows(cm) {
    const rows = prksHintResource(cm, 'conceptHintList') || [];
    const out = [];
    for (let i = 0; i < rows.length; i++) {
        const c = rows[i];
        if (!c || !c.name) continue;
        out.push({ name: String(c.name), haystack: String(c.name) });
        const aliases = c.aliases || [];
        for (let j = 0; j < aliases.length; j++) {
            const a = String(aliases[j] || '').trim();
            if (a) out.push({ name: String(c.name), haystack: a });
        }
    }
    return out;
}

function prksFilterConceptsForHint(query, cm) {
    const ql = query.trim().toLowerCase();
    const rows = prksConceptHintRows(cm);
    if (!ql) {
        const seen = {};
        const uniq = [];
        for (let i = 0; i < rows.length; i++) {
            if (seen[rows[i].name]) continue;
            seen[rows[i].name] = true;
            uniq.push(rows[i]);
            if (uniq.length >= PRKS_WIKI_HINT_MAX) break;
        }
        return uniq;
    }
    const pref = [];
    const sub = [];
    const seen = {};
    for (let i = 0; i < rows.length; i++) {
        const w = rows[i];
        const hl = w.haystack.toLowerCase();
        const nl = w.name.toLowerCase();
        if (seen[w.name]) continue;
        if (hl.startsWith(ql) || nl.startsWith(ql)) {
            seen[w.name] = true;
            pref.push(w);
        } else if (hl.includes(ql) || nl.includes(ql)) {
            seen[w.name] = true;
            sub.push(w);
        }
        if (pref.length >= PRKS_WIKI_HINT_MAX) break;
    }
    if (pref.length >= PRKS_WIKI_HINT_MAX) return pref.slice(0, PRKS_WIKI_HINT_MAX);
    return pref.concat(sub.slice(0, PRKS_WIKI_HINT_MAX - pref.length));
}

function prksConceptLinkCompletionPick(cm, data, completion) {
    if (!prksWorkNotesMutationAllowed(prksHintOwnerCtx(cm), cm)) {
        if (typeof prksOfflineGuardMutation === 'function') prksOfflineGuardMutation();
        return;
    }
    const from = completion.from != null ? completion.from : data.from;
    const to = completion.to != null ? completion.to : data.to;
    const name = typeof completion.text === 'string' ? completion.text : '';
    cm.replaceRange(name + ']]', from, to, 'complete');
}

function prksConceptLinkHint(cm) {
    const ctx = prksGetConceptLinkAutocompleteContext(cm);
    if (!ctx) return null;
    const matches = prksFilterConceptsForHint(ctx.query, cm);
    if (matches.length === 0) return null;
    return {
        from: ctx.from,
        to: ctx.to,
        list: matches.map((w) => ({
            text: w.name,
            displayText: w.haystack === w.name ? w.name : w.name + ' (' + w.haystack + ')',
            hint: prksConceptLinkCompletionPick,
        })),
    };
}

/**
 * CodeMirror's own `readOnly` option is not enough: PRKS's toolbar-driven
 * insert paths (Concept picker, Argument picker, wiki-link/PDF-annotation/
 * Concept autocomplete picks) call this directly and none of them consult
 * CodeMirror state first. This is the one predicate every PRKS-owned
 * programmatic Research Notes edit boundary must check before mutating
 * anything. It requires:
 *   - current PRKS connectivity == online
 *   - `ctx` (the resolved owner TabContext) is still live/current -- it may
 *     be stale (route navigated away) even while PRKS itself is online
 *   - a live `workNotes` resource on that ctx
 *   - when a CodeMirror instance `cm` is supplied (every real caller has
 *     one), it is the *exact* live instance still installed at
 *     notes.editor.codemirror -- never a detached CodeMirror from a picker/
 *     autocomplete callback that outlived a navigation to a different Work
 */
function prksWorkNotesMutationAllowed(ctx, cm) {
    const owner = ctx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    if (!owner) return false;
    if (
        typeof owner.isCurrent === 'function' &&
        typeof owner.generation === 'number' &&
        !owner.isCurrent(owner.generation)
    ) {
        return false;
    }
    const notes = typeof owner.getResource === 'function' ? owner.getResource('workNotes') : null;
    if (!notes || !notes.editor) return false;
    if (cm && notes.editor.codemirror !== cm) return false;
    if (typeof prksOfflineRuntimeState === 'function' && prksOfflineRuntimeState() !== 'online') return false;
    return true;
}

window.prksWorkNotesMutationAllowed = prksWorkNotesMutationAllowed;

function prksInsertNotesMarkup(cm, markup) {
    if (!cm || !markup) return;
    if (!prksWorkNotesMutationAllowed(prksHintOwnerCtx(cm), cm)) {
        if (typeof prksOfflineGuardMutation === 'function') prksOfflineGuardMutation();
        return;
    }
    const cur = cm.getCursor();
    cm.replaceRange(markup, cur, cur, 'complete');
    cm.focus();
}

function prksCurrentPdfPagesForWork(workId, ctx) {
    const owner = ctx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    const pdf = owner && owner.getResource ? owner.getResource('pdf') : null;
    const sess = pdf && pdf.pageSession;
    if (!sess || (workId && sess.workId !== workId)) return '';
    const n = sess.pageNumber;
    if (!Number.isFinite(n) || n < 1) return '';
    return String(Math.floor(n));
}

function prksCloseResearchPicker() {
    const el = document.getElementById('prks-research-picker');
    if (el && el.parentNode) el.parentNode.removeChild(el);
}

function prksOpenResearchPicker(opts) {
    prksCloseResearchPicker();
    const d = document;
    const overlay = d.createElement('div');
    overlay.id = 'prks-research-picker';
    overlay.className = 'prks-research-picker';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-labelledby', 'prks-research-picker-title');
    overlay.tabIndex = -1;
    const extra = opts.extraHtml
        ? '<div class="prks-research-picker__actions-start">' + opts.extraHtml + '</div>'
        : '';
    overlay.innerHTML =
        '<div class="prks-dialog prks-research-picker__dialog">' +
        '<div class="prks-dialog__header">' +
        '<h2 class="prks-dialog__title" id="prks-research-picker-title">' +
        prksEscapeHtmlLite(opts.title || '') +
        '</h2>' +
        '<button type="button" class="prks-icon-btn prks-research-picker__close" aria-label="Close">&times;</button>' +
        '</div>' +
        '<div class="prks-dialog__body">' +
        '<input type="search" class="prks-input prks-research-picker__q" placeholder="Search…" autocomplete="off">' +
        '<div class="prks-research-picker__list" role="listbox"></div>' +
        '</div>' +
        '<div class="prks-dialog__actions prks-research-picker__actions">' +
        extra +
        '<button type="button" class="prks-btn prks-btn--secondary prks-research-picker__close">Close</button>' +
        '</div>' +
        '</div>';
    d.body.appendChild(overlay);
    const q = overlay.querySelector('.prks-research-picker__q');
    const list = overlay.querySelector('.prks-research-picker__list');
    function render() {
        const query = (q.value || '').trim().toLowerCase();
        const rows = opts.items() || [];
        const filtered = [];
        for (let i = 0; i < rows.length; i++) {
            const r = rows[i];
            if (!query) {
                filtered.push(r);
            } else {
                const hay = (r.haystack || r.label || '').toLowerCase();
                if (hay.indexOf(query) >= 0) filtered.push(r);
            }
            if (filtered.length >= 40) break;
        }
        let html = filtered
            .map(function (r) {
                const kind = r.kind
                    ? '<span class="prks-research-row__kicker">' +
                      prksEscapeHtmlLite(r.kind) +
                      '</span>'
                    : '';
                return (
                    '<button type="button" class="prks-list-row prks-research-row prks-research-picker__item" data-id="' +
                    prksEscapeAttr(r.id) +
                    '"' +
                    (r.pickType
                        ? ' data-pick-type="' + prksEscapeAttr(r.pickType) + '"'
                        : '') +
                    '><span class="prks-research-row__body">' +
                    kind +
                    '<span class="prks-research-row__title">' +
                    prksEscapeHtmlLite(r.label) +
                    '</span></span></button>'
                );
            })
            .join('');
        const typed = (q.value || '').trim();
        if (typed && typeof opts.createItems === 'function') {
            html += (opts.createItems(typed) || [])
                .map(function (row) {
                    return (
                        '<button type="button" class="prks-list-row prks-research-row prks-research-picker__item prks-research-picker__create" data-create="' +
                        prksEscapeAttr(row.kind || '1') +
                        '">' +
                        prksEscapeHtmlLite(row.label || '') +
                        '</button>'
                    );
                })
                .join('');
        } else if (opts.createLabel && typed) {
            html +=
                '<button type="button" class="prks-list-row prks-research-row prks-research-picker__item prks-research-picker__create" data-create="1">' +
                prksEscapeHtmlLite(opts.createLabel(typed)) +
                '</button>';
        }
        if (!html) html = '<p class="meta-row">No matches.</p>';
        list.innerHTML = html;
    }
    function pick(id, createName, createKind, pickType) {
        prksCloseResearchPicker();
        if (createName && typeof opts.onCreate === 'function') opts.onCreate(createName, createKind);
        else if (id && typeof opts.onPick === 'function') opts.onPick(id, pickType);
    }
    list.addEventListener('click', function (e) {
        const btn = e.target.closest && e.target.closest('button[data-id], button[data-create]');
        if (!btn) return;
        const createKind = btn.getAttribute('data-create');
        if (createKind) pick('', (q.value || '').trim(), createKind);
        else pick(btn.getAttribute('data-id'), '', '', btn.getAttribute('data-pick-type'));
    });
    overlay.querySelectorAll('.prks-research-picker__close').forEach(function (btn) {
        btn.addEventListener('click', prksCloseResearchPicker);
    });
    overlay.addEventListener('click', function (e) {
        if (e.target === overlay) prksCloseResearchPicker();
    });
    overlay.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            e.preventDefault();
            prksCloseResearchPicker();
        }
    });
    q.addEventListener('input', render);
    q.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            e.preventDefault();
            prksCloseResearchPicker();
        }
        if (e.key === 'Enter') {
            e.preventDefault();
            const first = list.querySelector('button[data-id], button[data-create]');
            if (first) first.click();
        }
    });
    render();
    q.focus();
}

function prksOpenConceptPicker(cm) {
    const items = function () {
        return (prksHintResource(cm, 'conceptHintList') || []).map(function (c) {
            const aliases = (c.aliases || []).join(' ');
            return {
                id: c.id,
                label: c.name,
                haystack: (c.name || '') + ' ' + aliases,
            };
        });
    };
    prksOpenResearchPicker({
        title: 'Insert Concept',
        items: items,
        createLabel: function (name) {
            return 'Create “' + name + '”';
        },
        onPick: function (id) {
            const rows = prksHintResource(cm, 'conceptHintList') || [];
            let name = '';
            for (let i = 0; i < rows.length; i++) {
                if (rows[i].id === id) {
                    name = rows[i].name;
                    break;
                }
            }
            if (name) prksInsertNotesMarkup(cm, '[[concept:' + name + ']]');
        },
        onCreate: function (name) {
            prksInsertNotesMarkup(cm, '[[concept:' + name + ']]');
        },
    });
}

function prksOpenArgumentPicker(cm, work) {
    const items = function () {
        return (prksHintResource(cm, 'argumentHintList') || []).map(function (a) {
            return {
                id: a.id,
                label: a.name || a.id,
                kind: a.kind === 'stance' ? 'Stance' : 'Argument',
                haystack: (a.name || '') + ' ' + (a.id || '') + ' ' + (a.kind || ''),
            };
        });
    };
    function insertCreatedArgument(kind, name) {
        if (!prksWorkNotesMutationAllowed(prksHintOwnerCtx(cm), cm)) {
            if (typeof prksOfflineGuardMutation === 'function') prksOfflineGuardMutation();
            return;
        }
        void (async function () {
            if (typeof window.prksCreateArgumentFromWork !== 'function') return;
            const created = await window.prksCreateArgumentFromWork({
                kind: kind,
                name: name || '',
                workId: work && work.id,
                pages: prksCurrentPdfPagesForWork(work && work.id, prksHintOwnerCtx(cm)),
            });
            if (created && created.id) {
                prksInsertNotesMarkup(
                    cm,
                    '[[argument:' + created.id + '|' + (created.name || created.id) + ']]'
                );
                if (typeof fetchArguments === 'function') {
                    const list = await fetchArguments();
                    const owner = prksHintOwnerCtx(cm);
                    if (owner && typeof owner.setResource === 'function') owner.setResource('argumentHintList', list);
                }
            }
        })();
    }
    prksOpenResearchPicker({
        title: 'Insert Argument / Stance',
        items: items,
        createItems: function (name) {
            return [
                { kind: 'argument', label: 'Create Argument “' + name + '”' },
                { kind: 'stance', label: 'Create Stance “' + name + '”' },
            ];
        },
        onPick: function (id) {
            const rows = prksHintResource(cm, 'argumentHintList') || [];
            let name = id;
            for (let i = 0; i < rows.length; i++) {
                if (rows[i].id === id) {
                    name = rows[i].name || id;
                    break;
                }
            }
            prksInsertNotesMarkup(cm, '[[argument:' + id + '|' + name + ']]');
        },
        onCreate: function (name, kind) {
            insertCreatedArgument(kind === 'stance' ? 'stance' : 'argument', name);
        },
    });
}

async function deleteWork(w_id, ownerCtx) {
    if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) return;
    const ctx = ownerCtx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    const generation = ctx && ctx.generation;
    const confirmed = await prksConfirmDestructive({
        title: 'Delete file?',
        message: 'Are you sure you want to delete this file?',
        confirmLabel: 'Delete file',
    });
    if (!confirmed) return;
    if (
        typeof prksTabContextOwnsEntityRoute === 'function' &&
        !prksTabContextOwnsEntityRoute(ctx, generation, 'work', w_id, 'work')
    ) return;
    try {
        const res = await prksRequest('/api/works/' + encodeURIComponent(w_id), { method: 'DELETE' });
        if (!res.ok) {
            if (ctx && ctx.isCurrent && ctx.isCurrent(generation)) {
                await prksAlertMessage('Error deleting file!', 'Error');
            }
            return;
        }
        if (typeof prksOfflineMarkEntityChanged === 'function') {
            // DELETE is canonical only after this acknowledged success.
            prksOfflineMarkEntityChanged('work', w_id);
        }
        if (typeof prksOfflineMarkConceptsChanged === 'function') {
            // Deleting a Work removes its Concept mentions from canonical
            // research data, so cached Concept details/counts are now stale.
            prksOfflineMarkConceptsChanged();
        }
        window.__prksRecentlyAddedDirty = true;
        if (
            typeof prksTabContextOwnsEntityRoute === 'function' &&
            prksTabContextOwnsEntityRoute(ctx, generation, 'work', w_id, 'work') &&
            typeof prksNavigate === 'function'
        ) {
            prksNavigate('#/folders', { replace: true, tabId: ctx.tabId });
        }
    } catch (_e) {
        if (ctx && ctx.isCurrent && ctx.isCurrent(generation)) {
            await prksAlertMessage('Error deleting file!', 'Error');
        }
    }
}

async function renderWorkDetails(ctx, work, requestCtx) {
    const generation = requestCtx && requestCtx.generation;
    const routeSignal = requestCtx && requestCtx.signal;
    const container = ctx && ctx.root ? ctx.root : null;
    if (!container) return;
    if (!ctx || typeof ctx.isCurrent !== 'function') return;

    const isCurrent = function () {
        return ctx.isCurrent(generation);
    };

    if (!work) {
        container.innerHTML = '<p class="prks-inline-message prks-inline-message--error">File not found.</p>';
        return;
    }
    work.text_content = prksResearchNotesTextForWork(work.id, work.text_content);
    let pdfModule = null;
    const inferredKind =
        typeof prksInferWorkSourceKind === 'function' ? prksInferWorkSourceKind(work) : '';

    if (inferredKind === 'pdf' && work.file_path) {
        pdfModule = await import('/js/components/works-pdf.js');
    }
    if (!isCurrent()) return;
    let videoModule = null;
    if (inferredKind === 'video') {
        videoModule = await import('/js/components/works-video.js');
    }
    if (!isCurrent()) return;

    let authorsStr = '';
    if (work.roles) {
        const authorsList = [];
        work.roles.forEach((r) => {
            if (r.role_type === 'Author') authorsList.push(r);
            else {
                const display =
                    typeof prksRoleDisplayName === 'function'
                        ? prksRoleDisplayName(r)
                        : `${r.first_name || ''} ${r.last_name || ''}`.trim();
                const nm = `${prksEscapeHtmlLite(display)} (${prksEscapeHtmlLite(r.role_type)})`;
                authorsStr += `<span class="tag prks-person-chip" data-person-id="${prksEscapeAttr(String(r.id || ''))}" data-prks-route="#/people/${encodeURIComponent(String(r.id || ''))}">${nm}</span>`;
            }
        });
        if (authorsList.length > 0) {
            const authorChips = authorsList
                .map((a) => {
                    const display =
                        typeof prksRoleDisplayName === 'function'
                            ? prksRoleDisplayName(a)
                            : `${a.first_name || ''} ${a.last_name || ''}`.trim();
                    return `<span class="tag author-tag prks-person-chip" data-person-id="${prksEscapeAttr(String(a.id || ''))}" data-prks-route="#/people/${encodeURIComponent(String(a.id || ''))}">${typeof prksIcon === 'function' ? prksIcon('user', { size: 'sm' }) : ''} ${prksEscapeHtmlLite(display)}</span>`;
                })
                .join(' ');
            authorsStr = authorChips + authorsStr;
        }
    }

    let leftPane = '';
    if (inferredKind === 'pdf') {
        leftPane = work.file_path
            ? `<div class="work-pdf-pane"><div data-prks-role="pdf-viewer"></div></div>`
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
                            <button type="button" class="work-notes-toggle-btn" data-prks-role="work-notes-collapse-btn" aria-expanded="true" aria-controls="${ctx.domId('work-notes-editor-region')}" aria-label="Collapse research notes editor" title="Collapse notes"><span class="work-notes-toggle-btn__icon" aria-hidden="true"><svg class="work-notes-toggle-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2.65" stroke-linecap="round" stroke-linejoin="round"><polyline points="6.5 13 12 19 17.5 13"/><polyline points="6.5 6 12 12 17.5 6"/></svg></span></button>
                            <div data-prks-role="annotation-sync-status" class="work-annotation-sync-status work-annotation-sync-status--hidden" aria-live="polite"></div>
                            <div data-prks-role="editor-status" class="work-editor-status"></div>
                        </div>
                    </div>
                    <div class="work-notes-editor-wrap" data-prks-role="work-notes-editor-region" id="${ctx.domId('work-notes-editor-region')}">
                        <textarea data-prks-role="research-notes-editor"></textarea>
                    </div>
                </div>
            </div>
        </div>
    `;

    if (typeof ctx.setEntity === 'function') ctx.setEntity('work', work);
    const workTitle = String((work && work.title) || '').trim();
    const headerTitle = workTitle ? prksEscapeHtmlLite(workTitle) : 'Document';
    const pdfViewerActive = inferredKind === 'pdf' && !!work.file_path;
    const workHeader = pdfViewerActive
        ? ''
        : `
            <div class="prks-page-header page-header page-header--work">
                <div class="card-heading-row card-heading-row--wrap">
                    <h2 class="page-header--work-title">${headerTitle}</h2>
                    <span data-prks-role="work-header-doc-type-slot">${typeof prksDocTypeBadgeHtml === 'function' ? prksDocTypeBadgeHtml(work.doc_type) : ''}</span>
                </div>
            </div>
        `;

    container.innerHTML = `
        <div class="work-detail">
            ${workHeader}
            <div class="document-view document-view--work">
                ${mainContent}
            </div>
        </div>
    `;

    const notesTa = ctx.query('[data-prks-role="research-notes-editor"]');
    if (notesTa) notesTa.value = work.text_content || '';
    container.querySelectorAll('.prks-person-chip').forEach((el) => {
        el.style.cursor = 'pointer';
    });

    // Populate Right Panel only when this context is focused.
    if (typeof prksTabContextIsFocused === 'function' ? prksTabContextIsFocused(ctx) : true) {
        const panelTab = (ctx.ui && ctx.ui.rightPanelTab) || 'details';
        updatePanelContent(panelTab);
        if (typeof prksSyncRightPanelTabStrip === 'function') prksSyncRightPanelTabStrip(panelTab);

        const editBtn = document.getElementById('edit-metadata-btn');
        if (editBtn) {
            editBtn.onclick = () => toggleWorkMetaEdit(true);
        }
    }

    if (work.file_path && pdfModule && isCurrent()) {
        pdfModule.initPdfViewerForWork(ctx, work);
    }

    const setupTimer = setTimeout(async () => {
        if (ctx && ctx.timers && ctx.timers.get('workDeferredSetup') === setupTimer) {
            ctx.clearTimer('workDeferredSetup');
        }
        if (!isCurrent()) return;
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
            const works = await fetchWorks({ signal: routeSignal });
            if (!isCurrent()) return;
            if (typeof ctx.setResource === 'function') {
                ctx.setResource('wikiTitleMap', prksBuildWorkTitleLowerToIdMap(works));
                ctx.setResource('wikiWorkList', prksBuildWikiAutocompleteWorkList(works));
            }
        } catch (_e) {
            if (!isCurrent()) return;
            if (typeof ctx.setResource === 'function') {
                ctx.setResource('wikiTitleMap', {});
                ctx.setResource('wikiWorkList', []);
            }
        }
        try {
            if (typeof fetchConcepts === 'function') {
                const concepts = await fetchConcepts({ signal: routeSignal });
                if (!isCurrent()) return;
                if (typeof ctx.setResource === 'function') ctx.setResource('conceptHintList', concepts);
            }
        } catch (_e) {
            if (!isCurrent()) return;
            if (typeof ctx.setResource === 'function') ctx.setResource('conceptHintList', []);
        }
        try {
            if (typeof fetchArguments === 'function') {
                const argumentsList = await fetchArguments(undefined, { signal: routeSignal });
                if (!isCurrent()) return;
                if (typeof ctx.setResource === 'function') ctx.setResource('argumentHintList', argumentsList);
            }
        } catch (_e) {
            if (!isCurrent()) return;
            if (typeof ctx.setResource === 'function') ctx.setResource('argumentHintList', []);
        }
        if (!isCurrent()) return;
        initEasyMDE(ctx, work);
        setupWorkNotesSplitResize(ctx, work.id);
        setupWorkNotesCollapseToggle(ctx, work.id);
    }, 200);
    if (ctx && typeof ctx.setTimer === 'function') ctx.setTimer('workDeferredSetup', setupTimer);

    prksRequest(
        '/api/works/' + encodeURIComponent(work.id) + '/related_folders',
        { signal: routeSignal },
        { priority: 'background' }
    )
        .then((r) => (r.ok ? r.json() : []))
        .then((related) => {
            if (!isCurrent()) return;
            const target = ctx.query ? ctx.query('[data-prks-role="related-folders"]') : null;
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
                span.setAttribute('data-prks-route', '#/folders/' + encodeURIComponent(fid));
                target.appendChild(span);
            }
        })
        .catch((err) => {
            if (typeof prksIsAbortError === 'function' && prksIsAbortError(err)) return;
            console.error('related folders fetch failed', err);
        });
}

/** EasyMDE toolbar uses Font Awesome class names; PRKS does not load that webfont. Map buttons to Lucide. */
const PRKS_EASYMDE_TOOLBAR_ICONS = {
    bold: 'bold',
    italic: 'italic',
    heading: 'heading',
    quote: 'quote',
    'unordered-list': 'list',
    'ordered-list': 'list-ordered',
    link: 'link',
    image: 'image',
    'prks-insert-concept': 'lightbulb',
    'prks-insert-argument': 'message-square',
    preview: 'eye',
    'side-by-side': 'columns-2',
    fullscreen: 'maximize-2',
    'prks-notes-help': 'circle-help',
};

function prksPaintEasyMDEToolbarIcons(toolbar) {
    if (!toolbar) return;
    toolbar.querySelectorAll('button').forEach((btn) => {
        const el = btn.querySelector('i:not(.separator)');
        if (!el || el.getAttribute('data-lucide') || el.querySelector('svg, [data-lucide]')) return;
        let lucideName = '';
        btn.classList.forEach((cls) => {
            if (!lucideName && PRKS_EASYMDE_TOOLBAR_ICONS[cls]) {
                lucideName = PRKS_EASYMDE_TOOLBAR_ICONS[cls];
            }
        });
        if (!lucideName) return;
        el.setAttribute('data-lucide', lucideName);
        el.classList.add('prks-icon', 'prks-icon--sm');
    });
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(toolbar);
}

function initEasyMDE(ctx, work) {
    const titleLowerToId = (ctx && ctx.getResource ? ctx.getResource('wikiTitleMap') : null) || {};
    const prksNotesHelpHtml = `
<div class="prks-help-section">
  <div class="prks-help-title">Work links</div>
  <ul>
    <li><code>[[Work Title]]</code> open work</li>
    <li><code>[[Work Title|Label]]</code> open work with label</li>
  </ul>
</div>
<div class="prks-help-section">
  <div class="prks-help-title">Concepts</div>
  <ul>
    <li><code>[[concept:Culture Industry]]</code> Concept link (created on save if new)</li>
    <li>Type <code>[[concept:</code> for name/alias suggestions</li>
  </ul>
</div>
<div class="prks-help-section">
  <div class="prks-help-title">Arguments / Stances</div>
  <ul>
    <li><code>[[argument:A-123|Label]]</code> stable Argument link</li>
    <li>Unknown Argument IDs stay unresolved; they are not created</li>
  </ul>
</div>
<div class="prks-help-section">
  <div class="prks-help-title">PDF annotation links</div>
  <ul>
    <li><code>[[pdf:&lt;annotationId&gt;]]</code> jump inside PDF viewer</li>
    <li><code>[[pdf:&lt;annotationId&gt;|Label]]</code> jump with label</li>
  </ul>
</div>
<div class="prks-help-section">
  <div class="prks-help-title">Autocomplete</div>
  <ul>
    <li>Type <code>[[</code> then work suggestions</li>
    <li>Type <code>[[concept:</code> then Concept suggestions</li>
    <li>Type <code>[[pdf:</code> then annotation suggestions</li>
  </ul>
</div>
<div class="prks-help-section">
  <div class="prks-help-title">Preview</div>
  <ul>
    <li>Click work, Concept, or Argument link to open that record</li>
    <li>Click pdf link to jump to annotation</li>
  </ul>
</div>
`.trim();
    try {
        localStorage.removeItem('smde_work-notes-' + work.id);
    } catch (_e) {
        /* ignore */
    }
    const notesEl = ctx && ctx.query ? ctx.query('[data-prks-role="research-notes-editor"]') : null;
    if (!notesEl) return;
    const easyMDE = new EasyMDE({
        element: notesEl,
        spellChecker: false,
        autoDownloadFontAwesome: false,
        /* Server PATCH below is the source of truth; EasyMDE localStorage autosave would restore stale drafts after reload (autosave delay > PATCH delay). */
        autosave: { enabled: false },
        toolbar: [
            "bold",
            "italic",
            "heading",
            "|",
            "quote",
            "unordered-list",
            "ordered-list",
            "|",
            "link",
            "image",
            "|",
            {
                name: "prks-insert-concept",
                className: "fa fa-lightbulb-o prks-notes-concept",
                title: "Concept",
                action: () => {
                    const _mde = ctx && ctx.getResource ? ctx.getResource('workNotes') : null;
                    const editor = _mde && _mde.editor ? _mde.editor : _mde;
                    const cm = editor && editor.codemirror;
                    if (cm) prksOpenConceptPicker(cm);
                },
            },
            {
                name: "prks-insert-argument",
                className: "fa fa-comment prks-notes-argument",
                title: "Argument",
                action: () => {
                    const _mde = ctx && ctx.getResource ? ctx.getResource('workNotes') : null;
                    const editor = _mde && _mde.editor ? _mde.editor : _mde;
                    const cm = editor && editor.codemirror;
                    if (cm) prksOpenArgumentPicker(cm, work);
                },
            },
            "|",
            "preview",
            "side-by-side",
            "fullscreen",
            "|",
            {
                name: "prks-notes-help",
                className: "fa fa-question-circle prks-notes-help",
                title: "PRKS Notes Help",
                action: () => {
                    if (typeof prksAlertDialog === 'function') {
                        void prksAlertDialog({
                            title: 'Notes help',
                            messageHtml: prksNotesHelpHtml,
                            okLabel: 'Close',
                        });
                        return;
                    }
                    window.alert('Notes help: see wiki links [[...]] and [[pdf:...]].');
                },
            },
        ],
        status: ["lines", "words", "cursor"],
        minHeight: "120px",
        previewRender: (plainText) => {
            const _cwPrev = ctx && ctx.getEntity ? ctx.getEntity('work') : null;
            const refs =
                (_cwPrev && _cwPrev.id === work.id && _cwPrev.research_refs) ||
                work.research_refs ||
                {};
            let t = plainText;
            if (typeof window.prksReplaceResearchRefs === 'function') {
                t = window.prksReplaceResearchRefs(t, refs);
            }
            t = prksReplacePdfAnnotationWikiMarkers(t);
            t = prksReplaceWikiMarkersWithLinks(t, titleLowerToId);
            return prksSanitizeMarkdownPreviewHtml(easyMDE.markdown(t));
        },
    });

    const transient = prksWorkResearchDrafts.get(String(work.id));
    const workNotes = {
        workId: String(work.id),
        editor: easyMDE,
        hints: {
            wikiTitleMap: titleLowerToId,
        },
        pendingSave: false,
        drafting: false,
        saveError: false,
        editGeneration: 0,
        saveSequence: 0,
        latestSaveToken: 0,
        latestSaveEditGeneration: 0,
        settledSaveToken: 0,
        destroy: function () {
            try {
                const cm = easyMDE.codemirror;
                const handler = easyMDE.__notesChangeHandler;
                if (cm && handler) cm.off('change', handler);
                const beforeChangeHandler = easyMDE.__notesBeforeChangeHandler;
                if (cm && beforeChangeHandler) cm.off('beforeChange', beforeChangeHandler);
            } catch (_e) {}
            try {
                if (typeof easyMDE.toTextArea === 'function') easyMDE.toTextArea();
            } catch (_e) {}
        },
    };
    if (transient) prksSyncResearchNotesState(workNotes, transient);
    if (ctx && typeof ctx.setResource === 'function') {
        ctx.setResource('workNotes', workNotes, function () {
            workNotes.destroy();
        });
    }
    // Immediately reflect the current offline state -- an editor created
    // AFTER the runtime already left 'online' must never wait for a future
    // prksOfflineRuntimeSubscribe callback to become read-only.
    if (typeof prksOfflineRuntimeState === 'function') {
        prksApplyOfflineNotesReadOnly(ctx, prksOfflineRuntimeState() !== 'online');
    }
    prksAttachWikiLinkAutocomplete(easyMDE.codemirror, ctx);
    const toolbarHost = ctx && ctx.query ? ctx.query('.work-notes-editor-wrap .editor-toolbar') : null;
    prksPaintEasyMDEToolbarIcons(toolbarHost);

    const wrap = ctx && ctx.query ? ctx.query('.work-notes-editor-wrap') : null;
    if (wrap) {
        wrap.addEventListener(
            'click',
            (e) => {
                const pdfA = e.target.closest && e.target.closest('a.wiki-link-pdf-ann');
                if (pdfA) {
                    e.preventDefault();
                    const annId = pdfA.getAttribute('data-pdf-ann-id');
                    if (annId && typeof window.prksJumpToPdfAnnotationFromNotes === 'function') {
                        void window.prksJumpToPdfAnnotationFromNotes(annId, ctx);
                    }
                    return;
                }
                /* Internal wiki links use the workspace navigation contract. */
            },
            true
        );
    }

    const notesChangeHandler = () => {
        // CodeMirror's readOnly option blocks ordinary user edits, but this is a
        // defensive belt-and-suspenders check: offline must never enter drafting
        // state or arm a save debounce, no matter how the change event fired.
        if (typeof prksOfflineRuntimeState === 'function' && prksOfflineRuntimeState() !== 'online') return;
        const statusEl = ctx && ctx.query ? ctx.query('[data-prks-role="editor-status"]') : null;
        if (statusEl) statusEl.innerText = "Drafting...";
        prksWorkNotesMarkEdit(workNotes, work.id, easyMDE.value());
        if (ctx && ctx.tabId && typeof window.prksWorkspaceRefreshTabStatus === 'function') {
            window.prksWorkspaceRefreshTabStatus(ctx.tabId);
        }
        prksScheduleWorkResearchNotesSave(ctx, work.id);
    };
    easyMDE.codemirror.on("change", notesChangeHandler);
    easyMDE.__notesChangeHandler = notesChangeHandler;

    // Hard offline mutation barrier: CodeMirror's `readOnly` option only
    // blocks DOM-driven input (keyboard/mouse); it does not stop a
    // programmatic replaceRange/replaceSelection call from a toolbar
    // command, EasyMDE internal command, autocomplete pick, or a stale
    // picker callback left over from a previous Work. `beforeChange` fires
    // for every change regardless of origin and can cancel it outright, so
    // it is the one barrier that actually can't be bypassed by any of
    // those paths. 'setValue' is the sole allowlisted origin -- EasyMDE's
    // own initial-content set during construction (before this handler is
    // even attached) and any future programmatic full-content replace never
    // represent a user/tool mutation.
    const notesBeforeChangeHandler = function (_instance, changeObj) {
        if (!changeObj || changeObj.origin === 'setValue') return;
        if (typeof prksOfflineRuntimeState === 'function' && prksOfflineRuntimeState() !== 'online') {
            if (typeof changeObj.cancel === 'function') changeObj.cancel();
        }
    };
    easyMDE.codemirror.on('beforeChange', notesBeforeChangeHandler);
    easyMDE.__notesBeforeChangeHandler = notesBeforeChangeHandler;
}

function prksWorkNotesMarkEdit(notes, workId, text) {
    if (!notes) return 0;
    notes.editGeneration = (Number(notes.editGeneration) || 0) + 1;
    notes.drafting = true;
    const id = String(workId || notes.workId || '');
    if (id) {
        const entry = prksResearchDraftEntry(id, text);
        entry.text = String(text == null ? '' : text);
        entry.editGeneration = Math.max(entry.editGeneration + 1, notes.editGeneration);
        entry.state = 'drafting';
        entry.saveError = false;
        entry.updatedAt = Date.now();
        prksSyncResearchNotesState(notes, entry);
    }
    return notes.editGeneration;
}

function prksWorkNotesBeginSave(notes) {
    if (!notes) return 0;
    notes.saveSequence = (Number(notes.saveSequence) || 0) + 1;
    const token = notes.saveSequence;
    const capturedEditGeneration = Number(notes.editGeneration) || 0;
    notes.latestSaveToken = token;
    notes.latestSaveEditGeneration = capturedEditGeneration;
    notes.pendingSave = notes.latestSaveToken > (Number(notes.settledSaveToken) || 0);
    notes.drafting = (Number(notes.editGeneration) || 0) > capturedEditGeneration;
    return token;
}

function prksWorkNotesSettleSave(notes, token, ok) {
    if (!notes) return false;
    if (token !== notes.latestSaveToken) return false;
    notes.settledSaveToken = token;
    notes.pendingSave = notes.latestSaveToken > notes.settledSaveToken;
    notes.saveError = !ok;
    notes.drafting =
        (Number(notes.editGeneration) || 0) > (Number(notes.latestSaveEditGeneration) || 0);
    return true;
}

function prksScheduleWorkResearchNotesSave(ctx, workId) {
    if (ctx && typeof ctx.clearTimer === 'function') ctx.clearTimer('saveNotesTimeout');
    const tid = setTimeout(function () {
        if (ctx && ctx.timers && ctx.timers.get('saveNotesTimeout') === tid) {
            ctx.clearTimer('saveNotesTimeout');
        }
        prksEnqueueWorkResearchNotesSave(ctx, workId);
    }, 2000);
    if (ctx && typeof ctx.setTimer === 'function') ctx.setTimer('saveNotesTimeout', tid);
}

window.prksWorkNotesMarkEdit = prksWorkNotesMarkEdit;
window.prksWorkNotesBeginSave = prksWorkNotesBeginSave;
window.prksWorkNotesSettleSave = prksWorkNotesSettleSave;
window.prksScheduleWorkResearchNotesSave = prksScheduleWorkResearchNotesSave;

function prksEnqueueWorkResearchNotesSave(ctx, workId) {
    const owner = ctx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    if (typeof prksOfflineRuntimeState === 'function' && prksOfflineRuntimeState() !== 'online') {
        // No offline mutation outbox in Phase 1: leave the draft local (still visible/typed,
        // never silently discarded) rather than attempt a network PATCH that would only fail.
        // The connectivity subscriber below re-enqueues this once the app is back online.
        const statusEl = owner && owner.query ? owner.query('[data-prks-role="editor-status"]') : null;
        if (statusEl) statusEl.innerText = 'Offline — notes are read-only';
        return undefined;
    }
    const _cwSave = owner && owner.getEntity ? owner.getEntity('work') : null;
    const id = workId || (_cwSave && _cwSave.id);
    if (!id) return;
    const notes = owner && owner.getResource ? owner.getResource('workNotes') : null;
    const editor = notes && notes.editor ? notes.editor : notes;
    let content = '';
    if (editor && typeof editor.value === 'function') {
        content = editor.value();
    }
    const existingTransient = id ? prksWorkResearchDrafts.get(String(id)) : null;
    if (
        existingTransient &&
        existingTransient.promise &&
        existingTransient.text === content &&
        existingTransient.latestSaveEditGeneration === existingTransient.editGeneration &&
        existingTransient.latestSaveEditGeneration === (Number(notes && notes.editGeneration) || 0)
    ) {
        return existingTransient.promise;
    }
    const statusEl = owner && owner.query ? owner.query('[data-prks-role="editor-status"]') : null;
    const token = prksWorkNotesBeginSave(notes);
    const transient = id ? prksResearchDraftEntry(id, content) : null;
    let transientToken = 0;
    if (transient) {
        transient.text = content;
        transient.editGeneration = Math.max(transient.editGeneration, Number(notes && notes.editGeneration) || 0);
        transient.saveSequence += 1;
        transientToken = transient.saveSequence;
        transient.latestSaveToken = transientToken;
        transient.latestSaveEditGeneration = transient.editGeneration;
        transient.state = 'saving';
        transient.saveError = false;
        transient.updatedAt = Date.now();
        prksSyncResearchNotesState(notes, transient);
    }
    if (statusEl) statusEl.innerText = 'Saving...';
    if (owner && owner.tabId && typeof window.prksWorkspaceRefreshTabStatus === 'function') {
        window.prksWorkspaceRefreshTabStatus(owner.tabId);
    }
    const savePromise = prksRequest(
        '/api/works/' + encodeURIComponent(id),
        {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text_content: content }),
        },
        {
            coalesceKey: 'work-research-notes:' + id,
        }
    );
    if (transient) transient.promise = savePromise;
    void savePromise
        .then(async function (res) {
            const ok = !!(res && res.ok);
            if (ok && typeof prksOfflineMarkEntityChanged === 'function') {
                // Canonical success matters even when this editor token is stale.
                prksOfflineMarkEntityChanged('work', id);
            }
            if (ok && typeof prksOfflineMarkConceptsChanged === 'function') {
                // Research Notes are the canonical source of Work -> Concept
                // mentions, and unknown [[concept:...]] markup can create
                // Concepts outright, so every acknowledged notes save stales the
                // cached Concept index/details -- stale-for-UI is still a
                // successful canonical mutation here.
                prksOfflineMarkConceptsChanged();
            }
            const localApplied = prksWorkNotesSettleSave(notes, token, ok);
            let transientApplied = false;
            if (transient && transientToken === transient.latestSaveToken) {
                const hasNewerDraft = transient.editGeneration > transient.latestSaveEditGeneration;
                transient.settledSaveToken = transientToken;
                transient.promise = null;
                transient.saveError = !ok && !hasNewerDraft;
                transient.state = hasNewerDraft ? 'drafting' : !ok ? 'error' : 'committed';
                transient.updatedAt = Date.now();
                transientApplied = true;
                prksSyncResearchNotesState(notes, transient);
                prksSyncLiveResearchDraft(id, transient);
                prksPruneResearchDrafts();
            }
            if (!localApplied && !transientApplied) return undefined;
            const ownerLive = owner && typeof owner.isCurrent === 'function' && owner.isCurrent();
            if (statusEl && ownerLive) {
                statusEl.innerText =
                    transientApplied && transient.state === 'drafting'
                        ? 'Drafting...'
                        : ok
                          ? 'All changes saved'
                          : 'Error saving changes';
            }
            if (ok && typeof fetchWorkDetails === 'function') {
                let liveOwner = null;
                if (typeof prksForEachLiveTabContext === 'function') {
                    prksForEachLiveTabContext(function (candidate) {
                        if (liveOwner) return;
                        const liveWork = candidate.getEntity ? candidate.getEntity('work') : null;
                        if (liveWork && String(liveWork.id) === String(id)) liveOwner = candidate;
                    });
                }
                if (!liveOwner) return undefined;
                const generation = liveOwner.generation;
                const signal = liveOwner.abortController && liveOwner.abortController.signal;
                return fetchWorkDetails(id, { signal: signal }).then(function (latest) {
                    if (!liveOwner.isCurrent(generation)) return;
                    const live = liveOwner.getEntity ? liveOwner.getEntity('work') : null;
                    if (!live || String(live.id) !== String(id)) return;
                    if (latest && latest.research_refs) live.research_refs = latest.research_refs;
                    const currentDraft = prksWorkResearchDrafts.get(String(id));
                    if (
                        currentDraft &&
                        currentDraft.state === 'committed' &&
                        !currentDraft.promise &&
                        latest &&
                        String(latest.text_content == null ? '' : latest.text_content) === currentDraft.text
                    ) {
                        prksWorkResearchDrafts.delete(String(id));
                    }
                });
            }
            return undefined;
        })
        .catch(function () {
            const applied = prksWorkNotesSettleSave(notes, token, false);
            let transientApplied = false;
            if (transient && transientToken === transient.latestSaveToken) {
                const hasNewerDraft = transient.editGeneration > transient.latestSaveEditGeneration;
                transient.settledSaveToken = transientToken;
                transient.promise = null;
                transient.saveError = !hasNewerDraft;
                transient.state = hasNewerDraft ? 'drafting' : 'error';
                transient.updatedAt = Date.now();
                transientApplied = true;
                prksSyncResearchNotesState(notes, transient);
                prksSyncLiveResearchDraft(id, transient);
            }
            if (!applied && !transientApplied) return;
            const ownerLive = owner && typeof owner.isCurrent === 'function' && owner.isCurrent();
            if (statusEl && ownerLive) {
                statusEl.innerText =
                    transientApplied && transient.state === 'drafting'
                        ? 'Drafting...'
                        : 'Error saving changes';
            }
        });
    return savePromise;
}

function prksFlushPendingWorkResearchNotes(ctx) {
    const owner = ctx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    const _hasSaveTimer = owner && owner.timers && owner.timers.has('saveNotesTimeout');
    if (!_hasSaveTimer) return;
    if (owner && typeof owner.clearTimer === 'function') owner.clearTimer('saveNotesTimeout');
    const _cw = owner && owner.getEntity ? owner.getEntity('work') : null;
    const id = _cw && _cw.id;
    if (!id) return;
    prksEnqueueWorkResearchNotesSave(owner, id);
}

window.prksEnqueueWorkResearchNotesSave = prksEnqueueWorkResearchNotesSave;
window.prksFlushPendingWorkResearchNotes = prksFlushPendingWorkResearchNotes;

/**
 * Research Notes stay explicitly read-only while offline (AGENTS.md "Research
 * Notes and private notes" -- no offline outbox yet, autosave must not let a
 * user type for minutes only to discover nothing persisted). Reconnecting
 * quietly re-enqueues any draft that was held back while offline.
 */
/**
 * EasyMDE toolbar buttons that alter Markdown content. PRKS's own Concept/
 * Argument buttons call picker/insert logic directly and never consult
 * CodeMirror's readOnly flag at all -- disabling every mutating button here
 * (native `disabled`, blocks both mouse and keyboard activation) is the
 * belt to prksWorkNotesMutationAllowed()'s suspenders. Preview/side-by-side/
 * fullscreen/Help never alter the document and stay available.
 */
const PRKS_EASYMDE_MUTATING_TOOLBAR_CLASSES = [
    'bold',
    'italic',
    'heading',
    'quote',
    'unordered-list',
    'ordered-list',
    'link',
    'image',
    'prks-insert-concept',
    'prks-insert-argument',
];

function prksSetEasyMDEToolbarMutationEnabled(ctx, enabled) {
    const toolbar = ctx && ctx.query ? ctx.query('.work-notes-editor-wrap .editor-toolbar') : null;
    if (!toolbar) return;
    toolbar.querySelectorAll('button').forEach((btn) => {
        const mutating = PRKS_EASYMDE_MUTATING_TOOLBAR_CLASSES.some((cls) => btn.classList.contains(cls));
        if (!mutating) return;
        btn.disabled = !enabled;
        btn.classList.toggle('prks-toolbar-btn--disabled', !enabled);
        if (enabled) btn.removeAttribute('aria-disabled');
        else btn.setAttribute('aria-disabled', 'true');
    });
}

function prksApplyOfflineNotesReadOnly(ctx, offline) {
    const notes = ctx && ctx.getResource ? ctx.getResource('workNotes') : null;
    const cm = notes && notes.editor && notes.editor.codemirror;
    if (!cm) return;
    cm.setOption('readOnly', !!offline);
    prksSetEasyMDEToolbarMutationEnabled(ctx, !offline);
    const statusEl = ctx.query ? ctx.query('[data-prks-role="editor-status"]') : null;
    if (!statusEl) return;
    if (offline) {
        statusEl.innerText = 'Offline — notes are read-only';
    } else if (notes.drafting) {
        statusEl.innerText = 'Drafting...';
    } else if (notes.pendingSave) {
        statusEl.innerText = 'Saving...';
    } else if (notes.saveError) {
        statusEl.innerText = 'Error saving changes';
    } else {
        statusEl.innerText = 'All changes saved';
    }
}

if (typeof prksOfflineRuntimeSubscribe === 'function') {
    prksOfflineRuntimeSubscribe(function (state) {
        if (typeof prksForEachLiveTabContext !== 'function') return;
        const offline = state !== 'online';
        prksForEachLiveTabContext(function (ctx) {
            prksApplyOfflineNotesReadOnly(ctx, offline);
        });
        if (!offline) {
            prksForEachLiveTabContext(function (ctx) {
                const notes = ctx && ctx.getResource ? ctx.getResource('workNotes') : null;
                if (notes && notes.drafting) {
                    void prksEnqueueWorkResearchNotesSave(ctx, notes.workId);
                }
            });
        }
    });
}

function prksDestroyWorkNotesEditor(ctx) {
    const owner = ctx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    if (owner && typeof owner.clearResource === 'function') owner.clearResource('workNotes');
}

window.prksDestroyWorkNotesEditor = prksDestroyWorkNotesEditor;

function prksWorkNotesMobileSideActive(ctx) {
    const ws =
        ctx && typeof ctx.query === 'function'
            ? ctx.query('.work-workspace[data-work-id]')
            : document.querySelector('.prks-tab-root .work-workspace[data-work-id], .work-workspace[data-work-id]');
    return !!(ws && ws.classList.contains('work-workspace--side'));
}

function prksWorkNotesEditor(ctx) {
    return ctx && typeof ctx.getResource === 'function' ? ctx.getResource('workNotes') : null;
}

function prksSyncWorkNotesSplitAria(ws, handle, side) {
    if (!ws || !handle) return;
    const vertical = side != null ? side : ws.classList.contains('work-workspace--side');
    const min = 160;
    const max = vertical
        ? prksClampWorkNotesSideWidth(ws, handle, Number.MAX_SAFE_INTEGER)
        : prksClampWorkNotesHeight(ws, handle, Number.MAX_SAFE_INTEGER);
    const raw = ws.style.getPropertyValue(vertical ? '--work-notes-width' : '--work-notes-height').trim();
    const now = parseInt(raw, 10) || min;
    handle.setAttribute('aria-valuemin', String(min));
    handle.setAttribute('aria-valuemax', String(max));
    handle.setAttribute('aria-valuenow', String(Math.max(min, Math.min(max, now))));
}

function prksReapplyWorkNotesSplitLayout(ctx) {
    if (!ctx || typeof ctx.query !== 'function') return;
    const ws = ctx.query('.work-workspace[data-work-id]');
    if (!ws) return;
    const workId = ws.getAttribute('data-work-id');
    if (!workId) return;
    const handle = ws.querySelector('.work-split-handle');
    if (!handle) return;
    const enabled =
        typeof prksGetMobileWorkNotesRightEnabled === 'function' && prksGetMobileWorkNotesRightEnabled();
    const wantSide = !!(enabled && ws.clientWidth > 0 && ws.clientWidth < 720);
    ws.classList.toggle('work-workspace--side', wantSide);

    if (prksWorkNotesMobileSideActive(ctx)) {
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

    handle.setAttribute('aria-orientation', prksWorkNotesMobileSideActive(ctx) ? 'vertical' : 'horizontal');
    handle.setAttribute(
        'aria-label',
        prksWorkNotesMobileSideActive(ctx)
            ? 'Drag to resize research notes panel width'
            : 'Drag to resize research notes panel height'
    );
    prksSyncWorkNotesSplitAria(ws, handle, prksWorkNotesMobileSideActive(ctx));

    requestAnimationFrame(() => {
        const _mde = prksWorkNotesEditor(ctx);
        if (_mde && _mde.codemirror) {
            _mde.codemirror.refresh();
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

function setupWorkNotesSplitResize(ctx, workId) {
    const ws = ctx && ctx.query ? ctx.query('.work-workspace[data-work-id="' + workId + '"]') : null;
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

    if (prksWorkNotesMobileSideActive(ctx)) {
        ws.style.setProperty('--work-notes-width', prksClampWorkNotesSideWidth(ws, handle, initialW) + 'px');
    } else {
        ws.style.setProperty('--work-notes-height', prksClampWorkNotesHeight(ws, handle, initialH) + 'px');
    }
    handle.setAttribute('aria-orientation', prksWorkNotesMobileSideActive(ctx) ? 'vertical' : 'horizontal');
    handle.setAttribute(
        'aria-label',
        prksWorkNotesMobileSideActive(ctx)
            ? 'Drag to resize research notes panel width'
            : 'Drag to resize research notes panel height'
    );
    prksSyncWorkNotesSplitAria(ws, handle, prksWorkNotesMobileSideActive(ctx));

    function refreshNotesEditor() {
        const _mde = prksWorkNotesEditor(ctx);
        if (_mde && _mde.codemirror) {
            _mde.codemirror.refresh();
        }
    }

    handle.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        const side = ws.classList.contains('work-workspace--side');
        let dragging = true;
        const pointerId = e.pointerId;
        const startX = e.clientX;
        const startY = e.clientY;
        const startNw = notesPane.getBoundingClientRect().width;
        const startNh = notesPane.getBoundingClientRect().height;
        let unregisterCleanup = function () {};
        handle.classList.add('dragging');
        try {
            handle.setPointerCapture(pointerId);
        } catch (_err) {}

        function onMove(ev) {
            if (!dragging) return;
            ev.preventDefault();
            if (side) {
                const delta = ev.clientX - startX;
                const next = prksClampWorkNotesSideWidth(ws, handle, startNw - delta);
                ws.style.setProperty('--work-notes-width', next + 'px');
            } else {
                const delta = ev.clientY - startY;
                const next = prksClampWorkNotesHeight(ws, handle, startNh - delta);
                ws.style.setProperty('--work-notes-height', next + 'px');
            }
            prksSyncWorkNotesSplitAria(ws, handle, side);
            refreshNotesEditor();
        }

        function endDrag() {
            if (!dragging) return;
            dragging = false;
            unregisterCleanup();
            handle.classList.remove('dragging');
            document.removeEventListener('pointermove', onMove, true);
            document.removeEventListener('pointerup', endDrag, true);
            document.removeEventListener('pointercancel', endDrag, true);
            handle.removeEventListener('lostpointercapture', endDrag);
            try {
                if (handle.hasPointerCapture && handle.hasPointerCapture(pointerId)) {
                    handle.releasePointerCapture(pointerId);
                }
            } catch (_err2) {}
            if (side) {
                const raw = ws.style.getPropertyValue('--work-notes-width').trim();
                const w = parseInt(raw, 10);
                if (!Number.isNaN(w)) localStorage.setItem(storageKeyW, String(w));
            } else {
                const raw = ws.style.getPropertyValue('--work-notes-height').trim();
                const h = parseInt(raw, 10);
                if (!Number.isNaN(h)) localStorage.setItem(storageKeyH, String(h));
            }
            refreshNotesEditor();
        }

        if (ctx && typeof ctx.registerCleanup === 'function') unregisterCleanup = ctx.registerCleanup(endDrag);
        handle.addEventListener('lostpointercapture', endDrag);
        document.addEventListener('pointermove', onMove, true);
        document.addEventListener('pointerup', endDrag, true);
        document.addEventListener('pointercancel', endDrag, true);
    });

    handle.addEventListener('keydown', (e) => {
        const step = e.shiftKey ? 24 : 10;
        const side = ws.classList.contains('work-workspace--side');
        if (side) {
            const raw = ws.style.getPropertyValue('--work-notes-width').trim();
            const cur = parseInt(raw, 10) || initialW;
            if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
                e.preventDefault();
                const delta = e.key === 'ArrowLeft' ? step : -step;
                const next = prksClampWorkNotesSideWidth(ws, handle, cur + delta);
                ws.style.setProperty('--work-notes-width', next + 'px');
                localStorage.setItem(storageKeyW, String(next));
                prksSyncWorkNotesSplitAria(ws, handle, side);
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
                prksSyncWorkNotesSplitAria(ws, handle, side);
                refreshNotesEditor();
            }
        }
    });

    if (typeof ResizeObserver === 'function') {
        const ro = new ResizeObserver(function () {
            prksReapplyWorkNotesSplitLayout(ctx);
        });
        try {
            ro.observe(ws);
        } catch (_e) {}
        if (ctx && typeof ctx.registerCleanup === 'function') {
            ctx.registerCleanup(function () {
                try {
                    ro.disconnect();
                } catch (_e2) {}
            });
        }
    }

    if (!window.__prksWorkNotesViewportBound) {
        window.__prksWorkNotesViewportBound = true;
        window.addEventListener('resize', function () {
            if (typeof prksForEachMountedTabContext !== 'function') return;
            prksForEachMountedTabContext(function (c) {
                if (typeof prksReapplyWorkNotesSplitLayout === 'function') prksReapplyWorkNotesSplitLayout(c);
            });
        });
    }

    requestAnimationFrame(() => {
        refreshNotesEditor();
        const activeNotesPane = ws.querySelector('.work-notes-pane');
        if (!activeNotesPane) return;
        const nRect = activeNotesPane.getBoundingClientRect();
        const host = ws.closest('.prks-tile__body') || ws;
        const hostRect = host.getBoundingClientRect();
        const isOutOfViewport = nRect.bottom > hostRect.bottom + 1 || nRect.top < hostRect.top - 1;
        const collapsed = ws.classList.contains('work-workspace--notes-collapsed');
        const isSmall = ws.clientWidth > 0 && ws.clientWidth < 720;
        if (collapsed && isOutOfViewport && isSmall) {
            ws.classList.remove('work-workspace--notes-collapsed');
            try {
                localStorage.setItem('prks.workNotesCollapsed.' + workId, '0');
            } catch (_e) {}
            requestAnimationFrame(() => refreshNotesEditor());
        }
    });

    if (ctx && typeof ctx.setResource === 'function' && typeof ResizeObserver !== 'undefined') {
        if (!ctx.getResource('workNotesSideRo')) {
            let ticking = false;
            const ro = new ResizeObserver(function () {
                if (ticking) return;
                ticking = true;
                requestAnimationFrame(function () {
                    ticking = false;
                    if (typeof prksReapplyWorkNotesSplitLayout === 'function') {
                        prksReapplyWorkNotesSplitLayout(ctx);
                    }
                });
            });
            ro.observe(ws);
            ctx.setResource('workNotesSideRo', ro, function () {
                try {
                    ro.disconnect();
                } catch (_e) {}
            });
        }
    }
}

function setupWorkNotesCollapseToggle(ctx, workId) {
    const ws = ctx && ctx.query ? ctx.query('.work-workspace[data-work-id="' + workId + '"]') : null;
    const btn = ctx && ctx.query ? ctx.query('[data-prks-role="work-notes-collapse-btn"]') : null;
    if (!ws || !btn) return;

    const storageKey = 'prks.workNotesCollapsed.' + workId;

    function syncToggleUi() {
        const collapsed = ws.classList.contains('work-workspace--notes-collapsed');
        btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        const side = typeof prksWorkNotesMobileSideActive === 'function' && prksWorkNotesMobileSideActive(ctx);
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
            const _mdeC = prksWorkNotesEditor(ctx);
            if (_mdeC && _mdeC.codemirror) {
                _mdeC.codemirror.refresh();
            }
        });
    }

    syncToggleUi();
    if (ctx && typeof ctx.setResource === 'function') ctx.setResource('workNotesCollapseSync', syncToggleUi);
    btn.addEventListener('click', () => setCollapsed(!ws.classList.contains('work-workspace--notes-collapsed')));
}

const copyBibTeXResetTimers = new WeakMap();

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
    const previousTimer = copyBibTeXResetTimers.get(btn);
    if (previousTimer) {
        clearTimeout(previousTimer);
        copyBibTeXResetTimers.delete(btn);
        restore();
    }
    try {
        const res = await prksRequest('/api/bibtex/' + encodeURIComponent(workId));
        if (!res.ok) throw new Error('bibtex fetch failed');
        const text = await res.text();
        await prksCopyBibTeXToClipboard(text);
        snapshotIfNeeded();
        btn.innerHTML =
            (typeof prksIcon === 'function' ? prksIcon('check', { size: 'sm' }) : '') + ' BibTeX copied!';
        if (typeof prksRefreshIcons === 'function') prksRefreshIcons(btn);
        btn.style.color = '#16a34a';
        btn.style.borderColor = '#16a34a';
        const resetTimer = setTimeout(() => {
            restore();
            copyBibTeXResetTimers.delete(btn);
        }, 2500);
        copyBibTeXResetTimers.set(btn, resetTimer);
    } catch (e) {
        snapshotIfNeeded();
        btn.innerHTML =
            (typeof prksIcon === 'function' ? prksIcon('x', { size: 'sm' }) : '') + ' Copy failed';
        if (typeof prksRefreshIcons === 'function') prksRefreshIcons(btn);
        btn.style.color = '#ef4444';
        btn.style.borderColor = '#ef4444';
        const resetTimer = setTimeout(() => {
            restore();
            copyBibTeXResetTimers.delete(btn);
        }, 2500);
        copyBibTeXResetTimers.set(btn, resetTimer);
    }
}

/** Wire Copy BibTeX / Delete File in #panel-content (right column Details tab). */
function initWorkDetailRightPanelActions(work, ownerCtx) {
    const panel = document.getElementById('panel-content');
    if (ownerCtx && typeof prksRightPanelOwnedBy === 'function' && !prksRightPanelOwnedBy(ownerCtx, panel)) return;
    if (!panel || !work || !work.id) return;
    const copyBtn = panel.querySelector('.copy-bibtex-btn');
    if (copyBtn) {
        copyBtn.addEventListener('click', () => void copyBibTeX(work.id, copyBtn));
    }
    const delBtn = panel.querySelector('.delete-work-btn');
    if (delBtn) {
        delBtn.addEventListener('click', () => void deleteWork(work.id, ownerCtx));
    }
}
