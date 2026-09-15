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
 * - full calendar date: "DD/MM/YYYY" (year may be negative, e.g. 15/03/-384)
 */
function personDateToDisplayFormat(stored) {
    if (!stored || typeof stored !== 'string') return '';
    const t = stored.trim();
    if (/^-?\d+$/.test(t)) return t;
    const m = t.match(/^(-?\d+)-(\d{2})-(\d{2})$/);
    if (!m) return '';
    return `${m[3]}/${m[2]}/${m[1]}`;
}

/**
 * Parse birth/death field for the API. Empty → ''.
 * - Year only: "-428", "1903"
 * - Full date: dd/mm/yyyy (same separator twice), year last; e.g. 20/02/2001, 15/03/-384
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
    'Use dd/mm/yyyy (year last; may be negative, e.g. 15/03/-384), yyyy or -yyyy for year only, or 8 digits ddmmyyyy.';

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

/** Cached profile image via API (remote fetch + disk cache server-side). */
/* --- Offline policy for People routes (AGENTS.md "Offline / PWA") ------------
 * People are read-only offline in Phase 1: cached index/role views and Person
 * profiles render, every canonical mutation is blocked outright (never queued,
 * never faked). Graph navigation delegates availability to its route. Linked Work
 * cards and Group chips stay ordinary PRKS links so the Work and Group routes
 * decide for themselves. Controls carry these roles so one helper can settle
 * them all, including markup rerendered after the initial bind. */
const PERSON_MUTATION_ROLE = 'person-mutation-control';

/* Creating a Person is durable-first, so its controls are NEVER disabled: the
 * identity is chosen on this device and the record is complete the moment it is
 * written locally. Editing and deleting an existing Person still require the
 * server, so they keep `PERSON_MUTATION_ROLE` and its offline disable. Two roles
 * rather than one, because the two decisions genuinely differ now. */
const PERSON_CREATE_ROLE = 'person-create-control';

/* Editing a Person's PROFILE is durable too, so "Edit profile" is never
 * disabled: the fields are field-scoped operations that queue offline exactly
 * as they send online. Group membership became durable with the Person Group
 * families, and deletion with `DELETE_PERSON`, so none of those controls is
 * disabled by connectivity any more. What `PERSON_MUTATION_ROLE` still covers
 * is the Work-RELATIONSHIP editing on this page, which has not been moved to
 * the durable role family yet. */
const PERSON_EDIT_ROLE = 'person-edit-control';

/* Deleting is durable, and carries its own role purely so tests and styling
 * can name the control. It is deliberately absent from
 * PERSON_CONTROL_SELECTOR: the protection on deletion is canonical -- a Person
 * credited on a file is refused -- and has never been about connectivity. */
const PERSON_DELETE_ROLE = 'person-delete-control';

/* Group chips keep this role for styling/test identification only: since Person
 * Groups became offline-capable they are ordinary links and are deliberately
 * absent from PERSON_CONTROL_SELECTOR. */
const PERSON_GROUP_LINK_ROLE = 'person-group-link';
const PERSON_CONTROL_SELECTOR = '[data-prks-role="' + PERSON_MUTATION_ROLE + '"]';
/* The profile editor's own inputs: disabled while offline so a draft is held
 * rather than silently discarded. Cancel is deliberately excluded so the user
 * can always leave edit mode. */
/* Nothing inside an open editor requires the server any more.
 *
 * The profile fields, Save, and now the group controls are all durable, so an
 * editor that went inert the moment PRKS stopped answering would be the old
 * "two features" behaviour wearing a different shape -- the user would lose
 * the ability to record a change this device can perfectly well keep. The
 * selector stays as an empty registry rather than disappearing, so the next
 * control that genuinely IS server-bound has an obvious home. */
const PERSON_EDITOR_SELECTOR = '';

function prksPersonRuntimeState() {
    return typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : 'online';
}

/** Blocks a canonical Person mutation while PRKS is unreachable. */
function prksPersonMutationBlocked(message) {
    return typeof prksOfflineGuardMutation === 'function' ? prksOfflineGuardMutation(message) : false;
}

function prksApplyPersonOfflineState(container) {
    if (!container || !container.querySelectorAll) return;
    const online = prksPersonRuntimeState() === 'online';
    const nodes = container.querySelectorAll(PERSON_CONTROL_SELECTOR);
    for (let i = 0; i < nodes.length; i++) {
        const el = nodes[i];
        if ('disabled' in el) el.disabled = !online;
        if (online) {
            el.removeAttribute('aria-disabled');
            el.removeAttribute('title');
        } else {
            el.setAttribute('aria-disabled', 'true');
            el.setAttribute('title', 'Requires a connection to PRKS');
        }
    }
}

/**
 * The Person editor lives in the shared right panel, so it is only ever
 * settled when this context actually owns that panel -- a background Person tab
 * must never disable or rewrite the panel another tab owns.
 */
function prksApplyPersonPanelOfflineState(ctx) {
    const panel = document.getElementById('panel-content');
    if (!panel) return;
    if (typeof prksRightPanelOwnedBy === 'function' && !prksRightPanelOwnedBy(ctx, panel)) return;
    prksApplyPersonOfflineState(panel);
    if (!PERSON_EDITOR_SELECTOR) return;
    const online = prksPersonRuntimeState() === 'online';
    const editorNodes = panel.querySelectorAll(PERSON_EDITOR_SELECTOR);
    for (let i = 0; i < editorNodes.length; i++) {
        const el = editorNodes[i];
        if (!('disabled' in el)) continue;
        el.disabled = !online;
        if (online) el.removeAttribute('aria-disabled');
        else el.setAttribute('aria-disabled', 'true');
    }
}

/**
 * Keeps a mounted Person page's controls in step with connectivity: a page
 * built while online becomes read-only in place when PRKS stops answering, and
 * restores on reconnect. Cached content stays readable and linked Work cards
 * stay usable so the Work route can decide for itself.
 *
 * The subscription belongs to the route's owning TabContext, and each bind
 * replaces the previous one on the same container -- a TabContext container
 * survives route changes and rerenders, so re-binding must not accumulate
 * listeners (and there is no global Person runtime singleton).
 */
function prksBindPersonOfflineState(ctx, container) {
    if (!container) return function () {};
    if (typeof container.__prksPersonOfflineDispose === 'function') {
        try {
            container.__prksPersonOfflineDispose();
        } catch (_e) {
            /* a stale disposer must not block the new binding */
        }
    }
    function applyAll() {
        prksApplyPersonOfflineState(container);
        prksApplyPersonPanelOfflineState(ctx);
    }
    // Read current state immediately: a page rendered after the runtime already
    // left 'online' is never briefly mutable.
    applyAll();
    let unsubscribe = function () {};
    if (typeof prksOfflineRuntimeSubscribe === 'function') {
        unsubscribe =
            prksOfflineRuntimeSubscribe(function () {
                if (container.__prksPersonOfflineDispose !== dispose) return;
                applyAll();
            }) || function () {};
    }
    let unregister = function () {};
    function dispose() {
        if (container.__prksPersonOfflineDispose === dispose) container.__prksPersonOfflineDispose = null;
        unregister();
        unsubscribe();
    }
    if (ctx && typeof ctx.registerCleanup === 'function') {
        unregister = ctx.registerCleanup(dispose) || function () {};
    }
    container.__prksPersonOfflineDispose = dispose;
    return dispose;
}

/** No cached People index on this device -- distinct from a cached empty one. */
function renderPeopleListUnavailable(container) {
    if (!container) return;
    container.innerHTML =
        '<div class="prks-page-header page-header"><h2 class="prks-page-title">People not available offline</h2></div>' +
        '<p class="prks-inline-message" data-prks-role="offline-unavailable">This list has not been cached on this device.</p>';
}

