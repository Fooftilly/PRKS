/**
 * PDF viewer and annotation integration — loaded on demand when opening a work with a PDF.
 */

import { createPrksPdfViewer } from '/js/pdf-viewer-runtime.js';

function prksResolvePdfCtx(element) {
    if (typeof prksOwnerTabContext === 'function' && element) {
        const fromEl = prksOwnerTabContext(element);
        if (fromEl) return fromEl;
    }
    return typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
}

function prksPdfRuntime(ctx) {
    const owner = ctx || prksResolvePdfCtx();
    return owner && typeof owner.getResource === 'function' ? owner.getResource('pdf') : null;
}

function prksPdfViewer(ctx) {
    const rt = prksPdfRuntime(ctx);
    return rt && rt.viewer ? rt.viewer : null;
}

function prksPdfOwnerOrFocused(ctx) {
    if (ctx && typeof ctx.getResource === 'function') return ctx;
    return typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
}

function prksIsFocusedPdfCtx(ctx) {
    if (!ctx) return true;
    const focused = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    return !focused || focused.tabId === ctx.tabId;
}

function prksAnnotationTypeStr(obj) {
    if (!obj || typeof obj !== 'object') return '';
    return String(obj.type || obj.subtype || obj.annotationType || '').toLowerCase();
}

/** PDF / EmbedPDF link annotations (URI, internal GoTo, etc.) — sidebar list only when filtered. */
function prksIsPdfLinkAnnotation(item) {
    if (!item || typeof item !== 'object') return false;
    // pdf.js / common engines: subtype /Link is numeric 1 (string "1" after JSON round-trip).
    const rawType = item.type ?? item.annotationType ?? item.subtype ?? item.Subtype;
    // Legacy engine: numeric 1 as link. Do not treat pdf.js Link (2) as link without URI/dest/action — EmbedPDF may use 2 for markup.
    if (rawType === 1 || rawType === '1') return true;
    const t = prksAnnotationTypeStr(item);
    if (t.includes('link')) return true;
    const sub = String(item.subtype || item.Subtype || '').toLowerCase();
    if (sub.includes('link')) return true;
    // Viewer often sets subject/title/contents to the literal "Link" while type stays numeric.
    const labelFields = [item.contents, item.content, item.comment, item.text, item.subject, item.title, item.body];
    const labelJoined = labelFields.filter(Boolean).join(' ').trim().toLowerCase();
    if (labelJoined === 'link') return true;
    const uriLike = (v) =>
        typeof v === 'string' && v.trim() && (/^https?:\/\//i.test(v) || v.includes('://'));
    if (uriLike(item.uri) || uriLike(item.url) || uriLike(item.URL)) return true;
    const action = item.action;
    if (action && typeof action === 'object') {
        const at = String(action.type || action.S || action.s || '').toLowerCase();
        if (['uri', 'goto', 'gotor', 'launch', 'named'].some((x) => at.includes(x))) return true;
        const dest = action.uri || action.URL || action.url;
        if (uriLike(dest)) return true;
    }
    if (item.dest != null || item.destination != null) return true;
    // pdf.js Link = 2; EmbedPDF often omits URI on flattened state clones — treat 2 as link unless it looks like text markup.
    if (rawType === 2 || rawType === '2') {
        return !prksEmbedType2IsUserTextMarkup(item);
    }
    return false;
}

/** True when numeric type 2 is editor text markup (highlight/ink), not a PDF link. */
function prksEmbedType2IsUserTextMarkup(item) {
    if (!item || typeof item !== 'object') return false;
    if (Array.isArray(item.inkList) && item.inkList.length > 0) return true;
    if (Array.isArray(item.segmentRects) && item.segmentRects.length > 0) return true;
    const blob = [item.subtype, item.subType, item.annotationType, item.type, item.name]
        .filter((x) => x != null && x !== '')
        .map((x) => String(x).toLowerCase())
        .join(' ');
    if (/highlight|underline|strike|squiggly|ink|freetext|textmarkup/.test(blob)) return true;
    if (item.custom && typeof item.custom === 'object' && Object.keys(item.custom).length > 0) return true;
    return false;
}

/** pdf.js AnnotationType numbers (Link = 2 handled via prksIsPdfLinkAnnotation + prksEmbedType2IsUserTextMarkup). */
const PRKS_PDF_ANN_TYPE_USER_NUM = new Set([1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]);
const PRKS_PDF_ANN_TYPE_DENY_NUM = new Set([2, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27]);

function prksPdfAnnotationPrimaryTypeNumber(item) {
    if (!item || typeof item !== 'object') return NaN;
    const raw = item.type ?? item.annotationType ?? item.subtype ?? item.Subtype;
    if (typeof raw === 'number' && Number.isFinite(raw)) return raw;
    if (typeof raw === 'string' && /^\d+$/.test(raw.trim())) return parseInt(raw.trim(), 10);
    return NaN;
}

/**
 * Sidebar list + API persistence: only annotations users create in the editor (highlights, ink, …).
 * Excludes embedded PDF artifacts (links, watermarks, widgets, …).
 */
function prksIsUserMarkupAnnotation(item) {
    if (!item || typeof item !== 'object') return false;
    if (prksIsPdfLinkAnnotation(item)) return false;

    const id = item.id || item.uuid || item.annotationId || item._id || item.annotation_id || item.ID;
    if (!id) return false;

    const geometryBacked = isLikelyAnnotationObject(item);
    const typLo = String(item.type || item.annotationType || item.subtype || '').toLowerCase();
    const pageOk =
        item.pageIndex != null ||
        item.page != null ||
        item.pageNumber != null ||
        item.page_index != null;
    const persistedTextNote =
        !geometryBacked &&
        pageOk &&
        (typLo === 'note' || typLo === 'comment' || typLo === 'freetext' || typLo === 'text') &&
        !!(item.contents || item.content || item.comment || item.text);

    if (!geometryBacked && !persistedTextNote) return false;

    // EmbedPDF text markup: strong geometry + record hints (type fields often numeric only).
    if (geometryBacked) {
        if (Array.isArray(item.segmentRects) && item.segmentRects.length > 0) return true;
        if (Array.isArray(item.inkList) && item.inkList.length > 0) return true;
        const rec = item.recordType || item.schemaType || item.annotationKind || item.variant || item.name;
        if (
            typeof rec === 'string' &&
            /highlight|underline|strike|squiggly|ink|freetext|textmarkup|caret|line|polygon|polyline|square|circle|stamp/i.test(
                rec
            )
        ) {
            return true;
        }
    }

    const typeNum = prksPdfAnnotationPrimaryTypeNumber(item);
    if (Number.isFinite(typeNum)) {
        if (typeNum === 2 && prksEmbedType2IsUserTextMarkup(item)) return true;
        if (PRKS_PDF_ANN_TYPE_DENY_NUM.has(typeNum)) return false;
        if (PRKS_PDF_ANN_TYPE_USER_NUM.has(typeNum)) return true;
    }

    const parts = [item.type, item.annotationType, item.subtype, item.subType, item.Subtype]
        .filter((v) => v != null && v !== '')
        .map((v) => (typeof v === 'string' ? v : String(v)).toLowerCase());
    const blob = parts.join(' ');

    const denySubstr = [
        'watermark',
        'widget',
        'popup',
        'fileattachment',
        'movie',
        'sound',
        'screen',
        'printermark',
        'trapnet',
        'redact',
    ];
    if (denySubstr.some((d) => blob.includes(d))) return false;

    const tokens = blob.split(/[^a-z0-9]+/).filter(Boolean);
    const denyTokens = new Set([
        'watermark',
        'widget',
        'popup',
        'movie',
        'sound',
        'screen',
        'trapnet',
        'redact',
        'attachment',
    ]);
    if (tokens.some((t) => denyTokens.has(t))) return false;

    const allowTokens = new Set([
        'highlight',
        'underline',
        'strikeout',
        'strikethrough',
        'strike',
        'squiggly',
        'ink',
        'freetext',
        'caret',
        'stamp',
        'square',
        'circle',
        'line',
        'polygon',
        'polyline',
        'text',
        'note',
        'comment',
    ]);
    if (tokens.some((t) => allowTokens.has(t))) return true;

    const allowNeedle = [
        'highlight',
        'underline',
        'strikeout',
        'strikethrough',
        'squiggly',
        'freetext',
        'textmarkup',
    ];
    if (allowNeedle.some((n) => blob.includes(n))) return true;

    const custom = item.custom && typeof item.custom === 'object' ? item.custom : null;
    if (custom && typeof custom.prksComment === 'string' && custom.prksComment.trim()) return true;

    return false;
}

function isLikelyAnnotationObject(value) {
    if (!value || typeof value !== 'object' || value.deleted === true) return false;
    const id = value.id || value.uuid || value.annotationId || value._id || value.annotation_id || value.ID;
    if (!id) return false;
    
    // Relaxed check: if it has geometry and an ID, it's likely an annotation
    const hasGeometry = !!(
        value.rect ||
        value.rects ||
        value.quadPoints ||
        value.points ||
        value.position ||
        value.location ||
        value.box ||
        value.Rect ||
        value.QuadPoints ||
        (Array.isArray(value.segmentRects) && value.segmentRects.length > 0) ||
        (Array.isArray(value.inkList) && value.inkList.length > 0) ||
        (Array.isArray(value.vertices) && value.vertices.length > 0)
    );
    if (!hasGeometry) return false;
    
    const typeRaw = value.type || value.annotationType || value.subtype || value.subType || value.Subtype || '';
    const type = (typeof typeRaw === 'string' ? typeRaw : String(typeRaw)).toLowerCase();
    const hasType = ['high', 'mark', 'text', 'comment', 'strike', 'under', 'stamp', 'note', 'ink', 'shape', 'freetext', 'square', 'circle', 'line', 'poly', 'squiggly'].some((t) => type.includes(t));
    const hasContent = !!(value.contents || value.content || value.comment || value.text || value.body);
    
    return hasType || hasContent || !!value.rect || !!value.rects || !!value.quadPoints || (Array.isArray(value.segmentRects) && value.segmentRects.length > 0);
}

function prksViewerAnnotationObjects(viewer) {
    if (!viewer || typeof viewer.getAnnotations !== 'function') return [];
    const out = [];
    for (const a of viewer.getAnnotations() || []) {
        const obj = a && a.raw && typeof a.raw === 'object' ? a.raw : a;
        if (obj && typeof obj === 'object') out.push(obj);
    }
    return out;
}

function prksFindViewerAnnotation(viewer, annId) {
    const sid = String(annId);
    for (const obj of prksViewerAnnotationObjects(viewer)) {
        const id = obj.id || obj.uuid || obj.annotationId || obj._id;
        if (id != null && String(id) === sid) return obj;
    }
    return null;
}

function annotationListPageIndex(item) {
    if (!item || typeof item !== 'object') return Number.POSITIVE_INFINITY;
    const p = item.pageIndex ?? item.page ?? item.pageNumber ?? item.page_index;
    if (p === undefined || p === null) return Number.POSITIVE_INFINITY;
    const n = Number(p);
    return Number.isFinite(n) ? n : Number.POSITIVE_INFINITY;
}

function annotationListVerticalKey(item) {
    if (!item || typeof item !== 'object') return 0;
    const r = item.rect;
    if (r && r.origin && Number.isFinite(Number(r.origin.y))) return Number(r.origin.y);
    const segs = item.segmentRects;
    if (Array.isArray(segs)) {
        let minY = Infinity;
        for (const s of segs) {
            if (s && s.origin && Number.isFinite(Number(s.origin.y))) {
                minY = Math.min(minY, Number(s.origin.y));
            }
        }
        if (Number.isFinite(minY)) return minY;
    }
    return 0;
}

/** Sidebar + persisted list: page order (0-based index), then top-to-bottom on page, then id. */
function sortAnnotationsByPage(items) {
    if (!Array.isArray(items) || items.length === 0) return Array.isArray(items) ? items.slice() : [];
    if (items.length === 1) return items.slice();
    return items.slice().sort((a, b) => {
        const pa = annotationListPageIndex(a);
        const pb = annotationListPageIndex(b);
        if (pa !== pb) return pa - pb;
        const ya = annotationListVerticalKey(a);
        const yb = annotationListVerticalKey(b);
        if (ya !== yb) return ya - yb;
        const ida = String(a.id || a.uuid || a.annotationId || '');
        const idb = String(b.id || b.uuid || b.annotationId || '');
        return ida.localeCompare(idb);
    });
}

function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
        const sub = bytes.subarray(i, i + chunk);
        binary += String.fromCharCode.apply(null, sub);
    }
    return btoa(binary);
}

