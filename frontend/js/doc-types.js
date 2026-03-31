/**
 * BibTeX-aligned document types: stored value = @entry type. Colors shared by badges and graph nodes.
 */
const PRKS_DOC_TYPES = [
    { value: 'article', label: 'Article', color: '#3b82f6', border: '#1d4ed8' },
    { value: 'book', label: 'Book', color: '#a855f7', border: '#6d28d9' },
    { value: 'booklet', label: 'Booklet', color: '#c084fc', border: '#7c3aed' },
    { value: 'inbook', label: 'Inbook (chapter)', color: '#8b5cf6', border: '#5b21b6' },
    { value: 'incollection', label: 'Incollection', color: '#6366f1', border: '#4338ca' },
    { value: 'inproceedings', label: 'In proceedings', color: '#0ea5e9', border: '#0369a1' },
    { value: 'proceedings', label: 'Proceedings', color: '#06b6d4', border: '#0e7490' },
    { value: 'online', label: 'Online', color: '#22c55e', border: '#15803d' },
    { value: 'manual', label: 'Manual', color: '#64748b', border: '#334155' },
    { value: 'mastersthesis', label: "Master's thesis", color: '#f59e0b', border: '#b45309' },
    { value: 'phdthesis', label: 'PhD thesis', color: '#d97706', border: '#92400e' },
    { value: 'techreport', label: 'Technical report', color: '#10b981', border: '#047857' },
    { value: 'unpublished', label: 'Unpublished', color: '#94a3b8', border: '#475569' },
    { value: 'misc', label: 'Misc', color: '#78716c', border: '#44403c' },
];

const PRKS_DOC_TYPE_BY_VALUE = Object.fromEntries(PRKS_DOC_TYPES.map((d) => [d.value, d]));

function prksNormalizeDocType(raw) {
    if (raw == null || String(raw).trim() === '') return 'misc';
    const s = String(raw).trim().toLowerCase();
    return PRKS_DOC_TYPE_BY_VALUE[s] ? s : 'misc';
}

function prksDocTypeMeta(value) {
    const v = prksNormalizeDocType(value);
    return PRKS_DOC_TYPE_BY_VALUE[v] || PRKS_DOC_TYPE_BY_VALUE.misc;
}

function prksEscapeDocTypeAttr(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/"/g, '&quot;');
}

function prksDocTypeBadgeHtml(docType) {
    const m = prksDocTypeMeta(docType);
    const label = prksEscapeDocTypeAttr(m.label);
    return (
        '<span class="doc-type-badge" style="background:' +
        m.color +
        ';border-color:' +
        m.border +
        ';" title="BibTeX type: @' +
        prksEscapeDocTypeAttr(m.value) +
        '">' +
        label +
        '</span>'
    );
}

/** vis-network `groups` option: node.group should match doc_type value */
function prksDocTypeVisGroups() {
    const out = {};
    for (const d of PRKS_DOC_TYPES) {
        out[d.value] = {
            color: { background: d.color, border: d.border, highlight: { background: d.color, border: d.border } },
            /* Do not set group font color: it applies to file title labels on the canvas; white on light surface hid them. */
        };
    }
    return out;
}

/**
 * Markup for custom doc-type menu (hidden value + trigger + empty listbox panel).
 * @param {string} prefix - element id prefix, e.g. "work-doc-type" → work-doc-type-trigger, work-doc-type-listbox
 * @param {string} selectedValue
 * @param {boolean} [disabled]
 */
function prksDocTypeMenuShellHtml(prefix, selectedValue, disabled) {
    const sel = prksNormalizeDocType(selectedValue);
    const meta = prksDocTypeMeta(sel);
    const dis = disabled ? ' disabled' : '';
    const ariaDis = disabled ? ' aria-disabled="true"' : '';
    return (
        `<div class="prks-doc-type-menu combobox-container">` +
        `<input type="hidden" id="${prefix}" name="${prefix}" value="${prksEscapeDocTypeAttr(sel)}">` +
        `<button type="button" class="prks-doc-type-menu__trigger" id="${prefix}-trigger"${dis}${ariaDis} ` +
        `aria-haspopup="listbox" aria-expanded="false" aria-controls="${prefix}-listbox" ` +
        `aria-label="BibLaTeX document type">` +
        `<span class="prks-doc-type-menu__label">${prksEscapeDocTypeAttr(meta.label)}</span>` +
        `<span class="prks-doc-type-menu__caret" aria-hidden="true">▾</span>` +
        `</button>` +
        `<div id="${prefix}-listbox" class="prks-doc-type-menu__panel hidden" role="listbox"></div>` +
        `</div>`
    );
}

