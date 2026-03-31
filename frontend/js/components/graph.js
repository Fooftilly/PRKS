let _visNetworkScriptPromise = null;

function prksGraphOpenWork(hashOrNodeId, { newTab = false } = {}) {
    if (hashOrNodeId == null || hashOrNodeId === '') return;
    const hash =
        String(hashOrNodeId).startsWith('#') ? String(hashOrNodeId) : '#/works/' + String(hashOrNodeId);
    if (newTab && typeof prksOpenHashInNewTab === 'function') {
        prksOpenHashInNewTab(hash);
        return;
    }
    window.location.hash = hash;
}

function prksGraphEventIsMiddleClick(params) {
    const b = params && params.event && params.event.srcEvent ? params.event.srcEvent.button : undefined;
    return b === 1;
}

/**
 * When two files both [[link]] to each other, the API returns two wiki edges. vis-network draws
 * them as two parallel curves (looks like a double line). Merge into one edge with arrows on both ends.
 */
function prksMergeBidirectionalWikiEdges(edges) {
    const wiki = [];
    const rest = [];
    for (const e of edges) {
        if (e && e.kind === 'wiki') wiki.push(e);
        else rest.push(e);
    }
    const pairMap = new Map();
    for (const e of wiki) {
        const lo = e.from < e.to ? e.from : e.to;
        const hi = e.from < e.to ? e.to : e.from;
        const key = `${lo}\t${hi}`;
        const dir = `${e.from}\t${e.to}`;
        if (!pairMap.has(key)) pairMap.set(key, new Set());
        pairMap.get(key).add(dir);
    }
    const merged = [];
    for (const [key, dirs] of pairMap) {
        const [lo, hi] = key.split('\t');
        const forward = `${lo}\t${hi}`;
        const backward = `${hi}\t${lo}`;
        const hasF = dirs.has(forward);
        const hasB = dirs.has(backward);
        if (hasF && hasB) {
            merged.push({ from: lo, to: hi, kind: 'wiki', wikiBidirectional: true });
        } else if (hasF) {
            merged.push({ from: lo, to: hi, kind: 'wiki' });
        } else {
            merged.push({ from: hi, to: lo, kind: 'wiki' });
        }
    }
    return merged.concat(rest);
}

/** Unordered pair key for two node ids (same as merge/wiki dedupe). */
function prksGraphUnorderedPairKey(a, b) {
    const x = String(a);
    const y = String(b);
    return x < y ? `${x}\t${y}` : `${y}\t${x}`;
}

/**
 * When the same two files have both a wiki edge and shared_tag / wiki_cocite, vis draws two curves
 * (looked like one "double" wiki line). Drop the non-wiki edge if that pair already has wiki.
 */
function prksDropNonWikiEdgesWhenWikiConnectsPair(edges) {
    const wikiPairs = new Set();
    for (const e of edges) {
        if (e && e.kind === 'wiki') {
            wikiPairs.add(prksGraphUnorderedPairKey(e.from, e.to));
        }
    }
    const out = [];
    for (const e of edges) {
        if (!e) continue;
        if (e.kind === 'shared_tag' || e.kind === 'wiki_cocite') {
            if (wikiPairs.has(prksGraphUnorderedPairKey(e.from, e.to))) {
                continue;
            }
        }
        out.push(e);
    }
    return out;
}

function loadVisNetworkStandalone() {
    if (window.vis && window.vis.Network && window.vis.DataSet) {
        return Promise.resolve();
    }
    if (_visNetworkScriptPromise) return _visNetworkScriptPromise;
    _visNetworkScriptPromise = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = 'https://cdn.jsdelivr.net/npm/vis-network@9.1.9/standalone/umd/vis-network.min.js';
        s.async = true;
        s.onload = () => resolve();
        s.onerror = () => reject(new Error('Failed to load vis-network'));
        document.head.appendChild(s);
    });
    return _visNetworkScriptPromise;
}

function prksPrepareEdgesForVis(edges) {
    const afterMerge = prksMergeBidirectionalWikiEdges(edges || []);
    return prksDropNonWikiEdgesWhenWikiConnectsPair(afterMerge);
}

