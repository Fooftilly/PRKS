// Run immediately to prevent theme flashing
(function() {
    const savedTheme = localStorage.getItem('prks-theme') || 'system';
    if (savedTheme !== 'system') {
        document.documentElement.setAttribute('data-theme', savedTheme);
    }
})();

(function prksEarlyForceMobileClass() {
    try {
        const raw = localStorage.getItem('prks.ui.forceMobile');
        if (raw === '1' || raw === 'true') {
            document.documentElement.classList.add('prks-force-mobile');
        }
    } catch (_e) {}
})();

// Match hint toggle before first paint (ui.js defines prksApplyHintsPreferenceToDocument)
(function prksEarlyHintsDataset() {
    if (typeof window.prksApplyHintsPreferenceToDocument === 'function') {
        window.prksApplyHintsPreferenceToDocument();
    }
})();

(function prksEarlyViewportHeightVar() {
    const sync = () => {
        try {
            document.documentElement.style.setProperty('--prks-vh', `${window.innerHeight}px`);
            if (document.body) {
                document.body.style.height = `${window.innerHeight}px`;
            }
        } catch (_e) {}
    };
    sync();
    window.prksSyncViewportHeightVar = sync;
    if (!window.__prksViewportHeightVarBound) {
        window.__prksViewportHeightVarBound = true;
        window.addEventListener('resize', sync);
        window.addEventListener('orientationchange', sync);
        if (window.visualViewport && typeof window.visualViewport.addEventListener === 'function') {
            window.visualViewport.addEventListener('resize', sync);
        }
    }
})();

if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js').catch((err) => {
            console.warn('PRKS service worker registration failed:', err);
        });
    });
}

function initPrksGlobalErrorReporting() {
    if (window.__prksGlobalErrorReportingBound) return;
    window.__prksGlobalErrorReportingBound = true;
    window.addEventListener('error', (event) => {
        const err = event && event.error;
        const message = err && err.message ? String(err.message) : String(event && event.message ? event.message : 'Unhandled error');
        if (typeof window.prksReportClientError === 'function') {
            window.prksReportClientError({
                kind: 'window_error',
                message,
                stack: err && err.stack ? String(err.stack) : '',
                source: event && event.filename ? String(event.filename) : 'window',
            });
        }
    });
    window.addEventListener('unhandledrejection', (event) => {
        const reason = event ? event.reason : null;
        let message = 'Unhandled promise rejection';
        let stack = '';
        if (reason instanceof Error) {
            message = reason.message || message;
            stack = reason.stack || '';
        } else if (typeof reason === 'string') {
            message = reason;
        } else if (reason != null) {
            try {
                message = JSON.stringify(reason);
            } catch (_e) {
                message = String(reason);
            }
        }
        if (typeof window.prksReportClientError === 'function') {
            window.prksReportClientError({
                kind: 'unhandled_rejection',
                message,
                stack,
                source: 'promise',
            });
        }
    });
}

const PRKS_BIBTEX_EXPORT_FIELD_DEFS = [
    ['author', 'Author'],
    ['editor', 'Editor'],
    ['translator', 'Translator'],
    ['introduction', 'Introduction'],
    ['foreword', 'Foreword'],
    ['afterword', 'Afterword'],
    ['year', 'Year'],
    ['publisher', 'Publisher'],
    ['location', 'Location'],
    ['edition', 'Edition'],
    ['journal', 'Journal'],
    ['volume', 'Volume'],
    ['number', 'Issue (number)'],
    ['pages', 'Pages'],
    ['isbn', 'ISBN'],
    ['doi', 'DOI'],
    ['url', 'URL and access date'],
    ['abstract', 'Abstract'],
];

document.addEventListener('DOMContentLoaded', () => {
    initPrksGlobalErrorReporting();
    initTheme();
    void initAnnotationAuthorSetting();
    void initBibtexExportFieldsSetting();
    initPrksPdfTextReindexAction();
    initPrksExistingPdfLinearizeAction();
    initPrksPdfRememberPageSetting();
    initPrksPdfLastPageVisibilityFlush();
    initPrksHintsSetting();
    initForceMobileSetting();
    initMobileWorkNotesRightSetting();
    if (typeof initPrksHintUi === 'function') initPrksHintUi();
    initPrksMiddleClickNavigation();
    if (typeof initModalCloseUi === 'function') initModalCloseUi();
    if (typeof initMobileShell === 'function') initMobileShell();
    initRouter();
    initTabs();
    initForms();
    initSearch();
    initUploadDragAndDrop();
});

function prksAbsoluteUrlForHash(hash) {
    const h = String(hash || '');
    const url = new URL(window.location.href);
    url.hash = h.startsWith('#') ? h : '#' + h;
    return url.toString();
}

function prksOpenHashInNewTab(hash) {
    const abs = prksAbsoluteUrlForHash(hash);
    window.open(abs, '_blank', 'noopener');
}

/**
 * For inline handlers: open in new tab when middle-click (button 1).
 * Returns true when it handled the event.
 */
function prksMaybeOpenHashInNewTab(ev, hash) {
    const e = ev || window.event;
    if (!e) return false;
    if (e.button !== 1) return false;
    try {
        e.preventDefault();
        e.stopPropagation();
    } catch (_err) {}
    prksOpenHashInNewTab(hash);
    return true;
}

function prksSyncSwitchUi(btn, on) {
    btn.setAttribute('aria-checked', on ? 'true' : 'false');
    btn.classList.toggle('prks-toggle--on', on);
}

function prksBindSettingSwitch(buttonId, read, write, { onAfter } = {}) {
    const btn = document.getElementById(buttonId);
    if (!btn || btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';
    prksSyncSwitchUi(btn, !!read());
    const apply = (on) => {
        write(!!on);
        prksSyncSwitchUi(btn, !!on);
        if (typeof onAfter === 'function') onAfter(!!on);
    };
    btn.addEventListener('click', () => {
        apply(!read());
    });
    btn.addEventListener('keydown', (e) => {
        if (e.key === ' ' || e.key === 'Enter') {
            e.preventDefault();
            apply(!read());
        }
    });
}

const PRKS_LS_PDF_REMEMBER_LAST_PAGE = 'prks.pdf.rememberLastPage';

function prksGetPdfRememberLastPageEnabled() {
    try {
        const raw = localStorage.getItem(PRKS_LS_PDF_REMEMBER_LAST_PAGE);
        if (raw == null) return true;
        if (raw === '1' || raw === 'true') return true;
        if (raw === '0' || raw === 'false') return false;
        return true;
    } catch (_e) {
        return true;
    }
}

function prksSetPdfRememberLastPageEnabled(enabled) {
    try {
        localStorage.setItem(PRKS_LS_PDF_REMEMBER_LAST_PAGE, enabled ? '1' : '0');
    } catch (_e) {}
}

window.prksGetPdfRememberLastPageEnabled = prksGetPdfRememberLastPageEnabled;

function prksPdfLastPageStorageKey(workId) {
    return 'prks.pdf.lastPage.' + workId;
}

function prksExtractWorkIdFromHash(h) {
    if (!h || typeof h !== 'string' || !h.startsWith('#/works/')) return null;
    const parts = h.split('/');
    if (parts.length < 3) return null;
    try {
        return decodeURIComponent(parts[2]);
    } catch (_e) {
        return parts[2];
    }
}

function prksFlushPdfLastPageToStorage(workId) {
    if (!workId || !prksGetPdfRememberLastPageEnabled()) return;
    const sess = window.__prksPdfPageSession;
    if (!sess || sess.workId !== workId) return;
    const p = sess.pageNumber;
    const n = sess.totalPages;
    if (!Number.isFinite(p) || p < 1) return;
    try {
        const payload = JSON.stringify({
            p: Math.floor(p),
            n: Number.isFinite(n) ? Math.floor(n) : undefined,
        });
        localStorage.setItem(prksPdfLastPageStorageKey(workId), payload);
    } catch (_e) {}
    if (typeof window.__prksPdfLastPageDebounceClear === 'function') {
        window.__prksPdfLastPageDebounceClear(workId);
    }
}

function prksMaybeFlushPdfLastPageOnRouteChange(prevHash, newHash) {
    const prevWid = prksExtractWorkIdFromHash(prevHash);
    const newWid = prksExtractWorkIdFromHash(newHash);
    if (prevWid && prevWid !== newWid) {
        prksFlushPdfLastPageToStorage(prevWid);
    }
}

function initPrksPdfRememberPageSetting() {
    prksBindSettingSwitch(
        'prks-setting-pdf-remember-page',
        prksGetPdfRememberLastPageEnabled,
        prksSetPdfRememberLastPageEnabled
    );
}

function initPrksPdfLastPageVisibilityFlush() {
    if (window.__prksPdfVisibilityFlushBound) return;
    window.__prksPdfVisibilityFlushBound = true;
    const flush = () => {
        const sess = window.__prksPdfPageSession;
        if (sess && sess.workId) {
            prksFlushPdfLastPageToStorage(sess.workId);
        }
    };
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'hidden') return;
        flush();
    });
    window.addEventListener('pagehide', flush);
}

