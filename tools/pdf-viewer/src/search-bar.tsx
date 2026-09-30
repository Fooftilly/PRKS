import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useSearch } from '@embedpdf/plugin-search/react';
import type { ViewerController } from './controller';
import { pdfSearchBarView, type PdfSearchBarSettlement } from './search-bar-view';

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
    const [pending, setPending] = useState(false);
    const [settled, setSettled] = useState<PdfSearchBarSettlement>(null);

    useEffect(() => {
        controller.bindSearchChrome({
            open: () => setOpen(true),
            close: () => {
                setOpen(false);
                setDraft('');
                setPending(false);
                setSettled(null);
            },
            focus: () => {
                const input = inputRef.current;
                if (!input) return;
                input.focus();
                input.select();
            },
            setQuery: (query) => {
                setDraft(query);
                if (!query.trim()) {
                    setPending(false);
                    setSettled(null);
                    return;
                }
                setPending(true);
                setSettled(null);
            },
            applySettlement: (result) => {
                setPending(false);
                setSettled({ total: result.total, activeIndex: result.activeIndex });
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
            if (!value.trim()) {
                setPending(false);
                setSettled(null);
            } else {
                setPending(true);
                setSettled(null);
            }
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

    const view = pdfSearchBarView({
        draft,
        plugin: state,
        settled,
        pending,
        followRuntime: controller.hasSearchDriver(),
    });
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
                {view.label}
            </span>
            <button type="button" aria-label="Previous match" disabled={view.matchesDisabled} onClick={previous}>
                Previous
            </button>
            <button type="button" aria-label="Next match" disabled={view.matchesDisabled} onClick={next}>
                Next
            </button>
            <button type="button" aria-label="Close find" onClick={close}>
                Close
            </button>
        </form>
    );
}