function annotationToText(item) {
    if (!item || typeof item !== 'object') return '';
    // Prefer PRKS comment as the human label (matches EmbedPDF behavior of renaming when commented).
    if (
        item.custom &&
        typeof item.custom === 'object' &&
        typeof item.custom.prksComment === 'string' &&
        item.custom.prksComment.trim()
    ) {
        return item.custom.prksComment.trim();
    }
    // Omit author in the sidebar — single-user app; author is fixed in viewer config, not listed here.
    const fields = [item.contents, item.content, item.comment, item.text, item.subject, item.title, item.body];
    let joined = fields.filter(Boolean).join(' ').trim();
    if (/^(Guest|Anonymous|nikola|you)$/i.test(joined)) joined = '';
    const annAuthor = getPrksAnnotationAuthor();
    if (joined && annAuthor && joined.toLowerCase() === annAuthor.toLowerCase()) joined = '';
    if (joined) return joined;
    
    // Fallback for highlights with no user-added text
    const typeRaw = item.type || item.annotationType || item.subtype || '';
    const type = (typeof typeRaw === 'string' ? typeRaw : String(typeRaw)).toLowerCase();
    if (type.includes('highlight')) return 'Text Highlight';
    if (type.includes('underline')) return 'Underline';
    if (type.includes('strike')) return 'Strikethrough';
    if (type.includes('squiggly')) return 'Squiggly underline';
    if (type.includes('text') || type.includes('comment') || type.includes('note')) return 'Comment';
    if (type.includes('ink')) return 'Ink drawing';
    return '';
}

function prksEscapePdfAnnLabelForWiki(label) {
    // Keep label safe for `[[pdf:id|label]]` (no `]]`, no newlines, no `|`).
    return String(label || '')
        .replace(/\r?\n/g, ' ')
        .replace(/\]\]/g, '] ]')
        .replace(/\|/g, '/')
        .trim();
}

function prksBuildPdfAnnWikiLink(annId, label) {
    const id = annId == null ? '' : String(annId);
    if (!id) return '';
    const lab = label != null ? prksEscapePdfAnnLabelForWiki(label) : '';
    return lab ? `[[pdf:${id}|${lab}]]` : `[[pdf:${id}]]`;
}

async function prksCopyTextToClipboard(text) {
    const s = text == null ? '' : String(text);
    if (!s) return;
    let clipboardErr = null;
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        try {
            await navigator.clipboard.writeText(s);
            return;
        } catch (e) {
            clipboardErr = e;
        }
    }

    // Fallback for older browsers / blocked clipboard access.
    const ta = document.createElement('textarea');
    ta.value = s;
    ta.setAttribute('readonly', 'readonly');
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    ta.style.top = '-9999px';
    document.body.appendChild(ta);
    try {
        ta.select();
        const ok = document.execCommand('copy');
        if (!ok) throw clipboardErr || new Error('Copy failed');
    } finally {
        document.body.removeChild(ta);
    }
}