function prksBuildVisNodeRows(nodes) {
    return (nodes || []).map((n) => {
        const g =
            (n.group && String(n.group).trim()) ||
            (typeof prksNormalizeDocType === 'function' ? prksNormalizeDocType(n.doc_type) : 'misc');
        return {
            id: n.id,
            label: n.label || n.id,
            group: g,
        };
    });
}

function prksBuildVisEdgeRows(edgesForVis) {
    return edgesForVis.map((e, i) => {
        let title = 'Shared tag (same tag on both files; no direction)';
        let color = { color: '#94a3b8', dashes: true };
        let arrows = { to: { enabled: false }, from: { enabled: false } };
        if (e.kind === 'wiki') {
            color = { color: '#818cf8' };
            if (e.wikiBidirectional) {
                title =
                    'Both files link to each other (wiki [[links]]); arrows show each direction on one line';
                arrows = {
                    to: { enabled: true, scaleFactor: 0.85 },
                    from: { enabled: true, scaleFactor: 0.85 },
                };
            } else {
                title =
                    'Wiki link: arrow points to the linked file (from the file that contains [[link]] in notes/abstract)';
                arrows = { to: { enabled: true, scaleFactor: 0.9 } };
            }
        } else if (e.kind === 'wiki_cocite') {
            title = 'Same unresolved [[link]] text on both files (co-citation; no direction)';
            color = { color: '#a78bfa', dashes: [5, 5] };
        }
        return { id: 'e' + i, from: e.from, to: e.to, title, color, arrows };
    });
}

function prksGraphLabelFontColor() {
    return (
        (typeof getComputedStyle !== 'undefined' &&
            getComputedStyle(document.documentElement).getPropertyValue('--text-primary').trim()) ||
        '#1e293b'
    );
}

/** 1-hop ego: center work + every work directly linked to it in the graph. */
function prksFilterGraphEgoNetwork(graph, centerWorkId, workMeta) {
    const center = String(centerWorkId);
    const nodes = graph.nodes || [];
    const edges = graph.edges || [];
    const neighbor = new Set();
    for (const e of edges) {
        const a = String(e.from);
        const b = String(e.to);
        if (a === center) neighbor.add(b);
        else if (b === center) neighbor.add(a);
    }
    const keep = new Set([center, ...neighbor]);
    let filNodes = nodes.filter((n) => keep.has(String(n.id)));
    const hasCenter = filNodes.some((n) => String(n.id) === center);
    if (!hasCenter && workMeta && workMeta.id != null) {
        const dt =
            typeof prksNormalizeDocType === 'function'
                ? prksNormalizeDocType(workMeta.doc_type)
                : 'misc';
        filNodes = [
            {
                id: workMeta.id,
                label: (workMeta.title || String(workMeta.id)).trim() || String(workMeta.id),
                doc_type: dt,
                group: dt,
            },
            ...filNodes,
        ];
    }
    const filEdges = prksPrepareEdgesForVis(
        edges.filter((e) => keep.has(String(e.from)) && keep.has(String(e.to)))
    );
    return { nodes: filNodes, edges: filEdges };
}

/** Induced subgraph on a set of works (e.g. all files linked to a person). */
function prksFilterGraphInducedOnWorks(graph, workIds) {
    const keep = new Set((workIds || []).map((x) => String(x)));
    if (keep.size === 0) return { nodes: [], edges: [] };
    const nodes = (graph.nodes || []).filter((n) => keep.has(String(n.id)));
    const rawSub = (graph.edges || []).filter((e) => keep.has(String(e.from)) && keep.has(String(e.to)));
    const edges = prksPrepareEdgesForVis(rawSub);
    return { nodes, edges };
}

let _prksContextGraphGen = 0;

function destroyPrksContextGraph() {
    const st = window.__prksContextGraphState;
    if (st && st.network) {
        try {
            st.network.destroy();
        } catch (_e) {
            /* ignore */
        }
    }
    window.__prksContextGraphState = null;
}

/**
 * Right-panel graph for #/works/:id or #/people/:id — subset of /api/graph, not the full library graph.
 * @param {{ mode: 'work'|'person', centerWorkId?: string, workMeta?: object, workIds?: string[] }} spec
 */
