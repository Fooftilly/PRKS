function prksProcessingDocTypeIdPrefix(processingId) {
    return `prks-pf-dt-${String(processingId || 'x').replace(/[^a-zA-Z0-9_-]/g, '_')}`;
}

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

const PRKS_PROCESSING_ROLE_TYPES = [
    'Author',
    'Editor',
    'Reviewer',
    'Translator',
    'Introduction',
    'Foreword',
    'Afterword',
];

function prksProcessingStatusOptions(selected) {
    const statuses = ['Not Started', 'Planned', 'In Progress', 'Paused', 'Completed'];
    return statuses
        .map((s) => `<option value="${prksProcessingEsc(s)}"${s === selected ? ' selected' : ''}>${prksProcessingEsc(s)}</option>`)
        .join('');
}

function prksProcessingRoleTypeOptions(selected) {
    return PRKS_PROCESSING_ROLE_TYPES
        .map((s) => `<option value="${prksProcessingEsc(s)}"${s === selected ? ' selected' : ''}>${prksProcessingEsc(s)}</option>`)
        .join('');
}

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
                    results.classList.add('hidden');
                };
                results.appendChild(div);
            });
        }
        results.classList.remove('hidden');
    };
    input.addEventListener('focus', render);
    input.addEventListener('input', () => {
        hidden.value = '';
        render();
    });
    input.addEventListener('blur', () => {
        setTimeout(() => results.classList.add('hidden'), 180);
    });
}

