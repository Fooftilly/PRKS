import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { ZoomMode, useZoom } from '@embedpdf/plugin-zoom/react';
import { useScroll } from '@embedpdf/plugin-scroll/react';
import { usePan } from '@embedpdf/plugin-pan/react';
import { useAnnotation } from '@embedpdf/plugin-annotation/react';
import { useHistoryCapability } from '@embedpdf/plugin-history/react';
import { useInteractionManager } from '@embedpdf/plugin-interaction-manager/react';
import type { ViewerMode } from './types';
import { Icon, ICONS } from './icons';

const ZOOM_PRESETS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3] as const;

function MarkupTools({
    toolId,
    onHighlight,
    onUnderline,
}: {
    toolId: string | null;
    onHighlight: () => void;
    onUnderline: () => void;
}) {
    return (
        <>
            <button
                type="button"
                className={toolId === 'highlight' ? 'is-active' : ''}
                title="Highlight"
                aria-label="Highlight"
                aria-pressed={toolId === 'highlight'}
                onClick={onHighlight}
            >
                <Icon d={ICONS.highlight} />
            </button>
            <button
                type="button"
                className={toolId === 'underline' ? 'is-active' : ''}
                title="Underline"
                aria-label="Underline"
                aria-pressed={toolId === 'underline'}
                onClick={onUnderline}
            >
                <Icon d={ICONS.underline} />
            </button>
        </>
    );
}

function HistoryTools({ onUndo, onRedo }: { onUndo: () => void; onRedo: () => void }) {
    return (
        <>
            <button type="button" title="Undo" aria-label="Undo" onClick={onUndo}>
                <Icon d={ICONS.undo} />
            </button>
            <button type="button" title="Redo" aria-label="Redo" onClick={onRedo}>
                <Icon d={ICONS.redo} />
            </button>
        </>
    );
}

function PageField({
    page,
    total,
    onGo,
}: {
    page: number;
    total: number;
    onGo: (n: number) => void;
}) {
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState(String(page));
    const inputRef = useRef<HTMLInputElement | null>(null);
    const editingRef = useRef(false);
    const digits = Math.max(2, String(total || 0).length);

    useEffect(() => {
        if (!editing) setDraft(String(page));
    }, [page, editing]);

    const commit = () => {
        if (!editingRef.current) {
            setDraft(String(page));
            return;
        }
        editingRef.current = false;
        setEditing(false);
        const raw = draft.trim();
        if (!raw || !/^\d+$/.test(raw)) {
            setDraft(String(page));
            return;
        }
        const n = parseInt(raw, 10);
        if (!Number.isFinite(n)) {
            setDraft(String(page));
            return;
        }
        const next = Math.min(total || 1, Math.max(1, n));
        setDraft(String(next));
        if (next !== page) onGo(next);
    };

    const cancel = () => {
        editingRef.current = false;
        setEditing(false);
        setDraft(String(page));
    };

    const isolate = (event: ReactKeyboardEvent) => {
        event.stopPropagation();
    };

    return (
        <span className="prks-pdf-toolbar__pages">
            <input
                ref={inputRef}
                className="prks-pdf-toolbar__page-input"
                type="text"
                inputMode="numeric"
                autoComplete="off"
                spellCheck={false}
                disabled={!total}
                aria-label="Page number"
                title="Page number"
                value={editing ? draft : String(page)}
                style={{ width: `${digits + 0.8}ch` }}
                onFocus={(event) => {
                    editingRef.current = true;
                    setEditing(true);
                    setDraft(String(page));
                    const el = event.currentTarget;
                    requestAnimationFrame(() => el.select());
                }}
                onChange={(event) => setDraft(event.target.value)}
                onBlur={commit}
                onKeyDown={(event) => {
                    isolate(event);
                    if (event.key === 'Enter') {
                        event.preventDefault();
                        commit();
                        inputRef.current?.blur();
                    } else if (event.key === 'Escape') {
                        event.preventDefault();
                        cancel();
                        inputRef.current?.blur();
                    }
                }}
                onKeyUp={isolate}
            />
            <span className="prks-pdf-toolbar__page-sep" aria-hidden="true">
                /
            </span>
            <span className="prks-pdf-toolbar__page-total" style={{ minWidth: `${digits}ch` }}>
                {total || '—'}
            </span>
        </span>
    );
}

