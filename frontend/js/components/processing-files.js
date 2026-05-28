function prksProcessingDocTypeIdPrefix(processingId) {
    return `prks-pf-dt-${String(processingId || 'x').replace(/[^a-zA-Z0-9_-]/g, '_')}`;
}

function prksProcessingSegIdPrefix(processingId, kind) {
    const safe = String(processingId || 'x').replace(/[^a-zA-Z0-9_-]/g, '_');
    return `prks-pf-${String(kind || 'seg')}-${safe}`;
}

const PRKS_PROCESSING_STATUS_LABELS = ['Not Started', 'Planned', 'In Progress', 'Completed', 'Paused'];

function prksProcessingEsc(s) {
    if (typeof window.prksEscapeHtml === 'function') return window.prksEscapeHtml(s);
    if (s == null || s === '') return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function prksProcessingPreviewStacked() {
    return typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 1240px)').matches;
}

/** Put preview pane after list (second grid column on wide, below list on stacked). */
function prksProcessingPlacePreviewAfterList() {
    const listEl = document.getElementById('prks-processing-list');
    const aside = document.getElementById('prks-processing-inline-preview');
    if (!listEl || !aside) return;
    listEl.insertAdjacentElement('afterend', aside);
}

/** Stacked only: preview directly under active card. */
function prksProcessingPlacePreviewAfterCard(card) {
    const aside = document.getElementById('prks-processing-inline-preview');
    if (!card || !aside) return;
    card.insertAdjacentElement('afterend', aside);
}

function prksProcessingSyncPreviewSlot() {
    const layout = document.querySelector('.prks-processing-main-layout');
    const listEl = document.getElementById('prks-processing-list');
    const aside = document.getElementById('prks-processing-inline-preview');
    const shell = document.getElementById('prks-processing-inline-preview-frame-shell');
    if (!layout || !listEl || !aside) return;
    if (!prksProcessingPreviewStacked()) {
        prksProcessingPlacePreviewAfterList();
        return;
    }
    const fid = layout.getAttribute('data-preview-for') || '';
    if (fid && shell && !shell.classList.contains('hidden')) {
        const esc = typeof CSS !== 'undefined' && typeof CSS.escape === 'function' ? CSS.escape(fid) : String(fid).replace(/\\/g, '');
        const card = listEl.querySelector(`[data-processing-id="${esc}"]`);
        if (card) prksProcessingPlacePreviewAfterCard(card);
        else prksProcessingPlacePreviewAfterList();
    } else {
        prksProcessingPlacePreviewAfterList();
    }
}

function prksProcessingEnsureResizeSync() {
    if (window.__prksProcessingResizeBound) return;
    window.__prksProcessingResizeBound = true;
    let t;
    window.addEventListener('resize', () => {
        window.clearTimeout(t);
        t = window.setTimeout(() => {
            if (!document.getElementById('prks-processing-list')) return;
            prksProcessingSyncPreviewSlot();
        }, 150);
    });
}

const PRKS_PROCESSING_ROLE_TYPES = [
    'Author',
    'Editor',
    'Reviewer',
    'Translator',
    'Introduction',
    'Foreword',
    'Afterword',
];

function prksProcessingPersonDisplayName(person) {
    if (!person || typeof person !== 'object') return '';
    const first = String(person.first_name || '').trim();
    const last = String(person.last_name || '').trim();
    const full = [first, last].filter(Boolean).join(' ').trim();
    return full || String(person.id || '').trim();
}

function prksProcessingGetPeople() {
    const rows = window.__prksProcessingPeople;
    return Array.isArray(rows) ? rows : [];
}

function prksProcessingGetFolders() {
    const rows = window.__prksProcessingFolders;
    if (Array.isArray(rows) && rows.length) return rows;
    try {
        if (typeof allFolders !== 'undefined' && Array.isArray(allFolders) && allFolders.length) return allFolders;
    } catch (_e) {}
    return [];
}

function prksProcessingAttachFolderCombobox(card) {
    const input = card.querySelector('[data-role="folder-search"]');
    const hidden = card.querySelector('[data-role="folder-id"]');
    const results = card.querySelector('[data-role="folder-results"]');
    if (!input || !hidden || !results) return;
    const render = () => {
        const valRaw = String(input.value || '');
        const val = valRaw.toLowerCase().trim();
        const data = prksProcessingGetFolders().filter((f) => String(f.id || '').trim());
        const filtered = data
            .filter((f) => !val || String(f.title || '').toLowerCase().includes(val))
            .slice(0, 25);
        results.innerHTML = '';
        if (!filtered.length) {
            results.innerHTML = '<div class="result-item no-results">No folders found</div>';
        } else {
            filtered.forEach((folder) => {
                const label = String(folder.title || '').trim() || String(folder.id || '');
                const div = document.createElement('div');
                div.className = 'result-item';
                div.textContent = label;
                div.onmousedown = (e) => {
                    e.preventDefault();
                    hidden.value = String(folder.id || '');
                    input.value = label;
                    prksHideInlineComboboxResults(results);
                };
                results.appendChild(div);
            });
        }
        if (typeof prksShowInlineComboboxResults === 'function') {
            prksShowInlineComboboxResults(input, results);
        } else {
            results.classList.remove('hidden');
        }
    };
    const hideResults = () => {
        prksHideInlineComboboxResults(results);
    };
    input.addEventListener('focus', render);
    input.addEventListener('input', () => {
        hidden.value = '';
        render();
    });
    input.addEventListener('blur', () => {
        setTimeout(hideResults, 200);
    });
}

