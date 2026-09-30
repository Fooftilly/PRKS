export type PdfSearchCommitScope = {
    searchAllPages: (query: string) => { toPromise?: () => Promise<unknown> } | null | undefined;
    getState: () => { total?: number; activeResultIndex?: number } | null | undefined;
};

export type PdfSearchSettlement = {
    epoch: number;
    total: number;
    activeIndex: number;
};

export type PdfSearchFlight = {
    key: string;
    epoch: number;
    seq: number;
};

/** One in-flight plugin task. A later commit for the same trimmed query retargets it. */
export type PdfSearchFlightSlot = {
    current: PdfSearchFlight | null;
};

function settleIfCurrent(
    seq: number,
    currentSeq: () => number,
    settle: (result: PdfSearchSettlement) => void,
    result: PdfSearchSettlement,
) {
    if (seq !== currentSeq()) return;
    settle(result);
}

function emptySettlement(epoch: number): PdfSearchSettlement {
    return { epoch, total: 0, activeIndex: -1 };
}

function settlementFromScope(scope: PdfSearchCommitScope, epoch: number): PdfSearchSettlement {
    const found = scope.getState();
    const total = found && typeof found.total === 'number' ? found.total : 0;
    const activeIndex =
        found && typeof found.activeResultIndex === 'number' ? found.activeResultIndex : -1;
    return { epoch, total, activeIndex };
}

function startSearchTask(scope: PdfSearchCommitScope, query: string): Promise<unknown> {
    const task = scope.searchAllPages(query);
    if (!task || typeof task.toPromise !== 'function') return Promise.resolve();
    return Promise.resolve(task.toPromise());
}

function watchSearchTask(
    slot: PdfSearchFlightSlot | undefined,
    record: PdfSearchFlight,
    scope: PdfSearchCommitScope,
    promise: Promise<unknown>,
    currentSeq: () => number,
    settle: (result: PdfSearchSettlement) => void,
) {
    const finish = (result: PdfSearchSettlement) => {
        if (slot && slot.current !== record) return;
        if (slot) slot.current = null;
        settleIfCurrent(record.seq, currentSeq, settle, result);
    };
    promise.then(
        () => {
            finish(settlementFromScope(scope, record.epoch));
        },
        () => {
            finish(emptySettlement(record.epoch));
        },
    );
}

/**
 * Runs one in-document search commit.
 * A missing scope, a synchronous searchAllPages failure, or a synchronous
 * toPromise failure settles as no matches unless a newer sequence has started.
 * EmbedPDF 2.15.1 returns the current (possibly partial) result immediately
 * when the trimmed query is unchanged, so a second commit for that same
 * trimmed query stays on the in-flight task and settles only when it completes.
 */
export function commitPdfSearch(args: {
    query: string;
    epoch: number;
    scope: PdfSearchCommitScope | null | undefined;
    beginSeq: () => number;
    currentSeq: () => number;
    settle: (result: PdfSearchSettlement) => void;
    flight?: PdfSearchFlightSlot;
}): void {
    const seq = args.beginSeq();
    const empty = emptySettlement(args.epoch);
    const slot = args.flight;
    const key = args.query.trim();
    const scope = args.scope;
    if (!scope) {
        if (slot) slot.current = null;
        settleIfCurrent(seq, args.currentSeq, args.settle, empty);
        return;
    }
    const inflight = slot && slot.current;
    if (inflight && key && inflight.key === key) {
        inflight.epoch = args.epoch;
        inflight.seq = seq;
        return;
    }
    let promise: Promise<unknown>;
    try {
        promise = startSearchTask(scope, args.query);
    } catch {
        if (slot) slot.current = null;
        settleIfCurrent(seq, args.currentSeq, args.settle, empty);
        return;
    }
    const record: PdfSearchFlight = { key, epoch: args.epoch, seq };
    if (slot) slot.current = record;
    watchSearchTask(slot, record, scope, promise, args.currentSeq, args.settle);
}