export function Toolbar({
    documentId,
    mode,
    documentTitle,
    documentTypeLabel,
    documentTypeColor,
    documentTypeBorder,
}: {
    documentId: string;
    mode: ViewerMode;
    documentTitle?: string;
    documentTypeLabel?: string;
    documentTypeColor?: string;
    documentTypeBorder?: string;
}) {
    const { provides: zoom, state: zoomState } = useZoom(documentId);
    const { provides: scroll, state: scrollState } = useScroll(documentId);
    const { provides: pan, isPanning } = usePan(documentId);
    const { provides: annotation } = useAnnotation(documentId);
    const { provides: historyCap } = useHistoryCapability();
    const { provides: interaction } = useInteractionManager(documentId);
    const history = historyCap?.forDocument(documentId);
    const work = mode === 'work';
    const pct = Math.round((zoomState?.currentZoomLevel || 1) * 100);
    const page = (scroll && scroll.getCurrentPage()) || scrollState.currentPage || 1;
    const total = (scroll && scroll.getTotalPages()) || scrollState.totalPages || 0;
    const toolId = annotation?.getActiveTool()?.id || null;
    const pointerActive = !isPanning && !toolId;
    const [zoomOpen, setZoomOpen] = useState(false);
    const [moreOpen, setMoreOpen] = useState(false);
    const zoomWrap = useRef<HTMLDivElement | null>(null);
    const moreWrap = useRef<HTMLDivElement | null>(null);
    const title = (documentTitle || '').trim();
    const typeLabel = (documentTypeLabel || '').trim();
    const showIdentity = !!(title || typeLabel);

    const go = (n: number) => {
        if (!total) return;
        const next = Math.min(total, Math.max(1, n));
        scroll?.scrollToPage({ pageNumber: next, behavior: 'auto' });
    };

    useEffect(() => {
        if (!zoomOpen && !moreOpen) return;
        const onPointer = (event: PointerEvent) => {
            const t = event.target;
            if (!(t instanceof Node)) return;
            if (zoomWrap.current && zoomWrap.current.contains(t)) return;
            if (moreWrap.current && moreWrap.current.contains(t)) return;
            setZoomOpen(false);
            setMoreOpen(false);
        };
        const onKey = (event: KeyboardEvent) => {
            if (event.key !== 'Escape') return;
            setZoomOpen(false);
            setMoreOpen(false);
        };
        document.addEventListener('pointerdown', onPointer);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('pointerdown', onPointer);
            document.removeEventListener('keydown', onKey);
        };
    }, [zoomOpen, moreOpen]);

    const closeMenus = () => {
        setZoomOpen(false);
        setMoreOpen(false);
    };

    const markup = {
        onHighlight: () => {
            pan?.disablePan();
            annotation?.setActiveTool('highlight');
            closeMenus();
        },
        onUnderline: () => {
            pan?.disablePan();
            annotation?.setActiveTool('underline');
            closeMenus();
        },
        onUndo: () => {
            history?.undo();
            closeMenus();
        },
        onRedo: () => {
            history?.redo();
            closeMenus();
        },
    };

    const badgeStyle =
        documentTypeColor || documentTypeBorder
            ? {
                  background: documentTypeColor || undefined,
                  borderColor: documentTypeBorder || documentTypeColor || undefined,
              }
            : undefined;

    return (
        <div className="prks-pdf-toolbar" role="toolbar" aria-label="PDF reader">
            {showIdentity ? (
                <>
                    <div className="prks-pdf-toolbar__identity">
                        {title ? (
                            <span className="prks-pdf-toolbar__title" title={title}>
                                {title}
                            </span>
                        ) : null}
                        {typeLabel ? (
                            <span className="prks-pdf-toolbar__type" style={badgeStyle} title={typeLabel}>
                                {typeLabel}
                            </span>
                        ) : null}
                    </div>
                    <span className="prks-pdf-toolbar__sep" />
                </>
            ) : null}
            <div className="prks-pdf-toolbar__group">
                <button
                    type="button"
                    className={pointerActive ? 'is-active' : ''}
                    title="Pointer"
                    aria-label="Pointer"
                    aria-pressed={pointerActive}
                    onClick={() => {
                        annotation?.setActiveTool(null);
                        pan?.disablePan();
                        interaction?.activate('pointerMode');
                    }}
                >
                    <Icon d={ICONS.pointer} />
                </button>
                <button
                    type="button"
                    className={isPanning ? 'is-active' : ''}
                    title="Pan"
                    aria-label="Pan"
                    aria-pressed={!!isPanning}
                    onClick={() => {
                        annotation?.setActiveTool(null);
                        pan?.enablePan();
                    }}
                >
                    <Icon d={ICONS.pan} />
                </button>
            </div>
            <span className="prks-pdf-toolbar__sep" />
            <div className="prks-pdf-toolbar__group">
                <button
                    type="button"
                    title="Previous page"
                    aria-label="Previous page"
                    disabled={page <= 1}
                    onClick={() => go(page - 1)}
                >
                    <Icon d={ICONS.prev} />
                </button>
                <PageField page={page} total={total} onGo={go} />
                <button
                    type="button"
                    title="Next page"
                    aria-label="Next page"
                    disabled={!total || page >= total}
                    onClick={() => go(page + 1)}
                >
                    <Icon d={ICONS.next} />
                </button>
            </div>
            <span className="prks-pdf-toolbar__sep" />
            <div className="prks-pdf-toolbar__group">
                <button type="button" title="Zoom out" aria-label="Zoom out" onClick={() => zoom?.zoomOut()}>
                    <Icon d={ICONS.minus} />
                </button>
                <div className="prks-pdf-toolbar__cluster" ref={zoomWrap}>
                    <button
                        type="button"
                        className="prks-pdf-toolbar__zoom"
                        title="Zoom level"
                        aria-label="Zoom level"
                        aria-haspopup="menu"
                        aria-expanded={zoomOpen}
                        onClick={() => {
                            setMoreOpen(false);
                            setZoomOpen((open) => !open);
                        }}
                    >
                        <span className="prks-pdf-toolbar__zoom-pct">{pct}%</span>
                        <Icon d={ICONS.chevronDown} size={12} />
                    </button>
                    {zoomOpen ? (
                        <div className="prks-pdf-toolbar__menu" role="menu">
                            {ZOOM_PRESETS.map((level) => (
                                <button
                                    key={level}
                                    type="button"
                                    role="menuitem"
                                    onClick={() => {
                                        zoom?.requestZoom(level);
                                        setZoomOpen(false);
                                    }}
                                >
                                    {Math.round(level * 100)}%
                                </button>
                            ))}
                            <span className="prks-pdf-toolbar__menu-sep" />
                            <button
                                type="button"
                                role="menuitem"
                                onClick={() => {
                                    zoom?.requestZoom(ZoomMode.FitWidth);
                                    setZoomOpen(false);
                                }}
                            >
                                Fit width
                            </button>
                            <button
                                type="button"
                                role="menuitem"
                                onClick={() => {
                                    zoom?.requestZoom(ZoomMode.FitPage);
                                    setZoomOpen(false);
                                }}
                            >
                                Fit page
                            </button>
                        </div>
                    ) : null}
                </div>
                <button type="button" title="Zoom in" aria-label="Zoom in" onClick={() => zoom?.zoomIn()}>
                    <Icon d={ICONS.plus} />
                </button>
            </div>
            {work ? (
                <>
                    <span className="prks-pdf-toolbar__sep prks-pdf-toolbar__sep--secondary" />
                    <div className="prks-pdf-toolbar__secondary">
                        <div className="prks-pdf-toolbar__group">
                            <MarkupTools toolId={toolId} onHighlight={markup.onHighlight} onUnderline={markup.onUnderline} />
                        </div>
                        <span className="prks-pdf-toolbar__sep" />
                        <div className="prks-pdf-toolbar__group">
                            <HistoryTools onUndo={markup.onUndo} onRedo={markup.onRedo} />
                        </div>
                    </div>
                    <div className="prks-pdf-toolbar__cluster prks-pdf-toolbar__more" ref={moreWrap}>
                        <button
                            type="button"
                            title="More tools"
                            aria-label="More tools"
                            aria-haspopup="menu"
                            aria-expanded={moreOpen}
                            onClick={() => {
                                setZoomOpen(false);
                                setMoreOpen((open) => !open);
                            }}
                        >
                            ⋯
                        </button>
                        {moreOpen ? (
                            <div className="prks-pdf-toolbar__menu prks-pdf-toolbar__menu--end" role="menu">
                                <MarkupTools toolId={toolId} onHighlight={markup.onHighlight} onUnderline={markup.onUnderline} />
                                <HistoryTools onUndo={markup.onUndo} onRedo={markup.onRedo} />
                            </div>
                        ) : null}
                    </div>
                </>
            ) : null}
        </div>
    );
}