async function prksProcessingQuickCreateFolder(card) {
    const input = card.querySelector('[data-role="folder-search"]');
    const hidden = card.querySelector('[data-role="folder-id"]');
    const results = card.querySelector('[data-role="folder-results"]');
    const title = String(input?.value || '').trim();
    if (!title) {
        await prksAlertMessage('Enter folder title in search field first.', 'Validation');
        return;
    }
    try {
        const res = await fetch('/api/folders', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, description: 'Quick-created from processing inbox' }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            await prksAlertMessage(data.error || 'Could not create folder.', 'Could not save');
            return;
        }
        const folders = await fetchFolders();
        window.__prksProcessingFolders = Array.isArray(folders) ? folders : [];
        try {
            allFolders = window.__prksProcessingFolders;
            window.allFolders = allFolders;
        } catch (_e) {}
        if (hidden) hidden.value = data.id;
        if (input) input.value = title;
        if (results) prksHideInlineComboboxResults(results);
    } catch (e) {
        console.error(e);
        await prksAlertMessage('Could not create folder.', 'Error');
    }
}

function prksProcessingRenderTagList(card) {
    const listEl = card.querySelector('[data-role="tags-list"]');
    if (!listEl) return;
    const tags = Array.isArray(card.__processingTags) ? card.__processingTags : [];
    if (!tags.length) {
        listEl.innerHTML = '<span class="status-chip-list__empty">No tags selected</span>';
        return;
    }
    listEl.innerHTML = tags
        .map(
            (t, idx) =>
                `<span class="tag work-tag-chip">${prksProcessingEsc(t.name || '')} ` +
                `<button type="button" class="work-tag-remove" data-action="remove-tag" data-remove-tag-index="${idx}" title="Remove" aria-label="Remove tag">&times;</button></span>`
        )
        .join('');
}

function prksProcessingGetTags() {
    if (Array.isArray(window.__prksProcessingTagsCache)) return window.__prksProcessingTagsCache;
    if (Array.isArray(window.__prksAllTagsCache)) return window.__prksAllTagsCache;
    return [];
}

function prksProcessingAttachTagCombobox(card) {
    const input = card.querySelector('[data-role="tag-search"]');
    const results = card.querySelector('[data-role="tag-results"]');
    if (!input || !results || input.dataset.bound === '1') return;
    input.dataset.bound = '1';

    const attachedIds = () => new Set((card.__processingTags || []).map((t) => String(t.id || '')));

    const render = () => {
        const all = prksProcessingGetTags();
        const val = input.value.trim();
        const valLower = val.toLowerCase();
        const attached = attachedIds();
        const available = all.filter((t) => !attached.has(String(t.id || '')));
        const filtered = !val
            ? available.slice(0, 40)
            : available.filter((t) => typeof prksTagMatchesQuery === 'function' && prksTagMatchesQuery(t, valLower)).slice(0, 40);
        const exactMatch =
            val && available.some((t) => typeof prksTagExactMatch === 'function' && prksTagExactMatch(t, valLower));

        results.innerHTML = '';
        if (val && !exactMatch) {
            const c = document.createElement('div');
            c.className = 'result-item result-item--create';
            c.textContent = 'Create tag "' + val + '"';
            c.onmousedown = (ev) => {
                ev.preventDefault();
                void (async () => {
                    try {
                        const res = await fetch('/api/tags', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ name: val, color: '#6d6cf7' }),
                        });
                        const data = await res.json();
                        if (!res.ok || !data.id) throw new Error(data.error || 'no id');
                        window.__prksAllTagsCache = null;
                        window.__prksProcessingTagsCache = null;
                        card.__processingTags = Array.isArray(card.__processingTags) ? card.__processingTags : [];
                        if (!attachedIds().has(String(data.id))) {
                            card.__processingTags.push({ id: data.id, name: data.name || val });
                            prksProcessingRenderTagList(card);
                        }
                        input.value = '';
                        prksHideInlineComboboxResults(results);
                    } catch (e) {
                        console.error(e);
                        await prksAlertMessage('Could not create tag.', 'Error');
                    }
                })();
            };
            results.appendChild(c);
        }
        if (!filtered.length) {
            if (results.childElementCount === 0) {
                results.innerHTML = '<div class="result-item no-results">No tags found</div>';
            }
        } else {
            filtered.forEach((tag) => {
                const div = document.createElement('div');
                div.className = 'result-item';
                div.textContent =
                    typeof prksTagComboboxLabel === 'function' ? prksTagComboboxLabel(tag, valLower) : String(tag.name || '');
                div.onmousedown = (ev) => {
                    ev.preventDefault();
                    card.__processingTags = Array.isArray(card.__processingTags) ? card.__processingTags : [];
                    if (!attachedIds().has(String(tag.id || ''))) {
                        card.__processingTags.push({ id: tag.id, name: tag.name });
                        prksProcessingRenderTagList(card);
                    }
                    input.value = '';
                    prksHideInlineComboboxResults(results);
                };
                results.appendChild(div);
            });
        }
        if (typeof prksShowInlineComboboxResults === 'function') {
            prksShowInlineComboboxResults(input, results);
        } else {
            results.classList.remove('hidden');
        }
    };

    const hideResults = () => {
        prksHideInlineComboboxResults(results);
    };
    input.addEventListener('focus', render);
    input.addEventListener('input', render);
    input.addEventListener('blur', () => {
        setTimeout(hideResults, 200);
    });
}

