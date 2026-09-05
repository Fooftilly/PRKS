/**
 * Arguments & Stances: Hypernomicon-inspired Responds to / Where made / Counter-response.
 */
(function (root) {
    'use strict';

    function esc(s) {
        if (typeof root.prksEscapeHtml === 'function') return root.prksEscapeHtml(s);
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function md(text) {
        if (typeof root.prksResearchMarkdownHtml === 'function') return root.prksResearchMarkdownHtml(text);
        return '<p>' + esc(text || '') + '</p>';
    }

    function sectionHead(title, opts) {
        if (typeof root.prksResearchSectionHeadHtml === 'function') return root.prksResearchSectionHeadHtml(title, opts);
        return '<h3 id="' + esc((opts && opts.headingId) || '') + '">' + esc(title) + '</h3>';
    }

    function argumentRowHtml(a, iconArg) {
        const rowHtml =
            typeof root.prksResearchIndexRowHtml === 'function' ? root.prksResearchIndexRowHtml : null;
        const responses = Number(a.response_count) || 0;
        const targets = Array.isArray(a.targets) ? a.targets.length : 0;
        const sources = Array.isArray(a.sources) ? a.sources.length : 0;
        const kindLabel = a.kind === 'stance' ? 'Stance' : 'Argument';
        return rowHtml
            ? rowHtml({
                  href: '#/arguments/' + encodeURIComponent(a.id),
                  icon: iconArg,
                  title: esc(a.name || a.id),
                  kind: esc(kindLabel),
                  meta: [
                      String(responses) + (responses === 1 ? ' response' : ' responses'),
                      String(targets) + (targets === 1 ? ' target' : ' targets'),
                      String(sources) + (sources === 1 ? ' source' : ' sources'),
                  ],
              })
            : '<a class="prks-list-row prks-research-row" href="#/arguments/' +
              encodeURIComponent(a.id) +
              '">' +
              esc(a.name || a.id) +
              '</a>';
    }

    function matchArgument(a, q) {
        if (String(a.name || '').toLowerCase().indexOf(q) >= 0) return true;
        if (String(a.kind || '').toLowerCase().indexOf(q) >= 0) return true;
        const targets = Array.isArray(a.targets) ? a.targets : [];
        for (let i = 0; i < targets.length; i++) {
            if (String((targets[i] && targets[i].name) || '').toLowerCase().indexOf(q) >= 0) return true;
        }
        const sources = Array.isArray(a.sources) ? a.sources : [];
        for (let j = 0; j < sources.length; j++) {
            if (String((sources[j] && sources[j].work_title) || '').toLowerCase().indexOf(q) >= 0) return true;
        }
        return false;
    }

    function argumentsEmptyDataHtml() {
        return (
            '<div class="prks-research-index__empty">' +
            '<p class="meta-row">No Arguments or Stances yet.</p>' +
            '<p><button type="button" class="prks-btn prks-btn--secondary" id="prks-argument-new-empty">New Argument</button> ' +
            '<button type="button" class="prks-btn prks-btn--secondary" id="prks-stance-new-empty">New Stance</button></p>' +
            '</div>'
        );
    }

    function renderArgumentsIndex(items, container, filterKind) {
        const list = Array.isArray(items) ? items : [];
        const kind = filterKind || 'all';
        const iconArg =
            typeof root.prksIcon === 'function' ? root.prksIcon('messages-square', { size: 'sm' }) : '';
        function btn(k, label) {
            const on = kind === k;
            return (
                '<button type="button" class="prks-tab' +
                (on ? ' is-active' : '') +
                '" aria-selected="' +
                (on ? 'true' : 'false') +
                '" data-arg-filter="' +
                k +
                '">' +
                label +
                '</button>'
            );
        }

        function make(kindName) {
            return function () {
                void (async function () {
                    const name = await promptArgumentName(kindName);
                    if (!name) return;
                    const created = await root.createArgument({
                        name: name,
                        kind: kindName,
                    });
                    if (created && created.id && typeof root.prksNavigate === 'function') {
                        root.prksNavigate('#/arguments/' + encodeURIComponent(created.id));
                    }
                })();
            };
        }

        function renderRows(filtered, query) {
            const host = container.querySelector('#prks-argument-rows');
            if (!host) return;
            host.innerHTML = !filtered.length
                ? query && typeof root.prksResearchIndexSearchEmptyHtml === 'function'
                    ? root.prksResearchIndexSearchEmptyHtml('Arguments or Stances', query)
                    : argumentsEmptyDataHtml()
                : filtered
                      .map(function (a) {
                          return argumentRowHtml(a, iconArg);
                      })
                      .join('');
            if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(host);
            if (!filtered.length && !query) {
                const na = host.querySelector('#prks-argument-new-empty');
                const ns = host.querySelector('#prks-stance-new-empty');
                if (na) na.addEventListener('click', make('argument'));
                if (ns) ns.addEventListener('click', make('stance'));
            }
        }

        container.innerHTML =
            '<div class="prks-page-header page-header"><div class="page-header__title-row"><h2 class="prks-page-title">' +
            (typeof root.prksPageHeaderIconHtml === 'function' ? root.prksPageHeaderIconHtml('messages-square') : '') +
            ' Arguments &amp; Stances</h2>' +
            '<div class="page-header__actions">' +
            '<button type="button" class="prks-btn prks-btn--secondary" id="prks-argument-new">New Argument</button>' +
            '<button type="button" class="prks-btn prks-btn--secondary" id="prks-stance-new">New Stance</button>' +
            '</div></div></div>' +
            '<div class="prks-tabs" role="tablist" aria-label="Argument kind">' +
            btn('all', 'All') +
            btn('argument', 'Arguments') +
            btn('stance', 'Stances') +
            '</div>' +
            (list.length && typeof root.prksResearchIndexToolbarHtml === 'function'
                ? root.prksResearchIndexToolbarHtml('prks-argument-search', 'Search arguments and stances…')
                : '') +
            '<div class="list-view prks-research-index" id="prks-argument-rows"></div>';
        container.querySelectorAll('[data-arg-filter]').forEach(function (el) {
            el.addEventListener('click', function () {
                const k = el.getAttribute('data-arg-filter');
                if (typeof root.prksNavigate === 'function') {
                    root.prksNavigate(k === 'all' ? '#/arguments' : '#/arguments?kind=' + encodeURIComponent(k));
                }
            });
        });
        const na = container.querySelector('#prks-argument-new');
        const ns = container.querySelector('#prks-stance-new');
        if (na) na.addEventListener('click', make('argument'));
        if (ns) ns.addEventListener('click', make('stance'));
        renderRows(list, '');
        if (list.length && typeof root.prksBindResearchIndexSearch === 'function') {
            root.prksBindResearchIndexSearch(container, {
                inputSelector: '#prks-argument-search',
                items: list,
                matchFn: matchArgument,
                renderRows: renderRows,
            });
        }
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(container);
    }

    function renderArgumentNotFound(container) {
        container.innerHTML =
            '<div class="prks-page-header page-header"><h2 class="prks-page-title">Argument not found.</h2></div>' +
            '<p class="meta-row"><a class="prks-btn prks-btn--secondary" href="#/arguments">Back to Arguments &amp; Stances</a></p>';
    }

    function researchLinkRow(href, title, kind, meta) {
        const rowHtml =
            typeof root.prksResearchIndexRowHtml === 'function' ? root.prksResearchIndexRowHtml : null;
        const metaItems = (meta || []).filter(Boolean);
        if (rowHtml) {
            return rowHtml({
                href: href,
                title: esc(title),
                kind: kind ? esc(kind) : '',
                meta: metaItems.map(function (m) { return esc(m); }),
            });
        }
        return (
            '<a class="prks-list-row prks-research-row" href="' +
            href +
            '">' +
            esc(title) +
            '</a>'
        );
    }

    function sourceAuthorsLabel(s) {
        return (s.authors || [])
            .map(function (a) {
                return (((a.first_name || '') + ' ' + (a.last_name || '')).trim() || a.credit_name || '');
            })
            .filter(Boolean)
            .join(', ');
    }

    function renderArgumentDetail(ctx, argument, container) {
        const a = argument || {};
        if (ctx && typeof ctx.setEntity === 'function') ctx.setEntity('argument', a);
        const kindLabel = a.kind === 'stance' ? 'Stance' : 'Argument';
        const editing = !!(ctx && ctx.ui && ctx.ui.argumentEditing);
        const verdicts = a.verdicts || [];

        function graphHash() {
            return typeof root.prksGraphFocusHash === 'function'
                ? root.prksGraphFocusHash('argument', a.id)
                : '#/graph?focus=' + encodeURIComponent('argument:' + a.id);
        }

        function verdictOpts(selected) {
            return verdicts
                .map(function (v) {
                    const id = v.id || '';
                    const sel = id === selected ? ' selected' : '';
                    return '<option value="' + esc(id) + '"' + sel + '>' + esc(v.label || id) + '</option>';
                })
                .join('');
        }

        const header =
            '<div class="prks-page-header page-header"><div class="page-header__title-row"><div>' +
            '<p class="saved-view-detail__kicker">' +
            esc(kindLabel) +
            '</p><h2 class="prks-page-title">' +
            esc(a.name || a.id) +
            '</h2></div><div class="page-header__actions">' +
            (editing
                ? '<button type="button" class="prks-btn prks-btn--secondary" id="prks-arg-cancel">Cancel</button>'
                : '<button type="button" class="prks-btn prks-btn--secondary" id="prks-arg-view-graph">View in graph</button>' +
                  '<button type="button" class="prks-btn prks-btn--secondary" id="prks-arg-edit">Edit</button>' +
                  '<button type="button" class="prks-btn prks-btn--secondary" id="prks-arg-response">New response</button>' +
                  '<button type="button" class="prks-btn prks-btn--quiet-danger prks-page-action--destructive" id="prks-arg-delete">Delete</button>') +
            '</div></div></div>';

        if (!editing) {
            const bodyText = String(a.main_text || '').trim();
            const targetList = Array.isArray(a.targets) ? a.targets : [];
            const targets = targetList
                .map(function (t) {
                    const href =
                        t.type === 'position'
                            ? '#/positions/' + encodeURIComponent(t.id)
                            : '#/arguments/' + encodeURIComponent(t.id);
                    const kind = t.type === 'position' ? 'Position' : t.kind === 'stance' ? 'Stance' : 'Argument';
                    return researchLinkRow(href, t.name || t.id, kind, [t.verdict_label || t.verdict_id || '']);
                })
                .join('') || '<p class="meta-row">No targets.</p>';
            const sourceList = Array.isArray(a.sources) ? a.sources : [];
            const sources = sourceList
                .map(function (s) {
                    const authors = sourceAuthorsLabel(s);
                    const pages = s.pages ? 'pp. ' + String(s.pages) : '';
                    return researchLinkRow(
                        '#/works/' + encodeURIComponent(s.work_id),
                        s.work_title || s.work_id,
                        '',
                        [authors, pages].filter(Boolean)
                    );
                })
                .join('') || '<p class="meta-row">No sources.</p>';
            const responseList = Array.isArray(a.responses) ? a.responses : [];
            const responses = responseList
                .map(function (r) {
                    const kind = r.kind === 'stance' ? 'Stance' : 'Argument';
                    return researchLinkRow(
                        '#/arguments/' + encodeURIComponent(r.id),
                        r.name || r.id,
                        kind,
                        [r.verdict_label || r.verdict_id || '']
                    );
                })
                .join('') || '<p class="meta-row">No responses.</p>';
            const mentionList = Array.isArray(a.mentions) ? a.mentions : [];
            const mentions = mentionList
                .map(function (m) {
                    return researchLinkRow(
                        '#/works/' + encodeURIComponent(m.work_id),
                        m.title || m.work_id,
                        '',
                        []
                    );
                })
                .join('') || '<p class="meta-row">Not mentioned in research notes.</p>';
            container.innerHTML =
                header +
                '<div class="research-entity">' +
                '<section class="research-entity__section" aria-labelledby="prks-arg-text-h">' +
                sectionHead('Main text', { headingId: 'prks-arg-text-h' }) +
                '<div class="research-md">' +
                (bodyText ? md(a.main_text) : '<p class="meta-row">No main text yet.</p>') +
                '</div></section>' +
                '<section class="research-entity__section" aria-labelledby="prks-arg-targets-h">' +
                sectionHead('Responds to', { headingId: 'prks-arg-targets-h', count: targetList.length }) +
                '<div class="list-view prks-research-index">' +
                targets +
                '</div></section>' +
                '<section class="research-entity__section" aria-labelledby="prks-arg-sources-h">' +
                sectionHead('Sources', {
                    headingId: 'prks-arg-sources-h',
                    count: sourceList.length,
                    sub: 'Works where this was made or taken.',
                }) +
                '<div class="list-view prks-research-index">' +
                sources +
                '</div></section>' +
                '<section class="research-entity__section" aria-labelledby="prks-arg-resp-h">' +
                sectionHead('Responses', { headingId: 'prks-arg-resp-h', count: responseList.length }) +
                '<div class="list-view prks-research-index">' +
                responses +
                '</div></section>' +
                '<section class="research-entity__section" aria-labelledby="prks-arg-mentions-h">' +
                sectionHead('Mentioned in notes', { headingId: 'prks-arg-mentions-h', count: mentionList.length }) +
                '<div class="list-view prks-research-index">' +
                mentions +
                '</div></section></div>';
            bindArgumentRead(ctx, a, container, graphHash);
            if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(container);
            return;
        }

        const targetRows = (a.targets || [])
            .map(function (t, i) {
                return targetRowHtml(t, i, verdictOpts);
            })
            .join('');
        const sourceRows = (a.sources || [])
            .map(function (s, i) {
                return sourceRowHtml(s, i);
            })
            .join('');
        container.innerHTML =
            header +
            '<form id="prks-arg-form" class="prks-arg-form form-pane">' +
            '<label class="form-field-label" for="prks-arg-name">Name</label>' +
            '<input type="text" id="prks-arg-name" value="' +
            esc(a.name || '') +
            '">' +
            '<label class="form-field-label" for="prks-arg-kind">Kind</label>' +
            '<select id="prks-arg-kind">' +
            '<option value="argument"' +
            (a.kind !== 'stance' ? ' selected' : '') +
            '>Argument</option>' +
            '<option value="stance"' +
            (a.kind === 'stance' ? ' selected' : '') +
            '>Stance</option></select>' +
            '<label class="form-field-label" for="prks-arg-text">Main text</label>' +
            '<textarea id="prks-arg-text" class="textarea-md" rows="8">' +
            esc(a.main_text || '') +
            '</textarea>' +
            '<h3>Responds to</h3>' +
            '<div id="prks-arg-targets">' +
            (targetRows || '<p class="meta-row">None yet.</p>') +
            '</div>' +
            '<button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" id="prks-arg-add-target">Add target</button>' +
            '<h3>Sources</h3>' +
            '<p class="meta-row">Works where this was made or taken.</p>' +
            '<div id="prks-arg-sources">' +
            (sourceRows || '<p class="meta-row">None yet.</p>') +
            '</div>' +
            '<button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" id="prks-arg-add-source">Add source</button>' +
            '<p class="prks-arg-form__actions"><button type="submit" class="prks-btn prks-btn--primary">Save</button></p>' +
            '</form>';
        bindArgumentEdit(ctx, a, container, verdictOpts);
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(container);
    }

    function targetRowHtml(t, i, verdictOpts) {
        const row = t || {};
        const type = row.type === 'argument' ? 'argument' : 'position';
        const kindLabel = type === 'position' ? 'Position' : row.kind === 'stance' ? 'Stance' : 'Argument';
        const name = row.name || row.id || 'Choose…';
        return (
            '<div class="prks-arg-row" data-i="' +
            i +
            '">' +
            '<input type="hidden" data-field="type" value="' +
            esc(type) +
            '">' +
            '<input type="hidden" data-field="id" value="' +
            esc(row.id || '') +
            '">' +
            '<span class="prks-research-row__kicker">' +
            esc(kindLabel) +
            '</span>' +
            '<button type="button" class="prks-btn prks-btn--secondary prks-arg-rel__pick" data-pick="target">' +
            esc(name) +
            '</button>' +
            '<select data-field="verdict">' +
            verdictOpts(row.verdict_id) +
            '</select>' +
            '<button type="button" class="prks-btn prks-btn--ghost prks-btn--sm" data-remove="target">Remove</button>' +
            '</div>'
        );
    }

    function sourceRowHtml(s, i) {
        const row = s || {};
        const name = row.work_title || row.work_id || 'Choose a work…';
        return (
            '<div class="prks-arg-row" data-i="' +
            i +
            '">' +
            '<input type="hidden" data-field="work_id" value="' +
            esc(row.work_id || '') +
            '">' +
            '<button type="button" class="prks-btn prks-btn--secondary prks-arg-rel__pick" data-pick="source">' +
            esc(name) +
            '</button>' +
            '<input type="text" data-field="pages" value="' +
            esc(row.pages || '') +
            '" placeholder="pages" maxlength="100" aria-label="Pages">' +
            '<button type="button" class="prks-btn prks-btn--ghost prks-btn--sm" data-remove="source">Remove</button>' +
            '</div>'
        );
    }

    function bindArgumentRead(ctx, a, container, graphHash) {
        const generation = ctx && ctx.generation;
        const ownsArgument = function () {
            return typeof root.prksTabContextOwnsEntityRoute === 'function'
                ? root.prksTabContextOwnsEntityRoute(ctx, generation, 'argument', a.id, 'argument-detail')
                : !!(ctx && ctx.isCurrent && ctx.isCurrent(generation));
        };
        const viewGraph = container.querySelector('#prks-arg-view-graph');
        if (viewGraph) {
            viewGraph.addEventListener('click', function () {
                if (typeof root.prksNavigate === 'function') {
                    root.prksNavigate(graphHash(), { tabId: ctx && ctx.tabId });
                }
            });
        }
        const edit = container.querySelector('#prks-arg-edit');
        if (edit) {
            edit.addEventListener('click', function () {
                if (ctx && ctx.ui) ctx.ui.argumentEditing = true;
                renderArgumentDetail(ctx, a, container);
            });
        }
        const resp = container.querySelector('#prks-arg-response');
        if (resp) {
            resp.addEventListener('click', function () {
                void (async function () {
                    const name = await promptArgumentName('argument', 'New response argument');
                    if (!name) return;
                    const created = await root.createArgument({
                        name: name,
                        kind: 'argument',
                        targets: [{ type: 'argument', id: a.id, verdict_id: 'opposes' }],
                    });
                    if (created && created.id && ownsArgument() && typeof root.prksNavigate === 'function') {
                        root.prksNavigate('#/arguments/' + encodeURIComponent(created.id), { tabId: ctx.tabId });
                    }
                })();
            });
        }
        const del = container.querySelector('#prks-arg-delete');
        if (del) {
            del.addEventListener('click', function () {
                void deleteArgument(ctx, generation, a);
            });
        }
    }

    function bindArgumentEdit(ctx, a, container, verdictOpts) {
        const kindSel = container.querySelector('#prks-arg-kind');
        function defaultVerdict() {
            const kind = kindSel && kindSel.value === 'stance' ? 'stance' : 'argument';
            if (kind === 'stance') return 'holds';
            return 'supports';
        }
        function clearEmpty(wrap) {
            if (wrap && wrap.querySelector('.meta-row') && !wrap.querySelector('.prks-arg-row')) {
                wrap.innerHTML = '';
            }
        }
        function addTargetRow(preset) {
            const wrap = container.querySelector('#prks-arg-targets');
            if (!wrap) return;
            clearEmpty(wrap);
            const t = preset || {};
            if (!t.verdict_id) t.verdict_id = defaultVerdict();
            wrap.insertAdjacentHTML('beforeend', targetRowHtml(t, wrap.querySelectorAll('.prks-arg-row').length, verdictOpts));
        }
        function addSourceRow(preset) {
            const wrap = container.querySelector('#prks-arg-sources');
            if (!wrap) return;
            clearEmpty(wrap);
            wrap.insertAdjacentHTML(
                'beforeend',
                sourceRowHtml(preset || {}, wrap.querySelectorAll('.prks-arg-row').length)
            );
        }
        function openTargetPicker(row) {
            if (typeof root.prksOpenResearchPicker !== 'function' && typeof prksOpenResearchPicker !== 'function') {
                return;
            }
            const open = root.prksOpenResearchPicker || prksOpenResearchPicker;
            void (async function () {
                const [args, positions] = await Promise.all([
                    typeof root.fetchArguments === 'function' ? root.fetchArguments() : [],
                    typeof root.fetchPositions === 'function' ? root.fetchPositions() : [],
                ]);
                const selfId = String(a.id || '');
                const items = function () {
                    const out = [];
                    (positions || []).forEach(function (p) {
                        out.push({
                            id: p.id,
                            label: p.name || p.id,
                            kind: 'Position',
                            pickType: 'position',
                            haystack: (p.name || '') + ' ' + (p.id || ''),
                        });
                    });
                    (args || []).forEach(function (x) {
                        if (String(x.id) === selfId) return;
                        out.push({
                            id: x.id,
                            label: x.name || x.id,
                            kind: x.kind === 'stance' ? 'Stance' : 'Argument',
                            pickType: 'argument',
                            haystack: (x.name || '') + ' ' + (x.id || '') + ' ' + (x.kind || ''),
                        });
                    });
                    return out;
                };
                open({
                    title: 'Responds to',
                    items: items,
                    onPick: function (id, pickType) {
                        const type = pickType === 'argument' ? 'argument' : 'position';
                        const typeEl = row.querySelector('[data-field="type"]');
                        const idEl = row.querySelector('[data-field="id"]');
                        const pickBtn = row.querySelector('[data-pick="target"]');
                        const kicker = row.querySelector('.prks-research-row__kicker');
                        if (typeEl) typeEl.value = type;
                        if (idEl) idEl.value = id;
                        let label = id;
                        let kindLabel = type === 'position' ? 'Position' : 'Argument';
                        items().forEach(function (it) {
                            if (it.id === id) {
                                label = it.label;
                                kindLabel = it.kind;
                            }
                        });
                        if (pickBtn) pickBtn.textContent = label;
                        if (kicker) kicker.textContent = kindLabel;
                    },
                });
            })();
        }
        function openSourcePicker(row) {
            const open = (root.prksOpenResearchPicker || (typeof prksOpenResearchPicker === 'function' ? prksOpenResearchPicker : null));
            if (!open) return;
            void (async function () {
                const works = typeof root.fetchWorks === 'function' ? await root.fetchWorks() : [];
                const items = function () {
                    return (works || []).map(function (w) {
                        return {
                            id: w.id,
                            label: w.title || w.id,
                            kind: 'Work',
                            pickType: 'work',
                            haystack: (w.title || '') + ' ' + (w.id || ''),
                        };
                    });
                };
                open({
                    title: 'Source work',
                    items: items,
                    onPick: function (id) {
                        const idEl = row.querySelector('[data-field="work_id"]');
                        const pickBtn = row.querySelector('[data-pick="source"]');
                        if (idEl) idEl.value = id;
                        let label = id;
                        items().forEach(function (it) {
                            if (it.id === id) label = it.label;
                        });
                        if (pickBtn) pickBtn.textContent = label;
                    },
                });
            })();
        }
        const cancel = container.querySelector('#prks-arg-cancel');
        if (cancel) {
            cancel.addEventListener('click', function () {
                if (ctx && ctx.ui) ctx.ui.argumentEditing = false;
                renderArgumentDetail(ctx, a, container);
            });
        }
        const addT = container.querySelector('#prks-arg-add-target');
        const addS = container.querySelector('#prks-arg-add-source');
        if (addT) {
            addT.addEventListener('click', function () {
                addTargetRow();
                const rows = container.querySelectorAll('#prks-arg-targets .prks-arg-row');
                const last = rows[rows.length - 1];
                if (last) openTargetPicker(last);
            });
        }
        if (addS) {
            addS.addEventListener('click', function () {
                addSourceRow();
                const rows = container.querySelectorAll('#prks-arg-sources .prks-arg-row');
                const last = rows[rows.length - 1];
                if (last) openSourcePicker(last);
            });
        }
        const form = container.querySelector('#prks-arg-form');
        if (form) {
            form.addEventListener('click', function (e) {
                const pick = e.target.closest && e.target.closest('[data-pick]');
                if (pick) {
                    const row = pick.closest('.prks-arg-row');
                    if (!row) return;
                    if (pick.getAttribute('data-pick') === 'target') openTargetPicker(row);
                    else openSourcePicker(row);
                    return;
                }
                const btn = e.target.closest && e.target.closest('[data-remove]');
                if (!btn) return;
                const row = btn.closest('.prks-arg-row');
                if (row && row.parentNode) row.parentNode.removeChild(row);
            });
            form.addEventListener('submit', function (e) {
                e.preventDefault();
                void saveArgumentForm(ctx, a.id, container);
            });
        }
    }

    async function saveArgumentForm(ctx, id, container) {
        const generation = ctx && ctx.generation;
        const nameEl = container.querySelector('#prks-arg-name');
        const kindEl = container.querySelector('#prks-arg-kind');
        const textEl = container.querySelector('#prks-arg-text');
        const targets = [];
        container.querySelectorAll('#prks-arg-targets .prks-arg-row').forEach(function (row) {
            const type = (row.querySelector('[data-field="type"]') || {}).value || 'position';
            const tid = ((row.querySelector('[data-field="id"]') || {}).value || '').trim();
            const verdict = (row.querySelector('[data-field="verdict"]') || {}).value || '';
            if (tid) targets.push({ type: type, id: tid, verdict_id: verdict });
        });
        const sources = [];
        container.querySelectorAll('#prks-arg-sources .prks-arg-row').forEach(function (row) {
            const wid = ((row.querySelector('[data-field="work_id"]') || {}).value || '').trim();
            const pages = ((row.querySelector('[data-field="pages"]') || {}).value || '').trim();
            if (wid) sources.push({ work_id: wid, pages: pages });
        });
        try {
            await root.updateArgument(id, {
                name: nameEl ? nameEl.value : '',
                kind: kindEl ? kindEl.value : 'argument',
                main_text: textEl ? textEl.value : '',
            });
            await root.putArgumentTargets(id, targets);
            await root.putArgumentSources(id, sources);
            if (
                typeof root.prksTabContextOwnsEntityRoute === 'function' &&
                root.prksTabContextOwnsEntityRoute(ctx, generation, 'argument', id, 'argument-detail')
            ) {
                if (ctx.ui) ctx.ui.argumentEditing = false;
                if (typeof root.prksNavigate === 'function') {
                    root.prksNavigate('#/arguments/' + encodeURIComponent(id), {
                        replace: true,
                        tabId: ctx.tabId,
                    });
                }
            }
        } catch (err) {
            if (!ctx || !ctx.isCurrent || !ctx.isCurrent(generation)) return;
            if (typeof root.prksAlertDialog === 'function') {
                await root.prksAlertDialog({
                    title: 'Could not save',
                    message: (err && err.message) || '',
                });
            }
        }
    }

    async function deleteArgument(ctx, generation, a) {
        const ok =
            typeof root.prksConfirmDestructive === 'function'
                ? await root.prksConfirmDestructive({
                      title: 'Delete Argument?',
                      message: 'Remove note references and incoming responses first if deletion is blocked.',
                      confirmLabel: 'Delete',
                  })
                : true;
        if (!ok) return;
        if (!ctx || !ctx.isCurrent || !ctx.isCurrent(generation)) return;
        try {
            await root.deleteArgument(a.id);
            if (
                typeof root.prksTabContextOwnsEntityRoute === 'function' &&
                root.prksTabContextOwnsEntityRoute(ctx, generation, 'argument', a.id, 'argument-detail') &&
                typeof root.prksNavigate === 'function'
            ) {
                root.prksNavigate('#/arguments', { replace: true, tabId: ctx.tabId });
            }
        } catch (err) {
            if (!ctx || !ctx.isCurrent || !ctx.isCurrent(generation)) return;
            if (typeof root.prksAlertDialog === 'function') {
                await root.prksAlertDialog({
                    title: 'Cannot delete',
                    message: (err && err.message) || 'Could not delete Argument.',
                });
            }
        }
    }

    async function promptArgumentName(kindName, title) {
        const isStance = kindName === 'stance';
        if (typeof root.prksPromptTextDialog !== 'function') return null;
        const name = await root.prksPromptTextDialog({
            title: title || (isStance ? 'New Stance' : 'New Argument'),
            okLabel: 'Create',
        });
        if (name == null || !String(name).trim()) return null;
        return String(name).trim();
    }

    async function createArgumentFromWork(options) {
        const opts = options || {};
        const kind = opts.kind === 'stance' ? 'stance' : 'argument';
        const provided = opts.name != null ? String(opts.name).trim() : '';
        const name = provided || (await promptArgumentName(kind));
        if (!name) return null;
        const payload = {
            name: String(name).trim(),
            kind: kind,
            main_text: '',
            sources: [],
        };
        if (opts.workId) {
            payload.sources.push({
                work_id: opts.workId,
                pages: opts.pages || '',
            });
        }
        try {
            return await root.createArgument(payload);
        } catch (err) {
            if (typeof root.prksAlertDialog === 'function') {
                await root.prksAlertDialog({
                    title: 'Could not create',
                    message: (err && err.message) || '',
                });
            }
            return null;
        }
    }

    const api = {
        renderArgumentsIndex: renderArgumentsIndex,
        renderArgumentDetail: renderArgumentDetail,
        renderArgumentNotFound: renderArgumentNotFound,
        prksCreateArgumentFromWork: createArgumentFromWork,
    };
    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : typeof global !== 'undefined' ? global : this);
