import { createPluginRegistration } from '@embedpdf/core';
import { DocumentManagerPluginPackage } from '@embedpdf/plugin-document-manager/react';
import { ViewportPluginPackage } from '@embedpdf/plugin-viewport/react';
import { ScrollPluginPackage } from '@embedpdf/plugin-scroll/react';
import { RenderPluginPackage } from '@embedpdf/plugin-render/react';
import { ZoomMode, ZoomPluginPackage } from '@embedpdf/plugin-zoom/react';
import { InteractionManagerPluginPackage } from '@embedpdf/plugin-interaction-manager/react';
import { PanPluginPackage } from '@embedpdf/plugin-pan/react';
import { SelectionPluginPackage } from '@embedpdf/plugin-selection/react';
import { HistoryPluginPackage } from '@embedpdf/plugin-history/react';
import { AnnotationPluginPackage } from '@embedpdf/plugin-annotation/react';
import type { InitialDocumentOptions } from '@embedpdf/plugin-document-manager';
import type { ViewerSrc } from './types';
import { PRKS_MARKUP } from './markup';

export async function srcToInitialDocument(src: ViewerSrc): Promise<InitialDocumentOptions> {
    if (typeof src === 'string') {
        return { url: src };
    }
    if (src instanceof ArrayBuffer) {
        return { buffer: src.slice(0), name: 'document.pdf' };
    }
    const name = src instanceof File && src.name ? src.name : 'document.pdf';
    const buffer = await src.arrayBuffer();
    return { buffer, name };
}

export function buildPluginRegistrations(
    initialDocuments: InitialDocumentOptions[],
    annotationAuthor?: string,
) {
    return [
        createPluginRegistration(DocumentManagerPluginPackage, { initialDocuments }),
        createPluginRegistration(ViewportPluginPackage),
        createPluginRegistration(ScrollPluginPackage),
        createPluginRegistration(RenderPluginPackage),
        createPluginRegistration(ZoomPluginPackage, {
            defaultZoomLevel: ZoomMode.FitWidth,
        }),
        createPluginRegistration(InteractionManagerPluginPackage),
        createPluginRegistration(PanPluginPackage, { defaultMode: 'never' }),
        createPluginRegistration(SelectionPluginPackage, {
            marquee: { enabled: false },
            menuHeight: 40,
        }),
        createPluginRegistration(HistoryPluginPackage),
        createPluginRegistration(AnnotationPluginPackage, {
            annotationAuthor: annotationAuthor || 'PRKS',
            tools: [
                { id: 'highlight', defaults: { ...PRKS_MARKUP.highlight } },
                { id: 'underline', defaults: { ...PRKS_MARKUP.underline } },
            ],
        }),
    ];
}