function prksProcessingRenderRoleList(card) {
    const listEl = card.querySelector('[data-role="roles-list"]');
    if (!listEl) return;
    const roles = Array.isArray(card.__processingRoles) ? card.__processingRoles : [];
    if (!roles.length) {
        listEl.innerHTML = '<span class="status-chip-list__empty">No persons linked yet</span>';
        return;
    }
    listEl.innerHTML = roles
        .map(
            (r, idx) => `
            <span class="tag author-tag">
                ${typeof prksIcon === 'function' ? prksIcon('user', { size: 'sm' }) : ''} ${prksProcessingEsc(r.person_name || r.person_id)} (${prksProcessingEsc(r.role_type)})
                <button type="button" class="status-chip-remove" data-action="remove-role" data-remove-role-index="${idx}" aria-label="Remove role link">&times;</button>
            </span>`
        )
        .join(' ');
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(listEl);
}

function prksProcessingAttachPersonCombobox(card) {
    const input = card.querySelector('[data-role="person-search"]');
    const hidden = card.querySelector('[data-role="person-id"]');
    const results = card.querySelector('[data-role="person-results"]');
    if (!input || !hidden || !results) return;
    const render = () => {
        const valRaw = String(input.value || '');
        const val = valRaw.toLowerCase().trim();
        const data = prksProcessingGetPeople();
        const matchFn =
            typeof personMatchesComboboxQuery === 'function'
                ? personMatchesComboboxQuery
                : (p, qq) => {
                      const needle = String(qq || '').trim().toLowerCase();
                      if (!needle) return true;
                      return prksProcessingPersonDisplayName(p).toLowerCase().includes(needle);
                  };
        const filtered = data
            .filter((p) => String(p.id || '').trim())
            .filter((p) => matchFn(p, val))
            .slice(0, 25);

        results.innerHTML = '';
        if (valRaw.trim() && typeof prksQuickCreatePersonForSearchField === 'function') {
            const create = document.createElement('div');
            create.className = 'result-item result-item--create';
            create.textContent = `Quick-create person "${valRaw.trim()}"`;
            create.onmousedown = (e) => {
                e.preventDefault();
                prksHideInlineComboboxResults(results);
                void prksQuickCreatePersonForSearchField(
                    valRaw.trim(),
                    input,
                    hidden,
                    'Quick-created from processing inbox'
                );
            };
            results.appendChild(create);
        }
        if (!filtered.length) {
            if (results.childElementCount === 0) {
                results.innerHTML = '<div class="result-item no-results">No people found</div>';
            }
        } else {
            filtered.forEach((person) => {
                const label = prksProcessingPersonDisplayName(person);
                const div = document.createElement('div');
                div.className = 'result-item result-item--person-pick';
                const primary = document.createElement('div');
                primary.className = 'result-item__primary';
                primary.textContent = label || '(Unnamed)';
                div.appendChild(primary);
                if (typeof formatPersonComboboxSubtitle === 'function') {
                    const sub = formatPersonComboboxSubtitle(person);
                    if (sub) {
                        const secondary = document.createElement('div');
                        secondary.className = 'result-item__secondary';
                        secondary.textContent = sub;
                        div.appendChild(secondary);
                    }
                }
                div.onmousedown = (e) => {
                    e.preventDefault();
                    hidden.value = String(person.id || '');
                    input.value = label || '(Unnamed)';
                    prksHideInlineComboboxResults(results);
                };
                results.appendChild(div);
            });
        }
        if (typeof prksShowInlineComboboxResults === 'function') {
            prksShowInlineComboboxResults(input, results);
        } else {
            results.classList.remove('hidden');
        }
    };
    const hideResults = () => {
        prksHideInlineComboboxResults(results);
    };
    input.addEventListener('focus', render);
    input.addEventListener('input', () => {
        hidden.value = '';
        render();
    });
    input.addEventListener('blur', () => {
        setTimeout(hideResults, 200);
    });
}

function prksProcessingCollectDraft(card) {
    const get = (name) => {
        const el = card.querySelector(`[data-field="${name}"]`);
        return el ? String(el.value || '') : '';
    };
    return {
        title: get('title').trim(),
        status_draft: get('status_draft'),
        abstract: get('abstract'),
        source_url: get('source_url').trim(),
        published_date: prksParsePublishedDateInput(get('published_date')),
        year: get('year').trim(),
        publisher: get('publisher').trim(),
        location: get('location').trim(),
        edition: get('edition').trim(),
        journal: get('journal').trim(),
        volume: get('volume').trim(),
        issue: get('issue').trim(),
        pages: get('pages').trim(),
        isbn: get('isbn').trim(),
        doi: get('doi').trim(),
        doc_type: get('doc_type').trim() || 'article',
        private_notes: get('private_notes'),
        thumb_page: get('thumb_page').trim(),
        target_folder_id: (() => {
            const el = card.querySelector('[data-role="folder-id"]');
            return el ? String(el.value || '').trim() : '';
        })(),
        roles: Array.isArray(card.__processingRoles)
            ? card.__processingRoles.map((r) => ({
                  person_id: String(r.person_id || '').trim(),
                  role_type: String(r.role_type || '').trim(),
              }))
            : [],
        tags: Array.isArray(card.__processingTags)
            ? card.__processingTags.map((t) => ({
                  id: String(t.id || '').trim(),
                  name: String(t.name || '').trim(),
              }))
            : [],
    };
}

