import type {
    MarkupTool,
    PrksAnnotation,
    PrksAnnotationEvent,
    PrksPdfViewerHandle,
    InteractionMode,
} from './types';

type ReadyApi = {
    zoomIn: () => void;
    zoomOut: () => void;
    fitWidth: () => void;
    fitPage: () => void;
    goToPage: (pageNumber: number) => void;
    getCurrentPage: () => number;
    getPageCount: () => number;
    setInteractionMode: (mode: InteractionMode) => void;
    activateMarkupTool: (tool: MarkupTool) => void;
    clearActiveTool: () => void;
    undo: () => void;
    redo: () => void;
    getAnnotations: () => PrksAnnotation[];
    jumpToAnnotation: (annotationId: string, pageIndex?: number) => void;
    updateAnnotation: (annotationId: string, patch: Record<string, unknown>) => void;
    createAnnotation: (pageIndex: number, annotation: Record<string, unknown>) => void;
    deleteAnnotation: (id: string) => Promise<void>;
    selectAnnotation: (id: string) => void;
    saveCopy: () => Promise<ArrayBuffer>;
    getDocumentId: () => string | null;
    isSelecting: () => boolean;
};

export class ViewerController {
    readonly ready: Promise<void>;
    private resolveReady!: () => void;
    private rejectReady!: (err: Error) => void;
    private api: ReadyApi | null = null;
    private annotationListeners = new Set<(event: PrksAnnotationEvent) => void>();
    private destroyed = false;
    private failed = false;
    private destroyImpl: () => void = () => {};

    constructor() {
        this.ready = new Promise((resolve, reject) => {
            this.resolveReady = resolve;
            this.rejectReady = reject;
        });
    }

    attach(api: ReadyApi, destroyImpl: () => void) {
        this.api = api;
        this.destroyImpl = destroyImpl;
        this.resolveReady();
    }

    fail(err: Error) {
        if (this.destroyed || this.api || this.failed) return;
        this.failed = true;
        this.rejectReady(err);
    }

    emitAnnotation(event: PrksAnnotationEvent) {
        for (const fn of this.annotationListeners) fn(event);
    }

    asHandle(): PrksPdfViewerHandle {
        const need = (): ReadyApi => {
            if (this.destroyed) throw new Error('viewer destroyed');
            if (!this.api) throw new Error('viewer not ready');
            return this.api;
        };
        return {
            destroy: () => {
                if (this.destroyed) return;
                this.destroyed = true;
                this.destroyImpl();
                this.api = null;
                this.annotationListeners.clear();
            },
            zoomIn: () => need().zoomIn(),
            zoomOut: () => need().zoomOut(),
            fitWidth: () => need().fitWidth(),
            fitPage: () => need().fitPage(),
            goToPage: (pageNumber) => need().goToPage(pageNumber),
            getCurrentPage: () => (this.api ? this.api.getCurrentPage() : 1),
            getPageCount: () => (this.api ? this.api.getPageCount() : 0),
            setInteractionMode: (mode) => need().setInteractionMode(mode),
            activateMarkupTool: (tool) => need().activateMarkupTool(tool),
            clearActiveTool: () => need().clearActiveTool(),
            undo: () => need().undo(),
            redo: () => need().redo(),
            getAnnotations: () => (this.api ? this.api.getAnnotations() : []),
            jumpToAnnotation: (id, pageIndex) => need().jumpToAnnotation(id, pageIndex),
            updateAnnotation: (id, patch) => need().updateAnnotation(id, patch),
            createAnnotation: (pageIndex, annotation) => need().createAnnotation(pageIndex, annotation),
            deleteAnnotation: (id) => need().deleteAnnotation(id),
            selectAnnotation: (id) => need().selectAnnotation(id),
            onAnnotationEvent: (callback) => {
                this.annotationListeners.add(callback);
                return () => this.annotationListeners.delete(callback);
            },
            saveCopy: () => need().saveCopy(),
            getDocumentId: () => (this.api ? this.api.getDocumentId() : null),
            isSelecting: () => (this.api ? this.api.isSelecting() : false),
        };
    }
}
