import { useLayoutEffect, useRef, useState, useEffect } from 'react';
import type {
    CSSProperties,
    ReactNode,
    PointerEvent as ReactPointerEvent,
    MouseEvent as ReactMouseEvent,
} from 'react';
import type { Rect } from '@embedpdf/models';

const GAP = 8;
const PAD = 4;

export function stopMenuEvent(e: ReactPointerEvent | ReactMouseEvent) {
    e.preventDefault();
    e.stopPropagation();
}

function clampMenu(el: HTMLElement, viewport: DOMRect) {
    const box = el.getBoundingClientRect();
    let dx = 0;
    if (box.left < viewport.left + PAD) dx = viewport.left + PAD - box.left;
    if (box.right + dx > viewport.right - PAD) dx = viewport.right - PAD - box.right;
    return dx;
}

export function MenuButton({
    onActivate,
    className,
    title,
    'aria-label': ariaLabel,
    children,
}: {
    onActivate: () => void;
    className?: string;
    title: string;
    'aria-label': string;
    children: ReactNode;
}) {
    const skipClick = useRef(false);
    const activateRef = useRef(onActivate);
    activateRef.current = onActivate;

    return (
        <button
            type="button"
            className={className}
            title={title}
            aria-label={ariaLabel}
            onPointerDownCapture={(event) => {
                skipClick.current = false;
                event.preventDefault();
                event.stopPropagation();
            }}
            onPointerUpCapture={(event) => {
                event.preventDefault();
                event.stopPropagation();
                skipClick.current = true;
                activateRef.current();
            }}
            onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                if (skipClick.current) {
                    skipClick.current = false;
                    return;
                }
                activateRef.current();
            }}
        >
            {children}
        </button>
    );
}

export function FloatingMenu({
    menuWrapperProps,
    rect,
    preferAbove,
    className,
    children,
    onEscape,
}: {
    menuWrapperProps: { style?: CSSProperties; ref?: (el: HTMLDivElement | null) => void };
    rect: Rect;
    preferAbove: boolean;
    className?: string;
    children: ReactNode;
    onEscape?: () => void;
}) {
    const popupRef = useRef<HTMLDivElement | null>(null);
    const [above, setAbove] = useState(preferAbove);
    const [shiftX, setShiftX] = useState(0);

    useEffect(() => {
        setAbove(preferAbove);
        setShiftX(0);
    }, [preferAbove, rect.origin.x, rect.origin.y, rect.size.width, rect.size.height]);

    useLayoutEffect(() => {
        const el = popupRef.current;
        if (!el) return;
        const host =
            el.closest('.prks-pdf-viewport') ||
            el.closest('.prks-pdf-stage') ||
            el.closest('.prks-pdf-viewer');
        if (!(host instanceof HTMLElement)) return;
        const viewport = host.getBoundingClientRect();
        const box = el.getBoundingClientRect();
        if (above && box.top < viewport.top + PAD && box.height + GAP < viewport.height) {
            setAbove(false);
            return;
        }
        if (!above && box.bottom > viewport.bottom - PAD && box.top - box.height - GAP >= viewport.top + PAD) {
            setAbove(true);
            return;
        }
        const dx = clampMenu(el, viewport);
        if (dx !== shiftX) setShiftX(dx);
    }, [above, shiftX, preferAbove, rect.origin.x, rect.origin.y, rect.size.width, rect.size.height]);

    useEffect(() => {
        if (!onEscape) return;
        const onKey = (event: KeyboardEvent) => {
            if (event.key !== 'Escape') return;
            event.preventDefault();
            event.stopPropagation();
            onEscape();
        };
        document.addEventListener('keydown', onKey, true);
        return () => document.removeEventListener('keydown', onKey, true);
    }, [onEscape]);

    const innerStyle: CSSProperties = {
        position: 'absolute',
        left: '50%',
        top: above ? undefined : rect.size.height + GAP,
        bottom: above ? '100%' : undefined,
        marginBottom: above ? GAP : undefined,
        transform: `translateX(calc(-50% + ${shiftX}px))`,
        pointerEvents: 'auto',
    };

    return (
        <div
            ref={menuWrapperProps.ref}
            style={{
                ...(menuWrapperProps.style || {}),
                zIndex: 50,
                pointerEvents: 'none',
            }}
            data-no-interaction=""
        >
            <div
                ref={popupRef}
                className={['prks-pdf-floating-menu', className].filter(Boolean).join(' ')}
                role="toolbar"
                data-no-interaction=""
                style={innerStyle}
                onPointerDown={stopMenuEvent}
                onPointerUp={stopMenuEvent}
                onMouseDown={stopMenuEvent}
                onMouseUp={stopMenuEvent}
            >
                {children}
            </div>
        </div>
    );
}
