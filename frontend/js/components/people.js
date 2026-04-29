function escapeHtmlPerson(s) {
    if (typeof window.prksEscapeHtml === 'function') return window.prksEscapeHtml(s);
    if (s == null) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

const PERSON_YEAR_MIN = -99999;
const PERSON_YEAR_MAX = 99999;

function isLeapYearProleptic(y) {
    if (y % 400 === 0) return true;
    if (y % 100 === 0) return false;
    return y % 4 === 0;
}

function daysInMonthPerson(year, month) {
    const dim = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    if (month === 2 && isLeapYearProleptic(year)) return 29;
    return dim[month - 1];
}

function isValidPersonYearToken(s) {
    if (!/^-?\d+$/.test(s)) return false;
    const y = parseInt(s, 10);
    return y >= PERSON_YEAR_MIN && y <= PERSON_YEAR_MAX;
}

/**
 * Stored value → display in the text field:
 * - year only: "1903" or "-428"
 * - full calendar date: "DD-MM-YYYY" (year may be negative, e.g. 15-03--384)
 */
function personDateToDisplayFormat(stored) {
    if (!stored || typeof stored !== 'string') return '';
    const t = stored.trim();
    if (/^-?\d+$/.test(t)) return t;
    const m = t.match(/^(-?\d+)-(\d{2})-(\d{2})$/);
    if (!m) return '';
    return `${m[3]}-${m[2]}-${m[1]}`;
}

/**
 * Parse birth/death field for the API. Empty → ''.
 * - Year only: "-428", "1903"
 * - Full date: dd-mm-yyyy or dd/mm/yyyy (same separator twice), year last; e.g. 20-02-2001, 15/03/-384
 * - 8 digits: DDMMYYYY (Gregorian AD only)
 * Invalid → null.
 */
function parsePersonBirthDeathField(text) {
    let s = (text || '').trim();
    if (!s) return '';
    if (/^-?\d+$/.test(s)) {
        return isValidPersonYearToken(s) ? s : null;
    }
    if (/^\d{8}$/.test(s)) {
        s = `${s.slice(0, 2)}-${s.slice(2, 4)}-${s.slice(4, 8)}`;
    }
    const m = s.match(/^(\d{1,2})([/-])(\d{1,2})\2(-?\d+)$/);
    if (!m) return null;
    const day = parseInt(m[1], 10);
    const month = parseInt(m[3], 10);
    const yearStr = m[4];
    if (!isValidPersonYearToken(yearStr)) return null;
    const year = parseInt(yearStr, 10);
    if (month < 1 || month > 12) return null;
    const dim = daysInMonthPerson(year, month);
    if (day < 1 || day > dim) return null;
    const dt = new Date(year, month - 1, day);
    if (dt.getFullYear() !== year || dt.getMonth() !== month - 1 || dt.getDate() !== day) return null;
    return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

const PERSON_DATE_HELP =
    'Use dd-mm-yyyy or dd/mm/yyyy (year last; may be negative, e.g. 15-03--384), yyyy or -yyyy for year only, or 8 digits ddmmyyyy.';

const PERSON_MONTH_NAMES = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December'
];

function formatPersonYearForLongDisplay(y) {
    if (y === 0) return '0';
    if (y < 0) return `${Math.abs(y)} BC`;
    return String(y);
}

/** Readable lifetime text in About (not edit fields): e.g. 20 February 2001; year-only 1903 or 428 BC. */
function personDateToLongDisplayFormat(stored) {
    if (!stored || typeof stored !== 'string') return '';
    const t = stored.trim();
    if (/^-?\d+$/.test(t)) {
        if (!isValidPersonYearToken(t)) return t;
        return formatPersonYearForLongDisplay(parseInt(t, 10));
    }
    const m = t.match(/^(-?\d+)-(\d{2})-(\d{2})$/);
    if (!m) return '';
    const year = parseInt(m[1], 10);
    const month = parseInt(m[2], 10);
    const day = parseInt(m[3], 10);
    if (month < 1 || month > 12) return personDateToDisplayFormat(t);
    const name = PERSON_MONTH_NAMES[month - 1];
    return `${day} ${name} ${formatPersonYearForLongDisplay(year)}`;
}

function personLifespanDisplay(person) {
    const b = personDateToLongDisplayFormat(person.birth_date || '');
    const d = personDateToLongDisplayFormat(person.death_date || '');
    if (!b && !d) return '';
    if (b && d) return `${b} – ${d}`;
    if (b) return `Born ${b}`;
    return `Died ${d}`;
}

function safeHttpUrl(url) {
    const u = (url || '').trim();
    if (!u) return null;
    const lower = u.toLowerCase();
    if (lower.startsWith('https://') || lower.startsWith('http://')) return u;
    return null;
}

function parsePersonOtherLinkLine(line) {
    const trimmed = (line || '').trim();
    if (!trimmed) return null;
    const md = trimmed.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (!md) return null;
    const label = (md[1] || '').trim();
    const href = safeHttpUrl(md[2] || '');
    if (!label || !href) return null;
    return { label, href };
}

const PERSON_STANDARD_TEMPLATE_FIELDS = [
    { key: 'first_name', id: 'pd-first-name' },
    { key: 'last_name', id: 'pd-last-name' },
    { key: 'aliases', id: 'pd-aliases' },
    { key: 'about', id: 'pd-about' },
    { key: 'birth_date', id: 'pd-birth-date' },
    { key: 'death_date', id: 'pd-death-date' },
    { key: 'image_url', id: 'pd-image-url' },
    { key: 'link_wikipedia', id: 'pd-link-wikipedia' },
    { key: 'link_stanford_encyclopedia', id: 'pd-link-stanford' },
    { key: 'link_iep', id: 'pd-link-iep' },
    { key: 'links_other', id: 'pd-links-other' }
];

const PERSON_TEMPLATE_HELP_TEXT = [
    'Fill this template according to the provided rules:',
    '',
    '- first_name, last_name, aliases, about: plain text strings. Middle names should be placed in first_name field.',
    '- birth_date, death_date: use dd-mm-yyyy, dd/mm/yyyy, yyyy, or -yyyy (BC).',
    '- image_url, link_wikipedia, link_stanford_encyclopedia, link_iep: full URL strings (prefer https://). For image_url, use Wikipedia (or Wikimedia related) profile photo URL if it exists; otherwise other reliable sources are allowed.',
    "- links_other: one entry per line inside single JSON string. MUST USE [Title](https://...) format for links in this field. DO NOT use free-text notes. This field meant for links such as personal website of person, or other reputable links about this person. Don't include links to papers discussing this person or papers by this person. Acceptable links in this field are links to encyclopedias, profile pages...",
    '- empty fields should stay empty; do not fill them with values such as N/A',
    '- aliases: free text; comma-separated aliases recommended. Include Serbian Latin (not Cyrillic) variation of the name if it exists. If the person has a middle name, the variation with only first and last name should be added to the aliases field (if it was used anywhere).',
    '- Keep exact keys; do not add/remove keys.',
    '- Keep all values as JSON strings.',
].join('\n');

function setPersonTemplateFeedback(message, isError, targetId = 'person-template-feedback') {
    const feedback = document.getElementById(targetId);
    if (!feedback) return;
    feedback.textContent = message || '';
    feedback.className = isError ? 'prks-inline-message prks-inline-message--error' : 'prks-inline-message';
}

function buildPersonStandardTemplateFromProfile() {
    const out = {};
    const p = window.currentPerson || {};
    out.first_name = String(p.first_name || '');
    out.last_name = String(p.last_name || '');
    out.aliases = String(p.aliases || '');
    out.about = String(p.about || '');
    out.birth_date = String(personDateToDisplayFormat(p.birth_date || ''));
    out.death_date = String(personDateToDisplayFormat(p.death_date || ''));
    out.image_url = String(p.image_url || '');
    out.link_wikipedia = String(p.link_wikipedia || '');
    out.link_stanford_encyclopedia = String(p.link_stanford_encyclopedia || '');
    out.link_iep = String(p.link_iep || '');
    out.links_other = String(p.links_other || '');
    return out;
}

function parsePersonStandardTemplateJson(raw) {
    let parsed;
    try {
        parsed = JSON.parse(raw);
    } catch (_e) {
        return { ok: false, error: 'Invalid JSON. Paste valid JSON object template.' };
    }
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
        return { ok: false, error: 'Template must be JSON object.' };
    }
    const allowed = new Set(PERSON_STANDARD_TEMPLATE_FIELDS.map((f) => f.key));
    const keys = Object.keys(parsed);
    const unknown = keys.filter((k) => !allowed.has(k));
    if (unknown.length) {
        return { ok: false, error: `Unknown field(s): ${unknown.join(', ')}` };
    }
    const missing = PERSON_STANDARD_TEMPLATE_FIELDS.map((f) => f.key).filter(
        (k) => !Object.prototype.hasOwnProperty.call(parsed, k)
    );
    if (missing.length) {
        return { ok: false, error: `Missing field(s): ${missing.join(', ')}` };
    }
    const values = {};
    for (const field of PERSON_STANDARD_TEMPLATE_FIELDS) {
        const v = parsed[field.key];
        if (typeof v !== 'string') {
            return { ok: false, error: `Field "${field.key}" must be string.` };
        }
        values[field.key] = v;
    }
    return { ok: true, values };
}