function personProfileImageSrc(person) {
    if (!person || !person.id) return null;
    const raw = (person.image_url || '').trim();
    if (!raw) return null;
    return '/api/persons/' + encodeURIComponent(String(person.id)) + '/profile-image';
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
    '- first_name, last_name, aliases: plain text strings. Middle names should be placed in first_name field.',
    '- about: MUST review and update when filling this template; DO NOT leave unchanged or empty if you are enriching other fields and reliable sources exist. Short encyclopedic biography for person cards and search (About / expertise). Include who they are, time period, field/domain, 2-4 main ideas/works/contributions, and why they are notable. DO NOT put birth year, death year, or lifespan dates in about; use birth_date and death_date only. Neutral third-person prose, complete sentences, plain text only (JSON string; optional blank line between paragraphs as \\n\\n). No Markdown, bullet lists, or URLs (use link_* and links_other). Target ~80-500 words (~400-2500 characters); avoid one-liners and essay-length dumps. Synthesize from Wikipedia, SEP, IEP, or provided links; do not invent facts. If existing text is present, improve or expand it rather than skipping this field.',
    '- birth_date, death_date: plain date strings only — never prose, never "BC", "BCE", or "AD" suffixes. Allowed: (1) AD year-only positive yyyy, e.g. "1903". (2) BC year-only: leading minus + BC year digits, e.g. source says 428 BC → "-428" (same for 384 BC → "-384"). (3) Full date dd/mm/yyyy; BC uses negative year in last segment, e.g. 15 March 384 BC → "15/03/-384"; AD e.g. "20/02/2001". Wrong: "384 BC", "428 BCE", "-384 BC", "born 428 BC". Use day and month only if source states them; else year-only. Unknown date → "".',
    '- image_url: direct public HTTP/HTTPS portrait URL only (https preferred). Static JPEG/PNG/WebP/GIF raster only. The URL itself must return HTTP 200 with the image; PRKS does not follow redirects. Private/local network URLs are not accepted. MUST verify the link works before filling (use GET or HEAD, or open in browser). If 404, 403, login wall, HTML error page, redirect, or broken hotlink, set image_url to "". Prefer current stable upload.wikimedia.org (or equivalent direct file) URLs taken from the person\'s verified Wikimedia/Wikipedia file page — not guessed filenames, not old revision URLs, not search-result or temporary CDN/signed links. Do not invent URLs from memory. If no working portrait URL is confirmed, leave empty.',
    '- link_wikipedia, link_stanford_encyclopedia, link_iep: bare URL string only (starts with https://). Wrong: [Title](https://...), Markdown, or labels — [Title](url) format is ONLY for links_other. One direct entry URL per field; prefer https; not homepage/search URL. Do not repeat in links_other. Example link_wikipedia: "https://en.wikipedia.org/wiki/Example_Name".',
    "- links_other: one entry per line inside single JSON string. MUST USE [Title](https://...) Markdown link format per line (this is the only field that uses brackets). Only additional references not already in link_wikipedia, link_stanford_encyclopedia, or link_iep. DO NOT duplicate Wikipedia, Stanford Encyclopedia of Philosophy, or IEP links here. DO NOT add free-text notes, DOI links, paper links, or links by this person unless biographical.",
    '- empty fields should stay empty strings; do not fill placeholders such as N/A, unknown, none, -, or TBD.',
    '- aliases: free text; comma-separated aliases recommended. Include Serbian Latin (not Cyrillic) variation if it exists. Include common spelling/transliteration variants and first+last version when middle name exists. Do not include titles/honorifics (Dr., Prof., Sir) as aliases.',
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
    const p = (typeof prksFocusedEntity === 'function' ? prksFocusedEntity('person') : null) || {};
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

function _personTemplateCopyBtn(ev) {
    if (!ev) return null;
    const t = ev.currentTarget || ev.target;
    return t && typeof t.closest === 'function' ? t.closest('.inline-action-btn') : null;
}

async function copyPersonTemplateGuideText(ev) {
    const btn = _personTemplateCopyBtn(ev);
    const s = PERSON_TEMPLATE_HELP_TEXT;
    try {
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            await navigator.clipboard.writeText(s);
            setPersonTemplateFeedback('Template field guide copied.', false);
            if (typeof prksFlashInlineCopyButton === 'function') prksFlashInlineCopyButton(btn, true);
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
        if (typeof prksFlashInlineCopyButton === 'function') prksFlashInlineCopyButton(btn, true);
    } else {
        setPersonTemplateFeedback('Could not copy guide. Copy manually.', true);
        if (typeof prksFlashInlineCopyButton === 'function') prksFlashInlineCopyButton(btn, false);
    }
}

async function copyPersonTemplateAndGuideText(ev) {
    const btn = _personTemplateCopyBtn(ev);
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
            if (typeof prksFlashInlineCopyButton === 'function') prksFlashInlineCopyButton(btn, true);
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
        if (typeof prksFlashInlineCopyButton === 'function') prksFlashInlineCopyButton(btn, true);
    } else {
        setPersonTemplateFeedback('Could not copy template + guide. Copy manually.', true);
        if (typeof prksFlashInlineCopyButton === 'function') prksFlashInlineCopyButton(btn, false);
    }
}

function insertPersonProfileTemplateFromCurrentData() {
    const templateArea = document.getElementById('person-template-json');
    if (!templateArea) return;
    const tpl = buildPersonStandardTemplateFromProfile();
    templateArea.value = JSON.stringify(tpl, null, 2);
    setPersonTemplateFeedback('Template loaded from current profile values.', false);
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
                return `<li><a href="${escapeHtmlPerson(it.href)}" target="_blank" rel="noopener noreferrer"><span>${escapeHtmlPerson(it.label)}</span><span aria-hidden="true">↗</span></a></li>`;
            }
            return `<li>${escapeHtmlPerson(it.label)}</li>`;
        })
        .join('');
    return `
        <div class="person-external-links">
            <h4>References</h4>
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

function personReferenceCount(person) {
    let n = 0;
    if (safeHttpUrl(person.link_wikipedia)) n += 1;
    if (safeHttpUrl(person.link_stanford_encyclopedia)) n += 1;
    if (safeHttpUrl(person.link_iep)) n += 1;
    const otherLines = (person.links_other || '').split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    otherLines.forEach((line) => {
        if (parsePersonOtherLinkLine(line) || safeHttpUrl(line)) n += 1;
    });
    return n;
}

/** Metadata block for person list rows (no title). */
function buildPersonListDetailsHtml(p, options = {}) {
    const showGroups = options.showGroups !== false;
    const roleFilter = options.roleFilter || null;
    const aboutPreview = truncatePersonPreviewText(p.about || '', 180);

    let body = '';
    if (aboutPreview) {
        body += `<p class="meta-row person-card-about">${escapeHtmlPerson(aboutPreview)}</p>`;
    }
    const metaBits = [];
    if (showGroups && Array.isArray(p.groups) && p.groups.length > 0) {
        const tags = p.groups
            .map(
                (g) =>
                    `<a class="tag" data-prks-role="${PERSON_GROUP_LINK_ROLE}" href="#/people/groups/${encodeURIComponent(String(g.id || ''))}">${escapeHtmlPerson(g.name)}</a>`
            )
            .join(' ');
        metaBits.push(`<span class="prks-people-list__groups">${tags}</span>`);
    }
    const assignedRoles = Array.isArray(p.assigned_roles) ? p.assigned_roles : [];
    const visibleRoles = roleFilter
        ? assignedRoles.filter((role) => role !== roleFilter)
        : assignedRoles;
    if (visibleRoles.length) {
        metaBits.push(`<span class="prks-people-list__roles">${visibleRoles.map(escapeHtmlPerson).join(' · ')}</span>`);
    }
    if (metaBits.length) {
        body += `<p class="meta-row prks-people-list__meta-line">${metaBits.join('')}</p>`;
    }
    return body;
}

/** Inner HTML for a person list card (legacy card layout). */
function buildPersonListCardContentHtml(p) {
    const name = `${p.first_name || ''} ${p.last_name || ''}`.trim();
    return `<div class="card-title">${escapeHtmlPerson(name)}</div>${buildPersonListDetailsHtml(p, { showGroups: true })}`;
}

function buildPersonListRowHtml(p, options = {}) {
    const showGroups = options.showGroups !== false;
    const removeButton = options.removeButton === true;
    const name = `${p.first_name || ''} ${p.last_name || ''}`.trim() || 'Person';
    const pidEnc = encodeURIComponent(String(p.id || ''));
    const hash = `#/people/${pidEnc}`;
    const idAttr = escapeHtmlPerson(String(p.id || ''));
    const removableClass = removeButton ? ' prks-people-list__row--removable' : '';
    const removeHtml = removeButton
        ? `<button type="button" class="prks-people-list__remove" data-remove-member="${escapeHtmlPerson(p.id)}" aria-label="Remove from group" title="Remove from group">&times;</button>`
        : '';
    const details = buildPersonListDetailsHtml(p, { showGroups, roleFilter: options.roleFilter });
    const detailsBlock = details
        ? `<div class="prks-people-list__details">${details}</div>`
        : '';
    const lifespan = personLifespanDisplay(p);
    const lifespanHtml = lifespan
        ? `<span class="prks-people-list__lifespan">${escapeHtmlPerson(lifespan)}</span>`
        : '';

    return `
        <div class="prks-people-list__row${removableClass}" role="listitem" data-person-id="${idAttr}">
            ${removeHtml}
            <span class="prks-people-list__toggle-spacer" aria-hidden="true"></span>
            <div class="prks-people-list__body">
                <a class="prks-people-list__link" href="${hash}">
                    <span class="prks-people-list__icon">${typeof prksIcon === 'function' ? prksIcon('user', { size: 16 }) : ''}</span>
                    <span class="prks-people-list__title-row">
                    <span class="prks-people-list__title">${escapeHtmlPerson(name)}</span>
                    ${lifespanHtml}
                    </span>
                </a>
                ${detailsBlock}
            </div>
        </div>`;
}