function escapeHtml(s) {
    if (typeof window.prksEscapeHtml === 'function') return window.prksEscapeHtml(s);
    if (s == null) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function prksAnnotationCommentText(annObj) {
    if (!annObj || typeof annObj !== 'object') return '';
    const custom = annObj.custom && typeof annObj.custom === 'object' ? annObj.custom : null;
    // PRKS comment field (kept separate from EmbedPDF's own `custom.text`, which may contain extracted text).
    if (custom && typeof custom.prksComment === 'string') return custom.prksComment;
    // Fallback to standard PDF annotation contents (for comments authored outside PRKS / EmbedPDF defaults).
    if (typeof annObj.contents === 'string' && annObj.contents.trim()) return annObj.contents.trim();
    return '';
}

function prksPatchAnnotationListCacheAfterCommentSave(ctx, annId, commentVal) {
    const pdf = prksPdfRuntime(ctx);
    const c = pdf && pdf.annotationCache;
    if (!c || annId == null || annId === '') return;
    const sid = String(annId);
    const pools = [c.allItems, c.rawItems, c.items].filter(Array.isArray);
    for (const pool of pools) {
        for (const it of pool) {
            if (!it || typeof it !== 'object') continue;
            const id = it.id || it.uuid || it.annotationId || it._id;
            if (id == null || String(id) !== sid) continue;
            if (!it.custom || typeof it.custom !== 'object') it.custom = {};
            it.custom.prksComment = commentVal;
            it.contents = commentVal;
        }
    }
}

function prksShowWorkAnnotationsTab(ctx) {
    if (ctx && typeof window.prksRightPanelOwnedBy === 'function' && !window.prksRightPanelOwnedBy(ctx)) return false;
    const btn = document.querySelector('#right-panel .tab-btn[data-target="annotations"]');
    if (!btn) return false;
    if (btn.classList.contains('active')) return true;
    btn.click();
    return true;
}

window.closePdfAnnotationEditor = function (ctx) {
    const owner = prksPdfOwnerOrFocused(ctx);
    const pdf = prksPdfRuntime(owner);
    if (pdf) pdf.annotationEditorState = null;
    if (
        owner &&
        typeof window.prksRightPanelOwnedBy === 'function' &&
        !window.prksRightPanelOwnedBy(owner)
    ) return;
    const wrap = document.getElementById('pdf-annotation-editor');
    if (wrap) wrap.classList.add('hidden');
    const meta = document.getElementById('pdf-annotation-editor-meta');
    const txt = document.getElementById('pdf-annotation-editor-text');
    const hid = document.getElementById('pdf-annotation-editor-ann-id');
    const page = document.getElementById('pdf-annotation-editor-page-index');
    if (meta) meta.textContent = '';
    if (txt) txt.value = '';
    if (hid) hid.value = '';
    if (page) page.value = '';
};

window.openPdfAnnotationEditorByIndex = async function (idx, ctx) {
    const owner = prksPdfOwnerOrFocused(ctx);
    if (!owner || !prksShowWorkAnnotationsTab(owner)) return;
    const wrap = document.getElementById('pdf-annotation-editor');
    const meta = document.getElementById('pdf-annotation-editor-meta');
    const txt = document.getElementById('pdf-annotation-editor-text');
    const hid = document.getElementById('pdf-annotation-editor-ann-id');
    const page = document.getElementById('pdf-annotation-editor-page-index');
    if (!wrap || !meta || !txt || !hid || !page) return;

    const pdf = prksPdfRuntime(owner);
    const c = pdf && pdf.annotationCache;
    if (!c || !Array.isArray(c.items) || c.items[idx] == null) return;
    const item = c.items[idx];
    const annId = item.id || item.uuid || item.annotationId || item._id;
    const pageIndex = item.pageIndex ?? item.page ?? item.pageNumber ?? item.page_index;
    if (!annId) return;

    try {
        const viewer = prksPdfViewer(owner);
        const annObj = prksFindViewerAnnotation(viewer, annId) || item;
        const comment = prksAnnotationCommentText(annObj);
        hid.value = String(annId);
        page.value = pageIndex != null ? String(pageIndex) : '';
        txt.value = comment;
        const pageDisp = pageIndex != null && pageIndex !== '' ? Number(pageIndex) + 1 : '?';
        const type = (typeof annotationTypeLabel === 'function' ? annotationTypeLabel(annObj || item) : '') || 'Annotation';
        meta.textContent = `Page ${pageDisp} · ${type}`;
        wrap.classList.remove('hidden');
        const editorState = {
            annId: String(annId),
            pageIndex: pageIndex != null ? Number(pageIndex) : null,
            docId: viewer && typeof viewer.getDocumentId === 'function' ? viewer.getDocumentId() : null,
            custom: annObj && annObj.custom && typeof annObj.custom === 'object' ? annObj.custom : {},
        };
        if (pdf) pdf.annotationEditorState = editorState;
    } catch (_e) {}
};

window.openPdfAnnotationEditorById = async function (ctxOrId, maybeId) {
    let owner = null;
    let annId = ctxOrId;
    if (ctxOrId && typeof ctxOrId.getResource === 'function') {
        owner = ctxOrId;
        annId = maybeId;
    } else {
        owner = prksPdfOwnerOrFocused();
    }
    if (annId == null || annId === '') return;
    if (!owner || !prksShowWorkAnnotationsTab(owner)) return;
    const id = String(annId);
    const pdf = prksPdfRuntime(owner);
    const c = pdf && pdf.annotationCache;
    const items = c && Array.isArray(c.items) ? c.items : [];
    const idx = items.findIndex((item) => {
        const itemId = item && (item.id || item.uuid || item.annotationId || item._id);
        return itemId != null && String(itemId) === id;
    });
    if (idx >= 0 && typeof window.openPdfAnnotationEditorByIndex === 'function') {
        await window.openPdfAnnotationEditorByIndex(idx, owner);
        return;
    }
    const wrap = document.getElementById('pdf-annotation-editor');
    const meta = document.getElementById('pdf-annotation-editor-meta');
    const txt = document.getElementById('pdf-annotation-editor-text');
    const hid = document.getElementById('pdf-annotation-editor-ann-id');
    const page = document.getElementById('pdf-annotation-editor-page-index');
    if (!wrap || !meta || !txt || !hid || !page) return;
    try {
        const viewer = prksPdfViewer(owner);
        const annObj = prksFindViewerAnnotation(viewer, id);
        if (!annObj) return;
        const comment = prksAnnotationCommentText(annObj);
        const pageIndex = prksPageIndexFromAnnotationObject(annObj);
        hid.value = id;
        page.value = Number.isFinite(pageIndex) ? String(pageIndex) : '';
        txt.value = comment;
        const pageDisp = Number.isFinite(pageIndex) ? pageIndex + 1 : '?';
        const type =
            (typeof annotationTypeLabel === 'function' ? annotationTypeLabel(annObj) : '') ||
            'Annotation';
        meta.textContent = `Page ${pageDisp} · ${type}`;
        wrap.classList.remove('hidden');
        const editorState = {
            annId: id,
            pageIndex: Number.isFinite(pageIndex) ? pageIndex : null,
            docId: viewer && typeof viewer.getDocumentId === 'function' ? viewer.getDocumentId() : null,
            custom: annObj && annObj.custom && typeof annObj.custom === 'object' ? annObj.custom : {},
        };
        if (pdf) pdf.annotationEditorState = editorState;
    } catch (_e) {}
};

window.deletePdfAnnotationFromEditor = async function () {
    if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) return;
    const owner = prksPdfOwnerOrFocused();
    const pdf = prksPdfRuntime(owner);
    const st = pdf && pdf.annotationEditorState;
    if (!st || !st.annId) return;
    const annId = st.annId;
    const confirmed =
        typeof prksConfirmDeletePdfAnnotation === 'function'
            ? await prksConfirmDeletePdfAnnotation()
            : window.confirm('Delete this annotation from the PDF?');
    if (!confirmed) return;
    if (owner && owner.destroyed) return;
    try {
        const viewer = prksPdfViewer(owner);
        if (!viewer || typeof viewer.deleteAnnotation !== 'function') return;
        await viewer.deleteAnnotation(annId);
        if (typeof window.closePdfAnnotationEditor === 'function') {
            window.closePdfAnnotationEditor(owner);
        }
    } catch (_e) {}
};

window.savePdfAnnotationComment = async function () {
    if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) return;
    const owner = prksPdfOwnerOrFocused();
    const pdf = prksPdfRuntime(owner);
    const st = pdf && pdf.annotationEditorState;
    const txt = document.getElementById('pdf-annotation-editor-text');
    const pageHid = document.getElementById('pdf-annotation-editor-page-index');
    if (!st || !txt) return;
    const val = (txt.value || '').trim();
    try {
        const viewer = prksPdfViewer(owner);
        if (!viewer || typeof viewer.updateAnnotation !== 'function') return;
        const liveAnn = prksFindViewerAnnotation(viewer, st.annId);
        let pageIdx = st.pageIndex;
        if (!Number.isFinite(Number(pageIdx)) || Number(pageIdx) < 0) {
            if (liveAnn) {
                const resolved = prksPageIndexFromAnnotationObject(liveAnn);
                if (Number.isFinite(resolved) && resolved >= 0) pageIdx = resolved;
            }
        }
        if (!Number.isFinite(Number(pageIdx)) || Number(pageIdx) < 0) {
            if (pageHid && String(pageHid.value).trim() !== '') {
                const n = Number(pageHid.value);
                if (Number.isFinite(n) && n >= 0) pageIdx = n;
            }
        }
        if (!Number.isFinite(Number(pageIdx)) || Number(pageIdx) < 0) {
            return;
        }
        const baseCustom =
            liveAnn && liveAnn.custom && typeof liveAnn.custom === 'object'
                ? liveAnn.custom
                : st.custom && typeof st.custom === 'object'
                  ? st.custom
                  : {};
        const patch = {
            custom: Object.assign({}, baseCustom, { prksComment: val }),
            contents: val,
        };
        viewer.updateAnnotation(st.annId, patch);
        prksPatchAnnotationListCacheAfterCommentSave(owner, st.annId, val);
        if (typeof window.applyCachedAnnotationListToPanel === 'function') {
            window.applyCachedAnnotationListToPanel(owner);
        }
        if (pdf && typeof pdf.flushAnnotations === 'function') {
            void pdf.flushAnnotations();
        }
    } catch (_e) {}
};