function openPersonProfileTemplateModal() {
    if (typeof openModal === 'function') {
        openModal('person-template-modal');
    }
    const templateArea = document.getElementById('person-template-json');
    if (!templateArea) return;
    const tpl = buildPersonStandardTemplateFromProfile();
    templateArea.value = JSON.stringify(tpl, null, 2);
    setPersonTemplateFeedback('', false);
    if (typeof prksAutosizeTextarea === 'function') prksAutosizeTextarea(templateArea);
}

async function copyPersonTemplateGuideText() {
    const s = PERSON_TEMPLATE_HELP_TEXT;
    try {
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            await navigator.clipboard.writeText(s);
            setPersonTemplateFeedback('Template field guide copied.', false);
            return;
        }
    } catch (_e) {}
    const ta = document.createElement('textarea');
    ta.value = s;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    let ok = false;
    try {
        ok = !!document.execCommand('copy');
    } catch (_e) {
        ok = false;
    } finally {
        ta.remove();
    }
    if (ok) {
        setPersonTemplateFeedback('Template field guide copied.', false);
    } else {
        setPersonTemplateFeedback('Could not copy guide. Copy manually.', true);
    }
}

async function copyPersonTemplateAndGuideText() {
    const templateArea = document.getElementById('person-template-json');
    const rawTemplate = templateArea ? String(templateArea.value || '') : '';
    const template =
        rawTemplate.trim() ||
        JSON.stringify(buildPersonStandardTemplateFromProfile(), null, 2);
    const s = `${template}\n\n${PERSON_TEMPLATE_HELP_TEXT}`;
    try {
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            await navigator.clipboard.writeText(s);
            setPersonTemplateFeedback('Template + field guide copied.', false);
            return;
        }
    } catch (_e) {}
    const ta = document.createElement('textarea');
    ta.value = s;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    let ok = false;
    try {
        ok = !!document.execCommand('copy');
    } catch (_e) {
        ok = false;
    } finally {
        ta.remove();
    }
    if (ok) {
        setPersonTemplateFeedback('Template + field guide copied.', false);
    } else {
        setPersonTemplateFeedback('Could not copy template + guide. Copy manually.', true);
    }
}

