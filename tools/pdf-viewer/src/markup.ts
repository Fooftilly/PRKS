import { PdfAnnotationSubtype, PdfBlendMode } from '@embedpdf/models';

export const PRKS_MARKUP = {
    highlight: {
        type: PdfAnnotationSubtype.HIGHLIGHT,
        color: '#FFCD45',
        strokeColor: '#FFCD45',
        opacity: 1,
        blendMode: PdfBlendMode.Multiply,
    },
    underline: {
        type: PdfAnnotationSubtype.UNDERLINE,
        color: '#2563eb',
        strokeColor: '#2563eb',
        opacity: 1,
    },
} as const;
