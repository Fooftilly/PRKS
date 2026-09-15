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

    const PREVIEW_CHARS = 120;

    function bounded(text) {
        const value = String(text == null ? '' : text);
        if (value.length <= PREVIEW_CHARS) return value;
        const points = Array.from(value.slice(0, PREVIEW_CHARS * 2));
        return points.slice(0, PREVIEW_CHARS).join('') + '…';
    }

    /** The value a family's payload carries, for preview and sizing. */
    function payloadValue(op) {
        if (op.operation === 'SET_WORK_METADATA_FIELD') return op.payload.value;
        if (op.operation === 'SET_WORK_SOURCE') return op.payload.source && op.payload.source.url;
        if (op.operation === 'SET_WORK_PERSON_ROLE_CREDIT') return op.payload.credit_name;
        return null;
    }

    function sizeNote(op) {
        if (payloadValue(op) == null) return '';
        const value = String(payloadValue(op));
        if (value.length <= PREVIEW_CHARS) return '';
        const bytes = typeof root.prksWorkFieldUtf8Bytes === 'function'
            ? root.prksWorkFieldUtf8Bytes(value) : value.length;
        return ' · ' + Math.ceil(bytes / 1024) + ' KB';
    }

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
            // Bounded: an Abstract may be a megabyte, and Diagnostics is a
            // status list, not a place to render -- or log -- a whole value.
            return (labels[field] || field) + ' = "' + bounded(op.payload.value) + '"';
        }
        if (op.operation === 'SET_WORK_SOURCE') {
            return 'Video source = "' + bounded(payloadValue(op)) + '"';
        }
        if (root.PRKS_WORK_ROLE_OPERATION_TYPES &&
            root.PRKS_WORK_ROLE_OPERATION_TYPES.indexOf(op.operation) !== -1) {
            const who = (context.person && context.person.canonical_name) || op.payload.person_id;
            const role = op.payload.role_type;
            if (op.operation === 'REMOVE_WORK_PERSON_ROLE') return 'Unlink ' + who + ' (' + role + ')';
            if (op.operation === 'SET_WORK_PERSON_ROLE_CREDIT') {
                return 'Credit ' + who + ' (' + role + ') as "' +
                    bounded(op.payload.credit_name) + '"';
            }
            return 'Link ' + who + ' as ' + role;
        }
        if (op.operation === 'SET_PERSON_METADATA_FIELD') {
            const labels = root.PRKS_PERSON_FIELD_LABELS || {};
            const field = op.payload.field;
            // Bounded: a Biography has no length limit, and Diagnostics is a
            // status list, not a place to render -- or log -- a whole value.
            return (labels[field] || field) + ' = "' + bounded(op.payload.value) + '"';
        }
        if (op.operation === 'CREATE_PERSON') {
            const who = [op.payload.first_name, op.payload.last_name].filter(Boolean).join(' ') ||
                op.entity_id;
            return 'Create ' + who;
        }
        if (op.operation === 'CREATE_FOLDER') {
            return 'Create folder "' + bounded(op.payload.title) + '"';
        }
        if (op.operation === 'SET_FOLDER_FIELD') {
            const labels = root.PRKS_FOLDER_FIELD_LABELS || {};
            const field = op.payload.field;
            if (field === 'parent_id') {
                return op.payload.value
                    ? 'Move folder into ' + op.payload.value : 'Move folder to the top level';
            }
            return (labels[field] || field) + ' = "' + bounded(op.payload.value) + '"';
        }
        if (op.operation === 'DELETE_FOLDER') {
            return 'Delete a folder';
        }
        if (op.operation === 'SET_WORK_FOLDER') {
            const where = (context.folder && context.folder.title) || op.payload.folder_id;
            return op.payload.folder_id ? 'File in ' + where : 'Remove from its folder';
        }
        if (op.operation === 'CREATE_TAG') {
            return 'Create tag "' + bounded(op.payload.name) + '"';
        }
        if (op.operation === 'DELETE_TAG') {
            return 'Delete a tag';
        }
        if (op.operation === 'DELETE_PERSON') {
            return 'Delete a person';
        }
        if (op.operation === 'CREATE_PERSON_GROUP') {
            return 'Create group "' + bounded(op.payload.name) + '"';
        }
        if (op.operation === 'SET_PERSON_GROUP_FIELD') {
            const labels = root.PRKS_PERSON_GROUP_FIELD_LABELS || {};
            const field = op.payload.field;
            if (field === 'parent_id') {
                return op.payload.value
                    ? 'Move group into ' + op.payload.value : 'Move group to the top level';
            }
            return (labels[field] || field) + ' = "' + bounded(op.payload.value) + '"';
        }
        if (op.operation === 'ADD_PERSON_GROUP_MEMBER' ||
            op.operation === 'REMOVE_PERSON_GROUP_MEMBER') {
            const who = (context.person && context.person.canonical_name) ||
                op.payload.person_id;
            return (op.operation === 'ADD_PERSON_GROUP_MEMBER' ? 'Add ' : 'Remove ') +
                who + (op.operation === 'ADD_PERSON_GROUP_MEMBER' ? ' to' : ' from') +
                ' a group';
        }
        if (op.operation === 'DELETE_PERSON_GROUP') {
            return 'Delete a group';
        }
        if (op.operation === 'ADD_WORK_TAG' || op.operation === 'REMOVE_WORK_TAG') {
            return (op.operation === 'ADD_WORK_TAG' ? 'Add ' : 'Remove ') +
                ((context.tag && context.tag.name) || 'Tag');
        }
        /* Never a guess. Describing an unknown family as a Tag edit is how a
         * source operation came to be labelled "Remove Tag" -- and to
         * invalidate the Tag options cache when it was discarded. */
        return op.operation;
    }

    /* The projection each family's intent was overlaying. Explicit per family
     * and never an `else`: a family this list does not know invalidates the
     * Work alone, which is always true, rather than some other family's
     * projection, which is always wrong. */
    const DISCARD_INVALIDATES = Object.freeze({
        SET_WORK_METADATA_FIELD: 'work-metadata-state',
        SET_WORK_SOURCE: 'work-source-state',
        CREATE_FOLDER: 'folder-state',
        SET_FOLDER_FIELD: 'folder-state',
        DELETE_FOLDER: 'folder-state',
        /* Reached through the Work branch, which is where a filing belongs: the
         * folder a Work is in is a field on the WORK. */
        SET_WORK_FOLDER: 'work-folder-state',
        CREATE_TAG: 'tags:index',
        DELETE_TAG: 'tags:index',
        ADD_WORK_TAG: 'work-tag-options',
        REMOVE_WORK_TAG: 'work-tag-options',
        ADD_WORK_PERSON_ROLE: 'work-people-state',
        REMOVE_WORK_PERSON_ROLE: 'work-people-state',
        SET_WORK_PERSON_ROLE_CREDIT: 'work-people-state',
        CREATE_PERSON: 'person',
        DELETE_PERSON: 'person',
        /* Reached only through the Work branch below, which a Person-scoped
         * operation never takes -- `invalidate()` answers `person` entities
         * first, because the whole People read model carries every profile
         * field. Listed anyway so the registry stays the readable answer to
         * "what does discarding this stale?" for every durable family. */
        SET_PERSON_METADATA_FIELD: 'person-metadata-state',
        /* Reached only through the `person-group` branch of `invalidate()`,
         * for the same reason the Person entry above is. Listed so the
         * registry stays the readable answer to "what does discarding this
         * stale?" for every durable family. */
        CREATE_PERSON_GROUP: 'person-group-state',
        SET_PERSON_GROUP_FIELD: 'person-group-state',
        ADD_PERSON_GROUP_MEMBER: 'person-group-state',
        REMOVE_PERSON_GROUP_MEMBER: 'person-group-state',
        DELETE_PERSON_GROUP: 'person-group-state',
    });

    /** The cached read models a discarded operation's intent was overlaying. */
    function invalidate(op) {
        if (op.entity_type === 'person') {
            if (typeof root.prksOfflineMarkPeopleChanged === 'function') {
                root.prksOfflineMarkPeopleChanged();
            } else {
                root.prksOfflineMarkEntityChanged('person', op.entity_id);
            }
            /* The revision this edit was measured against is cached
             * separately from the profile itself, so the People domain does
             * not reach it. A discarded edit leaves the base it observed
             * stale, and the next edit would be enqueued against a revision
             * the server has already moved past. */
            root.prksOfflineMarkEntityChanged('person-metadata-state', op.entity_id);
            return;
        }
        if (op.entity_type === 'folder') {
            /* The whole hierarchy: a discarded creation leaves a library that
             * was showing a folder the server never stored, and a discarded
             * deletion one that was hiding a folder it still has. */
            if (typeof root.prksOfflineMarkFoldersChanged === 'function') {
                root.prksOfflineMarkFoldersChanged();
            }
            root.prksOfflineMarkEntityChanged('folder-state', op.entity_id);
            return;
        }
        if (op.entity_type === 'tag') {
            /* The whole vocabulary, not one row: a discarded creation leaves a
             * catalogue that was showing a Tag the server never stored, and a
             * discarded deletion one that was hiding a Tag it still has. */
            if (typeof root.prksOfflineMarkTagsChanged === 'function') {
                root.prksOfflineMarkTagsChanged();
            }
            return;
        }
        if (op.entity_type === 'person-group') {
            if (typeof root.prksOfflineMarkPersonGroupsChanged === 'function') {
                root.prksOfflineMarkPersonGroupsChanged();
            } else {
                root.prksOfflineMarkEntityChanged('person-group', op.entity_id);
            }
            /* The revisions this edit was measured against are cached
             * separately from the group itself, and a membership discarded
             * here leaves the PERSON's side of the pair stale too -- the two
             * projections describe the same scopes from opposite ends. */
            root.prksOfflineMarkEntityChanged('person-group-state', op.entity_id);
            const personId = op.payload && op.payload.person_id;
            if (personId) {
                root.prksOfflineMarkEntityChanged('person-group-memberships', personId);
                if (typeof root.prksOfflineMarkPeopleChanged === 'function') {
                    root.prksOfflineMarkPeopleChanged();
                }
            }
            return;
        }
        root.prksOfflineMarkEntityChanged('work', op.entity_id);
        const projection = DISCARD_INVALIDATES[op.operation];
        if (projection) root.prksOfflineMarkEntityChanged(projection, op.entity_id);
    }

    /* Terminal refusals a durable result can carry, in the user's words. The
     * durable row holds a CODE -- a closed vocabulary the store validates --
     * and this is the one place it becomes a sentence. */
    const NAMED_REFUSALS = Object.freeze({
        NAME_TAKEN: 'Something else already has that name.',
        TAG_MERGED: 'That tag was merged into another one.',
        TITLE_TAKEN: 'Something here already has that name.',
        FOLDER_NOT_FOUND: 'That folder no longer exists on the server.',
        FOLDER_NOT_EMPTY: 'It still holds files, so it cannot be deleted.',
        FOLDER_HAS_SUBFOLDERS: 'It still has subfolders, so it cannot be deleted.',
        TAG_DELETED: 'That tag was deleted on the server.',
        PARENT_NOT_FOUND: 'The group it would go inside no longer exists.',
        PARENT_CYCLE: 'That would put the group inside one of its own subgroups.',
        PERSON_NOT_FOUND: 'That person no longer exists on the server.',
        PERSON_HAS_LINKS: 'They are still credited on a file, so they cannot be deleted.',
        ENTITY_NOT_FOUND: 'It no longer exists on the server.',
    });

    function conflictDetail(op) {
        const result = op.server_result || {};
        /* Named, not left blank. This operation never reached the server at
         * all, so there is no "server currently has" to report -- and a bare
         * "Needs a decision" with no reason is exactly the stranded state the
         * dependency walk exists to prevent the user from being left in. */
        if (result.code === 'DEPENDENCY_FAILED') {
            return ' It was waiting on another change that could not be saved.';
        }
        /* Named per code. A group refused because its name is taken and one
         * refused because its parent would make a cycle are different problems
         * with different fixes, and "Needs a decision" is neither of them. */
        if (NAMED_REFUSALS[result.code]) return ' ' + NAMED_REFUSALS[result.code];
        if (typeof result.current_state === 'boolean' &&
            typeof result.current_value !== 'string') {
            return result.current_state
                ? ' The server already has this relationship.'
                : ' The server does not have this relationship.';
        }
        if (typeof result.current_preview === 'string') {
            return ' Server currently has "' + bounded(result.current_preview) + '" (' +
                Math.ceil((result.current_bytes || 0) / 1024) + ' KB).';
        }
        if (typeof result.current_value !== 'string') return '';
        return ' Server currently has "' + bounded(result.current_value) + '".';
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
            link.href = op.entity_type === 'folder'
                ? '#/folders/' + encodeURIComponent(op.entity_id)
                : op.entity_type === 'person'
                ? '#/people/' + encodeURIComponent(op.entity_id)
                : op.entity_type === 'person-group'
                    ? '#/people/groups/' + encodeURIComponent(op.entity_id)
                    : '#/works/' + encodeURIComponent(op.entity_id);
            link.textContent = describe(op);
            row.append(link, document.createTextNode(
                ' · ' + op.entity_id + sizeNote(op) + ' · ' + status(op) + conflictDetail(op) + ' '));
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