window.buildPersonListRowHtml = buildPersonListRowHtml;
window.buildPersonListCardContentHtml = buildPersonListCardContentHtml;
window.buildPersonListDetailsHtml = buildPersonListDetailsHtml;

function prksPersonViewInGraph() {
    const p = typeof prksFocusedEntity === 'function' ? prksFocusedEntity('person') : null;
    if (!p || !p.id) return;
    const hash =
        typeof window.prksGraphFocusHash === 'function'
            ? window.prksGraphFocusHash('person', p.id)
            : '#/graph?focus=' + encodeURIComponent('person:' + p.id);
    if (typeof window.prksNavigate === 'function') window.prksNavigate(hash);
}
window.prksPersonViewInGraph = prksPersonViewInGraph;

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

const PRKS_PEOPLE_LIBRARY_FILTER_KEY = 'prks-people-library-filter';

function prksPeopleLibraryFilterFromStorage() {
    try {
        return sessionStorage.getItem(PRKS_PEOPLE_LIBRARY_FILTER_KEY) || '';
    } catch (_e) {
        return '';
    }
}

function prksPeopleListMatchesQuery(p, query) {
    const q = String(query || '').trim().toLowerCase();
    if (!q || !p) return true;
    const name = `${p.first_name || ''} ${p.last_name || ''}`.trim().toLowerCase();
    const hay = [
        name,
        p.aliases,
        p.about,
        ...(Array.isArray(p.assigned_roles) ? p.assigned_roles : []),
        ...(Array.isArray(p.groups) ? p.groups.map((g) => g && g.name) : []),
    ];
    return hay.some((v) => v != null && String(v).toLowerCase().includes(q));
}

function prksPeopleListEmptyHtml(persons, filterQuery, roleFilter) {
    const q = String(filterQuery || '').trim();
    const all = Array.isArray(persons) ? persons : [];
    const roleFiltered = filterPersonsByAssignedRole(all, roleFilter);
    if (roleFilter && roleFiltered.length === 0) {
        return `<p class="prks-inline-message prks-people-list__empty">No people with the <strong>${escapeHtmlPerson(roleFilter)}</strong> role yet. Use <strong>Link Person to Work</strong> in the ribbon to assign roles.</p>`;
    }
    if (q) {
        return '<p class="prks-inline-message prks-people-list__empty">No people match your search.</p>';
    }
    return '<div class="prks-people-list__empty-state"><p class="prks-inline-message prks-people-list__empty">No people yet.</p><button type="button" class="prks-btn prks-btn--primary" data-prks-role="' + PERSON_CREATE_ROLE + '" onclick="openModal(\'person-modal\')">New Person</button></div>';
}

function prksPeopleListInnerHtml(persons, filterQuery, roleFilter) {
    const all = Array.isArray(persons) ? persons : [];
    let list = filterPersonsByAssignedRole(all, roleFilter);
    const q = String(filterQuery || '').trim();
    if (q) {
        list = list.filter((p) => prksPeopleListMatchesQuery(p, q));
    }
    if (!list.length) {
        return prksPeopleListEmptyHtml(all, filterQuery, roleFilter);
    }
    return `<div class="prks-people-list" role="list">${list.map((p) => buildPersonListRowHtml(p, { roleFilter })).join('')}</div>`;
}

function prksRerenderPeopleListOnly(root) {
    const st = root && root.__prksPeopleLibraryState;
    if (!st) return;
    const host = root.querySelector('[data-prks-people-list-host]');
    if (host) {
        host.innerHTML = prksPeopleListInnerHtml(st.persons, st.filterQuery, st.roleFilter);
        // Rerendered rows carry fresh Group chips, so re-apply connectivity
        // state to them.
        prksApplyPersonOfflineState(host);
        if (typeof prksRefreshIcons === 'function') prksRefreshIcons(host);
    }
}

function prksSyncPeopleLibrarySearchClear(input, clearBtn) {
    if (!clearBtn) return;
    const hasValue = Boolean(String((input && input.value) || '').trim());
    clearBtn.hidden = !hasValue;
    clearBtn.disabled = !hasValue;
}

function prksApplyPeopleLibrarySearchFilter(input) {
    const root = input && input.closest('.prks-people-library');
    const st = root && root.__prksPeopleLibraryState;
    if (!st || !input) return;
    const q = String(input.value || '');
    st.filterQuery = q;
    try {
        sessionStorage.setItem(PRKS_PEOPLE_LIBRARY_FILTER_KEY, q);
    } catch (_e) {
        /* ignore */
    }
    prksRerenderPeopleListOnly(root);
}

function prksBindPeopleLibrarySearch(root) {
    if (!root) return;
    const input = root.querySelector('#prks-people-library-search');
    const clearBtn = root.querySelector('#prks-people-library-search-clear');
    if (!input || input.dataset.bound === '1') return;
    input.dataset.bound = '1';
    let debounceTimer;
    const scheduleFilter = () => {
        window.clearTimeout(debounceTimer);
        debounceTimer = window.setTimeout(() => prksApplyPeopleLibrarySearchFilter(input), 150);
    };
    input.addEventListener('input', () => {
        prksSyncPeopleLibrarySearchClear(input, clearBtn);
        scheduleFilter();
    });
    if (clearBtn && clearBtn.dataset.bound !== '1') {
        clearBtn.dataset.bound = '1';
        clearBtn.addEventListener('click', () => {
            input.value = '';
            prksSyncPeopleLibrarySearchClear(input, clearBtn);
            input.focus();
            prksApplyPeopleLibrarySearchFilter(input);
        });
    }
    prksSyncPeopleLibrarySearchClear(input, clearBtn);
}

/**
 * `ctx` is the owning TabContext: the list subscribes to connectivity so its
 * creation control and Group chips follow live state, and that subscription is
 * registered with the route's context rather than leaked globally.
 */
