/**
 * PDF viewer and annotation integration — loaded on demand when opening a work with a PDF.
 */

import { createPrksPdfViewer } from '/js/pdf-viewer-runtime.js';

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
    try {
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            await navigator.clipboard.writeText(s);
            return;
        }
    } catch (_e) {}

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
        document.execCommand('copy');
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

function prksPatchAnnotationListCacheAfterCommentSave(annId, commentVal) {
    const c = window.__prksAnnotationListCache;
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

function prksShowWorkAnnotationsTab() {
    const btn = document.querySelector('#right-panel .tab-btn[data-target="annotations"]');
    if (!btn || btn.classList.contains('active')) return;
    btn.click();
}

window.closePdfAnnotationEditor = function () {
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
    window.__prksPdfAnnotationEditorState = null;
};

window.openPdfAnnotationEditorByIndex = async function (idx) {
    prksShowWorkAnnotationsTab();
    const wrap = document.getElementById('pdf-annotation-editor');
    const meta = document.getElementById('pdf-annotation-editor-meta');
    const txt = document.getElementById('pdf-annotation-editor-text');
    const hid = document.getElementById('pdf-annotation-editor-ann-id');
    const page = document.getElementById('pdf-annotation-editor-page-index');
    if (!wrap || !meta || !txt || !hid || !page) return;

    const c = window.__prksAnnotationListCache;
    if (!c || !Array.isArray(c.items) || c.items[idx] == null) return;
    const item = c.items[idx];
    const annId = item.id || item.uuid || item.annotationId || item._id;
    const pageIndex = item.pageIndex ?? item.page ?? item.pageNumber ?? item.page_index;
    if (!annId) return;

    try {
        const viewer = window.currentPdfViewer;
        const annObj = prksFindViewerAnnotation(viewer, annId) || item;
        const comment = prksAnnotationCommentText(annObj);
        hid.value = String(annId);
        page.value = pageIndex != null ? String(pageIndex) : '';
        txt.value = comment;
        const pageDisp = pageIndex != null && pageIndex !== '' ? Number(pageIndex) + 1 : '?';
        const type = (typeof annotationTypeLabel === 'function' ? annotationTypeLabel(annObj || item) : '') || 'Annotation';
        meta.textContent = `Page ${pageDisp} · ${type}`;
        wrap.classList.remove('hidden');
        window.__prksPdfAnnotationEditorState = {
            annId: String(annId),
            pageIndex: pageIndex != null ? Number(pageIndex) : null,
            docId: viewer && typeof viewer.getDocumentId === 'function' ? viewer.getDocumentId() : null,
            custom: annObj && annObj.custom && typeof annObj.custom === 'object' ? annObj.custom : {},
        };
    } catch (_e) {}
};

window.openPdfAnnotationEditorById = async function (annId) {
    if (annId == null || annId === '') return;
    prksShowWorkAnnotationsTab();
    const id = String(annId);
    const c = window.__prksAnnotationListCache;
    const items = c && Array.isArray(c.items) ? c.items : [];
    const idx = items.findIndex((item) => {
        const itemId = item && (item.id || item.uuid || item.annotationId || item._id);
        return itemId != null && String(itemId) === id;
    });
    if (idx >= 0 && typeof window.openPdfAnnotationEditorByIndex === 'function') {
        await window.openPdfAnnotationEditorByIndex(idx);
        return;
    }
    const wrap = document.getElementById('pdf-annotation-editor');
    const meta = document.getElementById('pdf-annotation-editor-meta');
    const txt = document.getElementById('pdf-annotation-editor-text');
    const hid = document.getElementById('pdf-annotation-editor-ann-id');
    const page = document.getElementById('pdf-annotation-editor-page-index');
    if (!wrap || !meta || !txt || !hid || !page) return;
    try {
        const viewer = window.currentPdfViewer;
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
        window.__prksPdfAnnotationEditorState = {
            annId: id,
            pageIndex: Number.isFinite(pageIndex) ? pageIndex : null,
            docId: viewer && typeof viewer.getDocumentId === 'function' ? viewer.getDocumentId() : null,
            custom: annObj && annObj.custom && typeof annObj.custom === 'object' ? annObj.custom : {},
        };
    } catch (_e) {}
};

window.deletePdfAnnotationFromEditor = async function () {
    const st = window.__prksPdfAnnotationEditorState;
    if (!st || !st.annId) return;
    if (!window.confirm('Delete this annotation from the PDF?')) return;
    try {
        const viewer = window.currentPdfViewer;
        if (!viewer || typeof viewer.deleteAnnotation !== 'function') return;
        await viewer.deleteAnnotation(st.annId);
        if (typeof window.closePdfAnnotationEditor === 'function') {
            window.closePdfAnnotationEditor();
        }
    } catch (_e) {}
};

window.savePdfAnnotationComment = async function () {
    const st = window.__prksPdfAnnotationEditorState;
    const txt = document.getElementById('pdf-annotation-editor-text');
    const pageHid = document.getElementById('pdf-annotation-editor-page-index');
    if (!st || !txt) return;
    const val = (txt.value || '').trim();
    try {
        const viewer = window.currentPdfViewer;
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
        // Write both:
        // - `custom.prksComment` for PRKS UI (avoids auto-filled extracted text)
        // - `contents` for standard PDF viewers (Okular, etc.)
        const patch = {
            custom: Object.assign({}, baseCustom, { prksComment: val }),
            contents: val,
        };
        viewer.updateAnnotation(st.annId, patch);
        prksPatchAnnotationListCacheAfterCommentSave(st.annId, val);
        if (typeof window.applyCachedAnnotationListToPanel === 'function') {
            window.applyCachedAnnotationListToPanel();
        }
        if (typeof window.__prksFlushWorkAnnotationPersistence === 'function') {
            void window.__prksFlushWorkAnnotationPersistence();
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

window.jumpToPdfAnnotationByIndex = async (idx) => {
    const c = window.__prksAnnotationListCache;
    if (!c || !Array.isArray(c.items) || c.items[idx] == null) return;
    const item = c.items[idx];
    const id = item.id || item.uuid || item.annotationId || item._id;
    const pageIndex = item.pageIndex ?? item.page ?? item.pageNumber ?? item.page_index;
    await window.jumpToPdfAnnotation(id, pageIndex, item);
};

/**
 * Rows for CodeMirror hints: { id, displayText } from the current annotation list cache.
 */
window.prksGetPdfAnnotationHintList = function () {
    const c = window.__prksAnnotationListCache;
    if (!c || !Array.isArray(c.items)) return [];
    const out = [];
    for (let idx = 0; idx < c.items.length; idx++) {
        const item = c.items[idx];
        if (!item || typeof item !== 'object') continue;
        const id = item.id || item.uuid || item.annotationId || item._id;
        if (id == null || id === '') continue;
        const sid = String(id);
        const page = item.pageIndex ?? item.page ?? item.pageNumber ?? item.page_index;
        const pageDisp = page !== undefined && page !== null ? Number(page) + 1 : '?';
        const text = annotationToText(item) || `Annotation ${idx + 1}`;
        const short = String(text).replace(/\s+/g, ' ').trim().slice(0, 48);
        out.push({
            id: sid,
            displayText: `${short} - p. ${pageDisp}`,
        });
    }
    return out;
};

/**
 * Jump from markdown preview / notes link to a PDF annotation by id (cache first, then viewer lookup).
 */
window.prksJumpToPdfAnnotationFromNotes = async function (annId) {
    if (annId == null || annId === '') return;
    const id = String(annId);
    if (!window.currentPdfViewer) return;
    const c = window.__prksAnnotationListCache;
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
                    await window.jumpToPdfAnnotation(id, pageIndex, item);
                    return;
                }
            }
        }
    }
    try {
        const annObj = prksFindViewerAnnotation(window.currentPdfViewer, id);
        if (annObj) {
            const pi = prksPageIndexFromAnnotationObject(annObj);
            if (Number.isFinite(pi)) {
                await window.jumpToPdfAnnotation(id, pi, annObj);
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
window.jumpToPdfAnnotation = async (id, pageIndex, _annItem) => {
    if (!window.currentPdfViewer || id == null || id === '') return;
    const annId = String(id);
    try {
        const viewer = window.currentPdfViewer;
        const pi = pageIndex !== undefined && pageIndex !== null ? Number(pageIndex) : NaN;
        if (typeof viewer.jumpToAnnotation === 'function') {
            viewer.jumpToAnnotation(annId, Number.isFinite(pi) ? pi : undefined);
        }
    } catch (err) {
        console.error('Jump to annotation failed', err);
    }
};

function renderAnnotationFallbackList(items, docId = null, workId = null) {
    const resolvedWorkId =
        workId != null && workId !== ''
            ? String(workId)
            : window.currentWork && window.currentWork.id != null
              ? String(window.currentWork.id)
              : null;
    const sorted = sortAnnotationsByPage(Array.isArray(items) ? items : []);
    const list = sorted.filter(prksIsUserMarkupAnnotation);
    window.__prksAnnotationListCache = {
        allItems: sorted,
        rawItems: sorted,
        items: list,
        docId: docId != null && docId !== '' ? docId : null,
        workId: resolvedWorkId,
    };
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

    target.onclick = (e) => {
        const row = e.target.closest('.annotation-row');
        if (!row || !target.contains(row)) return;
        const idx = Number(row.getAttribute('data-ann-idx'));
        if (!Number.isFinite(idx)) return;
        e.preventDefault();
        if (e.target && e.target.closest && e.target.closest('.annotation-row__edit-comment')) {
            if (typeof window.openPdfAnnotationEditorByIndex === 'function') {
                void window.openPdfAnnotationEditorByIndex(idx);
            }
            return;
        }
        if (e.target && e.target.closest && e.target.closest('.annotation-row__delete')) {
            const cache = window.__prksAnnotationListCache;
            const rowItem = cache && Array.isArray(cache.items) ? cache.items[idx] : null;
            const annId = rowItem && (rowItem.id || rowItem.uuid || rowItem.annotationId || rowItem._id);
            if (!annId) return;
            if (!window.confirm('Delete this annotation from the PDF?')) return;
            const viewer = window.currentPdfViewer;
            if (viewer && typeof viewer.deleteAnnotation === 'function') {
                void viewer.deleteAnnotation(String(annId)).then(() => {
                    if (typeof window.closePdfAnnotationEditor === 'function') {
                        const st = window.__prksPdfAnnotationEditorState;
                        if (st && String(st.annId) === String(annId)) {
                            window.closePdfAnnotationEditor();
                        }
                    }
                });
            }
            return;
        }
        if (e.target && e.target.closest && e.target.closest('.annotation-row__copy-link')) {
            const cache = window.__prksAnnotationListCache;
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
            void prksCopyTextToClipboard(wikiLink).then(() => {
                const btn = e.target.closest('.annotation-row__copy-link');
                if (!btn) return;
                const original = btn.textContent;
                btn.textContent = 'Copied';
                setTimeout(() => {
                    try {
                        btn.textContent = original;
                    } catch (_e) {}
                }, 1200);
            });
            return;
        }
        if (e.target.closest('.annotation-row__jump') || e.target.closest('.annotation-row__page-jump')) {
            const cache = window.__prksAnnotationListCache;
            const st = window.__prksPdfAnnotationEditorState;
            const rowItem = cache && Array.isArray(cache.items) ? cache.items[idx] : null;
            const rowAnnId = rowItem && (rowItem.id || rowItem.uuid || rowItem.annotationId || rowItem._id);
            if (st && rowAnnId != null && String(rowAnnId) !== String(st.annId)) {
                if (typeof window.closePdfAnnotationEditor === 'function') {
                    window.closePdfAnnotationEditor();
                }
            }
            void window.jumpToPdfAnnotationByIndex(idx);
        }
    };
}

window.applyCachedAnnotationListToPanel = function applyCachedAnnotationListToPanel() {
    const c = window.__prksAnnotationListCache;
    if (!c) return;
    const src = Array.isArray(c.rawItems) ? c.rawItems : c.items;
    if (!Array.isArray(src)) return;
    renderAnnotationFallbackList(src, c.docId, c.workId);
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

async function setupAnnotationPersistence(viewer, workId) {
    if (!viewer || typeof viewer.saveCopy !== 'function' || typeof viewer.getAnnotations !== 'function') {
        return;
    }
    if (window.annotationSyncInterval) {
        try { clearInterval(window.annotationSyncInterval); } catch (_e) {}
        window.annotationSyncInterval = null;
    }

    const syncState = {
        workId: String(workId),
        pendingChanges: false,
        inFlight: false,
        lastError: '',
        lastSuccessAt: 0,
        lastConfirmedToken: '',
        activeToken: '',
        localMutationSeen: false,
    };
    window.__prksWorkAnnotationSyncState = syncState;
    window.prksHasPendingWorkAnnotationSync = function () {
        const st = window.__prksWorkAnnotationSyncState;
        return !!(st && st.workId && (st.pendingChanges || st.inFlight));
    };
    prksEnsureAnnotationBeforeUnloadGuard();

    function renderSyncIndicator() {
        const el = document.getElementById('annotation-sync-status');
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
    }

    window.__prksAnnotationListCache = {
        allItems: [],
        rawItems: [],
        items: [],
        docId: viewer.getDocumentId ? viewer.getDocumentId() : null,
        workId: String(workId),
    };
    try {
        const savedRes = await fetch(`/api/works/${workId}/annotations`, { cache: 'no-store' });
        const savedData = await savedRes.json();
        const saved = JSON.parse(savedData.annotations_json || '[]');
        if (Array.isArray(saved) && saved.length > 0) {
            renderAnnotationFallbackList(saved, 'DB', workId);
        }
    } catch (_e) {}
    renderSyncIndicator();

    async function exportAndPersistPdfCopy(saveToken) {
        const buffer = await viewer.saveCopy();
        if (!buffer || !buffer.byteLength) return;
        const b64 = arrayBufferToBase64(buffer);
        const pdfRes = await fetch(`/api/works/${workId}/pdf`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_b64: b64, save_token: saveToken }),
        });
        if (!pdfRes.ok) {
            throw new Error(`PDF save failed (${pdfRes.status})`);
        }
    }

    async function runWorkAnnotationAndPdfPersistencePass(saveToken) {
        await exportAndPersistPdfCopy(saveToken);
        const itemsFound = prksViewerAnnotationObjects(viewer).filter(isLikelyAnnotationObject);
        const userItems = sortAnnotationsByPage(itemsFound.filter(prksIsUserMarkupAnnotation));
        const serialized = JSON.stringify(userItems);
        renderAnnotationFallbackList(itemsFound, viewer.getDocumentId ? viewer.getDocumentId() : null, workId);
        const annRes = await fetch(`/api/works/${workId}/annotations`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ annotations_json: serialized, save_token: saveToken }),
        });
        if (!annRes.ok) {
            throw new Error(`Annotation save failed (${annRes.status})`);
        }
    }

    async function confirmPersistedToken(saveToken) {
        const tries = 8;
        for (let attempt = 0; attempt < tries; attempt++) {
            try {
                const probe = await fetch(
                    `/api/works/${workId}/save-confirm?token=${encodeURIComponent(saveToken)}&t=${Date.now()}`,
                    { cache: 'no-store' }
                );
                if (probe.ok) {
                    const body = await probe.json().catch(() => ({}));
                    if (body && body.saved === true) return true;
                }
            } catch (_e) {}
            const wait = attempt < 2 ? 250 : attempt < 5 ? 450 : 800;
            await new Promise((r) => setTimeout(r, wait));
        }
        return false;
    }

    let queueRequested = false;
    let queueRunning = false;
    let queueDrainPromise = Promise.resolve();
    let retryTimer = null;

    function scheduleRetry() {
        if (retryTimer != null) return;
        retryTimer = setTimeout(() => {
            retryTimer = null;
            void requestFlush('retry');
        }, 2200);
    }

    async function drainFlushQueue() {
        while (queueRequested) {
            queueRequested = false;
            syncState.inFlight = true;
            syncState.lastError = '';
            renderSyncIndicator();
            const saveToken = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
            syncState.activeToken = saveToken;
            try {
                await runWorkAnnotationAndPdfPersistencePass(saveToken);
                const confirmed = await confirmPersistedToken(saveToken);
                if (!confirmed) throw new Error('Server confirmation timeout');
                syncState.lastConfirmedToken = saveToken;
                syncState.lastSuccessAt = Date.now();
                syncState.pendingChanges = queueRequested;
                syncState.lastError = '';
            } catch (err) {
                syncState.pendingChanges = true;
                syncState.lastError = err && err.message ? String(err.message) : 'Save failed';
                scheduleRetry();
            } finally {
                syncState.inFlight = false;
                renderSyncIndicator();
            }
        }
    }

    function requestFlush(_reason = 'manual') {
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

    window.__prksFlushWorkAnnotationPersistence = async function () {
        try {
            syncState.localMutationSeen = true;
            await requestFlush('manual');
        } catch (_e) {}
    };

    if (typeof viewer.onAnnotationEvent === 'function') {
        viewer.onAnnotationEvent((evt) => {
            if (!evt || evt.committed !== true) return;
            if (evt.kind !== 'create' && evt.kind !== 'update' && evt.kind !== 'delete') return;
            syncState.localMutationSeen = true;
            void requestFlush('annotation-event');
        });
    }
}

