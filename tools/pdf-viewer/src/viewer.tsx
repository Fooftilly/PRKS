import { useEffect, useRef } from 'react';
import { createRoot } from 'react-dom/client';
import { EmbedPDF, useRegistry } from '@embedpdf/core/react';
import { usePdfiumEngine } from '@embedpdf/engines/react';
import {
    DocumentContent,
    useDocumentManagerCapability,
} from '@embedpdf/plugin-document-manager/react';
import { Viewport } from '@embedpdf/plugin-viewport/react';
import { Scroller, useScroll, useScrollCapability } from '@embedpdf/plugin-scroll/react';
import { ZoomMode, ZoomGestureWrapper, useZoom, useZoomCapability } from '@embedpdf/plugin-zoom/react';
import {
    GlobalPointerProvider,
    useInteractionManager,
} from '@embedpdf/plugin-interaction-manager/react';
import { usePan } from '@embedpdf/plugin-pan/react';
import { useAnnotation, useAnnotationCapability } from '@embedpdf/plugin-annotation/react';
import { useHistoryCapability } from '@embedpdf/plugin-history/react';
import { useSelectionCapability } from '@embedpdf/plugin-selection/react';
import type { PdfAnnotationObject } from '@embedpdf/models';
import { ViewerController } from './controller';
import { buildPluginRegistrations, srcToInitialDocument } from './plugins';
import { PageView } from './page-view';
import { Toolbar } from './toolbar';
import { WheelZoom } from './gestures';
import type {
    PrksAnnotation,
    PrksPdfViewerHandle,
    PrksPdfViewerOptions,
} from './types';
import './styles.css';

function normalizeAnnotation(raw: PdfAnnotationObject): PrksAnnotation {
    const custom =
        raw && typeof raw === 'object' && 'custom' in raw
            ? (raw as { custom?: { prksComment?: string } }).custom
            : undefined;
    const comment =
        (custom && typeof custom.prksComment === 'string' && custom.prksComment) ||
        (typeof raw.contents === 'string' ? raw.contents : '');
    const rects =
        'segmentRects' in raw && Array.isArray((raw as { segmentRects?: unknown }).segmentRects)
            ? (raw as { segmentRects: unknown }).segmentRects
            : raw.rect;
    return {
        id: String(raw.id),
        type: String(raw.type),
        pageIndex: raw.pageIndex,
        rects,
        text: typeof raw.contents === 'string' ? raw.contents : '',
        comment,
        raw,
    };
}

