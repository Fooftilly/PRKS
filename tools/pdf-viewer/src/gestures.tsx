import { useEffect } from 'react';
import { useViewportElement } from '@embedpdf/plugin-viewport/react';
import { useZoom } from '@embedpdf/plugin-zoom/react';

/**
 * PRKS owns Ctrl/Cmd+wheel. EmbedPDF ZoomPlugin owns requestZoomBy and layout.
 * ZoomGestureWrapper stays at enableWheel={false}: its CSS scale preview
 * commits after 150ms and flashes the page. This adapter applies wheel
 * deltas through requestZoomBy on animation frames instead.
 */
export function WheelZoom({ documentId }: { documentId: string }) {
    const vpRef = useViewportElement();
    const { provides: zoom } = useZoom(documentId);

    useEffect(() => {
        const el = vpRef?.current;
        if (!el || !zoom) return;
        let pending = 0;
        let vx = 0;
        let vy = 0;
        let raf = 0;

        const flush = () => {
            raf = 0;
            const delta = pending;
            pending = 0;
            if (!delta) return;
            zoom.requestZoomBy(delta, { vx, vy });
        };

        const onWheel = (e: WheelEvent) => {
            if (!e.ctrlKey && !e.metaKey) return;
            e.preventDefault();
            e.stopPropagation();
            const rect = el.getBoundingClientRect();
            vx = e.clientX - rect.left;
            vy = e.clientY - rect.top;
            const cur = zoom.getState().currentZoomLevel || 1;
            const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
            pending += -dy * 0.0018 * cur;
            if (!raf) raf = requestAnimationFrame(flush);
        };

        el.addEventListener('wheel', onWheel, { passive: false, capture: true });
        return () => {
            el.removeEventListener('wheel', onWheel, true);
            if (raf) cancelAnimationFrame(raf);
        };
    }, [vpRef, zoom, documentId]);

    return null;
}