/** PDF page point for scrollToPage (EmbedPDF `Rect`: origin + size). */
function annotationScrollPagePoint(ann) {
    if (!ann || typeof ann !== 'object') return null;
    const pickPoint = (rect) => {
        if (!rect || !rect.origin || !rect.size) return null;
        const w = Number(rect.size.width);
        const h = Number(rect.size.height);
        const ox = Number(rect.origin.x);
        const oy = Number(rect.origin.y);
        if (!Number.isFinite(w) || !Number.isFinite(h) || !Number.isFinite(ox) || !Number.isFinite(oy)) return null;
        return {
            x: ox + w / 2,
            y: oy + Math.min(h * 0.28, 48),
        };
    };
    const fromRect = pickPoint(ann.rect);
    if (fromRect) return fromRect;
    const segs = ann.segmentRects;
    if (Array.isArray(segs) && segs.length > 0) {
        let minX = Infinity;
        let minY = Infinity;
        let maxX = -Infinity;
        let maxY = -Infinity;
        for (const r of segs) {
            if (!r || !r.origin || !r.size) continue;
            const ox = Number(r.origin.x);
            const oy = Number(r.origin.y);
            const w = Number(r.size.width);
            const h = Number(r.size.height);
            if (!Number.isFinite(ox) || !Number.isFinite(oy) || !Number.isFinite(w) || !Number.isFinite(h)) continue;
            minX = Math.min(minX, ox);
            minY = Math.min(minY, oy);
            maxX = Math.max(maxX, ox + w);
            maxY = Math.max(maxY, oy + h);
        }
        if (Number.isFinite(minX) && Number.isFinite(minY) && maxX > minX && maxY > minY) {
            return {
                x: (minX + maxX) / 2,
                y: minY + (maxY - minY) * 0.28,
            };
        }
    }
    return null;
}

function prksPageIndexFromAnnotationObject(obj) {
    if (!obj || typeof obj !== 'object') return NaN;
    let p = obj.pageIndex ?? obj.page;
    if (p === undefined && typeof obj.pageNumber === 'number') {
        p = obj.pageNumber - 1;
    }
    if (p === undefined || p === null) return NaN;
    const n = Number(p);
    return Number.isFinite(n) && n >= 0 ? n : NaN;
}

window.jumpToPdfAnnotationByIndex = async (idx, ctx) => {
    const owner = prksPdfOwnerOrFocused(ctx);
    const pdf = prksPdfRuntime(owner);
    const c = pdf && pdf.annotationCache;
    if (!c || !Array.isArray(c.items) || c.items[idx] == null) return;
    const item = c.items[idx];
    const id = item.id || item.uuid || item.annotationId || item._id;
    const pageIndex = item.pageIndex ?? item.page ?? item.pageNumber ?? item.page_index;
    await window.jumpToPdfAnnotation(id, pageIndex, item, owner);
};

/**
 * Rows for CodeMirror hints: { id, displayText } from the current annotation list cache.
 */
window.prksGetPdfAnnotationHintList = function (ctx) {
    const owner = prksPdfOwnerOrFocused(ctx);
    const pdf = prksPdfRuntime(owner);
    if (pdf && typeof pdf.getAnnotationHints === 'function') return pdf.getAnnotationHints();
    return [];
};

/**
 * Jump from markdown preview / notes link to a PDF annotation by id (cache first, then viewer lookup).
 */
window.prksJumpToPdfAnnotationFromNotes = async function (annId, ctx) {
    if (annId == null || annId === '') return;
    const id = String(annId);
    const owner = prksPdfOwnerOrFocused(ctx);
    if (!prksPdfViewer(owner)) return;
    const pdf = prksPdfRuntime(owner);
    const c = pdf && pdf.annotationCache;
    const searchPools = [];
    if (c && Array.isArray(c.allItems)) searchPools.push(c.allItems);
    if (c && Array.isArray(c.items)) searchPools.push(c.items);
    if (c && Array.isArray(c.rawItems)) searchPools.push(c.rawItems);
    for (const pool of searchPools) {
        for (const item of pool) {
            const iid = item && (item.id || item.uuid || item.annotationId || item._id);
            if (iid != null && String(iid) === id) {
                const pageIndex = prksPageIndexFromAnnotationObject(item);
                if (Number.isFinite(pageIndex)) {
                    await window.jumpToPdfAnnotation(id, pageIndex, item, owner);
                    return;
                }
            }
        }
    }
    try {
        const annObj = prksFindViewerAnnotation(prksPdfViewer(owner), id);
        if (annObj) {
            const pi = prksPageIndexFromAnnotationObject(annObj);
            if (Number.isFinite(pi)) {
                await window.jumpToPdfAnnotation(id, pi, annObj, owner);
            }
        }
    } catch (_e) {}
};

/**
 * EmbedPDF: plugin id is `annotation` (not annotation-engine). Selection is
 * `provides().forDocument(docId).selectAnnotation(pageIndex, id)`; scroll via
 * `scroll.forDocument(docId).scrollToPage({ pageNumber })` (pageNumber is 1-based).
 * Optional `annItem` supplies `pageCoordinates` + keeps the target in the upper part of the PDF pane (above the notes split) via low `alignY`.
 */
window.jumpToPdfAnnotation = async (id, pageIndex, _annItem, ctx) => {
    const owner = prksPdfOwnerOrFocused(ctx);
    if (!prksPdfViewer(owner) || id == null || id === '') return;
    const annId = String(id);
    try {
        const viewer = prksPdfViewer(owner);
        const pi = pageIndex !== undefined && pageIndex !== null ? Number(pageIndex) : NaN;
        if (typeof viewer.jumpToAnnotation === 'function') {
            viewer.jumpToAnnotation(annId, Number.isFinite(pi) ? pi : undefined);
        }
    } catch (err) {
        console.error('Jump to annotation failed', err);
    }
};