function ApiBinder({
    controller,
    mode,
    initialPage,
    onPageChange,
    onAnnotationSelect,
}: {
    controller: ViewerController;
    mode: 'work' | 'preview';
    initialPage?: number;
    onPageChange?: PrksPdfViewerOptions['onPageChange'];
    onAnnotationSelect?: PrksPdfViewerOptions['onAnnotationSelect'];
}) {
    const { registry, activeDocumentId } = useRegistry();
    const { provides: docs } = useDocumentManagerCapability();
    const { provides: zoomCap } = useZoomCapability();
    const { provides: scrollCap } = useScrollCapability();
    const { provides: annotationCap } = useAnnotationCapability();
    const { provides: historyCap } = useHistoryCapability();
    const docId = activeDocumentId || '';
    const { provides: zoom } = useZoom(docId);
    const { provides: scroll, state: scrollState } = useScroll(docId);
    const { provides: pan } = usePan(docId);
    const { provides: interaction } = useInteractionManager(docId);
    const { provides: annotation } = useAnnotation(docId);
    const { provides: selectionCap } = useSelectionCapability();
    const scrollStateRef = useRef(scrollState);
    scrollStateRef.current = scrollState;
    const pendingPageRef = useRef(
        Number.isFinite(initialPage) && (initialPage as number) > 1
            ? Math.floor(initialPage as number)
            : 0,
    );
    const restoreDeadlineRef = useRef(0);

    useEffect(() => {
        if (!registry || !docs || !activeDocumentId) return;
        const engine = registry.getEngine();
        const scrollForDoc = () => scroll || scrollCap?.forDocument(activeDocumentId);
        const applyInitialPage = () => {
            const target = pendingPageRef.current;
            if (!target) return;
            if (!restoreDeadlineRef.current) restoreDeadlineRef.current = Date.now() + 8000;
            if (Date.now() > restoreDeadlineRef.current) {
                pendingPageRef.current = 0;
                return;
            }
            const sc = scrollForDoc();
            if (!sc) return;
            try {
                const total = sc.getTotalPages() || 0;
                if (total < 1) return;
                const page = Math.min(target, total);
                if (page <= 1) {
                    pendingPageRef.current = 0;
                    return;
                }
                sc.scrollToPage({ pageNumber: page, behavior: 'instant' });
            } catch {
                /* layout not ready */
            }
        };
        const handlePage = (evt: { pageNumber: number; totalPages: number }) => {
            onPageChange?.({ pageNumber: evt.pageNumber, pageCount: evt.totalPages });
        };
        const unsubPage = scrollCap?.onPageChange(handlePage);
        const unsubLayoutReady = scrollCap?.onLayoutReady((evt) => {
            if (evt.documentId && evt.documentId !== activeDocumentId) return;
            applyInitialPage();
        });
        const unsubLayoutChange = scrollCap?.onLayoutChange((evt) => {
            if (evt.documentId && evt.documentId !== activeDocumentId) return;
            applyInitialPage();
        });
        const unsubZoom = zoomCap?.onZoomChange((evt) => {
            if (evt.documentId && evt.documentId !== activeDocumentId) return;
            requestAnimationFrame(applyInitialPage);
        });
        applyInitialPage();
        const restoreTimer = pendingPageRef.current
            ? window.setInterval(() => {
                  applyInitialPage();
                  if (!pendingPageRef.current) window.clearInterval(restoreTimer);
              }, 150)
            : 0;
        const unsubAnn = annotationCap?.onAnnotationEvent((evt) => {
            if (evt.type === 'loaded') return;
            controller.emitAnnotation({
                kind: evt.type,
                annotationId: evt.annotation.id,
                documentId: evt.documentId,
                committed: evt.committed === true,
            });
            if (evt.type === 'create' && evt.committed) {
                annotation?.setActiveTool(null);
                interaction?.activate('pointerMode');
            }
        });
        let lastSelected = '';
        const unsubSel =
            mode === 'work' && annotation
                ? annotation.onStateChange(() => {
                      const selected = annotation.getSelectedAnnotations() || [];
                      const first = selected[0];
                      const id = first && first.object ? String(first.object.id) : '';
                      if (id === lastSelected) return;
                      lastSelected = id;
                      if (!id) return;
                      const pi = first.object.pageIndex;
                      onAnnotationSelect?.({
                          annotationId: id,
                          pageIndex: Number.isFinite(pi) ? pi : 0,
                      });
                  })
                : undefined;
        controller.attach(
            {
                zoomIn: () => (zoom || zoomCap?.forDocument(activeDocumentId))?.zoomIn(),
                zoomOut: () => (zoom || zoomCap?.forDocument(activeDocumentId))?.zoomOut(),
                fitWidth: () =>
                    (zoom || zoomCap?.forDocument(activeDocumentId))?.requestZoom(ZoomMode.FitWidth),
                fitPage: () =>
                    (zoom || zoomCap?.forDocument(activeDocumentId))?.requestZoom(ZoomMode.FitPage),
                goToPage: (pageNumber) => {
                    (scroll || scrollCap?.forDocument(activeDocumentId))?.scrollToPage({
                        pageNumber,
                        behavior: 'auto',
                    });
                },
                getCurrentPage: () =>
                    (scroll || scrollCap?.forDocument(activeDocumentId))?.getCurrentPage() ||
                    scrollStateRef.current.currentPage ||
                    1,
                getPageCount: () =>
                    (scroll || scrollCap?.forDocument(activeDocumentId))?.getTotalPages() ||
                    scrollStateRef.current.totalPages ||
                    0,
                setInteractionMode: (m) => {
                    annotation?.setActiveTool(null);
                    if (m === 'pan') pan?.enablePan();
                    else {
                        pan?.disablePan();
                        interaction?.activate('pointerMode');
                    }
                },
                activateMarkupTool: (tool) => {
                    if (mode !== 'work') return;
                    pan?.disablePan();
                    annotation?.setActiveTool(tool);
                },
                clearActiveTool: () => annotation?.setActiveTool(null),
                undo: () => historyCap?.forDocument(activeDocumentId)?.undo(),
                redo: () => historyCap?.forDocument(activeDocumentId)?.redo(),
                getAnnotations: () => {
                    const items = annotation?.getAnnotations() || [];
                    return items.map((ta) => normalizeAnnotation(ta.object));
                },
                jumpToAnnotation: (annotationId, pageIndex) => {
                    const got = annotation?.getAnnotationById(annotationId);
                    const obj = got?.object;
                    const pi =
                        obj?.pageIndex ??
                        (pageIndex != null && Number.isFinite(pageIndex) ? pageIndex : undefined);
                    if (pi == null || !Number.isFinite(pi)) return;
                    const rect = obj?.rect;
                    (scroll || scrollCap?.forDocument(activeDocumentId))?.scrollToPage({
                        pageNumber: pi + 1,
                        behavior: 'smooth',
                        alignX: 50,
                        alignY: 22,
                        pageCoordinates: rect
                            ? { x: rect.origin.x + rect.size.width / 2, y: rect.origin.y }
                            : undefined,
                    });
                    annotation?.selectAnnotation(pi, annotationId);
                },
                updateAnnotation: (annotationId, patch) => {
                    const got = annotation?.getAnnotationById(annotationId);
                    const obj = got?.object;
                    const pi = obj?.pageIndex;
                    if (pi == null || !Number.isFinite(pi)) return;
                    annotation?.updateAnnotation(pi, annotationId, patch as never);
                },
                createAnnotation: (pageIndex, annotationObj) => {
                    if (mode !== 'work') return;
                    annotation?.createAnnotation(pageIndex, annotationObj as never);
                },
                deleteAnnotation: async (annotationId) => {
                    if (mode !== 'work') return;
                    const got = annotation?.getAnnotationById(annotationId);
                    const obj = got?.object;
                    const pi = obj?.pageIndex;
                    if (pi == null || !Number.isFinite(pi)) return;
                    annotation?.deleteAnnotation(pi, annotationId);
                },
                selectAnnotation: (annotationId) => {
                    const got = annotation?.getAnnotationById(annotationId);
                    const obj = got?.object;
                    const pi = obj?.pageIndex;
                    if (pi == null || !Number.isFinite(pi)) return;
                    annotation?.selectAnnotation(pi, annotationId);
                },
                saveCopy: async () => {
                    await annotation?.commit()?.toPromise();
                    const pdfDoc = docs.getDocument(activeDocumentId);
                    if (!pdfDoc) throw new Error('no document');
                    const buf = await engine.saveAsCopy(pdfDoc).toPromise();
                    return buf;
                },
                getDocumentId: () => activeDocumentId,
                isSelecting: () => {
                    const scope = selectionCap?.forDocument(activeDocumentId);
                    return !!scope?.getState()?.selecting;
                },
            },
            () => {},
        );
        return () => {
            window.clearInterval(restoreTimer);
            if (typeof unsubPage === 'function') unsubPage();
            if (typeof unsubAnn === 'function') unsubAnn();
            if (typeof unsubSel === 'function') unsubSel();
            if (typeof unsubLayoutReady === 'function') unsubLayoutReady();
            if (typeof unsubLayoutChange === 'function') unsubLayoutChange();
            if (typeof unsubZoom === 'function') unsubZoom();
        };
    }, [
        registry,
        docs,
        activeDocumentId,
        zoom,
        zoomCap,
        scroll,
        scrollCap,
        pan,
        interaction,
        annotation,
        annotationCap,
        historyCap,
        selectionCap,
        controller,
        mode,
        onPageChange,
        onAnnotationSelect,
    ]);

    return null;
}

