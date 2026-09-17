/**
 * Research Graph: read-only Cytoscape projection of canonical research relations.
 * Graph projection snapshot may be cached. Graph UI/layout state is never persisted.
 * Canonical graph state is never written by the Graph; edits happen on record pages.
 */
(function (root) {
    'use strict';

    const NODE_FILTER_KEYS = ['concepts', 'positions', 'arguments', 'works', 'people'];
    const EDGE_FILTER_KEYS = ['hierarchy', 'responds', 'sources', 'mentions'];
    const DEFAULT_FILTERS = {
        concepts: true,
        positions: true,
        arguments: true,
        works: true,
        people: false,
        hierarchy: true,
        responds: true,
        sources: true,
        mentions: true,
    };

    // activeRuntime singleton removed; use ctx.getResource('researchGraph') exclusively.

    function esc(s) {
        if (typeof root.prksEscapeHtml === 'function') return root.prksEscapeHtml(s);
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function peopleRequiredForFocus(focus) {
        return String(focus || '').startsWith('person:');
    }

    function token(name, fallback) {
        if (typeof document === 'undefined' || !document.documentElement) return fallback;
        try {
            const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
            return v || fallback;
        } catch (_e) {
            return fallback;
        }
    }

    function inspectorLabelForEdge(edge) {
        const t = edge && edge.type;
        if (t === 'concept_parent') return 'Subconcept of';
        if (t === 'argument_position' || t === 'argument_argument') {
            return String(edge.verdict_label || edge.verdict_id || 'Responds to');
        }
        if (t === 'argument_source') return 'Made/taken in';
        if (t === 'mentions_concept' || t === 'mentions_argument') {
            return 'Mentioned in research notes';
        }
        if (t === 'work_author') return 'Author';
        return t || '';
    }

    function canvasLabelForEdge(edge) {
        const t = edge && edge.type;
        if (t === 'concept_parent') return 'Subconcept';
        if (t === 'argument_position' || t === 'argument_argument') {
            return String(edge.verdict_label || edge.verdict_id || 'Responds to');
        }
        if (t === 'argument_source') return 'Source';
        if (t === 'mentions_concept' || t === 'mentions_argument') {
            const n = Number(edge.count) || 0;
            return n > 1 ? 'Note mention ×' + n : 'Note mention';
        }
        if (t === 'work_author') return 'Author';
        return t || '';
    }

    function displayLabelForEdge(edge) {
        return inspectorLabelForEdge(edge);
    }

    function nodeClasses(node) {
        const type = node && node.type ? String(node.type) : '';
        const kind = node && node.kind ? String(node.kind) : '';
        const parts = ['graph-node', 'graph-node--' + type];
        if (type === 'argument' && kind === 'stance') parts.push('graph-node--stance');
        return parts.join(' ');
    }

    function edgeClasses(edge) {
        const t = edge && edge.type ? String(edge.type) : '';
        if (t === 'concept_parent') return 'graph-edge graph-edge--hierarchy';
        if (t === 'argument_position' || t === 'argument_argument') return 'graph-edge graph-edge--responds';
        if (t === 'argument_source') return 'graph-edge graph-edge--source';
        if (t === 'mentions_concept' || t === 'mentions_argument') return 'graph-edge graph-edge--mentions';
        if (t === 'work_author') return 'graph-edge graph-edge--author';
        return 'graph-edge';
    }

    function toCytoscapeElements(data) {
        const nodes = Array.isArray(data && data.nodes) ? data.nodes : [];
        const edges = Array.isArray(data && data.edges) ? data.edges : [];
        const out = [];
        for (let i = 0; i < nodes.length; i++) {
            const n = nodes[i];
            out.push({
                group: 'nodes',
                data: {
                    id: n.id,
                    record_id: n.record_id,
                    type: n.type,
                    kind: n.kind || '',
                    label: n.label || n.record_id || n.id,
                    route: n.route || '',
                    doc_type: n.doc_type || '',
                },
                classes: nodeClasses(n),
            });
        }
        for (let j = 0; j < edges.length; j++) {
            const e = edges[j];
            out.push({
                group: 'edges',
                data: {
                    id: e.id,
                    source: e.source,
                    target: e.target,
                    type: e.type,
                    verdict_id: e.verdict_id || '',
                    verdict_label: e.verdict_label || '',
                    pages: e.pages || '',
                    count: e.count || 0,
                    canvasLabel: canvasLabelForEdge(e),
                    inspectorLabel: inspectorLabelForEdge(e),
                    displayLabel: inspectorLabelForEdge(e),
                },
                classes: edgeClasses(e),
            });
        }
        return out;
    }

    function nodeVisible(node, f) {
        const t = node && node.type;
        if (t === 'concept') return !!f.concepts;
        if (t === 'position') return !!f.positions;
        if (t === 'argument') return !!f.arguments;
        if (t === 'work') return !!f.works;
        if (t === 'person') return !!f.people;
        return true;
    }

    function edgeVisible(edge, f, visibleIds) {
        if (!visibleIds.has(edge.source) || !visibleIds.has(edge.target)) return false;
        const t = edge.type;
        if (t === 'concept_parent') return !!f.hierarchy;
        if (t === 'argument_position' || t === 'argument_argument') return !!f.responds;
        if (t === 'argument_source') return !!f.sources;
        if (t === 'mentions_concept' || t === 'mentions_argument') return !!f.mentions;
        if (t === 'work_author') return !!f.people;
        return true;
    }

    function visibleGraph(data, f) {
        const nodes = Array.isArray(data && data.nodes) ? data.nodes : [];
        const edges = Array.isArray(data && data.edges) ? data.edges : [];
        const shownNodes = nodes.filter(function (n) {
            return nodeVisible(n, f);
        });
        const ids = new Set(
            shownNodes.map(function (n) {
                return n.id;
            })
        );
        const shownEdges = edges.filter(function (e) {
            return edgeVisible(e, f, ids);
        });
        return { nodes: shownNodes, edges: shownEdges };
    }

    function applyGraphFilters(cy, data, f) {
        if (!cy || !data) return visibleGraph(data, f);
        const vis = visibleGraph(data, f);
        const keepNodes = new Set(
            vis.nodes.map(function (n) {
                return n.id;
            })
        );
        const keepEdges = new Set(
            vis.edges.map(function (e) {
                return e.id;
            })
        );
        cy.batch(function () {
            cy.nodes().forEach(function (n) {
                n.style('display', keepNodes.has(n.id()) ? 'element' : 'none');
            });
            cy.edges().forEach(function (e) {
                e.style('display', keepEdges.has(e.id()) ? 'element' : 'none');
            });
        });
        return vis;
    }

    function findNodesByLabel(data, query) {
        const q = String(query || '')
            .trim()
            .toLowerCase();
        const nodes = Array.isArray(data && data.nodes) ? data.nodes : [];
        if (!q) return [];
        const out = [];
        for (let i = 0; i < nodes.length; i++) {
            const n = nodes[i];
            const label = String(n.label || '').toLowerCase();
            if (label.indexOf(q) >= 0) out.push(n);
        }
        return out;
    }

    function nodeById(data, id) {
        const nodes = Array.isArray(data && data.nodes) ? data.nodes : [];
        for (let i = 0; i < nodes.length; i++) {
            if (nodes[i].id === id) return nodes[i];
        }
        return null;
    }

    function edgeById(data, id) {
        const edges = Array.isArray(data && data.edges) ? data.edges : [];
        for (let i = 0; i < edges.length; i++) {
            if (edges[i].id === id) return edges[i];
        }
        return null;
    }

    function selectionContextIds(data, nodeId, edgeId) {
        const nodeIds = {};
        const edgeIds = {};
        function addNode(id) {
            if (id) nodeIds[id] = true;
        }
        function addEdge(id) {
            if (id) edgeIds[id] = true;
        }
        if (edgeId) {
            const e = edgeById(data, edgeId);
            if (e) {
                addEdge(e.id);
                addNode(e.source);
                addNode(e.target);
            }
        }
        if (nodeId) {
            addNode(nodeId);
            const edges = Array.isArray(data && data.edges) ? data.edges : [];
            for (let i = 0; i < edges.length; i++) {
                const e = edges[i];
                if (e.source === nodeId || e.target === nodeId) {
                    addEdge(e.id);
                    addNode(e.source);
                    addNode(e.target);
                }
            }
        }
        return { nodeIds: nodeIds, edgeIds: edgeIds };
    }

    function edgeShouldShowCanvasLabel(edgeId, ctx) {
        if (!edgeId || !ctx) return false;
        if (ctx.hoverEdgeId && edgeId === ctx.hoverEdgeId) return true;
        if (ctx.selectedEdgeId && edgeId === ctx.selectedEdgeId) return true;
        return false;
    }

    function neighborGroups(data, nodeId) {
        const edges = Array.isArray(data && data.edges) ? data.edges : [];
        const groups = {
            parents: [],
            subconcepts: [],
            respondsTo: [],
            responses: [],
            sources: [],
            mentionedConcepts: [],
            mentionedArguments: [],
            noteLinkedWorks: [],
            authors: [],
            authoredWorks: [],
        };
        for (let i = 0; i < edges.length; i++) {
            const e = edges[i];
            if (e.type === 'concept_parent' && e.source === nodeId) {
                groups.parents.push({ id: e.target, edge: e });
            } else if (e.type === 'concept_parent' && e.target === nodeId) {
                groups.subconcepts.push({ id: e.source, edge: e });
            } else if (
                (e.type === 'argument_position' || e.type === 'argument_argument') &&
                e.source === nodeId
            ) {
                groups.respondsTo.push({ id: e.target, edge: e });
            } else if (
                (e.type === 'argument_position' || e.type === 'argument_argument') &&
                e.target === nodeId
            ) {
                groups.responses.push({ id: e.source, edge: e });
            } else if (e.type === 'argument_source' && e.source === nodeId) {
                groups.sources.push({ id: e.target, edge: e });
            } else if (e.type === 'mentions_concept' && e.source === nodeId) {
                groups.mentionedConcepts.push({ id: e.target, edge: e });
            } else if (e.type === 'mentions_argument' && e.source === nodeId) {
                groups.mentionedArguments.push({ id: e.target, edge: e });
            } else if (
                (e.type === 'mentions_concept' || e.type === 'mentions_argument') &&
                e.target === nodeId
            ) {
                groups.noteLinkedWorks.push({ id: e.source, edge: e });
            } else if (e.type === 'work_author' && e.target === nodeId) {
                groups.authors.push({ id: e.source, edge: e });
            } else if (e.type === 'work_author' && e.source === nodeId) {
                groups.authoredWorks.push({ id: e.target, edge: e });
            }
        }
        return groups;
    }

    function typeLabel(node) {
        if (!node) return 'Record';
        if (node.type === 'argument' && node.kind === 'stance') return 'Stance';
        if (node.type === 'concept') return 'Concept';
        if (node.type === 'position') return 'Position';
        if (node.type === 'argument') return 'Argument';
        if (node.type === 'work') return 'Work';
        if (node.type === 'person') return 'Person';
        return node.type || 'Record';
    }

    function openLabel(node) {
        const t = typeLabel(node);
        return 'Open ' + t;
    }

    function inspectorModel(data, nodeId) {
        const node = nodeById(data, nodeId);
        if (!node) return null;
        const g = neighborGroups(data, nodeId);
        const stats = [];
        if (node.type === 'concept') {
            stats.push({ label: 'Parents', value: g.parents.length });
            stats.push({ label: 'Subconcepts', value: g.subconcepts.length });
            stats.push({ label: 'Note-linked works', value: g.noteLinkedWorks.length });
        } else if (node.type === 'argument') {
            stats.push({ label: 'Targets', value: g.respondsTo.length });
            stats.push({ label: 'Sources', value: g.sources.length });
            stats.push({ label: 'Responses', value: g.responses.length });
        } else if (node.type === 'position') {
            stats.push({ label: 'Arguments', value: g.responses.length });
        } else if (node.type === 'work') {
            const researchConnections =
                g.sources.length +
                g.mentionedConcepts.length +
                g.mentionedArguments.length +
                g.authors.length;
            stats.push({ label: 'Research connections', value: researchConnections });
        } else if (node.type === 'person') {
            stats.push({ label: 'Authored works', value: g.authoredWorks.length });
        }
        const lists = [];
        function pushList(title, items) {
            if (!items.length) return;
            lists.push({
                title: title,
                items: items.map(function (row) {
                    const n = nodeById(data, row.id);
                    return {
                        id: row.id,
                        label: n ? n.label : row.id,
                        hint: inspectorLabelForEdge(row.edge),
                    };
                }),
            });
        }
        pushList('Parents', g.parents);
        pushList('Subconcepts', g.subconcepts);
        pushList('Responds to', g.respondsTo);
        pushList('Sources', g.sources);
        pushList('Mentioned concepts', g.mentionedConcepts);
        pushList('Mentioned arguments', g.mentionedArguments);
        pushList('Responses', g.responses);
        pushList('Mentioned in research notes', g.noteLinkedWorks);
        pushList('Authors', g.authors);
        pushList('Authored works', g.authoredWorks);
        return {
            id: node.id,
            type: node.type,
            kind: node.kind || '',
            typeLabel: typeLabel(node),
            label: node.label,
            route: node.route,
            openLabel: openLabel(node),
            stats: stats,
            lists: lists,
        };
    }

    function inspectorEdgeModel(data, edgeId) {
        const edge = edgeById(data, edgeId);
        if (!edge) return null;
        const source = nodeById(data, edge.source);
        const target = nodeById(data, edge.target);
        const relation = inspectorLabelForEdge(edge);
        let kicker = 'Relation';
        if (edge.type === 'argument_source') kicker = 'Source';
        const stats = [];
        if (edge.type === 'mentions_concept' || edge.type === 'mentions_argument') {
            stats.push({ label: 'Occurrences', value: Number(edge.count) || 1 });
        }
        if (edge.pages) {
            stats.push({ label: 'Pages', value: edge.pages });
        }
        return {
            id: edge.id,
            type: edge.type,
            kicker: kicker,
            relation: relation,
            source: source,
            target: target,
            stats: stats,
            openSourceLabel: source ? openLabel(source) : '',
            openTargetLabel: target ? openLabel(target) : '',
        };
    }

    function navigateToNode(node) {
        if (!node || !node.route) return false;
        if (typeof root.prksNavigate === 'function') {
            root.prksNavigate(node.route);
            return true;
        }
        return false;
    }

    function nodeIconStyle(lucideName, borderColor, extra) {
        const uri =
            typeof root.prksLucideSvgDataUri === 'function'
                ? root.prksLucideSvgDataUri(lucideName, borderColor)
                : '';
        const style = Object.assign(
            {
                shape: 'ellipse',
                width: 42,
                height: 42,
                'border-width': 2,
                'border-color': borderColor,
            },
            extra || {}
        );
        if (uri) {
            style['background-image'] = 'url("' + uri.replace(/"/g, '%22') + '")';
            style['background-fit'] = 'none';
            style['background-clip'] = 'none';
            style['background-width'] = '58%';
            style['background-height'] = '58%';
            style['background-position-x'] = '50%';
            style['background-position-y'] = '50%';
        }
        return style;
    }

    function cytoscapeStyle() {
        const text = token('--text-primary', '#1e293b');
        const secondary = token('--text-secondary', '#475569');
        const accent = token('--accent', '#6d6cf7');
        const accentSoft = token('--accent-soft', '#ececff');
        const surface = token('--surface', '#f8fafc');
        const muted = token('--surface-muted', '#f1f5f9');
        const border = token('--border', '#e2e8f0');
        const planned = token('--status-planned-border', '#7c3aed');
        const progress = token('--status-progress-border', '#ca8a04');
        const completed = token('--status-completed-border', '#16a34a');
        const paused = token('--status-paused-border', '#dc2626');
        return [
            {
                selector: 'node',
                style: {
                    label: 'data(label)',
                    color: text,
                    'font-size': 11,
                    'font-family': token('--font-family', 'Inter, sans-serif'),
                    'text-valign': 'bottom',
                    'text-halign': 'center',
                    'text-margin-y': 8,
                    'text-wrap': 'ellipsis',
                    'text-max-width': 110,
                    'background-color': surface,
                    'border-width': 2,
                    'border-color': border,
                    width: 28,
                    height: 28,
                    opacity: 1,
                    'overlay-opacity': 0,
                },
            },
            {
                selector: 'node.graph-node--concept',
                style: nodeIconStyle('network', accent),
            },
            {
                selector: 'node.graph-node--position',
                style: nodeIconStyle('flag', planned),
            },
            {
                selector: 'node.graph-node--argument',
                style: nodeIconStyle('messages-square', progress),
            },
            {
                selector: 'node.graph-node--stance',
                style: nodeIconStyle('messages-square', completed),
            },
            {
                selector: 'node.graph-node--work',
                style: nodeIconStyle('file-text', secondary, { 'background-color': muted }),
            },
            {
                selector: 'node.graph-node--person',
                style: nodeIconStyle('user', paused),
            },
            {
                selector: 'node:selected, node.graph-node--selected',
                style: {
                    'border-width': 2,
                    'border-color': token('--border-strong', '#9ca3af'),
                    'background-color': token('--surface-selected', '#f3f4f6'),
                },
            },
            {
                selector: 'node.graph-dim',
                style: { opacity: 0.3 },
            },
            {
                selector: 'edge',
                style: {
                    label: '',
                    color: text,
                    'font-size': 9,
                    'text-rotation': 'none',
                    'text-background-color': muted,
                    'text-background-opacity': 1,
                    'text-background-padding': 3,
                    'text-background-shape': 'roundrectangle',
                    'curve-style': 'bezier',
                    'target-arrow-shape': 'triangle',
                    'arrow-scale': 0.8,
                    width: 1.5,
                    'line-color': secondary,
                    'target-arrow-color': secondary,
                    opacity: 1,
                    'overlay-opacity': 0,
                },
            },
            {
                selector: 'edge.graph-edge--hierarchy',
                style: { 'line-style': 'solid', width: 2 },
            },
            {
                selector: 'edge.graph-edge--responds',
                style: { 'line-style': 'solid', width: 2, 'line-color': progress, 'target-arrow-color': progress },
            },
            {
                selector: 'edge.graph-edge--source',
                style: { 'line-style': 'solid', width: 2, 'line-color': accent, 'target-arrow-color': accent },
            },
            {
                selector: 'edge.graph-edge--mentions',
                style: {
                    'line-style': 'dotted',
                    width: 1.5,
                    'line-color': completed,
                    'target-arrow-color': completed,
                    'target-arrow-shape': 'tee',
                },
            },
            {
                selector: 'edge.graph-edge--author',
                style: { 'line-style': 'dashed', width: 1.5 },
            },
            {
                selector: 'edge.graph-edge--label-on',
                style: { label: 'data(canvasLabel)' },
            },
            {
                selector: 'edge.graph-focus',
                style: { width: 2.75, opacity: 1 },
            },
            {
                selector: 'edge.graph-dim',
                style: { opacity: 0.15 },
            },
        ];
    }

    function coseLayoutOptions(randomize) {
        return {
            name: 'cose',
            animate: false,
            randomize: !!randomize,
            fit: true,
            padding: 48,
            nodeDimensionsIncludeLabels: true,
            componentSpacing: 100,
            nodeOverlap: 10,
            gravity: 0.35,
            numIter: 1400,
            nodeRepulsion: function () {
                return 18000;
            },
            idealEdgeLength: function () {
                return 150;
            },
        };
    }

    function inspectorStatsHtml(stats) {
        if (!stats || !stats.length) return '';
        let html = '<ul class="person-sidebar__stats">';
        for (let i = 0; i < stats.length; i++) {
            html +=
                '<li><span class="research-graph__stat-label">' +
                esc(stats[i].label) +
                '</span> ' +
                esc(String(stats[i].value)) +
                '</li>';
        }
        html += '</ul>';
        return html;
    }

    function inspectorHeadHtml(kicker) {
        return (
            '<div class="research-graph__inspector-head">' +
            '<p class="saved-view-detail__kicker">' +
            esc(kicker) +
            '</p>' +
            '<button type="button" class="prks-btn prks-btn--ghost prks-btn--sm research-graph__clear-selection" data-graph-clear-selection>Clear selection</button>' +
            '</div>'
        );
    }

    function inspectorNeighborHtml(item) {
        return (
            '<button type="button" class="prks-list-row prks-research-row research-graph__neighbor" data-graph-node="' +
            esc(item.id) +
            '"><span class="prks-research-row__body"><span class="prks-research-row__title">' +
            esc(item.label) +
            '</span>' +
            (item.hint
                ? '<span class="prks-research-row__meta"><span class="prks-research-row__meta-item">' +
                  esc(item.hint) +
                  '</span></span>'
                : '') +
            '</span></button>'
        );
    }

    function reloadGraphFailureMessage(err) {
        if (err && err.code === 'graph_too_large') {
            return 'Graph is too large to render as a single snapshot.';
        }
        return 'Could not load Research Graph.';
    }

    function queryGraphRole(scope, role) {
        if (!scope || typeof scope.querySelector !== 'function') return null;
        try {
            return scope.querySelector('[data-prks-role="' + role + '"]');
        } catch (_e) {
            return null;
        }
    }

    function inspectorEl(liveDom) {
        if (typeof document !== 'undefined' && document.getElementById) {
            const panel = document.getElementById('panel-content');
            if (panel && typeof panel.querySelector === 'function') {
                const fromPanel = panel.querySelector('#prks-graph-inspector');
                if (fromPanel) return fromPanel;
            }
        }
        if (liveDom && liveDom.querySelector) return liveDom.querySelector('#prks-graph-inspector');
        return null;
    }

    function resolveGraphCtx(opts) {
        if (opts && opts.ctx) return opts.ctx;
        if (typeof root.prksGetFocusedTabContext === 'function') {
            return root.prksGetFocusedTabContext() || null;
        }
        return null;
    }

    function resolveActiveRuntime() {
        const ctx = typeof root.prksGetFocusedTabContext === 'function' ? root.prksGetFocusedTabContext() : null;
        return ctx && typeof ctx.getResource === 'function' ? ctx.getResource('researchGraph') : null;
    }

    function createResearchGraphRuntime(ctx, container, options) {
        options = options || {};
        let liveCy = null;
        let liveDom = null;
        let snapshot = null;
        let filters = Object.assign({}, DEFAULT_FILTERS);
        let includePeople = false;
        let selectedId = '';
        let selectedEdgeId = '';
        let hoverEdgeId = '';
        let findQuery = '';
        let findHits = [];
        let findIndex = -1;
        let statusMessage = '';
        let pendingFocus = '';
        let boundKeyHandler = null;
        let reloadGeneration = 0;
        let graphRouteSignal = options.signal || null;
        let destroyed = false;
        const unbinders = [];

        function chromeId(local) {
            if (ctx && typeof ctx.domId === 'function') return ctx.domId(local);
            return '';
        }

        function queryRole(role) {
            const fromLive = queryGraphRole(liveDom, role);
            if (fromLive) return fromLive;
            const fromContainer = queryGraphRole(container, role);
            if (fromContainer) return fromContainer;
            if (ctx && typeof ctx.query === 'function') {
                const fromCtx = ctx.query('[data-prks-role="' + role + '"]');
                if (fromCtx) return fromCtx;
            }
            if (ctx && ctx.root) return queryGraphRole(ctx.root, role);
            return null;
        }

        function listen(el, type, fn) {
            if (!el || typeof el.addEventListener !== 'function') return;
            el.addEventListener(type, fn);
            unbinders.push(function () {
                if (typeof el.removeEventListener === 'function') el.removeEventListener(type, fn);
            });
        }

        function unbindAll() {
            while (unbinders.length) {
                try {
                    unbinders.pop()();
                } catch (_e) {}
            }
            boundKeyHandler = null;
        }

        function graphReloadIsStale(gen, originDom) {
            return destroyed || gen !== reloadGeneration || liveDom !== originDom;
        }

        function teardownCy() {
            if (liveCy) {
                try {
                    liveCy.destroy();
                } catch (_e) {}
                liveCy = null;
            }
            root.__prksResearchGraphLiveCount = 0;
        }

        function destroy() {
            if (destroyed) return;
            destroyed = true;
            reloadGeneration += 1;
            unbindAll();
            teardownCy();
            liveDom = null;
            if (container && container.__prksGraphRuntime === runtime) {
                container.__prksGraphRuntime = null;
            }
            if (ctx && typeof ctx.getResource === 'function' && ctx.getResource('researchGraph') === runtime) {
                if (typeof ctx.clearResource === 'function') ctx.clearResource('researchGraph');
            }
        }

        function debug() {
            return { cy: liveCy, liveCount: liveCy ? 1 : 0 };
        }

        function updateEdgeLabels(cy) {
            if (!cy) return;
            const labelCtx = {
                hoverEdgeId: hoverEdgeId,
                selectedEdgeId: selectedEdgeId,
            };
            cy.edges().forEach(function (e) {
                if (edgeShouldShowCanvasLabel(e.id(), labelCtx)) e.addClass('graph-edge--label-on');
                else e.removeClass('graph-edge--label-on');
            });
        }

        function applySelectionContext(cy) {
            if (!cy) return;
            const hasFocus = !!(selectedId || selectedEdgeId);
            const selCtx = selectionContextIds(snapshot, selectedId, selectedEdgeId);
            cy.batch(function () {
                cy.nodes().forEach(function (n) {
                    const keep = !hasFocus || selCtx.nodeIds[n.id()];
                    if (keep) n.removeClass('graph-dim');
                    else n.addClass('graph-dim');
                    if (selectedId && n.id() === selectedId) n.addClass('graph-node--selected');
                    else n.removeClass('graph-node--selected');
                });
                cy.edges().forEach(function (e) {
                    const keep = !hasFocus || selCtx.edgeIds[e.id()];
                    if (keep) {
                        e.removeClass('graph-dim');
                        if (hasFocus) e.addClass('graph-focus');
                        else e.removeClass('graph-focus');
                    } else {
                        e.addClass('graph-dim');
                        e.removeClass('graph-focus');
                    }
                });
            });
            updateEdgeLabels(cy);
        }

        function centerNode(cy, id) {
            if (!cy || !id) return false;
            const el = cy.getElementById(id);
            if (!el || !el.length || el.empty()) return false;
            try {
                cy.animate({
                    fit: { eles: el, padding: 80 },
                    duration: 180,
                });
            } catch (_e) {
                try {
                    cy.fit(el, 80);
                } catch (_e2) {}
            }
            return true;
        }

        function selectNode(id, opts) {
            selectedId = id || '';
            selectedEdgeId = '';
            hoverEdgeId = '';
            const cy = liveCy;
            if (cy) {
                cy.elements().unselect();
                if (id) {
                    const el = cy.getElementById(id);
                    if (el && el.length && !el.empty()) {
                        el.select();
                        if (!opts || opts.center !== false) centerNode(cy, id);
                    }
                }
                applySelectionContext(cy);
            }
            renderInspector();
            syncInspectorVisibility();
            return selectedId;
        }

        function selectEdge(id) {
            selectedEdgeId = id || '';
            selectedId = '';
            const cy = liveCy;
            if (cy) {
                cy.elements().unselect();
                if (id) {
                    const el = cy.getElementById(id);
                    if (el && el.length && !el.empty()) el.select();
                }
                applySelectionContext(cy);
            }
            renderInspector();
            syncInspectorVisibility();
            return selectedEdgeId;
        }

        function clearGraphSelection() {
            selectedId = '';
            selectedEdgeId = '';
            hoverEdgeId = '';
            const cy = liveCy;
            if (cy) {
                cy.elements().unselect();
                applySelectionContext(cy);
            }
            renderInspector();
            syncInspectorVisibility();
        }

        function inspectorClick(ev) {
            const t = ev.target;
            if (!t || !t.closest) return;
            const clearBtn = t.closest('[data-graph-clear-selection]');
            if (clearBtn) {
                ev.preventDefault();
                clearGraphSelection();
                return;
            }
            const hit = t.closest('[data-graph-node]');
            if (hit) {
                ev.preventDefault();
                chooseFindHit(hit.getAttribute('data-graph-node'));
                return;
            }
            if (t.id === 'prks-graph-open' || t.closest('#prks-graph-open')) {
                ev.preventDefault();
                openSelectedRecord(selectedId);
                return;
            }
            const openBtn = t.closest('[data-graph-open]');
            if (openBtn) {
                ev.preventDefault();
                openSelectedRecord(openBtn.getAttribute('data-graph-open'));
            }
        }

        function ensureInspectorBound(el) {
            if (!el || typeof el.addEventListener !== 'function') return;
            if (el.getAttribute && el.getAttribute('data-graph-inspector-bound') === '1') return;
            if (el.setAttribute) el.setAttribute('data-graph-inspector-bound', '1');
            listen(el, 'click', inspectorClick);
            unbinders.push(function () {
                if (el.removeAttribute) el.removeAttribute('data-graph-inspector-bound');
            });
        }

        function paintInspector(html) {
            const el = inspectorEl(liveDom);
            if (!el) return;
            el.innerHTML = html;
            ensureInspectorBound(el);
        }

        function renderStatusMessage() {
            const el = queryRole('graph-status');
            if (!el) return;
            if (statusMessage) {
                el.textContent = statusMessage;
                el.hidden = false;
            } else {
                el.textContent = '';
                el.hidden = true;
            }
        }

        function syncInspectorVisibility() {
            if (typeof root.prksRefreshFocusedRightPanelVisibility === 'function') {
                root.prksRefreshFocusedRightPanelVisibility();
            }
            const cy = liveCy;
            if (!cy || typeof cy.resize !== 'function') return;
            if (typeof root.requestAnimationFrame === 'function') {
                root.requestAnimationFrame(function () {
                    if (!destroyed && liveCy === cy) cy.resize();
                });
            } else {
                cy.resize();
            }
        }

        function renderInspector() {
            const edgeModel = selectedEdgeId ? inspectorEdgeModel(snapshot, selectedEdgeId) : null;
            const model = !edgeModel ? inspectorModel(snapshot, selectedId) : null;
            let html = '';
            if (edgeModel) {
                html +=
                    inspectorHeadHtml(edgeModel.kicker) +
                    '<p class="card-title" id="prks-graph-inspector-title">' +
                    esc(edgeModel.relation) +
                    '</p>' +
                    '<p class="meta-row meta-row--compact research-graph__edge-ends">' +
                    '<span class="research-graph__edge-end">' +
                    esc(edgeModel.source ? edgeModel.source.label : '') +
                    '</span>' +
                    '<span class="research-graph__edge-arrow" aria-hidden="true"> → </span>' +
                    '<span class="research-graph__edge-end">' +
                    esc(edgeModel.target ? edgeModel.target.label : '') +
                    '</span></p>' +
                    inspectorStatsHtml(edgeModel.stats) +
                    '<div class="research-graph__inspector-actions">';
                if (edgeModel.source) {
                    html +=
                        '<button type="button" class="prks-btn prks-btn--secondary" data-graph-open="' +
                        esc(edgeModel.source.id) +
                        '">' +
                        esc(edgeModel.openSourceLabel) +
                        '</button>';
                }
                if (edgeModel.target) {
                    html +=
                        '<button type="button" class="prks-btn prks-btn--secondary" data-graph-open="' +
                        esc(edgeModel.target.id) +
                        '">' +
                        esc(edgeModel.openTargetLabel) +
                        '</button>';
                }
                html += '</div>';
                paintInspector(html);
                return edgeModel;
            }
            if (!model) {
                paintInspector('');
                return model;
            }
            html +=
                inspectorHeadHtml(model.typeLabel) +
                '<p class="card-title" id="prks-graph-inspector-title">' +
                esc(model.label) +
                '</p>' +
                inspectorStatsHtml(model.stats) +
                '<div class="research-graph__inspector-actions">' +
                '<button type="button" class="prks-btn prks-btn--primary" id="prks-graph-open">' +
                esc(model.openLabel) +
                '</button></div>';
            for (let g = 0; g < model.lists.length; g++) {
                const list = model.lists[g];
                html +=
                    '<p class="person-sidebar__section-label">' +
                    esc(list.title) +
                    '</p><div class="research-graph__neighbors">';
                for (let j = 0; j < list.items.length; j++) {
                    html += inspectorNeighborHtml(list.items[j]);
                }
                html += '</div>';
            }
            paintInspector(html);
            return model;
        }

        function renderFindResults() {
            if (!liveDom) return;
            const box = queryRole('graph-find-results');
            if (!box) return;
            if (!findQuery || !findHits.length) {
                box.innerHTML = findQuery ? '<p class="meta-row">No matching nodes.</p>' : '';
                box.hidden = !findQuery;
                return;
            }
            box.hidden = false;
            box.innerHTML = findHits
                .map(function (n, i) {
                    return (
                        '<button type="button" class="prks-list-row research-graph__find-hit' +
                        (i === findIndex ? ' is-active' : '') +
                        '" role="option" aria-selected="' +
                        (i === findIndex ? 'true' : 'false') +
                        '" data-graph-node="' +
                        esc(n.id) +
                        '">' +
                        esc(n.label) +
                        ' <span class="meta-row">' +
                        esc(typeLabel(n)) +
                        '</span></button>'
                    );
                })
                .join('');
        }

        function runFind(query) {
            findQuery = String(query || '');
            findHits = findNodesByLabel(snapshot, findQuery);
            findIndex = findHits.length ? 0 : -1;
            renderFindResults();
            return findHits;
        }

        function chooseFindHit(id) {
            const vis = visibleGraph(snapshot, filters);
            const allowed = vis.nodes.some(function (n) {
                return n.id === id;
            });
            if (!allowed) {
                statusMessage = 'That node is hidden by the current filters.';
                renderStatusMessage();
                return;
            }
            statusMessage = '';
            renderStatusMessage();
            selectNode(id);
        }

        function openSelectedRecord(nodeOrId) {
            const node = typeof nodeOrId === 'string' ? nodeById(snapshot, nodeOrId) : nodeOrId;
            return navigateToNode(node);
        }

        function fit() {
            if (liveCy) liveCy.fit(undefined, 40);
        }

        function rerunLayout() {
            if (!liveCy) return;
            const layout = liveCy.layout(coseLayoutOptions(true));
            layout.run();
        }

        function focusRole(role) {
            const el = queryRole(role);
            if (el && typeof el.focus === 'function') el.focus();
        }

        function auxPanelEls() {
            return {
                filtersBtn: queryRole('graph-filters-toggle'),
                filtersPanel: queryRole('graph-filters-panel'),
                legendBtn: queryRole('graph-legend-toggle'),
                legendPanel: queryRole('graph-legend-panel'),
            };
        }

        function setAuxPanelOpen(which, open) {
            const els = auxPanelEls();
            const btn = which === 'filters' ? els.filtersBtn : els.legendBtn;
            const panel = which === 'filters' ? els.filtersPanel : els.legendPanel;
            if (panel) panel.hidden = !open;
            if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        }

        function toggleAuxPanel(which) {
            const els = auxPanelEls();
            const panel = which === 'filters' ? els.filtersPanel : els.legendPanel;
            const isOpen = !!(panel && !panel.hidden);
            setAuxPanelOpen('filters', false);
            setAuxPanelOpen('legend', false);
            if (!isOpen) setAuxPanelOpen(which, true);
        }

        function closeAuxPanels() {
            setAuxPanelOpen('filters', false);
            setAuxPanelOpen('legend', false);
        }

        function bindShell(host) {
            liveDom = host && host.querySelector ? host.querySelector('.research-graph') : null;
            if (!liveDom) return;
            listen(liveDom, 'click', function (ev) {
                const t = ev.target;
                if (!t || !t.closest) return;
                const hit = t.closest('[data-graph-node]');
                if (hit) {
                    ev.preventDefault();
                    chooseFindHit(hit.getAttribute('data-graph-node'));
                    return;
                }
                if (t.id === 'prks-graph-open' || (t.closest && t.closest('#prks-graph-open'))) {
                    ev.preventDefault();
                    openSelectedRecord(selectedId);
                    return;
                }
                const openBtn = t.closest('[data-graph-open]');
                if (openBtn) {
                    ev.preventDefault();
                    openSelectedRecord(openBtn.getAttribute('data-graph-open'));
                    return;
                }
                if (t.closest('[data-prks-role="graph-fit"]')) {
                    fit();
                    return;
                }
                if (t.closest('[data-prks-role="graph-reset"]')) {
                    rerunLayout();
                    return;
                }
                if (t.closest('[data-prks-role="graph-filters-toggle"]')) {
                    toggleAuxPanel('filters');
                    return;
                }
                if (t.closest('[data-prks-role="graph-legend-toggle"]')) {
                    toggleAuxPanel('legend');
                }
            });
            listen(liveDom, 'keydown', function (ev) {
                if (ev.key !== 'Escape') return;
                const t = ev.target;
                if (!t || !t.closest) return;
                const els = auxPanelEls();
                const partOfFilters = !!(
                    t.closest('[data-prks-role="graph-filters-panel"]') ||
                    t.closest('[data-prks-role="graph-filters-toggle"]')
                );
                const partOfLegend = !!(
                    t.closest('[data-prks-role="graph-legend-panel"]') ||
                    t.closest('[data-prks-role="graph-legend-toggle"]')
                );
                if (partOfFilters && els.filtersPanel && !els.filtersPanel.hidden) {
                    ev.stopPropagation();
                    setAuxPanelOpen('filters', false);
                    focusRole('graph-filters-toggle');
                } else if (partOfLegend && els.legendPanel && !els.legendPanel.hidden) {
                    ev.stopPropagation();
                    setAuxPanelOpen('legend', false);
                    focusRole('graph-legend-toggle');
                }
            });
            const findInput = queryRole('graph-find');
            if (findInput) {
                listen(findInput, 'input', function () {
                    runFind(findInput.value);
                });
                listen(findInput, 'keydown', function (ev) {
                    if (ev.key === 'ArrowDown') {
                        ev.preventDefault();
                        if (findHits.length) {
                            findIndex = Math.min(findHits.length - 1, findIndex + 1);
                            renderFindResults();
                        }
                    } else if (ev.key === 'ArrowUp') {
                        ev.preventDefault();
                        if (findHits.length) {
                            findIndex = Math.max(0, findIndex - 1);
                            renderFindResults();
                        }
                    } else if (ev.key === 'Enter') {
                        ev.preventDefault();
                        if (findIndex >= 0 && findHits[findIndex]) chooseFindHit(findHits[findIndex].id);
                    } else if (ev.key === 'Escape') {
                        findInput.value = '';
                        runFind('');
                    }
                });
            }
            const filterRoot = liveDom.querySelectorAll ? liveDom : host;
            const filterEls =
                filterRoot && filterRoot.querySelectorAll
                    ? filterRoot.querySelectorAll('[data-graph-filter]')
                    : [];
            Array.prototype.forEach.call(filterEls, function (el) {
                listen(el, 'change', function () {
                    const key = el.getAttribute('data-graph-filter');
                    if (!key) return;
                    if (key === 'people') {
                        void reloadGraph(!!el.checked);
                        return;
                    }
                    filters[key] = !!el.checked;
                    const vis = applyGraphFilters(liveCy, snapshot, filters);
                    const selectedNodeStillVisible =
                        !selectedId ||
                        vis.nodes.some(function (n) {
                            return n.id === selectedId;
                        });
                    const selectedEdgeStillVisible =
                        !selectedEdgeId ||
                        vis.edges.some(function (e) {
                            return e.id === selectedEdgeId;
                        });
                    if (!selectedNodeStillVisible || !selectedEdgeStillVisible) {
                        clearGraphSelection();
                    } else if (selectedId || selectedEdgeId) {
                        applySelectionContext(liveCy);
                    }
                });
            });
        }

        function shellHtml(opts) {
            const derivedOff = opts && opts.derivedOff;
            const tooLarge = opts && opts.tooLarge;
            const loadError = opts && opts.loadError;
            function chk(key, label, on) {
                return (
                    '<label class="prks-filter-toggle"><input type="checkbox" data-graph-filter="' +
                    key +
                    '"' +
                    (on ? ' checked' : '') +
                    '> <span>' +
                    esc(label) +
                    '</span></label>'
                );
            }
            let body = '';
            if (tooLarge) {
                body =
                    '<p class="prks-inline-message" role="status">This graph is too large to render as a single snapshot.</p>';
            } else if (opts && opts.offlineUnavailable) {
                body = '<div class="prks-inline-message" data-prks-role="offline-unavailable" role="status">' +
                    '<p>' + (includePeople ? 'Research Graph variant unavailable offline' : 'Research Graph not available offline') + '</p>' +
                    '<p>Open the Research Graph while connected to cache this snapshot.</p></div>';
            } else if (loadError) {
                body = '<p class="prks-inline-message" role="status">Could not load Research Graph.</p>';
            } else {
                body =
                    '<div class="research-graph__stage">' +
                    '<div class="prks-panel research-graph__canvas-wrap"><div class="research-graph__canvas" data-prks-role="graph-canvas" role="img" aria-label="Research relationship graph"></div></div>' +
                    '</div>' +
                    (derivedOff
                        ? '<p class="meta-row" role="status">Note-mention edges unavailable. Canonical relationships still shown.</p>'
                        : '');
            }
            function legendIcon(name, kind) {
                const icon = typeof root.prksIcon === 'function' ? root.prksIcon(name, { size: 'sm' }) : '';
                return (
                    '<span class="research-graph__legend-icon research-graph__legend-icon--' +
                    kind +
                    '" aria-hidden="true">' +
                    icon +
                    '</span>'
                );
            }
            const findId = chromeId('graph-find');
            const resultsId = chromeId('graph-find-results');
            const filtersPanelId = chromeId('graph-filters-panel');
            const legendPanelId = chromeId('graph-legend-panel');
            const filtersHtml =
                '<div class="research-graph__filters">' +
                '<span class="research-graph__filter-group">Nodes</span>' +
                chk('concepts', 'Concepts', filters.concepts) +
                chk('positions', 'Positions', filters.positions) +
                chk('arguments', 'Arguments', filters.arguments) +
                chk('works', 'Works', filters.works) +
                chk('people', 'People', includePeople) +
                '</div>' +
                '<div class="research-graph__filters">' +
                '<span class="research-graph__filter-group">Relations</span>' +
                chk('hierarchy', 'Hierarchy', filters.hierarchy) +
                chk('responds', 'Responses', filters.responds) +
                chk('sources', 'Sources', filters.sources) +
                chk('mentions', 'Note mentions', filters.mentions) +
                '</div>';
            const legendHtml =
                '<div class="research-graph__legend-group"><span class="research-graph__filter-group">Nodes</span>' +
                '<ul>' +
                '<li>' +
                legendIcon('network', 'concept') +
                ' Concept</li>' +
                '<li>' +
                legendIcon('flag', 'position') +
                ' Position</li>' +
                '<li>' +
                legendIcon('messages-square', 'argument') +
                ' Argument</li>' +
                '<li>' +
                legendIcon('messages-square', 'stance') +
                ' Stance</li>' +
                '<li>' +
                legendIcon('file-text', 'work') +
                ' Work</li>' +
                '<li>' +
                legendIcon('user', 'person') +
                ' Person</li>' +
                '</ul></div>' +
                '<div class="research-graph__legend-group"><span class="research-graph__filter-group">Relations</span>' +
                '<ul>' +
                '<li><span class="research-graph__line research-graph__line--hierarchy"></span> Hierarchy</li>' +
                '<li><span class="research-graph__line research-graph__line--responds"></span> Response</li>' +
                '<li><span class="research-graph__line research-graph__line--source"></span> Source</li>' +
                '<li><span class="research-graph__line research-graph__line--mentions"></span> Note mention</li>' +
                '<li><span class="research-graph__line research-graph__line--author"></span> Author</li>' +
                '</ul></div>';
            return (
                '<div class="research-graph">' +
                '<div class="prks-page-header page-header"><div class="page-header__title-row"><h2 class="prks-page-title">' +
                (typeof root.prksPageHeaderIconHtml === 'function'
                    ? root.prksPageHeaderIconHtml('share-2')
                    : '') +
                ' Research Graph</h2></div></div>' +
                '<div class="prks-toolbar research-graph__toolbar">' +
                '<label class="research-graph__find-label"' +
                (findId ? ' for="' + esc(findId) + '"' : '') +
                '>Find node</label>' +
                '<input' +
                (findId ? ' id="' + esc(findId) + '"' : '') +
                ' class="prks-input" type="search" autocomplete="off" placeholder="Find node…" data-prks-role="graph-find"' +
                (resultsId ? ' aria-controls="' + esc(resultsId) + '"' : '') +
                '>' +
                '<button type="button" class="prks-btn prks-btn--secondary" data-prks-role="graph-fit">Fit</button>' +
                '<button type="button" class="prks-btn prks-btn--secondary" data-prks-role="graph-reset">Reset layout</button>' +
                '<button type="button" class="prks-btn prks-btn--secondary" data-prks-role="graph-filters-toggle" aria-expanded="false"' +
                (filtersPanelId ? ' aria-controls="' + esc(filtersPanelId) + '"' : '') +
                '>Filters</button>' +
                '<button type="button" class="prks-btn prks-btn--secondary" data-prks-role="graph-legend-toggle" aria-expanded="false"' +
                (legendPanelId ? ' aria-controls="' + esc(legendPanelId) + '"' : '') +
                '>Legend</button>' +
                '</div>' +
                '<div' +
                (resultsId ? ' id="' + esc(resultsId) + '"' : '') +
                ' class="research-graph__find-results" role="listbox" hidden data-prks-role="graph-find-results"></div>' +
                '<div class="research-graph__status prks-inline-message" role="status" data-prks-role="graph-status"' +
                (statusMessage ? '' : ' hidden') +
                '>' +
                esc(statusMessage) +
                '</div>' +
                '<div' +
                (filtersPanelId ? ' id="' + esc(filtersPanelId) + '"' : '') +
                ' class="research-graph__aux-panel research-graph__filters-panel" data-prks-role="graph-filters-panel" hidden>' +
                filtersHtml +
                '</div>' +
                '<div' +
                (legendPanelId ? ' id="' + esc(legendPanelId) + '"' : '') +
                ' class="research-graph__aux-panel research-graph__legend" data-prks-role="graph-legend-panel" aria-label="Graph legend" hidden>' +
                legendHtml +
                '</div>' +
                body +
                '</div>'
            );
        }

        function applyFocusAfterLayout() {
            const focus = pendingFocus;
            pendingFocus = '';
            if (!focus) {
                renderInspector();
                return;
            }
            const node = nodeById(snapshot, focus);
            if (!node) {
                if (!statusMessage) statusMessage = 'Requested node is not present in this graph.';
                renderStatusMessage();
                renderInspector();
                return;
            }
            // Layout may finish after a failed variant request. Keep its status
            // while applying the original focus to the still-visible snapshot.
            renderStatusMessage();
            selectNode(focus);
        }

        function mountCytoscape(host) {
            const canvas = queryGraphRole(host, 'graph-canvas') || queryRole('graph-canvas');
            if (!canvas || typeof root.cytoscape !== 'function') return;
            teardownCy();
            liveDom = host && host.querySelector ? host.querySelector('.research-graph') : liveDom;
            liveCy = root.cytoscape({
                container: canvas,
                elements: toCytoscapeElements(snapshot),
                style: cytoscapeStyle(),
                layout: { name: 'preset' },
                wheelSensitivity: 0.35,
                minZoom: 0.15,
                maxZoom: 3,
                boxSelectionEnabled: false,
                autoungrabify: false,
                autounselectify: false,
            });
            root.__prksResearchGraphLiveCount = 1;
            liveCy.on('tap', 'node', function (evt) {
                const id = evt.target.id();
                selectNode(id, { center: false });
            });
            liveCy.on('tap', 'edge', function (evt) {
                selectEdge(evt.target.id());
            });
            liveCy.on('tap', function (evt) {
                if (evt.target === liveCy) {
                    clearGraphSelection();
                }
            });
            liveCy.on('mouseover', 'edge', function (evt) {
                hoverEdgeId = evt.target.id();
                updateEdgeLabels(liveCy);
            });
            liveCy.on('mouseout', 'edge', function () {
                hoverEdgeId = '';
                updateEdgeLabels(liveCy);
            });
            liveCy.on('dbltap', 'node', function (evt) {
                openSelectedRecord(evt.target.id());
            });
            applyGraphFilters(liveCy, snapshot, filters);
            pendingFocus = pendingFocus || '';
            const layout = liveCy.layout(coseLayoutOptions(true));
            layout.one('layoutstop', function () {
                applyFocusAfterLayout();
            });
            layout.run();
            if (!liveCy.nodes().length) applyFocusAfterLayout();
        }

        function syncPeopleCheckbox() {
            const el = queryRole('graph-filter-people') || (liveDom && liveDom.querySelector
                ? liveDom.querySelector('[data-graph-filter="people"]')
                : null);
            if (el) el.checked = !!includePeople;
        }

        function refreshHost(host, htmlOpts) {
            if (!host) return;
            unbindAll();
            host.innerHTML = shellHtml(htmlOpts || {});
            bindShell(host);
            syncPeopleCheckbox();
            if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(host);
        }

        async function loadSnapshot(people, signal) {
            const result = typeof options.loadSnapshot === 'function'
                ? await options.loadSnapshot(people, signal)
                : { snapshot: await root.fetchResearchGraph({ people: people, signal: signal }), source: 'server', cachedAt: null };
            if (result.source === 'unavailable') {
                const err = new Error('Research Graph not available offline');
                err.code = 'graph_offline_unavailable';
                throw err;
            }
            return result;
        }

        function applyProvenance(result) {
            if (typeof options.onSnapshot === 'function') options.onSnapshot(result);
        }

        async function reloadGraph(nextPeople) {
            if (destroyed || !liveDom) return false;
            const wantPeople = nextPeople === undefined ? includePeople : !!nextPeople;
            const prevPeople = includePeople;
            const keep = selectedId;
            const originDom = liveDom;
            const host = originDom.parentNode;
            const gen = (reloadGeneration += 1);
            try {
                const result = await loadSnapshot(wantPeople, graphRouteSignal);
                const data = result.snapshot;
                if (graphReloadIsStale(gen, originDom)) return false;
                includePeople = wantPeople;
                filters.people = wantPeople;
                snapshot = data;
                statusMessage = '';
                if (keep && nodeById(snapshot, keep)) pendingFocus = keep;
                else pendingFocus = '';
                teardownCy();
                unbindAll();
                liveDom = null;
                if (!host) return false;
                host.innerHTML = shellHtml({
                    derivedOff: data.meta && data.meta.derived_note_edges_available === false,
                });
                bindShell(host);
                syncPeopleCheckbox();
                mountCytoscape(host);
                applyProvenance(result);
                if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(host);
                return true;
            } catch (e) {
                if (graphReloadIsStale(gen, originDom)) return false;
                if (typeof root.prksIsAbortError === 'function' && root.prksIsAbortError(e)) return false;
                includePeople = prevPeople;
                filters.people = prevPeople;
                syncPeopleCheckbox();
                statusMessage = e && e.code === 'graph_offline_unavailable'
                    ? (wantPeople ? 'People-inclusive graph' : 'Core graph') + ' is not available offline on this device.'
                    : reloadGraphFailureMessage(e);
                renderStatusMessage();
                return false;
            }
        }

        async function start() {
            if (destroyed) return;
            const focus = String(options.focus || '');
            includePeople = peopleRequiredForFocus(focus);
            filters = Object.assign({}, DEFAULT_FILTERS);
            filters.people = includePeople;
            pendingFocus = focus;
            statusMessage = '';
            selectedId = '';
            selectedEdgeId = '';
            hoverEdgeId = '';
            findQuery = '';
            findHits = [];
            findIndex = -1;
            graphRouteSignal = options.signal || graphRouteSignal;
            if (!container) return;
            const gen = (reloadGeneration += 1);
            const originHost = container;
            container.innerHTML = shellHtml({});
            bindShell(container);
            try {
                const result = await loadSnapshot(includePeople, options.signal);
                const data = result.snapshot;
                if (destroyed || gen !== reloadGeneration) return;
                if (options.stale && options.stale()) return;
                snapshot = data;
                refreshHost(originHost, {
                    derivedOff: data.meta && data.meta.derived_note_edges_available === false,
                });
                mountCytoscape(originHost);
                applyProvenance(result);
            } catch (e) {
                if (destroyed || gen !== reloadGeneration) return;
                if (options.stale && options.stale()) return;
                if (typeof root.prksIsAbortError === 'function' && root.prksIsAbortError(e)) return;
                const tooLarge = e && e.code === 'graph_too_large';
                refreshHost(originHost, { tooLarge: tooLarge, loadError: !tooLarge, offlineUnavailable: e && e.code === 'graph_offline_unavailable' });
            }
        }

        const runtime = {
            start: start,
            reload: reloadGraph,
            fit: fit,
            resetLayout: rerunLayout,
            destroy: destroy,
            debug: debug,
            selectNode: selectNode,
            selectEdge: selectEdge,
            clearSelection: clearGraphSelection,
            renderInspector: renderInspector,
            openSelectedRecord: openSelectedRecord,
            getSelectedId: function () {
                return selectedId;
            },
            getSelectedEdgeId: function () {
                return selectedEdgeId;
            },
            hasSelection: function () {
                return !!(selectedId || selectedEdgeId);
            },
        };
        return runtime;
    }

    function renderResearchGraph(container, opts) {
        const options = opts || {};
        const ctx = resolveGraphCtx(options);
        if (container && container.__prksGraphRuntime && typeof container.__prksGraphRuntime.destroy === 'function') {
            container.__prksGraphRuntime.destroy();
        }
        const runtime = createResearchGraphRuntime(ctx, container, options);
        if (ctx && typeof ctx.setResource === 'function') {
            ctx.setResource('researchGraph', runtime, function () {
                runtime.destroy();
            });
        }
        if (container) container.__prksGraphRuntime = runtime;
        return runtime.start();
    }

    function destroyResearchGraph(container) {
        if (container && container.__prksGraphRuntime && typeof container.__prksGraphRuntime.destroy === 'function') {
            container.__prksGraphRuntime.destroy();
            return;
        }
        const ctx = typeof root.prksGetFocusedTabContext === 'function' ? root.prksGetFocusedTabContext() : null;
        if (ctx && typeof ctx.getResource === 'function' && ctx.getResource('researchGraph')) {
            if (typeof ctx.clearResource === 'function') ctx.clearResource('researchGraph');
            else ctx.getResource('researchGraph').destroy();
        }
    }

    function prksGetResearchGraphDebug(tabId) {
        const ctx = tabId && root.prksGetTabContext
            ? root.prksGetTabContext(tabId)
            : root.prksGetFocusedTabContext && root.prksGetFocusedTabContext();
        const rt = ctx && ctx.getResource && ctx.getResource('researchGraph');
        if (rt && rt.debug) return rt.debug();
        return null;
    }

    function openSelectedRecord(nodeOrId) {
        if (typeof nodeOrId !== 'string') return navigateToNode(nodeOrId);
        const rt = resolveActiveRuntime();
        if (rt && typeof rt.openSelectedRecord === 'function') return rt.openSelectedRecord(nodeOrId);
        return false;
    }

    function reloadGraph(nextPeople) {
        const rt = resolveActiveRuntime();
        if (rt && typeof rt.reload === 'function') return rt.reload(nextPeople);
        return Promise.resolve(false);
    }

    function selectGraphNode(id, opts) {
        const rt = resolveActiveRuntime();
        if (rt && typeof rt.selectNode === 'function') return rt.selectNode(id, opts);
        return '';
    }

    function selectGraphEdge(id) {
        const rt = resolveActiveRuntime();
        if (rt && typeof rt.selectEdge === 'function') return rt.selectEdge(id);
        return '';
    }

    function clearGraphSelection() {
        const rt = resolveActiveRuntime();
        if (rt && typeof rt.clearSelection === 'function') return rt.clearSelection();
    }

    function renderGraphInspector() {
        const rt = resolveActiveRuntime();
        if (rt && typeof rt.renderInspector === 'function') return rt.renderInspector();
        return null;
    }

    function researchGraphHasInspectorSelection() {
        const rt = resolveActiveRuntime();
        return !!(rt && typeof rt.hasSelection === 'function' && rt.hasSelection());
    }

    const api = {
        renderResearchGraph: renderResearchGraph,
        destroyResearchGraph: destroyResearchGraph,
        createResearchGraphRuntime: createResearchGraphRuntime,
        prksGetResearchGraphDebug: prksGetResearchGraphDebug,
        reloadGraph: reloadGraph,
        peopleRequiredForFocus: peopleRequiredForFocus,
        prksResearchGraphHasInspectorSelection: researchGraphHasInspectorSelection,
        getSelectedGraphNodeId: function () {
            const rt = resolveActiveRuntime();
            return rt && typeof rt.getSelectedId === 'function' ? rt.getSelectedId() : '';
        },
        getSelectedGraphEdgeId: function () {
            const rt = resolveActiveRuntime();
            return rt && typeof rt.getSelectedEdgeId === 'function' ? rt.getSelectedEdgeId() : '';
        },
        toCytoscapeElements: toCytoscapeElements,
        displayLabelForEdge: displayLabelForEdge,
        canvasLabelForEdge: canvasLabelForEdge,
        inspectorLabelForEdge: inspectorLabelForEdge,
        cytoscapeStyle: cytoscapeStyle,
        edgeShouldShowCanvasLabel: edgeShouldShowCanvasLabel,
        selectionContextIds: selectionContextIds,
        inspectorEdgeModel: inspectorEdgeModel,
        nodeClasses: nodeClasses,
        edgeClasses: edgeClasses,
        visibleGraph: visibleGraph,
        applyGraphFilters: applyGraphFilters,
        findNodesByLabel: findNodesByLabel,
        inspectorModel: inspectorModel,
        neighborGroups: neighborGroups,
        openSelectedRecord: openSelectedRecord,
        selectGraphNode: selectGraphNode,
        selectGraphEdge: selectGraphEdge,
        clearGraphSelection: clearGraphSelection,
        renderGraphInspector: renderGraphInspector,
        defaultGraphFilters: function () {
            return Object.assign({}, DEFAULT_FILTERS);
        },
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : typeof global !== 'undefined' ? global : this);
