/**
 * Lucide icon helpers for PRKS (vanilla JS + UMD lucide.createIcons).
 * Use prksIcon() in HTML strings; call prksRefreshIcons(root) after dynamic innerHTML.
 */
(function (global) {
    'use strict';

    /** Semantic aliases used across the app (value = Lucide icon name). */
    const PRKS_ICON = {
        search: 'search',
        plus: 'plus',
        folder: 'folder',
        folders: 'folders',
        inbox: 'inbox',
        clock: 'clock',
        library: 'library',
        clapperboard: 'clapperboard',
        users: 'users',
        user: 'user',
        penLine: 'pen-line',
        filePen: 'file-pen',
        clipboardList: 'clipboard-list',
        languages: 'languages',
        bookOpen: 'book-open',
        book: 'book',
        bookMarked: 'book-marked',
        link: 'link',
        filePlus: 'file-plus',
        settings: 'settings',
        menu: 'menu',
        info: 'info',
        fileText: 'file-text',
        play: 'play',
        chevronDown: 'chevron-down',
        chevronUp: 'chevron-up',
        chevronRight: 'chevron-right',
        chevronLeft: 'chevron-left',
        copy: 'copy',
        x: 'x',
        trash: 'trash-2',
        arrowRight: 'arrow-right',
        arrowUp: 'arrow-up',
        arrowDown: 'arrow-down',
        pencil: 'pencil',
        check: 'check',
        bookmark: 'bookmark',
    };

    function prksTagSearchIconHtml() {
        return `<span class="tag-add-shell__icon">${prksIcon('search')}</span>`;
    }

    function prksTagPlusIconHtml() {
        return `<span class="tag-add-shell__icon">${prksIcon('plus')}</span>`;
    }

    function prksPageHeaderIconHtml(name) {
        return prksIcon(name, { size: 22, className: 'prks-page-header-icon' });
    }

    /** Same Lucide keys as sidebar People role filters (index.html nav-link--sub). */
    const PRKS_ROLE_TYPE_ICON = {
        Author: 'penLine',
        Editor: 'filePen',
        Reviewer: 'clipboardList',
        Translator: 'languages',
        Introduction: 'bookMarked',
        Foreword: 'bookOpen',
        Afterword: 'book',
        Mentioned: 'user',
    };

    /** Progress/status icon mapping used in sidebar, badges, segmented controls. */
    const PRKS_PROGRESS_STATUS_ICON = {
        'Not Started': 'circle',
        Planned: 'clipboard-list',
        'In Progress': 'play',
        Paused: 'pause',
        Completed: 'check',
    };

    function prksRoleTypeIconHtml(roleType, opts) {
        const key = PRKS_ROLE_TYPE_ICON[String(roleType || '').trim()];
        return key ? prksIcon(key, opts) : '';
    }

    function prksRoleTypeShortLabel(roleType) {
        return String(roleType || '').trim();
    }

    function prksProgressStatusIconHtml(status, opts) {
        const key = String(status || '').trim();
        const iconName = PRKS_PROGRESS_STATUS_ICON[key];
        if (!iconName) return '';
        return prksIcon(iconName, opts);
    }

    function prksResolveIconName(name) {
        if (!name) return '';
        if (PRKS_ICON[name] != null) return PRKS_ICON[name];
        return String(name);
    }

    /**
     * @param {string} name Lucide icon name or PRKS_ICON key
     * @param {{ className?: string, size?: number|'sm'|'md'|'lg'|'ribbon'|'nav' }} [opts]
     * @returns {string}
     */
    function prksIcon(name, opts) {
        const o = opts && typeof opts === 'object' ? opts : {};
        const lucideName = prksResolveIconName(name);
        if (!lucideName) return '';
        const parts = ['prks-icon'];
        if (o.className) parts.push(String(o.className));
        if (o.size === 'sm') parts.push('prks-icon--sm');
        else if (o.size === 'md') parts.push('prks-icon--md');
        else if (o.size === 'lg') parts.push('prks-icon--lg');
        else if (o.size === 'ribbon') parts.push('prks-icon--ribbon');
        else if (o.size === 'nav') parts.push('prks-icon--nav');
        else if (o.size === 'navSub') parts.push('prks-icon--nav-sub');
        const cls = parts.join(' ');
        let style = '';
        if (typeof o.size === 'number' && o.size > 0) {
            style = ` style="--prks-icon-size:${o.size}px"`;
        }
        return (
            `<i data-lucide="${lucideName}" class="${cls}" aria-hidden="true"${style}></i>`
        );
    }

    /**
     * SVG data URI for Cytoscape background-image. Empty when Lucide/DOM missing.
     * @param {string} name Lucide icon name or PRKS_ICON key
     * @param {string} [stroke] CSS color
     * @returns {string}
     */
    function prksLucideSvgDataUri(name, stroke) {
        const lucide = global.lucide;
        if (typeof document === 'undefined' || !lucide || typeof lucide.createElement !== 'function') {
            return '';
        }
        const lucideName = prksResolveIconName(name);
        const pascal = String(lucideName)
            .split('-')
            .map(function (part) {
                return part ? part.charAt(0).toUpperCase() + part.slice(1) : '';
            })
            .join('');
        const iconNode = (lucide.icons && lucide.icons[pascal]) || lucide[pascal];
        if (!iconNode) return '';
        let svg = null;
        try {
            svg = lucide.createElement(iconNode, {
                xmlns: 'http://www.w3.org/2000/svg',
                width: '64',
                height: '64',
                viewBox: '-3 -3 30 30',
                stroke: stroke || 'currentColor',
                fill: 'none',
                'stroke-width': '2.4',
                'stroke-linecap': 'round',
                'stroke-linejoin': 'round',
            });
        } catch (_e) {
            return '';
        }
        if (!svg || !svg.outerHTML) return '';
        return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg.outerHTML);
    }

    /**
     * Replace data-lucide placeholders with SVG under root (or whole document).
     * @param {Element|Document|undefined} root
     */
    function prksRefreshIcons(root) {
        const lucide = global.lucide;
        if (!lucide || typeof lucide.createIcons !== 'function') return;
        const nodes = [];
        if (root && root.nodeType === 1) {
            nodes.push(root);
        } else if (root && root.nodeType === 9) {
            nodes.push(root.documentElement || root.body);
        }
        const attrs = {
            'stroke-width': 2,
            'aria-hidden': 'true',
        };
        if (nodes.length) {
            lucide.createIcons({ nodes, attrs });
        } else {
            lucide.createIcons({ attrs });
        }
    }

    global.PRKS_ICON = PRKS_ICON;
    global.PRKS_ROLE_TYPE_ICON = PRKS_ROLE_TYPE_ICON;
    global.PRKS_PROGRESS_STATUS_ICON = PRKS_PROGRESS_STATUS_ICON;
    global.prksIcon = prksIcon;
    global.prksRoleTypeIconHtml = prksRoleTypeIconHtml;
    global.prksRoleTypeShortLabel = prksRoleTypeShortLabel;
    global.prksProgressStatusIconHtml = prksProgressStatusIconHtml;
    global.prksRefreshIcons = prksRefreshIcons;
    global.prksTagSearchIconHtml = prksTagSearchIconHtml;
    global.prksTagPlusIconHtml = prksTagPlusIconHtml;
    global.prksPageHeaderIconHtml = prksPageHeaderIconHtml;
    global.prksLucideSvgDataUri = prksLucideSvgDataUri;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => prksRefreshIcons());
    } else {
        prksRefreshIcons();
    }
})(typeof window !== 'undefined' ? window : globalThis);
