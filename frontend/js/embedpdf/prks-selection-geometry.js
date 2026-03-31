/**
 * PRKS — text selection helpers aligned with EmbedPDF `@embedpdf/plugin-selection` geometry.
 *
 * Word / line boundary logic adapted from Chromium / EmbedPDF (BSD-style).
 * Rectangle merge adapted from Chromium pdfium_range.cc (BSD).
 *
 * Action types must match the bundled viewer (snippet@2): grep embedpdf-*.js for SELECTION/
 */

export const PRKS_SELECTION = {
    SET_SELECTION: 'SELECTION/SET_SELECTION',
    SET_RECTS: 'SELECTION/SET_RECTS',
    SET_SLICES: 'SELECTION/SET_SLICES',
};

/** @param {any} geo */
function getTotalCharCount(geo) {
    if (!geo || !geo.runs || geo.runs.length === 0) return 0;
    const lastRun = geo.runs[geo.runs.length - 1];
    return lastRun.charStart + lastRun.glyphs.length;
}

/** @param {any} geo @param {number} charIndex */
function resolveCharIndex(geo, charIndex) {
    for (let r = 0; r < geo.runs.length; r++) {
        const run = geo.runs[r];
        const localIdx = charIndex - run.charStart;
        if (localIdx >= 0 && localIdx < run.glyphs.length) {
            return { runIdx: r, localIdx };
        }
    }
    return null;
}

/** @param {number} flags */
function isGlyphWordBoundary(flags) {
    return flags === 1 || flags === 2;
}

/**
 * @param {any} geo
 * @param {number} charIndex
 * @returns {{ from: number, to: number } | null}
 */
export function prksExpandToWordBoundary(geo, charIndex) {
    if (!geo) return null;
    const resolved = resolveCharIndex(geo, charIndex);
    if (!resolved) return null;

    const totalChars = getTotalCharCount(geo);
    if (totalChars === 0) return null;

    let from = charIndex;
    while (from > 0) {
        const prev = resolveCharIndex(geo, from - 1);
        if (!prev) break;
        if (isGlyphWordBoundary(geo.runs[prev.runIdx].glyphs[prev.localIdx].flags)) break;
        from--;
    }

    let to = charIndex;
    while (to < totalChars - 1) {
        const next = resolveCharIndex(geo, to + 1);
        if (!next) break;
        if (isGlyphWordBoundary(geo.runs[next.runIdx].glyphs[next.localIdx].flags)) break;
        to++;
    }

    return { from, to };
}

/** Horizontal gap between adjacent glyph boxes (PDF space). */
function prksGlyphPairGapLeftToRight(gLeft, gRight) {
    const r = (gLeft.tightX ?? gLeft.x) + (gLeft.tightWidth ?? gLeft.width);
    const l = gRight.tightX ?? gRight.x;
    return Math.max(0, l - r);
}

function prksRunMeanCharWidth(run) {
    if (!run || !run.glyphs || run.glyphs.length === 0) return 1;
    let sum = 0;
    let n = 0;
    for (const g of run.glyphs) {
        if (g.flags === 2) continue;
        sum += g.tightWidth ?? g.width;
        n++;
    }
    return n > 0 ? sum / n : 1;
}

function prksRunsVerticallyOverlap(a, b) {
    if (!a || !b || !a.rect || !b.rect) return false;
    const ay1 = a.rect.y;
    const ay2 = a.rect.y + a.rect.height;
    const by1 = b.rect.y;
    const by2 = b.rect.y + b.rect.height;
    const overlap = Math.min(ay2, by2) - Math.max(ay1, by1);
    return overlap > 0.15 * Math.min(a.rect.height, b.rect.height);
}

/**
 * Word span from horizontal gaps (same idea as prksRectsWithinSlice / pdfium_range).
 * Fills cases where Pdfium word-boundary flags split a single visual word (e.g. "Move"+"ment").
 * @param {any} geo
 * @param {number} charIndex
 * @returns {{ from: number, to: number } | null}
 */