function renderPeopleList(ctx, persons, container, options = {}) {
    const roleFilter = options.roleFilter || null;
    const list = Array.isArray(persons) ? persons : [];
    const filterQuery = prksPeopleLibraryFilterFromStorage();
    const filterEsc = escapeHtmlPerson(filterQuery);
    const titleExtra = roleFilter ? ` — ${PEOPLE_LIST_ROLE_LABELS[roleFilter] || roleFilter}` : '';
    const roleFiltered = filterPersonsByAssignedRole(list, roleFilter);
    const hasPeople = roleFiltered.length > 0;
    const searchToolbar = hasPeople
        ? `<div class="prks-people-library__toolbar">
            <div class="tag-add-shell tag-add-shell--flush prks-people-library__search">
                <div class="tag-add-shell__field">
                    ${typeof prksTagSearchIconHtml === 'function' ? prksTagSearchIconHtml() : ''}
                    <input type="text" id="prks-people-library-search" class="tag-add-shell__input" placeholder="Search people…" value="${filterEsc}" maxlength="300" autocomplete="off" aria-label="Filter people">
                    <button type="button" class="tag-add-shell__clear" id="prks-people-library-search-clear" aria-label="Clear search" title="Clear search" hidden>&times;</button>
                </div>
            </div>
        </div>`
        : '';
    const listHost = hasPeople
        ? `<div class="prks-people-library__scroll" data-prks-people-list-host>${prksPeopleListInnerHtml(list, filterQuery, roleFilter)}</div>`
        : `<div class="prks-people-library__empty">${prksPeopleListEmptyHtml(list, filterQuery, roleFilter)}</div>`;

    container.innerHTML = `
        <div class="prks-people-library">
        <div class="prks-page-header page-header prks-people-library__header">
            <h2 class="prks-page-title">People${escapeHtmlPerson(titleExtra)}</h2>
            <button type="button" class="prks-btn prks-btn--primary" data-prks-role="${PERSON_CREATE_ROLE}" onclick="openModal('person-modal')">New Person</button>
        </div>
        ${searchToolbar}
        ${listHost}
        </div>`;

    const root = container.querySelector('.prks-people-library');
    if (root) {
        // Offline search stays entirely client-side over the already-loaded
        // (possibly cached) array -- it issues no API requests.
        root.__prksPeopleLibraryState = { persons: list, container, filterQuery, roleFilter };
        prksBindPeopleLibrarySearch(root);
    }
    prksBindPersonOfflineState(ctx, container);
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(container);
}

function prksPersonDraftFromEntity(person) {
    const value = (key) => String(person && person[key] != null ? person[key] : '');
    return {
        personId: value('id'),
        first_name: value('first_name'),
        last_name: value('last_name'),
        aliases: value('aliases'),
        about: value('about'),
        birth_date: personDateToDisplayFormat(value('birth_date')),
        death_date: personDateToDisplayFormat(value('death_date')),
        image_url: value('image_url'),
        link_wikipedia: value('link_wikipedia'),
        link_stanford_encyclopedia: value('link_stanford_encyclopedia'),
        link_iep: value('link_iep'),
        links_other: value('links_other'),
        groups: (Array.isArray(person && person.groups) ? person.groups : []).map((group) => ({
            id: group.id,
            name: String(group.name == null ? '' : group.name),
        })),
    };
}

function prksPersonProfileDraftIsDirty(ctx, person) {
    if (!ctx || !ctx.ui || !person || person.id == null) return false;
    const draft = ctx.ui.personProfileDraft;
    if (!draft || String(draft.personId) !== String(person.id)) return false;
    const original = prksPersonDraftFromEntity(person);
    const scalarFields = [
        'first_name',
        'last_name',
        'aliases',
        'about',
        'birth_date',
        'death_date',
        'image_url',
        'link_wikipedia',
        'link_stanford_encyclopedia',
        'link_iep',
        'links_other',
    ];
    if (scalarFields.some((key) => String(draft[key] == null ? '' : draft[key]) !== original[key])) {
        return true;
    }
    const groupIdSet = (groups) =>
        Array.from(
            new Set(
                (Array.isArray(groups) ? groups : [])
                    .filter((group) => group && group.id != null)
                    .map((group) => String(group.id))
            )
        ).sort();
    const draftGroupIds = groupIdSet(draft.groups);
    const originalGroupIds = groupIdSet(original.groups);
    return (
        draftGroupIds.length !== originalGroupIds.length ||
        draftGroupIds.some((id, index) => id !== originalGroupIds[index])
    );
}

function prksEnsurePersonProfileDraft(ctx, person) {
    if (!ctx || !ctx.ui || !person || person.id == null) return null;
    const personId = String(person.id);
    const draft = ctx.ui.personProfileDraft;
    if (!draft || String(draft.personId) !== personId) {
        ctx.ui.personProfileDraft = prksPersonDraftFromEntity(person);
    }
    return ctx.ui.personProfileDraft;
}

function prksPersonProfileEditSessionCurrent(ctx, generation, personId, draft) {
    if (!ctx || !ctx.ui || !ctx.ui.personDetailEditing) return false;
    if (!draft || ctx.ui.personProfileDraft !== draft || String(draft.personId) !== String(personId)) return false;
    if (typeof prksTabContextOwnsEntityRoute === 'function') {
        return prksTabContextOwnsEntityRoute(ctx, generation, 'person', personId, 'person');
    }
    const person = ctx.getEntity && ctx.getEntity('person');
    return !!(
        person &&
        String(person.id) === String(personId) &&
        ctx.isCurrent &&
        ctx.isCurrent(generation)
    );
}

function prksPersonProfileEditorCurrent(ctx, generation, personId, draft, editor) {
    if (!prksPersonProfileEditSessionCurrent(ctx, generation, personId, draft) || !editor) return false;
    if (editor.isConnected === false || String(editor.getAttribute('data-person-edit-id') || '') !== String(personId)) {
        return false;
    }
    const panel = document.getElementById('panel-content');
    if (!panel || typeof prksRightPanelOwnedBy !== 'function' || !prksRightPanelOwnedBy(ctx, editor)) return false;
    return panel.querySelector('.person-panel-edit') === editor;
}

const PRKS_PERSON_DRAFT_FIELDS = {
    'pd-first-name': 'first_name',
    'pd-last-name': 'last_name',
    'pd-aliases': 'aliases',
    'pd-about': 'about',
    'pd-birth-date': 'birth_date',
    'pd-death-date': 'death_date',
    'pd-image-url': 'image_url',
    'pd-link-wikipedia': 'link_wikipedia',
    'pd-link-stanford': 'link_stanford_encyclopedia',
    'pd-link-iep': 'link_iep',
    'pd-links-other': 'links_other',
};

function prksSyncPersonProfileDraftFromEditor(ctx, editor, personId, generation) {
    const draft = ctx && ctx.ui && ctx.ui.personProfileDraft;
    if (!prksPersonProfileEditorCurrent(ctx, generation, personId, draft, editor)) return false;
    Object.entries(PRKS_PERSON_DRAFT_FIELDS).forEach(([id, key]) => {
        const field = editor.querySelector('#' + id);
        if (field) draft[key] = field.value;
    });
    return true;
}

async function prksMountPersonProfileEditor(ctx, person) {
    if (!ctx || !person || person.id == null) return;
    const generation = ctx.generation;
    const personId = String(person.id);
    const draft = prksEnsurePersonProfileDraft(ctx, person);
    if (!prksPersonProfileEditSessionCurrent(ctx, generation, personId, draft)) return;
    const panel = document.getElementById('panel-content');
    if (!panel || typeof prksRightPanelOwnedBy !== 'function' || !prksRightPanelOwnedBy(ctx, panel)) return;
    const editor = panel.querySelector('.person-panel-edit');
    if (!prksPersonProfileEditorCurrent(ctx, generation, personId, draft, editor)) return;
    const sync = () => prksSyncPersonProfileDraftFromEditor(ctx, editor, personId, generation);
    editor.addEventListener('input', sync);
    editor.addEventListener('change', sync);
    if (typeof prksMountPersonProfileGroupPicker === 'function') {
        await prksMountPersonProfileGroupPicker(ctx, person, editor);
    }
}