function prksPdfLastPageLocalKey(workId) {
    return 'prks.pdf.lastPage.' + workId;
}

const PRKS_PDF_LAST_PAGE_DEBOUNCE_MS = 900;

function createPdfLastPageController(work) {
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
        persistPayload(window.__prksPdfPageSession);
    }

    function persistDebounced() {
        if (!persistOk || !alive) return;
        const enabled =
            typeof window.prksGetPdfRememberLastPageEnabled === 'function' &&
            window.prksGetPdfRememberLastPageEnabled();
        if (!enabled) return;
        const sess = window.__prksPdfPageSession;
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

    const detach = () => {
        alive = false;
        debounceClear();
        persistNow();
        if (window.__prksPdfLastPageDetach === detach) {
            window.__prksPdfLastPageDetach = null;
        }
        if (window.__prksPdfLastPageDebounceClear === debounceClear) {
            window.__prksPdfLastPageDebounceClear = null;
        }
    };

    window.__prksPdfLastPageDetach = detach;
    window.__prksPdfLastPageDebounceClear = debounceClear;

    return {
        initialPage,
        setViewer() {},
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
            window.__prksPdfPageSession = {
                workId,
                pageNumber: pn,
                totalPages: Number.isFinite(tn) ? tn : undefined,
            };
            persistDebounced();
        },
        detach,
    };
}

