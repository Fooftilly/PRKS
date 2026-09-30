import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useSearch } from '@embedpdf/plugin-search/react';
import type { SearchDocumentState } from '@embedpdf/plugin-search';
import type { ViewerController } from './controller';

function matchLabel(draft: string, state: SearchDocumentState | null): string {
    if (!draft.trim()) return '';
    if (state?.loading) return 'Searching';
    const total = state && typeof state.total === 'number' ? state.total : 0;
    if (total < 1) return 'No matches';
    const active = state && state.activeResultIndex >= 0 ? state.activeResultIndex + 1 : 1;
    return `${active} of ${total}`;
}

/**
 * First-party find bar. It overlays the page and does not mount a document.
 * Match highlights come from the search plugin's layer, not from annotations.
 */
export function PdfSearchBar({
    documentId,
    controller,
    ownerTabId,
    ownerGeneration,
}: {
    documentId: string;
    controller: ViewerController;
    ownerTabId?: string;
    ownerGeneration?: number | string;
}) {
    const { state, provides } = useSearch(documentId);
    const countId = useId();
    const inputRef = useRef<HTMLInputElement | null>(null);
    const [open, setOpen] = useState(false);
    const [draft, setDraft] = useState('');

    useEffect(() => {
        controller.bindSearchChrome({
            open: () => setOpen(true),
            close: () => {
                setOpen(false);
                setDraft('');
            },
            focus: () => {
                const input = inputRef.current;
                if (!input) return;
                input.focus();
                input.select();
            },
        });
    }, [controller]);

    useEffect(() => {
        if (!open) return;
        inputRef.current?.focus();
    }, [open]);

    const close = () => {
        if (controller.hasSearchDriver()) controller.emitSearchClose();
        else {
            try {
                provides?.stopSearch();
            } catch {
                /* session already stopped */
            }
            setOpen(false);
            setDraft('');
        }
    };

    const next = () => {
        if (controller.hasSearchDriver()) controller.emitSearchNext();
        else provides?.nextResult();
    };

    const previous = () => {
        if (controller.hasSearchDriver()) controller.emitSearchPrevious();
        else provides?.previousResult();
    };

    const onDraft = (value: string) => {
        setDraft(value);
        if (controller.hasSearchDriver()) {
            controller.emitSearchQuery(value);
            return;
        }
        try {
            provides?.searchAllPages(value);
        } catch {
            /* document not ready */
        }
    };

    const onKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            close();
            return;
        }
        if (event.key === 'Enter') {
            event.preventDefault();
            event.stopPropagation();
            if (event.shiftKey) previous();
            else next();
        }
    };

    const total = state && typeof state.total === 'number' ? state.total : 0;
    const label = matchLabel(draft, state);
    const owner = ownerTabId ? String(ownerTabId) : '';
    const generation =
        ownerGeneration == null || ownerGeneration === '' ? '' : String(ownerGeneration);

    return (
        <form
            className="prks-pdf-search"
            role="search"
            hidden={!open}
            data-prks-role="pdf-search"
            data-prks-owner-tab-id={owner || undefined}
            data-prks-owner-generation={generation || undefined}
            onSubmit={(event) => {
                event.preventDefault();
                next();
            }}
        >
            <input
                ref={inputRef}
                className="prks-pdf-search__query"
                type="text"
                data-prks-role="pdf-search-query"
                aria-label="Find in document"
                aria-describedby={countId}
                autoComplete="off"
                spellCheck={false}
                value={draft}
                onChange={(event) => onDraft(event.target.value)}
                onKeyDown={onKeyDown}
            />
            <span id={countId} className="prks-pdf-search__count" aria-live="polite">
                {label}
            </span>
            <button type="button" aria-label="Previous match" disabled={total < 1} onClick={previous}>
                Previous
            </button>
            <button type="button" aria-label="Next match" disabled={total < 1} onClick={next}>
                Next
            </button>
            <button type="button" aria-label="Close find" onClick={close}>
                Close
            </button>
        </form>
    );
}
