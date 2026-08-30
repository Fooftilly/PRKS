import { PdfAnnotationSubtype } from '@embedpdf/models';

export function isPrksMarkupType(type: unknown): boolean {
    return type === PdfAnnotationSubtype.HIGHLIGHT || type === PdfAnnotationSubtype.UNDERLINE;
}

export function prksAnnotationActions(input: {
    type: unknown;
    structurallyLocked: boolean;
    contentLocked: boolean;
}) {
    const isPrksMarkup = isPrksMarkupType(input.type);
    return {
        deletable: isPrksMarkup && !input.structurallyLocked,
        commentable: isPrksMarkup && !input.contentLocked,
        editable: isPrksMarkup && !input.structurallyLocked && !input.contentLocked,
    };
}
