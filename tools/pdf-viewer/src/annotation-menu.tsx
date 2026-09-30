import { useEffect, useRef } from 'react';
import { useAnnotation } from '@embedpdf/plugin-annotation/react';
import type { AnnotationSelectionMenuProps } from '@embedpdf/plugin-annotation/react';
import { prksAnnotationActions } from './annotation-actions';
import { FloatingMenu, MenuButton } from './floating-menu';

/**
 * Selection stays with EmbedPDF. This slot only publishes an anchor for the
 * PRKS comment popup, or a delete affordance when the annotation cannot take
 * a comment. It does not store the comment.
 */
export function AnnotationMenu({
    documentId,
    selected,
    menuWrapperProps,
    rect,
    context,
    onCommentRequest,
    onCommentDismiss,
    onDeleteRequest,
}: AnnotationSelectionMenuProps & {
    documentId: string;
    onCommentRequest?: (info: {
        annotationId: string;
        pageIndex: number;
        deletable: boolean;
    }) => void;
    onCommentDismiss?: (info: { annotationId: string }) => void;
    onDeleteRequest?: (info: { annotationId: string; pageIndex: number }) => void;
}) {
    const { provides: annotation } = useAnnotation(documentId);
    const requestRef = useRef(onCommentRequest);
    const dismissRef = useRef(onCommentDismiss);
    requestRef.current = onCommentRequest;
    dismissRef.current = onCommentDismiss;

    const annotationContext = context.type === 'annotation' ? context : null;
    const obj = annotationContext ? annotationContext.annotation.object : null;
    const id = obj ? String(obj.id) : '';
    const pageIndex = annotationContext ? annotationContext.pageIndex : 0;
    const actions =
        obj && annotation
            ? prksAnnotationActions({
                  type: obj.type,
                  structurallyLocked: annotationContext ? annotationContext.structurallyLocked : false,
                  contentLocked: annotationContext ? annotationContext.contentLocked : false,
              })
            : null;
    const commentable = !!(selected && actions && actions.commentable);

    useEffect(() => {
        if (!commentable || !id) return;
        requestRef.current?.({ annotationId: id, pageIndex, deletable: !!actions?.deletable });
        return () => {
            dismissRef.current?.({ annotationId: id });
        };
    }, [commentable, id, pageIndex]);

    if (!selected || !annotation || !annotationContext || !actions || !obj) return null;
    if (!actions.commentable && !actions.deletable) return null;

    if (actions.commentable) {
        return (
            <div
                ref={menuWrapperProps.ref}
                style={{
                    ...(menuWrapperProps.style || {}),
                    pointerEvents: 'none',
                }}
                data-no-interaction=""
            >
                <div
                    data-prks-role="pdf-annotation-anchor"
                    data-prks-annotation-id={id}
                    data-no-interaction=""
                    style={{
                        width: Math.max(1, rect.size.width),
                        height: Math.max(1, rect.size.height),
                        pointerEvents: 'none',
                    }}
                />
            </div>
        );
    }

    return (
        <FloatingMenu
            menuWrapperProps={menuWrapperProps}
            rect={rect}
            preferAbove
            className="prks-pdf-annotation-menu"
            onEscape={() => annotation.deselectAnnotation()}
        >
            <MenuButton
                className="prks-pdf-floating-menu__danger"
                title="Delete"
                aria-label="Delete"
                onActivate={() => onDeleteRequest?.({ annotationId: id, pageIndex })}
            >
                Delete
            </MenuButton>
        </FloatingMenu>
    );
}