function ViewerTree({
    options,
    controller,
    plugins,
    wasmUrl,
}: {
    options: PrksPdfViewerOptions;
    controller: ViewerController;
    plugins: ReturnType<typeof buildPluginRegistrations>;
    wasmUrl: string;
}) {
    const { engine, isLoading, error } = usePdfiumEngine({
        wasmUrl,
        worker: true,
        fontFallback: null,
    });
    const mode = options.mode || 'work';

    useEffect(() => {
        if (error) {
            controller.fail(error instanceof Error ? error : new Error(String(error)));
            options.onError?.(error instanceof Error ? error : new Error(String(error)));
        }
    }, [error, options, controller]);

    if (error) {
        return <div className="prks-pdf-status prks-pdf-status--error">PDF engine failed to load.</div>;
    }
    if (isLoading || !engine) {
        return <div className="prks-pdf-status">Loading PDF engine…</div>;
    }

    return (
        <EmbedPDF engine={engine} plugins={plugins}>
            {({ activeDocumentId }) => (
                <>
                    <ApiBinder
                        controller={controller}
                        mode={mode}
                        initialPage={options.initialPage}
                        onPageChange={options.onPageChange}
                        onAnnotationSelect={options.onAnnotationSelect}
                    />
                    {activeDocumentId ? (
                        <Toolbar
                            documentId={activeDocumentId}
                            mode={mode}
                            documentTitle={options.documentTitle}
                            documentTypeLabel={options.documentTypeLabel}
                            documentTypeColor={options.documentTypeColor}
                            documentTypeBorder={options.documentTypeBorder}
                        />
                    ) : null}
                    <div className="prks-pdf-stage">
                        <DocumentContent documentId={activeDocumentId}>
                            {({ isLoading: docLoading, isError, isLoaded }) => {
                                if (docLoading) {
                                    return <div className="prks-pdf-status">Loading document…</div>;
                                }
                                if (isError) {
                                    controller.fail(new Error('Could not open PDF'));
                                    return (
                                        <div className="prks-pdf-status prks-pdf-status--error">
                                            Could not open PDF.
                                        </div>
                                    );
                                }
                                if (!isLoaded || !activeDocumentId) return null;
                                return (
                                    <GlobalPointerProvider documentId={activeDocumentId}>
                                        <div className="prks-pdf-viewport-host">
                                            <Viewport documentId={activeDocumentId} className="prks-pdf-viewport">
                                                <ZoomGestureWrapper
                                                    documentId={activeDocumentId}
                                                    enableWheel={false}
                                                >
                                                    <Scroller
                                                        documentId={activeDocumentId}
                                                        renderPage={(layout) => (
                                                            <PageView
                                                                documentId={activeDocumentId}
                                                                layout={layout}
                                                                workMode={mode === 'work'}
                                                                onCommentRequest={
                                                                    options.onAnnotationCommentRequest
                                                                }
                                                            />
                                                        )}
                                                    />
                                                </ZoomGestureWrapper>
                                                <WheelZoom documentId={activeDocumentId} />
                                            </Viewport>
                                        </div>
                                    </GlobalPointerProvider>
                                );
                            }}
                        </DocumentContent>
                    </div>
                </>
            )}
        </EmbedPDF>
    );
}

