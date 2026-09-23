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

        // Thumbnail source-awareness: PDF vs video classes, no URL in DOM attrs.
        const pdfCard = cardHtml({ id: 'W-4', title: 'PDF Work', file_path: '/api/pdfs/w4.pdf' });
        assert('pdf card gets pdf thumb class', pdfCard.indexOf('work-card__thumb--pdf') !== -1);
        assert('pdf card has no video thumb class', pdfCard.indexOf('work-card__thumb--video') === -1);
        assert('pdf thumb starts in loading state', pdfCard.indexOf('work-card__thumb--loading') !== -1);
        assert(
            'pdf thumb marks preview kind (no URL attr)',
            pdfCard.indexOf('data-prks-thumb-preview-kind="pdf"') !== -1
        );
        assert(
            'pdf thumb carries digit page for rebuild',
            pdfCard.indexOf('data-prks-thumb-page="1"') !== -1
        );
        assert(
            'pdf markup has no URL-bearing preview-src attr',
            pdfCard.indexOf('data-prks-thumb-preview-src') === -1
        );
        assert(
            'pdf markup has no lazy URL attr',
            pdfCard.indexOf('data-prks-thumb-src') === -1 && pdfCard.indexOf('data-prks-thumb-lazy="1"') !== -1
        );

        const videoWork = { id: 'W-5', title: 'Video Work', source_kind: 'video', thumb_url: 'https://img.example/thumb.jpg' };
        const videoCard = cardHtml(videoWork);
        assert('video card gets video thumb class', videoCard.indexOf('work-card__thumb--video') !== -1);
        assert('video card has no pdf thumb class', videoCard.indexOf('work-card__thumb--pdf') === -1);
        assert(
            'video thumb uses lazy marker without embedding URL in markup',
            videoCard.indexOf('data-prks-thumb-lazy="1"') !== -1 &&
                videoCard.indexOf('https://img.example/thumb.jpg') === -1
        );
        assert(
            'no eager network request in markup',
            videoCard.indexOf(' src="https://img.example/thumb.jpg"') === -1
        );
        if (typeof root.prksLookupRegisteredWorkThumbUrl === 'function') {
            assertEq(
                'video thumb registered by work id (not DOM)',
                root.prksLookupRegisteredWorkThumbUrl('W-5'),
                'https://img.example/thumb.jpg'
            );
        }

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
                if (typeof root.prksWorkBrowseModeToggleHtml === 'function') {
                    const listToggle = root.prksWorkBrowseModeToggleHtml('prks-work-browse-mode-test');
                    assert(
                        'list mode checks only List radio',
                        listToggle.indexOf('data-value="Cards" aria-checked="false"') !== -1 &&
                            listToggle.indexOf('data-value="List" aria-checked="true"') !== -1
                    );
                }
                root.prksSetWorkBrowseMode('cards');
            }
            const toggle = typeof root.prksWorkBrowseModeToggleHtml === 'function'
                ? root.prksWorkBrowseModeToggleHtml('prks-work-browse-mode-test')
                : '';
            assert('mode toggle exposes radiogroup', toggle.indexOf('role="radiogroup"') !== -1);
            assert('mode toggle labels Cards and List', toggle.indexOf('Cards') !== -1 && toggle.indexOf('List') !== -1);
            assert(
                'mode toggle radios use aria-checked',
                toggle.indexOf('aria-checked=') !== -1 && toggle.indexOf('aria-pressed=') === -1
            );
            assert(
                'cards mode checks only Cards radio',
                toggle.indexOf('data-value="Cards" aria-checked="true"') !== -1 &&
                    toggle.indexOf('data-value="List" aria-checked="false"') !== -1
            );
        }

        if (typeof root.prksSafeWorkThumbSrc === 'function') {
            assertEq(
                'safe pdf thumb rebuilt',
                root.prksSafeWorkThumbSrc('/api/works/W-1/thumbnail?page=2'),
                '/api/works/W-1/thumbnail?page=2'
            );
            assertEq(
                'javascript thumb rejected',
                root.prksSafeWorkThumbSrc('javascript:alert(1)'),
                ''
            );
            assertEq(
                'data thumb rejected',
                root.prksSafeWorkThumbSrc('data:text/html,x'),
                ''
            );
            assertEq(
                'https video thumb allowed',
                root.prksSafeWorkThumbSrc('https://img.example/thumb.jpg'),
                'https://img.example/thumb.jpg'
            );
            assertEq(
                'relative non-thumb path rejected',
                root.prksSafeWorkThumbSrc('/api/pdfs/x.pdf'),
                ''
            );
        }

        // PDF resolve rebuilds from work-id + page without reading a URL attr.
        if (
            typeof root.prksResolveWorkThumbSrc === 'function' &&
            typeof document !== 'undefined' &&
            document.createElement
        ) {
            const card = document.createElement('div');
            card.className = 'project-card project-card--work-card';
            card.setAttribute('data-work-id', 'W-resolve');
            const thumb = document.createElement('div');
            thumb.className = 'work-card__thumb';
            thumb.setAttribute('data-prks-thumb-preview-kind', 'pdf');
            thumb.setAttribute('data-prks-thumb-page', '3');
            card.appendChild(thumb);
            assertEq(
                'resolve rebuilds PDF thumb from id+page',
                root.prksResolveWorkThumbSrc(thumb),
                '/api/works/W-resolve/thumbnail?page=3'
            );
            assert(
                'resolve does not require preview-src attr',
                !thumb.getAttribute('data-prks-thumb-preview-src')
            );

            const emptyThumb = document.createElement('div');
            emptyThumb.className = 'work-card__thumb work-card__thumb--empty';
            emptyThumb.setAttribute('data-prks-thumb-state', 'empty');
            card.appendChild(emptyThumb);
            assertEq(
                'empty thumb slot resolves to no URL',
                root.prksResolveWorkThumbSrc(emptyThumb),
                ''
            );
            const bare = document.createElement('div');
            bare.className = 'work-card__thumb';
            card.appendChild(bare);
            assertEq(
                'thumb without kind/page resolves to no URL',
                root.prksResolveWorkThumbSrc(bare),
                ''
            );
        }

        // Show → hide → show same URL must not leave a blank preview frame.
        if (
            typeof root.prksShowWorkThumbPreview === 'function' &&
            typeof root.prksHideWorkThumbPreview === 'function' &&
            typeof document !== 'undefined' &&
            document.body &&
            document.createElement
        ) {
            const card = document.createElement('div');
            card.className = 'project-card project-card--work-card';
            card.setAttribute('data-work-id', 'W-preview');
            const thumb = document.createElement('div');
            thumb.className = 'work-card__thumb work-card__thumb--ready';
            thumb.setAttribute('data-prks-thumb-preview-kind', 'pdf');
            thumb.setAttribute('data-prks-thumb-page', '1');
            thumb.setAttribute('data-prks-thumb-state', 'ready');
            card.appendChild(thumb);
            document.body.appendChild(card);

            root.prksShowWorkThumbPreview(thumb);
            let preview = document.getElementById('prks-work-thumb-preview');
            let img = preview && preview.querySelector('.work-card-preview__img');
            assert('first show creates preview', !!(preview && !preview.hidden));
            assert(
                'first show assigns img src',
                !!(img && img.src && String(img.src).indexOf('/thumbnail?page=1') !== -1)
            );

            root.prksHideWorkThumbPreview();
            preview = document.getElementById('prks-work-thumb-preview');
            img = preview && preview.querySelector('.work-card-preview__img');
            assert('hide marks preview hidden', !!(preview && preview.hidden));
            assert('hide clears img src', !(img && img.src));

            root.prksShowWorkThumbPreview(thumb);
            preview = document.getElementById('prks-work-thumb-preview');
            img = preview && preview.querySelector('.work-card-preview__img');
            assert('second show reopens preview', !!(preview && !preview.hidden));
            assert(
                'second show reassigns img src (same URL)',
                !!(img && img.src && String(img.src).indexOf('/thumbnail?page=1') !== -1)
            );

            // Scoped release: only dismiss when the owning subtree is torn down.
            if (typeof root.prksReleaseWorkThumbPreview === 'function') {
                const other = document.createElement('div');
                document.body.appendChild(other);
                root.prksReleaseWorkThumbPreview(other);
                preview = document.getElementById('prks-work-thumb-preview');
                assert(
                    'release other root leaves preview up',
                    !!(preview && !preview.hidden && window.__prksWorkThumbPreviewSource === thumb)
                );
                other.parentNode && other.parentNode.removeChild(other);

                root.prksReleaseWorkThumbPreview(card);
                preview = document.getElementById('prks-work-thumb-preview');
                assert('release owning root hides preview', !!(preview && preview.hidden));
                assert('release owning root clears source', window.__prksWorkThumbPreviewSource == null);

                // Detached source prune (unscoped): P then navigate-away analog.
                root.prksShowWorkThumbPreview(thumb);
                assert(
                    'show before detach',
                    !!(document.getElementById('prks-work-thumb-preview') &&
                        !document.getElementById('prks-work-thumb-preview').hidden)
                );
                if (card.parentNode) card.parentNode.removeChild(card);
                root.prksReleaseWorkThumbPreview();
                preview = document.getElementById('prks-work-thumb-preview');
                assert('detach+release hides preview', !!(preview && preview.hidden));
                assert('detach+release clears source', window.__prksWorkThumbPreviewSource == null);
            } else {
                root.prksHideWorkThumbPreview();
            }
        }

        // Lazy-thumb IntersectionObserver must not retain detached card trees.
        if (
            typeof root.prksInitLazyWorkThumbs === 'function' &&
            typeof root.prksReleaseLazyWorkThumbs === 'function' &&
            typeof document !== 'undefined' &&
            document.body &&
            document.createElement &&
            typeof root.IntersectionObserver === 'function'
        ) {
            const host = document.createElement('div');
            host.className = 'work-browse-collection';
            const thumb = document.createElement('div');
            thumb.className = 'work-card__thumb work-card__thumb--pdf';
            thumb.setAttribute('data-prks-thumb-preview-kind', 'pdf');
            thumb.setAttribute('data-prks-thumb-page', '2');
            const img = document.createElement('img');
            img.setAttribute('data-prks-thumb-lazy', '1');
            img.setAttribute('alt', '');
            thumb.appendChild(img);
            host.appendChild(thumb);
            document.body.appendChild(host);

            root.prksInitLazyWorkThumbs(host);
            const observed = root.__prksWorkThumbObserved;
            const obs = root.__prksWorkThumbObserver;
            assert('lazy init tracks observed set', !!(observed && observed.has(img)));
            assert('lazy init observes target', !!(obs && obs.targets && obs.targets.has(img)));
            assert('lazy init marks observing attr', img.getAttribute('data-prks-thumb-observing') === '1');

            // Explicit release while still connected (route/tab teardown path).
            root.prksReleaseLazyWorkThumbs(host);
            assert('release drops tracked entry', !(observed && observed.has(img)));
            assert('release unobserves target', !(obs && obs.targets && obs.targets.has(img)));
            assert(
                'release clears observing attr',
                img.getAttribute('data-prks-thumb-observing') == null ||
                    img.getAttribute('data-prks-thumb-observing') === ''
            );

            // Re-init, then detach without release — prune on next init must clean up.
            root.prksInitLazyWorkThumbs(host);
            assert('re-init tracks again', !!(observed && observed.has(img)));
            if (typeof document.body.removeChild === 'function') {
                document.body.removeChild(host);
            } else {
                host.parentNode = null;
            }
            assert('detached thumb reports not connected', img.isConnected === false);
            root.prksInitLazyWorkThumbs(document.body);
            assert('prune on init drops detached', !(observed && observed.has(img)));
            assert('prune on init unobserves detached', !(obs && obs.targets && obs.targets.has(img)));
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
