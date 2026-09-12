// Run immediately to prevent theme flashing
(function() {
    const savedTheme = localStorage.getItem('prks-theme') || 'system';
    if (savedTheme !== 'system') {
        document.documentElement.setAttribute('data-theme', savedTheme);
    }
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
        const explicit = document.documentElement.getAttribute('data-theme');
        let dark = explicit === 'dark';
        if (explicit !== 'light' && explicit !== 'dark') {
            dark = typeof window.matchMedia === 'function'
                && window.matchMedia('(prefers-color-scheme: dark)').matches;
        }
        meta.setAttribute('content', dark ? '#818cf8' : '#6d6cf7');
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
        if (typeof window.prksReportClientError === 'function') {
            window.prksReportClientError({
                kind: 'window_error',
                error_name: err && err.name ? String(err.name) : 'Error',
                source: event && event.filename ? String(event.filename) : 'client',
                line: event && event.lineno,
                column: event && event.colno,
            });
        }
    });
    window.addEventListener('unhandledrejection', (event) => {
        const reason = event ? event.reason : null;
        if (typeof window.prksReportClientError === 'function') {
            window.prksReportClientError({
                kind: 'unhandled_rejection',
                error_name: reason instanceof Error ? (reason.name || 'Error') : 'Error',
                source: 'client',
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
    initPrksBackupRestoreAction();
    initPrksPdfTextReindexAction();
    initPrksExistingPdfLinearizeAction();
    initPrksPerformanceDiagnostics();
    initPrksOfflineCacheSettings();
    initPrksConnectivityIndicator();
    initPrksSettingsCategoryNav();
    initPrksPdfRememberPageSetting();
    initPrksPdfLastPageVisibilityFlush();
    initPrksHintsSetting();
    initForceMobileSetting();
    initMobileWorkNotesRightSetting();
    if (typeof initPrksHintUi === 'function') initPrksHintUi();
    if (typeof initModalCloseUi === 'function') initModalCloseUi();
    if (typeof initMobileShell === 'function') initMobileShell();
    if (typeof prksWorkspaceInit === 'function') prksWorkspaceInit();
    initRouter();
    initTabs();
    initForms();
    if (typeof prksInitNavDisclosures === 'function') prksInitNavDisclosures();
    if (typeof prksInitRibbonCreate === 'function') prksInitRibbonCreate();
    if (typeof prksInitCommandPalette === 'function') prksInitCommandPalette();
    if (typeof prksInitSavedViews === 'function') prksInitSavedViews();
    initUploadDragAndDrop();
});

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
    if (typeof prksParseRoute === 'function') {
        const route = prksParseRoute(h);
        return route.name === 'work' && route.params.workId ? route.params.workId : null;
    }
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
    if (typeof prksForEachLiveTabContext !== 'function') return;
    prksForEachLiveTabContext(function (ctx) {
        const pdf = ctx && typeof ctx.getResource === 'function' ? ctx.getResource('pdf') : null;
        if (!pdf) return;
        if (workId && pdf.workId && String(pdf.workId) !== String(workId)) return;
        if (typeof pdf.flushLastPage === 'function') pdf.flushLastPage();
    });
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
        if (typeof prksForEachLiveTabContext !== 'function') return;
        prksForEachLiveTabContext(function (ctx) {
            const pdf = ctx && typeof ctx.getResource === 'function' ? ctx.getResource('pdf') : null;
            if (pdf && typeof pdf.flushLastPage === 'function') pdf.flushLastPage();
        });
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
                    typeof prksSyncDenseWorkspaceShell === 'function' &&
                    typeof prksWorkspaceVisualTiled === 'function'
                ) {
                    prksSyncDenseWorkspaceShell(prksWorkspaceVisualTiled());
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
    function applyToWorkspace(ws) {
        if (!ws) return;
        const narrow = ws.clientWidth > 0 && ws.clientWidth < 720;
        const want = mobileWorkNotesRightEnabled && narrow;
        ws.classList.toggle('work-workspace--side', want);
    }
    if (typeof prksForEachMountedTabContext === 'function') {
        prksForEachMountedTabContext(function (c) {
            const ws = c && typeof c.query === 'function' ? c.query('.work-workspace[data-work-id]') : null;
            applyToWorkspace(ws);
            if (typeof window.prksReapplyWorkNotesSplitLayout === 'function') {
                window.prksReapplyWorkNotesSplitLayout(c);
            }
            const sync = c && typeof c.getResource === 'function' ? c.getResource('workNotesCollapseSync') : null;
            if (typeof sync === 'function') sync();
        });
    } else if (typeof window.prksReapplyWorkNotesSplitLayout === 'function') {
        window.prksReapplyWorkNotesSplitLayout();
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

function prksUpdateBibtexExportSummary() {
    const summary = document.getElementById('prks-bibtex-export-summary');
    if (!summary) return;
    const host = document.getElementById('prks-bibtex-export-fields');
    const total = PRKS_BIBTEX_EXPORT_FIELD_DEFS.length;
    const on = host
        ? host.querySelectorAll('button[data-bibtex-field].prks-toggle[aria-checked="true"]').length
        : 0;
    summary.textContent = on + ' of ' + total + ' fields included';
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
        prksUpdateBibtexExportSummary();
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
            prksUpdateBibtexExportSummary();
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

function initPrksBackupRestoreAction() {
    const downloadBtn = document.getElementById('prks-backup-download-btn');
    const cancelBtn = document.getElementById('prks-backup-cancel-btn');
    const downloadStatus = document.getElementById('prks-backup-download-status');
    const progressWrap = document.getElementById('prks-backup-progress');
    const progressBar = document.getElementById('prks-backup-progress-bar');
    const progressLabel = document.getElementById('prks-backup-progress-label');
    const fileInput = document.getElementById('prks-backup-file-input');
    const chooseBtn = document.getElementById('prks-backup-choose-btn');
    const fileLabel = document.getElementById('prks-backup-file-label');
    const verifyBtn = document.getElementById('prks-backup-verify-btn');
    const restoreStatus = document.getElementById('prks-backup-restore-status');
    const verifiedPanel = document.getElementById('prks-backup-verified-panel');
    const summaryEl = document.getElementById('prks-backup-summary');
    const restoreBtn = document.getElementById('prks-backup-restore-btn');
    const confirmInput = document.getElementById('prks-restore-confirm-input');
    const confirmSubmit = document.getElementById('prks-restore-confirm-submit');
    const confirmStatus = document.getElementById('prks-restore-confirm-status');
    if (!downloadBtn || downloadBtn.dataset.bound === '1') return;
    downloadBtn.dataset.bound = '1';

    let stagedToken = '';
    let restoreBusy = false;
    let backupAbort = null;

    const phaseLabel = (ev) => {
        const phase = String(ev && ev.phase ? ev.phase : '');
        const filesDone = Number(ev && ev.files_done) || 0;
        const filesTotal = Number(ev && ev.files_total) || 0;
        const filePart = filesTotal > 0 ? ` ${filesDone} of ${filesTotal} files.` : '';
        if (phase === 'snapshot') return 'Snapshotting database…';
        if (phase === 'archiving') return 'Packing files…' + filePart;
        if (phase === 'verifying') return 'Verifying backup…' + filePart;
        if (phase === 'ready') return 'Download starting…';
        if (phase === 'cancelled') return 'Backup cancelled.';
        if (phase === 'failed') return (ev && ev.error) || 'Backup could not be created.';
        return 'Preparing backup…';
    };

    const setBackupProgressUi = (ev) => {
        const pct = Math.max(0, Math.min(100, Number(ev && ev.percent) || 0));
        if (progressBar) progressBar.value = pct;
        if (progressLabel) progressLabel.textContent = phaseLabel(ev);
        if (progressWrap) progressWrap.classList.remove('hidden');
        progressWrap && progressWrap.setAttribute('aria-busy', ev && ev.phase === 'ready' ? 'false' : 'true');
    };

    const hideBackupProgress = () => {
        if (progressWrap) {
            progressWrap.classList.add('hidden');
            progressWrap.setAttribute('aria-busy', 'false');
        }
        if (progressBar) progressBar.value = 0;
        if (progressLabel) progressLabel.textContent = '';
    };

    const setBackupBusy = (busy) => {
        downloadBtn.disabled = !!busy;
        if (cancelBtn) {
            cancelBtn.classList.toggle('hidden', !busy);
            cancelBtn.disabled = !busy;
        }
    };

    const setRestoreBusy = (busy) => {
        restoreBusy = !!busy;
        if (chooseBtn) chooseBtn.disabled = restoreBusy;
        if (fileInput) fileInput.disabled = restoreBusy;
        if (verifyBtn) verifyBtn.disabled = restoreBusy;
        if (restoreBtn) restoreBtn.disabled = restoreBusy;
        if (confirmSubmit) {
            const typed = confirmInput && confirmInput.value.trim() === 'RESTORE';
            confirmSubmit.disabled = restoreBusy || !typed;
        }
        if (confirmInput) confirmInput.disabled = restoreBusy;
    };

    const hideVerified = () => {
        stagedToken = '';
        if (verifiedPanel) verifiedPanel.classList.add('hidden');
        if (summaryEl) summaryEl.innerHTML = '';
    };

    const formatCreated = (iso) => {
        const raw = String(iso || '').trim();
        if (!raw) return '';
        const d = new Date(raw);
        if (Number.isNaN(d.getTime())) return raw;
        try {
            return d.toLocaleString(undefined, {
                day: 'numeric',
                month: 'short',
                year: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
            });
        } catch (_e) {
            return raw;
        }
    };

    const startBackupDownload = (token, filename) => {
        const a = document.createElement('a');
        const q = new URLSearchParams({ token: String(token || '') });
        a.href = '/api/backups/download?' + q.toString();
        if (filename) a.setAttribute('download', filename);
        else a.setAttribute('download', '');
        a.style.display = 'none';
        document.body.appendChild(a);
        a.click();
        a.remove();
    };

    const readBackupProgress = async (reader) => {
        const decoder = new TextDecoder();
        let buf = '';
        let last = null;
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            const lines = buf.split('\n');
            buf = lines.pop() || '';
            for (const line of lines) {
                const raw = line.trim();
                if (!raw) continue;
                let ev;
                try {
                    ev = JSON.parse(raw);
                } catch (_e) {
                    continue;
                }
                last = ev;
                setBackupProgressUi(ev);
                if (ev.phase === 'ready' && ev.token) {
                    startBackupDownload(ev.token, ev.filename);
                }
            }
        }
        if (buf.trim()) {
            try {
                last = JSON.parse(buf.trim());
                setBackupProgressUi(last);
                if (last.phase === 'ready' && last.token) {
                    startBackupDownload(last.token, last.filename);
                }
            } catch (_e) {
                /* ignore trailing fragment */
            }
        }
        return last;
    };

    downloadBtn.addEventListener('click', async () => {
        if (backupAbort) return;
        setBackupBusy(true);
        hideBackupProgress();
        setBackupProgressUi({ phase: 'snapshot', percent: 1 });
        if (downloadStatus) downloadStatus.textContent = '';
        backupAbort = new AbortController();
        try {
            if (typeof prksStartBackupProgress !== 'function') {
                throw new Error('Backup API unavailable.');
            }
            const reader = await prksStartBackupProgress(backupAbort.signal);
            const last = await readBackupProgress(reader);
            if (!last || last.phase === 'cancelled') {
                if (downloadStatus) downloadStatus.textContent = 'Backup cancelled.';
                hideBackupProgress();
                return;
            }
            if (last.phase === 'failed') {
                hideBackupProgress();
                if (downloadStatus) {
                    downloadStatus.textContent =
                        last.error || 'Backup could not be created.';
                }
                return;
            }
            hideBackupProgress();
            const extra = Array.isArray(last.warnings) && last.warnings.length ? ` ${last.warnings.join(' ')}` : '';
            if (downloadStatus) downloadStatus.textContent = 'Download starting…' + extra;
        } catch (e) {
            if (e && e.name === 'AbortError') {
                if (downloadStatus) downloadStatus.textContent = 'Backup cancelled.';
                hideBackupProgress();
            } else if (downloadStatus) {
                downloadStatus.textContent = (e && e.message) || 'Backup could not be created.';
            }
        } finally {
            backupAbort = null;
            setBackupBusy(false);
        }
    });

    if (cancelBtn) {
        cancelBtn.addEventListener('click', () => {
            if (!backupAbort) return;
            backupAbort.abort();
        });
    }

    if (chooseBtn && fileInput) {
        chooseBtn.addEventListener('click', () => {
            if (restoreBusy) return;
            fileInput.click();
        });
    }
    if (fileInput) {
        fileInput.addEventListener('change', () => {
            hideVerified();
            const file = fileInput.files && fileInput.files[0];
            if (fileLabel) fileLabel.textContent = file ? file.name : 'No file selected';
            if (restoreStatus) restoreStatus.textContent = '';
        });
    }
    if (verifyBtn) {
        verifyBtn.addEventListener('click', async () => {
            const file = fileInput && fileInput.files && fileInput.files[0];
            if (!file) {
                if (restoreStatus) restoreStatus.textContent = 'Choose a backup file first.';
                return;
            }
            setRestoreBusy(true);
            hideVerified();
            if (restoreStatus) restoreStatus.textContent = 'Uploading backup… Verifying backup…';
            try {
                if (typeof prksStageBackup !== 'function') {
                    throw new Error('Backup API unavailable.');
                }
                const out = await prksStageBackup(file);
                stagedToken = String(out.token || '');
                const summary = out.summary && typeof out.summary === 'object' ? out.summary : {};
                if (summaryEl) {
                    summaryEl.innerHTML = '';
                    const rows = [
                        ['Created', formatCreated(summary.created_at)],
                        ['Works', String(summary.works ?? '')],
                        ['PDFs', String(summary.pdf_files ?? summary.managed_pdfs ?? '')],
                        ['Persons', String(summary.persons ?? '')],
                        ['Annotations', String(summary.annotations ?? '')],
                    ];
                    for (const [label, value] of rows) {
                        if (!value && value !== '0') continue;
                        const li = document.createElement('li');
                        li.textContent = `${label}: ${value}`;
                        summaryEl.appendChild(li);
                    }
                }
                if (verifiedPanel) verifiedPanel.classList.remove('hidden');
                const warnings = Array.isArray(out.warnings) ? out.warnings : [];
                if (restoreStatus) {
                    restoreStatus.textContent = warnings.length
                        ? `Backup verified. ${warnings.join(' ')}`
                        : 'Backup verified';
                }
            } catch (e) {
                hideVerified();
                if (restoreStatus) {
                    restoreStatus.textContent =
                        (e && e.message) || 'Backup could not be verified. Current PRKS data was not changed.';
                }
            } finally {
                setRestoreBusy(false);
            }
        });
    }
    if (restoreBtn) {
        restoreBtn.addEventListener('click', () => {
            if (!stagedToken || restoreBusy) return;
            if (confirmInput) confirmInput.value = '';
            if (confirmStatus) confirmStatus.textContent = '';
            if (confirmSubmit) confirmSubmit.disabled = true;
            if (typeof openModal === 'function') openModal('prks-restore-confirm-modal');
        });
    }
    if (confirmInput && confirmSubmit) {
        const syncConfirm = () => {
            confirmSubmit.disabled = restoreBusy || confirmInput.value.trim() !== 'RESTORE';
        };
        confirmInput.addEventListener('input', syncConfirm);
        confirmSubmit.addEventListener('click', async () => {
            if (confirmInput.value.trim() !== 'RESTORE' || !stagedToken || restoreBusy) return;
            setRestoreBusy(true);
            if (confirmStatus) confirmStatus.textContent = 'Restoring library… Rebuilding search index…';
            if (restoreStatus) restoreStatus.textContent = 'Restoring library… Rebuilding search index…';
            try {
                if (typeof prksRestoreBackup !== 'function') {
                    throw new Error('Backup API unavailable.');
                }
                const out = await prksRestoreBackup(stagedToken);
                stagedToken = '';
                const extra = Array.isArray(out.warnings) && out.warnings.length ? ` ${out.warnings.join(' ')}` : '';
                if (confirmStatus) confirmStatus.textContent = 'Restore complete — reloading…' + extra;
                if (restoreStatus) restoreStatus.textContent = 'Restore complete — reloading…' + extra;
                window.location.reload();
            } catch (e) {
                const msg =
                    (e && e.message) || 'Restore failed. Current PRKS data was not changed.';
                if (confirmStatus) confirmStatus.textContent = msg;
                if (restoreStatus) restoreStatus.textContent = msg;
                setRestoreBusy(false);
            }
        });
    }
}

function prksFormatTextIndexSummary(out) {
    const processed = Number(out.processed || 0);
    const updated = Number(out.updated || 0);
    const unchanged = Number(out.unchanged || 0);
    const empty = Number(out.empty || 0);
    const failed = Number(out.failed || 0);
    const orphans = Number(out.removed_orphans || 0);
    const parts = [];
    if (updated) parts.push(`${updated} rebuilt`);
    if (unchanged) parts.push(`${unchanged} already current`);
    if (empty) parts.push(`${empty} contained no text`);
    if (failed) parts.push(`${failed} failed`);
    const pdfLabel = processed === 1 ? 'PDF' : 'PDFs';
    let msg = `Done. ${processed} ${pdfLabel} checked`;
    if (parts.length) msg += `: ${parts.join(', ')}.`;
    else msg += '.';
    if (orphans) {
        const entryLabel = orphans === 1 ? 'entry was' : 'entries were';
        msg += ` ${orphans} stale index ${entryLabel} removed.`;
    }
    return msg;
}

function initPrksPdfTextReindexAction() {
    const btn = document.getElementById('prks-reindex-pdf-text-btn');
    const statusEl = document.getElementById('prks-reindex-pdf-text-status');
    if (!btn || btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', async () => {
        btn.disabled = true;
        const oldText = btn.textContent;
        btn.textContent = 'Rebuilding…';
        if (statusEl) statusEl.textContent = '';
        try {
            if (typeof prksReindexPdfText !== 'function') {
                throw new Error('PDF rebuild API unavailable.');
            }
            const out = await prksReindexPdfText();
            if (statusEl) statusEl.textContent = prksFormatTextIndexSummary(out);
        } catch (e) {
            if (statusEl) statusEl.textContent = (e && e.message) || 'Could not rebuild PDF text index.';
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

let __prksPerfSnapshot = null;
let __prksClientRequestSnapshot = null;

function prksFormatClientRequestReport(client) {
    if (!client || typeof client !== 'object') return '';
    const counts = client.counts || {};
    const current = client.current || {};
    const peaks = client.peaks || {};
    const waits = client.waits || {};
    const avoided = Number(counts.dedupeJoins || 0) + Number(counts.burstCacheHits || 0) + Number(counts.coalescedMutations || 0);
    return [
        'Client request coordinator',
        'Client requests: ' + String(counts.started || 0),
        'Network requests avoided: ' + String(avoided) +
            ' (deduped ' + String(counts.dedupeJoins || 0) +
            ', cache ' + String(counts.burstCacheHits || 0) +
            ', coalesced ' + String(counts.coalescedMutations || 0) + ')',
        'Retries: ' + String(counts.retries || 0),
        'Aborted obsolete reads: ' + String(counts.aborted || 0),
        'Now: reads ' + String(current.activeReads || 0) + '/4, mutations ' +
            String(current.activeMutation || 0) + '/1, queued reads ' +
            String((current.queuedForegroundReads || 0) + (current.queuedBackgroundReads || 0)) +
            ', queued mutations ' + String(current.queuedMutations || 0),
        'Peak mutation queue: ' + String(peaks.queuedMutations || 0),
        'Average read queue wait: ' + prksFormatPerfMs(waits.readAverageMs) + ' ms',
    ].join('\n');
}

function prksFormatPerfSeconds(s) {
    s = Math.max(0, Math.floor(Number(s) || 0));
    if (s < 60) return s + 's';
    if (s < 3600) return Math.floor(s / 60) + ' min';
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return m ? (h + 'h ' + m + 'm') : (h + 'h');
}

function prksFormatPerfMs(v) {
    if (v == null || !Number.isFinite(Number(v))) return '—';
    const n = Number(v);
    if (n >= 10) return String(Math.round(n));
    return n.toFixed(1);
}

function prksPerfRouteLabel(row) {
    return String(row.method || '') + ' ' + String(row.route || '');
}

function prksFormatPerformanceReport(snap) {
    if (!snap || typeof snap !== 'object') return 'PRKS performance report\nNo data.';
    const lines = [
        'PRKS performance report',
        'Measurement window: ' + prksFormatPerfSeconds(snap.measured_for_seconds),
        'Slow threshold: ' + prksFormatPerfMs(snap.slow_threshold_ms) + 'ms',
        'Requests: ' + String((snap.requests && snap.requests.total) || 0),
        'Slow: ' + String((snap.requests && snap.requests.slow) || 0),
        '',
    ];
    const routes = Array.isArray(snap.routes) ? snap.routes : [];
    routes.forEach((row) => {
        lines.push(
            prksPerfRouteLabel(row) +
            ' calls=' + String(row.count || 0) +
            ' avg=' + prksFormatPerfMs(row.avg_ms) + 'ms' +
            ' p95=' + prksFormatPerfMs(row.p95_ms) + 'ms' +
            ' max=' + prksFormatPerfMs(row.max_ms) + 'ms' +
            ' db_avg=' + prksFormatPerfMs(row.avg_db_ms) + 'ms' +
            ' db_share=' + (row.measured_db_share_percent == null ? '—' : String(row.measured_db_share_percent) + '%') +
            ' db_calls=' + prksFormatPerfMs(row.db_calls_avg != null ? row.db_calls_avg : ((row.count ? (Number(row.db_calls || 0) / row.count) : 0))) + '/call'
        );
    });
    const spans = snap.spans && typeof snap.spans === 'object' ? snap.spans : {};
    const spanNames = Object.keys(spans);
    if (spanNames.length) {
        lines.push('');
        spanNames.forEach((name) => {
            const sp = spans[name] || {};
            lines.push(
                name +
                ' count=' + String(sp.count || 0) +
                ' avg=' + prksFormatPerfMs(sp.avg_ms) + 'ms' +
                ' p95=' + prksFormatPerfMs(sp.p95_ms) + 'ms'
            );
        });
    }
    const c = snap.counters && typeof snap.counters === 'object' ? snap.counters : {};
    const hits = Number(c.thumbnail_cache_hits || 0);
    const misses = Number(c.thumbnail_cache_misses || 0);
    const total = hits + misses;
    const rate = total ? Math.round((1000 * hits) / total) / 10 : null;
    lines.push('');
    lines.push(
        'Thumbnail cache: ' +
        hits + ' hits / ' + misses + ' misses' +
        (rate == null ? '' : ' — ' + rate + '% hit rate')
    );
    const clientText = prksFormatClientRequestReport(__prksClientRequestSnapshot);
    if (clientText) {
        lines.push('');
        lines.push(clientText);
    }
    return lines.join('\n');
}

function prksRenderPerformanceDiagnostics(snap) {
    __prksPerfSnapshot = snap;
    const summaryEl = document.getElementById('prks-perf-summary');
    const bodyEl = document.getElementById('prks-perf-routes-body');
    const spansEl = document.getElementById('prks-perf-spans');
    const thumbsEl = document.getElementById('prks-perf-thumbs');
    const req = (snap && snap.requests) || {};
    const threshold = prksFormatPerfMs(snap && snap.slow_threshold_ms);
    if (summaryEl) {
        summaryEl.textContent =
            'Measured for: ' + prksFormatPerfSeconds(snap && snap.measured_for_seconds) +
            '. API requests: ' + String(req.total || 0) +
            '. Slow requests (>' + threshold + ' ms): ' + String(req.slow || 0) +
            '.';
    }
    const routes = Array.isArray(snap && snap.routes) ? snap.routes.slice() : [];
    if (bodyEl) {
        bodyEl.replaceChildren();
        if (!routes.length) {
            const tr = document.createElement('tr');
            const td = document.createElement('td');
            td.colSpan = 7;
            td.textContent = 'No API requests measured yet.';
            tr.appendChild(td);
            bodyEl.appendChild(tr);
        } else {
            routes.forEach((row) => {
                const tr = document.createElement('tr');
                const cells = [
                    prksPerfRouteLabel(row),
                    String(row.count || 0),
                    prksFormatPerfMs(row.avg_ms),
                    prksFormatPerfMs(row.p95_ms),
                    prksFormatPerfMs(row.max_ms),
                    row.measured_db_share_percent == null ? '—' : (String(row.measured_db_share_percent) + '%'),
                    prksFormatPerfMs(row.db_calls_avg != null ? row.db_calls_avg : (row.count ? (Number(row.db_calls || 0) / row.count) : 0)),
                ];
                cells.forEach((text) => {
                    const td = document.createElement('td');
                    td.textContent = text;
                    tr.appendChild(td);
                });
                bodyEl.appendChild(tr);
            });
        }
    }
    const spans = (snap && snap.spans && typeof snap.spans === 'object') ? snap.spans : {};
    const wanted = [
        ['pdf_file_stats', 'PDF file stats'],
        ['processing_scan', 'Processing scan'],
        ['pdf_text_search', 'PDF text search'],
        ['thumbnail_render', 'Thumbnail render'],
        ['json_encode', 'JSON encode'],
        ['gzip', 'Gzip'],
        ['portrait_fetch', 'Portrait fetch'],
        ['text_index_reconcile', 'Text index reconcile'],
        ['text_index_load_state', 'Text index load state'],
        ['text_index_source_scan', 'Text index source scan'],
        ['text_index_extract', 'Text index extract'],
        ['text_index_write', 'Text index write'],
        ['text_index_fts_verify', 'Text index FTS verify'],
        ['backup_create', 'Backup create'],
        ['restore_commit', 'Restore commit'],
        ['pdf_linearize', 'PDF linearize'],
    ];
    const spanParts = [];
    wanted.forEach(([key, label]) => {
        const sp = spans[key];
        if (!sp || !sp.count) return;
        spanParts.push(label + ' avg ' + prksFormatPerfMs(sp.avg_ms) + ' ms');
    });
    if (spansEl) {
        spansEl.textContent = spanParts.length ? ('Subsystems: ' + spanParts.join('. ') + '.') : '';
    }
    const c = (snap && snap.counters) || {};
    const hits = Number(c.thumbnail_cache_hits || 0);
    const misses = Number(c.thumbnail_cache_misses || 0);
    const total = hits + misses;
    const rate = total ? (Math.round((1000 * hits) / total) / 10) : null;
    if (thumbsEl) {
        thumbsEl.textContent = total
            ? ('Thumbnail cache: ' + hits + ' hits / ' + misses + ' misses — ' + rate + '% hit rate.')
            : 'Thumbnail cache: no thumbnail requests yet.';
    }
    const clientBody = document.getElementById('prks-perf-client-body');
    if (clientBody) {
        const client = __prksClientRequestSnapshot;
        if (!client || typeof client !== 'object') {
            clientBody.textContent = 'Client request coordinator: no measurements yet.';
        } else {
            const counts = client.counts || {};
            const current = client.current || {};
            const peaks = client.peaks || {};
            const waits = client.waits || {};
            const maxReads = typeof PRKS_REQUEST_MAX_READS === 'number' ? PRKS_REQUEST_MAX_READS : 4;
            clientBody.textContent =
                'Client requests: ' + String(counts.started || 0) + '. ' +
                'Network requests avoided: ' +
                String(counts.dedupeJoins || 0) + ' in-flight deduplicated, ' +
                String(counts.burstCacheHits || 0) + ' burst-cache hits, ' +
                String(counts.coalescedMutations || 0) + ' autosaves coalesced. ' +
                'Retries: ' + String(counts.retries || 0) + '. ' +
                'Aborted obsolete reads: ' + String(counts.aborted || 0) + '. ' +
                'Now: Reads ' + String(current.activeReads || 0) + '/' + String(maxReads) +
                ', Mutations ' + String(current.activeMutation || 0) + '/1' +
                ', Queued reads ' + String((current.queuedForegroundReads || 0) + (current.queuedBackgroundReads || 0)) +
                ', Queued mutations ' + String(current.queuedMutations || 0) + '. ' +
                'Peak mutation queue: ' + String(peaks.queuedMutations || 0) + '. ' +
                'Average read queue wait: ' + prksFormatPerfMs(waits.readAverageMs) + ' ms.';
        }
    }
}

async function prksLoadPerformanceDiagnostics() {
    const statusEl = document.getElementById('prks-perf-status');
    try {
        if (typeof prksGetPerformanceDiagnostics !== 'function') {
            throw new Error('Performance API unavailable.');
        }
        const snap = await prksGetPerformanceDiagnostics();
        __prksClientRequestSnapshot =
            typeof prksRequestCoordinatorSnapshot === 'function' ? prksRequestCoordinatorSnapshot() : null;
        prksRenderPerformanceDiagnostics(snap);
        if (statusEl) statusEl.textContent = '';
    } catch (e) {
        if (statusEl) statusEl.textContent = (e && e.message) || 'Could not load performance diagnostics.';
    }
}

window.prksLoadPerformanceDiagnostics = prksLoadPerformanceDiagnostics;

function initPrksPerformanceDiagnostics() {
    const refreshBtn = document.getElementById('prks-perf-refresh-btn');
    const resetBtn = document.getElementById('prks-perf-reset-btn');
    const copyBtn = document.getElementById('prks-perf-copy-btn');
    if (refreshBtn && refreshBtn.dataset.bound !== '1') {
        refreshBtn.dataset.bound = '1';
        refreshBtn.addEventListener('click', () => {
            void prksLoadPerformanceDiagnostics();
        });
    }
    if (resetBtn && resetBtn.dataset.bound !== '1') {
        resetBtn.dataset.bound = '1';
        resetBtn.addEventListener('click', async () => {
            const statusEl = document.getElementById('prks-perf-status');
            try {
                if (typeof prksResetPerformanceDiagnostics !== 'function') {
                    throw new Error('Performance API unavailable.');
                }
                await prksResetPerformanceDiagnostics();
                if (typeof prksResetRequestCoordinatorDiagnostics === 'function') {
                    prksResetRequestCoordinatorDiagnostics();
                }
                await prksLoadPerformanceDiagnostics();
                if (statusEl) statusEl.textContent = 'Measurements reset.';
            } catch (e) {
                if (statusEl) statusEl.textContent = (e && e.message) || 'Could not reset measurements.';
            }
        });
    }
    if (copyBtn && copyBtn.dataset.bound !== '1') {
        copyBtn.dataset.bound = '1';
        copyBtn.addEventListener('click', async () => {
            const statusEl = document.getElementById('prks-perf-status');
            const text = prksFormatPerformanceReport(__prksPerfSnapshot);
            try {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    await navigator.clipboard.writeText(text);
                } else {
                    throw new Error('clipboard unavailable');
                }
                if (statusEl) statusEl.textContent = 'Report copied.';
            } catch (_e) {
                if (statusEl) statusEl.textContent = 'Could not copy report.';
            }
        });
    }
}

// Settings modal category navigation. Presentation state only: never persisted,
// never routed. See DESIGN.md "Settings" and the storage rule in AGENTS.md.
const PRKS_SETTINGS_CATEGORIES = ['general', 'reading', 'export', 'backup', 'maintenance', 'diagnostics'];
// Same breakpoint as the `.prks-settings-nav` responsive rule in style.css.
const PRKS_SETTINGS_NARROW_MEDIA_QUERY = '(max-width: 640px)';
let __prksSettingsActiveCategory = 'general';
let __prksSettingsDiagnosticsLoaded = false;

// Keeps the tablist's aria-orientation in sync with its actual visual layout:
// vertical on desktop, horizontal once the nav becomes a horizontal strip.
function prksSyncSettingsNavOrientation() {
    const nav = document.getElementById('prks-settings-nav');
    if (!nav) return;
    const narrow =
        typeof window.matchMedia === 'function' &&
        window.matchMedia(PRKS_SETTINGS_NARROW_MEDIA_QUERY).matches;
    nav.setAttribute('aria-orientation', narrow ? 'horizontal' : 'vertical');
}
window.prksSyncSettingsNavOrientation = prksSyncSettingsNavOrientation;

function prksActivateSettingsCategory(categoryId, options) {
    const opts = options || {};
    const resolved = PRKS_SETTINGS_CATEGORIES.indexOf(categoryId) !== -1 ? categoryId : 'general';
    __prksSettingsActiveCategory = resolved;
    PRKS_SETTINGS_CATEGORIES.forEach((cat) => {
        const tab = document.getElementById('prks-settings-tab-' + cat);
        const panel = document.getElementById('prks-settings-panel-' + cat);
        const active = cat === resolved;
        if (tab) {
            tab.classList.toggle('is-active', active);
            tab.setAttribute('aria-selected', active ? 'true' : 'false');
            tab.tabIndex = active ? 0 : -1;
        }
        if (panel) {
            panel.hidden = !active;
            if (active) panel.removeAttribute('inert');
            else panel.setAttribute('inert', '');
        }
    });
    const activeTab = document.getElementById('prks-settings-tab-' + resolved);
    if (activeTab) {
        if (opts.focusTab) activeTab.focus();
        if (typeof activeTab.scrollIntoView === 'function') {
            activeTab.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        }
    }
    if (resolved === 'diagnostics' && !__prksSettingsDiagnosticsLoaded) {
        __prksSettingsDiagnosticsLoaded = true;
        void prksLoadPerformanceDiagnostics();
        void prksLoadOfflineCacheStatus();
    }
}
window.prksActivateSettingsCategory = prksActivateSettingsCategory;

function prksOpenSettingsToLastCategory() {
    prksActivateSettingsCategory(__prksSettingsActiveCategory);
}
window.prksOpenSettingsToLastCategory = prksOpenSettingsToLastCategory;

function initPrksSettingsCategoryNav() {
    const nav = document.getElementById('prks-settings-nav');
    if (!nav || nav.dataset.bound === '1') return;
    nav.dataset.bound = '1';
    const tabs = Array.prototype.slice.call(nav.querySelectorAll('.prks-settings-nav__item'));
    tabs.forEach((tab) => {
        tab.addEventListener('click', () => {
            prksActivateSettingsCategory(tab.dataset.prksSettingsCategory);
        });
    });
    nav.addEventListener('keydown', (e) => {
        const idx = tabs.indexOf(document.activeElement);
        if (idx === -1) return;
        let nextIdx = null;
        if (e.key === 'ArrowDown' || e.key === 'ArrowRight') nextIdx = (idx + 1) % tabs.length;
        else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') nextIdx = (idx - 1 + tabs.length) % tabs.length;
        else if (e.key === 'Home') nextIdx = 0;
        else if (e.key === 'End') nextIdx = tabs.length - 1;
        if (nextIdx === null) return;
        e.preventDefault();
        const nextTab = tabs[nextIdx];
        prksActivateSettingsCategory(nextTab.dataset.prksSettingsCategory, { focusTab: true });
    });

    prksSyncSettingsNavOrientation();
    if (typeof window.matchMedia === 'function') {
        const mq = window.matchMedia(PRKS_SETTINGS_NARROW_MEDIA_QUERY);
        if (typeof mq.addEventListener === 'function') {
            mq.addEventListener('change', prksSyncSettingsNavOrientation);
        } else if (typeof mq.addListener === 'function') {
            mq.addListener(prksSyncSettingsNavOrientation);
        }
    }
}
window.initPrksSettingsCategoryNav = initPrksSettingsCategoryNav;

function applyTheme(theme) {
    if (theme === 'system') {
        document.documentElement.removeAttribute('data-theme');
    } else {
        document.documentElement.setAttribute('data-theme', theme);
    }
    prksSyncThemeColor();
}

function prksSyncThemeColor() {
    const meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) return;
    const explicit = document.documentElement.getAttribute('data-theme');
    let dark = explicit === 'dark';
    if (explicit !== 'light' && explicit !== 'dark') {
        dark = typeof window.matchMedia === 'function'
            && window.matchMedia('(prefers-color-scheme: dark)').matches;
    }
    meta.setAttribute('content', dark ? '#818cf8' : '#6d6cf7');
}
if (typeof window.matchMedia === 'function') {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onScheme = () => {
        if ((localStorage.getItem('prks-theme') || 'system') === 'system') prksSyncThemeColor();
    };
    if (typeof mq.addEventListener === 'function') mq.addEventListener('change', onScheme);
    else if (typeof mq.addListener === 'function') mq.addListener(onScheme);
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

/**
 * Route-critical read models (Work only, Phase 1) go through the
 * offline-capable wrapper instead of the plain api.js fetcher: a genuine
 * network/server-unreachable failure falls back to the offline store
 * instead of silently becoming "not found", while a real HTTP domain
 * response keeps its normal meaning -- a 404 still renders "not found"
 * exactly like before, and every other domain failure (400/403/409/500,
 * invalid JSON) propagates to the caller so the route's usual error
 * handling (prksRenderRouteError) takes over instead of a wrong "not
 * found" page. See AGENTS.md "Offline / PWA".
 */
async function prksOfflineDetailFetch(kind, id, path, signal, options) {
    if (typeof prksOfflineReadEntity !== 'function') {
        return { value: null, source: 'unavailable', cachedAt: null };
    }
    const opts = options && typeof options === 'object' ? options : {};
    return await prksOfflineReadEntity(kind, id, path, {
        signal: signal,
        domain: opts.domain,
        validate: opts.validate,
    });
}

/**
 * List counterpart of prksOfflineDetailFetch, so an offline-capable index route
 * stays symmetrical with the Work detail route. Deliberately thin: fetch/retry/
 * connectivity policy and the network-vs-domain distinction stay owned by the
 * offline runtime, never re-implemented here.
 */
/** Loads one browse projection through the ordinary offline read-through. */
async function prksOfflineBrowseFetch(listKey, domain, path, validate, signal) {
    return await prksOfflineListFetch(listKey, path, signal, {
        domain: domain, validate: validate,
    });
}

async function prksOfflineListFetch(listKey, path, signal, options) {
    if (typeof prksOfflineReadList !== 'function') {
        return { value: null, source: 'unavailable', cachedAt: null };
    }
    const opts = options && typeof options === 'object' ? options : {};
    return await prksOfflineReadList(listKey, path, {
        signal: signal,
        domain: opts.domain,
        validate: opts.validate,
    });
}

// Server projection bounds; keep aligned with backend/research_graph.py.
const PRKS_RESEARCH_GRAPH_MAX_NODES = 2500;
const PRKS_RESEARCH_GRAPH_MAX_EDGES = 7500;
const PRKS_RESEARCH_GRAPH_ROUTES = Object.freeze({
    concept: 'concepts', position: 'positions', argument: 'arguments', work: 'works', person: 'people',
});
const PRKS_RESEARCH_GRAPH_ENDPOINTS = Object.freeze({
    concept_parent: ['concept', 'concept'], argument_position: ['argument', 'position'],
    argument_argument: ['argument', 'argument'], argument_source: ['argument', 'work'],
    mentions_concept: ['work', 'concept'], mentions_argument: ['work', 'argument'],
    work_author: ['person', 'work'],
});

/** Validate both authoritative and cached snapshots before graph code sees them. */
function prksIsResearchGraphSnapshot(value, includePeople) {
    const object = v => !!v && typeof v === 'object' && !Array.isArray(v);
    const nonblank = v => typeof v === 'string' && v.trim().length > 0;
    const optionalString = (v, key) => !Object.prototype.hasOwnProperty.call(v, key) || typeof v[key] === 'string';
    if (!object(value) || !Array.isArray(value.nodes) || !Array.isArray(value.edges) || !object(value.meta)) return false;
    const { nodes, edges, meta } = value;
    if (nodes.length > PRKS_RESEARCH_GRAPH_MAX_NODES || edges.length > PRKS_RESEARCH_GRAPH_MAX_EDGES ||
        !Number.isInteger(meta.node_count) || meta.node_count < 0 || meta.node_count !== nodes.length ||
        !Number.isInteger(meta.edge_count) || meta.edge_count < 0 || meta.edge_count !== edges.length ||
        typeof meta.derived_note_edges_available !== 'boolean' ||
        typeof meta.people_included !== 'boolean' || meta.people_included !== includePeople) return false;
    const nodeTypes = new Map();
    for (const n of nodes) {
        if (!object(n) || !nonblank(n.id) || !nonblank(n.record_id) || typeof n.type !== 'string' ||
            !Object.prototype.hasOwnProperty.call(PRKS_RESEARCH_GRAPH_ROUTES, n.type) ||
            typeof n.label !== 'string' || !nonblank(n.route) ||
            !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(n.record_id) ||
            n.id !== n.type + ':' + n.record_id ||
            n.route !== '#/' + PRKS_RESEARCH_GRAPH_ROUTES[n.type] + '/' + n.record_id ||
            nodeTypes.has(n.id) || (!includePeople && n.type === 'person')) return false;
        if (n.type === 'argument' && n.kind !== 'argument' && n.kind !== 'stance') return false;
        if (n.type === 'work' && !optionalString(n, 'doc_type')) return false;
        nodeTypes.set(n.id, n.type);
    }
    const edgeIds = new Set();
    for (const e of edges) {
        if (!object(e) || !nonblank(e.id) || !nonblank(e.source) || !nonblank(e.target) || typeof e.type !== 'string' ||
            !Object.prototype.hasOwnProperty.call(PRKS_RESEARCH_GRAPH_ENDPOINTS, e.type) ||
            e.id !== e.type + ':' + e.source + '>' + e.target || edgeIds.has(e.id) ||
            (!includePeople && e.type === 'work_author')) return false;
        const pair = PRKS_RESEARCH_GRAPH_ENDPOINTS[e.type];
        if (nodeTypes.get(e.source) !== pair[0] || nodeTypes.get(e.target) !== pair[1]) return false;
        if ((e.type === 'argument_position' || e.type === 'argument_argument') &&
            (!optionalString(e, 'verdict_id') || !optionalString(e, 'verdict_label'))) return false;
        if (e.type === 'argument_source' && !optionalString(e, 'pages')) return false;
        // Canonical aggregates count actual mentions, so count is required and positive.
        if ((e.type === 'mentions_concept' || e.type === 'mentions_argument') &&
            (!Number.isInteger(e.count) || e.count < 1)) return false;
        edgeIds.add(e.id);
    }
    return true;
}

async function prksOfflineResearchGraphFetch(includePeople, signal) {
    const kind = includePeople ? PRKS_OFFLINE_DOMAIN_RESEARCH_GRAPH_PEOPLE : PRKS_OFFLINE_DOMAIN_RESEARCH_GRAPH_CORE;
    const validate = value => prksIsResearchGraphSnapshot(value, includePeople);
    let result;
    try {
        result = await prksOfflineDetailFetch(kind, 'snapshot',
            '/api/research-graph' + (includePeople ? '?people=1' : ''), signal, { domain: kind, validate });
    } catch (err) {
        if (err && err.status === 413) err.code = 'graph_too_large';
        throw err;
    }
    if (result.source !== 'unavailable' && !validate(result.value)) {
        if (result.source === 'server') throw new Error('Received an unexpected Research Graph response.');
        if (typeof prksOfflineInvalidateEntity === 'function') void prksOfflineInvalidateEntity(kind, 'snapshot');
        return { snapshot: null, source: 'unavailable', cachedAt: null };
    }
    return { snapshot: result.value, source: result.source, cachedAt: result.cachedAt };
}

const PRKS_CONCEPTS_LIST_KEY =
    typeof PRKS_OFFLINE_CONCEPTS_LIST_KEY === 'string' ? PRKS_OFFLINE_CONCEPTS_LIST_KEY : 'concepts:index';
const PRKS_CONCEPTS_DOMAIN =
    typeof PRKS_OFFLINE_DOMAIN_CONCEPTS === 'string' ? PRKS_OFFLINE_DOMAIN_CONCEPTS : 'concepts';

/**
 * Shape guarantees the old fetchConcepts()/fetchConcept() helpers provided,
 * kept in one place. They are handed to the offline runtime as its `validate`
 * callback so an authoritative response is accepted BEFORE it can be published
 * to the cache -- a reachable server answering 200 with the wrong body must
 * never overwrite a previously good snapshot -- and reused below to judge a
 * cached value.
 */
function prksIsConceptIndexShape(value) {
    return Array.isArray(value);
}

function prksIsConceptShape(value, conceptId) {
    return !!(
        value &&
        typeof value === 'object' &&
        !Array.isArray(value) &&
        value.id != null &&
        String(value.id) === String(conceptId)
    );
}

/**
 * A wrong-shaped *server* body is a route error (never a silent empty list); a
 * wrong-shaped *cached* body means the cache is unusable -- "no Concept index
 * was cached", never "No Concepts yet." -- and is discarded best-effort.
 * Returns the array, or null when no usable index exists.
 */
function prksResolveOfflineConceptIndex(offlineResult) {
    if (!offlineResult || offlineResult.source === 'unavailable') return null;
    if (prksIsConceptIndexShape(offlineResult.value)) return offlineResult.value;
    if (offlineResult.source === 'server') {
        // Normally unreachable: the runtime rejects a bad authoritative shape
        // before it ever returns (or caches) one. Kept as the backstop for a
        // runtime without validator support.
        throw new Error('Received an unexpected Concepts response.');
    }
    if (typeof prksOfflineInvalidateList === 'function') {
        void prksOfflineInvalidateList(PRKS_CONCEPTS_LIST_KEY);
    }
    return null;
}

/**
 * Same split for one Concept: a bad *server* shape is a route error; a bad
 * *cached* shape makes the cache unavailable rather than a false "Concept not
 * found."
 */
function prksResolveOfflineConcept(offlineResult, conceptId) {
    if (!offlineResult || offlineResult.source === 'unavailable') {
        return { concept: null, unavailable: true };
    }
    const value = offlineResult.value;
    if (value == null) return { concept: null, unavailable: false };
    if (prksIsConceptShape(value, conceptId)) return { concept: value, unavailable: false };
    if (offlineResult.source === 'server') {
        throw new Error('Received an unexpected Concept response.');
    }
    if (typeof prksOfflineInvalidateEntity === 'function') {
        void prksOfflineInvalidateEntity('concept', conceptId);
    }
    return { concept: null, unavailable: true };
}

const PRKS_POSITIONS_LIST_KEY =
    typeof PRKS_OFFLINE_POSITIONS_LIST_KEY === 'string' ? PRKS_OFFLINE_POSITIONS_LIST_KEY : 'positions:index';
const PRKS_POSITIONS_DOMAIN =
    typeof PRKS_OFFLINE_DOMAIN_POSITIONS === 'string' ? PRKS_OFFLINE_DOMAIN_POSITIONS : 'positions';

/** A row that can actually be linked to: an object carrying a non-blank id. */
function prksHasUsableRowId(row) {
    return !!(
        row &&
        typeof row === 'object' &&
        !Array.isArray(row) &&
        row.id != null &&
        String(row.id).trim()
    );
}

/**
 * Shape guarantees the old fetchPositions()/fetchPosition() helpers provided,
 * applied as the runtime's `validate` callback so an authoritative response is
 * accepted BEFORE it can be published to the cache, and reused to judge a
 * cached value. Only what the server actually promises is required: the detail
 * read model always carries an `arguments` array, while the per-Argument
 * display fields (kind, verdict_label, ...) stay optional.
 */
function prksIsPositionIndexShape(value) {
    if (!Array.isArray(value)) return false;
    return value.every(prksHasUsableRowId);
}

function prksIsPositionShape(value, positionId) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    if (value.id == null || String(value.id) !== String(positionId)) return false;
    if (!Array.isArray(value.arguments)) return false;
    // Every embedded summary becomes an #/arguments/:id link, so each needs a
    // usable id; the display fields (kind, verdict_label, ...) stay optional
    // because the server does not promise them.
    return value.arguments.every(prksHasUsableRowId);
}

/** Same server/cache split as Concepts: route error vs. unusable cache. */
function prksResolveOfflinePositionIndex(offlineResult) {
    if (!offlineResult || offlineResult.source === 'unavailable') return null;
    if (prksIsPositionIndexShape(offlineResult.value)) return offlineResult.value;
    if (offlineResult.source === 'server') {
        // Normally unreachable: the runtime rejects a bad authoritative shape
        // before it ever returns (or caches) one. Kept as the backstop for a
        // runtime without validator support.
        throw new Error('Received an unexpected Positions response.');
    }
    if (typeof prksOfflineInvalidateList === 'function') {
        void prksOfflineInvalidateList(PRKS_POSITIONS_LIST_KEY);
    }
    return null;
}

function prksResolveOfflinePosition(offlineResult, positionId) {
    if (!offlineResult || offlineResult.source === 'unavailable') {
        return { position: null, unavailable: true };
    }
    const value = offlineResult.value;
    if (value == null) return { position: null, unavailable: false };
    if (prksIsPositionShape(value, positionId)) return { position: value, unavailable: false };
    if (offlineResult.source === 'server') {
        throw new Error('Received an unexpected Position response.');
    }
    if (typeof prksOfflineInvalidateEntity === 'function') {
        void prksOfflineInvalidateEntity('position', positionId);
    }
    return { position: null, unavailable: true };
}

const PRKS_ARGUMENTS_LIST_KEY =
    typeof PRKS_OFFLINE_ARGUMENTS_LIST_KEY === 'string' ? PRKS_OFFLINE_ARGUMENTS_LIST_KEY : 'arguments:index';
const PRKS_ARGUMENTS_DOMAIN =
    typeof PRKS_OFFLINE_DOMAIN_ARGUMENTS === 'string' ? PRKS_OFFLINE_DOMAIN_ARGUMENTS : 'arguments';

function prksIsArgumentKind(value) {
    return value === 'argument' || value === 'stance';
}

/**
 * Shape guarantees for the Argument/Stance read models, applied as the
 * runtime's `validate` callback so an authoritative response is accepted BEFORE
 * it can be published to the cache, and reused to judge a cached value.
 *
 * The rule throughout: require what a link or a filter is actually built from
 * (usable ids, the target's `type`, a valid `kind`, the collections the server
 * always sends), and never require a presentational field the server contract
 * leaves optional (verdict_label, work_title, author names, pages, ...).
 */
function prksIsArgumentTargetShape(row) {
    if (!prksHasUsableRowId(row)) return false;
    // `type` decides whether the row links to #/positions/:id or #/arguments/:id.
    return row.type === 'position' || row.type === 'argument';
}

/**
 * One Author row of a source Work. `_work_authors()` canonically supplies the
 * Person id, and the renderer walks every row to build its author label, so a
 * non-object row there is a crash rather than a cosmetic gap. The display
 * fields (first_name, last_name, credit_name) stay optional: any of them may
 * legitimately be empty or absent.
 */
function prksIsArgumentAuthorShape(row) {
    return prksHasUsableRowId(row);
}

function prksIsArgumentSourceShape(row) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) return false;
    if (row.work_id == null || !String(row.work_id).trim()) return false;
    return Array.isArray(row.authors) && row.authors.every(prksIsArgumentAuthorShape);
}

function prksIsArgumentMentionShape(row) {
    return !!(
        row &&
        typeof row === 'object' &&
        !Array.isArray(row) &&
        row.work_id != null &&
        String(row.work_id).trim()
    );
}

function prksIsArgumentResponseShape(row) {
    if (!prksHasUsableRowId(row)) return false;
    return row.kind == null || prksIsArgumentKind(row.kind);
}

/** One row of the complete /api/arguments collection. */
function prksIsArgumentIndexRowShape(row) {
    if (!prksHasUsableRowId(row)) return false;
    // The route filters All/Arguments/Stances locally off this field.
    if (!prksIsArgumentKind(row.kind)) return false;
    if (!Array.isArray(row.targets) || !row.targets.every(prksIsArgumentTargetShape)) return false;
    return Array.isArray(row.sources) && row.sources.every(prksIsArgumentSourceShape);
}

function prksIsArgumentIndexShape(value) {
    if (!Array.isArray(value)) return false;
    return value.every(prksIsArgumentIndexRowShape);
}

function prksIsArgumentShape(value, argumentId) {
    if (!prksHasUsableRowId(value)) return false;
    if (String(value.id) !== String(argumentId)) return false;
    if (!prksIsArgumentKind(value.kind)) return false;
    if (!Array.isArray(value.targets) || !value.targets.every(prksIsArgumentTargetShape)) return false;
    if (!Array.isArray(value.sources) || !value.sources.every(prksIsArgumentSourceShape)) return false;
    if (!Array.isArray(value.responses) || !value.responses.every(prksIsArgumentResponseShape)) return false;
    if (!Array.isArray(value.mentions) || !value.mentions.every(prksIsArgumentMentionShape)) return false;
    return Array.isArray(value.verdicts) && value.verdicts.every(prksHasUsableRowId);
}

/**
 * The complete unfiltered collection is what gets cached, under one key. A
 * `?kind=` route then selects its subset locally, so visiting the Stances tab
 * online still warms the cache for All and Arguments, and switching tabs
 * offline needs no separately cached server-filtered list.
 */
function prksFilterArgumentsByKind(items, filterKind) {
    const list = Array.isArray(items) ? items : [];
    if (filterKind !== 'argument' && filterKind !== 'stance') return list.slice();
    return list.filter(function (row) {
        return row && row.kind === filterKind;
    });
}

/** Same server/cache split as Concepts and Positions: route error vs. unusable cache. */
function prksResolveOfflineArgumentIndex(offlineResult) {
    if (!offlineResult || offlineResult.source === 'unavailable') return null;
    if (prksIsArgumentIndexShape(offlineResult.value)) return offlineResult.value;
    if (offlineResult.source === 'server') {
        // Normally unreachable: the runtime rejects a bad authoritative shape
        // before it ever returns (or caches) one. Kept as the backstop for a
        // runtime without validator support.
        throw new Error('Received an unexpected Arguments response.');
    }
    if (typeof prksOfflineInvalidateList === 'function') {
        void prksOfflineInvalidateList(PRKS_ARGUMENTS_LIST_KEY);
    }
    return null;
}

function prksResolveOfflineArgument(offlineResult, argumentId) {
    if (!offlineResult || offlineResult.source === 'unavailable') {
        return { argument: null, unavailable: true };
    }
    const value = offlineResult.value;
    if (value == null) return { argument: null, unavailable: false };
    if (prksIsArgumentShape(value, argumentId)) return { argument: value, unavailable: false };
    if (offlineResult.source === 'server') {
        throw new Error('Received an unexpected Argument response.');
    }
    if (typeof prksOfflineInvalidateEntity === 'function') {
        void prksOfflineInvalidateEntity('argument', argumentId);
    }
    return { argument: null, unavailable: true };
}

const PRKS_PEOPLE_LIST_KEY =
    typeof PRKS_OFFLINE_PEOPLE_LIST_KEY === 'string' ? PRKS_OFFLINE_PEOPLE_LIST_KEY : 'people:index';
const PRKS_PEOPLE_DOMAIN =
    typeof PRKS_OFFLINE_DOMAIN_PEOPLE === 'string' ? PRKS_OFFLINE_DOMAIN_PEOPLE : 'people';

/**
 * Shape guarantees for the People read models. As everywhere else: require what
 * the renderer and its links are actually built from -- usable ids on every
 * nested row that becomes a route -- and never a display field the server
 * leaves free to be blank. A Person legitimately has no first name, no
 * biography, no dates and no links.
 */
/**
 * Both `#/people` and `#/people/role/:role` read the COMPLETE collection under
 * one key. Kept as a single helper so a future People route cannot accidentally
 * introduce a second, role-filtered cache.
 */
async function prksOfflinePeopleFetch(signal) {
    return await prksOfflineListFetch(PRKS_PEOPLE_LIST_KEY, '/api/persons', signal, {
        domain: PRKS_PEOPLE_DOMAIN,
        validate: prksIsPeopleIndexShape,
    });
}

function prksIsPersonGroupRowShape(row) {
    // The id becomes #/people/groups/:id.
    return prksHasUsableRowId(row);
}

function prksIsOptionalString(value) {
    return value == null || typeof value === 'string';
}

const PRKS_PERSON_OPTIONAL_STRING_FIELDS = [
    'first_name',
    'last_name',
    'aliases',
    'about',
    'image_url',
    'link_wikipedia',
    'link_stanford_encyclopedia',
    'link_iep',
    'links_other',
    'birth_date',
    'death_date',
];

function prksIsPersonScalarShape(person) {
    if (!person || typeof person !== 'object' || Array.isArray(person)) return false;
    return PRKS_PERSON_OPTIONAL_STRING_FIELDS.every((field) => prksIsOptionalString(person[field]));
}

function prksIsPersonWorkRowShape(row) {
    // The id becomes #/works/:id. Most card display fields safely coerce their
    // values, but the year fallback directly applies string operations.
    if (!prksHasUsableRowId(row)) return false;
    return prksIsOptionalString(row.year) && prksIsOptionalString(row.published_date);
}

function prksIsAssignedRoleShape(value) {
    // Canonical data can carry roles beyond the navigable filter set (e.g.
    // 'Mentioned'), so accept any string rather than an allow-list.
    return typeof value === 'string';
}

function prksIsPeopleIndexRowShape(row) {
    if (!prksHasUsableRowId(row)) return false;
    if (!prksIsPersonScalarShape(row)) return false;
    if (!Array.isArray(row.assigned_roles) || !row.assigned_roles.every(prksIsAssignedRoleShape)) {
        return false;
    }
    return Array.isArray(row.groups) && row.groups.every(prksIsPersonGroupRowShape);
}

function prksIsPeopleIndexShape(value) {
    if (!Array.isArray(value)) return false;
    return value.every(prksIsPeopleIndexRowShape);
}

function prksIsPersonShape(value, personId) {
    if (!prksHasUsableRowId(value)) return false;
    if (String(value.id) !== String(personId)) return false;
    if (!prksIsPersonScalarShape(value)) return false;
    if (!Array.isArray(value.works) || !value.works.every(prksIsPersonWorkRowShape)) return false;
    return Array.isArray(value.groups) && value.groups.every(prksIsPersonGroupRowShape);
}

const PRKS_PERSON_GROUPS_LIST_KEY = 'person-groups:index';
const PRKS_PERSON_GROUPS_DOMAIN = 'person-groups';

function prksIsGroupCount(value) {
    return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}

function prksIsPersonGroupSummaryShape(group) {
    return prksHasUsableRowId(group) &&
        prksIsOptionalString(group.name) && prksIsOptionalString(group.description) &&
        (group.parent_id === null ||
            (typeof group.parent_id === 'string' && !!group.parent_id.trim())) &&
        prksIsGroupCount(group.member_count);
}

function prksIsPersonGroupsIndexShape(value) {
    return Array.isArray(value) && value.every((group) =>
        prksIsPersonGroupSummaryShape(group) && prksIsGroupCount(group.child_count));
}

function prksIsPersonGroupShape(value, groupId) {
    if (!prksIsPersonGroupSummaryShape(value) || String(value.id) !== String(groupId)) return false;
    if (value.parent !== null && !(prksHasUsableRowId(value.parent) &&
        prksIsOptionalString(value.parent.name))) return false;
    return Array.isArray(value.children) && value.children.every(prksIsPersonGroupSummaryShape) &&
        Array.isArray(value.members) && value.members.every(prksIsPeopleIndexRowShape);
}

function prksResolveOfflinePersonGroupsIndex(result) {
    if (!result || result.source === 'unavailable') return null;
    if (prksIsPersonGroupsIndexShape(result.value)) return result.value;
    if (result.source === 'server') throw new Error('Received an unexpected Person Groups response.');
    if (typeof prksOfflineInvalidateList === 'function') void prksOfflineInvalidateList(PRKS_PERSON_GROUPS_LIST_KEY);
    return null;
}

function prksResolveOfflinePersonGroup(result, groupId) {
    if (!result || result.source === 'unavailable') return { group: null, unavailable: true };
    if (result.source === 'server' && result.value === null) return { group: null, unavailable: false };
    if (prksIsPersonGroupShape(result.value, groupId)) return { group: result.value, unavailable: false };
    if (result.source === 'server') throw new Error('Received an unexpected Person Group response.');
    if (typeof prksOfflineInvalidateEntity === 'function') void prksOfflineInvalidateEntity('person-group', groupId);
    return { group: null, unavailable: true };
}

/* ---------------------------------------------------------------------------
 * Browse projections. Three independent caches, NOT one catalog: a single one
 * carrying `last_opened_at` would let merely opening a Work invalidate the
 * Progress/Types/Recently-added caches too. Each is a compact server
 * projection -- not the full Work summary -- so these validators protect the
 * browse card contract and each route's own extra field, nothing more.
 * ------------------------------------------------------------------------ */
const PRKS_WORKS_BROWSE_LIST_KEY = 'works-browse:index';
const PRKS_WORKS_BROWSE_DOMAIN = 'works-browse';
const PRKS_RECENT_LIST_KEY = 'recent:index';
const PRKS_RECENT_DOMAIN = 'recent';
const PRKS_RECENTLY_ADDED_LIST_KEY = 'recently-added:index';
const PRKS_RECENTLY_ADDED_DOMAIN = 'recently-added';

/** Non-negative integer, or absent/null. Used for byte and page counts. */
function prksIsOptionalNonNegativeInteger(value) {
    return value == null ||
        (typeof value === 'number' && Number.isFinite(value) &&
         Number.isInteger(value) && value >= 0);
}

/** `folder_id` must be PRESENT on a Recently-added row: the projection always
 *  selects it and spells "no folder" as an explicit null, so an absent field
 *  would silently read as unfiled. Same rule as Folder `parent_id`. */
function prksIsBrowseFolderId(row) {
    if (!Object.prototype.hasOwnProperty.call(row, 'folder_id')) return false;
    const value = row.folder_id;
    return value === null || (typeof value === 'string' && !!value.trim());
}

/* Shared by all three: exactly what prksWorkCardHtml() dereferences, plus the
 * source fields prksInferWorkSourceKind() reads. `file_size_bytes` is fed to
 * Number(), so a wrong type there renders "NaN MB" from cache. */
function prksIsBrowseCardRowShape(row) {
    if (!prksHasUsableRowId(row)) return false;
    return prksIsOptionalString(row.title) && prksIsOptionalString(row.status) &&
        prksIsOptionalString(row.doc_type) && prksIsOptionalString(row.file_path) &&
        prksIsOptionalString(row.source_kind) && prksIsOptionalString(row.source_url) &&
        prksIsOptionalString(row.thumb_url) && prksIsOptionalString(row.author_text) &&
        prksIsOptionalString(row.year) && prksIsOptionalString(row.published_date) &&
        prksIsOptionalString(row.linked_authors) && prksIsOptionalString(row.primary_author) &&
        prksIsOptionalString(row.primary_editor) &&
        prksIsOptionalNonNegativeInteger(row.thumb_page) &&
        prksIsOptionalNonNegativeInteger(row.file_size_bytes);
}

/* #/progress renders `abstract_excerpt` under each card, so it must be present
 * and a string -- the server always projects it (COALESCE + SUBSTR). */
function prksIsWorksBrowseRowShape(row) {
    if (!prksIsBrowseCardRowShape(row)) return false;
    if (!Object.prototype.hasOwnProperty.call(row, 'abstract_excerpt')) return false;
    return typeof row.abstract_excerpt === 'string';
}

function prksIsWorksBrowseIndexShape(value) {
    return Array.isArray(value) && value.every(prksIsWorksBrowseRowShape);
}

/* #/recent renders `Last opened: <date>` from this field and the canonical
 * projection selects only rows where it is NOT NULL, so a row without a usable
 * one is a malformed payload rather than a sparse record. */
function prksIsRecentRowShape(row) {
    return prksIsBrowseCardRowShape(row) &&
        typeof row.last_opened_at === 'string' && !!row.last_opened_at.trim();
}

function prksIsRecentIndexShape(value) {
    return Array.isArray(value) && value.every(prksIsRecentRowShape);
}

/* Recently added renders `Added <date>` from created_at and filters locally
 * over publisher and the folder title it resolves through folder_id. */
function prksIsRecentlyAddedRowShape(row) {
    return prksIsBrowseCardRowShape(row) &&
        typeof row.created_at === 'string' && !!row.created_at.trim() &&
        prksIsOptionalString(row.publisher) && prksIsBrowseFolderId(row);
}

function prksIsRecentlyAddedIndexShape(value) {
    return Array.isArray(value) && value.every(prksIsRecentlyAddedRowShape);
}

/** The stable catalog behind #/progress, #/types and #/types/:type. */
async function prksOfflineWorksBrowseFetch(signal) {
    return await prksOfflineBrowseFetch(
        PRKS_WORKS_BROWSE_LIST_KEY, PRKS_WORKS_BROWSE_DOMAIN,
        '/api/works?projection=browse', prksIsWorksBrowseIndexShape, signal
    );
}

function prksResolveOfflineWorksBrowse(result) {
    return prksResolveOfflineBrowseList(
        result, PRKS_WORKS_BROWSE_LIST_KEY, prksIsWorksBrowseIndexShape, 'Works browse'
    );
}

/** Home -> Recently added. Its own snapshot for the same reason as Recent. */
async function prksOfflineRecentlyAddedFetch(signal) {
    return await prksOfflineBrowseFetch(
        PRKS_RECENTLY_ADDED_LIST_KEY, PRKS_RECENTLY_ADDED_DOMAIN, '/api/recently-added',
        prksIsRecentlyAddedIndexShape, signal
    );
}

function prksResolveOfflineRecentlyAdded(result) {
    return prksResolveOfflineBrowseList(
        result, PRKS_RECENTLY_ADDED_LIST_KEY, prksIsRecentlyAddedIndexShape, 'Recently added'
    );
}

/** Shared resolver: null means "no usable snapshot", never "empty library". */
function prksResolveOfflineBrowseList(result, listKey, validate, label) {
    if (!result || result.source === 'unavailable') return null;
    if (validate(result.value)) return result.value;
    if (result.source === 'server') throw new Error('Received an unexpected ' + label + ' response.');
    if (typeof prksOfflineInvalidateList === 'function') void prksOfflineInvalidateList(listKey);
    return null;
}

const PRKS_FOLDERS_LIST_KEY = 'folders:index';
const PRKS_FOLDERS_DOMAIN = 'folders';

/* Folder validators gate cache publication, so they protect exactly what the
 * Folder renderers dereference. The index feeds the hierarchy tree (title,
 * parent_id, work_count, child_count); the detail additionally feeds whole
 * Work cards through prksWorkCardHtml() and the right-panel tag list. Backend
 * hierarchy rules (cycles, unique titles, count correctness) stay canonical --
 * this only stops a malformed payload from being cached or rendered. See
 * AGENTS.md, "Offline coherence domains". */
function prksIsFolderCount(value) {
    return typeof value === 'number' && Number.isFinite(value) && Number.isInteger(value) && value >= 0;
}

/* `parent_id` must be PRESENT: `get_all_folders()` selects `f.*`, `get_folder()`
 * selects `*`, and the children query names the column explicitly, so the
 * canonical API always carries it and represents a root folder as an explicit
 * `null`. Treating an absent field as root-like would let a truncated payload
 * silently reparent someone's hierarchy to the top level. The `parent` summary
 * is deliberately exempt -- it selects only id/title. */
function prksIsFolderParentId(row) {
    if (!row || typeof row !== 'object') return false;
    if (!Object.prototype.hasOwnProperty.call(row, 'parent_id')) return false;
    const value = row.parent_id;
    return value === null || (typeof value === 'string' && !!value.trim());
}

function prksIsFolderSummaryShape(row) {
    return prksHasUsableRowId(row) &&
        prksIsOptionalString(row.title) && prksIsOptionalString(row.description) &&
        prksIsFolderParentId(row) &&
        prksIsFolderCount(row.work_count) && prksIsFolderCount(row.child_count);
}

function prksIsFoldersIndexShape(value) {
    return Array.isArray(value) && value.every(prksIsFolderSummaryShape);
}

/* A Folder detail's Work rows are rendered by prksWorkCardHtml(), which reads
 * these fields. Most coerce safely, but `year`/`published_date` take direct
 * string operations and `file_size_bytes` is fed to Number() -- so a wrong
 * type there would render "NaN MB" from cache. Validated as the card's row
 * contract rather than the whole Work detail schema. */
function prksIsWorkCardRowShape(row) {
    if (!prksHasUsableRowId(row)) return false;
    return prksIsOptionalString(row.title) && prksIsOptionalString(row.year) &&
        prksIsOptionalString(row.published_date) && prksIsOptionalString(row.status) &&
        prksIsOptionalString(row.doc_type) && prksIsOptionalString(row.file_path) &&
        prksIsOptionalString(row.author_text) && prksIsOptionalString(row.linked_authors) &&
        prksIsOptionalString(row.primary_author) && prksIsOptionalString(row.primary_editor) &&
        prksIsOptionalString(row.thumb_url) &&
        (row.file_size_bytes == null || prksIsFolderCount(row.file_size_bytes));
}

/** Folder tag chips render id + name; unused columns are not the cache's business. */
function prksIsFolderTagRowShape(row) {
    return prksHasUsableRowId(row) && prksIsOptionalString(row.name);
}

function prksIsFolderShape(value, folderId) {
    if (!prksHasUsableRowId(value) || String(value.id) !== String(folderId)) return false;
    if (!prksIsOptionalString(value.title) || !prksIsOptionalString(value.description) ||
        !prksIsOptionalString(value.private_notes) || !prksIsFolderParentId(value)) return false;
    // `parent` is null for a root folder; when present it becomes a link.
    if (value.parent !== null && value.parent !== undefined) {
        if (!prksHasUsableRowId(value.parent) || !prksIsOptionalString(value.parent.title)) return false;
    }
    if (!Array.isArray(value.children) || !value.children.every(prksIsFolderSummaryShape)) return false;
    if (!Array.isArray(value.works) || !value.works.every(prksIsWorkCardRowShape)) return false;
    return Array.isArray(value.tags) && value.tags.every(prksIsFolderTagRowShape);
}

function prksResolveOfflineFoldersIndex(result) {
    if (!result || result.source === 'unavailable') return null;
    if (prksIsFoldersIndexShape(result.value)) return result.value;
    if (result.source === 'server') throw new Error('Received an unexpected Folders response.');
    if (typeof prksOfflineInvalidateList === 'function') void prksOfflineInvalidateList(PRKS_FOLDERS_LIST_KEY);
    return null;
}

function prksResolveOfflineFolder(result, folderId) {
    if (!result || result.source === 'unavailable') return { folder: null, unavailable: true };
    if (result.source === 'server' && result.value === null) return { folder: null, unavailable: false };
    if (prksIsFolderShape(result.value, folderId)) return { folder: result.value, unavailable: false };
    if (result.source === 'server') throw new Error('Received an unexpected Folder response.');
    if (typeof prksOfflineInvalidateEntity === 'function') void prksOfflineInvalidateEntity('folder', folderId);
    return { folder: null, unavailable: true };
}

const PRKS_PLAYLISTS_LIST_KEY = 'playlists:index';
const PRKS_PLAYLISTS_DOMAIN = 'playlists';

/* Playlist validators gate cache publication, so they protect exactly what the
 * Playlist renderers dereference -- not the whole Work summary the detail
 * endpoint happens to join in. `renderPlaylistsIndex()` uses id/title/
 * item_count; `renderPlaylistDetail()` and the right-panel editor use
 * id/title/description/original_url plus each item's id (-> #/works/:id),
 * title, author_text and published_date. See AGENTS.md, "Offline coherence
 * domains". */
function prksIsPlaylistItemCount(value) {
    return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}

function prksIsPlaylistScalarShape(row) {
    return prksIsOptionalString(row.title) && prksIsOptionalString(row.description) &&
        prksIsOptionalString(row.original_url);
}

function prksIsPlaylistIndexRowShape(row) {
    return prksHasUsableRowId(row) && prksIsPlaylistScalarShape(row) &&
        prksIsPlaylistItemCount(row.item_count);
}

function prksIsPlaylistsIndexShape(value) {
    return Array.isArray(value) && value.every(prksIsPlaylistIndexRowShape);
}

/* `position` is NOT NULL in the schema and always selected by get_playlist(),
 * so a row without a usable one is a malformed payload rather than a sparse
 * record -- even though the renderer takes its order from the array itself. */
function prksIsPlaylistItemShape(row) {
    return prksHasUsableRowId(row) &&
        prksIsOptionalString(row.title) && prksIsOptionalString(row.author_text) &&
        prksIsOptionalString(row.published_date) &&
        typeof row.position === 'number' && Number.isFinite(row.position) &&
        row.position >= 0 && Number.isInteger(row.position);
}

function prksIsPlaylistShape(value, playlistId) {
    if (!prksHasUsableRowId(value) || String(value.id) !== String(playlistId)) return false;
    if (!prksIsPlaylistScalarShape(value)) return false;
    return Array.isArray(value.items) && value.items.every(prksIsPlaylistItemShape);
}

function prksResolveOfflinePlaylistsIndex(result) {
    if (!result || result.source === 'unavailable') return null;
    if (prksIsPlaylistsIndexShape(result.value)) return result.value;
    if (result.source === 'server') throw new Error('Received an unexpected Playlists response.');
    if (typeof prksOfflineInvalidateList === 'function') void prksOfflineInvalidateList(PRKS_PLAYLISTS_LIST_KEY);
    return null;
}

function prksResolveOfflinePlaylist(result, playlistId) {
    if (!result || result.source === 'unavailable') return { playlist: null, unavailable: true };
    if (result.source === 'server' && result.value === null) return { playlist: null, unavailable: false };
    if (prksIsPlaylistShape(result.value, playlistId)) return { playlist: result.value, unavailable: false };
    if (result.source === 'server') throw new Error('Received an unexpected Playlist response.');
    if (typeof prksOfflineInvalidateEntity === 'function') void prksOfflineInvalidateEntity('playlist', playlistId);
    return { playlist: null, unavailable: true };
}

/**
 * The complete People collection is cached under one key. Every role-filtered
 * view (`#/people/role/:role`) is a local projection of it, so visiting one
 * role view online warms every other view and the unfiltered list too.
 */
function prksResolveOfflinePeopleIndex(offlineResult) {
    if (!offlineResult || offlineResult.source === 'unavailable') return null;
    if (prksIsPeopleIndexShape(offlineResult.value)) return offlineResult.value;
    if (offlineResult.source === 'server') {
        // Normally unreachable: the runtime rejects a bad authoritative shape
        // before it ever returns (or caches) one. Kept as the backstop for a
        // runtime without validator support.
        throw new Error('Received an unexpected People response.');
    }
    if (typeof prksOfflineInvalidateList === 'function') {
        void prksOfflineInvalidateList(PRKS_PEOPLE_LIST_KEY);
    }
    return null;
}

function prksResolveOfflinePerson(offlineResult, personId) {
    if (!offlineResult || offlineResult.source === 'unavailable') {
        return { person: null, unavailable: true };
    }
    const value = offlineResult.value;
    if (value == null) return { person: null, unavailable: false };
    if (prksIsPersonShape(value, personId)) return { person: value, unavailable: false };
    if (offlineResult.source === 'server') {
        throw new Error('Received an unexpected Person response.');
    }
    if (typeof prksOfflineInvalidateEntity === 'function') {
        void prksOfflineInvalidateEntity('person', personId);
    }
    return { person: null, unavailable: true };
}

function prksOfflineProvenanceBannerHtml(offlineResult) {
    if (!offlineResult || offlineResult.source !== 'cache') return '';
    const at =
        typeof prksOfflineFormatCachedAt === 'function' ? prksOfflineFormatCachedAt(offlineResult.cachedAt) : '';
    return (
        '<div class="prks-offline-banner" data-prks-role="offline-provenance-banner">' +
        '<span class="prks-offline-banner__icon" aria-hidden="true">' +
        (typeof prksIcon === 'function' ? prksIcon('wifi-off', { size: 'sm' }) : '') +
        '</span>' +
        '<span>Offline' +
        (at ? ' · cached ' + prksEscapeHtmlLite(at) : '') +
        '</span>' +
        '</div>'
    );
}

/** Prepends the cached-provenance banner into an already-rendered detail page. Online/not-found renders are untouched. */
function prksOfflinePrependBanner(container, offlineResult) {
    if (!container || !offlineResult || offlineResult.source !== 'cache') return;
    const html = prksOfflineProvenanceBannerHtml(offlineResult);
    if (!html) return;
    container.insertAdjacentHTML('afterbegin', html);
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(container);
}

function prksOfflineRenderUnavailable(container, label) {
    if (!container) return;
    container.innerHTML =
        '<div class="prks-page-header page-header"><h2 class="prks-page-title">' +
        prksEscapeHtmlLite(label || 'Not available offline') +
        '</h2></div>' +
        '<p class="prks-inline-message" data-prks-role="offline-unavailable">This item is not available offline.</p>';
}

/** After reconnecting, quietly refresh only a focused, cache-served route -- never an in-progress edit. */
function prksOfflineMaybeRefreshFocusedRoute() {
    if (typeof prksGetFocusedTabContext !== 'function' || typeof prksRenderTabRoute !== 'function') return;
    const ctx = prksGetFocusedTabContext();
    if (!ctx || !ctx.root || ctx.destroyed || !ctx.lastResolvedRoute) return;
    if (ctx.ui && (ctx.ui.workDetailsMode === 'metadata' || ctx.ui.personDetailEditing || ctx.ui.argumentEditing || ctx.ui.personGroupEditing || ctx.ui.personGroupMembersEditing || ctx.ui.playlistEditing)) return;
    const banner = ctx.root.querySelector('[data-prks-role="offline-provenance-banner"], [data-prks-role="offline-unavailable"]');
    if (!banner) return;
    void prksRenderTabRoute(ctx, ctx.lastResolvedRoute.canonicalHash, { leaveApproved: true, internalRefresh: true });
}

let __prksOfflinePrevState = 'online';

function prksRenderConnectivityIndicator(state) {
    const el = document.getElementById('prks-connectivity-indicator');
    const label = document.getElementById('prks-connectivity-indicator-label');
    if (!el) return;
    const online = state !== 'offline' && state !== 'reconnecting';
    el.hidden = online;
    el.classList.toggle('hidden', online);
    el.classList.toggle('prks-connectivity-indicator--reconnecting', state === 'reconnecting');
    if (label) label.textContent = state === 'reconnecting' ? 'Reconnecting…' : 'Offline';
}

function initPrksConnectivityIndicator() {
    if (typeof prksOfflineRuntimeInit === 'function') prksOfflineRuntimeInit();
    const initial = typeof prksOfflineRuntimeState === 'function' ? prksOfflineRuntimeState() : 'online';
    __prksOfflinePrevState = initial;
    prksRenderConnectivityIndicator(initial);
    if (typeof prksOfflineRuntimeSubscribe === 'function') {
        prksOfflineRuntimeSubscribe(function (state) {
            prksRenderConnectivityIndicator(state);
            if (state === 'online' && __prksOfflinePrevState !== 'online') {
                prksOfflineMaybeRefreshFocusedRoute();
            }
            __prksOfflinePrevState = state;
        });
    }
}

function prksFormatBytesApprox(n) {
    const num = Number(n) || 0;
    if (num < 1024) return num + ' B';
    if (num < 1024 * 1024) return Math.round(num / 1024) + ' KB';
    return (num / (1024 * 1024)).toFixed(1) + ' MB';
}

async function prksLoadOfflineCacheStatus() {
    const summaryEl = document.getElementById('prks-offline-cache-summary');
    try {
        if (typeof prksOfflineDiagnostics !== 'function') throw new Error('Offline cache unavailable.');
        const diag = await prksOfflineDiagnostics();
        if (!summaryEl) return;
        if (!diag.available) {
            summaryEl.textContent = 'Offline cache is unavailable in this browser. PRKS still works normally online.';
            return;
        }
        summaryEl.textContent =
            'Cached items: ' +
            String((diag.entityCount || 0) + (diag.listCount || 0)) +
            '. Cached PDFs: ' +
            String(diag.pdfCount || 0) +
            // navigator.storage.estimate() reports usage for the WHOLE origin
            // -- the precached app shell and anything else this browser stores
            // for PRKS, not only the rows and PDFs counted above. Labelling it
            // as the size of those items would overstate what Clear removes.
            '. Approx. PRKS browser storage on this device: ' +
            (diag.approxBytes != null ? prksFormatBytesApprox(diag.approxBytes) : '—') +
            '.';
        if (typeof prksSyncDiagnostics === 'function') {
            const sync = await prksSyncDiagnostics();
            summaryEl.textContent += ' Unsynchronized changes: ' + sync.pendingTotal +
                ' (waiting: ' + sync.byStatus.pending + ', syncing: ' + sync.byStatus.syncing +
                ', conflicts: ' + sync.byStatus.conflict + '). Clearing the cache preserves these changes.';
            // Activity events the server refused have no resolution to offer,
            // so they are consumed rather than parked -- but they are still
            // worth seeing here rather than vanishing without trace.
            if (sync.discarded && sync.discarded.length) {
                summaryEl.textContent += ' Open events dropped this session: ' + sync.discarded.length + '.';
            }
            if (typeof prksRenderSyncDiagnostics === 'function') await prksRenderSyncDiagnostics(summaryEl);
        }
    } catch (e) {
        if (summaryEl) summaryEl.textContent = (e && e.message) || 'Could not load offline cache status.';
    }
}
window.prksLoadOfflineCacheStatus = prksLoadOfflineCacheStatus;

function initPrksOfflineCacheSettings() {
    const refreshBtn = document.getElementById('prks-offline-cache-refresh-btn');
    const clearBtn = document.getElementById('prks-offline-cache-clear-btn');
    if (refreshBtn && refreshBtn.dataset.bound !== '1') {
        refreshBtn.dataset.bound = '1';
        refreshBtn.addEventListener('click', () => {
            void prksLoadOfflineCacheStatus();
        });
    }
    if (clearBtn && clearBtn.dataset.bound !== '1') {
        clearBtn.dataset.bound = '1';
        clearBtn.addEventListener('click', async () => {
            const statusEl = document.getElementById('prks-offline-cache-status');
            const confirmed =
                typeof prksConfirmDestructive === 'function'
                    ? await prksConfirmDestructive({
                          title: 'Clear offline cache?',
                          message:
                              'This removes pages and PDFs cached on this device for offline use. It never changes anything stored on the PRKS server.',
                          confirmLabel: 'Clear offline cache',
                      })
                    : window.confirm('Clear offline cache stored on this device?');
            if (!confirmed) return;
            if (typeof prksSetButtonBusy === 'function') prksSetButtonBusy(clearBtn, true, { busyLabel: 'Clearing…' });
            try {
                if (typeof prksOfflineClearCache === 'function') await prksOfflineClearCache();
                if (statusEl) statusEl.textContent = 'Offline cache cleared.';
                await prksLoadOfflineCacheStatus();
            } catch (_e) {
                if (statusEl) statusEl.textContent = 'Could not clear offline cache.';
            } finally {
                if (typeof prksSetButtonBusy === 'function') prksSetButtonBusy(clearBtn, false);
            }
        });
    }
}

function initRouter() {
    initSidebarBrandHome();
    if (typeof prksWorkspaceInit !== 'function') {
        window.addEventListener('hashchange', handleRoute);
    }
    handleRoute();
}

function prksRouteTitleFromHash(hash) {
    if (typeof prksRouteLoadingTitle === 'function') return prksRouteLoadingTitle(hash);
    return 'Loading';
}

function prksRenderRouteLoading(contentDiv, hash) {
    if (!contentDiv) return;
    const title = prksRouteTitleFromHash(hash);
    contentDiv.setAttribute('aria-busy', 'true');
    contentDiv.innerHTML = `
        <div class="prks-page-header page-header"><h2 class="prks-page-title">${title}</h2></div>
        <div class="prks-route-loading" role="status" aria-live="polite">
            <p class="meta-row">Loading view...</p>
            <div class="prks-route-loading__bar"></div>
        </div>
    `;
}

function prksPlayPageEnterAnimation(contentDiv) {
    if (!contentDiv) return;
    if (contentDiv.closest && contentDiv.closest('.prks-workspace-canvas--tiled')) return;
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

function prksResolveWorkspaceMainTab() {
    if (typeof prksWorkspaceSnapshot !== 'function') return null;
    const snap = prksWorkspaceSnapshot();
    if (!snap || !Array.isArray(snap.tabs)) return null;
    for (let i = 0; i < snap.tabs.length; i++) {
        if (snap.tabs[i] && snap.tabs[i].id === snap.mainTabId) return snap.tabs[i];
    }
    return snap.tabs[0] || null;
}

function prksEnsureMountedTabContext(tabId) {
    if (!tabId || typeof prksEnsureTabContext !== 'function' || typeof prksMountTabContext !== 'function') {
        return null;
    }
    const host =
        typeof prksWorkspaceHostForTab === 'function'
            ? prksWorkspaceHostForTab(tabId)
            : typeof prksTabContextHost === 'function'
              ? prksTabContextHost()
              : document.getElementById('page-content');
    if (!host) return null;
    prksEnsureTabContext(tabId);
    return prksMountTabContext(tabId, host);
}

function prksCanLeaveTabContext(ctx, nextHash) {
    if (typeof prksFlushPendingWorkResearchNotes === 'function') {
        prksFlushPendingWorkResearchNotes(ctx);
    }
    if (typeof prksFlushPendingPrivateNotes === 'function') {
        prksFlushPendingPrivateNotes(ctx);
    }
    const prevRoute = ctx && ctx.lastResolvedRoute;
    const route =
        typeof prksParseRoute === 'function'
            ? prksParseRoute(nextHash || '#/folders')
            : null;
    const leavingWorkPage = !!(
        prevRoute &&
        prevRoute.name === 'work' &&
        route &&
        route.canonicalHash !== prevRoute.canonicalHash
    );
    if (
        leavingWorkPage &&
        typeof window.prksHasPendingWorkAnnotationSync === 'function' &&
        window.prksHasPendingWorkAnnotationSync(ctx)
    ) {
        const annotationLeaveApproved = window.confirm(
            'PDF annotation sync still running. Leave page before all changes save to server?'
        );
        if (!annotationLeaveApproved) return false;
    }
    return prksCanLeaveTabContextOwnedDraft(ctx);
}

function prksCanLeaveTabContextOwnedDraft(ctx) {
    const prevRoute = ctx && ctx.lastResolvedRoute;
    if (!ctx || !ctx.ui || !prevRoute) return true;

    if (prevRoute.name === 'person' && ctx.ui.personDetailEditing) {
        const person = ctx.getEntity ? ctx.getEntity('person') : null;
        const draft = ctx.ui.personProfileDraft;
        if (person && draft && String(draft.personId) === String(person.id)) {
            if (
                typeof prksRightPanelOwnedBy === 'function' &&
                prksRightPanelOwnedBy(ctx) &&
                typeof prksSyncPersonProfileDraftFromEditor === 'function'
            ) {
                const panel = document.getElementById('panel-content');
                const editor = panel && panel.querySelector('.person-panel-edit');
                if (editor) {
                    prksSyncPersonProfileDraftFromEditor(ctx, editor, person.id, ctx.generation);
                }
            }
            if (
                typeof prksPersonProfileDraftIsDirty === 'function' &&
                prksPersonProfileDraftIsDirty(ctx, person)
            ) {
                if (typeof prksConfirmUnsavedRouteLeave !== 'function') return Promise.resolve(false);
                return prksConfirmUnsavedRouteLeave({
                    title: 'Discard profile changes?',
                    message: 'Your unsaved Person profile changes will be discarded.',
                });
            }
        }
    }

    if (prevRoute.name === 'work' && ctx.ui.workDetailsMode === 'metadata') {
        const work = ctx.getEntity ? ctx.getEntity('work') : null;
        if (work) {
            if (typeof prksCaptureWorkMetaDraft === 'function') {
                prksCaptureWorkMetaDraft(ctx);
            }
            if (
                typeof prksWorkMetaDraftIsDirty === 'function' &&
                prksWorkMetaDraftIsDirty(ctx, work)
            ) {
                if (typeof prksConfirmUnsavedRouteLeave !== 'function') return Promise.resolve(false);
                return prksConfirmUnsavedRouteLeave({
                    title: 'Discard metadata changes?',
                    message: 'Your unsaved Work metadata changes will be discarded.',
                });
            }
        }
    }
    return true;
}

function prksCanLeaveCurrentRoute(nextHash) {
    const ctx = typeof prksGetMainTabContext === 'function' ? prksGetMainTabContext() : null;
    return prksCanLeaveTabContext(ctx, nextHash);
}

window.prksCanLeaveCurrentRoute = prksCanLeaveCurrentRoute;
window.prksCanLeaveTabContext = prksCanLeaveTabContext;

async function handleRoute(options) {
    const opts = options || {};
    if (typeof prksCloseOverlays === 'function') prksCloseOverlays();
    if (!opts.fromWorkspace && typeof prksWorkspaceAdoptLocation === 'function') {
        prksWorkspaceAdoptLocation();
    }
    const tab = prksResolveWorkspaceMainTab();
    const tabId = opts.tabId || (tab && tab.id);
    const hash =
        opts.hash != null
            ? opts.hash
            : (tab && tab.route) || window.location.hash || '#/folders';
    if (!tabId) return;
    const ctx = prksEnsureMountedTabContext(tabId);
    if (!ctx || !ctx.root) return;
    return prksRenderTabRoute(ctx, hash, opts);
}

async function prksRenderTabRoute(ctx, hash, options) {
    if (!ctx || ctx.destroyed) return;
    const opts = options || {};
    const workspaceSwitch = !!opts.workspaceSwitch;
    const fromPopstate = !!opts.fromPopstate;
    const leaveApproved = !!opts.leaveApproved;
    const routeStateCaptured = !!opts.routeStateCaptured;
    // A re-render PRKS decided to do -- reconnecting, say -- is not the user
    // opening anything. Recording an open here would mean that regaining
    // connectivity silently reordered Recent, which is the same defect that
    // made GET /api/works/:id impure in the first place.
    const internalRefresh = !!opts.internalRefresh;
    const suppliedHash = hash == null ? '#/folders' : String(hash);
    let route = typeof prksParseRoute === 'function' ? prksParseRoute(suppliedHash) : null;
    if (!route) return;

    const prevRoute = ctx.lastResolvedRoute || null;
    const leavingWorkPage = !!(
        prevRoute &&
        prevRoute.name === 'work' &&
        route.canonicalHash !== prevRoute.canonicalHash
    );
    if (!leaveApproved) {
        if (
            leavingWorkPage &&
            typeof window.prksHasPendingWorkAnnotationSync === 'function' &&
            window.prksHasPendingWorkAnnotationSync(ctx)
        ) {
            const ok = window.confirm(
                'PDF annotation sync still running. Leave page before all changes save to server?'
            );
            if (!ok) {
                return { cancelled: true, reason: 'pending-sync' };
            }
        }
        const draftLeaveApproved = await Promise.resolve(prksCanLeaveTabContextOwnedDraft(ctx));
        if (!draftLeaveApproved) {
            return { cancelled: true, reason: 'unsaved-edit' };
        }
    }

    if (route.canonicalize && route.canonicalHash && route.canonicalHash !== suppliedHash) {
        const isMain = typeof prksIsMainTabContext === 'function' ? prksIsMainTabContext(ctx) : true;
        if (isMain && typeof prksNavigate === 'function') {
            prksNavigate(route.canonicalHash, { replace: true });
            return;
        }
        route = prksParseRoute(route.canonicalHash);
    }

    const prevHash = prevRoute ? prevRoute.hash || prevRoute.canonicalHash : '';
    const prevPdf = ctx.getResource && ctx.getResource('pdf');
    if (prevPdf && typeof prevPdf.flushLastPage === 'function') {
        try {
            prevPdf.flushLastPage();
        } catch (_e) {}
    }
    prksMaybeFlushPdfLastPageOnRouteChange(prevHash, route.hash);
    if (prevRoute && prevRoute.canonicalHash && prevRoute.canonicalHash !== route.canonicalHash) {
        if (!routeStateCaptured && typeof prksCaptureCurrentRouteState === 'function') {
            prksCaptureCurrentRouteState(prevRoute, ctx);
        }
        if (!workspaceSwitch && !fromPopstate && route.detail && typeof prksRememberOrigin === 'function') {
            prksRememberOrigin(route, prevRoute, ctx);
        }
    }

    if (typeof prksFlushPendingWorkResearchNotes === 'function') {
        prksFlushPendingWorkResearchNotes(ctx);
    }
    if (typeof prksFlushPendingPrivateNotes === 'function') {
        prksFlushPendingPrivateNotes(ctx);
    }

    const contentDiv = ctx.root;
    if (!contentDiv) return;

    const previousPersonGroup = ctx.getEntity && ctx.getEntity('personGroup');
    const previousPersonGroupId = previousPersonGroup ? String(previousPersonGroup.id) : '';
    const previousPersonGroupMembersEditing = !!(ctx.ui && ctx.ui.personGroupMembersEditing);
    const generation = ctx.beginRoute(route);
    const routeAbort = ctx.abortController;
    const routeSignal = routeAbort && routeAbort.signal;
    const stale = function () {
        return !ctx.isCurrent(generation);
    };
    const publishSidebar = function (data) {
        if (typeof prksPublishRouteSidebar === 'function') return prksPublishRouteSidebar(ctx, data, generation);
        if (stale()) return false;
        ctx.routeSidebar = data || {};
        return true;
    };

    if (typeof prksIsMainTabContext === 'function' ? prksIsMainTabContext(ctx) : true) {
        if (typeof prksSyncSidebarActive === 'function') prksSyncSidebarActive(route);
    }

    prksRenderRouteLoading(contentDiv, route.hash);

    let titleOpts = {};

    try {
        switch (route.name) {
            case 'folders': {
                const offlineFolders = await prksOfflineListFetch(
                    PRKS_FOLDERS_LIST_KEY, '/api/folders', routeSignal,
                    { domain: PRKS_FOLDERS_DOMAIN, validate: prksIsFoldersIndexShape }
                );
                if (stale()) return;
                const folders = prksResolveOfflineFoldersIndex(offlineFolders);
                if (!folders) {
                    // A cached [] is a real empty library; only a MISSING
                    // snapshot is an unavailable state.
                    prksOfflineRenderUnavailable(contentDiv, 'Folders not available offline');
                    titleOpts = { notFound: true, notFoundTitle: 'Folders not available offline' };
                    break;
                }
                publishSidebar({ folderCount: folders.length });
                renderDashboard(folders, contentDiv, { offlineCached: offlineFolders.source === 'cache', ctx: ctx });
                prksOfflinePrependBanner(contentDiv, offlineFolders);
                break;
            }
            case 'playlists': {
                if (typeof renderPlaylistsIndex === 'function') {
                    const offlinePlaylists = await prksOfflineListFetch(
                        PRKS_PLAYLISTS_LIST_KEY, '/api/playlists', routeSignal,
                        { domain: PRKS_PLAYLISTS_DOMAIN, validate: prksIsPlaylistsIndexShape }
                    );
                    if (stale()) return;
                    const pls = prksResolveOfflinePlaylistsIndex(offlinePlaylists);
                    if (!pls) {
                        prksOfflineRenderUnavailable(contentDiv, 'Playlists not available offline');
                        break;
                    }
                    renderPlaylistsIndex(pls, contentDiv, ctx);
                    prksOfflinePrependBanner(contentDiv, offlinePlaylists);
                } else {
                    contentDiv.innerHTML =
                        '<div class="prks-page-header page-header"><h2 class="prks-page-title">Playlists</h2></div><p class="meta-row">Playlist UI unavailable.</p>';
                }
                break;
            }
            case 'playlist-detail': {
                const plId = route.params.playlistId;
                if (typeof renderPlaylistDetail === 'function') {
                    const offlinePlaylist = await prksOfflineDetailFetch(
                        'playlist', plId, '/api/playlists/' + encodeURIComponent(plId), routeSignal,
                        { domain: PRKS_PLAYLISTS_DOMAIN, validate: (value) => prksIsPlaylistShape(value, plId) }
                    );
                    if (stale()) return;
                    const resolvedPlaylist = prksResolveOfflinePlaylist(offlinePlaylist, plId);
                    if (resolvedPlaylist.unavailable) {
                        ctx.setEntity('playlist', null);
                        ctx.ui.playlistEditing = false;
                        ctx.ui.playlistRename = {};
                        prksOfflineRenderUnavailable(contentDiv, 'Playlist not available offline');
                        titleOpts = { notFound: true, notFoundTitle: 'Playlist not available offline' };
                        break;
                    }
                    const pl = resolvedPlaylist.playlist;
                    ctx.setEntity('playlist', pl);
                    ctx.ui.playlistEditing = false;
                    ctx.ui.playlistRename = {};
                    publishSidebar(
                        pl
                            ? {
                                  playlistTitle: pl.title || 'Playlist',
                                  itemCount: Array.isArray(pl.items) ? pl.items.length : 0,
                              }
                            : { playlistTitle: 'Playlist', itemCount: 0 }
                    );
                    renderPlaylistDetail(ctx, pl, contentDiv);
                    prksOfflinePrependBanner(contentDiv, offlinePlaylist);
                    titleOpts = pl
                        ? { entityTitle: pl.title || 'Playlist' }
                        : { notFound: true, notFoundTitle: 'Playlist not found' };
                } else {
                    contentDiv.innerHTML =
                        '<div class="prks-page-header page-header"><h2 class="prks-page-title">Playlists</h2></div><p class="meta-row">Playlist UI unavailable.</p>';
                }
                break;
            }
            case 'folder-detail': {
                const folderId = route.params.folderId;
                const offlineFolder = await prksOfflineDetailFetch(
                    'folder', folderId, '/api/folders/' + encodeURIComponent(folderId), routeSignal,
                    { domain: PRKS_FOLDERS_DOMAIN, validate: (value) => prksIsFolderShape(value, folderId) }
                );
                if (stale()) return;
                const resolvedFolder = prksResolveOfflineFolder(offlineFolder, folderId);
                if (resolvedFolder.unavailable) {
                    ctx.setEntity('folder', null);
                    prksOfflineRenderUnavailable(contentDiv, 'Folder not available offline');
                    titleOpts = { notFound: true, notFoundTitle: 'Folder not available offline' };
                    break;
                }
                const folder = resolvedFolder.folder;
                ctx.setEntity('folder', folder);
                renderFolderDetails(ctx, folder, contentDiv, {
                    offlineCached: offlineFolder.source === 'cache',
                });
                prksOfflinePrependBanner(contentDiv, offlineFolder);
                titleOpts = folder
                    ? { entityTitle: folder.title || 'Folder' }
                    : { notFound: true, notFoundTitle: 'Folder not found' };
                break;
            }
            case 'people': {
                const offlinePeople = await prksOfflinePeopleFetch(routeSignal);
                if (stale()) return;
                const persons = prksResolveOfflinePeopleIndex(offlinePeople);
                if (!persons) {
                    if (typeof renderPeopleListUnavailable === 'function') renderPeopleListUnavailable(contentDiv);
                    else prksOfflineRenderUnavailable(contentDiv, 'People not available offline');
                    titleOpts = { notFound: true, notFoundTitle: 'People not available offline' };
                    break;
                }
                renderPeopleList(ctx, persons, contentDiv);
                prksOfflinePrependBanner(contentDiv, offlinePeople);
                break;
            }
            case 'people-role': {
                const roleFilter = route.params.knownRole ? route.params.role : null;
                // The same complete list under the same key: role views are
                // local projections, never separately cached server subsets.
                const offlineRolePeople = await prksOfflinePeopleFetch(routeSignal);
                if (stale()) return;
                const rolePersons = prksResolveOfflinePeopleIndex(offlineRolePeople);
                publishSidebar({ role: roleFilter || route.params.role || 'Unknown role' });
                if (!roleFilter) {
                    contentDiv.innerHTML =
                        '<div class="prks-page-header page-header"><h2 class="prks-page-title">People</h2></div><p class="prks-inline-message">Unknown role filter.</p>';
                    break;
                }
                if (!rolePersons) {
                    if (typeof renderPeopleListUnavailable === 'function') renderPeopleListUnavailable(contentDiv);
                    else prksOfflineRenderUnavailable(contentDiv, 'People not available offline');
                    titleOpts = { notFound: true, notFoundTitle: 'People not available offline' };
                    break;
                }
                renderPeopleList(ctx, rolePersons, contentDiv, { roleFilter });
                prksOfflinePrependBanner(contentDiv, offlineRolePeople);
                break;
            }
            case 'people-groups': {
                const offlineGroups = await prksOfflineListFetch(
                    PRKS_PERSON_GROUPS_LIST_KEY, '/api/person-groups', routeSignal,
                    { domain: PRKS_PERSON_GROUPS_DOMAIN, validate: prksIsPersonGroupsIndexShape }
                );
                if (stale()) return;
                const groups = prksResolveOfflinePersonGroupsIndex(offlineGroups);
                if (!groups) {
                    prksOfflineRenderUnavailable(contentDiv, 'Person Groups not available offline');
                    break;
                }
                publishSidebar({ groupCount: Array.isArray(groups) ? groups.length : 0 });
                renderPersonGroupsPage(groups, contentDiv, ctx);
                prksOfflinePrependBanner(contentDiv, offlineGroups);
                break;
            }
            case 'person-group-detail': {
                const groupId = route.params.groupId;
                const offlineGroup = await prksOfflineDetailFetch(
                    'person-group', groupId, '/api/person-groups/' + encodeURIComponent(groupId), routeSignal,
                    { domain: PRKS_PERSON_GROUPS_DOMAIN, validate: (value) => prksIsPersonGroupShape(value, groupId) }
                );
                if (stale()) return;
                const resolvedGroup = prksResolveOfflinePersonGroup(offlineGroup, groupId);
                if (resolvedGroup.unavailable) {
                    ctx.setEntity('personGroup', null);
                    prksOfflineRenderUnavailable(contentDiv, 'Group not available offline');
                    titleOpts = { notFound: true, notFoundTitle: 'Group not available offline' };
                    break;
                }
                const group = resolvedGroup.group;
                if (!group) {
                    contentDiv.innerHTML =
                        '<div class="prks-page-header page-header"><h2 class="prks-page-title">Group not found</h2></div><p class="meta-row"><a href="#/people/groups" class="route-sidebar__link">Back to groups</a></p>';
                    titleOpts = { notFound: true, notFoundTitle: 'Group not found' };
                } else {
                    const preserveMembersEditing =
                        previousPersonGroupId &&
                        previousPersonGroupId === String(group.id) &&
                        previousPersonGroupMembersEditing && offlineGroup.source === 'server';
                    ctx.setEntity('personGroup', group);
                    ctx.ui.personGroupEditing = false;
                    ctx.ui.personGroupMembersEditing = preserveMembersEditing;
                    publishSidebar({
                        groupName: group.name,
                        memberCount: Array.isArray(group.members) ? group.members.length : 0,
                        subgroupCount: Array.isArray(group.children) ? group.children.length : 0,
                    });
                    renderPersonGroupDetail(group, contentDiv, ctx);
                    prksOfflinePrependBanner(contentDiv, offlineGroup);
                    titleOpts = { entityTitle: group.name || 'Group' };
                }
                break;
            }
            case 'recent': {
                // Served from its OWN snapshot, never recomputed from the
                // stable catalog: the canonical order is top-N by
                // last_opened_at with an id tie-break, and the catalog does
                // not carry last_opened_at at all.
                const offlineRecent = await prksOfflineBrowseFetch(
                    PRKS_RECENT_LIST_KEY, PRKS_RECENT_DOMAIN, '/api/recent',
                    prksIsRecentIndexShape, routeSignal
                );
                if (stale()) return;
                const base = prksResolveOfflineBrowseList(
                    offlineRecent, PRKS_RECENT_LIST_KEY, prksIsRecentIndexShape, 'Recent'
                );
                // Acknowledged snapshot + durable pending open events. Pending
                // intent is never written into the cached list itself, and a
                // missing snapshot stays missing: one open event is not a
                // Recent page. Reconstructed from prks-local-v1, so it survives
                // a reload rather than living in this tab's memory.
                const works = base && typeof prksEffectiveRecent === 'function' && window.prksSync
                    ? prksEffectiveRecent(base, await prksSync.store.listOperations()) : base;
                if (stale()) return;
                if (!works) {
                    prksOfflineRenderUnavailable(contentDiv, 'Recently opened not available offline');
                    titleOpts = { notFound: true, notFoundTitle: 'Recently opened not available offline' };
                    break;
                }
                publishSidebar({ workCount: works.length });
                renderRecent(works, contentDiv, { offlineCached: offlineRecent.source === 'cache' });
                prksOfflinePrependBanner(contentDiv, offlineRecent);
                break;
            }
            case 'saved-views': {
                const views = typeof fetchSavedViews === 'function' ? await fetchSavedViews({ signal: routeSignal }) : [];
                if (stale()) return;
                if (typeof renderSavedViewsIndex === 'function') {
                    renderSavedViewsIndex(views, contentDiv);
                } else {
                    contentDiv.innerHTML =
                        '<div class="prks-page-header page-header"><h2 class="prks-page-title">Saved Views</h2></div><p class="meta-row">Saved Views UI unavailable.</p>';
                }
                break;
            }
            case 'saved-view-detail': {
                const viewId = route.params.viewId;
                const view = typeof fetchSavedView === 'function' ? await fetchSavedView(viewId, { signal: routeSignal }) : null;
                if (stale()) return;
                ctx.ui.currentSavedView = view || null;
                if (!view) {
                    if (typeof renderSavedViewNotFound === 'function') {
                        renderSavedViewNotFound(contentDiv);
                    } else {
                        contentDiv.innerHTML =
                            '<div class="prks-page-header page-header"><h2 class="prks-page-title">Saved View not found.</h2></div><p class="meta-row"><a href="#/views">Back to Saved Views</a></p>';
                    }
                    titleOpts = { notFound: true, notFoundTitle: 'Saved View not found' };
                    break;
                }
                const mapped =
                    typeof prksSearchOptionsFromDefinition === 'function'
                        ? prksSearchOptionsFromDefinition(view.search || {})
                        : { q: '', tag: null, options: {} };
                const results = await fetchSearch(mapped.q, mapped.tag, Object.assign({}, mapped.options, { signal: routeSignal }));
                if (stale()) return;
                if (typeof renderSavedViewDetail === 'function') {
                    renderSavedViewDetail(view, results, contentDiv);
                }
                titleOpts = { entityTitle: view.name || 'Saved View' };
                break;
            }
            case 'progress': {
                const status = route.params.status;
                const offlineBrowse = await prksOfflineWorksBrowseFetch(routeSignal);
                if (stale()) return;
                const base = prksResolveOfflineWorksBrowse(offlineBrowse);
                if (!base) {
                    prksOfflineRenderUnavailable(contentDiv, 'Progress not available offline');
                    titleOpts = { notFound: true, notFoundTitle: 'Progress not available offline' };
                    break;
                }
                // Acknowledged catalog + pending Abstract edits, through the
                // projection's own derivation: what reaches this row is not the
                // Abstract but its excerpt, exactly as the server would derive
                // it. The cached catalog itself is never rewritten.
                if (typeof prksRefreshPendingWorkMetadata === 'function') {
                    await prksRefreshPendingWorkMetadata();
                    if (stale()) return;
                }
                const works = typeof prksEffectiveProjectionRows === 'function'
                    ? prksEffectiveProjectionRows(base, 'works-browse') : base;
                publishSidebar({ status });
                // Pure local projection of the cached catalog -- no request.
                renderProgressByStatus(works, status, contentDiv,
                    { offlineCached: offlineBrowse.source === 'cache' });
                prksOfflinePrependBanner(contentDiv, offlineBrowse);
                break;
            }
            case 'processing-files': {
                if (typeof prksRenderProcessingFilesPageWithFetch === 'function') {
                    await prksRenderProcessingFilesPageWithFetch(contentDiv, { rescan: true, routeGen: generation, signal: routeSignal, ctx: ctx });
                    if (stale()) return;
                } else {
                    const rows = await fetchProcessingFiles({ rescan: true, signal: routeSignal });
                    if (stale()) return;
                    publishSidebar({ pendingCount: Array.isArray(rows) ? rows.length : 0 });
                    if (typeof renderProcessingFilesPage === 'function') {
                        renderProcessingFilesPage(rows, contentDiv);
                    } else {
                        contentDiv.innerHTML =
                            '<div class="prks-page-header page-header"><h2 class="prks-page-title">Files for Processing</h2></div><p class="meta-row">Processing inbox UI unavailable.</p>';
                    }
                }
                break;
            }
            case 'search': {
                const query = route.params.q || '';
                const tag = route.params.tag || '';
                const author = route.params.author || '';
                const publisher = route.params.publisher || '';
                const any = route.params.any || '';
                const results = await fetchSearch(query, tag, { author, publisher, any, signal: routeSignal });
                if (stale()) return;
                publishSidebar({
                    query,
                    tag,
                    author,
                    publisher,
                    resultCount: Array.isArray(results) ? results.length : 0,
                });
                renderSearch(results, query, contentDiv, { tag, author, publisher, any });
                break;
            }
            case 'tags': {
                if (typeof renderTagsPage === 'function') {
                    await renderTagsPage(contentDiv, generation, { signal: routeSignal, ctx: ctx });
                    if (stale()) return;
                }
                break;
            }
            case 'publishers': {
                if (typeof renderPublishersPage === 'function') {
                    await renderPublishersPage(contentDiv, generation, { signal: routeSignal, ctx: ctx });
                    if (stale()) return;
                }
                break;
            }
            case 'types': {
                const offlineBrowse = await prksOfflineWorksBrowseFetch(routeSignal);
                if (stale()) return;
                const works = prksResolveOfflineWorksBrowse(offlineBrowse);
                if (!works) {
                    prksOfflineRenderUnavailable(contentDiv, 'Types not available offline');
                    titleOpts = { notFound: true, notFoundTitle: 'Types not available offline' };
                    break;
                }
                renderTypesIndex(works, contentDiv);
                prksOfflinePrependBanner(contentDiv, offlineBrowse);
                break;
            }
            case 'type-detail': {
                const offlineBrowse = await prksOfflineWorksBrowseFetch(routeSignal);
                if (stale()) return;
                const works = prksResolveOfflineWorksBrowse(offlineBrowse);
                if (!works) {
                    prksOfflineRenderUnavailable(contentDiv, 'Types not available offline');
                    titleOpts = { notFound: true, notFoundTitle: 'Types not available offline' };
                    break;
                }
                renderWorksByDocType(works, route.params.docType, contentDiv,
                    { offlineCached: offlineBrowse.source === 'cache' });
                prksOfflinePrependBanner(contentDiv, offlineBrowse);
                break;
            }
            case 'work': {
                const workId = route.params.workId;
                const offlineWork = await prksOfflineDetailFetch(
                    'work',
                    workId,
                    '/api/works/' + encodeURIComponent(workId),
                    routeSignal
                );
                if (stale()) return;
                // This route IS the genuine foreground open, so it is the only
                // place that records one. The read itself is pure; the explicit
                // event is what reorders #/recent. It takes the durable path
                // whether or not PRKS is reachable -- a Work opened from cache
                // offline is just as genuinely opened -- so online and offline
                // share one implementation rather than diverging.
                // Fire-and-forget, and deliberately never awaited: recording
                // the activity must not delay or endanger showing the Work.
                if (!internalRefresh && offlineWork.value && typeof prksRecordWorkOpened === 'function') {
                    void prksRecordWorkOpened(offlineWork.value);
                }
                const work = offlineWork.value;
                if (!work && offlineWork.source === 'unavailable') {
                    prksOfflineRenderUnavailable(contentDiv, 'File not available offline');
                    titleOpts = { notFound: true, notFoundTitle: 'File not available offline' };
                    break;
                }
                await renderWorkDetails(ctx, work, { generation: generation, signal: routeSignal });
                if (stale()) return;
                prksOfflinePrependBanner(contentDiv, offlineWork);
                titleOpts = work
                    ? { entityTitle: String(work.title || '').trim() || 'File' }
                    : { notFound: true, notFoundTitle: 'File not found' };
                break;
            }
            case 'concepts': {
                const offlineConcepts = await prksOfflineListFetch(
                    PRKS_CONCEPTS_LIST_KEY,
                    '/api/concepts',
                    routeSignal,
                    { domain: PRKS_CONCEPTS_DOMAIN, validate: prksIsConceptIndexShape }
                );
                if (stale()) return;
                const conceptItems = prksResolveOfflineConceptIndex(offlineConcepts);
                if (!conceptItems) {
                    if (typeof renderConceptsIndexUnavailable === 'function') renderConceptsIndexUnavailable(contentDiv);
                    else prksOfflineRenderUnavailable(contentDiv, 'Concepts not available offline');
                    titleOpts = { notFound: true, notFoundTitle: 'Concepts not available offline' };
                    break;
                }
                if (typeof renderConceptsIndex === 'function') renderConceptsIndex(ctx, conceptItems, contentDiv);
                else contentDiv.innerHTML = '<div class="prks-page-header page-header"><h2 class="prks-page-title">Concepts</h2></div>';
                prksOfflinePrependBanner(contentDiv, offlineConcepts);
                break;
            }
            case 'concept-detail': {
                const conceptId = route.params.conceptId;
                const offlineConcept = await prksOfflineDetailFetch(
                    'concept',
                    conceptId,
                    '/api/concepts/' + encodeURIComponent(conceptId),
                    routeSignal,
                    {
                        domain: PRKS_CONCEPTS_DOMAIN,
                        validate: function (value) {
                            return prksIsConceptShape(value, conceptId);
                        },
                    }
                );
                if (stale()) return;
                const resolvedConcept = prksResolveOfflineConcept(offlineConcept, conceptId);
                if (resolvedConcept.unavailable) {
                    prksOfflineRenderUnavailable(contentDiv, 'Concept not available offline');
                    titleOpts = { notFound: true, notFoundTitle: 'Concept not available offline' };
                    break;
                }
                const item = resolvedConcept.concept;
                if (!item) {
                    if (typeof renderConceptNotFound === 'function') renderConceptNotFound(contentDiv);
                    else contentDiv.innerHTML = '<div class="prks-page-header page-header"><h2 class="prks-page-title">Concept not found.</h2></div>';
                    titleOpts = { notFound: true, notFoundTitle: 'Concept not found' };
                } else {
                    ctx.setEntity('concept', item);
                    if (typeof renderConceptDetail === 'function') renderConceptDetail(ctx, item, contentDiv);
                    prksOfflinePrependBanner(contentDiv, offlineConcept);
                    titleOpts = { entityTitle: item.name || 'Concept' };
                }
                break;
            }
            case 'positions': {
                const offlinePositions = await prksOfflineListFetch(
                    PRKS_POSITIONS_LIST_KEY,
                    '/api/positions',
                    routeSignal,
                    { domain: PRKS_POSITIONS_DOMAIN, validate: prksIsPositionIndexShape }
                );
                if (stale()) return;
                const positionItems = prksResolveOfflinePositionIndex(offlinePositions);
                if (!positionItems) {
                    if (typeof renderPositionsIndexUnavailable === 'function') renderPositionsIndexUnavailable(contentDiv);
                    else prksOfflineRenderUnavailable(contentDiv, 'Positions not available offline');
                    titleOpts = { notFound: true, notFoundTitle: 'Positions not available offline' };
                    break;
                }
                if (typeof renderPositionsIndex === 'function') renderPositionsIndex(ctx, positionItems, contentDiv);
                else contentDiv.innerHTML = '<div class="prks-page-header page-header"><h2 class="prks-page-title">Positions</h2></div>';
                prksOfflinePrependBanner(contentDiv, offlinePositions);
                break;
            }
            case 'position-detail': {
                const positionId = route.params.positionId;
                const offlinePosition = await prksOfflineDetailFetch(
                    'position',
                    positionId,
                    '/api/positions/' + encodeURIComponent(positionId),
                    routeSignal,
                    {
                        domain: PRKS_POSITIONS_DOMAIN,
                        validate: function (value) {
                            return prksIsPositionShape(value, positionId);
                        },
                    }
                );
                if (stale()) return;
                const resolvedPosition = prksResolveOfflinePosition(offlinePosition, positionId);
                if (resolvedPosition.unavailable) {
                    prksOfflineRenderUnavailable(contentDiv, 'Position not available offline');
                    titleOpts = { notFound: true, notFoundTitle: 'Position not available offline' };
                    break;
                }
                const item = resolvedPosition.position;
                if (!item) {
                    if (typeof renderPositionNotFound === 'function') renderPositionNotFound(contentDiv);
                    else contentDiv.innerHTML = '<div class="prks-page-header page-header"><h2 class="prks-page-title">Position not found.</h2></div>';
                    titleOpts = { notFound: true, notFoundTitle: 'Position not found' };
                } else {
                    ctx.setEntity('position', item);
                    if (typeof renderPositionDetail === 'function') renderPositionDetail(ctx, item, contentDiv);
                    prksOfflinePrependBanner(contentDiv, offlinePosition);
                    titleOpts = { entityTitle: item.name || 'Position' };
                }
                break;
            }
            case 'arguments': {
                const kind = route.params.kind || '';
                // Always the COMPLETE collection: one cache key holds the whole
                // list and every ?kind= route derives its subset locally, so a
                // visit to any tab warms the cache for all of them.
                const offlineArguments = await prksOfflineListFetch(
                    PRKS_ARGUMENTS_LIST_KEY,
                    '/api/arguments',
                    routeSignal,
                    { domain: PRKS_ARGUMENTS_DOMAIN, validate: prksIsArgumentIndexShape }
                );
                if (stale()) return;
                const allArguments = prksResolveOfflineArgumentIndex(offlineArguments);
                if (!allArguments) {
                    if (typeof renderArgumentsIndexUnavailable === 'function') renderArgumentsIndexUnavailable(contentDiv);
                    else prksOfflineRenderUnavailable(contentDiv, 'Arguments & Stances not available offline');
                    titleOpts = { notFound: true, notFoundTitle: 'Arguments & Stances not available offline' };
                    break;
                }
                const argumentItems = prksFilterArgumentsByKind(allArguments, kind);
                if (typeof renderArgumentsIndex === 'function') renderArgumentsIndex(ctx, argumentItems, contentDiv, kind || 'all');
                else contentDiv.innerHTML = '<div class="prks-page-header page-header"><h2 class="prks-page-title">Arguments &amp; Stances</h2></div>';
                prksOfflinePrependBanner(contentDiv, offlineArguments);
                break;
            }
            case 'argument-detail': {
                const argumentId = route.params.argumentId;
                const offlineArgument = await prksOfflineDetailFetch(
                    'argument',
                    argumentId,
                    '/api/arguments/' + encodeURIComponent(argumentId),
                    routeSignal,
                    {
                        domain: PRKS_ARGUMENTS_DOMAIN,
                        validate: function (value) {
                            return prksIsArgumentShape(value, argumentId);
                        },
                    }
                );
                if (stale()) return;
                const resolvedArgument = prksResolveOfflineArgument(offlineArgument, argumentId);
                if (resolvedArgument.unavailable) {
                    prksOfflineRenderUnavailable(contentDiv, 'Argument or Stance not available offline');
                    titleOpts = { notFound: true, notFoundTitle: 'Argument or Stance not available offline' };
                    break;
                }
                const item = resolvedArgument.argument;
                if (!item) {
                    if (typeof renderArgumentNotFound === 'function') renderArgumentNotFound(contentDiv);
                    else contentDiv.innerHTML = '<div class="prks-page-header page-header"><h2 class="prks-page-title">Argument not found.</h2></div>';
                    titleOpts = { notFound: true, notFoundTitle: 'Argument not found' };
                } else {
                    ctx.setEntity('argument', item);
                    // A freshly rendered route always starts read-only, even if a
                    // previous mount left an edit session behind.
                    ctx.ui.argumentEditing = false;
                    if (typeof renderArgumentDetail === 'function') renderArgumentDetail(ctx, item, contentDiv);
                    prksOfflinePrependBanner(contentDiv, offlineArgument);
                    titleOpts = { entityTitle: item.name || 'Argument' };
                }
                break;
            }
            case 'research-graph': {
                if (typeof renderResearchGraph === 'function') {
                    await renderResearchGraph(contentDiv, {
                        ctx: ctx,
                        focus: route.params.focus || '',
                        loadSnapshot: prksOfflineResearchGraphFetch,
                        onSnapshot: function (result) {
                            contentDiv.querySelectorAll('[data-prks-role="offline-provenance-banner"]').forEach(el => el.remove());
                            prksOfflinePrependBanner(contentDiv, result);
                        },
                        routeGen: generation,
                        stale: stale,
                        signal: routeSignal,
                    });
                } else {
                    contentDiv.innerHTML =
                        '<div class="prks-page-header page-header"><h2 class="prks-page-title">Research Graph</h2></div><p class="meta-row">Graph UI unavailable.</p>';
                }
                break;
            }
            case 'person': {
                const personId = route.params.personId;
                const offlinePerson = await prksOfflineDetailFetch(
                    'person',
                    personId,
                    '/api/persons/' + encodeURIComponent(personId),
                    routeSignal,
                    {
                        domain: PRKS_PEOPLE_DOMAIN,
                        validate: function (value) {
                            return prksIsPersonShape(value, personId);
                        },
                    }
                );
                if (stale()) return;
                const resolvedPerson = prksResolveOfflinePerson(offlinePerson, personId);
                if (resolvedPerson.unavailable) {
                    prksOfflineRenderUnavailable(contentDiv, 'Person not available offline');
                    titleOpts = { notFound: true, notFoundTitle: 'Person not available offline' };
                    ctx.setEntity('person', null);
                    break;
                }
                const person = resolvedPerson.person;
                publishSidebar(
                    person
                        ? {
                              personDisplayName:
                                  typeof personDisplayName === 'function'
                                      ? personDisplayName(person) || 'Person'
                                      : 'Person',
                              linkedWorks:
                                  typeof prksUniquePersonWorks === 'function'
                                      ? prksUniquePersonWorks(person).length
                                      : person.works
                                        ? person.works.length
                                        : 0,
                          }
                        : { personDisplayName: 'Person not found', linkedWorks: 0 }
                );
                ctx.setEntity('person', person);
                // A freshly mounted route always starts read-only, even if a
                // previous mount left an edit session behind.
                ctx.ui.personDetailEditing = false;
                ctx.ui.personProfileDraft = null;
                ctx.ui.personWorksEditing = false;
                // A page served from cache must not ask PRKS for portrait or
                // Work-thumbnail bytes it cannot get; render the no-media form.
                ctx.ui.personOfflineCached = offlinePerson.source === 'cache';
                renderPersonDetails(ctx, person, contentDiv);
                prksOfflinePrependBanner(contentDiv, offlinePerson);
                if (person) {
                    const nm =
                        typeof personDisplayName === 'function'
                            ? personDisplayName(person)
                            : `${person.first_name || ''} ${person.last_name || ''}`.trim();
                    titleOpts = { entityTitle: String(nm || '').trim() || 'Person' };
                } else {
                    titleOpts = { notFound: true, notFoundTitle: 'Person not found' };
                }
                break;
            }
            default: {
                ctx.setEntity('work', null);
                if (typeof prksTabContextIsFocused === 'function' ? prksTabContextIsFocused(ctx) : true) {
                    updatePanelContent('details');
                }
                contentDiv.innerHTML =
                    '<div class="prks-page-header page-header"><h2 class="prks-page-title">Section In Development</h2></div><p class="prks-dev-path-msg prks-inline-message"></p>';
                const devPathEl = contentDiv.querySelector('.prks-dev-path-msg');
                if (devPathEl) devPathEl.textContent = `The requested path (${route.hash}) is not yet fully implemented.`;
                break;
            }
        }
    } catch (_e) {
        if (stale()) return;
        if (typeof prksIsAbortError === 'function' && prksIsAbortError(_e)) return;
        if (typeof prksRenderRouteError === 'function') {
            prksRenderRouteError(contentDiv, ctx, route.canonicalHash || route.hash, generation);
        }
        else contentDiv.innerHTML = '<p class="prks-inline-message">Could not load this view.</p>';
        if (typeof prksFinishRouteRender === 'function') {
            prksFinishRouteRender(ctx, route, generation, contentDiv, titleOpts);
        } else {
            contentDiv.removeAttribute('aria-busy');
        }
        return;
    }

    if (stale()) return;

    if (typeof window.prksInitLazyWorkThumbs === 'function') {
        window.prksInitLazyWorkThumbs(contentDiv);
    }

    const apiErr =
        typeof window.prksConsumeApiError === 'function'
            ? window.prksConsumeApiError(routeSignal)
            : null;
    if (apiErr && contentDiv && !contentDiv.querySelector('#prks-route-retry')) {
        const bar = document.createElement('div');
        bar.className = 'api-warning-banner';
        bar.setAttribute('role', 'status');
        bar.textContent = apiErr.message || 'Some data could not be loaded.';
        contentDiv.prepend(bar);
    }

    const isMain =
        typeof prksIsMainTabContext === 'function' ? prksIsMainTabContext(ctx) : true;
    const isFocused =
        typeof prksTabContextIsFocused === 'function' ? prksTabContextIsFocused(ctx) : isMain;
    const onWorkDetailPage = route.name === 'work' && ctx.getEntity('work');
    if (isFocused) {
        let tab = (ctx.ui && ctx.ui.rightPanelTab) || 'details';
        if (!onWorkDetailPage && tab === 'annotations') {
            tab = 'details';
            if (ctx.ui) ctx.ui.rightPanelTab = 'details';
        }
        if (typeof prksSyncRightPanelTabStrip === 'function') prksSyncRightPanelTabStrip(tab);
        if (!onWorkDetailPage) {
            updatePanelContent(tab);
        }
    }
    if (stale()) return;
    if (typeof prksFinishRouteRender === 'function') {
        prksFinishRouteRender(ctx, route, generation, contentDiv, titleOpts);
    } else {
        ctx.lastResolvedRoute = route;
        contentDiv.removeAttribute('aria-busy');
        prksPlayPageEnterAnimation(contentDiv);
    }
}

window.prksRenderTabRoute = prksRenderTabRoute;
window.handleRoute = handleRoute;


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
        if (window.__prksWorkCreateInFlight) return;
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

        const folderId = document.getElementById('work-folder-id').value;
        const videoUrlEl = document.getElementById('work-video-url');
        const videoChanEl = document.getElementById('work-video-channel');
        const videoPubEl = document.getElementById('work-video-published-date');
        const videoUrlDateEl = document.getElementById('work-video-urldate');
        const videoPlaylistEl = document.getElementById('work-video-playlist-id');
        const pdfSourceUrlEl = document.getElementById('work-pdf-source-url');
        let sourceUrl = '';
        if (sourceKind === 'video' && videoUrlEl) {
            sourceUrl = String(videoUrlEl.value || '').trim();
        } else if (sourceKind === 'pdf' && pdfSourceUrlEl) {
            sourceUrl = String(pdfSourceUrlEl.value || '').trim();
        }
        const publishedDate =
            sourceKind === 'video' && videoPubEl ? String(videoPubEl.value || '').trim() : '';
        const publishedIso =
            sourceKind === 'video' ? prksParsePublishedDateInput(publishedDate) : '';
        const workDateEl = document.getElementById('work-date');
        const pdfPublishedRaw =
            sourceKind === 'pdf' && workDateEl ? String(workDateEl.value || '').trim() : '';
        const pdfPublished =
            sourceKind === 'pdf' ? prksParsePublishedDateInput(pdfPublishedRaw) : '';

        if (typeof prksClearWorkModalErrors === 'function') prksClearWorkModalErrors();
        let firstInvalid = null;
        const setErr = (control, message, errorId) => {
            if (typeof prksSetWorkModalFieldError === 'function') {
                prksSetWorkModalFieldError(control, message, errorId);
            }
            if (!firstInvalid) firstInvalid = control;
        };
        if (sourceKind === 'pdf' && !pdfFileForUpload) {
            setErr(
                document.getElementById('upload-drop-zone'),
                'Choose a PDF file.',
                'work-file-error'
            );
        }
        if (sourceKind === 'video') {
            const validVideoUrl =
                typeof window.prksIsValidYoutubeUrl === 'function' &&
                window.prksIsValidYoutubeUrl(sourceUrl);
            if (!validVideoUrl) {
                setErr(videoUrlEl, 'Enter a valid YouTube URL.', 'work-video-url-error');
            }
        }
        const folderSearchEl = document.getElementById('work-folder-search');
        if (
            typeof window.prksIsWorkModalFolderCommitted === 'function' &&
            !window.prksIsWorkModalFolderCommitted()
        ) {
            setErr(
                folderSearchEl,
                'Choose a folder from the list, create this folder, or select Uncategorized.',
                'work-folder-error'
            );
        }
        if (sourceKind === 'video' && publishedDate && !publishedIso) {
            setErr(videoPubEl, 'Use dd/mm/yyyy.', 'work-video-published-date-error');
        }
        if (sourceKind === 'pdf' && pdfPublishedRaw && !pdfPublished) {
            setErr(workDateEl, 'Use dd/mm/yyyy.', 'work-date-error');
        }
        if (firstInvalid) {
            if (typeof prksFocusWorkModalControl === 'function') {
                prksFocusWorkModalControl(firstInvalid);
            } else if (firstInvalid.focus) {
                firstInvalid.focus();
            }
            return;
        }

        window.__prksWorkCreateInFlight = true;
        if (typeof prksSetWorkModalCreateBusy === 'function') prksSetWorkModalCreateBusy(true);

        try {
        if (pdfFileForUpload) {
            const file = pdfFileForUpload;
            fileName = file.name;
            try {
                await new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onload = e => { fileBase64 = e.target.result.split(',')[1]; resolve(); };
                    reader.onerror = () => reject(reader.error || new Error('File read error'));
                    reader.onabort = () => reject(new Error('File read aborted'));
                    reader.readAsDataURL(file);
                });
            } catch (_readErr) {
                if (typeof prksSetWorkModalFieldError === 'function') {
                    prksSetWorkModalFieldError(
                        document.getElementById('upload-drop-zone'),
                        'Could not read this PDF. Choose the file again.',
                        'work-file-error'
                    );
                }
                if (typeof prksFocusWorkModalControl === 'function') {
                    prksFocusWorkModalControl(document.getElementById('upload-drop-zone'));
                }
                return;
            }
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
        const playlistId =
            sourceKind === 'video' && videoPlaylistEl ? String(videoPlaylistEl.value || '').trim() : '';

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
            roles: uploadRoles,
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
        if (sourceKind === 'video') {
            payload.doc_type = 'online';
        }

        const statusMsg = document.getElementById('upload-status-msg');
        if (statusMsg) {
            statusMsg.textContent = '';
            statusMsg.classList.add('hidden');
        }

        let res;
        try {
            res = await prksRequest('/api/works', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
        } catch (e) {
            if (statusMsg) {
                statusMsg.textContent = 'Could not create the file. Try again.';
                statusMsg.classList.remove('hidden');
            }
            return;
        }
        const data = await res.json().catch(() => ({}));
        if (res.ok && String(payload.playlist_id || '').trim()) {
            // The create endpoint can attach the new video to a Playlist in the
            // same canonical request, bypassing addWorkToPlaylist(). The attach
            // is best-effort server-side, so invalidate whenever one was asked
            // for: if it succeeded the cache was stale, and if it did not this
            // costs one refetch. The new Work has no cached entity to evict.
            prksMarkPlaylistsDomainChanged();
        }
        if (res.ok && Array.isArray(payload.roles) && payload.roles.length) {
            prksMarkPersonGroupsDomainChanged();
            // The Work-create endpoint can create role links in the same
            // canonical request, bypassing POST /api/roles entirely -- so this
            // path owes People its own invalidation.
            if (typeof prksMarkPeopleDomainChanged === 'function') prksMarkPeopleDomainChanged();
        }
        if (res.ok && typeof prksMarkWorksBrowseChanged === 'function') {
            // A new Work enters the stable catalog and the top of Recently
            // added. It does NOT enter Recent: last_opened_at is still NULL,
            // which is one reason these are three independent projections.
            prksMarkWorksBrowseChanged();
            prksMarkRecentlyAddedChanged();
        }
        if (res.ok && typeof prksMarkFoldersDomainChanged === 'function') {
            // Unlike Playlists, folder membership is NOT optional: the create
            // endpoint files every new Work into the requested folder or into
            // the default "Uncategorized" one, so a folders:index work_count
            // (and possibly a cached Folder detail) always changes.
            prksMarkFoldersDomainChanged();
        }
        if (!res.ok) {
            const errText = data.error || 'Could not create the file.';
            if (statusMsg) {
                statusMsg.textContent = errText;
                statusMsg.classList.remove('hidden');
            }
            const lower = String(errText).toLowerCase();
            if (sourceKind === 'video' && videoUrlEl && lower.indexOf('url') !== -1) {
                if (typeof prksSetWorkModalFieldError === 'function') {
                    prksSetWorkModalFieldError(videoUrlEl, 'Enter a valid YouTube URL.', 'work-video-url-error');
                }
                if (typeof prksFocusWorkModalControl === 'function') prksFocusWorkModalControl(videoUrlEl);
            }
            return;
        }
        const newId = data.id;
        if (newId && typeof uploadTagsSelected !== 'undefined' && uploadTagsSelected.length) {
            for (const t of uploadTagsSelected) {
                try {
                    const tr = await prksRequest(`/api/works/${encodeURIComponent(newId)}/tags`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ tag_id: t.id }),
                    });
                    if (!tr.ok) throw new Error('tag attach failed');
                } catch (_e) {
                    if (statusMsg) {
                        statusMsg.textContent = 'File added, but one or more tags could not be attached.';
                        statusMsg.classList.remove('hidden');
                    }
                    break;
                }
            }
        }
        closeModals();
        if (newId && typeof prksNavigate === 'function') {
            prksNavigate('#/works/' + encodeURIComponent(newId));
        }
        } finally {
            window.__prksWorkCreateInFlight = false;
            const modal = document.getElementById('work-modal');
            const stillOpen = modal && !modal.classList.contains('hidden');
            if (stillOpen && typeof prksSetWorkModalCreateBusy === 'function') {
                prksSetWorkModalCreateBusy(false);
            }
        }
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
            if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) return;
            const ownerCtx =
                typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
            if (folderBtn.disabled) return;
            const payload = {
                title: document.getElementById('folder-title').value,
                description: document.getElementById('folder-description').value,
                parent_id: (() => {
                    const raw = (document.getElementById('folder-parent-id')?.value || '').trim();
                    return raw || null;
                })()
            };
            let newFolderId;
            try {
                newFolderId = await createFolder(payload.title, payload.description, {
                    parent_id: payload.parent_id,
                });
            } catch (e) {
                if (!prksOfflineWasGuardRefusal(e)) {
                    await prksAlertMessage((e && e.message) || 'Could not create folder', 'Could not save');
                }
                return;
            }
            const data = { id: newFolderId };
            const pending = window.__prksPendingWorkFolderAttach;
            if (pending && pending.workId && typeof patchWorkFolder === 'function') {
                const attachWid = String(pending.workId);
                window.__prksPendingWorkFolderAttach = null;
                closeModals();
                let attachCoherenceToken = null;
                try {
                    attachCoherenceToken = await patchWorkFolder(attachWid, data.id);
                } catch (e) {
                    await prksAlertMessage(
                        (e && e.message) || 'Folder created but could not assign this file.',
                        'Error'
                    );
                }
                if (typeof fetchWorkDetails === 'function') {
                    const _aw = await fetchWorkDetails(attachWid);
                    if (_aw && typeof prksOfflineCacheEntityIfCurrent === 'function' && attachCoherenceToken != null) {
                        void prksOfflineCacheEntityIfCurrent('work', attachWid, _aw, attachCoherenceToken);
                    }
                    if (typeof prksApplyOwnedWorkEntity === 'function' && prksApplyOwnedWorkEntity(ownerCtx, attachWid, _aw)) {
                        if (ownerCtx && ownerCtx.ui) ownerCtx.ui.workFolderEditing = false;
                        if (typeof prksTabContextIsFocused === 'function' ? prksTabContextIsFocused(ownerCtx) : false) {
                            updatePanelContent('details');
                        }
                    }
                }
                return;
            }
            closeModals();
            const ownerRoute = ownerCtx && (ownerCtx.lastResolvedRoute || ownerCtx.route);
            if (
                ownerRoute &&
                ownerRoute.name === 'folders' &&
                typeof fetchFolders === 'function' &&
                typeof renderDashboard === 'function'
            ) {
                const folders = await fetchFolders();
                if (ownerCtx && ownerCtx.root && ownerCtx.mounted) {
                    renderDashboard(folders, ownerCtx.root);
                }
                return;
            }
            window.location.reload();
        };
    }

    const playlistBtn = document.getElementById('save-playlist-btn');
    if (playlistBtn) {
        playlistBtn.onclick = async () => {
            if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) return;
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
                // Canonical wrapper: it owns the Playlists-domain invalidation
                // and re-guards connectivity, which can have dropped since the
                // modal opened.
                const newId = await createPlaylist(title, description);
                if (!newId) throw new Error('Could not create playlist');
                closeModals();
                // If a work is waiting to be attached, attach it now. The
                // wrapper also invalidates that Work's cached entity, whose
                // playlist_id/playlist_title just changed.
                const pending = window.__prksPendingPlaylistAttach;
                if (pending && pending.workId) {
                    try {
                        await addWorkToPlaylist(newId, pending.workId);
                    } catch (_e) {}
                    window.__prksPendingPlaylistAttach = null;
                }
                // Refresh select controls if mounted.
                if (typeof window.__prksRefreshPlaylistSelects === 'function') {
                    await window.__prksRefreshPlaylistSelects(newId);
                }
                if (typeof window.__prksRefreshAllPlaylistSelects === 'function') {
                    await window.__prksRefreshAllPlaylistSelects(newId);
                }
                // Navigate only when playlist creation came from the playlists index (not from New File flow).
                if (window.__prksReturnToWorkModalAfterPlaylist === true) {
                    // closeModals() will restore the New File modal.
                } else if ((window.location.hash || '') === '#/playlists') {
                    if (typeof prksNavigate === 'function') {
                        prksNavigate('#/playlists/' + encodeURIComponent(newId));
                    }
                }
            } catch (e) {
                // A blocked mutation already told the user why; a second
                // "Could not create playlist." would only muddy it.
                if (typeof prksPlaylistWasBlocked === 'function' && prksPlaylistWasBlocked(e)) return;
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
            // The modal was guarded when it opened; connectivity can change
            // while it is open, so re-check immediately before the POST.
            if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) return;
            if (typeof prksSetButtonBusy === 'function') prksSetButtonBusy(personBtn, true, { busyLabel: 'Saving…' });
            try {
                const res = await prksRequest('/api/persons', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    await prksAlertMessage(data.error || `Could not save person (${res.status})`, 'Could not save');
                    return;
                }
                if (typeof prksMarkPeopleDomainChanged === 'function') prksMarkPeopleDomainChanged();
            } catch (e) {
                await prksAlertMessage('Network error — could not save person.', 'Error');
                return;
            } finally {
                if (typeof prksSetButtonBusy === 'function') prksSetButtonBusy(personBtn, false);
            }
            closeModals(); window.location.reload();
        };
    }

    const saveGroupBtn = document.getElementById('save-group-btn');
    if (saveGroupBtn) {
        saveGroupBtn.onclick = async () => {
            if (typeof prksOfflineGuardMutation === 'function' &&
                prksOfflineGuardMutation('Creating a Person Group requires a connection to PRKS.')) return;
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
            if (typeof prksSetButtonBusy === 'function') prksSetButtonBusy(saveGroupBtn, true, { busyLabel: 'Creating…' });
            try {
                const { ok, data } = await createPersonGroup(payload);
                if (!ok) {
                    await prksAlertMessage(data.error || 'Could not create group.', 'Could not save');
                    return;
                }
                closeModals();
                if (typeof prksNavigate === 'function') {
                    prksNavigate('#/people/groups/' + (data.id || ''));
                }
            } catch (e) {
                await prksAlertMessage('Network error — could not create group.', 'Error');
            } finally {
                if (typeof prksSetButtonBusy === 'function') prksSetButtonBusy(saveGroupBtn, false);
            }
        };
    }

    const saveRoleBtn = document.getElementById('save-role-btn');
    saveRoleBtn.onclick = async () => {
        if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) return;
        const ownerCtx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
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
        const _cwDupCheck = typeof prksFocusedEntity === 'function' ? prksFocusedEntity('work') : null;
        if (
            typeof prksWorkHasRoleLink === 'function' &&
            _cwDupCheck &&
            String(_cwDupCheck.id) === String(work_id) &&
            prksWorkHasRoleLink(_cwDupCheck.roles, person_id, role_type)
        ) {
            if (typeof prksShowDuplicateRoleLinkAlert === 'function') {
                await prksShowDuplicateRoleLinkAlert(role_type);
            }
            return;
        }
        if (typeof prksSetButtonBusy === 'function') prksSetButtonBusy(saveRoleBtn, true, { busyLabel: 'Linking…' });
        let coherenceToken = null;
        try {
            const res = await prksRequest('/api/roles', {
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
            // Role links appear in cached Person read models (every role type) and
            // in cached Argument sources (Author only); the shared helper owns
            // both so every role surface stays consistent.
            coherenceToken =
                typeof prksMarkWorkRoleChanged === 'function'
                    ? prksMarkWorkRoleChanged(work_id, role_type)
                    : typeof prksOfflineMarkEntityChanged === 'function'
                      ? prksOfflineMarkEntityChanged('work', work_id)
                      : null;
        } catch (e) {
            console.error(e);
            if (typeof prksAlertDialog === 'function') {
                await prksAlertDialog({
                    title: 'Could not link',
                    message: 'Could not create link.',
                });
            }
            return;
        } finally {
            if (typeof prksSetButtonBusy === 'function') prksSetButtonBusy(saveRoleBtn, false);
        }
        closeModals();
        const expectedWork = ownerCtx && ownerCtx.getEntity ? ownerCtx.getEntity('work') : null;
        const ownsWork =
            expectedWork &&
            String(expectedWork.id) === String(work_id) &&
            typeof prksApplyOwnedWorkEntity === 'function';
        if (ownsWork && typeof fetchWorkDetails === 'function') {
            const _rw = await fetchWorkDetails(work_id);
            if (_rw && typeof prksOfflineCacheEntityIfCurrent === 'function' && coherenceToken != null) {
                void prksOfflineCacheEntityIfCurrent('work', work_id, _rw, coherenceToken);
            }
            if (prksApplyOwnedWorkEntity(ownerCtx, work_id, _rw)) {
                if (typeof prksReplaceFocusedWorkDetailsPanel === 'function') {
                    prksReplaceFocusedWorkDetailsPanel(ownerCtx, _rw);
                }
            }
        } else {
            window.location.reload();
        }
    };
}