function prksProcessingCardHtml(file) {
    const status = String(file.status || 'pending');
    const canImport = status !== 'missing' && status !== 'error';
    const canPreview = !!file.exists && canImport;
    const statusLabel = status.charAt(0).toUpperCase() + status.slice(1);
    const sourceHint = file.exists
        ? 'Source file exists in for_processing.'
        : 'Source file missing from for_processing.';
    const rolesPayload = encodeURIComponent(JSON.stringify(Array.isArray(file.roles) ? file.roles : []));
    const docDtPrefix = prksProcessingDocTypeIdPrefix(file.id);
    const statusSegId = prksProcessingSegIdPrefix(file.id, 'status');
    const roleSegId = prksProcessingSegIdPrefix(file.id, 'role');
    const statusDraft = file.status_draft || 'Not Started';
    const statusSegHtml =
        typeof prksSegmentedControlHtml === 'function'
            ? prksSegmentedControlHtml(
                  statusSegId,
                  'File status',
                  PRKS_PROCESSING_STATUS_LABELS,
                  statusDraft,
                  'status',
                  { dataField: 'status_draft' }
              )
            : `<input type="hidden" data-field="status_draft" value="${prksProcessingEsc(statusDraft)}">`;
    const roleSegHtml =
        typeof prksSegmentedControlHtml === 'function'
            ? prksSegmentedControlHtml(roleSegId, 'Role for linked person', PRKS_PROCESSING_ROLE_TYPES, 'Author', 'roles', {
                  compact: true,
                  dataRole: 'role-type',
                  withRoleIcons: true,
              })
            : `<input type="hidden" data-role="role-type" value="Author">`;
    let docTypeFieldHtml = '';
    if (typeof prksDocTypeMenuShellHtml === 'function') {
        const shell = prksDocTypeMenuShellHtml(docDtPrefix, file.doc_type || 'article', false);
        const shellTagged = shell.replace(
            `id="${docDtPrefix}" name="${docDtPrefix}"`,
            `id="${docDtPrefix}" name="${docDtPrefix}" data-field="doc_type"`
        );
        docTypeFieldHtml = `
                    <div>
                        <label for="${docDtPrefix}-trigger">Document type (BibLaTeX)</label>
                        ${shellTagged}
                    </div>`;
    } else {
        docTypeFieldHtml = `
                    <div>
                        <label>Doc type</label>
                        <input type="text" data-field="doc_type" value="${prksProcessingEsc(file.doc_type || 'article')}" placeholder="article, book, online...">
                    </div>`;
    }
    const publishedDateValue = prksProcessingEsc(
        typeof prksIsoToDdMmYyyy === 'function' ? prksIsoToDdMmYyyy(file.published_date || '') : file.published_date || ''
    );
    const relPath = String(file.rel_path || '');
    const pathTitleAttr = relPath ? ` title="${prksProcessingEsc(relPath)}"` : '';
    return `
        <article class="project-card prks-processing-card" data-processing-id="${prksProcessingEsc(file.id)}" data-processing-roles="${rolesPayload}">
            <header class="prks-processing-card__header">
                <div class="card-title prks-processing-card__title">${prksProcessingEsc(file.filename || file.rel_path || 'PDF')}</div>
                <div class="prks-processing-card__meta">
                    <p class="meta-row"><strong>Path:</strong> <code${pathTitleAttr}>${prksProcessingEsc(relPath)}</code></p>
                    <p class="meta-row"><strong>State:</strong> ${prksProcessingEsc(statusLabel)} · ${prksProcessingEsc(sourceHint)}</p>
                    ${file.last_error ? `<p class="meta-row" style="color: var(--danger-color);"><strong>Error:</strong> ${prksProcessingEsc(file.last_error)}</p>` : ''}
                </div>
            </header>
            <div class="form-pane form-pane--tight prks-processing-card__core">
                <div class="prks-processing-card__section">
                    <div class="prks-processing-card__title-row">
                        <label>Title</label>
                        <input type="text" data-field="title" value="${prksProcessingEsc(file.title || '')}" placeholder="Library title">
                    </div>
                    <div class="prks-processing-card__status-field prks-work-upload-status-field">
                        <label>Status</label>
                        ${statusSegHtml}
                    </div>
                </div>
                <div class="prks-processing-card__section">
                    <div class="prks-processing-card__section-title">Link person to roles</div>
                    <div class="prks-upload-person-stack">
                        <div class="form-row prks-upload-person-stack__search">
                            <div class="prks-combobox-with-action">
                                <div class="tag-add-shell combobox-container tag-add-shell--flush prks-inline-combobox-shell">
                                    <div class="tag-add-shell__field">
                                        ${typeof prksTagSearchIconHtml === 'function' ? prksTagSearchIconHtml() : ''}
                                        <input type="text" class="tag-add-shell__input" data-role="person-search" placeholder="Search person from library…" autocomplete="off" aria-label="Search person">
                                    </div>
                                    <input type="hidden" data-role="person-id" value="">
                                    <div class="combobox-results combobox-results--tag-panel hidden" data-role="person-results"></div>
                                </div>
                            </div>
                            <button type="button" class="ribbon-btn ribbon-btn--sm" data-action="add-role"><span class="ribbon-btn__icon">${typeof prksIcon === 'function' ? prksIcon('link', { size: 'sm' }) : ''}</span><span class="ribbon-btn__label">Link</span></button>
                        </div>
                        <div class="prks-upload-person-stack__roles prks-upload-person-stack__roles--tiles">
                            <div class="prks-upload-role-seg">
                                ${roleSegHtml}
                            </div>
                        </div>
                    </div>
                    <div class="tag-cloud status-chip-list" data-role="roles-list"></div>
                </div>
                <div class="prks-processing-card__section">
                    <p class="tag-add-field__caption">Tags (optional)</p>
                    <div class="tag-add-shell combobox-container tag-add-shell--flush prks-inline-combobox-shell">
                        <div class="tag-add-shell__field">
                            ${typeof prksTagPlusIconHtml === 'function' ? prksTagPlusIconHtml() : (typeof prksTagSearchIconHtml === 'function' ? prksTagSearchIconHtml() : '')}
                            <input type="text" class="tag-add-shell__input" data-role="tag-search" placeholder="Search or create tag…" maxlength="300" autocomplete="off" aria-label="Add tag for processing file">
                        </div>
                        <div class="combobox-results combobox-results--tag-panel hidden" data-role="tag-results"></div>
                    </div>
                    <div class="tag-cloud work-tags-list" data-role="tags-list"></div>
                </div>
                <div class="prks-processing-card__section">
                    <div class="prks-processing-card__section-title">Bibliographic</div>
                    <div class="form-grid-2">
                        <div>
                            <label>Year</label>
                            <input type="text" data-field="year" value="${prksProcessingEsc(file.year || '')}">
                        </div>
                        <div>
                            <label>Published date</label>
                            <input type="text" data-field="published_date" placeholder="dd/mm/yyyy" inputmode="numeric" autocomplete="off" value="${publishedDateValue}">
                        </div>
                    </div>
                    ${docTypeFieldHtml}
                    <div class="form-grid-2">
                        <div>
                            <label>Publisher</label>
                            <input type="text" data-field="publisher" value="${prksProcessingEsc(file.publisher || '')}">
                        </div>
                        <div>
                            <label>Location</label>
                            <input type="text" data-field="location" value="${prksProcessingEsc(file.location || '')}">
                        </div>
                    </div>
                </div>
                <div class="prks-processing-card__section">
                    <label>Folder (optional)</label>
                    <p class="meta-row meta-row--hint" style="margin:0 0 6px 0;">Placed in this folder when you import.</p>
                    <div class="prks-combobox-with-action">
                        <div class="tag-add-shell combobox-container tag-add-shell--flush prks-inline-combobox-shell">
                            <div class="tag-add-shell__field">
                                ${typeof prksTagSearchIconHtml === 'function' ? prksTagSearchIconHtml() : ''}
                                <input type="text" class="tag-add-shell__input" data-role="folder-search" placeholder="Search folder…" autocomplete="off" aria-label="Search folder">
                            </div>
                            <input type="hidden" data-role="folder-id" value="">
                            <div class="combobox-results combobox-results--tag-panel hidden" data-role="folder-results"></div>
                        </div>
                        <button type="button" class="ribbon-btn ribbon-btn--sm" data-action="quick-folder" title="Create new folder" aria-label="Create new folder"><span class="ribbon-btn__icon">${typeof prksIcon === 'function' ? prksIcon('plus', { size: 'sm' }) : ''}</span></button>
                    </div>
                </div>
                <details class="prks-processing-card__more">
                    <summary>More metadata</summary>
                    <div class="prks-processing-card__more-body">
                        <label>Original URL</label>
                        <input type="url" data-field="source_url" value="${prksProcessingEsc(file.source_url || '')}" placeholder="https://...">
                        <div class="form-grid-2">
                            <div>
                                <label>Edition</label>
                                <input type="text" data-field="edition" value="${prksProcessingEsc(file.edition || '')}">
                            </div>
                            <div>
                                <label>Journal</label>
                                <input type="text" data-field="journal" value="${prksProcessingEsc(file.journal || '')}">
                            </div>
                        </div>
                        <div class="form-grid-2">
                            <div>
                                <label>Volume</label>
                                <input type="text" data-field="volume" value="${prksProcessingEsc(file.volume || '')}">
                            </div>
                            <div>
                                <label>Issue</label>
                                <input type="text" data-field="issue" value="${prksProcessingEsc(file.issue || '')}">
                            </div>
                        </div>
                        <div class="form-grid-2">
                            <div>
                                <label>Pages</label>
                                <input type="text" data-field="pages" value="${prksProcessingEsc(file.pages || '')}">
                            </div>
                            <div>
                                <label>ISBN</label>
                                <input type="text" data-field="isbn" value="${prksProcessingEsc(file.isbn || '')}">
                            </div>
                        </div>
                        <div class="form-grid-2">
                            <div>
                                <label>DOI</label>
                                <input type="text" data-field="doi" value="${prksProcessingEsc(file.doi || '')}">
                            </div>
                            <div>
                                <label>Thumbnail page</label>
                                <input type="number" min="1" step="1" data-field="thumb_page" value="${prksProcessingEsc(file.thumb_page || '')}">
                            </div>
                        </div>
                        <label>Abstract</label>
                        <textarea class="textarea-sm" data-field="abstract">${prksProcessingEsc(file.abstract || '')}</textarea>
                        <label>Private notes</label>
                        <textarea class="textarea-sm" data-field="private_notes">${prksProcessingEsc(file.private_notes || '')}</textarea>
                    </div>
                </details>
            </div>
            <div class="form-actions prks-processing-card__actions">
                <button type="button" class="ribbon-btn form-actions__btn form-actions__btn--secondary" data-action="preview"${canPreview ? '' : ' disabled'}>Preview</button>
                <button type="button" class="ribbon-btn form-actions__btn form-actions__btn--secondary" data-action="save">Save metadata</button>
                <button type="button" class="add-new-btn form-actions__btn form-actions__btn--primary" data-action="import"${canImport ? '' : ' disabled'}>Import to library</button>
            </div>
            <p class="meta-row prks-processing-card__message" data-role="message" aria-live="polite"></p>
        </article>
    `;
}

