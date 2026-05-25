/**
 * Calendar dates: user-facing dd/mm/yyyy, API/storage YYYY-MM-DD.
 */
(function (global) {
    'use strict';

    const PRKS_DATE_DD_MM_YYYY_RE = /^(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{4})$/;
    const PRKS_DATE_ISO_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

    function prksDaysInMonth(year, month) {
        if (month < 1 || month > 12) return 0;
        const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
        const maxDay = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
        return maxDay[month - 1];
    }

    function prksIsoToDdMmYyyy(iso) {
        const s = String(iso || '').trim();
        if (!s) return '';
        const m = s.match(PRKS_DATE_ISO_RE);
        if (!m) return s;
        const yyyy = Number(m[1]);
        const mm = Number(m[2]);
        const dd = Number(m[3]);
        if (!Number.isFinite(yyyy) || !Number.isFinite(mm) || !Number.isFinite(dd)) return '';
        if (mm < 1 || mm > 12 || dd < 1 || dd > prksDaysInMonth(yyyy, mm)) return '';
        return `${String(dd).padStart(2, '0')}/${String(mm).padStart(2, '0')}/${yyyy}`;
    }

    function prksParseDdMmYyyyToIso(raw) {
        const s = String(raw || '').trim();
        if (!s) return '';
        const iso = s.match(PRKS_DATE_ISO_RE);
        if (iso) {
            const yyyy = Number(iso[1]);
            const mm = Number(iso[2]);
            const dd = Number(iso[3]);
            if (!Number.isFinite(yyyy) || !Number.isFinite(mm) || !Number.isFinite(dd)) return '';
            if (mm < 1 || mm > 12 || dd < 1 || dd > prksDaysInMonth(yyyy, mm)) return '';
            return `${yyyy}-${String(mm).padStart(2, '0')}-${String(dd).padStart(2, '0')}`;
        }
        const m = s.match(PRKS_DATE_DD_MM_YYYY_RE);
        if (!m) return '';
        const dd = Number(m[1]);
        const mm = Number(m[2]);
        const yyyy = Number(m[3]);
        if (!Number.isFinite(dd) || !Number.isFinite(mm) || !Number.isFinite(yyyy)) return '';
        if (yyyy < 0 || mm < 1 || mm > 12 || dd < 1 || dd > prksDaysInMonth(yyyy, mm)) return '';
        return `${yyyy}-${String(mm).padStart(2, '0')}-${String(dd).padStart(2, '0')}`;
    }

    function prksFormatPublishedForDisplay(stored) {
        const s = String(stored || '').trim();
        if (!s) return '';
        const formatted = prksIsoToDdMmYyyy(s);
        return formatted || s;
    }

    function prksParsePublishedDateInput(raw) {
        return prksParseDdMmYyyyToIso(raw);
    }

    global.prksIsoToDdMmYyyy = prksIsoToDdMmYyyy;
    global.prksParseDdMmYyyyToIso = prksParseDdMmYyyyToIso;
    global.prksFormatPublishedForDisplay = prksFormatPublishedForDisplay;
    global.prksParsePublishedDateInput = prksParsePublishedDateInput;
})(typeof window !== 'undefined' ? window : globalThis);