function renderAnnotationFallbackList(items, docId = null, workId = null, ctx) {
    const owner = prksPdfOwnerOrFocused(ctx);
    const pdf = prksPdfRuntime(owner);
    const resolvedWorkId =
        workId != null && workId !== ''
            ? String(workId)
            : pdf && pdf.workId
              ? String(pdf.workId)
              : owner && owner.getEntity
                ? (function () {
                      const w = owner.getEntity('work');
                      return w && w.id != null ? String(w.id) : null;
                  })()
                : null;
    const sorted = sortAnnotationsByPage(Array.isArray(items) ? items : []);
    const list = sorted.filter(prksIsUserMarkupAnnotation);
    const cache = {
        allItems: sorted,
        rawItems: sorted,
        items: list,
        docId: docId != null && docId !== '' ? docId : null,
        workId: resolvedWorkId,
    };
    if (pdf) pdf.annotationCache = cache;
    if (!prksIsFocusedPdfCtx(owner)) return;
    const target = document.getElementById('annotation-fallback-list');
    if (!target) return;

    const now = new Date().toLocaleTimeString();
    const count = list.length;
    const info = docId ? `ID: ${docId.substring(0, 8)}...` : 'No ID';
    const statusHtml = `<div class="annotation-list-status">Last sync: ${escapeHtml(now)} (${count} found, ${escapeHtml(info)})</div>`;

    if (!list.length) {
        target.innerHTML =
            statusHtml + '<p class="annotations-tab__empty">No annotations loaded yet.</p>';
        target.onclick = null;
        return;
    }
    const html = list.map((item, idx) => {
        const text = annotationToText(item) || `Annotation ${idx + 1}`;
        const prksComment =
            item && item.custom && typeof item.custom === 'object' && typeof item.custom.prksComment === 'string'
                ? item.custom.prksComment.trim()
                : '';
        const page = item.pageIndex ?? item.page ?? item.pageNumber ?? item.page_index;
        const pageDisplay = page !== undefined ? Number(page) + 1 : '?';
        const pageLabel = page !== undefined ? `Page ${pageDisplay}` : 'Unknown page';

        // If the main label is already the comment, don't repeat it as secondary.
        const commentHtml =
            prksComment && prksComment !== text ? `<div class="annotation-row__comment">${escapeHtml(prksComment)}</div>` : '';

        return `<div class="annotation-row" data-ann-idx="${idx}" role="listitem" tabindex="0">
<div class="annotation-row__header">
<button type="button" class="annotation-row__page-jump">${escapeHtml(pageLabel)}</button>
<button type="button" class="annotation-row__copy-link" title="Copy link to this PDF annotation for your notes">Copy link</button>
<button type="button" class="annotation-row__edit-comment">Edit/Add comment</button>
<button type="button" class="annotation-row__delete">Delete</button>
</div>
<button type="button" class="annotation-row__jump">
<span class="annotation-row__text">${escapeHtml(text)}</span>
</button>
${commentHtml}
</div>`;
    }).join('');
    target.innerHTML = statusHtml + html;

    target.onclick = async (e) => {
        const row = e.target.closest('.annotation-row');
        if (!row || !target.contains(row)) return;
        const idx = Number(row.getAttribute('data-ann-idx'));
        if (!Number.isFinite(idx)) return;
        e.preventDefault();
        if (e.target && e.target.closest && e.target.closest('.annotation-row__edit-comment')) {
            if (typeof window.openPdfAnnotationEditorByIndex === 'function') {
                void window.openPdfAnnotationEditorByIndex(idx, owner);
            }
            return;
        }
        if (e.target && e.target.closest && e.target.closest('.annotation-row__delete')) {
            if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) return;
            const cache = pdf && pdf.annotationCache;
            const rowItem = cache && Array.isArray(cache.items) ? cache.items[idx] : null;
            const annId = rowItem && (rowItem.id || rowItem.uuid || rowItem.annotationId || rowItem._id);
            if (!annId) return;
            const confirmed =
                typeof prksConfirmDeletePdfAnnotation === 'function'
                    ? await prksConfirmDeletePdfAnnotation()
                    : window.confirm('Delete this annotation from the PDF?');
            if (!confirmed) return;
            if (owner && owner.destroyed) return;
            const viewer = prksPdfViewer(owner);
            if (viewer && typeof viewer.deleteAnnotation === 'function') {
                try {
                    await viewer.deleteAnnotation(String(annId));
                    if (typeof window.closePdfAnnotationEditor === 'function') {
                        const st = pdf && pdf.annotationEditorState;
                        if (st && String(st.annId) === String(annId)) {
                            window.closePdfAnnotationEditor(owner);
                        }
                    }
                } catch (_e) {}
            }
            return;
        }
        if (e.target && e.target.closest && e.target.closest('.annotation-row__copy-link')) {
            const cache = pdf && pdf.annotationCache;
            const rowItem = cache && Array.isArray(cache.items) ? cache.items[idx] : null;
            const annId = rowItem && (rowItem.id || rowItem.uuid || rowItem.annotationId || rowItem._id);
            if (!annId) return;
            const base = annotationToText(rowItem) || `Annotation`;
            const pageIndex = rowItem.pageIndex ?? rowItem.page ?? rowItem.pageNumber ?? rowItem.page_index;
            const pageDisp = pageIndex !== undefined && pageIndex !== null ? Number(pageIndex) + 1 : null;
            const alreadyHasPage =
                typeof base === 'string' && /\s-\s*p\.\s*\d+/i.test(base);
            const label =
                pageDisp != null && Number.isFinite(pageDisp) && !alreadyHasPage
                    ? `${base} - p. ${pageDisp}`
                    : base;
            const wikiLink = prksBuildPdfAnnWikiLink(annId, label);
            const btn = e.target.closest('.annotation-row__copy-link');
            try {
                await prksCopyTextToClipboard(wikiLink);
                if (typeof prksFlashButtonLabel === 'function') {
                    prksFlashButtonLabel(btn, true, { successLabel: 'Copied', errorLabel: 'Copy failed', restoreMs: 1200 });
                }
            } catch (_e) {
                if (typeof prksFlashButtonLabel === 'function') {
                    prksFlashButtonLabel(btn, false, { successLabel: 'Copied', errorLabel: 'Copy failed', restoreMs: 1200 });
                }
            }
            return;
        }
        if (e.target.closest('.annotation-row__jump') || e.target.closest('.annotation-row__page-jump')) {
            const cache = pdf && pdf.annotationCache;
            const st = pdf && pdf.annotationEditorState;
            const rowItem = cache && Array.isArray(cache.items) ? cache.items[idx] : null;
            const rowAnnId = rowItem && (rowItem.id || rowItem.uuid || rowItem.annotationId || rowItem._id);
            if (st && rowAnnId != null && String(rowAnnId) !== String(st.annId)) {
                if (typeof window.closePdfAnnotationEditor === 'function') {
                    window.closePdfAnnotationEditor(owner);
                }
            }
            void window.jumpToPdfAnnotationByIndex(idx, owner);
        }
    };
}

window.applyCachedAnnotationListToPanel = function applyCachedAnnotationListToPanel(ctx) {
    const owner = prksPdfOwnerOrFocused(ctx);
    if (
        owner &&
        typeof window.prksRightPanelOwnedBy === 'function' &&
        !window.prksRightPanelOwnedBy(owner)
    ) return;
    const pdf = prksPdfRuntime(owner);
    const c = pdf && pdf.annotationCache;
    if (!c) return;
    const src = Array.isArray(c.rawItems) ? c.rawItems : c.items;
    if (!Array.isArray(src)) return;
    renderAnnotationFallbackList(src, c.docId, c.workId, owner);
};

