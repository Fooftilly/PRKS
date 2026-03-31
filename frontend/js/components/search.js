function searchEscapeHtml(s) {
    if (typeof window.prksEscapeHtml === 'function') return window.prksEscapeHtml(s);
    if (s == null || s === '') return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function renderRecent(works, container) {
    let html = `<div class="page-header"><h2>⏱ Recently Opened</h2></div><div class="card-grid">`;
    if (works && works.length > 0) {
        works.forEach(w => {
            let dateStr = w.last_opened_at ? new Date(w.last_opened_at).toLocaleString() : 'Unknown';
            const subtitle = `Last opened: ${dateStr}`;
            html += typeof prksWorkCardHtml === 'function' ? prksWorkCardHtml(w, { subtitle }) : '';
        });
    } else {
        html += '<p class="prks-inline-message">No recently opened documents found.</p>';
    }
    html += `</div>`;
    container.innerHTML = html;
}

function prksRunSearchFromForm() {
    const qv = (document.getElementById('search-q-input') || {}).value.trim() || '';
    const av = (document.getElementById('search-author-input') || {}).value.trim() || '';
    const pv = (document.getElementById('search-publisher-input') || {}).value.trim() || '';
    if (!qv && !av && !pv) return;
    const p = new URLSearchParams();
    if (qv) p.set('q', qv);
    if (av) p.set('author', av);
    if (pv) p.set('publisher', pv);
    window.location.hash = '#/search?' + p.toString();
}

function renderSearch(results, query, container, options = {}) {
    const tag = options.tag || '';
    const author = options.author || '';
    const publisher = options.publisher || '';
    let title;
    if (tag) {
        title = `Files tagged “${searchEscapeHtml(tag)}”`;
    } else if (author && query && publisher) {
        title = `Search: “${searchEscapeHtml(query)}” · author “${searchEscapeHtml(author)}” · publisher “${searchEscapeHtml(publisher)}”`;
    } else if (author && publisher && !query) {
        title = `Author “${searchEscapeHtml(author)}” · publisher “${searchEscapeHtml(publisher)}”`;
    } else if (publisher && query && !author) {
        title = `Search: “${searchEscapeHtml(query)}” · publisher “${searchEscapeHtml(publisher)}”`;
    } else if (publisher && !query && !author) {
        title = `Files with publisher matching “${searchEscapeHtml(publisher)}”`;
    } else if (author && query) {
        title = `Search: “${searchEscapeHtml(query)}” · author “${searchEscapeHtml(author)}”`;
    } else if (author) {
        title = `Files with author matching “${searchEscapeHtml(author)}”`;
    } else {
        title = `Search results for “${searchEscapeHtml(query)}”`;
    }
    const emptyMsg = tag
        ? 'No files have this tag yet.'
        : 'No results found matching your query.';
    let html = `<div class="page-header page-header--search"><h2>${title}</h2>`;
    if (!tag) {
        const qEsc = searchEscapeHtml(query);
        const aEsc = searchEscapeHtml(author);
        const pEsc = searchEscapeHtml(publisher);
        html += `
            <div class="search-advanced" role="search">
                <div class="search-advanced__row">
                    <label class="search-advanced__label" for="search-q-input">Keywords</label>
                    <div class="tag-add-shell">
                        <div class="tag-add-shell__field">
                            <span class="tag-add-shell__icon" aria-hidden="true">🔍</span>
                            <input type="search" id="search-q-input" class="tag-add-shell__input" value="${qEsc}" placeholder="Title, notes, abstract, numbers…" maxlength="500" autocomplete="off" aria-label="Search keywords">
                        </div>
                    </div>
                </div>
                <div class="search-advanced__row">
                    <label class="search-advanced__label" for="search-author-input">Author</label>
                    <div class="tag-add-shell">
                        <div class="tag-add-shell__field">
                            <span class="tag-add-shell__icon" aria-hidden="true">🔍</span>
                            <input type="search" id="search-author-input" class="tag-add-shell__input" value="${aEsc}" placeholder="Name in metadata or linked person…" maxlength="200" autocomplete="off" aria-label="Search by author">
                        </div>
                    </div>
                </div>
                <div class="search-advanced__row">
                    <label class="search-advanced__label" for="search-publisher-input">Publisher</label>
                    <div class="tag-add-shell">
                        <div class="tag-add-shell__field">
                            <span class="tag-add-shell__icon" aria-hidden="true">🔍</span>
                            <input type="search" id="search-publisher-input" class="tag-add-shell__input" value="${pEsc}" placeholder="Publisher field; alternate names from Publishers page…" maxlength="200" autocomplete="off" aria-label="Search by publisher">
                        </div>
                    </div>
                </div>
                <button type="button" class="ribbon-btn search-advanced__submit" id="search-run-btn">Search</button>
            </div>`;
    }
    html += `</div><div class="card-grid">`;
    if (results && results.length > 0) {
        results.forEach(w => {
            const subtitle = w.abstract ? w.abstract.substring(0, 100) + '…' : '';
            html += typeof prksWorkCardHtml === 'function' ? prksWorkCardHtml(w, { subtitle }) : '';
        });
    } else {
        html += `<p class="prks-inline-message">${emptyMsg}</p>`;
    }
    html += `</div>`;
    container.innerHTML = html;
    if (!tag) {
        const runBtn = document.getElementById('search-run-btn');
        const qIn = document.getElementById('search-q-input');
        const aIn = document.getElementById('search-author-input');
        const pIn = document.getElementById('search-publisher-input');
        if (runBtn) runBtn.onclick = () => prksRunSearchFromForm();
        const onEnter = (e) => {
            if (e.key === 'Enter') prksRunSearchFromForm();
        };
        if (qIn) qIn.onkeydown = onEnter;
        if (aIn) aIn.onkeydown = onEnter;
        if (pIn) pIn.onkeydown = onEnter;
    }
}