export async function createPrksPdfViewer(
    options: PrksPdfViewerOptions,
): Promise<PrksPdfViewerHandle> {
    const initial = await srcToInitialDocument(options.src);
    const plugins = buildPluginRegistrations(
        [initial],
        options.annotationAuthor,
    );
    const host = document.createElement('div');
    host.className = 'prks-pdf-viewer';
    host.style.height = '100%';
    const base = (options.assetBaseUrl || '/vendor/prks-pdf-viewer/').replace(/\/?$/, '/');
    const wasmUrl = new URL(`${base}pdfium.wasm`, window.location.origin).href;
    host.dataset.wasmUrl = wasmUrl;
    options.target.appendChild(host);
    try {
        await fetch(wasmUrl);
    } catch {
        /* engine load reports wasm failures */
    }
    const controller = new ViewerController();
    const root = createRoot(host);
    root.render(
        <ViewerTree
            options={options}
            controller={controller}
            plugins={plugins}
            wasmUrl={wasmUrl}
        />,
    );
    const handle = controller.asHandle();
    if (options.onAnnotationChange) {
        handle.onAnnotationEvent(options.onAnnotationChange);
    }
    const origDestroy = handle.destroy.bind(handle);
    handle.destroy = () => {
        origDestroy();
        root.unmount();
        host.remove();
    };
    await controller.ready;
    options.onReady?.(handle);
    return handle;
}