function initPrksHintsSetting() {
    prksBindSettingSwitch(
        'prks-setting-ui-hints',
        () =>
            typeof window.prksGetHintsEnabled === 'function'
                ? window.prksGetHintsEnabled()
                : true,
        (on) => {
            if (typeof window.prksSetHintsEnabled === 'function') {
                window.prksSetHintsEnabled(on);
            }
        },
        {
            onAfter() {
                if (typeof window.prksApplyHintsPreferenceToDocument === 'function') {
                    window.prksApplyHintsPreferenceToDocument();
                }
                if (typeof window.prksCloseHintPopover === 'function') {
                    window.prksCloseHintPopover();
                }
            },
        }
    );
}

const PRKS_LS_FORCE_MOBILE = 'prks.ui.forceMobile';

function prksGetForceMobileEnabled() {
    try {
        const raw = localStorage.getItem(PRKS_LS_FORCE_MOBILE);
        if (raw == null) return false;
        if (raw === '1' || raw === 'true') return true;
        if (raw === '0' || raw === 'false') return false;
        return false;
    } catch (_e) {
        return false;
    }
}

function prksSetForceMobileEnabled(enabled) {
    try {
        localStorage.setItem(PRKS_LS_FORCE_MOBILE, enabled ? '1' : '0');
    } catch (_e) {}
}

function initForceMobileSetting() {
    prksBindSettingSwitch(
        'prks-setting-force-mobile',
        prksGetForceMobileEnabled,
        prksSetForceMobileEnabled,
        {
            onAfter(on) {
                document.documentElement.classList.toggle('prks-force-mobile', on);
                if (typeof prksSyncMobileToggleButtons === 'function') {
                    prksSyncMobileToggleButtons();
                }
                if (
                    !on &&
                    window.matchMedia &&
                    !window.matchMedia('(max-width: 900px)').matches &&
                    typeof prksCloseOverlays === 'function'
                ) {
                    prksCloseOverlays();
                }
                if (typeof prksSyncWorkNotesMobileSideClass === 'function') {
                    prksSyncWorkNotesMobileSideClass();
                }
            },
        }
    );
}

const PRKS_LS_MOBILE_WORK_NOTES_RIGHT = 'prks.ui.mobileWorkNotesRight';

function prksGetMobileWorkNotesRightEnabled() {
    try {
        const raw = localStorage.getItem(PRKS_LS_MOBILE_WORK_NOTES_RIGHT);
        if (raw == null) return false;
        if (raw === '1' || raw === 'true') return true;
        if (raw === '0' || raw === 'false') return false;
        return false;
    } catch (_e) {
        return false;
    }
}

function prksSetMobileWorkNotesRightEnabled(enabled) {
    try {
        localStorage.setItem(PRKS_LS_MOBILE_WORK_NOTES_RIGHT, enabled ? '1' : '0');
    } catch (_e) {}
}

function prksSyncWorkNotesMobileSideClass() {
    if (typeof window.prksSyncViewportHeightVar === 'function') {
        window.prksSyncViewportHeightVar();
    }
    const mobileWorkNotesRightEnabled = prksGetMobileWorkNotesRightEnabled();
    const isSmall =
        typeof prksIsSmallScreen === 'function' &&
        prksIsSmallScreen();
    const want = mobileWorkNotesRightEnabled && isSmall;
    document.documentElement.classList.toggle('prks-work-notes-mobile-side', want);
    if (typeof window.prksReapplyWorkNotesSplitLayout === 'function') {
        window.prksReapplyWorkNotesSplitLayout();
    }
    const splitHandle = document.querySelector('.document-view--work .work-split-handle');
    if (splitHandle) {
        splitHandle.setAttribute('aria-orientation', want ? 'vertical' : 'horizontal');
    }
    if (typeof window.__prksWorkNotesCollapseSyncUi === 'function') {
        window.__prksWorkNotesCollapseSyncUi();
    }
}

window.prksSyncWorkNotesMobileSideClass = prksSyncWorkNotesMobileSideClass;

function initMobileWorkNotesRightSetting() {
    prksBindSettingSwitch(
        'prks-setting-mobile-work-notes-right',
        prksGetMobileWorkNotesRightEnabled,
        prksSetMobileWorkNotesRightEnabled,
        {
            onAfter() {
                prksSyncWorkNotesMobileSideClass();
            },
        }
    );
    if (!window.__prksWorkNotesMobileSideResizeBound) {
        window.__prksWorkNotesMobileSideResizeBound = true;
        window.addEventListener('resize', () => {
            prksSyncWorkNotesMobileSideClass();
        });
    }
    prksSyncWorkNotesMobileSideClass();
}

/**
 * Prevent default browser auto-scroll on middle-click for navigable cards.
 * (Still allows middle-click to open via prksMaybeOpenHashInNewTab.)
 */
function initPrksMiddleClickNavigation() {
    if (window.__prksMiddleClickNavBound) return;
    window.__prksMiddleClickNavBound = true;
    document.addEventListener(
        'mousedown',
        (e) => {
            const t = e.target && e.target.closest ? e.target.closest('[data-prks-middleclick-nav]') : null;
            if (!t) return;
            if (e.button === 1) {
                e.preventDefault();
            }
        },
        true
    );
}