function prksFormatSyncClock(tsMs) {
    if (!Number.isFinite(tsMs) || tsMs <= 0) return '';
    try {
        return new Date(tsMs).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch (_e) {
        return '';
    }
}

function prksEnsureAnnotationBeforeUnloadGuard() {
    if (window.__prksAnnotationBeforeUnloadBound) return;
    window.__prksAnnotationBeforeUnloadBound = true;
    window.addEventListener('beforeunload', (e) => {
        try {
            if (typeof window.prksHasPendingWorkAnnotationSync !== 'function') return;
            if (!window.prksHasPendingWorkAnnotationSync()) return;
            e.preventDefault();
            e.returnValue = '';
        } catch (_err) {}
    });
}

async function setupAnnotationPersistence(ctx, runtime, workId, viewer, setupToken) {
    const generation = ctx && typeof ctx.generation === 'number' ? ctx.generation : undefined;
    if (!viewer || typeof viewer.saveCopy !== 'function' || typeof viewer.getAnnotations !== 'function') {
        if (runtime) runtime._persistenceSetupStarted = false;
        return;
    }
    if (ctx && typeof ctx.clearTimer === 'function') ctx.clearTimer('annotationSyncInterval');

    // Pinned to the exact viewer instance + setup generation this call was
    // started for (AGENTS.md "Make annotation persistence viewer-identity-
    // safe"): an async setup must never install/keep running for a runtime
    // that has since moved on to a different viewer.
    function stillLive() {
        return typeof prksPdfPersistenceStillLive === 'function'
            ? prksPdfPersistenceStillLive(ctx, generation, runtime, viewer, setupToken)
            : !!(runtime && !runtime._destroyed);
    }

    // Setup-time eligibility: stricter than stillLive() above. An async
    // setup begun while the runtime was 'work'/online must re-check, after
    // every await boundary and immediately before installing the worker,
    // that it is still 'work'/online -- a reconcile may have flipped the
    // live viewer into preview mode (or PRKS may have gone offline) while
    // this GET/JSON-parse was in flight. This is *not* the same predicate an
    // already-installed worker keeps using once paused (AGENTS.md
    // "persistence setup cannot install an active worker after an offline
    // transition"; "an already-installed worker should remain live while
    // paused offline").
    function setupEligible() {
        return typeof prksPdfPersistenceSetupEligible === 'function'
            ? prksPdfPersistenceSetupEligible(ctx, generation, runtime, viewer, setupToken)
            : stillLive();
    }

    // Abandon this setup attempt without installing a worker and without
    // destroying the viewer -- reset the started-flag so a later online
    // reconcile can call prksEnsureAnnotationPersistence() again.
    function abandonSetup() {
        runtime._persistenceSetupStarted = false;
    }

    const syncState = runtime.syncState || {
        workId: String(workId),
        pendingChanges: false,
        inFlight: false,
        lastError: '',
        lastSuccessAt: 0,
        lastConfirmedToken: '',
        activeToken: '',
        localMutationSeen: false,
    };
    runtime.syncState = syncState;
    prksEnsureAnnotationBeforeUnloadGuard();

    function renderSyncIndicator() {
        try {
            if (!stillLive()) return;
            const el = ctx && ctx.query ? ctx.query('[data-prks-role="annotation-sync-status"]') : null;
            if (!el) return;
            el.classList.remove(
                'work-annotation-sync-status--hidden',
                'work-annotation-sync-status--saving',
                'work-annotation-sync-status--saved',
                'work-annotation-sync-status--error'
            );
            if (syncState.inFlight || syncState.pendingChanges) {
                el.classList.add('work-annotation-sync-status--saving');
                el.textContent = syncState.lastError ? 'Sync retry pending...' : 'PDF annotations syncing...';
                return;
            }
            if (syncState.lastError) {
                el.classList.add('work-annotation-sync-status--error');
                el.textContent = 'PDF annotations sync failed';
                return;
            }
            el.classList.add('work-annotation-sync-status--saved');
            const t = prksFormatSyncClock(syncState.lastSuccessAt);
            el.textContent = t ? `PDF annotations saved at ${t}` : 'PDF annotations saved';
        } finally {
            if (ctx && ctx.tabId && typeof window.prksWorkspaceRefreshTabStatus === 'function') {
                window.prksWorkspaceRefreshTabStatus(ctx.tabId);
            }
        }
    }

    try {
        const savedRes = await prksRequest(`/api/works/${workId}/annotations`, { cache: 'no-store' }, {
            dedupe: false,
            retry: false,
            freshForMs: 0,
        });
        if (!setupEligible()) {
            abandonSetup();
            return;
        }
        const savedData = await savedRes.json();
        if (!setupEligible()) {
            abandonSetup();
            return;
        }
        const saved = JSON.parse(savedData.annotations_json || '[]');
        if (Array.isArray(saved) && saved.length > 0) {
            runtime.annotationCache = {
                allItems: saved,
                rawItems: saved,
                items: saved,
                docId: 'DB',
                workId: String(workId),
            };
            renderAnnotationFallbackList(saved, 'DB', workId, ctx);
        }
    } catch (_e) {}
    if (!setupEligible()) {
        abandonSetup();
        return;
    }

    runtime.annotationCache = runtime.annotationCache || {
        allItems: [],
        rawItems: [],
        items: [],
        docId: viewer.getDocumentId ? viewer.getDocumentId() : null,
        workId: String(workId),
    };
    renderSyncIndicator();

    async function exportAndPersistPdfCopy(saveToken) {
        if (!stillLive()) return;
        const buffer = await viewer.saveCopy();
        if (!stillLive()) return;
        if (!buffer || !buffer.byteLength) return;
        const b64 = arrayBufferToBase64(buffer);
        if (!stillLive()) return;
        const pdfRes = await prksRequest(`/api/works/${workId}/pdf`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_b64: b64, save_token: saveToken }),
        }, {
            dedupe: false,
            retry: false,
            freshForMs: 0,
        });
        if (!pdfRes.ok) {
            throw new Error(`PDF save failed (${pdfRes.status})`);
        }
        if (typeof prksOfflineMarkEntityChanged === 'function') {
            prksOfflineMarkEntityChanged('work', workId);
        }
        if (typeof prksOfflineMarkPeopleChanged === 'function') {
            // Two independent effects on cached People: a Person's Work cards
            // show file_size_bytes, derived from the managed PDF on disk, and
            // the PDF-save backend can add 'Mentioned' roles from annotation
            // markup. Invalidate on success rather than trying to detect which.
            prksOfflineMarkPeopleChanged();
        }
    }

    async function runWorkAnnotationAndPdfPersistencePass(saveToken) {
        if (!stillLive()) return;
        await exportAndPersistPdfCopy(saveToken);
        if (!stillLive()) return;
        const itemsFound = prksViewerAnnotationObjects(viewer).filter(isLikelyAnnotationObject);
        const userItems = sortAnnotationsByPage(itemsFound.filter(prksIsUserMarkupAnnotation));
        const serialized = JSON.stringify(userItems);
        renderAnnotationFallbackList(itemsFound, viewer.getDocumentId ? viewer.getDocumentId() : null, workId, ctx);
        if (!stillLive()) return;
        const annRes = await prksRequest(`/api/works/${workId}/annotations`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ annotations_json: serialized, save_token: saveToken }),
        }, {
            dedupe: false,
            retry: false,
            freshForMs: 0,
        });
        if (!annRes.ok) {
            throw new Error(`Annotation save failed (${annRes.status})`);
        }
        if (typeof prksOfflineMarkEntityChanged === 'function') {
            prksOfflineMarkEntityChanged('work', workId);
        }
    }

    // PRKS may go offline mid-confirmation-loop (the persistence worker
    // pauses itself, but this poll loop only checked stillLive() before) --
    // this must stop issuing /save-confirm requests the instant that
    // happens, both before starting a new attempt and before continuing
    // through a backoff delay, and leave pendingChanges/lastConfirmedToken
    // untouched so the outer queue naturally resumes once reconnected.
    function pausedOffline() {
        return !!(worker && worker.paused);
    }

    async function confirmPersistedToken(saveToken) {
        const tries = 8;
        for (let attempt = 0; attempt < tries; attempt++) {
            if (!stillLive() || pausedOffline()) return false;
            try {
                const probe = await prksRequest(
                    `/api/works/${workId}/save-confirm?token=${encodeURIComponent(saveToken)}&t=${Date.now()}`,
                    { cache: 'no-store' },
                    { dedupe: false, retry: false, freshForMs: 0 }
                );
                if (!stillLive() || pausedOffline()) return false;
                if (probe.ok) {
                    const body = await probe.json().catch(() => ({}));
                    if (body && body.saved === true) return true;
                }
            } catch (_e) {}
            if (!stillLive() || pausedOffline()) return false;
            const wait = attempt < 2 ? 250 : attempt < 5 ? 450 : 800;
            await new Promise((r) => setTimeout(r, wait));
        }
        return false;
    }

    let queueRequested = false;
    let queueRunning = false;
    let queueDrainPromise = Promise.resolve();
    let worker = null;

    async function drainFlushQueue() {
        while (queueRequested) {
            if (worker && worker.destroyed) {
                queueRequested = false;
                break;
            }
            if (worker && worker.paused) {
                // PRKS is offline/reconnecting: leave pendingChanges/
                // queueRequested representing the unsynced state and stop --
                // worker.resume() requests exactly one flush once reachable
                // again. Never poll/retry against an unreachable server.
                break;
            }
            if (!stillLive()) {
                queueRequested = false;
                break;
            }
            queueRequested = false;
            syncState.inFlight = true;
            syncState.lastError = '';
            renderSyncIndicator();
            const saveToken = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
            syncState.activeToken = saveToken;
            try {
                await runWorkAnnotationAndPdfPersistencePass(saveToken);
                if (!stillLive()) break;
                const confirmed = await confirmPersistedToken(saveToken);
                if (!stillLive()) break;
                if (!confirmed) throw new Error('Server confirmation timeout');
                syncState.lastConfirmedToken = saveToken;
                syncState.lastSuccessAt = Date.now();
                syncState.pendingChanges = queueRequested;
                syncState.lastError = '';
            } catch (err) {
                if (stillLive() && !(worker && worker.destroyed)) {
                    syncState.pendingChanges = true;
                    syncState.lastError = err && err.message ? String(err.message) : 'Save failed';
                    if (worker && typeof worker.scheduleRetry === 'function') worker.scheduleRetry();
                }
            } finally {
                syncState.inFlight = false;
                renderSyncIndicator();
            }
        }
    }

    function requestFlush(_reason = 'manual') {
        if (worker && worker.destroyed) return undefined;
        if (!stillLive()) return undefined;
        syncState.pendingChanges = true;
        queueRequested = true;
        renderSyncIndicator();
        if (queueRunning) return queueDrainPromise;
        queueRunning = true;
        queueDrainPromise = drainFlushQueue().finally(() => {
            queueRunning = false;
            renderSyncIndicator();
        });
        return queueDrainPromise;
    }

    function onAnnotationEvent(evt) {
        if (worker && worker.destroyed) return;
        if (!stillLive()) return;
        if (!evt || evt.committed !== true) return;
        if (evt.kind !== 'create' && evt.kind !== 'update' && evt.kind !== 'delete') return;
        syncState.localMutationSeen = true;
        void requestFlush('annotation-event');
    }

    // Final eligibility gate, immediately before the worker is actually
    // installed: viewer identity, 'work' mode, and online connectivity must
    // all still hold. Do not destroy the viewer either way -- only decide
    // whether to install.
    if (!setupEligible()) {
        abandonSetup();
        return;
    }

    const installed = typeof prksInstallPdfAnnotationPersistenceIfCurrent === 'function'
        ? prksInstallPdfAnnotationPersistenceIfCurrent(ctx, generation, runtime, viewer, setupToken, function () {
            worker =
                typeof createPdfAnnotationPersistenceWorker === 'function'
                    ? createPdfAnnotationPersistenceWorker({
                          runtime: runtime,
                          setTimer: function (timerId) {
                              if (ctx && typeof ctx.setTimer === 'function') {
                                  ctx.setTimer('annotationPersistenceRetry', timerId);
                              }
                          },
                          clearTimer: function () {
                              if (ctx && typeof ctx.clearTimer === 'function') {
                                  ctx.clearTimer('annotationPersistenceRetry');
                              }
                          },
                          onFlush: function (reason) {
                              return requestFlush(reason);
                          },
                          hasPendingChanges: function () {
                              return !!(syncState && syncState.pendingChanges);
                          },
                          onDestroy: function () {
                              if (viewer && typeof viewer.offAnnotationEvent === 'function') {
                                  try {
                                      viewer.offAnnotationEvent(onAnnotationEvent);
                                  } catch (_e) {}
                              }
                          },
                      })
                    : {
                          destroyed: false,
                          paused: false,
                          requestFlush: requestFlush,
                          scheduleRetry: function () {},
                          pause: function () {
                              this.paused = true;
                          },
                          resume: function () {
                              this.paused = false;
                          },
                          flush: function () {
                              return requestFlush('manual');
                          },
                          destroy: function () {
                              this.destroyed = true;
                          },
                      };
            runtime.annotationPersistence = worker;
            runtime._flushAnnotationsImpl = async function () {
                if (!stillLive() || (worker && worker.destroyed)) return;
                try {
                    syncState.localMutationSeen = true;
                    await requestFlush('manual');
                } catch (_e) {}
            };
            if (typeof viewer.onAnnotationEvent === 'function') {
                viewer.onAnnotationEvent(onAnnotationEvent);
            }
        })
        : false;
    if (!installed) {
        abandonSetup();
        return;
    }
}