async function mountPrksContextGraphPanel(spec) {
    const gen = ++_prksContextGraphGen;
    destroyPrksContextGraph();

    const statusEl = document.getElementById('prks-context-graph-status');
    const netEl = document.getElementById('prks-context-graph-network');

    if (!netEl || !spec || !spec.mode) return;

    if (spec.mode === 'person' && (!spec.workIds || spec.workIds.length === 0)) {
        if (statusEl) statusEl.textContent = 'No linked files — nothing to graph yet.';
        return;
    }

    try {
        await loadVisNetworkStandalone();
    } catch (e) {
        console.error(e);
        if (statusEl) statusEl.textContent = 'Could not load graph library.';
        return;
    }

    if (gen !== _prksContextGraphGen) return;

    let raw;
    try {
        raw = await fetchGraph();
    } catch (_e) {
        raw = { nodes: [], edges: [] };
    }

    if (gen !== _prksContextGraphGen) return;

    const nodesFull = raw.nodes || [];
    if (nodesFull.length === 0) {
        if (statusEl) statusEl.textContent = 'No files in the library yet.';
        return;
    }

    let subNodes;
    let subEdges;
    if (spec.mode === 'work') {
        const r = prksFilterGraphEgoNetwork(raw, spec.centerWorkId, spec.workMeta);
        subNodes = r.nodes;
        subEdges = r.edges;
    } else {
        const r = prksFilterGraphInducedOnWorks(raw, spec.workIds);
        subNodes = r.nodes;
        subEdges = r.edges;
    }

    if (gen !== _prksContextGraphGen) return;

    if (!subNodes || subNodes.length === 0) {
        if (statusEl) statusEl.textContent = 'No matching nodes in graph data.';
        return;
    }

    const visNodeRows = prksBuildVisNodeRows(subNodes);
    const visEdgeRows = prksBuildVisEdgeRows(subEdges);
    const vis = window.vis;
    const visNodes = new vis.DataSet(visNodeRows);
    const visEdges = new vis.DataSet(visEdgeRows);
    const data = { nodes: visNodes, edges: visEdges };
    const docGroups = typeof prksDocTypeVisGroups === 'function' ? prksDocTypeVisGroups() : {};
    const labelColor = prksGraphLabelFontColor();
    const options = {
        groups: docGroups,
        nodes: {
            shape: 'dot',
            size: 16,
            font: {
                size: 11,
                face: 'Inter, sans-serif',
                multi: true,
                color: labelColor,
            },
            borderWidth: 2,
            chosen: {
                node: (values) => {
                    values.borderWidth = 3;
                },
            },
        },
        edges: {
            smooth: { type: 'continuous' },
            arrowStrikethrough: false,
        },
        physics: {
            enabled: true,
            stabilization: { iterations: 100 },
            barnesHut: { gravitationalConstant: -2200 },
        },
        interaction: {
            hover: true,
            tooltipDelay: 120,
            selectConnectedEdges: false,
        },
    };

    const network = new vis.Network(netEl, data, options);
    window.__prksContextGraphState = { network };

    if (statusEl) {
        const n = visNodeRows.length;
        const m = visEdgeRows.length;
        statusEl.textContent =
            spec.mode === 'work'
                ? `${n} file${n === 1 ? '' : 's'} (this one + direct links) · ${m} link${m === 1 ? '' : 's'}`
                : `${n} linked file${n === 1 ? '' : 's'} · ${m} link${m === 1 ? '' : 's'} among them`;
    }

    network.on('click', (params) => {
        if (params.nodes && params.nodes.length > 0) {
            const nodeId = params.nodes[0];
            const wantNewTab = prksGraphEventIsMiddleClick(params);
            prksGraphOpenWork(nodeId, { newTab: wantNewTab });
        }
    });

    network.once('stabilizationIterationsDone', () => {
        try {
            network.setOptions({ physics: false });
            network.fit({ animation: { duration: 200 } });
        } catch (_e) {
            try {
                network.setOptions({ physics: false });
            } catch (_e2) {
                /* ignore */
            }
        }
    });
}