function openPersonProfileEdit() {
    const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    const person = ctx && ctx.getEntity ? ctx.getEntity('person') : null;
    if (!ctx || !ctx.ui || !person) return;
    /* No connectivity guard. Profile fields are durable now, so a cached
     * profile is editable exactly as a connected one is -- the same feature,
     * not two. What CAN stop a save is not knowing this Person's revisions,
     * and that is decided at save time by `prksAcknowledgedPersonBase`,
     * because it is a fact about this device's cache rather than about the
     * network. The group controls inside the editor stay connection-required:
     * a membership is a relationship, not a profile scalar. */
    if (ctx && ctx.ui) {
        ctx.ui.personWorksEditing = false;
        ctx.ui.personDetailEditing = true;
    }
    prksEnsurePersonProfileDraft(ctx, person);
    /* Warm the revisions this edit will be measured against, the way the Work
     * metadata editor prepares its own base. Opening the editor is the moment
     * the user declares an intent to change something, and it is the last
     * moment the device is reliably able to ask: a save attempted after a
     * disconnect can only use what was cached before it. */
    if (typeof prksReadPersonMetadataState === 'function' && person.id) {
        void prksReadPersonMetadataState(person.id);
    }
    /* And the membership revisions, for the same reason: opening the editor is
     * the last moment this device can reliably ask. */
    if (typeof prksReadPersonGroupsStateForPerson === 'function' && person.id) {
        void prksReadPersonGroupsStateForPerson(person.id);
    }
    if (typeof updatePanelContent === 'function') updatePanelContent('details');
    /* Settle the freshly rendered editor against the CURRENT connectivity.
     * The binding only reacts to changes, so an editor opened while already
     * offline would otherwise show its group controls live until connectivity
     * happened to change again. */
    prksApplyPersonPanelOfflineState(ctx);
}

function closePersonProfileEdit() {
    const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    if (ctx && ctx.ui) {
        ctx.ui.personDetailEditing = false;
        ctx.ui.personProfileDraft = null;
    }
    if (typeof updatePanelContent === 'function') updatePanelContent('details');
}

window.prksEnsurePersonProfileDraft = prksEnsurePersonProfileDraft;
window.prksPersonProfileDraftIsDirty = prksPersonProfileDraftIsDirty;
window.prksPersonProfileEditSessionCurrent = prksPersonProfileEditSessionCurrent;
window.prksPersonProfileEditorCurrent = prksPersonProfileEditorCurrent;
window.prksSyncPersonProfileDraftFromEditor = prksSyncPersonProfileDraftFromEditor;
window.prksMountPersonProfileEditor = prksMountPersonProfileEditor;

async function deletePerson() {
    const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    const generation = ctx && ctx.generation;
    const p = ctx && ctx.getEntity ? ctx.getEntity('person') : null;
    const personId = p && p.id ? String(p.id) : '';
    if (!personId) return;
    /* No connectivity guard: deleting a Person is durable now. The PROTECTION
     * is unchanged and still canonical -- a Person credited on a file cannot be
     * deleted -- and the check below is the local, earlier half of it, exactly
     * as it was when this made a request. */
    const linkedWorks = p ? prksUniquePersonWorks(p).length : 0;
    if (linkedWorks > 0) {
        await prksAlertMessage('Cannot delete person with linked files. Unlink all files first.', 'Not allowed');
        return;
    }
    const confirmed = await prksConfirmDestructive({
        title: 'Delete person?',
        message: 'Delete this person permanently?',
        confirmLabel: 'Delete person',
    });
    if (!confirmed) return;
    if (
        typeof prksTabContextOwnsEntityRoute === 'function' &&
        !prksTabContextOwnsEntityRoute(ctx, generation, 'person', personId, 'person')
    ) return;
    try {
        /* A tombstone, not a destruction: the acknowledged cache is untouched,
         * so a server that refuses the deletion restores them by doing nothing.
         * A Person created here and never sent folds away entirely. */
        await prksDeletePersonDurably(personId);
    } catch (error) {
        if (ctx && ctx.isCurrent && ctx.isCurrent(generation)) {
            await prksAlertMessage(
                (error && error.prksLocalStoreCode === 'entity_deleted')
                    ? 'This person is already being deleted.'
                    : 'Could not delete this person locally. Please retry.', 'Could not delete');
        }
        return;
    }
    if (
        typeof prksTabContextOwnsEntityRoute === 'function' &&
        !prksTabContextOwnsEntityRoute(ctx, generation, 'person', personId, 'person')
    ) return;
    if (ctx && typeof ctx.setEntity === 'function') ctx.setEntity('person', null);
    if (ctx && ctx.ui) {
        ctx.ui.personDetailEditing = false;
        ctx.ui.personWorksEditing = false;
    }
    if (typeof prksNavigate === 'function') prksNavigate('#/people', { replace: true, tabId: ctx.tabId });
}

function prksTogglePersonWorksEdit() {
    const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    const p = ctx && ctx.getEntity ? ctx.getEntity('person') : null;
    if (!p || !ctx) return;
    const nowEditing = !!(ctx.ui && ctx.ui.personWorksEditing);
    if (nowEditing) {
        // Leaving relationship-edit mode stays possible while offline.
        ctx.ui.personWorksEditing = false;
    } else {
        if (!p.works || p.works.length === 0) return;
        if (prksPersonMutationBlocked('Editing relationships requires a connection to PRKS.')) return;
        ctx.ui.personWorksEditing = true;
    }
    const root = ctx.root;
    if (root && typeof renderPersonDetails === 'function') {
        renderPersonDetails(ctx, p, root);
    }
    if (typeof updatePanelContent === 'function') {
        updatePanelContent('details');
    }
}

function prksPersonAdvancedKeydown(event) {
    if (!event || event.key !== 'Escape') return;
    const details = event.currentTarget;
    if (!details || !details.open) return;
    event.preventDefault();
    details.open = false;
    const summary = details.querySelector('summary');
    if (summary) summary.focus();
}
window.prksPersonAdvancedKeydown = prksPersonAdvancedKeydown;

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
    const nWorks = prksUniquePersonWorks(person).length;
    const nGroups = Array.isArray(person.groups) ? person.groups.length : 0;
    const nRefs = personReferenceCount(person);
    const deleteBtn = nWorks === 0
        ? `<button type="button" class="prks-btn prks-btn--danger person-sidebar__advanced-action" data-prks-role="${PERSON_DELETE_ROLE}" onclick="deletePerson()">Delete person</button>`
        : `<button type="button" class="prks-btn prks-btn--danger person-sidebar__advanced-action" disabled title="Unlink all files first">Delete person</button>`;
    return `
        <div class="doc-meta-card person-sidebar-summary">
            <p class="saved-view-detail__kicker">Profile</p>
            <ul class="person-sidebar__stats">
                <li>${nWorks} linked file${nWorks === 1 ? '' : 's'}</li>
                <li>${nGroups} group${nGroups === 1 ? '' : 's'}</li>
                <li>${nRefs} reference${nRefs === 1 ? '' : 's'}</li>
            </ul>
            <button type="button" class="prks-btn prks-btn--primary person-sidebar__cta" data-prks-role="${PERSON_EDIT_ROLE}" onclick="openPersonProfileEdit()">Edit profile</button>
            <button type="button" class="prks-btn prks-btn--secondary person-sidebar__cta" id="prks-person-view-graph" onclick="prksPersonViewInGraph()">View in graph</button>
            <details class="person-sidebar__advanced" onkeydown="prksPersonAdvancedKeydown(event)">
                <summary>More</summary>
                <div class="person-sidebar__advanced-actions">
                    <button type="button" class="prks-btn prks-btn--secondary person-sidebar__advanced-action" onclick="openPersonProfileTemplateModal()">Edit using template</button>
                    ${deleteBtn}
                </div>
            </details>
            <p class="route-sidebar__action"><a href="#/people" class="route-sidebar__link">All people</a></p>
        </div>`;
}

