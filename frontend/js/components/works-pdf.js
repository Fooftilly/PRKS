/**
 * PDF / EmbedPDF viewer and annotation integration — loaded on demand when opening a work with a PDF.
 */
 
function findDocumentIdFromState(state) {
    if (!state || typeof state !== 'object') return null;
    const candidates = [];
    const pushIf = (v) => {
        if (typeof v === 'string' && v.trim()) candidates.push(v);
    };
    pushIf(state.activeDocumentId);
    pushIf(state.documentId);
    if (state.core && typeof state.core === 'object') {
        pushIf(state.core.activeDocumentId);
        pushIf(state.core.documentId);
    }
    if (state.documents && typeof state.documents === 'object') {
        for (const k of Object.keys(state.documents)) pushIf(k);
    }
    if (state.core && state.core.documents && typeof state.core.documents === 'object') {
        for (const k of Object.keys(state.core.documents)) pushIf(k);
    }
    return candidates[0] || null;
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

/**
 * On phones/tablets EmbedPDF often defaults to pan (hand) mode, so drags scroll instead of selecting text.
 * mode:view alone does not switch interaction out of panMode; set default + activate pointerMode on first layout.
 */
function prksPrimeEmbedPdfPointerModeForCoarseTouch(registry, findDocumentIdFromState, viewer) {
    if (!registry || !viewer || typeof findDocumentIdFromState !== 'function') return;
    if (viewer.__prksTouchPointerPrimed) return;
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    if (!window.matchMedia('(pointer: coarse)').matches) return;
    const isSmallScreen =
        typeof window.prksIsSmallScreen === 'function'
            ? !!window.prksIsSmallScreen()
            : true;
    if (isSmallScreen) {
        return;
    }

    const scroll = registry.getPlugin('scroll')?.provides?.();
    const commands = registry.getPlugin('commands')?.provides?.();
    if (!scroll || typeof scroll.onLayoutReady !== 'function' || !commands || typeof commands.forDocument !== 'function') {
        return;
    }

    let unsubLayout = null;
    unsubLayout = scroll.onLayoutReady((event) => {
        if (viewer.__prksTouchPointerPrimed) return;
        try {
            const docId =
                event && event.documentId
                    ? event.documentId
                    : findDocumentIdFromState(registry.store && registry.store.getState ? registry.store.getState() : null);
            if (!docId) return;
            const scope = typeof commands.forDocument === 'function' ? commands.forDocument(docId) : null;
            if (scope && typeof scope.execute === 'function') {
                scope.execute('mode:view', 'api');
            }
            const im = registry.getPlugin('interaction-manager')?.provides?.();
            if (im && typeof im.setDefaultMode === 'function') {
                try {
                    im.setDefaultMode('pointerMode');
                } catch (_sd) {}
            }
            const ims = im && typeof im.forDocument === 'function' ? im.forDocument(docId) : null;
            if (ims && typeof ims.activate === 'function') {
                ims.activate('pointerMode');
            }
            viewer.__prksTouchPointerPrimed = true;
        } catch (_e) {}
        if (typeof unsubLayout === 'function') unsubLayout();
        else if (unsubLayout && typeof unsubLayout.unsubscribe === 'function') unsubLayout.unsubscribe();
    });
}

/**
 * EmbedPDF: trim mode tabs (View/Annotate only) and annotation toolbar to markup + comment + style + undo/redo.
 * See default schema in @embedpdf/snippet (mode-tabs, annotation-toolbar).
 */
async function applyEmbedPdfUiCustomization(viewer) {
    if (!viewer) return;
    try {
        const reg = viewer.registry;
        let registry;
        if (reg && typeof reg.then === 'function') {
            registry = await reg;
        } else if (reg && typeof reg.getPlugin === 'function') {
            registry = reg;
        } else {
            return;
        }

        prksPrimeEmbedPdfPointerModeForCoarseTouch(registry, findDocumentIdFromState, viewer);

        const ui = registry.getPlugin('ui')?.provides();
        if (!ui || typeof ui.getSchema !== 'function' || typeof ui.mergeSchema !== 'function') return;
        const schema = ui.getSchema();
        const mainToolbar = schema.toolbars['main-toolbar'];
        const annotationToolbar = schema.toolbars['annotation-toolbar'];
        if (!mainToolbar || !annotationToolbar) return;

        const isSmallScreen =
            typeof window.prksIsSmallScreen === 'function'
                ? !!window.prksIsSmallScreen()
                : false;
        const mainItemsRaw = JSON.parse(JSON.stringify(mainToolbar.items));
        const mainItems = mainItemsRaw.filter(
            (it) =>
                !(
                    (it && it.id === 'mode-tabs') ||
                    (it && it.id === 'mode-select-button') ||
                    (it && it.id === 'overflow-tabs-button')
                )
        );

        const annToolbarCopy = JSON.parse(JSON.stringify(annotationToolbar));
        const toolsGroup = annToolbarCopy.items.find((i) => i.id === 'annotation-tools');
        const byId = new Map();
        if (toolsGroup && Array.isArray(toolsGroup.items)) {
            for (const it of toolsGroup.items) {
                if (it && it.id) byId.set(it.id, it);
            }
        }

        // Main toolbar: keep Search only on the right group (remove comment sidebar).
        const rightGroup = mainItems.find((i) => i.type === 'group' && i.id === 'right-group');
        if (rightGroup && Array.isArray(rightGroup.items)) {
            rightGroup.items = rightGroup.items.filter((x) => x && x.id !== 'comment-button');
        }

        // Main toolbar: inject markup tools into the center group, near pointer/pan.
        const centerGroup = mainItems.find((i) => i.type === 'group' && i.id === 'center-group');
        if (centerGroup && Array.isArray(centerGroup.items)) {
            const sourceCenter =
                (mainItemsRaw.find((i) => i && i.type === 'group' && i.id === 'center-group')?.items || []);
            const pointerTemplate = sourceCenter.find((x) => x && x.id === 'pointer-button') || null;
            const panTemplate = sourceCenter.find((x) => x && x.id === 'pan-button') || null;

            // Remove any previous injected tools to avoid duplicates across re-init.
            centerGroup.items = centerGroup.items.filter(
                (x) =>
                    !(
                        x &&
                        [
                            'prks-center-annotation-divider',
                            'prks-center-undo-divider',
                            'add-highlight',
                            'add-underline',
                            'add-strikeout',
                            'add-squiggly',
                            'undo-button',
                            'redo-button',
                            'toggle-annotation-style',
                        ].includes(x.id)
                    )
            );

            if (isSmallScreen) {
                const hasPointer = centerGroup.items.some((x) => x && x.id === 'pointer-button');
                const hasPan = centerGroup.items.some((x) => x && x.id === 'pan-button');
                if (!hasPan && panTemplate) centerGroup.items.unshift({ ...panTemplate });
                if (!hasPointer && pointerTemplate) centerGroup.items.unshift({ ...pointerTemplate });
            }

            const inject = [];
            inject.push({
                type: 'divider',
                id: 'prks-center-annotation-divider',
                orientation: 'vertical',
            });
            for (const id of ['add-highlight', 'add-underline']) {
                const obj = byId.get(id);
                if (obj) inject.push(obj);
            }
            const undo = byId.get('undo-button');
            const redo = byId.get('redo-button');
            if (undo || redo) {
                inject.push({
                    type: 'divider',
                    id: 'prks-center-undo-divider',
                    orientation: 'vertical',
                });
                if (undo) inject.push(undo);
                if (redo) inject.push(redo);
            }

            // Place PRKS injects after pointer button if present; otherwise append.
            const ptrIdx = centerGroup.items.findIndex((x) => x && x.id === 'pointer-button');
            const ins = ptrIdx >= 0 ? ptrIdx + 1 : centerGroup.items.length;
            centerGroup.items.splice(ins, 0, ...inject);

            
        }

        // Disable annotation-toolbar content (tools live in main toolbar).
        annToolbarCopy.items = [];
        annToolbarCopy.categories = [];
        annToolbarCopy.permanent = false;
        delete annToolbarCopy.responsive;

        if (typeof ui.disableCategory === 'function') {
            // Make sidebar comment panel inaccessible from viewer UI (default commenting is PRKS sidebar editor).
            ui.disableCategory('panel-comment');
            // Rubber stamps: link/markup hover menu + left "rubber-stamp-panel" (category differs from annotation-stamp).
            ui.disableCategory('stamp');
            ui.disableCategory('insert-rubber-stamp');
        }

        ui.mergeSchema({
            toolbars: {
                'main-toolbar': { 
                    ...mainToolbar, 
                    position: { placement: 'floating', slot: 'main', order: 0 },
                    items: mainItems 
                },
                'annotation-toolbar': { 
                    ...annToolbarCopy,
                    position: { placement: 'floating', slot: 'main', order: 1 }
                },
            },
        });
        const hostForModeFix = document.getElementById(viewer === window.uploadViewer ? 'upload-viewer' : 'pdf-viewer');
        if (hostForModeFix && !viewer.__prksToolbarModeFixBound) {
            viewer.__prksToolbarModeFixBound = true;
            const inBand = Number(window.innerWidth || 0) >= 421 && Number(window.innerWidth || 0) <= 521;
            if (!inBand) return;
            const maxModeFixAttempts = 36;
            let modeFixAttempt = 0;
            const runModeFixAttempt = () => {
                modeFixAttempt += 1;
                const seen = new Set();
                let panNode = null;
                let ptrNode = null;
                const walk = (node) => {
                    if (!node || panNode && ptrNode) return;
                    if (node.nodeType !== 1) return;
                    if (seen.has(node)) return;
                    seen.add(node);
                    const aria = node.getAttribute ? String(node.getAttribute('aria-label') || '').toLowerCase() : '';
                    if (!panNode && aria === 'toggle pan mode') {
                        panNode = node;
                    }
                    if (!ptrNode && aria === 'toggle pointer mode') {
                        ptrNode = node;
                    }
                    if (panNode && ptrNode) return;
                    if (node.shadowRoot) {
                        const sk = node.shadowRoot.children || [];
                        for (let i = 0; i < sk.length; i++) walk(sk[i]);
                    }
                    const kids = node.children || [];
                    for (let i = 0; i < kids.length; i++) walk(kids[i]);
                };
                walk(hostForModeFix);
                const panWrap = panNode && panNode.parentElement ? panNode.parentElement : null;
                const ptrWrap = ptrNode && ptrNode.parentElement ? ptrNode.parentElement : null;
                const panBefore = panWrap ? window.getComputedStyle(panWrap).display : null;
                const ptrBefore = ptrWrap ? window.getComputedStyle(ptrWrap).display : null;
                if (panWrap && window.getComputedStyle(panWrap).display === 'none') {
                    panWrap.style.display = 'contents';
                }
                if (ptrWrap && window.getComputedStyle(ptrWrap).display === 'none') {
                    ptrWrap.style.display = 'contents';
                }
                const gotBoth = !!panNode && !!ptrNode;
                if (gotBoth || modeFixAttempt >= maxModeFixAttempts) {
                    return;
                }
                requestAnimationFrame(runModeFixAttempt);
            };
            requestAnimationFrame(() => requestAnimationFrame(runModeFixAttempt));
        }

        const live = ui.getSchema();
        // Floating bar for selected markup: remove comment + style (PRKS uses sidebar comments).
        const PRKS_SELECTION_POPUP_STRIP_IDS = new Set([
            'add-comment',
            'comment-button',
            'toggle-annotation-style',
            'create-stamp-from-annotation',
            'create-stamp-from-group',
            'add-strikeout',
            'add-squiggly',
        ]);
        const PRKS_SELECTION_POPUP_STRIP_COMMAND_IDS = new Set([
            'annotation:toggle-comment',
            'stamp:create-from-selected',
            'stamp:create-from-group',
            'annotation:add-strikeout',
            'annotation:add-squiggly',
        ]);
        const sm = live.selectionMenus;
        if (sm && typeof sm === 'object') {
            for (const key of Object.keys(sm)) {
                const menu = sm[key];
                if (!menu || !Array.isArray(menu.items)) continue;
                menu.items = menu.items.filter((it) => {
                    if (!it || typeof it !== 'object') return true;
                    if (PRKS_SELECTION_POPUP_STRIP_IDS.has(it.id)) return false;
                    if (it.commandId && PRKS_SELECTION_POPUP_STRIP_COMMAND_IDS.has(it.commandId)) return false;
                    if (
                        typeof it.commandId === 'string' &&
                        it.commandId.startsWith('stamp:')
                    ) {
                        return false;
                    }
                    if (Array.isArray(it.categories) && it.categories.includes('stamp')) return false;
                    return true;
                });
                if (menu.visibilityDependsOn && Array.isArray(menu.visibilityDependsOn.itemIds)) {
                    const stripIds = PRKS_SELECTION_POPUP_STRIP_IDS;
                    menu.visibilityDependsOn.itemIds = menu.visibilityDependsOn.itemIds.filter((id) => !stripIds.has(id));
                }
            }
        }

        // Keep EmbedPDF in pointer / view mode after creating markup from selection popup.
        // (This matches the built-in "mode:view" command behavior: pointerMode + close annotate toolbar.)
        if (!viewer.__prksKeepEmbedPdfViewMode) {
            viewer.__prksKeepEmbedPdfViewMode = true;
            const commands = registry.getPlugin('commands')?.provides?.();
            const annotation = registry.getPlugin('annotation')?.provides?.();
            if (commands && annotation && typeof annotation.onAnnotationEvent === 'function') {
                annotation.onAnnotationEvent((evt) => {
                    try {
                        if (!evt) return;
                        const docId = evt.documentId;
                        if (!docId) return;
                        const t = evt.type;
                        const isDeleteLike =
                            t === 'delete' ||
                            t === 'remove' ||
                            t === 'destroy' ||
                            t === 'annotation:delete' ||
                            (typeof t === 'string' && t.toLowerCase().includes('delet'));
                        if (evt.committed !== true && !isDeleteLike) return;
                        const scope = typeof commands.forDocument === 'function' ? commands.forDocument(docId) : null;
                        if (!scope || typeof scope.execute !== 'function') return;
                        const shouldModeView = t === 'create' && evt.committed === true;
                        const shouldFlush =
                            (evt.committed === true &&
                                (t === 'create' || t === 'update' || t === 'delete')) ||
                            isDeleteLike;
                        if (!shouldModeView && !shouldFlush) return;
                        const defer = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : queueMicrotask;
                        defer(() => {
                            try {
                                if (shouldModeView) scope.execute('mode:view', 'api');
                            } catch (_x) {}
                            if (!shouldFlush) return;
                            const flushLater =
                                typeof requestAnimationFrame === 'function' ? requestAnimationFrame : queueMicrotask;
                            flushLater(() => {
                                if (typeof window.__prksFlushWorkAnnotationPersistence === 'function') {
                                    void window.__prksFlushWorkAnnotationPersistence();
                                }
                            });
                        });
                    } catch (_e) {}
                });
            }
        }

        void import('/js/embedpdf/prks-selection-touch.js')
            .then((m) => {
                if (m && typeof m.installPrksEmbedPdfSelectionAssist === 'function') {
                    m.installPrksEmbedPdfSelectionAssist(viewer, findDocumentIdFromState);
                }
            })
            .catch(() => {});
    } catch (_e) {}
}

/** True when PRKS toolbar injection is present (schema may be empty on first frame after init). */
async function prksEmbedPdfCustomizationLooksApplied(viewer) {
    if (!viewer) return false;
    try {
        const reg = viewer.registry && typeof viewer.registry.then === 'function' ? await viewer.registry : viewer.registry;
        if (!reg || typeof reg.getPlugin !== 'function') return false;
        const ui = reg.getPlugin('ui')?.provides?.();
        if (!ui || typeof ui.getSchema !== 'function') return false;
        const schema = ui.getSchema();
        const main = schema && schema.toolbars && schema.toolbars['main-toolbar'];
        if (!main || !Array.isArray(main.items)) return false;
        const center = main.items.find((it) => it && it.type === 'group' && it.id === 'center-group');
        if (!center || !Array.isArray(center.items)) return false;
        return center.items.some((x) => x && x.id === 'prks-center-annotation-divider');
    } catch (_e) {
        return false;
    }
}

/** Re-apply customization until EmbedPDF UI schema is ready (blob / modal timing). */
async function prksApplyEmbedPdfCustomizationWithRetry(viewer, maxAttempts = 40) {
    if (!viewer) return;
    for (let i = 0; i < maxAttempts; i++) {
        await applyEmbedPdfUiCustomization(viewer);
        if (await prksEmbedPdfCustomizationLooksApplied(viewer)) return;
        await new Promise((r) => requestAnimationFrame(r));
    }
}

window.applyEmbedPdfUiCustomization = applyEmbedPdfUiCustomization;
window.prksApplyEmbedPdfCustomizationWithRetry = prksApplyEmbedPdfCustomizationWithRetry;

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

function collectLikelyAnnotationsFromState(node, out, depth = 0, seenIds = new Set(), visited = new WeakSet()) {
    if (!node || typeof node !== 'object' || depth > 12 || visited.has(node)) return;
    visited.add(node);

    if (Array.isArray(node)) {
        for (const item of node) {
            if (isLikelyAnnotationObject(item)) {
                const id = item.id || item.uuid || item.annotationId || item._id || item.annotation_id || item.ID;
                if (id && !seenIds.has(id)) {
                    seenIds.add(id);
                    out.push(item);
                }
            } else if (item && typeof item === 'object') {
                collectLikelyAnnotationsFromState(item, out, depth + 1, seenIds, visited);
            }
        }
        return;
    }

    for (const [k, v] of Object.entries(node)) {
        if (!v || typeof v !== 'object') continue;
        if (isLikelyAnnotationObject(v)) {
            const id = v.id || v.uuid || v.annotationId || v._id || v.annotation_id || v.ID;
            if (id && !seenIds.has(id)) {
                seenIds.add(id);
                out.push(v);
            }
        } else {
            collectLikelyAnnotationsFromState(v, out, depth + 1, seenIds, visited);
        }
    }
}

/**
 * Redux `documents[id]` is often a wrapper; engine wants PdfDocumentObject.
 */
function prksResolvePdfDocumentObject(docFromStore) {
    if (!docFromStore || typeof docFromStore !== 'object') return docFromStore;
    const d =
        docFromStore.pdfDocument ||
        docFromStore.document ||
        docFromStore.doc ||
        docFromStore.ref ||
        docFromStore.handle ||
        (docFromStore.core && docFromStore.core.document) ||
        docFromStore;
    return d && typeof d === 'object' ? d : docFromStore;
}

/** Only explicit per-doc annotation arrays — never deep-walk whole plugin (800+ link ghosts). */
function prksCollectAnnotationsFromPluginState(state, docIdHint, out) {
    const plugins = state && state.plugins;
    if (!plugins || typeof plugins !== 'object') return;
    const docKey = typeof docIdHint === 'string' && docIdHint ? docIdHint : null;
    if (!docKey) return;

    const seenIds = new Set();
    const pushAnn = (item) => {
        if (!isLikelyAnnotationObject(item)) return;
        const id = item.id || item.uuid || item.annotationId || item._id || item.annotation_id || item.ID;
        if (id && !seenIds.has(id)) {
            seenIds.add(id);
            out.push(item);
        }
    };

    const harvestFromSlice = (slice) => {
        if (!slice || typeof slice !== 'object') return;
        const topLists = [slice.annotations, slice.annotationList, slice.list, slice.pageAnnotations];
        for (const lst of topLists) {
            if (!Array.isArray(lst)) continue;
            for (const item of lst) pushAnn(item);
        }
        const pages = slice.pages;
        if (pages && typeof pages === 'object') {
            for (const pv of Object.values(pages)) {
                if (!pv || typeof pv !== 'object') continue;
                const arr = pv.annotations;
                if (Array.isArray(arr)) {
                    for (const item of arr) pushAnn(item);
                }
            }
        }
    };

    for (const key of ['annotation-engine', 'annotation']) {
        const root = plugins[key];
        if (!root || typeof root !== 'object') continue;
        const byDoc = root.documents;
        if (byDoc && typeof byDoc === 'object' && byDoc[docKey]) {
            harvestFromSlice(byDoc[docKey]);
        } else if (root[docKey]) {
            harvestFromSlice(root[docKey]);
        }
    }
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

async function prksGetEmbedPdfAnnotationScope(viewer, docIdHint) {
    if (!viewer || typeof viewer.registry?.then !== 'function') return null;
    const registry = await viewer.registry;
    const state =
        registry.store && typeof registry.store.getState === 'function' ? registry.store.getState() : null;
    const hint = typeof docIdHint === 'string' && /^doc-/.test(docIdHint) ? docIdHint : null;
    const docId = hint || findDocumentIdFromState(state);
    if (!docId) return null;
    const annPlug = registry.getPlugin('annotation')?.provides?.();
    const scope = annPlug && typeof annPlug.forDocument === 'function' ? annPlug.forDocument(docId) : null;
    if (!scope) return null;
    return { registry, scope, docId };
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
        const got = await prksGetEmbedPdfAnnotationScope(viewer, c.docId || null);
        if (!got) return;
        const objRaw =
            typeof got.scope.getAnnotationById === 'function' ? got.scope.getAnnotationById(String(annId)) : null;
        const annObj = objRaw && typeof objRaw === 'object' ? objRaw.object || objRaw : item;

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
            docId: got.docId,
            custom: annObj && annObj.custom && typeof annObj.custom === 'object' ? annObj.custom : {},
        };
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
        const got = await prksGetEmbedPdfAnnotationScope(viewer, st.docId || null);
        if (!got) return;
        const raw =
            typeof got.scope.getAnnotationById === 'function'
                ? got.scope.getAnnotationById(String(st.annId))
                : null;
        const liveAnn = raw && typeof raw === 'object' ? raw.object || raw : null;
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
        const pi = Number(pageIdx);
        got.scope.updateAnnotation(pi, st.annId, patch);
        const merged =
            liveAnn && typeof liveAnn === 'object'
                ? Object.assign({}, liveAnn, patch, { id: st.annId })
                : Object.assign({}, patch, { id: st.annId });
        got.scope.updateAnnotation(pi, st.annId, merged);
        got.scope.commit?.();
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
        const registry = await window.currentPdfViewer.registry;
        const state = registry.store && typeof registry.store.getState === 'function' ? registry.store.getState() : null;
        const docId = findDocumentIdFromState(state);
        const annPlug = registry.getPlugin('annotation')?.provides?.();
        const scope = annPlug && docId && typeof annPlug.forDocument === 'function' ? annPlug.forDocument(docId) : null;
        if (scope && typeof scope.getAnnotationById === 'function') {
            const got = scope.getAnnotationById(id);
            const annObj = got && typeof got === 'object' ? got.object || got : null;
            if (annObj) {
                const pi = prksPageIndexFromAnnotationObject(annObj);
                if (Number.isFinite(pi)) {
                    await window.jumpToPdfAnnotation(id, pi, annObj);
                }
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
window.jumpToPdfAnnotation = async (id, pageIndex, annItem) => {
    if (!window.currentPdfViewer || id == null || id === '') return;
    const annId = String(id);
    try {
        const registry = await window.currentPdfViewer.registry;
        const store = registry.store;
        const state = store && typeof store.getState === 'function' ? store.getState() : null;
        const docId = state ? findDocumentIdFromState(state) : null;

        const ui = registry.getPlugin('ui')?.provides();
        if (ui && typeof ui.setMode === 'function') {
            ui.setMode('mode:annotate');
        }

        const scrollCap = registry.getPlugin('scroll')?.provides();
        const pi = pageIndex !== undefined && pageIndex !== null ? Number(pageIndex) : NaN;
        if (scrollCap && docId && Number.isFinite(pi) && pi >= 0) {
            const scope = typeof scrollCap.forDocument === 'function' ? scrollCap.forDocument(docId) : scrollCap;
            if (scope && typeof scope.scrollToPage === 'function') {
                const pageCoords = annItem && typeof annItem === 'object' ? annotationScrollPagePoint(annItem) : null;
                // alignY 0 = target at top of viewport; ~22 keeps the annotation well above the PDF/notes drag handle.
                const scrollOpts = {
                    pageNumber: pi + 1,
                    behavior: 'smooth',
                    alignX: 50,
                    alignY: 22,
                };
                if (pageCoords) scrollOpts.pageCoordinates = pageCoords;
                scope.scrollToPage(scrollOpts);
            }
        }

        const annCap = registry.getPlugin('annotation')?.provides();
        if (annCap && docId && typeof annCap.forDocument === 'function' && Number.isFinite(pi) && pi >= 0) {
            const scope = annCap.forDocument(docId);
            if (scope && typeof scope.selectAnnotation === 'function') {
                scope.setActiveTool?.(null);
                scope.selectAnnotation(pi, annId);
            }
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

/** EmbedPDF: engine.getAllAnnotations(doc) → Record<pageIndex, PdfAnnotationObject[]> */
async function collectAnnotationsViaGetAllAnnotations(engine, docObj, docIdHint) {
    if (!engine || typeof engine.getAllAnnotations !== 'function') {
        return { items: [], enumerated: false };
    }
    if (!docObj && !docIdHint) {
        return { items: [], enumerated: false };
    }

    function runGetAllAnnotationsTask(arg) {
        if (arg == null) return Promise.reject(new Error('no doc arg'));
        const task = engine.getAllAnnotations(arg);
        const byProgress = {};
        if (task && typeof task.onProgress === 'function') {
            task.onProgress((progress) => {
                try {
                    if (!progress || !Array.isArray(progress.annotations)) return;
                    const p = progress.page;
                    if (!Number.isFinite(Number(p))) return;
                    const pi = Number(p);
                    if (!Array.isArray(byProgress[pi])) byProgress[pi] = [];
                    for (const ann of progress.annotations) {
                        if (ann && typeof ann === 'object') byProgress[pi].push(ann);
                    }
                } catch (_x) {}
            });
        }
        if (task && typeof task.toPromise === 'function') {
            return task
                .toPromise()
                .then((final) => {
                    if (final && typeof final === 'object' && Object.keys(final).length > 0) return final;
                    return Object.keys(byProgress).length > 0 ? byProgress : final || {};
                })
                .catch(() => {
                    if (Object.keys(byProgress).length > 0) return byProgress;
                    throw new Error('getAllAnnotations failed');
                });
        }
        if (task && typeof task.then === 'function') return task;
        return Promise.reject(new Error('no PdfTask from getAllAnnotations'));
    }

    try {
        const resolvedDoc = prksResolvePdfDocumentObject(docObj);
        let byPage = null;
        try {
            byPage = await runGetAllAnnotationsTask(resolvedDoc);
        } catch (_first) {
            if (docIdHint != null && docIdHint !== '' && docIdHint !== docObj && docIdHint !== resolvedDoc) {
                byPage = await runGetAllAnnotationsTask(docIdHint);
            } else {
                throw _first;
            }
        }
        if (!byPage || typeof byPage !== 'object') {
            return { items: [], enumerated: false };
        }
        const out = [];
        for (const [pageKey, annos] of Object.entries(byPage)) {
            const pageNum = Number(pageKey);
            const pageIndex = Number.isFinite(pageNum) ? pageNum : undefined;
            if (!Array.isArray(annos)) continue;
            for (const ann of annos) {
                if (!ann || typeof ann !== 'object') continue;
                const merged =
                    ann.pageIndex !== undefined ? ann : pageIndex !== undefined ? { ...ann, pageIndex } : ann;
                out.push(merged);
            }
        }
        return { items: out, enumerated: true };
    } catch (_e) {
        return { items: [], enumerated: false };
    }
}

async function setupAnnotationPersistence(viewer, workId) {
    if (!viewer || !viewer.registry || typeof viewer.registry.then !== 'function') {
        return;
    }
    const registry = await viewer.registry;
    const engine = registry && registry.engine;
    if (!engine || typeof engine.saveAsCopy !== 'function') {
        return;
    }

    window.__prksAnnotationListCache = {
        allItems: [],
        rawItems: [],
        items: [],
        docId: null,
        workId: String(workId),
    };
    try {
        const savedRes = await fetch(`/api/works/${workId}/annotations`);
        const savedData = await savedRes.json();
        const saved = JSON.parse(savedData.annotations_json || '[]');
        if (Array.isArray(saved) && saved.length > 0) {
            renderAnnotationFallbackList(saved, 'DB', workId);
        }
    } catch (_e) { }

    function resolveDocHandle() {
        if (!registry || !registry.store || typeof registry.store.getState !== 'function') return null;
        try {
            const state = registry.store.getState();
            const docId = findDocumentIdFromState(state);
            const docsFromCore = state && state.core && state.core.documents;
            const docsFromRoot = state && state.documents;
            return (docsFromCore && docsFromCore[docId]) || (docsFromRoot && docsFromRoot[docId]) || null;
        } catch (_e) {
            return null;
        }
    }

    async function exportAndPersistPdfCopy() {
        if (!engine || typeof engine.saveAsCopy !== 'function') return;
        const rawDoc = resolveDocHandle();
        if (!rawDoc) return;
        try {
            try {
                const state = registry.store.getState();
                const docId = findDocumentIdFromState(state);
                const annProv = registry.getPlugin('annotation')?.provides();
                const scope =
                    docId && annProv && typeof annProv.forDocument === 'function' ? annProv.forDocument(docId) : null;
                if (scope && typeof scope.commit === 'function') scope.commit();
            } catch (_c) {}

            // saveAsCopy expects the same document handle as the viewer store (wrapper), not an inner PdfDocumentObject.
            let saveResult = engine.saveAsCopy(rawDoc);
            let buffer = null;
            if (saveResult && typeof saveResult.toPromise === 'function') {
                buffer = await saveResult.toPromise();
            } else if (saveResult && typeof saveResult.then === 'function') {
                buffer = await saveResult;
            } else if (saveResult instanceof ArrayBuffer) {
                buffer = saveResult;
            }
            if (!buffer || !buffer.byteLength) return;
            const b64 = arrayBufferToBase64(buffer);
            await fetch(`/api/works/${workId}/pdf`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file_b64: b64 })
            });
        } catch (_e) { }
    }

    let lastSerialized = '';

    async function runWorkAnnotationAndPdfPersistencePass() {
        const state = registry.store.getState();
        const currentDocId = findDocumentIdFromState(state);

        await exportAndPersistPdfCopy();

        let itemsFound = [];
        let trustEmpty = false;
        const docObj = resolveDocHandle();

        if (engine && (docObj || currentDocId)) {
            const { items: fromPdf, enumerated } = await collectAnnotationsViaGetAllAnnotations(
                engine,
                docObj,
                currentDocId
            );
            trustEmpty = enumerated;
            itemsFound = fromPdf.filter(isLikelyAnnotationObject);
        }

        const annEngine = registry.getPlugin('annotation-engine')?.provides();
        if (itemsFound.length === 0 && annEngine && typeof annEngine.getAnnotations === 'function' && currentDocId) {
            try {
                const apiAnns = await annEngine.getAnnotations(currentDocId);
                if (Array.isArray(apiAnns) && apiAnns.length > 0) {
                    itemsFound = apiAnns.filter(isLikelyAnnotationObject);
                    if (itemsFound.length > 0) trustEmpty = true;
                }
            } catch (_err) {}
        }

        if (itemsFound.length === 0 && currentDocId) {
            const annProv = registry.getPlugin('annotation')?.provides();
            const scope =
                annProv && typeof annProv.forDocument === 'function' ? annProv.forDocument(currentDocId) : null;
            if (scope) {
                for (const fn of ['getAnnotations', 'listAnnotations']) {
                    if (typeof scope[fn] !== 'function') continue;
                    try {
                        const r = scope[fn]();
                        const arr = r && typeof r.then === 'function' ? await r : r;
                        if (Array.isArray(arr) && arr.length > 0) {
                            itemsFound = arr.filter(isLikelyAnnotationObject);
                            if (itemsFound.length > 0) trustEmpty = true;
                            break;
                        }
                    } catch (_e) {}
                }
            }
        }

        if (itemsFound.length === 0 && !trustEmpty) {
            prksCollectAnnotationsFromPluginState(state, currentDocId, itemsFound);
        }

        const userItems = sortAnnotationsByPage(itemsFound.filter(prksIsUserMarkupAnnotation));

        if (userItems.length === 0 && !trustEmpty) {
            return;
        }

        // When user deleted all markup, itemsFound may still contain PDF links — userItems=[] but we must re-render + POST [] to clear DB/sidebar.
        const serialized = JSON.stringify(userItems);
        renderAnnotationFallbackList(itemsFound, currentDocId, workId);

        await fetch(`/api/works/${workId}/annotations`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ annotations_json: serialized }),
        });
    }

    window.__prksFlushWorkAnnotationPersistence = async function () {
        try {
            await runWorkAnnotationAndPdfPersistencePass();
        } catch (_e) {}
    };

    window.annotationSyncInterval = setInterval(async () => {
        try {
            if (document.hidden) return;
            if (!registry || !registry.store || typeof registry.store.getState !== 'function') return;
            const state = registry.store.getState();

            const currentDocId = findDocumentIdFromState(state);
            const annPluginState =
                state && state.plugins && Object.prototype.hasOwnProperty.call(state.plugins, 'annotation-engine')
                    ? state.plugins['annotation-engine']
                    : null;
            const serializedState = JSON.stringify({
                d: currentDocId,
                a: annPluginState || {},
                p: state && state.core && state.core.pageNavigation ? state.core.pageNavigation : null,
            });
            if (serializedState === lastSerialized) return;
            lastSerialized = serializedState;

            await runWorkAnnotationAndPdfPersistencePass();
        } catch (_e) {}
    }, 4000);
}

/** Load-error card uses alert icon wrapper `.bg-state-error-light`; password UI uses `.bg-accent-light` and different button layout. */
const PRKS_HIDE_EMBEDPDF_ERROR_CLOSE_CSS =
    'div.bg-bg-surface.flex.max-w-sm.flex-col.items-center.text-center:has(.bg-state-error-light) button.bg-accent.text-accent-fg.mt-5.w-full{display:none!important}';

function prksDetachHideEmbedPdfErrorCloseButton(previewRootEl) {
    if (!previewRootEl || !previewRootEl.__prksHideErrorCloseMo) return;
    try {
        previewRootEl.__prksHideErrorCloseMo.disconnect();
    } catch (_e) {}
    previewRootEl.__prksHideErrorCloseMo = null;
}

function prksInjectHideEmbedPdfErrorCloseIntoShadow(sr) {
    if (!sr || sr.__prksHideErrorCloseInjected) return;
    sr.__prksHideErrorCloseInjected = true;
    try {
        const sheet = new CSSStyleSheet();
        sheet.replaceSync(PRKS_HIDE_EMBEDPDF_ERROR_CLOSE_CSS);
        sr.adoptedStyleSheets = [...sr.adoptedStyleSheets, sheet];
    } catch (_e) {
        const st = document.createElement('style');
        st.textContent = PRKS_HIDE_EMBEDPDF_ERROR_CLOSE_CSS;
        sr.appendChild(st);
    }
}

function prksScanInjectHideEmbedPdfErrorClose(previewRootEl) {
    if (!previewRootEl) return;
    if (previewRootEl.shadowRoot) prksInjectHideEmbedPdfErrorCloseIntoShadow(previewRootEl.shadowRoot);
    previewRootEl.querySelectorAll('*').forEach((el) => {
        if (el.shadowRoot) prksInjectHideEmbedPdfErrorCloseIntoShadow(el.shadowRoot);
    });
}

/** Hides EmbedPDF's "Close Document" on the document load error screen only. */
function prksAttachHideEmbedPdfErrorCloseButton(previewRootEl) {
    if (!previewRootEl) return;
    prksDetachHideEmbedPdfErrorCloseButton(previewRootEl);
    prksScanInjectHideEmbedPdfErrorClose(previewRootEl);
    const mo = new MutationObserver(() => prksScanInjectHideEmbedPdfErrorClose(previewRootEl));
    mo.observe(previewRootEl, { childList: true, subtree: true });
    previewRootEl.__prksHideErrorCloseMo = mo;
}

window.prksDetachHideEmbedPdfErrorCloseButton = prksDetachHideEmbedPdfErrorCloseButton;
window.prksAttachHideEmbedPdfErrorCloseButton = prksAttachHideEmbedPdfErrorCloseButton;

const PRKS_PDF_LAST_PAGE_DEBOUNCE_MS = 900;

function prksPdfLastPageLocalKey(workId) {
    return 'prks.pdf.lastPage.' + workId;
}

/**
 * Remember scroll page per work (localStorage + Settings toggle). Debounced writes; detach prior viewer on re-init.
 */
async function setupPdfLastPageMemory(viewer, work) {
    if (!viewer || !work || !work.id) return;
    if (typeof window.__prksPdfLastPageDetach === 'function') {
        try {
            window.__prksPdfLastPageDetach();
        } catch (_e) {}
    }

    const workId = work.id;
    let debounceTimer = null;
    let unsubLayout = null;
    let unsubPage = null;

    function debounceClear(wid) {
        if (wid != null && wid !== workId) return;
        if (debounceTimer != null) {
            clearTimeout(debounceTimer);
            debounceTimer = null;
        }
    }

    const debounceClearRef = function (wid) {
        debounceClear(wid);
    };

    function persistDebounced() {
        const enabled =
            typeof window.prksGetPdfRememberLastPageEnabled === 'function' &&
            window.prksGetPdfRememberLastPageEnabled();
        if (!enabled) return;
        const sess = window.__prksPdfPageSession;
        if (!sess || sess.workId !== workId) return;
        debounceClear(workId);
        debounceTimer = setTimeout(() => {
            debounceTimer = null;
            const on =
                typeof window.prksGetPdfRememberLastPageEnabled === 'function' &&
                window.prksGetPdfRememberLastPageEnabled();
            if (!on) return;
            const s = window.__prksPdfPageSession;
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

    const detach = () => {
        debounceClear(workId);
        try {
            if (typeof unsubLayout === 'function') unsubLayout();
        } catch (_e) {}
        try {
            if (typeof unsubPage === 'function') unsubPage();
        } catch (_e) {}
        unsubLayout = null;
        unsubPage = null;
        if (window.__prksPdfLastPageDetach === detach) {
            window.__prksPdfLastPageDetach = null;
        }
        if (window.__prksPdfLastPageDebounceClear === debounceClearRef) {
            window.__prksPdfLastPageDebounceClear = null;
        }
    };

    try {
        const registry = await viewer.registry;
        const scrollPl = registry.getPlugin('scroll');
        const scroll = scrollPl && typeof scrollPl.provides === 'function' ? scrollPl.provides() : null;
        if (!scroll) {
            window.__prksPdfLastPageDetach = null;
            return;
        }

        window.__prksPdfLastPageDebounceClear = debounceClearRef;

        if (typeof scroll.onLayoutReady === 'function') {
            unsubLayout = scroll.onLayoutReady((event) => {
                const enabled =
                    typeof window.prksGetPdfRememberLastPageEnabled === 'function' &&
                    window.prksGetPdfRememberLastPageEnabled();
                if (!enabled) return;
                const docId = event && event.documentId;
                if (!docId) return;
                const stored = parseStored();
                if (!stored) return;
                const total = event.totalPages != null ? Number(event.totalPages) : NaN;
                if (!Number.isFinite(total) || total < 1) return;
                if (stored.n != null && stored.n !== total) return;
                const page = Math.min(Math.max(1, stored.p), total);
                const scope = typeof scroll.forDocument === 'function' ? scroll.forDocument(docId) : scroll;
                if (scope && typeof scope.scrollToPage === 'function') {
                    scope.scrollToPage({ pageNumber: page, behavior: 'instant' });
                }
            });
        }

        if (typeof scroll.onPageChange === 'function') {
            unsubPage = scroll.onPageChange((event) => {
                const pn = event && event.pageNumber != null ? Number(event.pageNumber) : NaN;
                const tn = event && event.totalPages != null ? Number(event.totalPages) : NaN;
                if (!Number.isFinite(pn) || pn < 1) return;
                window.__prksPdfPageSession = {
                    workId,
                    pageNumber: pn,
                    totalPages: Number.isFinite(tn) ? tn : undefined,
                };
                persistDebounced();
            });
        }

        window.__prksPdfLastPageDetach = detach;
    } catch (_e) {
        window.__prksPdfLastPageDetach = null;
        if (window.__prksPdfLastPageDebounceClear === debounceClearRef) {
            window.__prksPdfLastPageDebounceClear = null;
        }
    }
}

export function initPdfViewerForWork(work) {
    if (!work || !work.file_path) return;
    setTimeout(() => {
        if (typeof window.__prksPdfLastPageDetach === 'function') {
            try {
                window.__prksPdfLastPageDetach();
            } catch (_e) {}
        }
        if (
            typeof window.__prksEmbedPdfSelectionAssistDetach === 'function' &&
            window.__prksEmbedPdfSelectionAssistViewer === window.currentPdfViewer
        ) {
            try {
                window.__prksEmbedPdfSelectionAssistDetach();
            } catch (_e) {}
        }
        const targetNode = document.getElementById('pdf-viewer');
        if (targetNode) prksAttachHideEmbedPdfErrorCloseButton(targetNode);
        import('https://cdn.jsdelivr.net/npm/@embedpdf/snippet@2/dist/embedpdf.js')
            .then(embedModule => {
                const EmbedPDF = embedModule.default;
                const ZoomMode = embedModule.ZoomMode;
                const disabledCategories = [
                    'annotation-shape',
                    'annotation-ink',
                    'redaction',
                    'form',
                    'annotation-text',
                    'annotation-stamp',
                    'stamp',
                    'insert-rubber-stamp',
                    'document',
                    'panel-sidebar',
                    'panel-comment',
                ];

                const initResult = EmbedPDF.init({
                    type: 'container',
                    target: targetNode,
                    src: work.file_path,
                    disabledCategories,
                    annotations: { annotationAuthor: getPrksAnnotationAuthor() },
                    zoom: ZoomMode
                        ? { defaultZoomLevel: ZoomMode.FitWidth }
                        : undefined,
                    theme:
                        typeof window.getPrksEmbedPdfTheme === 'function'
                            ? window.getPrksEmbedPdfTheme()
                            : { preference: window.localStorage.getItem('prks-theme') || 'system' },
                });
                if (initResult && typeof initResult.then === 'function') {
                    initResult
                        .then(async (viewer) => {
                            window.currentPdfViewer = viewer;
                            await prksApplyEmbedPdfCustomizationWithRetry(viewer);
                            setupAnnotationPersistence(viewer, work.id);
                            await setupPdfLastPageMemory(viewer, work);
                        })
                        .catch((err) => {
                            console.error('EmbedPDF init failed', err);
                        });
                } else {
                    window.currentPdfViewer = initResult;
                    Promise.resolve(prksApplyEmbedPdfCustomizationWithRetry(initResult)).then(async () => {
                        setupAnnotationPersistence(initResult, work.id);
                        await setupPdfLastPageMemory(initResult, work);
                    });
                }
            }).catch(err => {
                console.error('Failed to load EmbedPDF', err);
            });
    }, 100);
}
