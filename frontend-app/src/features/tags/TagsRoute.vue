<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksIconButton from '../../components/PrksIconButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import { usePendingAction } from '../../route-surface/pending-action'
import { registerTagsAliasCloser, registerTagsMergeCloser } from './closers'
import type { TagsIntents } from './intents'
import type { TagsDialogKind, TagsDialogState, TagsRefreshSink } from './session'
import { filterMergeCandidates, type TagCloudRow, type TagsProjection } from './projection'

const props = defineProps<{
  projection: TagsProjection
  intents: TagsIntents
  dialogState?: TagsDialogState
  refreshSink?: TagsRefreshSink
}>()

const { actionBusy, actionBlocked, withBusy } = usePendingAction()
const rootEl = ref<HTMLElement | null>(null)
const aliasTagId = ref<string | null>(props.projection.openAliasTagId)
const dialogKind = ref<TagsDialogKind>(
  props.projection.openMergeSourceId ? 'merge' : props.projection.openAliasTagId ? 'alias' : null,
)
const aliasDraft = ref('')
const aliasTrigger = ref<HTMLElement | null>(null)
const aliasAddError = ref('')
const aliasRemoveError = ref('')
const aliasDeleteError = ref('')
const refreshError = ref('')
const mergeSourceId = ref<string | null>(props.projection.openMergeSourceId)
const mergeTargetId = ref<string | null>(props.projection.openMergeTargetId)
const mergeFilter = ref('')
const mergeError = ref('')
const mergeTrigger = ref<HTMLElement | null>(null)
const rows = computed(() => props.projection.rows)
const aliasTag = computed(() => rows.value.find((row) => row.id === aliasTagId.value) ?? null)
const mergeSource = computed(() => rows.value.find((row) => row.id === mergeSourceId.value) ?? null)
const mergeTarget = computed(() => rows.value.find((row) => row.id === mergeTargetId.value) ?? null)
const mergeCandidates = computed(() =>
  mergeSourceId.value ? filterMergeCandidates(rows.value, mergeSourceId.value, mergeFilter.value) : [],
)
const aliasIcon = computed(() => window.prksIcon?.('ellipsis', { size: 'sm' }) ?? '')
const mergeIcon = computed(() => window.prksIcon?.('arrowRight', { size: 'sm' }) ?? '')
const closeIcon = computed(() => window.prksIcon?.('x', { size: 'sm' }) ?? '')

let unregisterAlias: (() => void) | null = null
let unregisterMerge: (() => void) | null = null

function showRefreshFailure(message: string): void {
  refreshError.value = message
}

function aliasEditButton(tagId: string): HTMLElement | null {
  const root = rootEl.value
  if (!root) return null
  const escaped = typeof CSS !== 'undefined' && typeof CSS.escape === 'function' ? CSS.escape(tagId) : tagId
  const node = root.querySelector(`[data-tag-alias-edit="${escaped}"]`)
  return node instanceof HTMLElement ? node : null
}

function mergeRowButton(tagId: string): HTMLElement | null {
  const root = rootEl.value
  if (!root) return null
  const escaped = typeof CSS !== 'undefined' && typeof CSS.escape === 'function' ? CSS.escape(tagId) : tagId
  const node = root.querySelector(`[data-tag-merge="${escaped}"]`)
  return node instanceof HTMLElement ? node : null
}

function restoreFocus(trigger: HTMLElement | null, fallback: HTMLElement | null): void {
  const root = rootEl.value
  const target = trigger && trigger.isConnected && root?.contains(trigger) ? trigger : fallback
  if (!target || typeof target.focus !== 'function') return
  try {
    target.focus()
  } catch {
    /* The control may already be gone. */
  }
}

function publishDialog(): void {
  const state = props.dialogState
  if (!state) return
  state.kind = dialogKind.value
  state.aliasTagId = aliasTagId.value
  state.mergeSourceId = mergeSourceId.value
  state.mergeTargetId = mergeTargetId.value
}

function closeAlias(): void {
  const trigger = aliasTrigger.value
  const tagId = aliasTagId.value
  aliasTagId.value = null
  aliasDraft.value = ''
  aliasTrigger.value = null
  if (dialogKind.value === 'alias') dialogKind.value = mergeSourceId.value ? 'merge' : null
  void nextTick(() => restoreFocus(trigger, tagId ? aliasEditButton(tagId) : null))
}

function closeMerge(): void {
  const trigger = mergeTrigger.value
  const sourceId = mergeSourceId.value
  mergeSourceId.value = null
  mergeTargetId.value = null
  mergeFilter.value = ''
  mergeError.value = ''
  mergeTrigger.value = null
  if (dialogKind.value === 'merge') dialogKind.value = aliasTagId.value ? 'alias' : null
  void nextTick(() => restoreFocus(trigger, sourceId ? mergeRowButton(sourceId) : null))
}