function renderPersonProfileEditFormHtml(person, draft) {
    if (!person) return '';
    const id = escapeHtmlPerson(person.id);
    const state = draft && String(draft.personId) === String(person.id) ? draft : prksPersonDraftFromEntity(person);
    return `
        <div class="doc-meta-card person-panel-edit" data-person-edit-id="${id}">
            <div class="card-heading-row card-heading-row--wrap">
                <h3>Edit profile</h3>
            </div>
            <div class="form-pane person-edit-form person-edit-form--panel">
                <section class="person-edit-section" aria-labelledby="person-edit-identity-heading">
                    <h4 id="person-edit-identity-heading">Identity</h4>
                    <label for="pd-first-name">First name</label>
                    <input type="text" id="pd-first-name" value="${escapeHtmlPerson(state.first_name)}">
                    <label for="pd-last-name">Last name</label>
                    <input type="text" id="pd-last-name" value="${escapeHtmlPerson(state.last_name)}">
                    <label for="pd-aliases">Aliases</label>
                    <input type="text" id="pd-aliases" value="${escapeHtmlPerson(state.aliases)}">
                </section>
                <section class="person-edit-section" aria-labelledby="person-edit-biography-heading">
                    <h4 id="person-edit-biography-heading">Biography</h4>
                    <label for="pd-about">About / expertise</label>
                    <textarea id="pd-about" class="textarea-sm">${escapeHtmlPerson(state.about)}</textarea>
                </section>
                <section class="person-edit-section" aria-labelledby="person-edit-dates-heading">
                    <h4 id="person-edit-dates-heading">Dates</h4>
                    <div class="form-grid-2 form-grid-2--compact">
                        <div><label for="pd-birth-date">Birth date</label><input type="text" id="pd-birth-date" placeholder="dd/mm/yyyy or yyyy" autocomplete="off" value="${escapeHtmlPerson(state.birth_date)}"></div>
                        <div><label for="pd-death-date">Date of death</label><input type="text" id="pd-death-date" placeholder="dd/mm/yyyy or yyyy" autocomplete="off" value="${escapeHtmlPerson(state.death_date)}"></div>
                    </div>
                </section>
                <section class="person-edit-section" aria-labelledby="person-edit-portrait-heading">
                    <h4 id="person-edit-portrait-heading">Portrait</h4>
                    <label for="pd-image-url">Portrait image URL</label>
                    <input type="url" id="pd-image-url" value="${escapeHtmlPerson(state.image_url)}">
                </section>
                <section class="person-edit-section" aria-labelledby="person-edit-references-heading">
                    <h4 id="person-edit-references-heading">References</h4>
                    <label for="pd-link-wikipedia">Wikipedia</label>
                    <input type="url" id="pd-link-wikipedia" value="${escapeHtmlPerson(state.link_wikipedia)}">
                    <label for="pd-link-stanford">Stanford Encyclopedia of Philosophy</label>
                    <input type="url" id="pd-link-stanford" value="${escapeHtmlPerson(state.link_stanford_encyclopedia)}">
                    <label for="pd-link-iep">Internet Encyclopedia of Philosophy</label>
                    <input type="url" id="pd-link-iep" value="${escapeHtmlPerson(state.link_iep)}">
                    <label for="pd-links-other">Other links</label>
                    <textarea id="pd-links-other" placeholder="One URL per line, or [Title](https://...)" class="textarea-sm">${escapeHtmlPerson(state.links_other)}</textarea>
                </section>
                <section class="person-edit-section" aria-labelledby="person-edit-groups-heading">
                    <h4 id="person-edit-groups-heading">Groups</h4>
                    <fieldset class="person-groups-fieldset">
                        <legend class="sr-only">Groups</legend>
                        <p class="meta-row">Search for a group, pick from the list, or type a new name and <strong>Add</strong> to create a top-level group. Names are unique. <a href="#/people/groups">Browse groups</a>.</p>
                        <div id="pd-group-chips" class="tag-cloud person-groups-fieldset__chips"></div>
                        <label for="pd-group-search">Add group</label>
                        <div class="tag-add-shell combobox-container tag-add-shell--flush prks-inline-combobox-shell"><div class="tag-add-shell__field">${typeof prksTagSearchIconHtml === 'function' ? prksTagSearchIconHtml() : ''}<input type="text" id="pd-group-search" class="tag-add-shell__input" placeholder="Search or type new group name…" autocomplete="off" aria-label="Search group to add"></div><input type="hidden" id="pd-group-pick-id" value=""><div id="pd-group-results" class="combobox-results combobox-results--tag-panel hidden"></div></div>
                        <button type="button" class="prks-btn prks-btn--primary person-groups-fieldset__action" id="pd-group-add-btn">Add group</button>
                    </fieldset>
                </section>
            </div>
            <div class="form-actions prks-form-actions--split person-edit-footer"><button type="button" data-prks-person-cancel onclick="closePersonProfileEdit()" class="prks-btn prks-btn--secondary">Cancel</button><button type="button" id="pd-save-btn" class="prks-btn prks-btn--primary" onclick="savePersonProfile('${id}')">Save profile</button></div>
        </div>`;
}

/* The profile fields this editor owns, in the durable family's vocabulary.
 * One list: a field the form can change but the queue cannot carry would be
 * savable online and impossible offline. */
const PRKS_PERSON_PROFILE_FIELDS = Object.freeze([
    'first_name', 'last_name', 'aliases', 'about', 'image_url',
    'link_wikipedia', 'link_stanford_encyclopedia', 'link_iep',
    'links_other', 'birth_date', 'death_date',
]);

/**
 * The acknowledged base and the pending intent, read together.
 *
 * Deliberately NOT `ctx.getEntity('person')`: that record is the EFFECTIVE
 * one, already overlaid with this device's unsynchronized edits, and a base
 * taken from it would make a pending value look like the server's own. The
 * shared helper owns that distinction -- and what a pending operation means --
 * so a component cannot grow a second interpretation of either.
 *
 * Returns `{base, operations}`, with a null `base` when it is not knowable.
 */
async function prksReadPersonProfileBase(personId) {
    if (typeof prksAcknowledgedPersonBase !== 'function') {
        return { base: null, operations: [] };
    }
    const operations = await prksDurableOperationsOrNone();
    return { base: await prksAcknowledgedPersonBase(personId, operations), operations };
}

/**
 * Group membership, as one durable intent per PAIR.
 *
 * A membership is a relationship, not a profile scalar: the conflict unit is
 * `(group, person)`, so two devices that added different people to one group
 * have not collided and a profile-wide replacement would have made them look
 * as though they had. Each pair the form changed becomes its own operation,
 * measured against its own acknowledged state.
 *
 * Returns true when every change was recorded. A pair that could not be
 * measured -- this device has never read that group's revisions -- is reported
 * and the editor stays open, because the profile half is already durable and
 * the part worth retrying is the part that failed.
 */
async function prksSavePersonGroupMemberships(ctx, personId, groupIds) {
    const before = prksPersonEditGroupIds(ctx);
    const after = (groupIds || []).map(String);
    const added = after.filter(id => before.indexOf(id) === -1);
    const removed = before.filter(id => after.indexOf(id) === -1);
    if (!added.length && !removed.length) return true;
    const failures = [];
    for (const groupId of added.concat(removed)) {
        const present = added.indexOf(groupId) !== -1;
        try {
            if (!await prksSetPersonGroupMembership(groupId, personId, present, true)) {
                failures.push(groupId);
            }
        } catch (_e) {
            failures.push(groupId);
        }
    }
    if (failures.length) {
        /* Reports only what it owns, once. The profile half is already durable
         * by the time this runs, so "could not save" would be wrong twice
         * over -- and one dialog per pair would be worse than useless. */
        await prksAlertMessage(
            failures.length === 1
                ? 'The profile was saved, but one group change could not be recorded. '
                  + 'Open that group once while connected to PRKS and try again.'
                : 'The profile was saved, but ' + failures.length + ' group changes could '
                  + 'not be recorded. Open those groups once while connected to PRKS and '
                  + 'try again.',
            'Groups not updated');
    }
    return failures.length === 0;
}

/** The groups the Person record on screen says they are in. */
function prksPersonEditGroupIds(ctx) {
    const person = ctx && ctx.getEntity ? ctx.getEntity('person') : null;
    return (person && Array.isArray(person.groups) ? person.groups : [])
        .map(g => String(g && g.id));
}

