/**
 * Research Graph: read-only Cytoscape projection of canonical research relations.
 * Graph state is never persisted. Edits happen on canonical record pages.
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
    let graphRouteSignal = null;

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

    function openSelectedRecord(nodeOrId) {
        const node = typeof nodeOrId === 'string' ? nodeById(snapshot, nodeOrId) : nodeOrId;
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
                    'border-width': 3,
                    'border-color': accent,
                    'background-color': accentSoft,
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

    function graphReloadIsStale(gen, originDom) {
        return gen !== reloadGeneration || liveDom !== originDom;
    }

    function destroyResearchGraph() {
        reloadGeneration += 1;
        if (boundKeyHandler && liveDom) {
            liveDom.removeEventListener('keydown', boundKeyHandler);
            boundKeyHandler = null;
        }
        if (liveCy) {
            try {
                liveCy.destroy();
            } catch (_e) {}
            liveCy = null;
        }
        liveDom = null;
        root.__prksResearchGraphLiveCount = 0;
        root.__prksResearchGraphCy = null;
    }

    function updateEdgeLabels(cy) {
        if (!cy) return;
        const ctx = {
            hoverEdgeId: hoverEdgeId,
            selectedEdgeId: selectedEdgeId,
        };
        cy.edges().forEach(function (e) {
            if (edgeShouldShowCanvasLabel(e.id(), ctx)) e.addClass('graph-edge--label-on');
            else e.removeClass('graph-edge--label-on');
        });
    }

    function applySelectionContext(cy) {
        if (!cy) return;
        const hasFocus = !!(selectedId || selectedEdgeId);
        const ctx = selectionContextIds(snapshot, selectedId, selectedEdgeId);
        cy.batch(function () {
            cy.nodes().forEach(function (n) {
                const keep = !hasFocus || ctx.nodeIds[n.id()];
                if (keep) n.removeClass('graph-dim');
                else n.addClass('graph-dim');
                if (selectedId && n.id() === selectedId) n.addClass('graph-node--selected');
                else n.removeClass('graph-node--selected');
            });
            cy.edges().forEach(function (e) {
                const keep = !hasFocus || ctx.edgeIds[e.id()];
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
    }

    function inspectorEl() {
        if (typeof document !== 'undefined' && document.getElementById) {
            const panel = document.getElementById('prks-graph-inspector');
            if (panel) return panel;
        }
        if (liveDom && liveDom.querySelector) return liveDom.querySelector('#prks-graph-inspector');
        return null;
    }

    function inspectorClick(ev) {
        const t = ev.target;
        if (!t || !t.closest) return;
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
        el.addEventListener('click', inspectorClick);
    }

    function paintInspector(html) {
        const el = inspectorEl();
        if (!el) return;
        el.innerHTML = html;
        ensureInspectorBound(el);
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

    function inspectorNeighborHtml(item) {
        return (
            '<button type="button" class="prks-list-row research-graph__neighbor" data-graph-node="' +
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

    function renderInspector() {
        const edgeModel = selectedEdgeId ? inspectorEdgeModel(snapshot, selectedEdgeId) : null;
        const model = !edgeModel ? inspectorModel(snapshot, selectedId) : null;
        let html = '';
        if (statusMessage) {
            html += '<p class="prks-inline-message" role="status">' + esc(statusMessage) + '</p>';
        }
        if (edgeModel) {
            html +=
                '<div class="doc-meta-card">' +
                '<p class="saved-view-detail__kicker">' +
                esc(edgeModel.kicker) +
                '</p><p class="card-title" id="prks-graph-inspector-title">' +
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
            html += '</div></div>';
            paintInspector(html);
            return edgeModel;
        }
        if (!model) {
            html +=
                '<div class="doc-meta-card">' +
                '<p class="saved-view-detail__kicker">Selection</p>' +
                '<p class="meta-row">Select a node or edge. Relationships stay on the canvas as highlights, not mass labels.</p>' +
                '</div>';
            paintInspector(html);
            return model;
        }
        html +=
            '<div class="doc-meta-card">' +
            '<p class="saved-view-detail__kicker">' +
            esc(model.typeLabel) +
            '</p><p class="card-title" id="prks-graph-inspector-title">' +
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
        html += '</div>';
        paintInspector(html);
        return model;
    }

    function renderFindResults() {
        if (!liveDom) return;
        const box = liveDom.querySelector('#prks-graph-find-results');
        if (!box) return;
        if (!findQuery || !findHits.length) {
            box.innerHTML = findQuery
                ? '<p class="meta-row">No matching nodes.</p>'
                : '';
            box.hidden = !findQuery;
            return;
        }
        box.hidden = false;
        box.innerHTML = findHits
            .map(function (n, i) {
                return (
                    '<button type="button" class="prks-list-row research-graph__find-hit' + (i === findIndex ? ' is-active' : '') + '" role="option" aria-selected="' +
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
            renderInspector();
            return;
        }
        statusMessage = '';
        selectNode(id);
    }

    function bindShell(container) {
        liveDom = container.querySelector('.research-graph');
        if (!liveDom) return;
        liveDom.addEventListener('click', function (ev) {
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
            if (t.id === 'prks-graph-fit') {
                if (liveCy) liveCy.fit(undefined, 40);
                return;
            }
            if (t.id === 'prks-graph-reset') {
                rerunLayout();
                return;
            }
        });
        const findInput = liveDom.querySelector('#prks-graph-find');
        if (findInput) {
            findInput.addEventListener('input', function () {
                runFind(findInput.value);
            });
            findInput.addEventListener('keydown', function (ev) {
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
        liveDom.querySelectorAll('[data-graph-filter]').forEach(function (el) {
            el.addEventListener('change', function () {
                const key = el.getAttribute('data-graph-filter');
                if (!key) return;
                if (key === 'people') {
                    void reloadGraph(!!el.checked);
                    return;
                }
                filters[key] = !!el.checked;
                applyGraphFilters(liveCy, snapshot, filters);
                if (selectedId) {
                    const vis = visibleGraph(snapshot, filters);
                    const still = vis.nodes.some(function (n) {
                        return n.id === selectedId;
                    });
                    if (!still) {
                        selectedId = '';
                        selectedEdgeId = '';
                        if (liveCy) liveCy.elements().unselect();
                        applySelectionContext(liveCy);
                        renderInspector();
                    } else {
                        applySelectionContext(liveCy);
                    }
                } else if (selectedEdgeId) {
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
        } else if (loadError) {
            body = '<p class="prks-inline-message" role="status">Could not load Research Graph.</p>';
        } else {
            body =
                '<div class="research-graph__stage">' +
                '<div class="prks-panel research-graph__canvas-wrap"><div class="research-graph__canvas" id="prks-graph-canvas" role="img" aria-label="Research relationship graph"></div></div>' +
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
        return (
            '<div class="research-graph">' +
            '<div class="prks-page-header page-header"><div class="page-header__title-row"><h2 class="prks-page-title">' +
            (typeof root.prksPageHeaderIconHtml === 'function'
                ? root.prksPageHeaderIconHtml('share-2')
                : '') +
            ' Research Graph</h2></div></div>' +
            '<div class="prks-toolbar research-graph__toolbar">' +
            '<label class="research-graph__find-label" for="prks-graph-find">Find node</label>' +
            '<input id="prks-graph-find" class="prks-input" type="search" autocomplete="off" placeholder="Find node…">' +
            '<button type="button" class="prks-btn prks-btn--secondary" id="prks-graph-fit">Fit</button>' +
            '<button type="button" class="prks-btn prks-btn--secondary" id="prks-graph-reset">Reset layout</button>' +
            '</div>' +
            '<div id="prks-graph-find-results" class="research-graph__find-results" role="listbox" hidden></div>' +
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
            '</div>' +
            '<div class="research-graph__legend" aria-label="Graph legend">' +
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
            '</ul></div>' +
            '</div>' +
            body +
            '</div>'
        );
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

    function rerunLayout() {
        if (!liveCy) return;
        const layout = liveCy.layout(coseLayoutOptions(true));
        layout.run();
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
            statusMessage = 'Requested node is not present in this graph.';
            renderInspector();
            return;
        }
        statusMessage = '';
        selectNode(focus);
    }

    function mountCytoscape(container) {
        const canvas = container.querySelector('#prks-graph-canvas');
        if (!canvas || typeof root.cytoscape !== 'function') return;
        destroyResearchGraph();
        liveDom = container.querySelector('.research-graph');
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
        root.__prksResearchGraphCy = liveCy;
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
        if (!liveDom || !liveDom.querySelector) return;
        const el = liveDom.querySelector('[data-graph-filter="people"]');
        if (el) el.checked = !!includePeople;
    }

    function reloadGraphFailureMessage(err) {
        if (err && err.code === 'graph_too_large') {
            return 'Graph is too large to render as a single snapshot.';
        }
        return 'Could not load Research Graph.';
    }

    async function reloadGraph(nextPeople) {
        if (!liveDom) return false;
        const wantPeople = nextPeople === undefined ? includePeople : !!nextPeople;
        const prevPeople = includePeople;
        const keep = selectedId;
        const originDom = liveDom;
        const host = originDom.parentNode;
        const gen = (reloadGeneration += 1);
        try {
            const data = await root.fetchResearchGraph({ people: wantPeople, signal: graphRouteSignal });
            if (graphReloadIsStale(gen, originDom)) return false;
            includePeople = wantPeople;
            filters.people = wantPeople;
            snapshot = data;
            statusMessage = '';
            if (keep && nodeById(snapshot, keep)) pendingFocus = keep;
            else pendingFocus = '';
            destroyResearchGraph();
            if (!host) return false;
            host.innerHTML = shellHtml({
                derivedOff: data.meta && data.meta.derived_note_edges_available === false,
            });
            bindShell(host);
            syncPeopleCheckbox();
            mountCytoscape(host);
            if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(host);
            return true;
        } catch (e) {
            if (graphReloadIsStale(gen, originDom)) return false;
            if (typeof root.prksIsAbortError === 'function' && root.prksIsAbortError(e)) return false;
            includePeople = prevPeople;
            filters.people = prevPeople;
            syncPeopleCheckbox();
            statusMessage = reloadGraphFailureMessage(e);
            renderInspector();
            return false;
        }
    }

    async function renderResearchGraph(container, opts) {
        destroyResearchGraph();
        const options = opts || {};
        graphRouteSignal = options.signal || null;
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
        if (!container) return;
        container.innerHTML = shellHtml({});
        try {
            const data = await (typeof root.fetchResearchGraph === 'function'
                ? root.fetchResearchGraph({ people: includePeople, signal: options.signal })
                : Promise.resolve({ nodes: [], edges: [], meta: {} }));
            if (options.stale && options.stale()) return;
            snapshot = data;
            container.innerHTML = shellHtml({
                derivedOff: data.meta && data.meta.derived_note_edges_available === false,
            });
            bindShell(container);
            syncPeopleCheckbox();
            mountCytoscape(container);
            if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(container);
        } catch (e) {
            if (options.stale && options.stale()) return;
            if (typeof root.prksIsAbortError === 'function' && root.prksIsAbortError(e)) return;
            const tooLarge = e && e.code === 'graph_too_large';
            container.innerHTML = shellHtml({ tooLarge: tooLarge, loadError: !tooLarge });
            bindShell(container);
            syncPeopleCheckbox();
            if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(container);
        }
    }

    const api = {
        renderResearchGraph: renderResearchGraph,
        destroyResearchGraph: destroyResearchGraph,
        reloadGraph: reloadGraph,
        peopleRequiredForFocus: peopleRequiredForFocus,
        getSelectedGraphNodeId: function () {
            return selectedId;
        },
        getSelectedGraphEdgeId: function () {
            return selectedEdgeId;
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
        selectGraphNode: selectNode,
        selectGraphEdge: selectEdge,
        clearGraphSelection: clearGraphSelection,
        renderGraphInspector: renderInspector,
        defaultGraphFilters: function () {
            return Object.assign({}, DEFAULT_FILTERS);
        },
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : typeof global !== 'undefined' ? global : this);