function insertPersonProfileTemplateFromCurrentData() {
    const templateArea = document.getElementById('person-template-json');
    if (!templateArea) return;
    const tpl = buildPersonStandardTemplateFromProfile();
    templateArea.value = JSON.stringify(tpl, null, 2);
    setPersonTemplateFeedback('Template refreshed from current profile values.', false);
    if (typeof prksAutosizeTextarea === 'function') prksAutosizeTextarea(templateArea);
}

function applyPersonTemplateValuesToEditForm(values) {
    const targets = {};
    for (const field of PERSON_STANDARD_TEMPLATE_FIELDS) {
        const el = document.getElementById(field.id);
        if (!el) return { ok: false, error: `Could not find field "${field.key}" in edit form.` };
        targets[field.key] = el;
    }
    PERSON_STANDARD_TEMPLATE_FIELDS.forEach((field) => {
        targets[field.key].value = values[field.key];
    });
    if (typeof prksAutosizeTextarea === 'function') {
        ['pd-about', 'pd-links-other'].forEach((id) => {
            const el = document.getElementById(id);
            if (el) prksAutosizeTextarea(el);
        });
    }
    return { ok: true };
}

async function applyPersonProfileTemplateFromModal() {
    const templateArea = document.getElementById('person-template-json');
    if (!templateArea) return;
    const parsed = parsePersonStandardTemplateJson(templateArea.value);
    if (!parsed.ok) {
        setPersonTemplateFeedback(parsed.error || 'Could not apply template.', true);
        return;
    }
    await openPersonProfileEdit();
    const applied = applyPersonTemplateValuesToEditForm(parsed.values);
    if (!applied.ok) {
        setPersonTemplateFeedback(applied.error || 'Could not apply template.', true);
        return;
    }
    if (typeof closeModals === 'function') closeModals();
}

