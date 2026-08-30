import { useAnnotation } from '@embedpdf/plugin-annotation/react';
import { useSelectionCapability } from '@embedpdf/plugin-selection/react';
import type { SelectionSelectionMenuProps } from '@embedpdf/plugin-selection/react';
import { useCallback, useRef } from 'react';
import { PRKS_MARKUP } from './markup';
import { FloatingMenu, MenuButton } from './floating-menu';
import { Icon, ICONS } from './icons';

export function SelectionMenu({
    documentId,
    selected,
    menuWrapperProps,
    rect,
    placement,
}: SelectionSelectionMenuProps & { documentId: string }) {
    const { provides: annotation } = useAnnotation(documentId);
    const { provides: selectionCap } = useSelectionCapability();
    const formattedRef = useRef(selectionCap?.forDocument(documentId)?.getFormattedSelection() || []);
    formattedRef.current = selectionCap?.forDocument(documentId)?.getFormattedSelection() || [];

    const dismiss = useCallback(() => {
        selectionCap?.forDocument(documentId)?.clear();
        annotation?.setActiveTool(null);
    }, [annotation, documentId, selectionCap]);

    if (!selected || !annotation) return null;

    const apply = (tool: 'highlight' | 'underline') => {
        const scope = selectionCap?.forDocument(documentId);
        const live = scope?.getFormattedSelection() || [];
        const formatted = live.length ? live : formattedRef.current;
        const markup = PRKS_MARKUP[tool];
        let lastId: string | null = null;
        let lastPage = 0;
        for (const item of formatted) {
            const rects = item.segmentRects && item.segmentRects.length ? item.segmentRects : [item.rect];
            const id = crypto.randomUUID();
            lastId = id;
            lastPage = item.pageIndex;
            annotation.createAnnotation(item.pageIndex, {
                id,
                ...markup,
                pageIndex: item.pageIndex,
                rect: item.rect,
                segmentRects: rects,
                created: new Date(),
            } as never);
        }
        annotation.setActiveTool(null);
        if (lastId) annotation.selectAnnotation(lastPage, lastId);
        scope?.clear();
    };

    return (
        <FloatingMenu
            menuWrapperProps={menuWrapperProps}
            rect={rect}
            preferAbove={placement?.suggestTop !== false}
            className="prks-pdf-selection-popup"
            onEscape={dismiss}
        >
            <MenuButton title="Highlight" aria-label="Highlight" onActivate={() => apply('highlight')}>
                <Icon d={ICONS.highlight} size={18} />
            </MenuButton>
            <MenuButton title="Underline" aria-label="Underline" onActivate={() => apply('underline')}>
                <Icon d={ICONS.underline} size={18} />
            </MenuButton>
            <MenuButton
                title="Copy"
                aria-label="Copy"
                onActivate={() => selectionCap?.forDocument(documentId)?.copyToClipboard()}
            >
                <Icon d={ICONS.copy} size={18} />
            </MenuButton>
        </FloatingMenu>
    );
}