function prksPdfLastPageLocalKey(workId) {
    return 'prks.pdf.lastPage.' + workId;
}

const PRKS_PDF_LAST_PAGE_DEBOUNCE_MS = 900;

function createPdfLastPageController(work, runtime) {
    const workId = work && work.id;
    let debounceTimer = null;
    let alive = true;
    let persistOk = false;
    const openedAt = Date.now();

    function debounceClear() {
        if (debounceTimer != null) {
            clearTimeout(debounceTimer);
            debounceTimer = null;
        }
    }

    function persistPayload(s) {
        if (!s || s.workId !== workId) return;
        const p = s.pageNumber;
        const n = s.totalPages;
        if (!Number.isFinite(p) || p < 1) return;
        try {
            localStorage.setItem(
                prksPdfLastPageLocalKey(workId),
                JSON.stringify({
                    p: Math.floor(p),
                    n: Number.isFinite(n) ? Math.floor(n) : undefined,
                })
            );
        } catch (_e) {}
    }

    function persistNow() {
        if (!persistOk) return;
        const enabled =
            typeof window.prksGetPdfRememberLastPageEnabled === 'function' &&
            window.prksGetPdfRememberLastPageEnabled();
        if (!enabled) return;
        persistPayload(runtime && runtime.pageSession);
    }

    function persistDebounced() {
        if (!persistOk || !alive) return;
        const enabled =
            typeof window.prksGetPdfRememberLastPageEnabled === 'function' &&
            window.prksGetPdfRememberLastPageEnabled();
        if (!enabled) return;
        const sess = runtime && runtime.pageSession;
        if (!sess || sess.workId !== workId) return;
        debounceClear();
        debounceTimer = setTimeout(() => {
            debounceTimer = null;
            if (!persistOk || !alive) return;
            persistNow();
        }, PRKS_PDF_LAST_PAGE_DEBOUNCE_MS);
    }

    function parseStored() {
        try {
            const raw = localStorage.getItem(prksPdfLastPageLocalKey(workId));
            if (!raw) return null;
            const o = JSON.parse(raw);
            const p = o && o.p != null ? Number(o.p) : NaN;
            const n = o && o.n != null ? Number(o.n) : null;
            if (!Number.isFinite(p) || p < 1) return null;
            return { p: Math.floor(p), n: Number.isFinite(n) ? Math.floor(n) : null };
        } catch (_e) {
            return null;
        }
    }

    const stored = parseStored();
    const rememberOn =
        typeof window.prksGetPdfRememberLastPageEnabled !== 'function' ||
        window.prksGetPdfRememberLastPageEnabled();
    const initialPage = rememberOn && stored && stored.p > 1 ? stored.p : 1;
    if (initialPage <= 1) persistOk = true;
    if (runtime) {
        runtime.pageSession = {
            workId,
            pageNumber: initialPage,
            totalPages: stored && stored.n != null ? stored.n : undefined,
        };
    }

    const detach = () => {
        alive = false;
        debounceClear();
        persistNow();
    };

    return {
        initialPage,
        setViewer() {},
        persistNow,
        debounceClear,
        onPageChange(info) {
            if (!alive) return;
            if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
            const pn = info && info.pageNumber != null ? Number(info.pageNumber) : NaN;
            const tn = info && info.pageCount != null ? Number(info.pageCount) : NaN;
            if (!Number.isFinite(pn) || pn < 1) return;
            const target =
                Number.isFinite(tn) && tn > 0 ? Math.min(initialPage, tn) : initialPage;
            if (!persistOk) {
                if (target <= 1 || pn === target || pn > 1) persistOk = true;
                else return;
            }
            if (pn === 1 && target > 1 && Date.now() - openedAt < 8000) return;
            if (runtime) {
                runtime.pageSession = {
                    workId,
                    pageNumber: pn,
                    totalPages: Number.isFinite(tn) ? tn : undefined,
                };
            }
            persistDebounced();
        },
        detach,
    };
}

function prksDestroyWorkPdfViewer(ctx) {
    const owner = ctx || prksResolvePdfCtx();
    if (owner && typeof owner.clearTimer === 'function') owner.clearTimer('annotationSyncInterval');
    if (owner && typeof owner.clearResource === 'function') owner.clearResource('pdf');
}

/**
 * PRKS is offline (or still reconnecting) whenever the runtime is not
 * confirmed reachable. A Work PDF viewer mounted or reconciled in that state
 * runs with mutation disabled -- render/scroll/zoom/navigate stay available,
 * but highlight/underline/select-to-markup/comment/create/update/delete are
 * all gated by the same 'work'/'preview' interaction boundary inside the
 * vendor viewer (see PrksPdfViewerHandle.setMutationEnabled), and
 * annotation-sync persistence is paused rather than installed/kept running.
 * See AGENTS.md "Offline / PWA".
 */
function prksPdfDesiredMode() {
    return typeof prksOfflineRuntimeState === 'function' && prksOfflineRuntimeState() !== 'online' ? 'preview' : 'work';
}