function initTheme() {
    const picker = document.querySelector('.prks-theme-picker');
    if (!picker || picker.dataset.bound === '1') return;
    picker.dataset.bound = '1';
    const savedTheme = localStorage.getItem('prks-theme') || 'system';
    const buttons = picker.querySelectorAll('.prks-theme-option[data-prks-theme]');

    function syncThemePickerUi(theme) {
        buttons.forEach((btn) => {
            const on = btn.getAttribute('data-prks-theme') === theme;
            btn.classList.toggle('prks-theme-option--active', on);
            btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
    }

    syncThemePickerUi(savedTheme);

    buttons.forEach((btn) => {
        btn.addEventListener('click', () => {
            const newTheme = btn.getAttribute('data-prks-theme') || 'system';
            if (newTheme === (localStorage.getItem('prks-theme') || 'system')) return;
            localStorage.setItem('prks-theme', newTheme);
            applyTheme(newTheme);
            window.location.reload();
        });
    });
}

async function initAnnotationAuthorSetting() {
    const input = document.getElementById('annotation-author-input');
    if (!input || input.dataset.bound === '1') return;
    input.dataset.bound = '1';
    try {
        if (typeof prksLoadAppSettings === 'function') {
            await prksLoadAppSettings();
        }
    } catch (_e) {
        /* ignore */
    }
    input.value =
        (typeof window.__prksAnnotationAuthor === 'string' ? window.__prksAnnotationAuthor : '') || '';

    let debounceTimer = null;
    const persist = () => {
        const v = input.value.trim();
        if (typeof prksSetAnnotationAuthorCache === 'function') {
            prksSetAnnotationAuthorCache(v);
        }
        window.clearTimeout(debounceTimer);
        debounceTimer = window.setTimeout(async () => {
            try {
                if (typeof prksPatchAppSettings === 'function') {
                    await prksPatchAppSettings({ annotation_author: v });
                }
            } catch (e) {
                console.warn(e);
            }
        }, 450);
    };
    input.addEventListener('change', persist);
    input.addEventListener('input', persist);
}

async function initBibtexExportFieldsSetting() {
    const host = document.getElementById('prks-bibtex-export-fields');
    if (!host || host.dataset.bound === '1') return;
    host.dataset.bound = '1';

    host.innerHTML = '';
    for (const [id, label] of PRKS_BIBTEX_EXPORT_FIELD_DEFS) {
        const row = document.createElement('div');
        row.className = 'prks-setting-row';
        const text = document.createElement('div');
        text.className = 'prks-setting-row__text';
        const lbl = document.createElement('span');
        lbl.className = 'prks-setting-row__label';
        lbl.id = 'prks-lbl-bibtex-export-' + id;
        lbl.textContent = label;
        text.appendChild(lbl);
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'prks-toggle';
        btn.setAttribute('role', 'switch');
        btn.dataset.bibtexField = id;
        btn.setAttribute('aria-labelledby', lbl.id);
        btn.setAttribute('aria-checked', 'true');
        const thumb = document.createElement('span');
        thumb.className = 'prks-toggle__thumb';
        thumb.setAttribute('aria-hidden', 'true');
        btn.appendChild(thumb);
        row.appendChild(text);
        row.appendChild(btn);
        host.appendChild(row);
    }

    const syncTogglesFromMap = (m) => {
        const map = m && typeof m === 'object' ? m : {};
        host.querySelectorAll('button[data-bibtex-field].prks-toggle').forEach((btn) => {
            const fid = btn.dataset.bibtexField;
            prksSyncSwitchUi(btn, map[fid] !== false);
        });
    };

    syncTogglesFromMap({});

    try {
        if (typeof prksLoadAppSettings === 'function') {
            await prksLoadAppSettings();
        }
    } catch (_e) {
        /* ignore */
    }
    syncTogglesFromMap(
        typeof window.__prksBibtexExportFields === 'object' && window.__prksBibtexExportFields !== null
            ? window.__prksBibtexExportFields
            : {},
    );

    let debounceTimer = null;
    const persist = () => {
        const next = {};
        for (const [id] of PRKS_BIBTEX_EXPORT_FIELD_DEFS) {
            const btn = host.querySelector(`button[data-bibtex-field="${id}"]`);
            next[id] = btn && btn.getAttribute('aria-checked') === 'true';
        }
        if (typeof prksSetBibtexExportFieldsCache === 'function') {
            prksSetBibtexExportFieldsCache(next);
        }
        window.clearTimeout(debounceTimer);
        debounceTimer = window.setTimeout(async () => {
            try {
                if (typeof prksPatchAppSettings === 'function') {
                    await prksPatchAppSettings({ bibtex_export_fields: next });
                }
            } catch (e) {
                console.warn(e);
            }
        }, 450);
    };

    host.querySelectorAll('button[data-bibtex-field].prks-toggle').forEach((btn) => {
        const flip = () => {
            const on = btn.getAttribute('aria-checked') !== 'true';
            prksSyncSwitchUi(btn, on);
            persist();
        };
        btn.addEventListener('click', flip);
        btn.addEventListener('keydown', (e) => {
            if (e.key === ' ' || e.key === 'Enter') {
                e.preventDefault();
                flip();
            }
        });
    });

    const resetBtn = document.getElementById('prks-bibtex-export-reset');
    if (resetBtn && resetBtn.dataset.bound !== '1') {
        resetBtn.dataset.bound = '1';
        resetBtn.addEventListener('click', async () => {
            const allTrue = Object.fromEntries(PRKS_BIBTEX_EXPORT_FIELD_DEFS.map(([id]) => [id, true]));
            if (typeof prksSetBibtexExportFieldsCache === 'function') {
                prksSetBibtexExportFieldsCache(allTrue);
            }
            syncTogglesFromMap(allTrue);
            try {
                if (typeof prksPatchAppSettings === 'function') {
                    await prksPatchAppSettings({ bibtex_export_fields: allTrue });
                }
            } catch (e) {
                console.warn(e);
            }
        });
    }
}

function initPrksPdfTextReindexAction() {
    const btn = document.getElementById('prks-reindex-pdf-text-btn');
    const statusEl = document.getElementById('prks-reindex-pdf-text-status');
    if (!btn || btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', async () => {
        btn.disabled = true;
        const oldText = btn.textContent;
        btn.textContent = 'Re-indexing…';
        if (statusEl) statusEl.textContent = '';
        try {
            if (typeof prksReindexPdfText !== 'function') {
                throw new Error('PDF re-index API unavailable.');
            }
            const out = await prksReindexPdfText();
            if (statusEl) {
                const processed = Number(out.processed || 0);
                const indexed = Number(out.indexed || 0);
                const failed = Number(out.failed || 0);
                statusEl.textContent = `Done. Processed ${processed}, indexed ${indexed}, failed ${failed}.`;
            }
        } catch (e) {
            if (statusEl) statusEl.textContent = (e && e.message) || 'Could not re-index PDF text.';
        } finally {
            btn.disabled = false;
            btn.textContent = oldText;
        }
    });
}

function initPrksExistingPdfLinearizeAction() {
    const btn = document.getElementById('prks-linearize-existing-pdfs-btn');
    const statusEl = document.getElementById('prks-linearize-existing-pdfs-status');
    if (!btn || btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', async () => {
        btn.disabled = true;
        const oldText = btn.textContent;
        btn.textContent = 'Linearizing…';
        if (statusEl) statusEl.textContent = '';
        try {
            if (typeof prksLinearizeExistingPdfs !== 'function') {
                throw new Error('PDF linearization API unavailable.');
            }
            const out = await prksLinearizeExistingPdfs(true);
            if (statusEl) {
                const processed = Number(out.processed || 0);
                const changed = Number(out.changed || 0);
                const already = Number(out.already_linearized || 0);
                const skipped = Number(out.skipped || 0);
                const failed = Number(out.failed || 0);
                statusEl.textContent = `Done. Processed ${processed}, linearized ${changed}, already linearized ${already}, skipped ${skipped}, failed ${failed}.`;
            }
        } catch (e) {
            if (statusEl) statusEl.textContent = (e && e.message) || 'Could not linearize existing PDFs.';
        } finally {
            btn.disabled = false;
            btn.textContent = oldText;
        }
    });
}

function applyTheme(theme) {
    if (theme === 'system') {
        document.documentElement.removeAttribute('data-theme');
    } else {
        document.documentElement.setAttribute('data-theme', theme);
    }
}
const PRKS_HOME_HASH = '#/folders';

function initSidebarBrandHome() {
    const brand = document.querySelector('.sidebar-brand');
    if (!brand || brand.dataset.prksHomeBound === '1') return;
    brand.dataset.prksHomeBound = '1';
    brand.addEventListener('click', (e) => {
        const hash = window.location.hash || PRKS_HOME_HASH;
        if (hash !== PRKS_HOME_HASH) return;
        e.preventDefault();
        try {
            sessionStorage.removeItem('prks-folder-library-filter');
        } catch (_err) {
            /* ignore */
        }
        const st = window.__prksFolderDashboardState;
        if (st) st.filterQuery = '';
        handleRoute();
    });
}

function initRouter() {
    initSidebarBrandHome();
    window.addEventListener('hashchange', handleRoute);
    handleRoute();
}