async function prksRenderProcessingFilesPageWithFetch(container, options = {}) {
    const [items, people, folders, tags] = await Promise.all([
        fetchProcessingFiles(options),
        fetchPersons(),
        fetchFolders(),
        fetchTags({ used: false }),
    ]);
    window.__prksProcessingPeople = Array.isArray(people) ? people : [];
    window.__prksProcessingFolders = Array.isArray(folders) ? folders : [];
    window.__prksProcessingTagsCache = Array.isArray(tags) ? tags : [];
    window.__prksAllTagsCache = window.__prksProcessingTagsCache;
    try {
        allFolders = window.__prksProcessingFolders;
        window.allFolders = allFolders;
    } catch (_e) {}
    if (!window.__prksRouteSidebar || typeof window.__prksRouteSidebar !== 'object') {
        window.__prksRouteSidebar = {};
    }
    window.__prksRouteSidebar.pendingCount = Array.isArray(items) ? items.length : 0;
    renderProcessingFilesPage(items, container);
}

function renderProcessingFilesPage(items, container) {
    const list = Array.isArray(items) ? items : [];
    const DEFAULT_VISIBLE = 25;
    const currentVisibleRaw = Number(container && container.dataset ? container.dataset.prksProcessingVisibleCount : 0);
    const visibleCount = Number.isFinite(currentVisibleRaw) && currentVisibleRaw > 0
        ? Math.min(list.length, Math.floor(currentVisibleRaw))
        : Math.min(list.length, DEFAULT_VISIBLE);
    if (container && container.dataset) container.dataset.prksProcessingVisibleCount = String(visibleCount);
    const visibleList = list.slice(0, visibleCount);
    const remainingCount = Math.max(0, list.length - visibleCount);
    const idToFile = new Map(list.map((f) => [String(f.id || ''), f]));
    const cards = visibleList.map(prksProcessingCardHtml).join('');
    container.innerHTML = `
        <div class="page-header" style="gap:12px;flex-wrap:wrap;">
            <h2>Files for Processing</h2>
            <div style="flex:1 1 auto;"></div>
            <button type="button" class="ribbon-btn" id="prks-processing-refresh">Refresh folder scan</button>
        </div>
        <p class="meta-row" style="margin:0 0 14px 0;">
            Inbox reads PDFs recursively from <code>/data/for_processing</code>. Files here stay out of library search and graph until imported.
        </p>
        ${remainingCount > 0 ? `<p class="meta-row" id="prks-processing-visible-note">Showing first ${visibleCount} of ${list.length} files to keep page responsive.</p>` : ''}
        <div class="prks-processing-main-layout">
            <div class="list-view prks-processing-main-layout__list" id="prks-processing-list">
                ${cards || '<p class="meta-row">No PDF files waiting for processing.</p>'}
                ${remainingCount > 0 ? `<div class="prks-processing-load-more-row"><button type="button" class="ribbon-btn" id="prks-processing-load-more">Load ${Math.min(25, remainingCount)} more</button></div>` : ''}
            </div>
            <aside class="prks-processing-inline-preview" id="prks-processing-inline-preview">
                <h3 class="prks-processing-inline-preview__title">PDF Preview</h3>
                <p class="prks-processing-inline-preview__file" id="prks-processing-inline-preview-file">No file selected</p>
                <p class="prks-processing-inline-preview__empty" id="prks-processing-inline-preview-empty">Click Preview on file card to open PDF here.</p>
                <div class="prks-processing-inline-preview__frame-shell hidden" id="prks-processing-inline-preview-frame-shell">
                    <iframe class="prks-processing-inline-preview__frame hidden" id="prks-processing-inline-preview-frame" title="PDF preview" loading="lazy" referrerpolicy="no-referrer"></iframe>
                </div>
                <p class="meta-row"><a class="route-sidebar__link hidden" id="prks-processing-inline-preview-link" href="#" target="_blank" rel="noopener">Open preview in new tab</a></p>
            </aside>
        </div>
    `;

    const refreshBtn = container.querySelector('#prks-processing-refresh');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', async () => {
            refreshBtn.disabled = true;
            const old = refreshBtn.textContent;
            refreshBtn.textContent = 'Scanning...';
            try {
                await prksRenderProcessingFilesPageWithFetch(container, { rescan: true });
            } finally {
                refreshBtn.disabled = false;
                refreshBtn.textContent = old;
            }
        });
    }
    const loadMoreBtn = container.querySelector('#prks-processing-load-more');
    if (loadMoreBtn) {
        loadMoreBtn.addEventListener('click', () => {
            const prevVisible = Number(container.dataset.prksProcessingVisibleCount || visibleCount);
            const nextVisible = Math.min(list.length, (Number.isFinite(prevVisible) ? prevVisible : visibleCount) + 25);
            container.dataset.prksProcessingVisibleCount = String(nextVisible);
            renderProcessingFilesPage(list, container);
        });
    }

    const inlinePreviewFile = container.querySelector('#prks-processing-inline-preview-file');
    const inlinePreviewEmpty = container.querySelector('#prks-processing-inline-preview-empty');
    const inlinePreviewFrameShell = container.querySelector('#prks-processing-inline-preview-frame-shell');
    const inlinePreviewFrame = container.querySelector('#prks-processing-inline-preview-frame');
    const inlinePreviewLink = container.querySelector('#prks-processing-inline-preview-link');
    const layoutEl = container.querySelector('.prks-processing-main-layout');
    const setInlinePreview = (fileRow, fileId, anchorCard) => {
        const canPreview = !!(fileRow && fileRow.exists && fileRow.status !== 'missing' && fileRow.status !== 'error');
        const src = `/api/processing-files/${encodeURIComponent(String(fileId || ''))}/pdf`;
        const label = String((fileRow && (fileRow.filename || fileRow.rel_path)) || 'Selected file');
        if (inlinePreviewFile) inlinePreviewFile.textContent = label;
        if (!canPreview) {
            if (layoutEl) layoutEl.removeAttribute('data-preview-for');
            prksProcessingPlacePreviewAfterList();
            if (inlinePreviewEmpty) {
                inlinePreviewEmpty.textContent = 'Preview unavailable for this file.';
                inlinePreviewEmpty.classList.remove('hidden');
            }
            if (inlinePreviewFrameShell) inlinePreviewFrameShell.classList.add('hidden');
            if (inlinePreviewFrame) {
                inlinePreviewFrame.classList.add('hidden');
                inlinePreviewFrame.removeAttribute('src');
            }
            if (inlinePreviewLink) inlinePreviewLink.classList.add('hidden');
            return canPreview;
        }
        if (inlinePreviewFrameShell) inlinePreviewFrameShell.classList.remove('hidden');
        if (inlinePreviewFrame) {
            inlinePreviewFrame.setAttribute('src', src);
            inlinePreviewFrame.setAttribute('title', `PDF preview for ${label}`);
            inlinePreviewFrame.classList.remove('hidden');
        }
        if (inlinePreviewEmpty) inlinePreviewEmpty.classList.add('hidden');
        if (inlinePreviewLink) {
            inlinePreviewLink.setAttribute('href', src);
            inlinePreviewLink.classList.remove('hidden');
        }
        if (prksProcessingPreviewStacked() && anchorCard) {
            if (layoutEl) layoutEl.setAttribute('data-preview-for', String(fileId || ''));
            prksProcessingPlacePreviewAfterCard(anchorCard);
        } else {
            if (layoutEl) layoutEl.removeAttribute('data-preview-for');
            prksProcessingPlacePreviewAfterList();
        }
        if (canPreview && prksProcessingPreviewStacked()) {
            const root = document.getElementById('prks-processing-inline-preview');
            if (root) {
                requestAnimationFrame(() => {
                    root.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                });
            }
        }
        return canPreview;
    };

    prksProcessingEnsureResizeSync();

    container.querySelectorAll('[data-processing-id]').forEach((card) => {
        const fileId = card.getAttribute('data-processing-id');
        const fileRow = idToFile.get(String(fileId || ''));
        const msgEl = card.querySelector('[data-role="message"]');
        const previewBtn = card.querySelector('[data-action="preview"]');
        const saveBtn = card.querySelector('[data-action="save"]');
        const importBtn = card.querySelector('[data-action="import"]');
        const addRoleBtn = card.querySelector('[data-action="add-role"]');
        const roleTypeEl = card.querySelector('[data-role="role-type"]');
        const personIdEl = card.querySelector('[data-role="person-id"]');
        const personSearchEl = card.querySelector('[data-role="person-search"]');
        let sourceRoles = [];
        const rolesAttr = card.getAttribute('data-processing-roles');
        if (rolesAttr) {
            try {
                const parsed = JSON.parse(decodeURIComponent(rolesAttr));
                sourceRoles = Array.isArray(parsed) ? parsed : [];
            } catch {
                sourceRoles = [];
            }
        }
        card.__processingRoles = sourceRoles.map((r) => ({
            person_id: String(r.person_id || ''),
            person_name: String(r.person_name || r.person_id || ''),
            role_type: String(r.role_type || 'Author'),
        }));
        const sourceTags = fileRow && Array.isArray(fileRow.tags) ? fileRow.tags : [];
        card.__processingTags = sourceTags.map((t) => ({
            id: String(t.id || ''),
            name: String(t.name || ''),
        }));
        prksProcessingRenderRoleList(card);
        prksProcessingRenderTagList(card);
        prksProcessingAttachPersonCombobox(card);
        prksProcessingAttachTagCombobox(card);
        const folderHidden = card.querySelector('[data-role="folder-id"]');
        const folderSearch = card.querySelector('[data-role="folder-search"]');
        const tf = fileRow && String(fileRow.target_folder_id || '').trim();
        if (tf && folderHidden) {
            folderHidden.value = tf;
            const fo = prksProcessingGetFolders().find((x) => String(x.id) === tf);
            if (folderSearch && fo) folderSearch.value = String(fo.title || '').trim();
        }
        prksProcessingAttachFolderCombobox(card);
        const quickFolderBtn = card.querySelector('[data-action="quick-folder"]');
        if (quickFolderBtn) {
            quickFolderBtn.addEventListener('click', () => {
                void prksProcessingQuickCreateFolder(card);
            });
        }
        const dtPrefix = prksProcessingDocTypeIdPrefix(fileId);
        if (typeof initPrksDocTypeMenu === 'function' && document.getElementById(dtPrefix)) {
            initPrksDocTypeMenu(dtPrefix, {});
        }
        const statusSegId = prksProcessingSegIdPrefix(fileId, 'status');
        const roleSegId = prksProcessingSegIdPrefix(fileId, 'role');
        if (typeof prksBindSegmentedHidden === 'function') {
            prksBindSegmentedHidden(statusSegId);
            prksBindSegmentedHidden(roleSegId);
        }
        if (addRoleBtn && roleTypeEl && personIdEl && personSearchEl) {
            addRoleBtn.addEventListener('click', () => {
                const personId = String(personIdEl.value || '').trim();
                if (!personId) {
                    if (msgEl) msgEl.textContent = 'Pick person from results first.';
                    return;
                }
                const roleType = String(roleTypeEl.value || 'Author').trim() || 'Author';
                const person = prksProcessingGetPeople().find((p) => String(p.id || '') === personId);
                const personName = person ? prksProcessingPersonDisplayName(person) : String(personSearchEl.value || personId).trim();
                card.__processingRoles = Array.isArray(card.__processingRoles) ? card.__processingRoles : [];
                const hasDup =
                    typeof prksWorkHasRoleLink === 'function'
                        ? prksWorkHasRoleLink(card.__processingRoles, personId, roleType)
                        : card.__processingRoles.some(
                              (r) =>
                                  String(r.person_id || '') === personId &&
                                  String(r.role_type || '') === roleType
                          );
                if (hasDup) {
                    if (msgEl) msgEl.textContent = `Already linked as ${roleType}.`;
                    return;
                }
                card.__processingRoles.push({ person_id: personId, person_name: personName, role_type: roleType });
                prksProcessingRenderRoleList(card);
                personIdEl.value = '';
                personSearchEl.value = '';
                if (msgEl) msgEl.textContent = '';
            });
        }
        card.addEventListener('click', (ev) => {
            const roleBtn = ev.target && ev.target.closest ? ev.target.closest('[data-action="remove-role"]') : null;
            if (roleBtn) {
                const idx = Number(roleBtn.getAttribute('data-remove-role-index'));
                if (!Number.isFinite(idx)) return;
                card.__processingRoles = Array.isArray(card.__processingRoles) ? card.__processingRoles : [];
                card.__processingRoles.splice(idx, 1);
                prksProcessingRenderRoleList(card);
                return;
            }
            const tagBtn = ev.target && ev.target.closest ? ev.target.closest('[data-action="remove-tag"]') : null;
            if (!tagBtn) return;
            const tagIdx = Number(tagBtn.getAttribute('data-remove-tag-index'));
            if (!Number.isFinite(tagIdx)) return;
            card.__processingTags = Array.isArray(card.__processingTags) ? card.__processingTags : [];
            card.__processingTags.splice(tagIdx, 1);
            prksProcessingRenderTagList(card);
        });
        if (previewBtn) {
            previewBtn.addEventListener('click', () => {
                const canPreview = setInlinePreview(fileRow, fileId, card);
                if (msgEl) {
                    if (!canPreview) {
                        msgEl.textContent = 'Preview unavailable for this file.';
                    } else if (prksProcessingPreviewStacked()) {
                        msgEl.textContent = 'Preview below this card.';
                    } else {
                        msgEl.textContent = 'Preview opened on the right.';
                    }
                }
            });
        }
        if (saveBtn) {
            saveBtn.addEventListener('click', async () => {
                const payload = prksProcessingCollectDraft(card);
                try {
                    saveBtn.disabled = true;
                    if (msgEl) msgEl.textContent = 'Saving...';
                    await patchProcessingFile(fileId, payload);
                    if (msgEl) msgEl.textContent = 'Saved.';
                } catch (e) {
                    if (msgEl) msgEl.textContent = e && e.message ? e.message : 'Save failed.';
                } finally {
                    saveBtn.disabled = false;
                }
            });
        }
        if (importBtn) {
            importBtn.addEventListener('click', async () => {
                try {
                    importBtn.disabled = true;
                    if (saveBtn) saveBtn.disabled = true;
                    if (msgEl) msgEl.textContent = 'Importing...';
                    const payload = prksProcessingCollectDraft(card);
                    await patchProcessingFile(fileId, payload);
                    await importProcessingFile(fileId);
                    if (msgEl) msgEl.textContent = 'Imported to library.';
                    await prksRenderProcessingFilesPageWithFetch(container, { rescan: true });
                } catch (e) {
                    if (msgEl) msgEl.textContent = e && e.message ? e.message : 'Import failed.';
                    importBtn.disabled = false;
                    if (saveBtn) saveBtn.disabled = false;
                }
            });
        }
    });
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(container);
}
