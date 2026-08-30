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
