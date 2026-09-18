export type ViewerMode = 'work' | 'preview';
export type InteractionMode = 'pointer' | 'pan';
export type MarkupTool = 'highlight' | 'underline';

export type ViewerSrc = string | Blob | ArrayBuffer;

export interface PrksPdfViewerOptions {
    target: HTMLElement;
    src: ViewerSrc;
    mode?: ViewerMode;
    annotationAuthor?: string;
    documentTitle?: string;
    documentTypeLabel?: string;
    documentTypeColor?: string;
    documentTypeBorder?: string;
    initialZoom?: 'fit-width' | 'fit-page';
    /** 1-based page to open after the first layout is ready. */
    initialPage?: number;
    assetBaseUrl?: string;
    onReady?: (viewer: PrksPdfViewerHandle) => void;
    onPageChange?: (info: { pageNumber: number; pageCount: number }) => void;
    onAnnotationChange?: (event: PrksAnnotationEvent) => void;
    onAnnotationSelect?: (info: { annotationId: string; pageIndex: number }) => void;
    onAnnotationCommentRequest?: (info: { annotationId: string; pageIndex: number }) => void;
    onError?: (error: Error) => void;
}

export interface PrksAnnotation {
    id: string;
    type: string;
    pageIndex: number;
    rects: unknown;
    text: string;
    comment: string;
    raw: unknown;
}

export interface PrksAnnotationEvent {
    kind: 'create' | 'update' | 'delete';
    annotationId: string;
    documentId: string;
    committed: boolean;
}

export interface PrksPdfViewerHandle {
    destroy(): void;
    /**
     * Enable/disable user-driven annotation mutation. Synchronous: the
     * controller gate flips before any React mode rerender. Disabling leaves
     * existing annotations rendered and scroll/zoom/page navigation working;
     * it removes markup-tool activation and blocks user-driven
     * create/update/delete.
     *
     * Live user-input mutation lock, independent of document/viewer lifetime.
     * Never recreates the PDF engine or document — toggles the same work-mode
     * controller gate in place.
     *
     * Programmatic create/update/delete used by PRKS reconcile still run while
     * disabled when wrapped in begin/endProgrammaticAnnotationMutation.
     */
    setMutationEnabled(enabled: boolean): void;
    /**
     * Allow create/update/deleteAnnotation while user input is locked
     * (preview / setMutationEnabled(false)). Nestable; must be balanced.
     */
    beginProgrammaticAnnotationMutation(): void;
    endProgrammaticAnnotationMutation(): void;
    zoomIn(): void;
    zoomOut(): void;
    fitWidth(): void;
    fitPage(): void;
    goToPage(pageNumber: number): void;
    getCurrentPage(): number;
    getPageCount(): number;
    setInteractionMode(mode: InteractionMode): void;
    activateMarkupTool(tool: MarkupTool): void;
    clearActiveTool(): void;
    undo(): void;
    redo(): void;
    getAnnotations(): PrksAnnotation[];
    jumpToAnnotation(annotationId: string, pageIndex?: number): void;
    updateAnnotation(annotationId: string, patch: Record<string, unknown>): void;
    createAnnotation(pageIndex: number, annotation: Record<string, unknown>): void;
    deleteAnnotation(annotationId: string): Promise<void>;
    selectAnnotation(annotationId: string): void;
    onAnnotationEvent(callback: (event: PrksAnnotationEvent) => void): () => void;
    saveCopy(): Promise<ArrayBuffer>;
    getDocumentId(): string | null;
    isSelecting(): boolean;
}
