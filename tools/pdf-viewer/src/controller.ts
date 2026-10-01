import type {
    MarkupTool,
    PrksAnnotation,
    PrksAnnotationEvent,
    PrksPdfSearchDriver,
    PrksPdfSearchSettled,
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
    deselectAnnotation: () => void;
    saveCopy: () => Promise<ArrayBuffer>;
    getDocumentId: () => string | null;
    isSelecting: () => boolean;
    openSearch: () => void;
    closeSearch: () => void;
    commitSearch: (query: string, epoch: number) => void;
    clearSearchMatches: () => void;
    searchNext: () => number;
    searchPrevious: () => number;
};

type SearchChrome = {
    open: () => void;
    close: () => void;
    focus: () => void;
    /** Display-only. Must not emit another query intent. */
    setQuery?: (query: string) => void;
    applySettlement?: (result: PrksPdfSearchSettled) => void;
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
    /** Nestable depth: create/update/delete may run while user input is locked. */
    private programmaticMutationDepth = 0;
    private searchDriver: PrksPdfSearchDriver | null = null;
    private searchSeq = 0;
    private searchChrome: SearchChrome = {
        open: () => {},
        close: () => {},
        focus: () => {},
        setQuery: () => {},
        applySettlement: () => {},
    };
    private drawerOpen = false;
    private drawerChrome: { setOpen: (open: boolean) => void } = { setOpen: () => {} };
    /**
     * Synchronous user-mutation gate. Updated immediately by setMutationEnabled
     * — do not rely only on React mode rerender for create/update/delete.
     */
    private userMutationEnabled = true;

    constructor() {
        this.ready = new Promise((resolve, reject) => {
            this.resolveReady = resolve;
            this.rejectReady = reject;
        });
    }

    setUserMutationEnabled(enabled: boolean) {
        this.userMutationEnabled = !!enabled;
    }

    allowsUserAnnotationMutation() {
        return this.userMutationEnabled;
    }

    beginProgrammaticAnnotationMutation() {
        this.programmaticMutationDepth += 1;
    }

    endProgrammaticAnnotationMutation() {
        this.programmaticMutationDepth = Math.max(0, this.programmaticMutationDepth - 1);
    }

    allowsProgrammaticAnnotationMutation() {
        return this.programmaticMutationDepth > 0;
    }

    /** User input enabled, or reconcile wrapped in begin/endProgrammatic. */
    allowsAnnotationMutation() {
        return this.userMutationEnabled || this.programmaticMutationDepth > 0;
    }

    setSearchDriver(driver: PrksPdfSearchDriver | null) {
        this.searchDriver = driver;
    }

    hasSearchDriver() {
        return !!this.searchDriver;
    }

    bindSearchChrome(chrome: SearchChrome) {
        this.searchChrome = chrome;
    }

    bindAnnotationDrawerChrome(chrome: { setOpen: (open: boolean) => void }) {
        this.drawerChrome = chrome;
        chrome.setOpen(this.drawerOpen);
    }

    setAnnotationDrawerOpen(open: boolean) {
        this.drawerOpen = !!open;
        this.drawerChrome.setOpen(this.drawerOpen);
    }

    presentSearch() {
        this.searchChrome.open();
    }

    dismissSearch() {
        this.searchChrome.close();
    }

    focusSearch() {
        this.searchChrome.focus();
    }

    emitSearchQuery(query: string) {
        if (this.searchDriver) this.searchDriver.onQuery(query);
    }

    emitSearchNext() {
        if (this.searchDriver) this.searchDriver.onNext();
    }

    emitSearchPrevious() {
        if (this.searchDriver) this.searchDriver.onPrevious();
    }

    emitSearchClose() {
        if (this.searchDriver) this.searchDriver.onClose();
    }

    emitSearchSettled(result: PrksPdfSearchSettled) {
        if (this.searchDriver) this.searchDriver.onSettled(result);
        if (typeof this.searchChrome.applySettlement === 'function') {
            this.searchChrome.applySettlement(result);
        }
    }

    private showSearchQuery(query: string) {
        if (typeof this.searchChrome.setQuery === 'function') this.searchChrome.setQuery(query);
    }

    nextSearchSeq() {
        this.searchSeq += 1;
        return this.searchSeq;
    }

    searchSeqCurrent() {
        return this.searchSeq;
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
            // Overwritten in createPrksPdfViewer() with the real live-mode
            // toggle -- this placeholder syncs the controller gate only.
            setMutationEnabled: (enabled: boolean) => {
                this.setUserMutationEnabled(enabled);
            },
            beginProgrammaticAnnotationMutation: () => {
                this.beginProgrammaticAnnotationMutation();
            },
            endProgrammaticAnnotationMutation: () => {
                this.endProgrammaticAnnotationMutation();
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
            deselectAnnotation: () => need().deselectAnnotation(),
            onAnnotationEvent: (callback) => {
                this.annotationListeners.add(callback);
                return () => this.annotationListeners.delete(callback);
            },
            saveCopy: () => need().saveCopy(),
            getDocumentId: () => (this.api ? this.api.getDocumentId() : null),
            isSelecting: () => (this.api ? this.api.isSelecting() : false),
            openSearch: () => {
                if (this.destroyed || !this.api) return;
                this.api.openSearch();
            },
            closeSearch: () => {
                if (this.destroyed || !this.api) return;
                this.api.closeSearch();
            },
            commitSearch: (query, epoch) => {
                if (this.destroyed || !this.api) return;
                this.showSearchQuery(query);
                this.api.commitSearch(query, epoch);
            },
            clearSearchMatches: () => {
                if (this.destroyed || !this.api) return;
                this.showSearchQuery('');
                this.api.clearSearchMatches();
            },
            searchNext: () => (this.api && !this.destroyed ? this.api.searchNext() : -1),
            searchPrevious: () => (this.api && !this.destroyed ? this.api.searchPrevious() : -1),
            setSearchDriver: (driver) => {
                this.setSearchDriver(driver);
            },
            setAnnotationDrawerOpen: (open: boolean) => {
                if (this.destroyed) return;
                this.setAnnotationDrawerOpen(open);
            },
        };
    }
}
