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

        assert('classic prksWorkCardHtml is retired', typeof root.prksWorkCardHtml !== 'function');

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
                root.prksReleaseWorkThumbPreview(document.body);
                preview = document.getElementById('prks-work-thumb-preview');
                assert('detach+scoped-release hides leftover preview', !!(preview && preview.hidden));
                assert('detach+scoped-release clears source', window.__prksWorkThumbPreviewSource == null);
            } else {
                root.prksHideWorkThumbPreview();
            }

            if (typeof document._dispatch === 'function') {
                const nativeCard = document.createElement('div');
                nativeCard.className = 'project-card project-card--work-card';
                nativeCard.setAttribute('data-work-id', 'W-preview-link');
                nativeCard.setAttribute('data-prks-route', '#/works/W-preview-link');
                const nativeLink = document.createElement('a');
                nativeLink.className = 'work-card__link';
                nativeLink.setAttribute('href', '#/works/W-preview-link');
                const nativeThumb = document.createElement('div');
                nativeThumb.className = 'work-card__thumb work-card__thumb--ready';
                nativeThumb.setAttribute('data-prks-thumb-preview-kind', 'pdf');
                nativeThumb.setAttribute('data-prks-thumb-page', '1');
                const nativeBody = document.createElement('div');
                nativeBody.className = 'work-card__body';
                const nativeTitle = document.createElement('div');
                nativeTitle.className = 'card-title';
                nativeBody.appendChild(nativeTitle);
                nativeLink.appendChild(nativeThumb);
                nativeLink.appendChild(nativeBody);
                nativeCard.appendChild(nativeLink);
                document.body.appendChild(nativeCard);

                document._dispatch('keydown', { key: 'p', target: nativeLink });
                let kbdPreview = document.getElementById('prks-work-thumb-preview');
                assert(
                    'P on native work-card__link shows preview',
                    !!(kbdPreview && !kbdPreview.hidden && window.__prksWorkThumbPreviewSource === nativeThumb)
                );

                root.prksHideWorkThumbPreview();
                document._dispatch('pointerover', { target: nativeTitle });
                kbdPreview = document.getElementById('prks-work-thumb-preview');
                assert(
                    'hover on card body does not show preview',
                    !!(kbdPreview && kbdPreview.hidden)
                );
                document._dispatch('pointerover', { target: nativeThumb });
                kbdPreview = document.getElementById('prks-work-thumb-preview');
                assert(
                    'hover on thumbnail shows preview',
                    !!(kbdPreview && !kbdPreview.hidden && window.__prksWorkThumbPreviewSource === nativeThumb)
                );

                const thumbInner = document.createElement('img');
                nativeThumb.appendChild(thumbInner);
                document._dispatch('pointerout', { target: nativeThumb, relatedTarget: thumbInner });
                kbdPreview = document.getElementById('prks-work-thumb-preview');
                assert(
                    'pointerout within the thumbnail keeps preview',
                    !!(kbdPreview && !kbdPreview.hidden && window.__prksWorkThumbPreviewSource === nativeThumb)
                );

                document._dispatch('pointerout', { target: nativeThumb, relatedTarget: kbdPreview });
                kbdPreview = document.getElementById('prks-work-thumb-preview');
                assert(
                    'pointerout into preview overlay keeps preview',
                    !!(kbdPreview && !kbdPreview.hidden && window.__prksWorkThumbPreviewSource === nativeThumb)
                );

                document._dispatch('pointerout', { target: nativeThumb, relatedTarget: nativeTitle });
                kbdPreview = document.getElementById('prks-work-thumb-preview');
                assert(
                    'pointerout from thumbnail to card body hides preview',
                    !!(kbdPreview && kbdPreview.hidden)
                );

                document._dispatch('pointerover', { target: nativeThumb });
                const outside = document.createElement('div');
                document.body.appendChild(outside);
                document._dispatch('pointerout', { target: nativeThumb, relatedTarget: outside });
                kbdPreview = document.getElementById('prks-work-thumb-preview');
                assert(
                    'pointerout leaving the thumbnail hides preview',
                    !!(kbdPreview && kbdPreview.hidden)
                );

                const select = document.createElement('div');
                select.className = 'work-card__select';
                nativeCard.appendChild(select);
                document._dispatch('pointerover', { target: select });
                kbdPreview = document.getElementById('prks-work-thumb-preview');
                assert(
                    'hover on bulk checkbox does not show preview',
                    !!(kbdPreview && kbdPreview.hidden)
                );

                if (typeof document.body.removeChild === 'function') {
                    document.body.removeChild(nativeCard);
                    document.body.removeChild(outside);
                }
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

        return { passed: passed, failed: failed, rows: rows };
    }

    root.prksRunWorkCardSelfTests = prksRunWorkCardSelfTests;

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { prksRunWorkCardSelfTests: prksRunWorkCardSelfTests };
    }
})(typeof window !== 'undefined' ? window : globalThis);
