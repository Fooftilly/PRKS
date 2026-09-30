export type PdfSearchBarPluginState = {
    loading?: boolean;
    total?: number;
    activeResultIndex?: number;
} | null;

export type PdfSearchBarSettlement = {
    total: number;
    activeIndex: number;
} | null;

export type PdfSearchBarView = {
    label: string;
    matchesDisabled: boolean;
};

function blankView(): PdfSearchBarView {
    return { label: '', matchesDisabled: true };
}

function runtimeSearchBarView(
    draft: string,
    pending: boolean,
    settled: PdfSearchBarSettlement,
    plugin: PdfSearchBarPluginState,
): PdfSearchBarView {
    if (!draft.trim()) return blankView();
    if (pending || !settled) return { label: 'Searching', matchesDisabled: true };
    if (settled.total < 1) return { label: 'No matches', matchesDisabled: true };
    const pluginIndex =
        plugin && !plugin.loading && plugin.total === settled.total ? plugin.activeResultIndex : undefined;
    const index = typeof pluginIndex === 'number' && pluginIndex >= 0 ? pluginIndex : settled.activeIndex;
    const active = index >= 0 ? index + 1 : 1;
    return { label: `${active} of ${settled.total}`, matchesDisabled: false };
}

function pluginSearchBarView(draft: string, state: PdfSearchBarPluginState): PdfSearchBarView {
    if (!draft.trim()) return blankView();
    if (state?.loading) return { label: 'Searching', matchesDisabled: true };
    const total = state && typeof state.total === 'number' ? state.total : 0;
    if (total < 1) return { label: 'No matches', matchesDisabled: true };
    const active =
        state && typeof state.activeResultIndex === 'number' && state.activeResultIndex >= 0
            ? state.activeResultIndex + 1
            : 1;
    return { label: `${active} of ${total}`, matchesDisabled: false };
}

/** Label and next/previous disabled state for the find bar. */
export function pdfSearchBarView(args: {
    draft: string;
    plugin: PdfSearchBarPluginState;
    settled: PdfSearchBarSettlement;
    pending: boolean;
    followRuntime: boolean;
}): PdfSearchBarView {
    if (args.followRuntime) return runtimeSearchBarView(args.draft, args.pending, args.settled, args.plugin);
    return pluginSearchBarView(args.draft, args.plugin);
}