function prksRouteTitleFromHash(hash) {
    if (hash.startsWith('#/works/')) return 'File';
    if (hash.startsWith('#/folders/')) return 'Folder';
    if (hash.startsWith('#/people/groups/')) return 'Group';
    if (hash.startsWith('#/people/')) return 'Person';
    if (hash.startsWith('#/playlists/')) return 'Playlist';
    if (hash.startsWith('#/search')) return 'Search';
    if (hash.startsWith('#/progress')) return 'Progress';
    if (hash === '#/processing-files') return 'Files for Processing';
    if (hash.startsWith('#/types/')) return 'File Type';
    if (hash === '#/folders') return 'Folders';
    if (hash === '#/playlists') return 'Playlists';
    if (hash === '#/people') return 'People';
    if (hash === '#/people/groups') return 'People Groups';
    if (hash === '#/recent') return 'Recent';
    if (hash === '#/tags') return 'Tags';
    if (hash === '#/publishers') return 'Publishers';
    if (hash === '#/types') return 'File Types';
    return 'Loading';
}

function prksRenderRouteLoading(contentDiv, hash) {
    if (!contentDiv) return;
    const title = prksRouteTitleFromHash(hash);
    contentDiv.setAttribute('aria-busy', 'true');
    contentDiv.innerHTML = `
        <div class="page-header"><h2>${title}</h2></div>
        <div class="prks-route-loading" role="status" aria-live="polite">
            <p class="meta-row">Loading view...</p>
            <div class="prks-route-loading__bar"></div>
        </div>
    `;
}

function prksPlayPageEnterAnimation(contentDiv) {
    if (!contentDiv) return;
    if (
        typeof window.matchMedia === 'function' &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches
    ) {
        return;
    }
    if (contentDiv.querySelector('.prks-route-loading')) return;
    contentDiv.classList.remove('prks-page-enter');
    void contentDiv.offsetWidth;
    contentDiv.classList.add('prks-page-enter');
    const onEnd = (e) => {
        if (e.target !== contentDiv) return;
        contentDiv.classList.remove('prks-page-enter');
        contentDiv.removeEventListener('animationend', onEnd);
    };
    contentDiv.addEventListener('animationend', onEnd);
}

