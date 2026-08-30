import { useAnnotation } from '@embedpdf/plugin-annotation/react';
import type { AnnotationSelectionMenuProps } from '@embedpdf/plugin-annotation/react';
import { useCallback } from 'react';
import { prksAnnotationActions } from './annotation-actions';
import { FloatingMenu, MenuButton } from './floating-menu';

export function AnnotationMenu({
    documentId,
    selected,
    menuWrapperProps,
    rect,
    context,
    onCommentRequest,
}: AnnotationSelectionMenuProps & {
    documentId: string;
    onCommentRequest?: (info: { annotationId: string; pageIndex: number }) => void;
}) {
    const { provides: annotation } = useAnnotation(documentId);
    const dismiss = useCallback(() => {
        annotation?.deselectAnnotation();
    }, [annotation]);

    if (!selected || !annotation || context.type !== 'annotation') return null;
    const obj = context.annotation.object;
    const actions = prksAnnotationActions({
        type: obj.type,
        structurallyLocked: context.structurallyLocked,
        contentLocked: context.contentLocked,
    });
    if (!actions.commentable && !actions.deletable) return null;
    const id = String(obj.id);
    const pageIndex = context.pageIndex;

    return (
        <FloatingMenu
            menuWrapperProps={menuWrapperProps}
            rect={rect}
            preferAbove
            className="prks-pdf-annotation-menu"
            onEscape={dismiss}
        >
            {actions.commentable ? (
                <MenuButton
                    title="Edit comment"
                    aria-label="Edit comment"
                    onActivate={() => onCommentRequest?.({ annotationId: id, pageIndex })}
                >
                    Edit comment
                </MenuButton>
            ) : null}
            {actions.deletable ? (
                <MenuButton
                    className="prks-pdf-floating-menu__danger"
                    title="Delete"
                    aria-label="Delete"
                    onActivate={() => annotation.deleteAnnotation(pageIndex, id)}
                >
                    Delete
                </MenuButton>
            ) : null}
        </FloatingMenu>
    );
}
