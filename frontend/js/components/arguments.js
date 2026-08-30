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

    function targetLine(t) {
        const label = esc(t.verdict_label || t.verdict_id || '');
        if (t.type === 'position') {
            return (
                label +
                '   Position: <a href="#/positions/' +
                encodeURIComponent(t.id) +
                '">' +
                esc(t.name || t.id) +
                '</a>'
            );
        }
        return (
            label +
            '   Argument: <a href="#/arguments/' +
            encodeURIComponent(t.id) +
            '">' +
            esc(t.name || t.id) +
            '</a>'
        );
    }

    function sourceLine(s) {
        const authors = (s.authors || [])
            .map(function (a) {
                const n = ((a.first_name || '') + ' ' + (a.last_name || '')).trim() || a.credit_name || '';
                return esc(n);
            })
            .filter(Boolean)
            .join(', ');
        const pages = s.pages ? ' pp. ' + esc(s.pages) : '';
        return (
            (authors ? authors + ' · ' : '') +
            '<a href="#/works/' +
            encodeURIComponent(s.work_id) +
            '">' +
            esc(s.work_title || s.work_id) +
            '</a>' +
            pages
        );
    }

    function renderArgumentsIndex(items, container, filterKind) {
        const list = Array.isArray(items) ? items : [];
        const kind = filterKind || 'all';
        const rows = list.length
            ? list
                  .map(function (a) {
                      const responds = (a.targets || []).map(targetLine).join('<br>') || '—';
                      const sources = (a.sources || []).map(sourceLine).join('<br>') || '—';
                      return (
                          '<div class="project-card"><a href="#/arguments/' +
                          encodeURIComponent(a.id) +
                          '"><strong>' +
                          esc(a.name || a.id) +
                          '</strong></a>' +
                          '<p class="meta-row">' +
                          esc(a.kind === 'stance' ? 'Stance' : 'Argument') +
                          ' · Responses: ' +
                          esc(String(a.response_count || 0)) +
                          '</p>' +
                          '<p class="meta-row">Responds to: ' +
                          responds +
                          '</p>' +
                          '<p class="meta-row">Sources: ' +
                          sources +
                          '</p></div>'
                      );
                  })
                  .join('')
            : '<p class="meta-row">No Arguments or Stances yet.</p>';
        function btn(k, label) {
            return (
                '<button type="button" class="ribbon-btn ribbon-btn--sm' +
                (kind === k ? ' ribbon-btn--active' : '') +
                '" data-arg-filter="' +
                k +
                '">' +
                label +
                '</button>'
            );
        }
        container.innerHTML =
            '<div class="page-header"><div class="page-header__title-row"><h2>' +
            (typeof root.prksPageHeaderIconHtml === 'function' ? root.prksPageHeaderIconHtml('messages-square') : '') +
            ' Arguments &amp; Stances</h2>' +
            '<div class="page-header__actions">' +
            '<button type="button" class="ribbon-btn" id="prks-argument-new">New Argument</button>' +
            '<button type="button" class="ribbon-btn" id="prks-stance-new">New Stance</button>' +
            '</div></div></div>' +
            '<p>' +
            btn('all', 'All') +
            ' ' +
            btn('argument', 'Arguments') +
            ' ' +
            btn('stance', 'Stances') +
            '</p><div class="list-view">' +
            rows +
            '</div>';
        container.querySelectorAll('[data-arg-filter]').forEach(function (el) {
            el.addEventListener('click', function () {
                const k = el.getAttribute('data-arg-filter');
                if (typeof root.prksNavigate === 'function') {
                    root.prksNavigate(k === 'all' ? '#/arguments' : '#/arguments?kind=' + encodeURIComponent(k));
                }
            });
        });
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
        const na = container.querySelector('#prks-argument-new');
        const ns = container.querySelector('#prks-stance-new');
        if (na) na.addEventListener('click', make('argument'));
        if (ns) ns.addEventListener('click', make('stance'));
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(container);
    }

    function renderArgumentNotFound(container) {
        container.innerHTML =
            '<div class="page-header"><h2>Argument not found.</h2></div>' +
            '<p class="meta-row"><a class="ribbon-btn" href="#/arguments">Back to Arguments &amp; Stances</a></p>';
    }

    function renderArgumentDetail(argument, container) {
        const a = argument || {};
        const kindLabel = a.kind === 'stance' ? 'Stance' : 'Argument';
        const verdicts = a.verdicts || [];
        const responses = (a.responses || []).map(function (r) {
            return (
                '<p>' +
                esc(r.verdict_label || r.verdict_id || '') +
                '   <a href="#/arguments/' +
                encodeURIComponent(r.id) +
                '">' +
                esc(r.name || r.id) +
                '</a></p>'
            );
        }).join('') || '<p class="meta-row">None</p>';
        const mentions = (a.mentions || [])
            .map(function (m) {
                return (
                    '<div class="project-card"><a href="#/works/' +
                    encodeURIComponent(m.work_id) +
                    '">' +
                    esc(m.title || m.work_id) +
                    '</a></div>'
                );
            })
            .join('') || '<p class="meta-row">Not mentioned in research notes.</p>';
        const verdictOpts = function (selected) {
            return verdicts
                .map(function (v) {
                    const id = v.id || '';
                    const sel = id === selected ? ' selected' : '';
                    return '<option value="' + esc(id) + '"' + sel + '>' + esc(v.label || id) + '</option>';
                })
                .join('');
        };
        const targetRows = (a.targets || [])
            .map(function (t, i) {
                return (
                    '<div class="prks-arg-row" data-i="' +
                    i +
                    '">' +
                    '<select data-field="type"><option value="position"' +
                    (t.type === 'position' ? ' selected' : '') +
                    '>Position</option><option value="argument"' +
                    (t.type === 'argument' ? ' selected' : '') +
                    '>Argument</option></select> ' +
                    '<input type="text" data-field="id" value="' +
                    esc(t.id || '') +
                    '" placeholder="P-… or A-…"> ' +
                    '<select data-field="verdict">' +
                    verdictOpts(t.verdict_id) +
                    '</select> ' +
                    '<button type="button" class="ribbon-btn ribbon-btn--sm" data-remove="target">Remove</button>' +
                    '</div>'
                );
            })
            .join('');
        const sourceRows = (a.sources || [])
            .map(function (s, i) {
                return (
                    '<div class="prks-arg-row" data-i="' +
                    i +
                    '">' +
                    '<input type="text" data-field="work_id" value="' +
                    esc(s.work_id || '') +
                    '" placeholder="Work id"> ' +
                    '<input type="text" data-field="pages" value="' +
                    esc(s.pages || '') +
                    '" placeholder="pages" maxlength="100"> ' +
                    '<button type="button" class="ribbon-btn ribbon-btn--sm" data-remove="source">Remove</button>' +
                    '</div>'
                );
            })
            .join('');
        container.innerHTML =
            '<div class="page-header"><div class="page-header__title-row"><div>' +
            '<p class="saved-view-detail__kicker">' +
            esc(kindLabel) +
            '</p><h2>' +
            esc(a.name || a.id) +
            '</h2></div><div class="page-header__actions">' +
            '<button type="button" class="ribbon-btn" id="prks-arg-response">New response argument</button>' +
            '<button type="button" class="ribbon-btn" id="prks-arg-delete">Delete</button>' +
            '</div></div></div>' +
            '<form id="prks-arg-form" class="prks-arg-form form-pane">' +
            '<label class="form-field-label" for="prks-arg-name">Name</label>' +
            '<input type="text" id="prks-arg-name" value="' +
            esc(a.name || '') +
            '">' +
            '<label class="form-field-label" for="prks-arg-kind">Argument / Stance</label>' +
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
            (targetRows || '<p class="meta-row">None</p>') +
            '</div>' +
            '<button type="button" class="ribbon-btn ribbon-btn--sm" id="prks-arg-add-target">Add target</button>' +
            '<h3>Where made/taken</h3>' +
            '<div id="prks-arg-sources">' +
            (sourceRows || '<p class="meta-row">None</p>') +
            '</div>' +
            '<button type="button" class="ribbon-btn ribbon-btn--sm" id="prks-arg-add-source">Add source</button>' +
            '<p class="prks-arg-form__actions"><button type="submit" class="add-new-btn">Save</button></p>' +
            '</form>' +
            '<h3>Counter/Response Arguments</h3>' +
            responses +
            '<h3>Mentioned in notes</h3>' +
            mentions;
        const kindSel = container.querySelector('#prks-arg-kind');
        function defaultVerdict() {
            const kind = kindSel && kindSel.value === 'stance' ? 'stance' : 'argument';
            if (kind === 'stance') return 'holds';
            return 'supports';
        }
        function addTargetRow(preset) {
            const wrap = container.querySelector('#prks-arg-targets');
            if (!wrap) return;
            if (wrap.querySelector('.meta-row')) wrap.innerHTML = '';
            const div = document.createElement('div');
            div.className = 'prks-arg-row';
            const t = preset || {};
            div.innerHTML =
                '<select data-field="type"><option value="position"' +
                (t.type !== 'argument' ? ' selected' : '') +
                '>Position</option><option value="argument"' +
                (t.type === 'argument' ? ' selected' : '') +
                '>Argument</option></select> ' +
                '<input type="text" data-field="id" value="' +
                esc(t.id || '') +
                '" placeholder="P-… or A-…"> ' +
                '<select data-field="verdict">' +
                verdictOpts(t.verdict_id || defaultVerdict()) +
                '</select> ' +
                '<button type="button" class="ribbon-btn ribbon-btn--sm" data-remove="target">Remove</button>';
            wrap.appendChild(div);
        }
        function addSourceRow(preset) {
            const wrap = container.querySelector('#prks-arg-sources');
            if (!wrap) return;
            if (wrap.querySelector('.meta-row')) wrap.innerHTML = '';
            const s = preset || {};
            const div = document.createElement('div');
            div.className = 'prks-arg-row';
            div.innerHTML =
                '<input type="text" data-field="work_id" value="' +
                esc(s.work_id || '') +
                '" placeholder="Work id"> ' +
                '<input type="text" data-field="pages" value="' +
                esc(s.pages || '') +
                '" placeholder="pages" maxlength="100"> ' +
                '<button type="button" class="ribbon-btn ribbon-btn--sm" data-remove="source">Remove</button>';
            wrap.appendChild(div);
        }
        const addT = container.querySelector('#prks-arg-add-target');
        const addS = container.querySelector('#prks-arg-add-source');
        if (addT) addT.addEventListener('click', function () { addTargetRow(); });
        if (addS) addS.addEventListener('click', function () { addSourceRow(); });
        container.addEventListener('click', function (e) {
            const btn = e.target.closest && e.target.closest('[data-remove]');
            if (!btn) return;
            const row = btn.closest('.prks-arg-row');
            if (row && row.parentNode) row.parentNode.removeChild(row);
        });
        const form = container.querySelector('#prks-arg-form');
        if (form) {
            form.addEventListener('submit', function (e) {
                e.preventDefault();
                void saveArgumentForm(a.id, container);
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
                    if (created && created.id && typeof root.prksNavigate === 'function') {
                        root.prksNavigate('#/arguments/' + encodeURIComponent(created.id));
                    }
                })();
            });
        }
        const del = container.querySelector('#prks-arg-delete');
        if (del) {
            del.addEventListener('click', function () {
                void deleteArgument(a);
            });
        }
        if (typeof root.prksRefreshIcons === 'function') root.prksRefreshIcons(container);
    }

    async function saveArgumentForm(id, container) {
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
            if (typeof root.prksNavigate === 'function') {
                root.prksNavigate(root.location.hash, { replace: true });
            }
        } catch (err) {
            if (typeof root.prksAlertDialog === 'function') {
                await root.prksAlertDialog({
                    title: 'Could not save',
                    message: (err && err.message) || '',
                });
            }
        }
    }

    async function deleteArgument(a) {
        const ok =
            typeof root.prksConfirmDestructive === 'function'
                ? await root.prksConfirmDestructive({
                      title: 'Delete Argument?',
                      message: 'Remove note references and incoming responses first if deletion is blocked.',
                      confirmLabel: 'Delete',
                  })
                : true;
        if (!ok) return;
        try {
            await root.deleteArgument(a.id);
            if (typeof root.prksNavigate === 'function') root.prksNavigate('#/arguments', { replace: true });
        } catch (err) {
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
        const name = await promptArgumentName(kind);
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