async function savePersonProfile(personId) {
    const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    const generation = ctx && ctx.generation;
    const root = ctx && ctx.root;
    if (
        typeof prksTabContextOwnsEntityRoute !== 'function' ||
        !prksTabContextOwnsEntityRoute(ctx, generation, 'person', personId, 'person')
    ) return;
    const panel = document.getElementById('panel-content');
    if (!panel || typeof prksRightPanelOwnedBy !== 'function' || !prksRightPanelOwnedBy(ctx, panel)) return;
    const editor = panel.querySelector('.person-panel-edit');
    const draft = ctx && ctx.ui && ctx.ui.personProfileDraft;
    if (!draft || String(draft.personId) !== String(personId)) return;
    if (!prksSyncPersonProfileDraftFromEditor(ctx, editor, personId, generation)) return;
    const birthIso = parsePersonBirthDeathField(draft.birth_date);
    if (birthIso === null) {
        await prksAlertMessage(`Birth:\n${PERSON_DATE_HELP}`, 'Validation');
        return;
    }
    const deathIso = parsePersonBirthDeathField(draft.death_date);
    if (deathIso === null) {
        await prksAlertMessage(`Date of death:\n${PERSON_DATE_HELP}`, 'Validation');
        return;
    }
    const payload = {
        first_name: draft.first_name,
        last_name: draft.last_name,
        aliases: draft.aliases,
        about: draft.about,
        image_url: draft.image_url,
        link_wikipedia: draft.link_wikipedia,
        link_stanford_encyclopedia: draft.link_stanford_encyclopedia,
        link_iep: draft.link_iep,
        links_other: draft.links_other,
        birth_date: birthIso,
        death_date: deathIso,
        group_ids: (Array.isArray(draft.groups) ? draft.groups : []).map((group) => group.id)
    };
    if (!payload.last_name.trim()) {
        await prksAlertMessage('Last name is required.', 'Validation');
        return;
    }
    const btn = panel.querySelector('#pd-save-btn');
    if (btn && typeof prksSetButtonBusy === 'function') {
        prksSetButtonBusy(btn, true, { busyLabel: 'Saving\u2026' });
    }
    try {
        /* Durable-first, with or without the server, through ONE path.
         *
         * Saving creates the intent and updates effective local state; putting
         * it on the wire is subsequent work. A connectivity guard here would
         * make the same edit two different features -- and a refetch as the
         * completion condition would make Save fail for a reason the user
         * cannot act on. */
        const desired = {};
        PRKS_PERSON_PROFILE_FIELDS.forEach(function (name) {
            desired[name] = name === 'birth_date' ? birthIso
                : name === 'death_date' ? deathIso
                : String(draft[name] == null ? '' : draft[name]);
        });
        const { base, operations } = await prksReadPersonProfileBase(personId);
        if (!base) {
            /* Unknown is not empty. Without this Person's revisions, an edit
             * would have to guess revision 0 and could silently overwrite what
             * another device wrote -- which is the one thing a base revision
             * exists to prevent. */
            await prksAlertMessage(
                'This profile cannot be edited offline yet. Open it once while connected to '
                + 'PRKS so its synchronization state is prepared.', 'Unavailable');
            return;
        }
        /* Only what this editing session actually changed. Saving all eleven
         * fields every time would let one syncing or conflicted field refuse
         * the whole form -- and would rewrite an untouched field's intent
         * against a base it was never measured against. */
        const changes = prksDirtyPersonFields(personId, desired, base, operations);
        try {
            if (Object.keys(changes).length) {
                await prksSavePersonFieldsDurably(personId, changes, base);
            }
        } catch (error) {
            const code = error && error.prksLocalStoreCode;
            await prksAlertMessage(
                code === 'scope_busy'
                    ? 'One of these fields is syncing or needs a decision. Try again shortly.'
                    : code === 'dependency_failed'
                      ? 'This person could not be created on PRKS, so their profile cannot be '
                        + 'edited. Discard that creation in Sync Diagnostics.'
                      : 'Could not save this profile locally. Please retry.',
                'Could not save');
            return;
        }
        /* Group membership is a RELATIONSHIP, not a profile scalar, and is not
         * part of this milestone's durable vocabulary. It stays a canonical
         * request -- and the controls that change it are disabled offline --
         * so the two never half-apply: the profile is already durable by the
         * time this runs, and a failure here reports only what it owns. */
        /* The editor stays OPEN when only the membership half failed. The
         * profile is already durable, so re-saving it costs nothing -- there
         * is no second intent to create for a value that is already the
         * observed one -- and keeping the draft is what lets the user retry
         * the part that actually failed. */
        if (!await prksSavePersonGroupMemberships(ctx, personId,
                (Array.isArray(draft.groups) ? draft.groups : []).map(g => g.id))) {
            return;
        }
        if (
            typeof prksTabContextOwnsEntityRoute === 'function' &&
            !prksTabContextOwnsEntityRoute(ctx, generation, 'person', personId, 'person')
        ) return;
        /* The record the user now sees: what was cached, overlaid with the
         * intent just written. No refetch -- there is nothing to fetch, the
         * change is local, and a reload would throw away every other pending
         * change on the page. */
        /* Rebuilt from the ACKNOWLEDGED record, not from the one on screen:
         * that one is already overlaid, and an overlay can add or remove but
         * cannot restore what an earlier overlay removed -- so a membership
         * this save cancelled would survive on the page. */
        const person = await prksPersonRecordFor(personId);
        if (person) {
            if (ctx && typeof ctx.setEntity === 'function') ctx.setEntity('person', person);
            if (ctx) ctx.routeSidebar = {
                personDisplayName:
                    typeof personDisplayName === 'function' ? personDisplayName(person) || 'Person' : 'Person',
                linkedWorks: prksUniquePersonWorks(person).length
            };
        }
        if (ctx && ctx.ui && ctx.ui.personProfileDraft === draft) {
            ctx.ui.personDetailEditing = false;
            ctx.ui.personProfileDraft = null;
        }
        if (person && root && ctx && ctx.mounted) {
            renderPersonDetails(ctx, person, root);
        }
        if (
            typeof updatePanelContent === 'function' &&
            typeof prksRightPanelOwnedBy === 'function' &&
            prksRightPanelOwnedBy(ctx)
        ) {
            updatePanelContent('details');
        }
    } catch (_e) {
        if (
            prksPersonProfileEditSessionCurrent(ctx, generation, personId, draft) &&
            typeof prksRightPanelOwnedBy === 'function' &&
            prksRightPanelOwnedBy(ctx)
        ) {
            await prksAlertMessage('Could not save profile.', 'Error');
        }
    } finally {
        if (btn && typeof prksSetButtonBusy === 'function') {
            prksSetButtonBusy(btn, false);
        }
    }
}

function personRoleBlockHtml(heading, count, cardsHtml) {
    return (
        `<section class="person-profile__role-block">` +
        `<h3 class="person-profile__role-heading"><span>${escapeHtmlPerson(heading)}</span><span class="person-profile__role-count">${count}</span></h3>` +
        `<div class="card-grid">${cardsHtml}</div>` +
        `</section>`
    );
}

