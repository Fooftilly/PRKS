/**
 * Work-card self-tests. Load after frontend/js/components/work-cards.js.
 * Node and (optional) browser fixtures both call prksRunWorkCardSelfTests().
 */
(function (root) {
    'use strict';

    function prksRunWorkCardSelfTests() {
        const rows = [];
        let passed = 0;
        let failed = 0;

        function record(name, ok, detail) {
            rows.push({ name: name, ok: !!ok, detail: detail || '' });
            if (ok) passed += 1;
            else failed += 1;
        }

        function assert(name, cond, detail) {
            record(name, !!cond, detail);
        }

        function assertEq(name, got, want) {
            const ok = got === want;
            record(name, ok, ok ? '' : 'got ' + JSON.stringify(got) + ' want ' + JSON.stringify(want));
        }

        const cardHtml = root.prksWorkCardHtml;
        if (typeof cardHtml !== 'function') {
            record('prksWorkCardHtml exists', false, 'missing');
            return { passed: passed, failed: failed + 1, rows: rows };
        }

        // Bibliographic identity vs. contextual metadata: distinct areas, not concatenated.
        const pdfWork = {
            id: 'W-1',
            title: 'The Culture Industry',
            primary_author: 'Theodor W. Adorno',
            year: '1972',
            status: 'In Progress',
            doc_type: 'article',
            file_size_bytes: 1024 * 1024,
            file_path: '/api/pdfs/w1.pdf',
        };
        const withContext = cardHtml(pdfWork, { subtitle: 'Added Sep 5, 2026' });
        assert('meta line present', withContext.indexOf('work-card__meta') !== -1);
        assert('context line present', withContext.indexOf('work-card__context') !== -1);
        assert('meta line has author + year', /work-card__meta">Author: Theodor W\. Adorno · 1972</.test(withContext));
        assert(
            'context text not concatenated into meta line',
            !/work-card__meta">[^<]*Added Sep 5, 2026/.test(withContext)
        );
        assert('context line carries subtitle text', /work-card__context">Added Sep 5, 2026</.test(withContext));

        const noContext = cardHtml({ id: 'W-2', title: 'No Subtitle', year: '2001' });
        assert('no context line when subtitle omitted', noContext.indexOf('work-card__context') === -1);

        // Cache validation rejects these shapes. Card helper still fails safe
        // when called directly with imperfect data from another surface.
        let malformedYearCard = '';
        let malformedDateCard = '';
        let malformedCardError = '';
        try {
            malformedYearCard = cardHtml({ id: 'W-bad-year', title: 'Bad year', year: [] });
            malformedDateCard = cardHtml({ id: 'W-bad-date', title: 'Bad date', published_date: {} });
        } catch (err) {
            malformedCardError = String(err && err.message ? err.message : err);
        }
        assertEq('malformed year/date do not throw', malformedCardError, '');
        assert('malformed year omitted', malformedYearCard.indexOf('work-card__meta') === -1);
        assert('malformed published date omitted', malformedDateCard.indexOf('work-card__meta') === -1);

        // Title clamp contract: full title kept via title attribute, not truncated.
        const longTitle =
            'A Very Long Work Title That Would Otherwise Break Card Height Consistency Across A Dense Grid Layout';
        const longCard = cardHtml({ id: 'W-3', title: longTitle });
        assert('full title present in DOM text', longCard.indexOf(longTitle) !== -1);
        assert('full title exposed via title attribute', longCard.indexOf('title="' + longTitle + '"') !== -1);

        // Thumbnail source-awareness: PDF vs video classes, no extra network call.
        const pdfCard = cardHtml({ id: 'W-4', title: 'PDF Work', file_path: '/api/pdfs/w4.pdf' });
        assert('pdf card gets pdf thumb class', pdfCard.indexOf('work-card__thumb--pdf') !== -1);
        assert('pdf card has no video thumb class', pdfCard.indexOf('work-card__thumb--video') === -1);
        assert('pdf thumb starts in loading state', pdfCard.indexOf('work-card__thumb--loading') !== -1);
        assert(
            'pdf thumb carries preview src for quick preview',
            pdfCard.indexOf('data-prks-thumb-preview-src="/api/works/W-4/thumbnail?page=1"') !== -1
        );

        const videoWork = { id: 'W-5', title: 'Video Work', source_kind: 'video', thumb_url: 'https://img.example/thumb.jpg' };
        const videoCard = cardHtml(videoWork);
        assert('video card gets video thumb class', videoCard.indexOf('work-card__thumb--video') !== -1);
        assert('video card has no pdf thumb class', videoCard.indexOf('work-card__thumb--pdf') === -1);
        assert('video thumb uses provided thumb_url as lazy src', videoCard.indexOf('data-prks-thumb-src="https://img.example/thumb.jpg"') !== -1);
        assert(
            'no eager network request in markup — real URL only in the lazy data attribute',
            videoCard.indexOf(' src="https://img.example/thumb.jpg"') === -1
        );

        // Empty thumbnail fallback is source-appropriate, not a blanket "PDF".
        const emptyVideoCard = cardHtml({ id: 'W-6', title: 'Video no thumb', source_kind: 'video' });
        assert('empty video card marked empty', emptyVideoCard.indexOf('work-card__thumb--empty') !== -1);
        assert('empty video card keeps video class for CSS-driven label', emptyVideoCard.indexOf('work-card__thumb--video') !== -1);

        const emptyPdfCard = cardHtml({ id: 'W-7', title: 'PDF no thumb' });
        assert('empty pdf card marked empty', emptyPdfCard.indexOf('work-card__thumb--empty') !== -1);
        assert('empty pdf card keeps pdf class for CSS-driven label', emptyPdfCard.indexOf('work-card__thumb--pdf') !== -1);

        const suppressed = cardHtml(
            { id: 'W-8', title: 'Offline PDF', file_path: '/api/pdfs/w8.pdf' },
            { suppressThumbnail: true }
        );
        assert('suppressThumbnail yields empty slot', suppressed.indexOf('work-card__thumb--empty') !== -1);
        assert('suppressThumbnail does not embed thumb URL', suppressed.indexOf('/thumbnail') === -1);

        // Browse mode preference helpers (default cards; list is opt-in).
        if (typeof root.prksGetWorkBrowseMode === 'function') {
            assertEq('default browse mode is cards', root.prksGetWorkBrowseMode(), 'cards');
            const listClass = root.prksWorkBrowseCollectionClass();
            assert('cards collection includes card-grid', listClass.indexOf('card-grid') !== -1);
            if (typeof root.prksSetWorkBrowseMode === 'function' && typeof root.localStorage !== 'undefined') {
                root.prksSetWorkBrowseMode('list');
                assertEq('set list mode', root.prksGetWorkBrowseMode(), 'list');
                const listColl = root.prksWorkBrowseCollectionClass();
                assert('list collection class', listColl.indexOf('work-browse-collection--list') !== -1);
                assert('list collection drops card-grid', listColl.indexOf('card-grid') === -1);
                root.prksSetWorkBrowseMode('cards');
            }
            const toggle = typeof root.prksWorkBrowseModeToggleHtml === 'function'
                ? root.prksWorkBrowseModeToggleHtml('prks-work-browse-mode-test')
                : '';
            assert('mode toggle exposes radiogroup', toggle.indexOf('role="radiogroup"') !== -1);
            assert('mode toggle labels Cards and List', toggle.indexOf('Cards') !== -1 && toggle.indexOf('List') !== -1);
        }

        // Thumbnails stay decorative — no redundant screen-reader announcement of the title.
        assert('thumb image alt is empty', /alt=""/.test(pdfCard));
        assert('thumb image alt does not repeat title', pdfCard.indexOf('alt="PDF Work"') === -1);

        // Status/type/size stay at the bottom, after title + meta + context.
        const fullCard = cardHtml(
            { ...pdfWork, doc_type: 'book' },
            { subtitle: 'Last opened: Sep 5, 2026, 14:20' }
        );
        const titleAt = fullCard.indexOf('card-title');
        const metaAt = fullCard.indexOf('work-card__meta');
        const contextAt = fullCard.indexOf('work-card__context');
        const badgesAt = fullCard.indexOf('work-card__badges');
        assert('title precedes meta', titleAt < metaAt);
        assert('meta precedes context', metaAt < contextAt);
        assert('context precedes badges', contextAt < badgesAt);

        return { passed: passed, failed: failed, rows: rows };
    }

    root.prksRunWorkCardSelfTests = prksRunWorkCardSelfTests;

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { prksRunWorkCardSelfTests: prksRunWorkCardSelfTests };
    }
})(typeof window !== 'undefined' ? window : globalThis);
