export type PdfSearchCommitScope = {
    searchAllPages: (query: string) => { toPromise?: () => Promise<unknown> } | null | undefined;
    getState: () => { total?: number; activeResultIndex?: number } | null | undefined;
};

export type PdfSearchSettlement = {
    epoch: number;
    total: number;
    activeIndex: number;
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

/**
 * Runs one in-document search commit. A missing scope or a synchronous
 * searchAllPages failure settles as no matches, unless a newer sequence
 * has already replaced this commit.
 */
export function commitPdfSearch(args: {
    query: string;
    epoch: number;
    scope: PdfSearchCommitScope | null | undefined;
    beginSeq: () => number;
    currentSeq: () => number;
    settle: (result: PdfSearchSettlement) => void;
}): void {
    const seq = args.beginSeq();
    const empty: PdfSearchSettlement = { epoch: args.epoch, total: 0, activeIndex: -1 };
    const scope = args.scope;
    if (!scope) {
        settleIfCurrent(seq, args.currentSeq, args.settle, empty);
        return;
    }
    let task: { toPromise?: () => Promise<unknown> } | null | undefined;
    try {
        task = scope.searchAllPages(args.query);
    } catch {
        settleIfCurrent(seq, args.currentSeq, args.settle, empty);
        return;
    }
    const settled =
        task && typeof task.toPromise === 'function' ? task.toPromise() : Promise.resolve();
    settled
        .then(() => {
            if (seq !== args.currentSeq()) return;
            const found = scope.getState();
            args.settle({
                epoch: args.epoch,
                total: found && typeof found.total === 'number' ? found.total : 0,
                activeIndex:
                    found && typeof found.activeResultIndex === 'number'
                        ? found.activeResultIndex
                        : -1,
            });
        })
        .catch(() => {
            settleIfCurrent(seq, args.currentSeq, args.settle, empty);
        });
}