export function prksExpandToWordBoundaryByGap(geo, charIndex) {
    if (!geo || !geo.runs || geo.runs.length === 0) return null;
    const resolved = resolveCharIndex(geo, charIndex);
    if (!resolved) return null;

    const GAP_FACTOR = 2.5;
    let { runIdx, localIdx } = resolved;
    let run = geo.runs[runIdx];
    let glyphs = run.glyphs;
    let avg = prksRunMeanCharWidth(run);

    let lo = localIdx;
    while (lo > 0) {
        const g0 = glyphs[lo - 1];
        const g1 = glyphs[lo];
        if (g0.flags === 2 || g1.flags === 2) {
            lo--;
            continue;
        }
        if (prksGlyphPairGapLeftToRight(g0, g1) > GAP_FACTOR * avg) break;
        lo--;
    }

    let hi = localIdx;
    while (hi < glyphs.length - 1) {
        const g0 = glyphs[hi];
        const g1 = glyphs[hi + 1];
        if (g0.flags === 2 || g1.flags === 2) {
            hi++;
            continue;
        }
        if (prksGlyphPairGapLeftToRight(g0, g1) > GAP_FACTOR * avg) break;
        hi++;
    }

    let from = run.charStart + lo;
    let to = run.charStart + hi;

    while (runIdx < geo.runs.length - 1 && hi === glyphs.length - 1) {
        const nextRun = geo.runs[runIdx + 1];
        if (!nextRun || !nextRun.glyphs || nextRun.glyphs.length === 0) break;
        if (!prksRunsVerticallyOverlap(run, nextRun)) break;
        const gLast = glyphs[hi];
        const gFirst = nextRun.glyphs[0];
        const nextAvg = prksRunMeanCharWidth(nextRun);
        const blendAvg = (avg + nextAvg) / 2;
        if (gLast.flags !== 2 && gFirst.flags !== 2) {
            const g = prksGlyphPairGapLeftToRight(gLast, gFirst);
            if (g > GAP_FACTOR * blendAvg) break;
        }
        runIdx++;
        run = nextRun;
        glyphs = run.glyphs;
        avg = nextAvg;
        lo = 0;
        hi = 0;
        while (hi < glyphs.length - 1) {
            const g0 = glyphs[hi];
            const g1 = glyphs[hi + 1];
            if (g0.flags === 2 || g1.flags === 2) {
                hi++;
                continue;
            }
            if (prksGlyphPairGapLeftToRight(g0, g1) > GAP_FACTOR * avg) break;
            hi++;
        }
        to = run.charStart + hi;
    }

    return { from, to };
}

/**
 * @param { { start: { page: number, index: number }, end: { page: number, index: number } } | null } sel
 * @param {any} geo
 * @param {number} page
 */
export function prksSliceBounds(sel, geo, page) {
    if (!sel || !geo) return null;
    if (page < sel.start.page || page > sel.end.page) return null;

    const from = page === sel.start.page ? sel.start.index : 0;

    const lastRun = geo.runs[geo.runs.length - 1];
    const lastCharOnPage = lastRun.charStart + lastRun.glyphs.length - 1;

    const to = page === sel.end.page ? sel.end.index : lastCharOnPage;

    return { from, to };
}

function rectUnion(rect1, rect2) {
    const left = Math.min(rect1.origin.x, rect2.origin.x);
    const top = Math.min(rect1.origin.y, rect2.origin.y);
    const right = Math.max(rect1.origin.x + rect1.size.width, rect2.origin.x + rect2.size.width);
    const bottom = Math.max(rect1.origin.y + rect1.size.height, rect2.origin.y + rect2.size.height);

    return {
        origin: { x: left, y: top },
        size: { width: right - left, height: bottom - top },
    };
}

function rectIntersect(rect1, rect2) {
    const left = Math.max(rect1.origin.x, rect2.origin.x);
    const top = Math.max(rect1.origin.y, rect2.origin.y);
    const right = Math.min(rect1.origin.x + rect1.size.width, rect2.origin.x + rect2.size.width);
    const bottom = Math.min(rect1.origin.y + rect1.size.height, rect2.origin.y + rect2.size.height);

    const width = Math.max(0, right - left);
    const height = Math.max(0, bottom - top);

    return {
        origin: { x: left, y: top },
        size: { width, height },
    };
}

function rectIsEmpty(rect) {
    return rect.size.width <= 0 || rect.size.height <= 0;
}

function getVerticalOverlap(rect1, rect2) {
    if (rectIsEmpty(rect1) || rectIsEmpty(rect2)) return 0;

    const unionRect = rectUnion(rect1, rect2);

    if (unionRect.size.height === rect1.size.height || unionRect.size.height === rect2.size.height) {
        return 1.0;
    }

    const intersectRect = rectIntersect(rect1, rect2);
    return intersectRect.size.height / unionRect.size.height;
}