function closeAliasFor(modal?: Element | null): boolean {
  if (modal instanceof Element) {
    const root = rootEl.value
    if (!root || !root.contains(modal)) return false
  }
  closeAlias()
  return true
}

function closeMergeFor(modal?: Element | null): boolean {
  if (modal instanceof Element) {
    const root = rootEl.value
    if (!root || !root.contains(modal)) return false
  }
  closeMerge()
  return true
}

function chipStyle(row: TagCloudRow): Record<string, string> {
  return {
    '--tag-scale': row.scale,
    '--tag-accent': row.color,
    '--tag-border-width': row.borderWidth,
  }
}

function openAlias(row: TagCloudRow, event: MouseEvent): void {
  aliasTrigger.value = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  aliasDraft.value = ''
  dialogKind.value = 'alias'
  aliasTagId.value = row.id
}

function openMerge(row: TagCloudRow, event: MouseEvent): void {
  mergeTrigger.value = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  mergeTargetId.value = null
  mergeFilter.value = ''
  dialogKind.value = 'merge'
  mergeSourceId.value = row.id
}

function onTagKeydown(row: TagCloudRow, event: KeyboardEvent): void {
  if (event.key !== 'Enter' && event.key !== ' ') return
  event.preventDefault()
  props.intents.openTag(row.name)
}

function focusAliasInput(): void {
  rootEl.value?.querySelector<HTMLInputElement>('#tags-page-alias-input')?.focus()
}

function focusMergeFilter(): void {
  rootEl.value?.querySelector<HTMLInputElement>('#tags-page-merge-filter')?.focus()
}

function focusMergeConfirm(): void {
  rootEl.value?.querySelector<HTMLElement>('#tags-page-merge-confirm-btn')?.focus()
}

function clearAliasActionErrors(): void {
  aliasAddError.value = ''
  aliasRemoveError.value = ''
  aliasDeleteError.value = ''
}

function addAlias(): void {
  const tag = aliasTag.value
  if (!tag) return
  const tagId = tag.id
  const draft = aliasDraft.value
  void withBusy('alias-add', async () => {
    const outcome = await props.intents.addAlias(tagId, draft)
    if (aliasTagId.value !== tagId) return
    if (outcome.status === 'error') aliasAddError.value = outcome.message
    else if (outcome.status === 'success') {
      aliasAddError.value = ''
      aliasDraft.value = ''
    }
  })
}

function removeAlias(alias: string): void {
  const tag = aliasTag.value
  if (!tag) return
  const tagId = tag.id
  void withBusy(`alias-remove:${alias}`, async () => {
    const outcome = await props.intents.removeAlias(tagId, alias)
    if (aliasTagId.value !== tagId) return
    if (outcome.status === 'error') aliasRemoveError.value = outcome.message
    else if (outcome.status === 'success') aliasRemoveError.value = ''
  })
}

function removeTag(): void {
  const tag = aliasTag.value
  if (!tag) return
  const tagId = tag.id
  void withBusy('alias-delete', async () => {
    const outcome = await props.intents.remove(tagId, tag.name)
    if (aliasTagId.value !== tagId) return
    if (outcome.status === 'error') aliasDeleteError.value = outcome.message
    else if (outcome.status === 'success') aliasDeleteError.value = ''
  })
}

function pickMergeTarget(id: string): void {
  mergeTargetId.value = id
}

function backToMergePick(): void {
  mergeTargetId.value = null
  void nextTick(() => focusMergeFilter())
}

function confirmMerge(): void {
  const source = mergeSource.value
  const target = mergeTarget.value
  if (!source || !target) return
  const sourceId = source.id
  const targetId = target.id
  void withBusy('merge', async () => {
    const outcome = await props.intents.merge(sourceId, targetId)
    if (mergeSourceId.value !== sourceId || mergeTargetId.value !== targetId) return
    if (outcome.status === 'error') mergeError.value = outcome.message
    else if (outcome.status === 'success') mergeError.value = ''
  })
}

watch([aliasTagId, mergeSourceId, mergeTargetId, dialogKind], () => {
  publishDialog()
}, { immediate: true })

watch(aliasTagId, (id) => {
  clearAliasActionErrors()
  if (!id) return
  void nextTick(() => focusAliasInput())
}, { immediate: true })

watch(mergeSourceId, (id) => {
  mergeError.value = ''
  if (!id) return
  void nextTick(() => {
    if (mergeTargetId.value) focusMergeConfirm()
    else focusMergeFilter()
  })
}, { immediate: true })

watch(mergeTargetId, () => {
  mergeError.value = ''
})

onMounted(() => {
  window.prksRefreshIcons?.(rootEl.value)
  if (props.refreshSink) props.refreshSink.set = showRefreshFailure
  unregisterAlias = registerTagsAliasCloser(closeAliasFor)
  unregisterMerge = registerTagsMergeCloser(closeMergeFor)
})