function prksCurrentPdfPageNumber(runtime) {
    const sess = runtime && runtime.pageSession;
    const p = sess && sess.pageNumber != null ? Number(sess.pageNumber) : NaN;
    return Number.isFinite(p) && p >= 1 ? p : 1;
}

/**
 * Starts annotation-sync persistence for `runtime` at most once per viewer
 * instance. Guards against a reconcile calling this a second time while an
 * earlier async setup (its initial GET of previously-saved annotations) is
 * still in flight -- e.g. rapid offline->online->offline toggles.
 */
function prksEnsureAnnotationPersistence(ctx, runtime, workId, viewer, setupToken) {
    if (!runtime || runtime.annotationPersistence || runtime._persistenceSetupStarted) return;
    runtime._persistenceSetupStarted = true;
    void setupAnnotationPersistence(ctx, runtime, workId, viewer, setupToken);
}

/** Builds + awaits one viewer instance for a Work PDF; installs annotation persistence only in 'work' mode. */
async function prksMountPdfViewer(ctx, work, runtime, targetNode, initialPage, mode) {
    const generation = ctx.generation;
    const stale = function () {
        return typeof ctx.isCurrent === 'function' ? !ctx.isCurrent(generation) : !ctx.mounted;
    };
    const src =
        String(work.file_path || '') +
        (String(work.file_path || '').includes('?') ? '&' : '?') +
        'prksv=' +
        Date.now();
    const author = typeof getPrksAnnotationAuthor === 'function' ? getPrksAnnotationAuthor() : 'You';
    const typeMeta = typeof prksDocTypeMeta === 'function' ? prksDocTypeMeta(work.doc_type) : null;
    const viewer = await createPrksPdfViewer({
        target: targetNode,
        src,
        mode: mode,
        annotationAuthor: author,
        documentTitle: work.title || 'Document',
        documentTypeLabel: typeMeta && typeMeta.label ? typeMeta.label : '',
        documentTypeColor: typeMeta && typeMeta.color ? typeMeta.color : undefined,
        documentTypeBorder: typeMeta && typeMeta.border ? typeMeta.border : undefined,
        initialPage: initialPage,
        onPageChange: (info) => runtime.lastPage && runtime.lastPage.onPageChange(info),
        onAnnotationCommentRequest: (info) => {
            // Live-checked against the runtime's currently reconciled mode,
            // not the mode this mount started with -- a Work viewer that
            // began online and was later mutation-locked offline must not
            // still let a click open the comment editor.
            if (runtime.mode !== 'work' || !info || !info.annotationId) return;
            if (typeof window.openPdfAnnotationEditorById === 'function') {
                void window.openPdfAnnotationEditorById(ctx, info.annotationId);
            }
        },
        onError: (err) => console.error('PDF viewer failed', err),
    });
    if (stale() || (ctx.getResource ? ctx.getResource('pdf') !== runtime : false)) {
        if (viewer && typeof viewer.destroy === 'function') {
            try { viewer.destroy(); } catch (_e) {}
        }
        return null;
    }
    if (runtime.lastPage && typeof runtime.lastPage.setViewer === 'function') runtime.lastPage.setViewer(viewer);
    runtime.viewer = viewer;
    runtime.viewerSetupToken = (runtime.viewerSetupToken || 0) + 1;
    const setupToken = runtime.viewerSetupToken;
    // `mode` reflects the desired state when this async mount *started*;
    // createPrksPdfViewer() awaits engine/document load, so connectivity can
    // change mid-flight. Reconcile against the *current* desired mode before
    // publishing capability -- via the live mutation lock, never by
    // discarding this viewer/document and mounting another one -- so a
    // viewer that began mounting offline-preview but finished after PRKS
    // came back online (or vice versa) is never stuck on the stale mode.
    const desired = prksPdfDesiredMode();
    if (desired !== mode && typeof viewer.setMutationEnabled === 'function') {
        viewer.setMutationEnabled(desired === 'work');
    }
    runtime.mode = desired;
    if (desired === 'work') {
        prksEnsureAnnotationPersistence(ctx, runtime, work.id, viewer, setupToken);
    }
    return viewer;
}

export function initPdfViewerForWork(ctx, work) {
    if (!work || !work.file_path || !ctx) return;
    const _pdfGen = ctx.generation;
    const _pdfStale = function () {
        return typeof ctx.isCurrent === 'function' ? !ctx.isCurrent(_pdfGen) : !ctx.mounted;
    };
    const setupTimer = setTimeout(() => {
        if (ctx && ctx.timers && ctx.timers.get('pdfDeferredSetup') === setupTimer) {
            ctx.clearTimer('pdfDeferredSetup');
        }
        if (_pdfStale()) return;
        if (typeof ctx.clearResource === 'function') ctx.clearResource('pdf');
        const targetNode = ctx.query ? ctx.query('[data-prks-role="pdf-viewer"]') : null;
        if (!targetNode) return;
        targetNode.innerHTML = '';
        const runtime =
            typeof createWorkPdfRuntime === 'function'
                ? createWorkPdfRuntime({ workId: String(work.id) })
                : {
                      viewer: null,
                      workId: String(work.id),
                      pageSession: { workId: String(work.id), pageNumber: 1 },
                      annotationCache: { allItems: [], rawItems: [], items: [], docId: null, workId: String(work.id) },
                      syncState: { pendingChanges: false, inFlight: false },
                      hasPendingSync: function () { return false; },
                      flushAnnotations: async function () {},
                      flushLastPage: function () {},
                      getAnnotationHints: function () { return []; },
                      destroy: function () {},
                  };
        const lastPage = createPdfLastPageController(work, runtime);
        runtime.lastPage = lastPage;
        runtime.work = work;
        ctx.setResource('pdf', runtime, function () {
            runtime.destroy();
        });
        // Prime the service worker's whole-file PDF cache in the background (AGENTS.md
        // "PDF offline support"). The viewer itself loads progressively via Range
        // requests, which never populate that cache -- this plain GET is what lets a
        // previously opened PDF reopen offline. Best-effort only; never blocks or
        // affects the live viewer either way.
        if (typeof prksRequest === 'function' && work.file_path) {
            void prksRequest(String(work.file_path), {}, { priority: 'background' }).catch(function () {});
        }
        void prksMountPdfViewer(ctx, work, runtime, targetNode, lastPage.initialPage, prksPdfDesiredMode()).catch((err) => {
            console.error('Failed to load PDF viewer', err);
        });
    }, 100);
    if (ctx && typeof ctx.setTimer === 'function') ctx.setTimer('pdfDeferredSetup', setupTimer);
}

/**
 * Reconciles a mounted Work PDF viewer's live mutation capability against
 * current connectivity. Never destroys/recreates the viewer or document
 * (PrksPdfViewerHandle.setMutationEnabled toggles the same 'work'/'preview'
 * boundary in place) -- an annotation created while online must never
 * disappear merely because connectivity dropped before it finished saving.
 * Pausing/resuming the same annotation-sync worker (rather than tearing it
 * down) keeps pendingChanges/unsaved state intact across the transition.
 */
function prksReconcilePdfMutationMode(ctx, runtime) {
    if (!ctx || !runtime || runtime._destroyed || !runtime.viewer) return;
    const desired = prksPdfDesiredMode();
    if (runtime.mode === desired) return;
    if (typeof runtime.viewer.setMutationEnabled === 'function') {
        runtime.viewer.setMutationEnabled(desired === 'work');
    }
    runtime.mode = desired;
    if (desired === 'work') {
        if (runtime.annotationPersistence && typeof runtime.annotationPersistence.resume === 'function') {
            runtime.annotationPersistence.resume();
        } else {
            // Offline-created viewer that never installed persistence yet
            // (nothing to resume) -- install it for the first time now.
            prksEnsureAnnotationPersistence(ctx, runtime, runtime.workId, runtime.viewer, runtime.viewerSetupToken);
        }
    } else if (runtime.annotationPersistence && typeof runtime.annotationPersistence.pause === 'function') {
        runtime.annotationPersistence.pause();
    }
}

if (typeof prksOfflineRuntimeSubscribe === 'function') {
    prksOfflineRuntimeSubscribe(function () {
        if (typeof prksForEachLiveTabContext !== 'function') return;
        prksForEachLiveTabContext(function (ctx) {
            const runtime = ctx && ctx.getResource ? ctx.getResource('pdf') : null;
            if (runtime) prksReconcilePdfMutationMode(ctx, runtime);
        });
    });
}