function shouldMergeHorizontalRects(textRun1, textRun2) {
    const FONT_SIZE_RATIO_THRESHOLD = 1.5;
    if (
        textRun1.fontSize != null &&
        textRun2.fontSize != null &&
        textRun1.fontSize > 0 &&
        textRun2.fontSize > 0
    ) {
        const ratio =
            Math.max(textRun1.fontSize, textRun2.fontSize) / Math.min(textRun1.fontSize, textRun2.fontSize);
        if (ratio > FONT_SIZE_RATIO_THRESHOLD) {
            return false;
        }
    }

    const VERTICAL_OVERLAP_THRESHOLD = 0.8;
    const rect1 = textRun1.rect;
    const rect2 = textRun2.rect;

    if (getVerticalOverlap(rect1, rect2) < VERTICAL_OVERLAP_THRESHOLD) {
        return false;
    }

    const HORIZONTAL_WIDTH_FACTOR = 1.0;
    const averageWidth1 = (HORIZONTAL_WIDTH_FACTOR * rect1.size.width) / textRun1.charCount;
    const averageWidth2 = (HORIZONTAL_WIDTH_FACTOR * rect2.size.width) / textRun2.charCount;

    const rect1Left = rect1.origin.x - averageWidth1;
    const rect1Right = rect1.origin.x + rect1.size.width + averageWidth1;
    const rect2Left = rect2.origin.x - averageWidth2;
    const rect2Right = rect2.origin.x + rect2.size.width + averageWidth2;

    return rect1Left < rect2Right && rect1Right > rect2Left;
}

function mergeAdjacentRects(textRuns) {
    const results = [];
    let previousTextRun = null;
    let currentRect = null;

    for (const textRun of textRuns) {
        if (previousTextRun && currentRect) {
            if (shouldMergeHorizontalRects(previousTextRun, textRun)) {
                currentRect = rectUnion(currentRect, textRun.rect);
            } else {
                results.push(currentRect);
                currentRect = textRun.rect;
            }
        } else {
            currentRect = textRun.rect;
        }
        previousTextRun = textRun;
    }

    if (currentRect && !rectIsEmpty(currentRect)) {
        results.push(currentRect);
    }

    return results;
}

/**
 * @param {any} geo
 * @param {number} from
 * @param {number} to
 * @param {boolean} [merge]
 */
export function prksRectsWithinSlice(geo, from, to, merge = true) {
    const textRuns = [];
    const CHAR_DISTANCE_FACTOR = 2.5;

    for (const run of geo.runs) {
        const runStart = run.charStart;
        const runEnd = runStart + run.glyphs.length - 1;
        if (runEnd < from || runStart > to) continue;

        const sIdx = Math.max(from, runStart) - runStart;
        const eIdx = Math.min(to, runEnd) - runStart;

        let minX = Infinity;
        let maxX = -Infinity;
        let minY = Infinity;
        let maxY = -Infinity;
        let charCount = 0;
        let widthSum = 0;
        let prevRight = -Infinity;

        const flushSubRun = () => {
            if (minX !== Infinity && charCount > 0) {
                textRuns.push({
                    rect: {
                        origin: { x: minX, y: minY },
                        size: { width: maxX - minX, height: maxY - minY },
                    },
                    charCount,
                    fontSize: run.fontSize,
                });
            }
            minX = Infinity;
            maxX = -Infinity;
            minY = Infinity;
            maxY = -Infinity;
            charCount = 0;
            widthSum = 0;
            prevRight = -Infinity;
        };

        for (let i = sIdx; i <= eIdx; i++) {
            const g = run.glyphs[i];
            if (g.flags === 2) continue;

            if (charCount > 0 && prevRight > -Infinity) {
                const gap = Math.abs(g.x - prevRight);
                const avgWidth = widthSum / charCount;
                if (avgWidth > 0 && gap > CHAR_DISTANCE_FACTOR * avgWidth) {
                    flushSubRun();
                }
            }

            minX = Math.min(minX, g.x);
            maxX = Math.max(maxX, g.x + g.width);
            minY = Math.min(minY, g.y);
            maxY = Math.max(maxY, g.y + g.height);

            charCount++;
            widthSum += g.width;
            prevRight = g.x + g.width;
        }

        flushSubRun();
    }

    if (!merge) {
        return textRuns.map((r) => r.rect);
    }

    return mergeAdjacentRects(textRuns);
}

function computeTolerance(geo, factor) {
    let totalHeight = 0;
    let count = 0;

    for (const run of geo.runs) {
        for (const g of run.glyphs) {
            if (g.flags === 2) continue;
            totalHeight += g.height;
            count++;
        }
    }

    if (count === 0) return 0;
    return (totalHeight / count) * factor;
}

/**
 * @param {any} geo
 * @param {{ x: number, y: number }} pt page space
 * @param {number} [toleranceFactor]
 * @returns {number} glyph index or -1
 */
