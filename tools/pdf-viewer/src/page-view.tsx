import type { CSSProperties } from 'react';
import { PagePointerProvider } from '@embedpdf/plugin-interaction-manager/react';
import { RenderLayer } from '@embedpdf/plugin-render/react';
import { SelectionLayer } from '@embedpdf/plugin-selection/react';
import { AnnotationLayer } from '@embedpdf/plugin-annotation/react';
import type { PageLayout } from '@embedpdf/plugin-scroll';
import { SelectionMenu } from './selection-menu';
import { AnnotationMenu } from './annotation-menu';

export function PageView({
    documentId,
    layout,
    workMode,
    onCommentRequest,
}: {
    documentId: string;
    layout: PageLayout;
    workMode: boolean;
    onCommentRequest?: (info: { annotationId: string; pageIndex: number }) => void;
}) {
    const box: CSSProperties = {
        width: layout.width,
        height: layout.height,
        position: 'relative',
    };

    return (
        <div
            className="prks-pdf-page"
            style={box}
            onDragStartCapture={(event) => {
                event.preventDefault();
            }}
        >
            <PagePointerProvider documentId={documentId} pageIndex={layout.pageIndex}>
                <RenderLayer
                    documentId={documentId}
                    pageIndex={layout.pageIndex}
                    draggable={false}
                    className="prks-pdf-render-image"
                />
                <AnnotationLayer
                    documentId={documentId}
                    pageIndex={layout.pageIndex}
                    selectionMenu={
                        workMode
                            ? (props) => (
                                  <AnnotationMenu
                                      {...props}
                                      documentId={documentId}
                                      onCommentRequest={onCommentRequest}
                                  />
                              )
                            : undefined
                    }
                />
                <SelectionLayer
                    documentId={documentId}
                    pageIndex={layout.pageIndex}
                    selectionMenu={
                        workMode
                            ? (props) => <SelectionMenu {...props} documentId={documentId} />
                            : undefined
                    }
                />
            </PagePointerProvider>
        </div>
    );
}