async function handleRoute() {
    if (typeof prksCloseOverlays === 'function') prksCloseOverlays();
    if (window.__prksRouteRevertDueToPendingSync === true) {
        window.__prksRouteRevertDueToPendingSync = false;
        return;
    }
    const prevResolvedHash = window.__prksLastResolvedHash || '';
    let hash = window.location.hash || '#/folders';
    const leavingWorkPage = !!prevResolvedHash && prevResolvedHash.startsWith('#/works/') && hash !== prevResolvedHash;
    if (
        leavingWorkPage &&
        typeof window.prksHasPendingWorkAnnotationSync === 'function' &&
        window.prksHasPendingWorkAnnotationSync()
    ) {
        const ok = window.confirm(
            'PDF annotation sync still running. Leave page before all changes save to server?'
        );
        if (!ok) {
            window.__prksRouteRevertDueToPendingSync = true;
            window.location.hash = prevResolvedHash;
            return;
        }
    }
    prksMaybeFlushPdfLastPageOnRouteChange(prevResolvedHash, hash);
    if (hash === '#/graph') {
        window.location.hash = '#/folders';
        return;
    }
    syncProgressSidebarActive(null);
    const contentDiv = document.getElementById('page-content');
    if (!contentDiv) return;
    window.__prksRouteGen = (window.__prksRouteGen || 0) + 1;
    const routeGen = window.__prksRouteGen;
    if (window.annotationSyncInterval) {
        clearInterval(window.annotationSyncInterval);
        window.annotationSyncInterval = null;
    }
    if (window.saveNotesTimeout) {
        clearTimeout(window.saveNotesTimeout);
        window.saveNotesTimeout = null;
    }
    if (typeof window.prksDestroyWorkNotesEditor === 'function') {
        window.prksDestroyWorkNotesEditor();
    }
    // Deep reset work context
    window.currentWork = null;
    window.currentFolder = null;
    window.currentPerson = null;
    window.currentPersonGroup = null;
    window.currentPlaylist = null;
    window.__prksPersonDetailEditing = false;
    window.__prksPersonWorksEditing = false;
    window.__prksPersonGroupDetailEditing = false;
    window.__prksRouteSidebar = {};

    document.querySelectorAll('.nav-link').forEach((l) => {
        l.classList.remove('active');
        l.removeAttribute('aria-current');
    });
    const link = Array.from(document.querySelectorAll('.nav-link')).find((l) => l.getAttribute('href') === hash);
    if (link) {
        link.classList.add('active');
        link.setAttribute('aria-current', 'page');
    } else if (hash.startsWith('#/people/') && !hash.startsWith('#/people/role/') && !hash.startsWith('#/people/groups')) {
        const peopleLink = document.querySelector('.nav-link[href="#/people"]');
        if (peopleLink) {
            peopleLink.classList.add('active');
            peopleLink.setAttribute('aria-current', 'page');
        }
    }

    prksRenderRouteLoading(contentDiv, hash);

    if (hash === '#/folders') {
        const folders = await fetchFolders();
        if (routeGen !== window.__prksRouteGen) return;
        window.__prksRouteSidebar = { folderCount: folders.length };
        renderDashboard(folders, contentDiv);
    } else if (hash === '#/playlists') {
        if (typeof fetchPlaylists === 'function' && typeof renderPlaylistsIndex === 'function') {
            const pls = await fetchPlaylists();
            if (routeGen !== window.__prksRouteGen) return;
            renderPlaylistsIndex(pls, contentDiv);
        } else {
            contentDiv.innerHTML = '<div class="page-header"><h2>Playlists</h2></div><p class="meta-row">Playlist UI unavailable.</p>';
        }
    } else if (hash.startsWith('#/playlists/')) {
        const plId = hash.split('/')[2];
        if (typeof fetchPlaylistDetails === 'function' && typeof renderPlaylistDetail === 'function') {
            const pl = await fetchPlaylistDetails(plId);
            if (routeGen !== window.__prksRouteGen) return;
            window.currentPlaylist = pl;
            window.__prksPlaylistDetailEditing = false;
            window.__prksPlaylistRename = {};
            window.__prksRouteSidebar = pl
                ? { playlistTitle: pl.title || 'Playlist', itemCount: Array.isArray(pl.items) ? pl.items.length : 0 }
                : { playlistTitle: 'Playlist', itemCount: 0 };
            renderPlaylistDetail(pl, contentDiv);
        } else {
            contentDiv.innerHTML = '<div class="page-header"><h2>Playlists</h2></div><p class="meta-row">Playlist UI unavailable.</p>';
        }
    } else if (hash.startsWith('#/folders/')) {
        const f_id = hash.split('/')[2];
        const folder = await fetchFolderDetails(f_id);
        if (routeGen !== window.__prksRouteGen) return;
        renderFolderDetails(folder, contentDiv);
    } else if (hash === '#/people') {
        const persons = await fetchPersons();
        if (routeGen !== window.__prksRouteGen) return;
        renderPeopleList(persons, contentDiv);
    } else if (hash.startsWith('#/people/role/')) {
        const roleSlug = hash.slice('#/people/role/'.length);
        const allowed = ['Author', 'Editor', 'Reviewer', 'Translator', 'Introduction', 'Foreword', 'Afterword'];
        const roleFilter = allowed.includes(roleSlug) ? roleSlug : null;
        window.__prksRouteSidebar = { role: roleFilter || decodeURIComponent(roleSlug.replace(/\+/g, ' ')) || 'Unknown role' };
        const persons = await fetchPersons();
        if (routeGen !== window.__prksRouteGen) return;
        if (roleFilter) {
            renderPeopleList(persons, contentDiv, { roleFilter });
        } else {
            contentDiv.innerHTML =
                '<div class="page-header"><h2>People</h2></div><p class="prks-inline-message">Unknown role filter.</p>';
        }
    } else if (hash === '#/people/groups' || hash.startsWith('#/people/groups/')) {
        const groupsLink = document.querySelector('.nav-link[href="#/people/groups"]');
        if (groupsLink) {
            groupsLink.classList.add('active');
            groupsLink.setAttribute('aria-current', 'page');
        }
        if (hash === '#/people/groups') {
            const groups = await fetchPersonGroups();
            if (routeGen !== window.__prksRouteGen) return;
            window.__prksRouteSidebar = { groupCount: Array.isArray(groups) ? groups.length : 0 };
            renderPersonGroupsPage(groups, contentDiv);
        } else {
            // '#/people/groups/PG-…'.split('/') → ['#','people','groups',id]; filter(Boolean) keeps '#', so parts[2] was wrongly 'groups'
            const g_id = hash.split('/')[3];
            const group = g_id ? await fetchPersonGroupDetails(g_id) : null;
            if (routeGen !== window.__prksRouteGen) return;
            if (!group) {
                contentDiv.innerHTML =
                    '<div class="page-header"><h2>Group not found</h2></div><p class="meta-row"><a href="#/people/groups" class="route-sidebar__link">Back to groups</a></p>';
            } else {
                window.currentPersonGroup = group;
                window.__prksRouteSidebar = {
                    groupName: group.name,
                    memberCount: Array.isArray(group.members) ? group.members.length : 0,
                    subgroupCount: Array.isArray(group.children) ? group.children.length : 0
                };
                renderPersonGroupDetail(group, contentDiv);
            }
        }
    } else if (hash === '#/recent') {
        const works = await fetchRecent();
        if (routeGen !== window.__prksRouteGen) return;
        window.__prksRouteSidebar = { workCount: works.length };
        renderRecent(works, contentDiv);
    } else if (hash.startsWith('#/progress')) {
        const status = parseProgressStatusFromHash(hash);
        if (!status) {
            window.location.hash = '#/progress?status=' + encodeURIComponent(PRKS_PROGRESS_STATUSES[0]);
            return;
        }
        const works = await fetchWorks();
        if (routeGen !== window.__prksRouteGen) return;
        window.__prksRouteSidebar = { status };
        renderProgressByStatus(works, status, contentDiv);
        syncProgressSidebarActive(status);
    } else if (hash === '#/processing-files') {
        if (typeof prksRenderProcessingFilesPageWithFetch === 'function') {
            await prksRenderProcessingFilesPageWithFetch(contentDiv, { rescan: true });
            if (routeGen !== window.__prksRouteGen) return;
        } else {
            const rows = await fetchProcessingFiles({ rescan: true });
            if (routeGen !== window.__prksRouteGen) return;
            window.__prksRouteSidebar = { pendingCount: Array.isArray(rows) ? rows.length : 0 };
            if (typeof renderProcessingFilesPage === 'function') {
                renderProcessingFilesPage(rows, contentDiv);
            } else {
                contentDiv.innerHTML =
                    '<div class="page-header"><h2>Files for Processing</h2></div><p class="meta-row">Processing inbox UI unavailable.</p>';
            }
        }
    } else if (hash.startsWith('#/search')) {
        const urlParams = new URLSearchParams(hash.split('?')[1]);
        const query = urlParams.get('q') || '';
        const tag = urlParams.get('tag') || '';
        const author = urlParams.get('author') || '';
        const publisher = urlParams.get('publisher') || '';
        const any = urlParams.get('any') || '';
        const results = await fetchSearch(query, tag, { author, publisher, any });
        if (routeGen !== window.__prksRouteGen) return;
        window.__prksRouteSidebar = {
            query,
            tag,
            author,
            publisher,
            resultCount: Array.isArray(results) ? results.length : 0
        };
        renderSearch(results, query, contentDiv, { tag, author, publisher, any });
    } else if (hash === '#/tags') {
        if (typeof renderTagsPage === 'function') {
            await renderTagsPage(contentDiv);
            if (routeGen !== window.__prksRouteGen) return;
        }
    } else if (hash === '#/publishers') {
        if (typeof renderPublishersPage === 'function') {
            await renderPublishersPage(contentDiv);
            if (routeGen !== window.__prksRouteGen) return;
        }
    } else if (hash === '#/types') {
        const works = await fetchWorks();
        if (routeGen !== window.__prksRouteGen) return;
        renderTypesIndex(works, contentDiv);
    } else if (hash.startsWith('#/types/')) {
        const docType = decodeURIComponent(hash.slice('#/types/'.length));
        const works = await fetchWorks();
        if (routeGen !== window.__prksRouteGen) return;
        renderWorksByDocType(works, docType, contentDiv);
    } else if (hash.startsWith('#/works/')) {
        const w_id = hash.split('/')[2];
        const work = await fetchWorkDetails(w_id);
        if (routeGen !== window.__prksRouteGen) return;
        await renderWorkDetails(work, contentDiv, routeGen);
    } else if (hash.startsWith('#/people/')) {
        const p_id = hash.split('/')[2];
        const person = await fetchPersonDetails(p_id);
        if (routeGen !== window.__prksRouteGen) return;
        window.__prksRouteSidebar = person
            ? {
                  personDisplayName:
                      typeof personDisplayName === 'function' ? personDisplayName(person) || 'Person' : 'Person',
                  linkedWorks:
                      typeof prksUniquePersonWorks === 'function'
                          ? prksUniquePersonWorks(person).length
                          : person.works
                            ? person.works.length
                            : 0
              }
            : { personDisplayName: 'Person not found', linkedWorks: 0 };
        renderPersonDetails(person, contentDiv);
    } else {
        window.currentWork = null;
        updatePanelContent('details');
        contentDiv.innerHTML =
            '<div class="page-header"><h2>Section In Development</h2></div><p class="prks-dev-path-msg prks-inline-message"></p>';
        const devPathEl = contentDiv.querySelector('.prks-dev-path-msg');
        if (devPathEl) devPathEl.textContent = `The requested path (${hash}) is not yet fully implemented.`;
    }

    if (routeGen !== window.__prksRouteGen) return;

    if (typeof window.prksInitLazyWorkThumbs === 'function') {
        window.prksInitLazyWorkThumbs(contentDiv);
    }

    const apiErr = typeof window.prksConsumeApiError === 'function' ? window.prksConsumeApiError() : null;
    if (apiErr && contentDiv) {
        const bar = document.createElement('div');
        bar.className = 'api-warning-banner';
        bar.setAttribute('role', 'status');
        bar.textContent = apiErr.message || 'Some data could not be loaded.';
        contentDiv.prepend(bar);
    }

    // Leaving a file clears currentWork at the start of this function, but the right panel
    // is only filled inside renderWorkDetails — refresh it for folders, people, search, etc.
    const onWorkDetailPage = hash.startsWith('#/works/') && window.currentWork;
    if (!onWorkDetailPage) {
        updatePanelContent(getActiveRightPanelTab());
    }
    window.__prksLastResolvedHash = hash;
    contentDiv.removeAttribute('aria-busy');
    prksPlayPageEnterAnimation(contentDiv);
}


