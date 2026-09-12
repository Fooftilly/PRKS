/* Settings -> Diagnostics: the durable queue, for every operation family.
 *
 * This exists because the disposable cache and the durable queue have separate
 * lifecycles. Clearing the cache can leave a conflict whose Work page is no
 * longer available at all, and a change the user cannot reach is a change they
 * cannot decide about -- so every unsynchronized operation is listed and
 * resolvable here, with no navigation to the entity required.
 */
(function (root) {
    'use strict';

    function status(op) {
        if (op.status === 'conflict') return 'Needs a decision';
        if (op.status === 'syncing') return 'Syncing…';
        if (op.last_error) return 'Sync failed · retry scheduled';
        return root.prksOfflineRuntimeState() === 'online' ? 'Waiting to sync' : 'Offline · saved locally';
    }

    /** What the user actually changed, in their words rather than the protocol's. */
    function describe(op) {
        const context = op.local_context || {};
        if (op.operation === 'MARK_WORK_OPENED') {
            return 'Opened ' + ((context.recent_item && context.recent_item.title) || 'a file');
        }
        if (op.operation === 'SET_WORK_METADATA_FIELD') {
            const labels = root.PRKS_SYNCED_WORK_FIELD_LABELS || {};
            const field = op.payload.field;
            return (labels[field] || field) + ' = "' + op.payload.value + '"';
        }
        return (op.operation === 'ADD_WORK_TAG' ? 'Add ' : 'Remove ') +
            ((context.tag && context.tag.name) || 'Tag');
    }

    /** The cached read models a discarded operation's intent was overlaying. */
    function invalidate(op) {
        root.prksOfflineMarkEntityChanged('work', op.entity_id);
        if (op.operation === 'SET_WORK_METADATA_FIELD') {
            root.prksOfflineMarkEntityChanged('work-metadata-state', op.entity_id);
        } else {
            root.prksOfflineMarkEntityChanged('work-tag-options', op.entity_id);
        }
    }

    function conflictDetail(op) {
        const result = op.server_result || {};
        if (typeof result.current_value !== 'string') return '';
        return ' Server currently has "' + result.current_value + '".';
    }

    root.prksRenderSyncDiagnostics = async host => {
        let section = host.parentElement.querySelector('[data-sync-diagnostics]');
        if (!section) {
            section = document.createElement('div');
            section.dataset.syncDiagnostics = '';
            host.after(section);
        }
        section.replaceChildren();
        const operations = (await root.prksSync.store.listOperations())
            .filter(op => op.status !== 'acknowledged');
        for (const op of operations) {
            const row = document.createElement('p');
            const link = document.createElement('a');
            link.href = '#/works/' + encodeURIComponent(op.entity_id);
            link.textContent = describe(op);
            row.append(link, document.createTextNode(
                ' · ' + op.entity_id + ' · ' + status(op) + conflictDetail(op) + ' '));
            if (op.status === 'conflict') {
                const discard = document.createElement('button');
                discard.type = 'button';
                discard.className = 'prks-btn prks-btn--secondary prks-btn--sm';
                discard.textContent = 'Discard local change';
                discard.onclick = async () => {
                    discard.disabled = true;
                    try {
                        invalidate(op);
                        await root.prksSync.store.resolveConflict(op.op_id, false);
                        root.prksSync.changed();
                        await root.prksRenderSyncDiagnostics(host);
                    } catch (_) {
                        discard.disabled = false;
                        discard.textContent = 'Could not discard; retry';
                    }
                };
                row.appendChild(discard);
            }
            section.appendChild(row);
        }
    };
})(typeof window === 'undefined' ? globalThis : window);