function prksDestroyWorkPdfViewer() {
    if (typeof window.__prksPdfLastPageDetach === 'function') {
        try { window.__prksPdfLastPageDetach(); } catch (_e) {}
    }
    if (window.annotationSyncInterval) {
        try { clearInterval(window.annotationSyncInterval); } catch (_e) {}
        window.annotationSyncInterval = null;
    }
    const viewer = window.currentPdfViewer;
    window.currentPdfViewer = null;
    if (viewer && typeof viewer.destroy === 'function') {
        try { viewer.destroy(); } catch (_e) {}
    }
}

export function initPdfViewerForWork(work) {
    if (!work || !work.file_path) return;
    setTimeout(() => {
        prksDestroyWorkPdfViewer();
        const targetNode = document.getElementById('pdf-viewer');
        if (!targetNode) return;
        targetNode.innerHTML = '';
        const lastPage = createPdfLastPageController(work);
        const src =
            String(work.file_path || '') +
            (String(work.file_path || '').includes('?') ? '&' : '?') +
            'prksv=' +
            Date.now();
        const author =
            typeof getPrksAnnotationAuthor === 'function' ? getPrksAnnotationAuthor() : 'You';
        const typeMeta =
            typeof prksDocTypeMeta === 'function' ? prksDocTypeMeta(work.doc_type) : null;
        createPrksPdfViewer({
            target: targetNode,
            src,
            mode: 'work',
            annotationAuthor: author,
            documentTitle: work.title || 'Document',
            documentTypeLabel: typeMeta && typeMeta.label ? typeMeta.label : '',
            documentTypeColor: typeMeta && typeMeta.color ? typeMeta.color : undefined,
            documentTypeBorder: typeMeta && typeMeta.border ? typeMeta.border : undefined,
            initialPage: lastPage.initialPage,
            onPageChange: (info) => lastPage.onPageChange(info),
            onAnnotationCommentRequest: (info) => {
                if (!info || !info.annotationId) return;
                if (typeof window.openPdfAnnotationEditorById === 'function') {
                    void window.openPdfAnnotationEditorById(info.annotationId);
                }
            },
            onError: (err) => console.error('PDF viewer failed', err),
        })
            .then((viewer) => {
                lastPage.setViewer(viewer);
                window.currentPdfViewer = viewer;
                void setupAnnotationPersistence(viewer, work.id);
            })
            .catch((err) => {
                console.error('Failed to load PDF viewer', err);
            });
    }, 100);
}