function initForms() {
    if (typeof initPrksDocTypeMenu === 'function') {
        initPrksDocTypeMenu('work-doc-type', { selectedValue: 'article' });
    }
    if (typeof prksMountUploadRoleSegmented === 'function') {
        prksMountUploadRoleSegmented('Author');
    }
    if (typeof prksMountLinkRoleSegmented === 'function') {
        prksMountLinkRoleSegmented('Author');
    }
    if (typeof prksBindSegmentedHidden === 'function') {
        prksBindSegmentedHidden('work-status');
    }
    if (typeof prksBindAutosizeTextareas === 'function') {
        prksBindAutosizeTextareas(document);
    }

    document.getElementById('save-work-btn').onclick = async () => {
        const kindEl = document.getElementById('work-source-kind');
        const sourceKind = kindEl ? String(kindEl.value || 'pdf') : 'pdf';
        const fileInput = document.getElementById('work-file');
        let fileBase64 = null; let fileName = null;

        const pdfFileForUpload =
            sourceKind !== 'video' && fileInput && fileInput.files.length > 0
                ? fileInput.files[0]
                : sourceKind !== 'video' && window.__prksPendingUploadPdfFile instanceof File
                  ? window.__prksPendingUploadPdfFile
                  : null;
        if (pdfFileForUpload) {
            const file = pdfFileForUpload;
            fileName = file.name;
            await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = e => { fileBase64 = e.target.result.split(',')[1]; resolve(); };
                reader.onerror = () => reject(reader.error);
                reader.onabort = () => reject(new Error('File read aborted'));
                reader.readAsDataURL(file);
            });
        }

        const folderId = document.getElementById('work-folder-id').value;
        const videoUrlEl = document.getElementById('work-video-url');
        const videoChanEl = document.getElementById('work-video-channel');
        const videoPubEl = document.getElementById('work-video-published-date');
        const videoUrlDateEl = document.getElementById('work-video-urldate');
        const videoPlaylistEl = document.getElementById('work-video-playlist-id'); // hidden input
        const pdfSourceUrlEl = document.getElementById('work-pdf-source-url');
        let sourceUrl = '';
        if (sourceKind === 'video' && videoUrlEl) {
            sourceUrl = String(videoUrlEl.value || '').trim();
        } else if (sourceKind === 'pdf' && pdfSourceUrlEl) {
            sourceUrl = String(pdfSourceUrlEl.value || '').trim();
        }
        if (
            sourceKind === 'video' &&
            sourceUrl &&
            typeof window.prksHandleVideoUrlInput === 'function'
        ) {
            const last = String(window.__prksLastVideoPreviewUrl || '').trim();
            if (last !== sourceUrl || !window.__prksUploadVideoMeta) {
                await window.prksHandleVideoUrlInput(sourceUrl);
            }
        }
        const meta = window.__prksUploadVideoMeta && typeof window.__prksUploadVideoMeta === 'object'
            ? window.__prksUploadVideoMeta
            : null;
        const channelName =
            sourceKind === 'video' && videoChanEl
                ? String(videoChanEl.value || '').trim()
                : '';
        const publishedDate =
            sourceKind === 'video' && videoPubEl ? String(videoPubEl.value || '').trim() : '';
        const urlDate =
            sourceKind === 'video' && videoUrlDateEl ? String(videoUrlDateEl.value || '').trim() : '';
        const playlistId =
            sourceKind === 'video' && videoPlaylistEl ? String(videoPlaylistEl.value || '').trim() : '';
        const publishedIso =
            sourceKind === 'video' ? prksParsePublishedDateInput(publishedDate) : '';
        const workDateEl = document.getElementById('work-date');
        const pdfPublishedRaw =
            sourceKind === 'pdf' && workDateEl ? String(workDateEl.value || '').trim() : '';
        const pdfPublished =
            sourceKind === 'pdf' ? prksParsePublishedDateInput(pdfPublishedRaw) : '';

        let thumb_page = null;
        if (sourceKind === 'pdf') {
            const tpEl = document.getElementById('work-thumb-page');
            const rawTp = tpEl ? String(tpEl.value || '').trim() : '';
            if (rawTp) {
                const n = parseInt(rawTp, 10);
                if (Number.isFinite(n) && n >= 1) thumb_page = n;
            }
        }

        const privNotesEl = document.getElementById('work-private-notes');
        const private_notes = privNotesEl ? String(privNotesEl.value || '') : '';

        const gv = (id) => {
            const el = document.getElementById(id);
            return el ? String(el.value || '').trim() : '';
        };

        const payload = {
            title: document.getElementById('work-title').value,
            status: document.getElementById('work-status').value,
            doc_type: document.getElementById('work-doc-type')
                ? document.getElementById('work-doc-type').value
                : 'article',
            abstract: document.getElementById('work-abstract').value,
            author_text:
                sourceKind === 'video'
                    ? channelName || (meta && meta.author_name ? String(meta.author_name) : "")
                    : "",
            year: document.getElementById('work-year').value,
            folder_id: folderId && folderId.trim() !== "" ? folderId : null,
            file_b64: fileBase64,
            file_name: fileName,
            roles: uploadRoles, // From ui.js
            source_kind: sourceKind,
            source_url: sourceUrl,
            thumb_url: sourceKind === 'video' && meta && meta.thumbnail_url ? String(meta.thumbnail_url) : "",
            provider: sourceKind === 'video' ? "youtube" : "",
            published_date: sourceKind === 'video' ? (publishedIso || null) : (pdfPublished || null),
            urldate: "",
            playlist_id: sourceKind === 'video' ? playlistId : "",
            private_notes,
            thumb_page,
        };

        if (sourceKind === 'pdf') {
            payload.publisher = gv('work-publisher');
            const locEl = document.getElementById('work-location');
            payload.location = locEl ? String(locEl.value || '') : '';
            payload.edition = gv('work-edition');
            payload.journal = gv('work-journal');
            payload.volume = gv('work-volume');
            payload.issue = gv('work-issue');
            payload.pages = gv('work-pages');
            payload.isbn = gv('work-isbn');
            payload.doi = gv('work-doi');
        }

        if (sourceKind === 'video' && !payload.source_url) {
            await prksAlertMessage('Please paste a video URL.', 'Validation');
            return;
        }
        if (sourceKind === 'video' && publishedDate && !publishedIso) {
            await prksAlertMessage('Published date must be in dd/mm/yyyy.', 'Validation');
            return;
        }
        if (sourceKind === 'pdf' && pdfPublishedRaw && !pdfPublished) {
            await prksAlertMessage('Published date must be in dd/mm/yyyy.', 'Validation');
            return;
        }
        if (sourceKind === 'pdf' && !payload.file_b64) {
            await prksAlertMessage('Please select a PDF file.', 'Validation');
            return;
        }
        if (sourceKind === 'video') {
            payload.doc_type = 'online';
        }


        const statusMsg = document.getElementById('upload-status-msg');
        if (statusMsg) { statusMsg.innerText = "Adding..."; statusMsg.classList.remove('hidden'); }

        let res;
        try {
            res = await fetch('/api/works', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
        } catch (e) {
            if (statusMsg) {
                statusMsg.innerText = 'Network error.';
                statusMsg.classList.remove('hidden');
            }
            return;
        }
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            if (statusMsg) {
                statusMsg.innerText = data.error || 'Could not add file.';
                statusMsg.classList.remove('hidden');
            }
            return;
        }
        const newId = data.id;
        if (newId && typeof uploadTagsSelected !== 'undefined' && uploadTagsSelected.length) {
            for (const t of uploadTagsSelected) {
                try {
                    const tr = await fetch(`/api/works/${encodeURIComponent(newId)}/tags`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ tag_id: t.id }),
                    });
                    if (!tr.ok) throw new Error('tag attach failed');
                } catch (_e) {
                    if (statusMsg) {
                        statusMsg.innerText = 'File added, but one or more tags could not be attached.';
                        statusMsg.classList.remove('hidden');
                    }
                    break;
                }
            }
        }
        closeModals();
        window.location.reload();
    };




    const folderTitleInput = document.getElementById('folder-title');
    const folderTitleError = document.getElementById('folder-title-error');
    const folderBtn = document.getElementById('save-folder-btn');
    const folderParentInput = document.getElementById('folder-parent-search');
    const folderParentHidden = document.getElementById('folder-parent-id');
    const folderParentResults = document.getElementById('folder-parent-results');
    if (folderTitleInput && folderTitleError && folderBtn) {
        let folderModalFolders = [];
        let folderParentBound = false;

        function folderTitleNormKey(s) {
            return (s || '').trim().toLowerCase();
        }
        function folderParentNormKey(parentId) {
            return String(parentId || '').trim();
        }
        function effectiveFolderTitleFromInput(raw) {
            const t = (raw || '').trim();
            return t || 'Untitled Folder';
        }
        function folderTitleConflicts(raw, folders, parentId) {
            const key = folderTitleNormKey(effectiveFolderTitleFromInput(raw));
            const pKey = folderParentNormKey(parentId);
            return folders.some(
                (f) =>
                    folderTitleNormKey(f.title) === key &&
                    folderParentNormKey(f.parent_id) === pKey
            );
        }
        function updateFolderTitleDuplicateUi() {
            const parentId = folderParentHidden ? folderParentHidden.value : '';
            const dup = folderTitleConflicts(folderTitleInput.value, folderModalFolders, parentId);
            if (dup) {
                folderTitleError.textContent = 'A folder with this name already exists in this location.';
                folderTitleError.classList.remove('hidden');
                folderTitleInput.setAttribute('aria-invalid', 'true');
                folderBtn.disabled = true;
            } else {
                folderTitleError.textContent = '';
                folderTitleError.classList.add('hidden');
                folderTitleInput.removeAttribute('aria-invalid');
                folderBtn.disabled = false;
            }
        }
        function renderFolderParentDropdown() {
            if (!folderParentInput || !folderParentHidden || !folderParentResults) return;
            const q = String(folderParentInput.value || '').trim().toLowerCase();
            const filtered = folderModalFolders.filter((f) => {
                const label = typeof window.prksFolderRowLabel === 'function'
                    ? window.prksFolderRowLabel(f, folderModalFolders)
                    : String(f.title || '');
                return (
                    !q ||
                    label.toLowerCase().includes(q) ||
                    String(f.title || '').toLowerCase().includes(q)
                );
            });
            folderParentResults.innerHTML = '';
            if (filtered.length === 0) {
                folderParentResults.innerHTML = '<div class="result-item no-results">No folders found</div>';
            } else {
                filtered.slice(0, 80).forEach((f) => {
                    const div = document.createElement('div');
                    div.className = 'result-item';
                    div.textContent = typeof window.prksFolderRowLabel === 'function'
                        ? window.prksFolderRowLabel(f, folderModalFolders)
                        : String(f.title || 'Folder');
                    div.onmousedown = (ev) => {
                        ev.preventDefault();
                        folderParentHidden.value = String(f.id || '');
                        folderParentInput.value = div.textContent || '';
                        prksHideInlineComboboxResults(folderParentResults);
                        updateFolderTitleDuplicateUi();
                    };
                    folderParentResults.appendChild(div);
                });
            }
            if (typeof prksShowInlineComboboxResults === 'function') {
                prksShowInlineComboboxResults(folderParentInput, folderParentResults);
            } else {
                folderParentResults.classList.remove('hidden');
            }
        }
        function bindFolderParentCombobox() {
            if (
                folderParentBound ||
                !folderParentInput ||
                !folderParentHidden ||
                !folderParentResults
            ) {
                return;
            }
            folderParentBound = true;
            folderParentInput.addEventListener('focus', () => renderFolderParentDropdown());
            folderParentInput.addEventListener('input', () => {
                folderParentHidden.value = '';
                renderFolderParentDropdown();
                updateFolderTitleDuplicateUi();
            });
            folderParentInput.addEventListener('blur', () =>
                setTimeout(() => prksHideInlineComboboxResults(folderParentResults), 200)
            );
        }
        window.prksRefreshFolderModalValidation = async function () {
            try {
                folderModalFolders = await fetchFolders();
            } catch (e) {
                folderModalFolders = [];
            }
            bindFolderParentCombobox();
            updateFolderTitleDuplicateUi();
        };
        folderTitleInput.addEventListener('input', updateFolderTitleDuplicateUi);
    }

    if (folderBtn) {
        folderBtn.onclick = async () => {
            if (folderBtn.disabled) return;
            const payload = {
                title: document.getElementById('folder-title').value,
                description: document.getElementById('folder-description').value,
                parent_id: (() => {
                    const raw = (document.getElementById('folder-parent-id')?.value || '').trim();
                    return raw || null;
                })()
            };
            const res = await fetch('/api/folders', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                await prksAlertMessage(data.error || 'Could not create folder', 'Could not save');
                return;
            }
            const pending = window.__prksPendingWorkFolderAttach;
            if (pending && pending.workId && typeof patchWorkFolder === 'function') {
                const attachWid = String(pending.workId);
                window.__prksPendingWorkFolderAttach = null;
                closeModals();
                try {
                    await patchWorkFolder(attachWid, data.id);
                } catch (e) {
                    await prksAlertMessage(
                        (e && e.message) || 'Folder created but could not assign this file.',
                        'Error'
                    );
                }
                if (typeof fetchWorkDetails === 'function' && typeof updatePanelContent === 'function') {
                    window.currentWork = await fetchWorkDetails(attachWid);
                    if (!window.__prksWorkFolderEdit || typeof window.__prksWorkFolderEdit !== 'object') {
                        window.__prksWorkFolderEdit = {};
                    }
                    window.__prksWorkFolderEdit[attachWid] = false;
                    updatePanelContent('details');
                }
                return;
            }
            closeModals();
            const hash = window.location.hash || '';
            if (
                hash === '#/folders' &&
                typeof fetchFolders === 'function' &&
                typeof renderDashboard === 'function'
            ) {
                const contentDiv = document.getElementById('page-content');
                const folders = await fetchFolders();
                if (contentDiv) {
                    renderDashboard(folders, contentDiv);
                }
                return;
            }
            window.location.reload();
        };
    }

    const playlistBtn = document.getElementById('save-playlist-btn');
    if (playlistBtn) {
        playlistBtn.onclick = async () => {
            const titleEl = document.getElementById('playlist-title');
            const descEl = document.getElementById('playlist-description');
            const errEl = document.getElementById('playlist-error');
            const title = titleEl ? String(titleEl.value || '').trim() : '';
            const description = descEl ? String(descEl.value || '').trim() : '';
            if (!title) {
                if (errEl) {
                    errEl.textContent = 'Playlist title is required.';
                    errEl.classList.remove('hidden');
                }
                return;
            }
            if (errEl) {
                errEl.textContent = '';
                errEl.classList.add('hidden');
            }
            playlistBtn.disabled = true;
            const old = playlistBtn.textContent;
            playlistBtn.textContent = 'Creating…';
            try {
                const res = await fetch('/api/playlists', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title, description }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok || !data.id) {
                    throw new Error(data.error || 'Could not create playlist');
                }
                closeModals();
                // If a work is waiting to be attached, attach it now.
                const pending = window.__prksPendingPlaylistAttach;
                if (pending && pending.workId) {
                    try {
                        await fetch(`/api/playlists/${encodeURIComponent(data.id)}/items`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ work_id: pending.workId }),
                        });
                    } catch (_e) {}
                    window.__prksPendingPlaylistAttach = null;
                }
                // Refresh select controls if mounted.
                if (typeof window.__prksRefreshPlaylistSelects === 'function') {
                    await window.__prksRefreshPlaylistSelects(data.id);
                }
                if (typeof window.__prksRefreshAllPlaylistSelects === 'function') {
                    await window.__prksRefreshAllPlaylistSelects(data.id);
                }
                // Navigate only when playlist creation came from the playlists index (not from New File flow).
                if (window.__prksReturnToWorkModalAfterPlaylist === true) {
                    // closeModals() will restore the New File modal.
                } else if ((window.location.hash || '') === '#/playlists') {
                    window.location.hash = '#/playlists/' + encodeURIComponent(data.id);
                    window.location.reload();
                }
            } catch (e) {
                console.error(e);
                if (errEl) {
                    errEl.textContent = 'Could not create playlist.';
                    errEl.classList.remove('hidden');
                } else {
                    await prksAlertMessage('Could not create playlist.', 'Error');
                }
            } finally {
                playlistBtn.disabled = false;
                playlistBtn.textContent = old;
            }
        };
    }

    const personFname = document.getElementById('person-fname');
    const personLname = document.getElementById('person-lname');
    const personAliases = document.getElementById('person-aliases');
    if (personFname && personLname && personAliases) {
        personAliases.addEventListener('input', () => {
            window._personAliasesManual = true;
        });
        personFname.addEventListener('input', () => syncPersonAliasesFromNames());
        personLname.addEventListener('input', () => syncPersonAliasesFromNames());
    }

    const personBtn = document.getElementById('save-person-btn');
    if (personBtn) {
        personBtn.onclick = async () => {
            const birthIso = parsePersonBirthDeathField(document.getElementById('person-birth-date').value);
            if (birthIso === null) {
                await prksAlertMessage(`Birth:\n${PERSON_DATE_HELP}`, 'Validation');
                return;
            }
            const deathIso = parsePersonBirthDeathField(document.getElementById('person-death-date').value);
            if (deathIso === null) {
                await prksAlertMessage(`Date of death:\n${PERSON_DATE_HELP}`, 'Validation');
                return;
            }
            const payload = {
                first_name: document.getElementById('person-fname').value,
                last_name: document.getElementById('person-lname').value,
                aliases: document.getElementById('person-aliases').value,
                about: document.getElementById('person-about').value,
                image_url: document.getElementById('person-image-url').value,
                link_wikipedia: document.getElementById('person-link-wikipedia').value,
                link_stanford_encyclopedia: document.getElementById('person-link-stanford').value,
                link_iep: document.getElementById('person-link-iep').value,
                links_other: document.getElementById('person-links-other').value,
                birth_date: birthIso,
                death_date: deathIso,
            };
            if (!payload.last_name) {
                await prksAlertMessage('Last name is required.', 'Validation');
                return;
            }
            try {
                const res = await fetch('/api/persons', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    await prksAlertMessage(data.error || `Could not save person (${res.status})`, 'Could not save');
                    return;
                }
            } catch (e) {
                await prksAlertMessage('Network error — could not save person.', 'Error');
                return;
            }
            closeModals(); window.location.reload();
        };
    }

    const saveGroupBtn = document.getElementById('save-group-btn');
    if (saveGroupBtn) {
        saveGroupBtn.onclick = async () => {
            const name = document.getElementById('group-name')?.value || '';
            const parentHid = document.getElementById('group-parent-id')?.value?.trim() || '';
            const parentSearch = document.getElementById('group-parent-search')?.value?.trim() || '';
            const description = document.getElementById('group-description')?.value || '';
            const payload = {
                name: name.trim(),
                description: description.trim()
            };
            if (!payload.name) {
                await prksAlertMessage('Group name is required.', 'Validation');
                return;
            }
            if (parentHid) payload.parent_id = parentHid;
            else if (parentSearch) payload.parent_name = parentSearch;
            const res = await fetch('/api/person-groups', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                await prksAlertMessage(data.error || 'Could not create group.', 'Could not save');
                return;
            }
            closeModals();
            window.location.hash = '#/people/groups/' + (data.id || '');
            window.location.reload();
        };
    }

    document.getElementById('save-role-btn').onclick = async () => {
        const person_id = document.getElementById('role-person-id').value;
        const work_id = document.getElementById('role-work-id').value;
        if (!person_id || !work_id) {
            await prksAlertMessage('Please select both a person and a file.', 'Validation');
            return;
        }
        const role_type = document.getElementById('role-type').value;
        const credit_name =
            typeof prksResolveRoleCreditNameForLink === 'function'
                ? prksResolveRoleCreditNameForLink(
                      'role-link',
                      person_id,
                      'role-person-search'
                  )
                : typeof prksReadRoleCreditName === 'function'
                  ? prksReadRoleCreditName('role-link')
                  : '';
        const payload = {
            person_id,
            work_id,
            role_type,
            credit_name,
        };
        if (
            typeof prksWorkHasRoleLink === 'function' &&
            window.currentWork &&
            String(window.currentWork.id) === String(work_id) &&
            prksWorkHasRoleLink(window.currentWork.roles, person_id, role_type)
        ) {
            if (typeof prksShowDuplicateRoleLinkAlert === 'function') {
                await prksShowDuplicateRoleLinkAlert(role_type);
            }
            return;
        }
        try {
            const res = await fetch('/api/roles', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                if (typeof prksNotifyRoleLinkFailure === 'function') {
                    await prksNotifyRoleLinkFailure(data.error, role_type);
                }
                return;
            }
        } catch (e) {
            console.error(e);
            if (typeof prksAlertDialog === 'function') {
                await prksAlertDialog({
                    title: 'Could not link',
                    message: 'Could not create link.',
                });
            }
            return;
        }
        closeModals();
        const hash = window.location.hash || '';
        const workIdFromHash = hash.startsWith('#/works/') ? hash.split('/')[2] : '';
        const onThisWork =
            workIdFromHash &&
            String(workIdFromHash) === String(work_id) &&
            window.currentWork &&
            String(window.currentWork.id) === String(work_id);
        if (onThisWork && typeof fetchWorkDetails === 'function') {
            window.currentWork = await fetchWorkDetails(work_id);
            const panel = document.getElementById('panel-content');
            const tab = typeof getActiveRightPanelTab === 'function' ? getActiveRightPanelTab() : 'details';
            if (panel && tab === 'details' && typeof prksWorkRightPanelStackHtml === 'function') {
                panel.innerHTML = prksWorkRightPanelStackHtml(window.currentWork, false);
                if (typeof initPrksPrivateNotesEditor === 'function') {
                    initPrksPrivateNotesEditor('work', window.currentWork.id);
                }
                if (typeof initWorkTagCombobox === 'function') initWorkTagCombobox(window.currentWork.id);
                if (typeof initWorkDetailRightPanelActions === 'function') {
                    initWorkDetailRightPanelActions(window.currentWork);
                }
            }
        } else {
            window.location.reload();
        }
    };
}