export function prksGlyphAt(geo, pt, toleranceFactor = 1.5) {
    for (const run of geo.runs) {
        const inRun =
            pt.y >= run.rect.y &&
            pt.y <= run.rect.y + run.rect.height &&
            pt.x >= run.rect.x &&
            pt.x <= run.rect.x + run.rect.width;

        if (!inRun) continue;

        const rel = run.glyphs.findIndex((g) => {
            const gx = g.tightX ?? g.x;
            const gy = g.tightY ?? g.y;
            const gw = g.tightWidth ?? g.width;
            const gh = g.tightHeight ?? g.height;
            return pt.x >= gx && pt.x <= gx + gw && pt.y >= gy && pt.y <= gy + gh;
        });

        if (rel !== -1) {
            return run.charStart + rel;
        }
    }

    if (toleranceFactor <= 0) return -1;

    const tolerance = computeTolerance(geo, toleranceFactor);
    const halfTol = tolerance / 2;

    let bestIndex = -1;
    let bestDist = Infinity;

    for (const run of geo.runs) {
        if (
            pt.y < run.rect.y - halfTol ||
            pt.y > run.rect.y + run.rect.height + halfTol ||
            pt.x < run.rect.x - halfTol ||
            pt.x > run.rect.x + run.rect.width + halfTol
        ) {
            continue;
        }

        for (let i = 0; i < run.glyphs.length; i++) {
            const g = run.glyphs[i];
            if (g.flags === 2) continue;

            const gx = g.tightX ?? g.x;
            const gy = g.tightY ?? g.y;
            const gw = g.tightWidth ?? g.width;
            const gh = g.tightHeight ?? g.height;

            const expandedLeft = gx - halfTol;
            const expandedRight = gx + gw + halfTol;
            const expandedTop = gy - halfTol;
            const expandedBottom = gy + gh + halfTol;

            if (
                pt.x < expandedLeft ||
                pt.x > expandedRight ||
                pt.y < expandedTop ||
                pt.y > expandedBottom
            ) {
                continue;
            }

            const curXdif = Math.min(Math.abs(pt.x - gx), Math.abs(pt.x - (gx + gw)));
            const curYdif = Math.min(Math.abs(pt.y - gy), Math.abs(pt.y - (gy + gh)));
            const dist = curXdif + curYdif;

            if (dist < bestDist) {
                bestDist = dist;
                bestIndex = run.charStart + i;
            }
        }
    }

    return bestIndex;
}

/**
 * @param { { start: { page: number, index: number }, end: { page: number, index: number } } } range
 * @param {Record<number, any>} geometry
 * @returns {{ allRects: Record<number, any>, allSlices: Record<number, { start: number, count: number }> }}
 */
export function prksComputeRectsAndSlices(range, geometry) {
    const allRects = {};
    const allSlices = {};

    for (let p = range.start.page; p <= range.end.page; p++) {
        const geo = geometry[p];
        const sb = prksSliceBounds(range, geo, p);
        if (!sb) continue;

        allRects[p] = prksRectsWithinSlice(geo, sb.from, sb.to);
        allSlices[p] = { start: sb.from, count: sb.to - sb.from + 1 };
    }

    return { allRects, allSlices };
}

/**
 * Snap selection endpoints to whole words (per page).
 * @param { { selection: any, geometry: Record<number, any> } } docState
 */
export function prksSnapSelectionRangeToWords(docState) {
    const sel = docState && docState.selection;
    if (!sel || !docState.geometry) return null;

    let start = { page: sel.start.page, index: sel.start.index };
    let end = { page: sel.end.page, index: sel.end.index };

    const sg = docState.geometry[start.page];
    if (sg) {
        const wf = prksExpandToWordBoundary(sg, start.index);
        const wg = prksExpandToWordBoundaryByGap(sg, start.index);
        let from = start.index;
        if (wf) from = wf.from;
        else if (wg) from = wg.from;
        start = { page: start.page, index: from };
    }

    const eg = docState.geometry[end.page];
    if (eg) {
        const wf = prksExpandToWordBoundary(eg, end.index);
        const wg = prksExpandToWordBoundaryByGap(eg, end.index);
        let to = end.index;
        if (wf) to = wf.to;
        else if (wg) to = wg.to;
        end = { page: end.page, index: to };
    }

    if (start.page > end.page || (start.page === end.page && start.index > end.index)) {
        const t = start;
        start = end;
        end = t;
    }

    return { start, end };
}

/**
 * @param {any} store registry.store
 * @param {string} docId
 * @param { { start: { page: number, index: number }, end: { page: number, index: number } } } range
 * @param {Record<number, any>} geometry from selection document state
 */
export function prksDispatchSelectionRange(store, docId, range, geometry) {
    if (!store || typeof store.dispatch !== 'function' || !docId || !range) return;

    const { allRects, allSlices } = prksComputeRectsAndSlices(range, geometry);

    store.dispatch({
        type: PRKS_SELECTION.SET_SELECTION,
        payload: { documentId: docId, selection: range },
    });
    store.dispatch({
        type: PRKS_SELECTION.SET_RECTS,
        payload: { documentId: docId, rects: allRects },
    });
    store.dispatch({
        type: PRKS_SELECTION.SET_SLICES,
        payload: { documentId: docId, slices: allSlices },
    });
}