async function prksProcessingQuickCreateFolder(card) {
    const input = card.querySelector('[data-role="folder-search"]');
    const hidden = card.querySelector('[data-role="folder-id"]');
    const results = card.querySelector('[data-role="folder-results"]');
    const title = String(input?.value || '').trim();
    if (!title) {
        alert('Enter folder title in search field first.');
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
            alert(data.error || 'Could not create folder.');
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
        if (results) results.classList.add('hidden');
    } catch (e) {
        console.error(e);
        alert('Could not create folder.');
    }
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
                👤 ${prksProcessingEsc(r.person_name || r.person_id)} (${prksProcessingEsc(r.role_type)})
                <button type="button" class="status-chip-remove" data-action="remove-role" data-remove-role-index="${idx}" aria-label="Remove role link">&times;</button>
            </span>`
        )
        .join(' ');
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
                results.classList.add('hidden');
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
                    results.classList.add('hidden');
                };
                results.appendChild(div);
            });
        }
        results.classList.remove('hidden');
    };
    input.addEventListener('focus', render);
    input.addEventListener('input', () => {
        hidden.value = '';
        render();
    });
    input.addEventListener('blur', () => {
        setTimeout(() => results.classList.add('hidden'), 180);
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
        published_date: get('published_date').trim(),
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
    const previewUrl = `/api/processing-files/${encodeURIComponent(String(file.id || ''))}/pdf`;
    const rolesPayload = encodeURIComponent(JSON.stringify(Array.isArray(file.roles) ? file.roles : []));
    const docDtPrefix = prksProcessingDocTypeIdPrefix(file.id);
    let docTypeMoreHtml = '';
    if (typeof prksDocTypeMenuShellHtml === 'function') {
        const shell = prksDocTypeMenuShellHtml(docDtPrefix, file.doc_type || 'article', false);
        const shellTagged = shell.replace(
            `id="${docDtPrefix}" name="${docDtPrefix}"`,
            `id="${docDtPrefix}" name="${docDtPrefix}" data-field="doc_type"`
        );
        docTypeMoreHtml = `
                            <div>
                                <label for="${docDtPrefix}-trigger">Document type (BibLaTeX)</label>
                                ${shellTagged}
                            </div>`;
    } else {
        docTypeMoreHtml = `
                            <div>
                                <label>Doc type</label>
                                <input type="text" data-field="doc_type" value="${prksProcessingEsc(file.doc_type || 'article')}" placeholder="article, book, online...">
                            </div>`;
    }
    return `
        <article class="project-card prks-processing-card" data-processing-id="${prksProcessingEsc(file.id)}" data-processing-roles="${rolesPayload}">
            <div class="card-title prks-processing-card__title">${prksProcessingEsc(file.filename || file.rel_path || 'PDF')}</div>
            <p class="meta-row"><strong>Path:</strong> <code>${prksProcessingEsc(file.rel_path || '')}</code></p>
            <p class="meta-row"><strong>State:</strong> ${prksProcessingEsc(statusLabel)} · ${prksProcessingEsc(sourceHint)}</p>
            ${file.last_error ? `<p class="meta-row" style="color: var(--danger-color);"><strong>Error:</strong> ${prksProcessingEsc(file.last_error)}</p>` : ''}
            <div class="form-pane form-pane--tight prks-processing-card__core">
                <div class="form-grid-2">
                    <div>
                        <label>Title</label>
                        <input type="text" data-field="title" value="${prksProcessingEsc(file.title || '')}" placeholder="Defaults to file name">
                    </div>
                    <div>
                        <label>Status</label>
                        <select data-field="status_draft">${prksProcessingStatusOptions(file.status_draft || 'Not Started')}</select>
                    </div>
                </div>
                <label>Link person to roles</label>
                <div class="prks-processing-role-picker">
                    <div class="combobox-container">
                        <div class="tag-add-shell">
                            <div class="tag-add-shell__field">
                                <span class="tag-add-shell__icon" aria-hidden="true">🔍</span>
                                <input type="text" class="tag-add-shell__input" data-role="person-search" placeholder="Search person from library…" autocomplete="off">
                            </div>
                        </div>
                        <input type="hidden" data-role="person-id" value="">
                        <div class="combobox-results hidden" data-role="person-results"></div>
                    </div>
                    <div>
                        <select data-role="role-type">${prksProcessingRoleTypeOptions('Author')}</select>
                    </div>
                    <div>
                        <button type="button" class="add-new-btn ribbon-btn--sm" data-action="add-role">+ Link</button>
                    </div>
                </div>
                <div class="tag-cloud status-chip-list" data-role="roles-list"></div>
                <div>
                    <label>Year</label>
                    <input type="text" data-field="year" value="${prksProcessingEsc(file.year || '')}">
                </div>
                <label>Folder (optional)</label>
                <p class="meta-row meta-row--hint" style="margin:0 0 6px 0;">Placed in this folder when you import.</p>
                <div class="prks-combobox-with-action">
                    <div class="combobox-container">
                        <div class="tag-add-shell">
                            <div class="tag-add-shell__field">
                                <span class="tag-add-shell__icon" aria-hidden="true">🔍</span>
                                <input type="text" class="tag-add-shell__input" data-role="folder-search" placeholder="Search folder…" autocomplete="off" aria-label="Search folder">
                            </div>
                        </div>
                        <input type="hidden" data-role="folder-id" value="">
                        <div class="combobox-results hidden" data-role="folder-results"></div>
                    </div>
                    <button type="button" class="ribbon-btn ribbon-btn--sm" data-action="quick-folder" title="Create new folder">+</button>
                </div>
                <details class="prks-processing-card__more">
                    <summary>More metadata</summary>
                    <div class="prks-processing-card__more-body">
                        <div class="form-grid-2">
                            <div>
                                <label>Published date</label>
                                <input type="date" data-field="published_date" value="${prksProcessingEsc(file.published_date || '')}">
                            </div>
                            ${docTypeMoreHtml}
                        </div>
                        <label>Original URL</label>
                        <input type="url" data-field="source_url" value="${prksProcessingEsc(file.source_url || '')}" placeholder="https://...">
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
            <div class="prks-processing-card__actions">
                <button type="button" class="ribbon-btn" data-action="save">Save metadata</button>
                <button type="button" class="add-new-btn" data-action="import"${canImport ? '' : ' disabled'}>Import to library</button>
                <span class="meta-row" data-role="message" aria-live="polite"></span>
            </div>
            <details class="prks-processing-card__preview-wrap" data-role="preview-wrap"${canPreview ? '' : ' hidden'}>
                <summary>Open inline preview</summary>
                <div class="prks-processing-card__preview-body">
                    <iframe class="prks-processing-card__preview" data-role="preview-frame" title="PDF preview for ${prksProcessingEsc(file.filename || 'file')}" loading="lazy" referrerpolicy="no-referrer"></iframe>
                    <p class="meta-row"><a class="route-sidebar__link" href="${prksProcessingEsc(previewUrl)}" target="_blank" rel="noopener">Open preview in new tab</a></p>
                </div>
            </details>
        </article>
    `;
}

async function prksRenderProcessingFilesPageWithFetch(container, options = {}) {
    const [items, people, folders] = await Promise.all([
        fetchProcessingFiles(options),
        fetchPersons(),
        fetchFolders(),
    ]);
    window.__prksProcessingPeople = Array.isArray(people) ? people : [];
    window.__prksProcessingFolders = Array.isArray(folders) ? folders : [];
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
    const idToFile = new Map(list.map((f) => [String(f.id || ''), f]));
    const cards = list.map(prksProcessingCardHtml).join('');
    container.innerHTML = `
        <div class="page-header" style="gap:12px;flex-wrap:wrap;">
            <h2>Files for Processing</h2>
            <div style="flex:1 1 auto;"></div>
            <button type="button" class="ribbon-btn" id="prks-processing-refresh">Refresh folder scan</button>
        </div>
        <p class="meta-row" style="margin:0 0 14px 0;">
            Inbox reads PDFs recursively from <code>/data/for_processing</code>. Files here stay out of library search and graph until imported.
        </p>
        <div class="list-view" id="prks-processing-list">
            ${cards || '<p class="meta-row">No PDF files waiting for processing.</p>'}
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

    container.querySelectorAll('[data-processing-id]').forEach((card) => {
        const fileId = card.getAttribute('data-processing-id');
        const fileRow = idToFile.get(String(fileId || ''));
        const msgEl = card.querySelector('[data-role="message"]');
        const saveBtn = card.querySelector('[data-action="save"]');
        const importBtn = card.querySelector('[data-action="import"]');
        const addRoleBtn = card.querySelector('[data-action="add-role"]');
        const roleTypeEl = card.querySelector('[data-role="role-type"]');
        const personIdEl = card.querySelector('[data-role="person-id"]');
        const personSearchEl = card.querySelector('[data-role="person-search"]');
        const previewWrap = card.querySelector('[data-role="preview-wrap"]');
        const previewFrame = card.querySelector('[data-role="preview-frame"]');
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
        prksProcessingRenderRoleList(card);
        prksProcessingAttachPersonCombobox(card);
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
                card.__processingRoles.push({ person_id: personId, person_name: personName, role_type: roleType });
                prksProcessingRenderRoleList(card);
                personIdEl.value = '';
                personSearchEl.value = '';
                if (msgEl) msgEl.textContent = '';
            });
        }
        card.addEventListener('click', (ev) => {
            const btn = ev.target && ev.target.closest ? ev.target.closest('[data-action="remove-role"]') : null;
            if (!btn) return;
            const idxRaw = btn.getAttribute('data-remove-role-index');
            const idx = Number(idxRaw);
            if (!Number.isFinite(idx)) return;
            card.__processingRoles = Array.isArray(card.__processingRoles) ? card.__processingRoles : [];
            card.__processingRoles.splice(idx, 1);
            prksProcessingRenderRoleList(card);
        });
        if (previewWrap && previewFrame) {
            const ensurePreviewSrc = () => {
                const src = `/api/processing-files/${encodeURIComponent(String(fileId || ''))}/pdf`;
                if (!previewFrame.getAttribute('src')) {
                    previewFrame.setAttribute('src', src);
                }
            };
            previewWrap.addEventListener('toggle', () => {
                if (previewWrap.open) ensurePreviewSrc();
            });
            if (previewWrap.open) ensurePreviewSrc();
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
}