function renderPersonExternalLinksList(person) {
    const items = [];
    const wiki = safeHttpUrl(person.link_wikipedia);
    if (wiki) {
        items.push({ label: 'Wikipedia', href: wiki });
    }
    const sep = safeHttpUrl(person.link_stanford_encyclopedia);
    if (sep) {
        items.push({ label: 'Stanford Encyclopedia of Philosophy', href: sep });
    }
    const iep = safeHttpUrl(person.link_iep);
    if (iep) {
        items.push({ label: 'Internet Encyclopedia of Philosophy', href: iep });
    }
    const other = (person.links_other || '').split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    other.forEach((line, i) => {
        const mdLink = parsePersonOtherLinkLine(line);
        if (mdLink) {
            items.push(mdLink);
            return;
        }
        const href = safeHttpUrl(line);
        if (href) {
            items.push({ label: href.replace(/^https?:\/\//i, '').split('/')[0] || `Link ${i + 1}`, href });
        } else {
            items.push({ label: line, href: null });
        }
    });
    if (!items.length) return '';
    const lis = items
        .map(it => {
            if (it.href) {
                return `<li><a href="${escapeHtmlPerson(it.href)}" target="_blank" rel="noopener noreferrer">${escapeHtmlPerson(it.label)}</a></li>`;
            }
            return `<li>${escapeHtmlPerson(it.label)}</li>`;
        })
        .join('');
    return `
        <div class="person-external-links">
            <h4>External references</h4>
            <ul class="person-link-list">${lis}</ul>
        </div>`;
}

function truncatePersonPreviewText(text, maxLen) {
    if (!text || typeof text !== 'string') return '';
    const oneLine = text.replace(/\s+/g, ' ').trim();
    if (!oneLine) return '';
    if (oneLine.length <= maxLen) return oneLine;
    return `${oneLine.slice(0, maxLen - 1).trim()}…`;
}

function personExternalRefsSummary(person) {
    const bits = [];
    if (safeHttpUrl(person.link_wikipedia)) bits.push('Wikipedia');
    if (safeHttpUrl(person.link_stanford_encyclopedia)) bits.push('Stanford Enc.');
    if (safeHttpUrl(person.link_iep)) bits.push('IEP');
    const otherLines = (person.links_other || '').split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    const otherHttp = otherLines.filter(l => parsePersonOtherLinkLine(l) || safeHttpUrl(l)).length;
    if (otherHttp) bits.push(otherHttp === 1 ? 'Other link' : `${otherHttp} other links`);
    return bits.length ? bits.join(' · ') : '';
}

/** Inner HTML for a person list card (shared with People list and group member list). */
function buildPersonListCardContentHtml(p) {
    const name = `${p.first_name || ''} ${p.last_name || ''}`.trim();
    const lifespan = personLifespanDisplay(p);
    const aliasesRaw = (p.aliases || '').trim();
    const aliasesPreview = truncatePersonPreviewText(aliasesRaw, 100);
    const aboutPreview = truncatePersonPreviewText(p.about || '', 220);
    const refsSummary = personExternalRefsSummary(p);

    let body = `
        <div class="card-title">${escapeHtmlPerson(name)}</div>`;
    if (lifespan) {
        body += `<p class="meta-row person-card-lifespan">${escapeHtmlPerson(lifespan)}</p>`;
    }
    if (aliasesPreview) {
        body += `<p class="meta-row person-card-aliases"><span class="person-card-label">Also known as</span> ${escapeHtmlPerson(aliasesPreview)}</p>`;
    }
    if (aboutPreview) {
        body += `<p class="meta-row person-card-about">${escapeHtmlPerson(aboutPreview)}</p>`;
    } else if (!lifespan && !aliasesPreview && !refsSummary) {
        body += `<p class="meta-row person-card-about person-card-about--empty">No biography or links yet.</p>`;
    }
    if (refsSummary) {
        body += `<p class="meta-row person-card-refs"><span class="person-card-label">References</span> ${escapeHtmlPerson(refsSummary)}</p>`;
    }
    if (Array.isArray(p.groups) && p.groups.length > 0) {
        const tags = p.groups
            .map(
                (g) =>
                    `<span class="tag" onclick="event.stopPropagation();window.location.hash='#/people/groups/${escapeHtmlPerson(g.id)}'">${escapeHtmlPerson(g.name)}</span>`
            )
            .join(' ');
        body += `<p class="meta-row person-card-groups"><span class="person-card-label">Groups</span> ${tags}</p>`;
    }
    return body;
}

function renderPersonListCard(p) {
    return `
        <div class="project-card project-card--person" onclick="window.location.hash='#/people/${escapeHtmlPerson(p.id)}'">
            ${buildPersonListCardContentHtml(p)}
        </div>`;
}

const PEOPLE_LIST_ROLE_LABELS = {
    Author: 'Authors',
    Editor: 'Editors',
    Reviewer: 'Reviewers',
    Translator: 'Translators',
    Introduction: 'Introduction writers',
    Foreword: 'Foreword writers',
    Afterword: 'Afterword writers'
};

function filterPersonsByAssignedRole(persons, roleType) {
    if (!roleType) return persons || [];
    return (persons || []).filter(
        p => Array.isArray(p.assigned_roles) && p.assigned_roles.includes(roleType)
    );
}

function renderPeopleList(persons, container, options = {}) {
    const roleFilter = options.roleFilter || null;
    const filtered = filterPersonsByAssignedRole(persons, roleFilter);
    const titleExtra = roleFilter ? ` — ${PEOPLE_LIST_ROLE_LABELS[roleFilter] || roleFilter}` : '';
    let html = `<div class="page-header"><h2>People${escapeHtmlPerson(titleExtra)}</h2></div><div class="list-view list-view--people">`;
    if (filtered && filtered.length > 0) {
        filtered.forEach(p => {
            html += renderPersonListCard(p);
        });
    } else if (persons && persons.length > 0 && roleFilter) {
        html += `<p class="prks-inline-message">No people with the <strong>${escapeHtmlPerson(roleFilter)}</strong> role yet. Use <strong>Link Person to Work</strong> in the ribbon to assign roles.</p>`;
    } else {
        html += '<p class="prks-inline-message">No people yet. Use <strong>New Person</strong> in the ribbon to add one.</p>';
    }
    html += '</div>';
    container.innerHTML = html;
}

async function openPersonProfileEdit() {
    window.__prksPersonWorksEditing = false;
    window.__prksPersonDetailEditing = true;
    if (typeof updatePanelContent === 'function') updatePanelContent('details');
    if (typeof prksMountPersonProfileGroupPicker === 'function' && window.currentPerson) {
        await prksMountPersonProfileGroupPicker(window.currentPerson);
    }
}

function closePersonProfileEdit() {
    window.__prksPersonDetailEditing = false;
    window.__prksPersonEditSelectedGroups = null;
    if (typeof updatePanelContent === 'function') updatePanelContent('details');
}

function prksTogglePersonWorksEdit() {
    const p = window.currentPerson;
    if (!p) return;
    const nowEditing = window.__prksPersonWorksEditing === true;
    if (nowEditing) {
        window.__prksPersonWorksEditing = false;
    } else {
        if (!p.works || p.works.length === 0) return;
        window.__prksPersonWorksEditing = true;
    }
    const contentDiv = document.getElementById('page-content');
    if (contentDiv && typeof renderPersonDetails === 'function') {
        renderPersonDetails(p, contentDiv);
    }
    if (typeof updatePanelContent === 'function') {
        updatePanelContent('details');
    }
}

function prksUniquePersonWorks(person) {
    const works = Array.isArray(person && person.works) ? person.works : [];
    const byId = new Map();
    works.forEach((w, idx) => {
        const rawId = w && w.id != null ? String(w.id).trim() : '';
        const key = rawId || `__row_${idx}`;
        if (!byId.has(key)) {
            byId.set(key, w);
        }
    });
    return Array.from(byId.values());
}

function prksPersonWorkRolesById(person) {
    const works = Array.isArray(person && person.works) ? person.works : [];
    const byId = new Map();
    works.forEach((w, idx) => {
        const rawId = w && w.id != null ? String(w.id).trim() : '';
        const key = rawId || `__row_${idx}`;
        if (!byId.has(key)) byId.set(key, []);
        const roles = byId.get(key);
        const role = w && w.role_type != null ? String(w.role_type).trim() : '';
        if (role && !roles.includes(role)) roles.push(role);
    });
    return byId;
}

function renderPersonProfileDetailsSidebarHtml(person) {
    if (!person) return '';
    const display = `${(person.first_name || '').trim()} ${(person.last_name || '').trim()}`.trim() || 'Person';
    const name = escapeHtmlPerson(display);
    const nWorks = prksUniquePersonWorks(person).length;
    const editingWorks = window.__prksPersonWorksEditing === true;
    const worksEditBtn =
        nWorks > 0 || editingWorks
            ? `<button type="button" class="add-new-btn person-sidebar__cta" onclick="prksTogglePersonWorksEdit()">${
                  editingWorks ? 'Done' : 'Edit works'
              }</button>`
            : '';
    return `
        <div class="doc-meta-card person-sidebar-summary">
            <h3>${name}</h3>
            <p class="meta-row">Biography, portrait, and external links are in the main column.</p>
            <p class="route-sidebar__meta">${nWorks} linked file${nWorks === 1 ? '' : 's'}</p>
            ${worksEditBtn}
            <button type="button" class="add-new-btn person-sidebar__cta" onclick="openPersonProfileEdit()">Edit profile</button>
            <button type="button" class="add-new-btn person-sidebar__cta" onclick="openPersonProfileTemplateModal()">Edit profile using template</button>
            <p class="route-sidebar__action"><a href="#/people" class="route-sidebar__link">All people</a></p>
        </div>`;
}

function renderPersonProfileEditFormHtml(person) {
    if (!person) return '';
    const id = escapeHtmlPerson(person.id);
    return `
        <div class="doc-meta-card person-panel-edit">
            <div class="card-heading-row card-heading-row--wrap">
                <h3>Edit profile</h3>
                <button type="button" onclick="closePersonProfileEdit()" class="inline-action-btn">Cancel</button>
            </div>
            <div class="form-pane person-edit-form person-edit-form--panel">
                <label for="pd-first-name">First name</label>
                <input type="text" id="pd-first-name" value="${escapeHtmlPerson(person.first_name)}">
                <label for="pd-last-name">Last name</label>
                <input type="text" id="pd-last-name" value="${escapeHtmlPerson(person.last_name)}">
                <label for="pd-aliases">Aliases</label>
                <input type="text" id="pd-aliases" value="${escapeHtmlPerson(person.aliases)}">
                <label for="pd-about">About / expertise</label>
                <textarea id="pd-about" class="textarea-sm">${escapeHtmlPerson(person.about)}</textarea>
                <div class="form-grid-2 form-grid-2--compact">
                    <div>
                        <label for="pd-birth-date">Birth date</label>
                        <input type="text" id="pd-birth-date" placeholder="dd-mm-yyyy or yyyy" autocomplete="off" value="${escapeHtmlPerson(personDateToDisplayFormat(person.birth_date || ''))}">
                    </div>
                    <div>
                        <label for="pd-death-date">Date of death</label>
                        <input type="text" id="pd-death-date" placeholder="dd-mm-yyyy or yyyy" autocomplete="off" value="${escapeHtmlPerson(personDateToDisplayFormat(person.death_date || ''))}">
                    </div>
                </div>
                <label for="pd-image-url">Portrait image URL</label>
                <input type="url" id="pd-image-url" value="${escapeHtmlPerson(person.image_url)}">
                <label for="pd-link-wikipedia">Wikipedia</label>
                <input type="url" id="pd-link-wikipedia" value="${escapeHtmlPerson(person.link_wikipedia)}">
                <label for="pd-link-stanford">Stanford Encyclopedia of Philosophy</label>
                <input type="url" id="pd-link-stanford" value="${escapeHtmlPerson(person.link_stanford_encyclopedia)}">
                <label for="pd-link-iep">Internet Encyclopedia of Philosophy</label>
                <input type="url" id="pd-link-iep" value="${escapeHtmlPerson(person.link_iep)}">
                <label for="pd-links-other">Other links</label>
                <textarea id="pd-links-other" placeholder="One URL per line, or [Title](https://...)" class="textarea-sm">${escapeHtmlPerson(person.links_other)}</textarea>
                <fieldset class="person-groups-fieldset">
                    <legend class="person-groups-fieldset__legend">Groups</legend>
                    <p class="meta-row">Search for a group, pick from the list, or type a new name and <strong>Add</strong> to create a top-level group. Names are unique. <a href="#/people/groups">Browse groups</a>.</p>
                    <div id="pd-group-chips" class="tag-cloud person-groups-fieldset__chips"></div>
                    <label for="pd-group-search">Add group</label>
                    <div class="combobox-container">
                        <div class="tag-add-shell">
                            <div class="tag-add-shell__field">
                                <span class="tag-add-shell__icon" aria-hidden="true">🔍</span>
                                <input type="text" id="pd-group-search" class="tag-add-shell__input" placeholder="Search or type new group name…" autocomplete="off" aria-label="Search group to add">
                            </div>
                        </div>
                        <input type="hidden" id="pd-group-pick-id" value="">
                        <div id="pd-group-results" class="combobox-results hidden"></div>
                    </div>
                    <button type="button" class="add-new-btn person-groups-fieldset__action" id="pd-group-add-btn">Add group</button>
                </fieldset>
                <button type="button" id="pd-save-btn" class="add-new-btn person-groups-fieldset__action" onclick="savePersonProfile('${id}')">Save profile</button>
            </div>
        </div>`;
}

async function savePersonProfile(personId) {
    const birthIso = parsePersonBirthDeathField(document.getElementById('pd-birth-date').value);
    if (birthIso === null) {
        return alert(`Birth:\n${PERSON_DATE_HELP}`);
    }
    const deathIso = parsePersonBirthDeathField(document.getElementById('pd-death-date').value);
    if (deathIso === null) {
        return alert(`Date of death:\n${PERSON_DATE_HELP}`);
    }
    let group_ids =
        typeof prksGetPersonEditGroupIdsFromDom === 'function' ? prksGetPersonEditGroupIdsFromDom() : undefined;
    if (group_ids === undefined) {
        group_ids = (window.currentPerson && window.currentPerson.groups
            ? window.currentPerson.groups
            : []
        ).map((g) => g.id);
    }
    const payload = {
        first_name: document.getElementById('pd-first-name').value,
        last_name: document.getElementById('pd-last-name').value,
        aliases: document.getElementById('pd-aliases').value,
        about: document.getElementById('pd-about').value,
        image_url: document.getElementById('pd-image-url').value,
        link_wikipedia: document.getElementById('pd-link-wikipedia').value,
        link_stanford_encyclopedia: document.getElementById('pd-link-stanford').value,
        link_iep: document.getElementById('pd-link-iep').value,
        links_other: document.getElementById('pd-links-other').value,
        birth_date: birthIso,
        death_date: deathIso,
        group_ids
    };
    if (!payload.last_name.trim()) {
        return alert('Last name is required.');
    }
    const btn = document.getElementById('pd-save-btn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Saving…';
    }
    try {
        const res = await fetch(`/api/persons/${personId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const patchBody = await res.json().catch(() => ({}));
        if (!res.ok) {
            alert(patchBody.error || 'Could not save profile.');
            return;
        }
        const person = await fetchPersonDetails(personId);
        window.__prksPersonDetailEditing = false;
        if (person) {
            window.currentPerson = person;
            window.__prksRouteSidebar = {
                personDisplayName:
                    typeof personDisplayName === 'function' ? personDisplayName(person) || 'Person' : 'Person',
                linkedWorks: prksUniquePersonWorks(person).length
            };
        }
        const contentDiv = document.getElementById('page-content');
        if (person && contentDiv) {
            renderPersonDetails(person, contentDiv);
        }
        if (typeof updatePanelContent === 'function') {
            updatePanelContent('details');
        }
    } catch (e) {
        console.error(e);
        alert('Could not save profile.');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Save profile';
        }
    }
}

function renderPersonDetails(person, container) {
    if (!person) {
        window.currentPerson = null;
        window.__prksPersonWorksEditing = false;
        container.innerHTML = '<h2>Person not found</h2>';
        return;
    }
    window.currentPerson = person;

    const worksEditing = window.__prksPersonWorksEditing === true;
    let worksHtml = `<div class="card-grid">`;
    if (person.works && person.works.length > 0) {
        if (worksEditing) {
            const groupedWorks = person.works.reduce((acc, w) => {
                if (!acc[w.role_type]) acc[w.role_type] = [];
                acc[w.role_type].push(w);
                return acc;
            }, {});

            for (const [role, worksList] of Object.entries(groupedWorks)) {
                worksHtml += `<h3 class="person-profile__role-heading">${escapeHtmlPerson(role)}</h3>`;
                worksList.forEach((w) => {
                    const card = typeof prksWorkCardHtml === 'function' ? prksWorkCardHtml(w) : '';
                    const oi =
                        w.order_index != null && w.order_index !== '' ? String(w.order_index) : '0';
                    const rt = escapeHtmlPerson(w.role_type || 'Linked');
                    const pid = escapeHtmlPerson(person.id);
                    const wid = escapeHtmlPerson(w.id);
                    worksHtml += `<div class="person-profile__work-card-wrap">${card}<button type="button" class="person-profile__card-unlink" aria-label="Remove link to this file" data-work-id="${wid}" data-person-id="${pid}" data-role-type="${rt}" data-order-index="${escapeHtmlPerson(oi)}" onclick="event.stopPropagation(); void prksRemoveWorkRoleLink(this);">×</button></div>`;
                });
            }
        } else {
            const uniqueWorks = prksUniquePersonWorks(person);
            const rolesByWork = prksPersonWorkRolesById(person);
            uniqueWorks.forEach((w) => {
                const workId = w && w.id != null ? String(w.id).trim() : '';
                const roleList = rolesByWork.get(workId) || [];
                const subtitle = roleList.join(', ');
                const card =
                    typeof prksWorkCardHtml === 'function'
                        ? prksWorkCardHtml(w, subtitle ? { subtitle } : {})
                        : '';
                worksHtml += `<div class="person-profile__work-card-wrap">${card}</div>`;
            });
        }
    } else {
        worksHtml += '<p class="prks-inline-message">This person is not linked to any files.</p>';
    }
    worksHtml += `</div>`;

    const imgUrl = safeHttpUrl(person.image_url);
    const heroNoPhotoClass = imgUrl ? '' : ' person-profile__hero--no-photo';
    const portraitCol = imgUrl
        ? `<div class="person-profile__portrait"><div class="person-portrait-wrap"><img class="person-portrait" src="${escapeHtmlPerson(imgUrl)}" alt=""></div></div>`
        : '';

    const linksBlock = renderPersonExternalLinksList(person);
    const lifespan = personLifespanDisplay(person);
    const lifespanHtml = lifespan
        ? `<p class="meta-row person-lifespan"><strong>Lifetime:</strong> ${escapeHtmlPerson(lifespan)}</p>`
        : '';
    let groupsHtml = '';
    if (Array.isArray(person.groups) && person.groups.length > 0) {
        const tags = person.groups
            .map(
                (g) =>
                    `<span class="tag" onclick="window.location.hash='#/people/groups/${escapeHtmlPerson(g.id)}'">${escapeHtmlPerson(g.name)}</span>`
            )
            .join(' ');
        groupsHtml = `<p class="meta-row"><strong>Groups:</strong> ${tags}</p>`;
    }

    container.innerHTML = `
        <div class="page-header page-header--split">
            <h2>👤 ${escapeHtmlPerson(person.first_name || '')} ${escapeHtmlPerson(person.last_name)}</h2>
        </div>
        <div class="document-view document-view--person">
            <div class="doc-content person-profile">
                <div class="person-profile__hero${heroNoPhotoClass}">
                    ${portraitCol}
                    <div class="person-profile__info">
                        <div class="doc-meta-card">
                            <h3>About</h3>
                            ${lifespanHtml}
                            ${groupsHtml}
                            <p class="person-profile__about-text">${escapeHtmlPerson(person.about || 'No details available.')}</p>
                            <p class="meta-row"><strong>Aliases:</strong> ${escapeHtmlPerson(person.aliases || 'None')}</p>
                        </div>
                        ${linksBlock}
                    </div>
                </div>
                <section class="person-profile__works${worksEditing ? ' person-profile__works--editing' : ''}" aria-labelledby="person-profile-works-heading">
                    <h2 id="person-profile-works-heading" class="person-profile__works-title">Linked files</h2>
                    ${worksHtml}
                </section>
            </div>
        </div>
    `;
}
