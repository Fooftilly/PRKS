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
        typeof prksTabContextHost === 'function'
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
        return window.confirm(
            'PDF annotation sync still running. Leave page before all changes save to server?'
        );
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
    prksMaybeFlushPdfLastPageOnRouteChange(prevHash, route.hash);
    if (prevRoute && prevRoute.canonicalHash && prevRoute.canonicalHash !== route.canonicalHash) {
        if (typeof prksCaptureCurrentRouteState === 'function') prksCaptureCurrentRouteState(prevRoute, ctx);
        if (!workspaceSwitch && !fromPopstate && route.detail && typeof prksRememberOrigin === 'function') {
            prksRememberOrigin(route, prevRoute, ctx);
        }
    }

    if (typeof prksFlushPendingWorkResearchNotes === 'function') {
        prksFlushPendingWorkResearchNotes(ctx);
    }

    const contentDiv = ctx.root;
    if (!contentDiv) return;

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
                const folders = await fetchFolders({ signal: routeSignal });
                if (stale()) return;
                publishSidebar({ folderCount: folders.length });
                renderDashboard(folders, contentDiv);
                break;
            }
            case 'playlists': {
                if (typeof fetchPlaylists === 'function' && typeof renderPlaylistsIndex === 'function') {
                    const pls = await fetchPlaylists({ signal: routeSignal });
                    if (stale()) return;
                    renderPlaylistsIndex(pls, contentDiv);
                } else {
                    contentDiv.innerHTML =
                        '<div class="prks-page-header page-header"><h2 class="prks-page-title">Playlists</h2></div><p class="meta-row">Playlist UI unavailable.</p>';
                }
                break;
            }
            case 'playlist-detail': {
                const plId = route.params.playlistId;
                if (typeof fetchPlaylistDetails === 'function' && typeof renderPlaylistDetail === 'function') {
                    const pl = await fetchPlaylistDetails(plId, { signal: routeSignal });
                    if (stale()) return;
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
                    renderPlaylistDetail(pl, contentDiv);
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
                const folder = await fetchFolderDetails(route.params.folderId, { signal: routeSignal });
                if (stale()) return;
                renderFolderDetails(folder, contentDiv);
                titleOpts = folder
                    ? { entityTitle: folder.title || 'Folder' }
                    : { notFound: true, notFoundTitle: 'Folder not found' };
                break;
            }
            case 'people': {
                const persons = await fetchPersons({ signal: routeSignal });
                if (stale()) return;
                renderPeopleList(persons, contentDiv);
                break;
            }
            case 'people-role': {
                const roleFilter = route.params.knownRole ? route.params.role : null;
                const persons = await fetchPersons({ signal: routeSignal });
                if (stale()) return;
                publishSidebar({ role: roleFilter || route.params.role || 'Unknown role' });
                if (roleFilter) {
                    renderPeopleList(persons, contentDiv, { roleFilter });
                } else {
                    contentDiv.innerHTML =
                        '<div class="prks-page-header page-header"><h2 class="prks-page-title">People</h2></div><p class="prks-inline-message">Unknown role filter.</p>';
                }
                break;
            }
            case 'people-groups': {
                const groups = await fetchPersonGroups({ signal: routeSignal });
                if (stale()) return;
                publishSidebar({ groupCount: Array.isArray(groups) ? groups.length : 0 });
                renderPersonGroupsPage(groups, contentDiv);
                break;
            }
            case 'person-group-detail': {
                const group = route.params.groupId ? await fetchPersonGroupDetails(route.params.groupId, { signal: routeSignal }) : null;
                if (stale()) return;
                if (!group) {
                    contentDiv.innerHTML =
                        '<div class="prks-page-header page-header"><h2 class="prks-page-title">Group not found</h2></div><p class="meta-row"><a href="#/people/groups" class="route-sidebar__link">Back to groups</a></p>';
                    titleOpts = { notFound: true, notFoundTitle: 'Group not found' };
                } else {
                    ctx.setEntity('personGroup', group);
                    publishSidebar({
                        groupName: group.name,
                        memberCount: Array.isArray(group.members) ? group.members.length : 0,
                        subgroupCount: Array.isArray(group.children) ? group.children.length : 0,
                    });
                    renderPersonGroupDetail(group, contentDiv);
                    titleOpts = { entityTitle: group.name || 'Group' };
                }
                break;
            }
            case 'recent': {
                const works = await fetchRecent({ signal: routeSignal });
                if (stale()) return;
                publishSidebar({ workCount: works.length });
                renderRecent(works, contentDiv);
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
                const works = await fetchWorks({ signal: routeSignal });
                if (stale()) return;
                publishSidebar({ status });
                renderProgressByStatus(works, status, contentDiv);
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
                const works = await fetchWorks({ signal: routeSignal });
                if (stale()) return;
                renderTypesIndex(works, contentDiv);
                break;
            }
            case 'type-detail': {
                const works = await fetchWorks({ signal: routeSignal });
                if (stale()) return;
                renderWorksByDocType(works, route.params.docType, contentDiv);
                break;
            }
            case 'work': {
                const work = await fetchWorkDetails(route.params.workId, { signal: routeSignal });
                if (stale()) return;
                await renderWorkDetails(ctx, work, { generation: generation, signal: routeSignal });
                if (stale()) return;
                titleOpts = work
                    ? { entityTitle: String(work.title || '').trim() || 'File' }
                    : { notFound: true, notFoundTitle: 'File not found' };
                break;
            }
            case 'concepts': {
                const items = typeof fetchConcepts === 'function' ? await fetchConcepts({ signal: routeSignal }) : [];
                if (stale()) return;
                if (typeof renderConceptsIndex === 'function') renderConceptsIndex(items, contentDiv);
                else contentDiv.innerHTML = '<div class="prks-page-header page-header"><h2 class="prks-page-title">Concepts</h2></div>';
                break;
            }
            case 'concept-detail': {
                const item = typeof fetchConcept === 'function' ? await fetchConcept(route.params.conceptId, { signal: routeSignal }) : null;
                if (stale()) return;
                if (!item) {
                    if (typeof renderConceptNotFound === 'function') renderConceptNotFound(contentDiv);
                    else contentDiv.innerHTML = '<div class="prks-page-header page-header"><h2 class="prks-page-title">Concept not found.</h2></div>';
                    titleOpts = { notFound: true, notFoundTitle: 'Concept not found' };
                } else {
                    if (typeof renderConceptDetail === 'function') renderConceptDetail(item, contentDiv);
                    titleOpts = { entityTitle: item.name || 'Concept' };
                }
                break;
            }
            case 'positions': {
                const items = typeof fetchPositions === 'function' ? await fetchPositions({ signal: routeSignal }) : [];
                if (stale()) return;
                if (typeof renderPositionsIndex === 'function') renderPositionsIndex(items, contentDiv);
                else contentDiv.innerHTML = '<div class="prks-page-header page-header"><h2 class="prks-page-title">Positions</h2></div>';
                break;
            }
            case 'position-detail': {
                const item = typeof fetchPosition === 'function' ? await fetchPosition(route.params.positionId, { signal: routeSignal }) : null;
                if (stale()) return;
                if (!item) {
                    if (typeof renderPositionNotFound === 'function') renderPositionNotFound(contentDiv);
                    else contentDiv.innerHTML = '<div class="prks-page-header page-header"><h2 class="prks-page-title">Position not found.</h2></div>';
                    titleOpts = { notFound: true, notFoundTitle: 'Position not found' };
                } else {
                    if (typeof renderPositionDetail === 'function') renderPositionDetail(item, contentDiv);
                    titleOpts = { entityTitle: item.name || 'Position' };
                }
                break;
            }
            case 'arguments': {
                const kind = route.params.kind || '';
                const items = typeof fetchArguments === 'function' ? await fetchArguments(kind || undefined, { signal: routeSignal }) : [];
                if (stale()) return;
                if (typeof renderArgumentsIndex === 'function') renderArgumentsIndex(items, contentDiv, kind || 'all');
                else contentDiv.innerHTML = '<div class="prks-page-header page-header"><h2 class="prks-page-title">Arguments &amp; Stances</h2></div>';
                break;
            }
            case 'argument-detail': {
                const item = typeof fetchArgument === 'function' ? await fetchArgument(route.params.argumentId, { signal: routeSignal }) : null;
                if (stale()) return;
                if (!item) {
                    if (typeof renderArgumentNotFound === 'function') renderArgumentNotFound(contentDiv);
                    else contentDiv.innerHTML = '<div class="prks-page-header page-header"><h2 class="prks-page-title">Argument not found.</h2></div>';
                    titleOpts = { notFound: true, notFoundTitle: 'Argument not found' };
                } else {
                    if (typeof renderArgumentDetail === 'function') renderArgumentDetail(item, contentDiv);
                    titleOpts = { entityTitle: item.name || 'Argument' };
                }
                break;
            }
            case 'research-graph': {
                if (typeof renderResearchGraph === 'function') {
                    await renderResearchGraph(contentDiv, {
                        ctx: ctx,
                        focus: route.params.focus || '',
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
                const person = await fetchPersonDetails(route.params.personId, { signal: routeSignal });
                if (stale()) return;
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
                renderPersonDetails(person, contentDiv);
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
                updatePanelContent('details');
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
        if (typeof prksRenderRouteError === 'function') prksRenderRouteError(contentDiv);
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

    const apiErr = typeof window.prksConsumeApiError === 'function' ? window.prksConsumeApiError() : null;
    if (apiErr && contentDiv && !contentDiv.querySelector('#prks-route-retry')) {
        const bar = document.createElement('div');
        bar.className = 'api-warning-banner';
        bar.setAttribute('role', 'status');
        bar.textContent = apiErr.message || 'Some data could not be loaded.';
        contentDiv.prepend(bar);
    }

    const isShell =
        typeof prksIsMainTabContext === 'function' ? prksIsMainTabContext(ctx) : true;
    const onWorkDetailPage = route.name === 'work' && ctx.getEntity('work');
    if (isShell && !onWorkDetailPage) {
        updatePanelContent(getActiveRightPanelTab());
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
            res = await prksRequest('/api/works', {
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
                    const tr = await prksRequest(`/api/works/${encodeURIComponent(newId)}/tags`, {
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
            const res = await prksRequest('/api/folders', {
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
                    const _aw = await fetchWorkDetails(attachWid);
                    if (typeof prksSetFocusedEntity === 'function') prksSetFocusedEntity('work', _aw);
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
                const res = await prksRequest('/api/playlists', {
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
                        await prksRequest(`/api/playlists/${encodeURIComponent(data.id)}/items`, {
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
                    if (typeof prksNavigate === 'function') {
                        prksNavigate('#/playlists/' + encodeURIComponent(data.id));
                    }
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
            const res = await prksRequest('/api/person-groups', {
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
            if (typeof prksNavigate === 'function') {
                prksNavigate('#/people/groups/' + (data.id || ''));
            }
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
        const workIdFromHash =
            typeof prksParseRoute === 'function'
                ? prksParseRoute(hash).name === 'work'
                    ? prksParseRoute(hash).params.workId || ''
                    : ''
                : hash.startsWith('#/works/')
                  ? hash.split('/')[2]
                  : '';
        const _cwApp = typeof prksFocusedEntity === 'function' ? prksFocusedEntity('work') : null;
        const onThisWork =
            workIdFromHash &&
            String(workIdFromHash) === String(work_id) &&
            _cwApp &&
            String(_cwApp.id) === String(work_id);
        if (onThisWork && typeof fetchWorkDetails === 'function') {
            const _rw = await fetchWorkDetails(work_id);
            if (typeof prksSetFocusedEntity === 'function') prksSetFocusedEntity('work', _rw);
            const panel = document.getElementById('panel-content');
            const tab = typeof getActiveRightPanelTab === 'function' ? getActiveRightPanelTab() : 'details';
            if (panel && tab === 'details' && typeof prksWorkRightPanelStackHtml === 'function') {
                panel.innerHTML = prksWorkRightPanelStackHtml(_rw, false);
                if (typeof initPrksPrivateNotesEditor === 'function') {
                    initPrksPrivateNotesEditor('work', _rw.id);
                }
                if (typeof initWorkTagCombobox === 'function') initWorkTagCombobox(_rw.id);
                if (typeof initWorkDetailRightPanelActions === 'function') {
                    initWorkDetailRightPanelActions(_rw);
                }
            }
        } else {
            window.location.reload();
        }
    };
}