function renderPersonDetails(ctx, person, container) {
    if (!container) return;
    if (!person) {
        if (ctx && typeof ctx.setEntity === 'function') ctx.setEntity('person', null);
        if (ctx && ctx.ui) ctx.ui.personWorksEditing = false;
        container.innerHTML = '<div class="prks-page-header page-header"><h2 class="prks-page-title">Person not found</h2></div>';
        return;
    }
    if (ctx && typeof ctx.setEntity === 'function') ctx.setEntity('person', person);

    const worksEditing = !!(ctx && ctx.ui && ctx.ui.personWorksEditing);
    // A page mounted from cached data while PRKS is unreachable must not ask
    // the server for portrait or thumbnail bytes it cannot get -- a broken
    // image is worse than the ordinary no-photo presentation. Media already
    // loaded online is never rerendered away just because connectivity changed.
    const offlineCached = !!(ctx && ctx.ui && ctx.ui.personOfflineCached);
    const rolesByWork = prksPersonWorkRolesById(person);
    let worksHtml = '';
    /* Acknowledged summaries + pending Work-field edits + pending RELATIONSHIP
     * intents; the cached Person is left exactly as the server sent it.
     * Membership first: a Work this Person was just linked to has to appear
     * before its own fields are overlaid onto it. */
    const membership = typeof prksEffectivePersonWorks === 'function'
        ? prksEffectivePersonWorks(person, person.works || []) : (person.works || []);
    const personWorks = typeof prksEffectiveWorkSummaryRows === 'function'
        ? prksEffectiveWorkSummaryRows(membership) : membership;
    if (personWorks.length > 0) {
        if (worksEditing) {
            const groupedWorks = personWorks.reduce((acc, w) => {
                const role = (w && w.role_type) || 'Linked';
                if (!acc[role]) acc[role] = [];
                acc[role].push(w);
                return acc;
            }, {});
            for (const [role, worksList] of Object.entries(groupedWorks)) {
                let cards = '';
                worksList.forEach((w) => {
                    const card =
                        typeof prksWorkCardHtml === 'function'
                            ? prksWorkCardHtml(w, offlineCached ? { suppressThumbnail: true } : {})
                            : '';
                    const oi =
                        w.order_index != null && w.order_index !== '' ? String(w.order_index) : '0';
                    const rt = escapeHtmlPerson(w.role_type || 'Linked');
                    const pid = escapeHtmlPerson(person.id);
                    const wid = escapeHtmlPerson(w.id);
                    cards += `<div class="person-profile__work-card-wrap">${card}<button type="button" class="person-profile__card-unlink" data-prks-role="${PERSON_MUTATION_ROLE}" aria-label="Remove link to this file" data-work-id="${wid}" data-person-id="${pid}" data-role-type="${rt}" data-order-index="${escapeHtmlPerson(oi)}" onclick="event.stopPropagation(); void prksRemoveWorkRoleLink(this);">×</button></div>`;
                });
                worksHtml += personRoleBlockHtml(role || 'Linked', worksList.length, cards);
            }
        } else {
            // Same overlay on the read-only path.
            const uniqueWorks = typeof prksEffectiveWorkSummaryRows === 'function'
                ? prksEffectiveWorkSummaryRows(typeof prksEffectivePersonWorks === 'function'
                    ? prksEffectivePersonWorks(person, prksUniquePersonWorks(person))
                    : prksUniquePersonWorks(person))
                : prksUniquePersonWorks(person);
            const groupedUnique = {};
            uniqueWorks.forEach((w) => {
                const workId = w && w.id != null ? String(w.id).trim() : '';
                const roles = rolesByWork.get(workId) || [];
                const primary = roles[0] || 'Linked';
                if (!groupedUnique[primary]) groupedUnique[primary] = [];
                groupedUnique[primary].push({ work: w, roles: roles });
            });
            for (const [role, rows] of Object.entries(groupedUnique)) {
                let cards = '';
                rows.forEach((row) => {
                    const w = row.work;
                    const workId = w && w.id != null ? String(w.id).trim() : '';
                    const roleList = row.roles || [];
                    const credit = (person.works || [])
                        .filter((x) => x && String(x.id) === workId)
                        .map((x) => (x.credit_name != null ? String(x.credit_name).trim() : ''))
                        .find(Boolean) || '';
                    const roleContext = roleList.join(' · ');
                    const subtitle = credit
                        ? `${roleContext}${roleContext ? ' · ' : ''}${credit}`
                        : roleContext;
                    const card =
                        typeof prksWorkCardHtml === 'function'
                            ? prksWorkCardHtml(
                                  w,
                                  Object.assign(
                                      {},
                                      subtitle ? { subtitle: subtitle } : {},
                                      offlineCached ? { suppressThumbnail: true } : {}
                                  )
                              )
                            : '';
                    cards += `<div class="person-profile__work-card-wrap">${card}</div>`;
                });
                worksHtml += personRoleBlockHtml(role || 'Linked', rows.length, cards);
            }
        }
    } else {
        worksHtml = '<p class="prks-inline-message">This person is not linked to any files.</p>';
    }

    const portraitApi = offlineCached ? null : personProfileImageSrc(person);
    const heroNoPhotoClass = portraitApi ? '' : ' person-profile__hero--no-photo';
    const portraitCol = portraitApi
        ? `<div class="person-profile__portrait"><div class="person-portrait-wrap"><img class="person-portrait" src="${escapeHtmlPerson(portraitApi)}" alt=""></div></div>`
        : '';

    const linksBlock = renderPersonExternalLinksList(person);
    const lifespan = personLifespanDisplay(person);
    const lifespanHtml = lifespan
        ? `<p class="person-profile__lifespan">${escapeHtmlPerson(lifespan)}</p>`
        : '';
    const aliasesRaw = (person.aliases || '').trim();
    const aliasesHtml = aliasesRaw
        ? `<div class="person-profile__aliases"><span class="person-card-label">Also known as</span><span class="person-profile__alias-list">${aliasesRaw.split(',').map((alias) => alias.trim()).filter(Boolean).map((alias) => `<span class="person-profile__alias-tag">${escapeHtmlPerson(alias)}</span>`).join('')}</span></div>`
        : '';
    let groupsHtml = '';
    if (Array.isArray(person.groups) && person.groups.length > 0) {
        const tags = person.groups
            .map(
                (g) =>
                    `<a class="tag" data-prks-role="${PERSON_GROUP_LINK_ROLE}" href="#/people/groups/${encodeURIComponent(String(g.id || ''))}">${escapeHtmlPerson(g.name)}</a>`
            )
            .join(' ');
        groupsHtml = `<p class="meta-row person-profile__groups">${tags}</p>`;
    }
    const aboutText = (person.about || '').trim();
    const aboutHtml = aboutText
        ? `<section class="person-profile__about" aria-labelledby="person-profile-about-heading"><h3 id="person-profile-about-heading">About</h3><p class="person-profile__about-text">${escapeHtmlPerson(aboutText)}</p></section>`
        : '';
    const nWorks = prksUniquePersonWorks(person).length;

    container.innerHTML = `
        <div class="prks-page-header page-header page-header--split">
            <h2 class="prks-page-title">${typeof prksPageHeaderIconHtml === 'function' ? prksPageHeaderIconHtml('user') : ''} ${escapeHtmlPerson(person.first_name || '')} ${escapeHtmlPerson(person.last_name)}</h2>
        </div>
        <div class="document-view document-view--person">
            <div class="doc-content person-profile">
                <div class="person-profile__hero${heroNoPhotoClass}">
                    ${portraitCol}
                    <div class="person-profile__info">
                        <div class="person-profile__summary">
                            ${lifespanHtml}
                            ${aliasesHtml}
                            ${groupsHtml}
                        </div>
                        ${aboutHtml}
                        ${linksBlock}
                    </div>
                </div>
                <section class="person-profile__works${worksEditing ? ' person-profile__works--editing' : ''}" aria-labelledby="person-profile-works-heading">
                    <div class="person-profile__works-head">
                    <h2 id="person-profile-works-heading" class="person-profile__works-title">Linked files</h2>
                    <span class="person-profile__works-count">${nWorks}</span>
                    ${nWorks > 0 || worksEditing ? `<button type="button" class="prks-btn prks-btn--secondary prks-btn--sm person-profile__works-action"${worksEditing ? '' : ` data-prks-role="${PERSON_MUTATION_ROLE}"`} onclick="prksTogglePersonWorksEdit()">${worksEditing ? 'Done' : 'Edit relationships'}</button>` : ''}
                    </div>
                    ${worksHtml}
                </section>
            </div>
        </div>
    `;
    if (typeof window.prksInitLazyWorkThumbs === 'function') {
        window.prksInitLazyWorkThumbs(container);
    }
    prksBindPersonOfflineState(ctx, container);
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(container);
}
