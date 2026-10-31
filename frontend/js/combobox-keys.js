/**
 * Shared keyboard model for PRKS comboboxes (New File, Work Tags, Folder Tags,
 * and the Processing card). ArrowUp/ArrowDown move the highlighted row, the
 * first ArrowDown lands on a real match rather than a create row, Enter picks
 * the highlighted row (or, for a typed query, the first real match, then a
 * create row when enterCreates is set), and Escape closes the list. Picking
 * dispatches the row's mousedown, so pointer and keyboard share one commit.
 * Typing marks the open rows stale until the list re-renders, so Enter cannot
 * pick a row from the previous query.
 *
 * An open New File list is still closed by the modal Escape stack, which runs
 * before this handler. Other pickers pass onEscape.
 */
(function (root) {
    'use strict';

    let comboboxSeq = 0;

    function prksComboboxPanelIsOpen(results) {
        if (!results || results.classList.contains('hidden')) return false;
        if (results.classList.contains('combobox-results--tag-panel')) {
            return results.classList.contains('is-open');
        }
        return true;
    }

    function ensureId(el) {
        if (el.id) return el.id;
        comboboxSeq += 1;
        el.id = 'prks-combobox-' + comboboxSeq;
        return el.id;
    }

    function prksBindComboboxKeys(input, results, options) {
        if (!input || !results || input.dataset.prksKeysBound === '1') return;
        input.dataset.prksKeysBound = '1';
        const enterCreates = !!(options && options.enterCreates);
        const onOpen = options && options.onOpen;
        const onEscape = options && options.onEscape;
        const resultsId = ensureId(results);
        input.setAttribute('role', 'combobox');
        input.setAttribute('aria-autocomplete', 'list');
        input.setAttribute('aria-controls', resultsId);
        input.setAttribute('aria-expanded', prksComboboxPanelIsOpen(results) ? 'true' : 'false');
        results.setAttribute('role', 'listbox');
        let active = -1;
        // Set when ArrowDown reopens a closed list: highlight once rows exist
        // (some lists render asynchronously), so one press still lands on a match.
        let highlightOnRender = false;
        // Typing invalidates the open rows until the list re-renders (the tag and
        // playlist lists render after a fetch): Enter must not pick a stale row.
        let stale = false;
        const optionRows = () => Array.from(results.querySelectorAll('.result-item:not(.no-results)'));
        const items = () => optionRows().filter((el) => el.getAttribute('aria-disabled') !== 'true');
        const firstRealIndex = (list) => {
            const i = list.findIndex((el) => !el.classList.contains('result-item--create'));
            return i >= 0 ? i : 0;
        };
        const paint = () => {
            const nav = items();
            optionRows().forEach((el, i) => {
                if (!el.id) el.id = resultsId + '-opt-' + i;
                el.setAttribute('role', 'option');
                const on = nav[active] === el;
                el.classList.toggle('result-item--active', on);
                el.setAttribute('aria-selected', on ? 'true' : 'false');
                if (on) {
                    input.setAttribute('aria-activedescendant', el.id);
                    if (typeof el.scrollIntoView === 'function') {
                        el.scrollIntoView({ block: 'nearest' });
                    }
                }
            });
            if (active < 0 || !nav[active]) input.removeAttribute('aria-activedescendant');
        };
        const hideList = () => {
            highlightOnRender = false;
            active = -1;
            if (typeof onEscape === 'function') onEscape();
            else if (typeof root.prksHideInlineComboboxResults === 'function') {
                root.prksHideInlineComboboxResults(results);
            } else {
                results.classList.remove('is-open');
                results.classList.add('hidden');
            }
            input.setAttribute('aria-expanded', prksComboboxPanelIsOpen(results) ? 'true' : 'false');
            paint();
        };
        // Child changes are delivered before class changes so a render that
        // both paints rows and opens the panel keeps the highlight the child
        // observer just applied. A class-only open (rows already painted)
        // still highlights below.
        new MutationObserver(() => {
            stale = false;
            const list = items();
            active = highlightOnRender && list.length ? firstRealIndex(list) : -1;
            if (list.length) highlightOnRender = false;
            paint();
        }).observe(results, { childList: true });
        new MutationObserver(() => {
            const open = prksComboboxPanelIsOpen(results);
            input.setAttribute('aria-expanded', open ? 'true' : 'false');
            if (!open) {
                if (active !== -1) {
                    active = -1;
                    paint();
                }
                return;
            }
            if (!highlightOnRender) return;
            const list = items();
            if (!list.length) return;
            active = firstRealIndex(list);
            highlightOnRender = false;
            stale = false;
            paint();
        }).observe(results, { attributes: true, attributeFilter: ['class'] });
        // Capture phase: runs before the list's own input handler, whose render
        // (synchronous or not) is what marks the rows fresh again.
        input.addEventListener(
            'input',
            () => {
                highlightOnRender = false;
                stale = true;
            },
            true
        );
        results.addEventListener('mouseover', (event) => {
            if (!prksComboboxPanelIsOpen(results)) return;
            const target = event.target;
            const el = target && target.closest ? target.closest('.result-item') : null;
            if (!el || !results.contains(el)) return;
            const list = items();
            const index = list.indexOf(el);
            if (index < 0 || index === active) return;
            active = index;
            paint();
        });
        input.addEventListener('keydown', (e) => {
            if (e.isComposing) return;
            if (e.key === 'Escape') {
                if (!prksComboboxPanelIsOpen(results)) return;
                e.preventDefault();
                e.stopPropagation();
                hideList();
                return;
            }
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                if (!prksComboboxPanelIsOpen(results)) {
                    highlightOnRender = e.key === 'ArrowDown';
                    if (typeof onOpen === 'function') onOpen();
                    else if (typeof input.onfocus === 'function') input.onfocus();
                    return;
                }
                const list = items();
                if (!list.length) return;
                if (e.key === 'ArrowDown' && active < 0) {
                    // A "Quick-create …" / "Create …" row is listed first; land on a real match.
                    active = firstRealIndex(list);
                } else if (e.key === 'ArrowDown') active = (active + 1) % list.length;
                else active = active <= 0 ? list.length - 1 : active - 1;
                paint();
                return;
            }
            if (e.key !== 'Enter' || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
            if (!prksComboboxPanelIsOpen(results)) return;
            if (stale) {
                e.preventDefault();
                e.stopPropagation();
                return;
            }
            const list = items();
            let pick = active >= 0 ? list[active] : null;
            if (!pick && String(input.value || '').trim()) {
                pick =
                    list.find((el) => !el.classList.contains('result-item--create')) ||
                    (enterCreates ? list.find((el) => el.classList.contains('result-item--create')) : null) ||
                    null;
            }
            if (!pick) return;
            e.preventDefault();
            e.stopPropagation();
            pick.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
        });
    }

    root.prksBindComboboxKeys = prksBindComboboxKeys;
    root.prksComboboxPanelIsOpen = prksComboboxPanelIsOpen;
})(typeof window !== 'undefined' ? window : globalThis);

if (typeof module === 'object' && module != null && module.exports) {
    module.exports = {
        prksBindComboboxKeys: globalThis.prksBindComboboxKeys,
        prksComboboxPanelIsOpen: globalThis.prksComboboxPanelIsOpen,
    };
}