function prksCloseAllDocTypeMenus(exceptWrap) {
    document.querySelectorAll('.prks-doc-type-menu').forEach((w) => {
        if (exceptWrap && w === exceptWrap) return;
        const p = w.querySelector('.prks-doc-type-menu__panel');
        const t = w.querySelector('.prks-doc-type-menu__trigger');
        if (p) p.classList.add('hidden');
        if (t) t.setAttribute('aria-expanded', 'false');
    });
}

function prksRefreshDocTypeMenu(wrap) {
    const hidden = wrap.querySelector('input[type="hidden"]');
    const trigger = wrap.querySelector('.prks-doc-type-menu__trigger');
    const panel = wrap.querySelector('.prks-doc-type-menu__panel');
    const labelEl = wrap.querySelector('.prks-doc-type-menu__label');
    if (!hidden || !trigger || !panel) return;
    const current = prksNormalizeDocType(hidden.value || 'article');
    hidden.value = current;
    const meta = prksDocTypeMeta(current);
    if (labelEl) labelEl.textContent = meta.label;

    panel.innerHTML = '';
    for (const d of PRKS_DOC_TYPES) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'prks-doc-type-menu__option' + (d.value === current ? ' is-selected' : '');
        b.setAttribute('role', 'option');
        b.setAttribute('data-value', d.value);
        b.setAttribute('aria-selected', d.value === current ? 'true' : 'false');
        b.innerHTML =
            `<span class="prks-doc-type-menu__swatch" style="background:${prksEscapeDocTypeAttr(d.color)}"></span>` +
            `<span class="prks-doc-type-menu__option-label">${prksEscapeDocTypeAttr(d.label)}</span>`;
        panel.appendChild(b);
    }
}

function prksEnsureDocTypeMenuGlobals() {
    if (window.__prksDocTypeMenuGlobals) return;
    window.__prksDocTypeMenuGlobals = true;
    document.addEventListener('click', (e) => {
        if (e.target.closest('.prks-doc-type-menu')) return;
        prksCloseAllDocTypeMenus(null);
    });
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        prksCloseAllDocTypeMenus(null);
    });
}

/**
 * Custom app-styled doc type picker (not native &lt;select&gt;).
 * @param {string} hiddenInputId - id of hidden input (e.g. work-doc-type)
 * @param {{ selectedValue?: string, disabled?: boolean }} [opts]
 */
function initPrksDocTypeMenu(hiddenInputId, opts) {
    opts = opts || {};
    prksEnsureDocTypeMenuGlobals();
    const hidden = document.getElementById(hiddenInputId);
    if (!hidden || hidden.type !== 'hidden') return;
    const wrap = hidden.closest('.prks-doc-type-menu');
    if (!wrap) return;
    const trigger = wrap.querySelector('.prks-doc-type-menu__trigger');
    const panel = wrap.querySelector('.prks-doc-type-menu__panel');
    if (!trigger || !panel) return;

    if (opts.selectedValue != null) {
        hidden.value = prksNormalizeDocType(opts.selectedValue);
    }
    if (!hidden.value) hidden.value = 'article';

    prksRefreshDocTypeMenu(wrap);

    if (opts.disabled != null) {
        trigger.disabled = !!opts.disabled;
        if (trigger.disabled) {
            trigger.setAttribute('aria-disabled', 'true');
            panel.classList.add('hidden');
            trigger.setAttribute('aria-expanded', 'false');
        } else {
            trigger.removeAttribute('aria-disabled');
        }
    }

    if (wrap.dataset.prksDocMenuInit === '1') return;
    wrap.dataset.prksDocMenuInit = '1';

    wrap.addEventListener('click', (e) => {
        const optBtn = e.target.closest('.prks-doc-type-menu__option');
        if (optBtn && panel.contains(optBtn)) {
            e.preventDefault();
            e.stopPropagation();
            if (trigger.disabled) return;
            const v = optBtn.getAttribute('data-value');
            if (!v) return;
            hidden.value = prksNormalizeDocType(v);
            prksRefreshDocTypeMenu(wrap);
            panel.classList.add('hidden');
            trigger.setAttribute('aria-expanded', 'false');
            return;
        }
        if (e.target.closest('.prks-doc-type-menu__trigger')) {
            e.stopPropagation();
            if (trigger.disabled) return;
            const open = !panel.classList.contains('hidden');
            if (open) {
                panel.classList.add('hidden');
                trigger.setAttribute('aria-expanded', 'false');
            } else {
                prksCloseAllDocTypeMenus(wrap);
                panel.classList.remove('hidden');
                trigger.setAttribute('aria-expanded', 'true');
            }
        }
    });
}