function initSearch() {
    const searchInput = document.getElementById('global-search');
    const root = document.getElementById('prks-global-search');
    const chip = document.getElementById('prks-global-search-mode');
    const runBtn = document.getElementById('prks-global-search-run');
    if (!searchInput || !root || !chip || !runBtn || root.dataset.bound === '1') return;
    root.dataset.bound = '1';

    const LS_KEY = 'prks.search.mode';
    const VALID = new Set(['keywords', 'people', 'published', 'all']);
    const ORDER = ['all', 'keywords', 'people', 'published'];

    const readMode = () => {
        try {
            const raw = String(localStorage.getItem(LS_KEY) || '').trim();
            return VALID.has(raw) ? raw : 'all';
        } catch (_e) {
            return 'all';
        }
    };
    const writeMode = (m) => {
        const mode = VALID.has(m) ? m : 'all';
        try {
            localStorage.setItem(LS_KEY, mode);
        } catch (_e) {}
        return mode;
    };

    const modeLabel = (mode) => {
        if (mode === 'people') return 'People';
        if (mode === 'published') return 'Publisher';
        if (mode === 'keywords') return 'Keywords';
        return 'All';
    };

    const placeholderForMode = (mode) => {
        if (mode === 'people') return 'People…';
        if (mode === 'published') return 'Publisher…';
        if (mode === 'all') return 'Search…';
        return 'Keywords…';
    };

    const applyMode = (mode) => {
        const m = writeMode(mode);
        chip.textContent = modeLabel(m);
        chip.setAttribute('aria-label', `Search mode: ${modeLabel(m)}`);
        searchInput.setAttribute('placeholder', placeholderForMode(m));
    };

    const cycleMode = () => {
        const cur = readMode();
        const idx = ORDER.indexOf(cur);
        const next = ORDER[(idx >= 0 ? idx + 1 : 0) % ORDER.length];
        applyMode(next);
    };

    const run = () => {
        const query = String(searchInput.value || '').trim();
        if (!query) return;
        const mode = readMode();
        const p = new URLSearchParams();
        if (mode === 'keywords') {
            p.set('q', query);
        } else if (mode === 'people') {
            p.set('author', query);
        } else if (mode === 'published') {
            p.set('publisher', query);
        } else {
            // "All" should be OR across keyword/author/publisher.
            // Backend supports this via /api/search?any=1&q=...
            p.set('any', '1');
            p.set('q', query);
        }
        const targetHash = '#/search?' + p.toString();
        const prevHash = window.location.hash || '';
        window.location.hash = targetHash;
        if (prevHash === targetHash && typeof handleRoute === 'function') {
            void handleRoute();
        }
    };

    applyMode(readMode());

    searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            run();
        }
    });

    runBtn.addEventListener('click', () => {
        run();
        searchInput.focus();
    });

    chip.addEventListener('click', () => {
        cycleMode();
        searchInput.focus();
    });
    chip.addEventListener('keydown', (e) => {
        if (e.key === ' ' || e.key === 'Enter') {
            e.preventDefault();
            cycleMode();
            searchInput.focus();
        }
    });
}