onUnmounted(() => {
  if (props.refreshSink?.set === showRefreshFailure) props.refreshSink.set = null
  unregisterAlias?.()
  unregisterMerge?.()
  unregisterAlias = null
  unregisterMerge = null
})
</script>

<template>
  <div ref="rootEl" class="tags-page" data-prks-tags-page>
    <div class="prks-page-header page-header tags-page__header">
      <h2 class="prks-page-title">All tags</h2>
      <p class="tags-page__sub">
        Tags currently used on at least one file or folder. Click a name to list matching files. Use the alias control
        for alternate names (e.g. other languages). Use the merge control to merge this tag into another; the merged
        name becomes an alias and no longer appears as its own tag.
      </p>
    </div>
    <PrksInlineMessage v-if="refreshError" tone="error" status data-tags-refresh-error>
      {{ refreshError }}
    </PrksInlineMessage>
    <div id="tags-page-cloud" class="tag-cloud tag-cloud--page">
      <p v-if="!rows.length" class="tags-page__empty">
        No tags in use yet. Add tags to files or folders from the details panel.
      </p>
      <span
        v-for="row in rows"
        :key="row.id"
        class="tag tag--page tag--page-with-actions"
        :style="chipStyle(row)"
      >
        <span
          class="tag--page__nav"
          role="button"
          tabindex="0"
          :data-prks-route="row.searchHash"
          data-prks-middleclick-nav="1"
          :data-tag-nav="row.encodedName"
          @keydown="onTagKeydown(row, $event)"
        >{{ row.name }}</span>
        <PrksIconButton
          size="sm"
          :label="`Edit aliases for ${row.name}`"
          title="Aliases"
          :data-tag-alias-edit="row.id"
          @click="openAlias(row, $event)"
        >
          <span v-if="aliasIcon" class="work-html-slot" v-html="aliasIcon"></span>
        </PrksIconButton>
        <PrksIconButton
          size="sm"
          :label="`Merge ${row.name} into another tag`"
          title="Merge into another tag"
          :data-tag-merge="row.id"
          @click="openMerge(row, $event)"
        >
          <span v-if="mergeIcon" class="work-html-slot" v-html="mergeIcon"></span>
        </PrksIconButton>
      </span>
    </div>

    <div
      v-if="aliasTag"
      id="tags-page-alias-backdrop"
      class="modal-backdrop tags-page-alias-backdrop"
      role="presentation"
      @click.self="closeAlias"
    >
      <div
        id="tags-page-alias-modal"
        class="modal tags-page-alias-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="tags-page-alias-heading"
        tabindex="-1"
      >
        <div class="modal-header">
          <h3 id="tags-page-alias-heading">Tag aliases</h3>
          <PrksIconButton
            id="tags-page-alias-modal-close"
            class="close-btn"
            label="Close"
            @click="closeAlias"
          >
            <span v-if="closeIcon" class="work-html-slot" v-html="closeIcon"></span>
          </PrksIconButton>
        </div>
        <div class="modal-body tags-page-alias-modal__body">
          <p class="modal-helper">Alternate names for this tag (same file set). Not shown as separate tags in the list.</p>
          <p class="tags-page-alias-panel__for">
            Canonical name: <strong id="tags-page-alias-canonical">{{ aliasTag.name }}</strong>
          </p>
          <ul id="tags-page-alias-list" class="tags-page-alias-list">
            <li v-if="!aliasTag.aliases.length" class="tags-page-alias-list__none">No aliases yet.</li>
            <li v-for="alias in aliasTag.aliases" :key="alias" class="tags-page-alias-list__item">
              <span class="tags-page-alias-list__text">{{ alias }}</span>
              <PrksIconButton
                variant="danger"
                size="sm"
                :class="{ 'tags-page-alias-remove--busy': actionBusy(`alias-remove:${alias}`) }"
                :data-alias-remove="alias"
                label="Remove alias"
                :busy="actionBusy(`alias-remove:${alias}`)"
                :disabled="actionBlocked(`alias-remove:${alias}`)"
                busy-label="Removing…"
                @click="removeAlias(alias)"
              >
                <span v-if="closeIcon" class="work-html-slot" v-html="closeIcon"></span>
              </PrksIconButton>
            </li>
          </ul>
          <PrksInlineMessage
            v-if="aliasRemoveError"
            id="tags-page-alias-remove-error"
            tone="error"
            status
            data-tags-alias-remove-error
          >
            {{ aliasRemoveError }}
          </PrksInlineMessage>
          <div class="tags-page-alias-add">
            <input
              id="tags-page-alias-input"
              v-model="aliasDraft"
              type="text"
              class="prks-input"
              maxlength="120"
              placeholder="New alias…"
              autocomplete="off"
              aria-label="New alias"
              :aria-invalid="aliasAddError ? 'true' : undefined"
              :aria-describedby="aliasAddError ? 'tags-page-alias-add-error' : undefined"
            >
            <PrksButton
              id="tags-page-alias-add-btn"
              variant="secondary"
              size="sm"
              :busy="actionBusy('alias-add')"
              :disabled="actionBlocked('alias-add')"
              busy-label="Adding…"
              @click="addAlias"
            >
              Add alias
            </PrksButton>
          </div>
          <PrksInlineMessage
            v-if="aliasAddError"
            id="tags-page-alias-add-error"
            tone="error"
            status
            data-tags-alias-add-error
          >
            {{ aliasAddError }}
          </PrksInlineMessage>
          <div class="tags-page-alias-delete">
            <PrksButton
              id="tags-page-alias-delete-btn"
              variant="danger"
              :busy="actionBusy('alias-delete')"
              :disabled="actionBlocked('alias-delete')"
              busy-label="Deleting…"
              @click="removeTag"
            >
              Delete tag
            </PrksButton>
            <PrksInlineMessage
              v-if="aliasDeleteError"
              id="tags-page-alias-delete-error"
              tone="error"
              status
              data-tags-alias-delete-error
            >
              {{ aliasDeleteError }}
            </PrksInlineMessage>
          </div>
        </div>
      </div>
    </div>

    <div
      v-if="mergeSource"
      id="tags-page-merge-backdrop"
      class="modal-backdrop tags-page-merge-backdrop"
      role="presentation"
      @click.self="closeMerge"
    >
      <div
        id="tags-page-merge-modal"
        class="modal tags-page-merge-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="tags-page-merge-heading"
        tabindex="-1"
      >
        <div class="modal-header">
          <h3 id="tags-page-merge-heading">Merge tag</h3>
          <PrksIconButton
            id="tags-page-merge-modal-close"
            class="close-btn"
            label="Close"
            @click="closeMerge"
          >
            <span v-if="closeIcon" class="work-html-slot" v-html="closeIcon"></span>
          </PrksIconButton>
        </div>
        <div class="modal-body tags-page-merge-modal__body">
          <p class="modal-helper">
            Pick the tag that should stay. The tag you started from will be removed from the list; its name will
            resolve to the same files as the target.
          </p>
          <p id="tags-page-merge-source-label" class="tags-page-merge-source-label">
            Merging: <strong>{{ mergeSource.name }}</strong>
          </p>
          <div v-if="!mergeTarget" id="tags-page-merge-pick">
            <label class="tags-page-merge-filter-label" for="tags-page-merge-filter">Merge into</label>
            <input
              id="tags-page-merge-filter"
              v-model="mergeFilter"
              type="search"
              class="prks-input tags-page-merge-filter"
              maxlength="120"
              placeholder="Search tags…"
              autocomplete="off"
              aria-label="Filter tags to merge into"
            >
            <ul id="tags-page-merge-target-list" class="tags-page-merge-list">
              <li v-if="!mergeCandidates.length" class="tags-page-merge-list__none">
                No other tags match. Try another search.
              </li>
              <li v-for="candidate in mergeCandidates" :key="candidate.id" class="tags-page-merge-list__item">
                <PrksButton
                  variant="secondary"
                  class="tags-page-merge-pick-btn"
                  :data-tag-merge-pick="candidate.id"
                  :style="{ '--tag-accent': candidate.color }"
                  @click="pickMergeTarget(candidate.id)"
                >
                  {{ candidate.name }}
                </PrksButton>
              </li>
            </ul>
          </div>
          <div v-else id="tags-page-merge-confirm" class="tags-page-merge-confirm">
            <p id="tags-page-merge-confirm-text" class="tags-page-merge-confirm-text">
              <strong>{{ mergeSource.name }}</strong> will become an alias of <strong>{{ mergeTarget.name }}</strong>.
              It will no longer appear as a separate tag; searches and links using that name will use the same files
              as the target tag.
            </p>
            <PrksInlineMessage
              v-if="mergeError"
              id="tags-page-merge-error"
              tone="error"
              status
              data-tags-merge-error
            >
              {{ mergeError }}
            </PrksInlineMessage>
            <div class="tags-page-merge-confirm-actions">
              <PrksButton
                id="tags-page-merge-back-btn"
                variant="secondary"
                :disabled="actionBusy('merge') || actionBlocked('merge')"
                @click="backToMergePick"
              >
                Back
              </PrksButton>
              <PrksButton
                id="tags-page-merge-confirm-btn"
                variant="primary"
                :busy="actionBusy('merge')"
                :disabled="actionBlocked('merge')"
                busy-label="Merging…"
                @click="confirmMerge"
              >
                Merge
              </PrksButton>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
