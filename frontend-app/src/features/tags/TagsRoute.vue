<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { usePendingAction } from '../../route-surface/pending-action'
import { registerTagsAliasCloser, registerTagsMergeCloser } from './closers'
import type { TagsIntents } from './intents'
import { filterMergeCandidates, type TagCloudRow, type TagsProjection } from './projection'

const props = defineProps<{
  projection: TagsProjection
  intents: TagsIntents
}>()

const { actionBusy, actionBlocked, withBusy } = usePendingAction()
const rootEl = ref<HTMLElement | null>(null)
const aliasTagId = ref<string | null>(props.projection.openAliasTagId)
const aliasDraft = ref('')
const aliasTrigger = ref<HTMLElement | null>(null)
const mergeSourceId = ref<string | null>(null)
const mergeTargetId = ref<string | null>(null)
const mergeFilter = ref('')
const mergeTrigger = ref<HTMLElement | null>(null)
const rows = computed(() => props.projection.rows)
const aliasTag = computed(() => rows.value.find((row) => row.id === aliasTagId.value) ?? null)
const mergeSource = computed(() => rows.value.find((row) => row.id === mergeSourceId.value) ?? null)
const mergeTarget = computed(() => rows.value.find((row) => row.id === mergeTargetId.value) ?? null)
const mergeCandidates = computed(() =>
  mergeSourceId.value ? filterMergeCandidates(rows.value, mergeSourceId.value, mergeFilter.value) : [],
)
const mergeIcon = computed(() => window.prksIcon?.('arrowRight', { size: 'sm' }) ?? '→')

let unregisterAlias: (() => void) | null = null
let unregisterMerge: (() => void) | null = null

function restoreFocus(trigger: HTMLElement | null): void {
  if (!trigger || !trigger.isConnected || typeof trigger.focus !== 'function') return
  try {
    trigger.focus()
  } catch {
    /* The control may already be gone. */
  }
}

function closeAlias(): void {
  const trigger = aliasTrigger.value
  aliasTagId.value = null
  aliasDraft.value = ''
  aliasTrigger.value = null
  restoreFocus(trigger)
}

function closeMerge(): void {
  const trigger = mergeTrigger.value
  mergeSourceId.value = null
  mergeTargetId.value = null
  mergeFilter.value = ''
  mergeTrigger.value = null
  restoreFocus(trigger)
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
  aliasTagId.value = row.id
}

function openMerge(row: TagCloudRow, event: MouseEvent): void {
  mergeTrigger.value = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  mergeTargetId.value = null
  mergeFilter.value = ''
  mergeSourceId.value = row.id
}

function onTagKeydown(row: TagCloudRow, event: KeyboardEvent): void {
  if (event.key !== 'Enter' && event.key !== ' ') return
  event.preventDefault()
  props.intents.openTag(row.name)
}

async function report(title: string, message: string): Promise<void> {
  const alertFn = window.prksAlertMessage
  if (typeof alertFn === 'function') await alertFn(message, title)
}

function focusAliasInput(): void {
  rootEl.value?.querySelector<HTMLInputElement>('#tags-page-alias-input')?.focus()
}

function focusMergeFilter(): void {
  rootEl.value?.querySelector<HTMLInputElement>('#tags-page-merge-filter')?.focus()
}

function addAlias(): void {
  const tag = aliasTag.value
  if (!tag) return
  const draft = aliasDraft.value
  void withBusy('alias-add', async () => {
    const outcome = await props.intents.addAlias(tag.id, draft)
    if (outcome.status === 'error') await report('Error', outcome.message)
  })
}

function removeAlias(alias: string): void {
  const tag = aliasTag.value
  if (!tag) return
  void withBusy(`alias-remove:${alias}`, async () => {
    const outcome = await props.intents.removeAlias(tag.id, alias)
    if (outcome.status === 'error') await report('Error', outcome.message)
  })
}

function removeTag(): void {
  const tag = aliasTag.value
  if (!tag) return
  void withBusy('alias-delete', async () => {
    const outcome = await props.intents.remove(tag.id, tag.name)
    if (outcome.status === 'error') await report('Could not delete', outcome.message)
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
  void withBusy('merge', async () => {
    const outcome = await props.intents.merge(source.id, target.id)
    if (outcome.status === 'error') await report('Error', outcome.message)
  })
}

watch(aliasTagId, (id) => {
  if (!id) return
  void nextTick(() => focusAliasInput())
})

watch(mergeSourceId, (id) => {
  if (!id || mergeTargetId.value) return
  void nextTick(() => focusMergeFilter())
})

onMounted(() => {
  window.prksRefreshIcons?.(rootEl.value)
  unregisterAlias = registerTagsAliasCloser(closeAlias)
  unregisterMerge = registerTagsMergeCloser(closeMerge)
  if (aliasTagId.value) void nextTick(() => focusAliasInput())
})

onUnmounted(() => {
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
        Tags currently used on at least one file or folder. Click a name to list matching files. Use ⋯ for alternate
        names (e.g. other languages). Use → to merge this tag into another; the merged name becomes an alias and no
        longer appears as its own tag.
      </p>
    </div>
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
        <button
          type="button"
          class="tag-page-alias-btn"
          :data-tag-alias-edit="row.id"
          title="Aliases"
          :aria-label="`Edit aliases for ${row.name}`"
          @click="openAlias(row, $event)"
        >
          ⋯
        </button>
        <button
          type="button"
          class="tag-page-merge-btn"
          :data-tag-merge="row.id"
          title="Merge into another tag"
          :aria-label="`Merge ${row.name} into another tag`"
          @click="openMerge(row, $event)"
        >
          <span class="work-html-slot" v-html="mergeIcon"></span>
        </button>
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
          <button
            id="tags-page-alias-modal-close"
            type="button"
            class="prks-icon-btn close-btn"
            aria-label="Close"
            @click="closeAlias"
          >
            ×
          </button>
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
              <button
                type="button"
                class="tags-page-alias-remove"
                :data-alias-remove="alias"
                aria-label="Remove alias"
                :disabled="actionBlocked(`alias-remove:${alias}`)"
                @click="removeAlias(alias)"
              >
                ×
              </button>
            </li>
          </ul>
          <div class="tags-page-alias-add">
            <input
              id="tags-page-alias-input"
              v-model="aliasDraft"
              type="text"
              class="tags-page-alias-input"
              maxlength="120"
              placeholder="New alias…"
              autocomplete="off"
              aria-label="New alias"
            >
            <button
              id="tags-page-alias-add-btn"
              type="button"
              class="tags-page-alias-add__submit"
              :disabled="actionBlocked('alias-add') || actionBusy('alias-add')"
              @click="addAlias"
            >
              Add alias
            </button>
          </div>
          <div class="tags-page-alias-delete">
            <button
              id="tags-page-alias-delete-btn"
              type="button"
              class="prks-btn prks-btn--danger"
              :disabled="actionBlocked('alias-delete')"
              @click="removeTag"
            >
              Delete tag
            </button>
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
          <button
            id="tags-page-merge-modal-close"
            type="button"
            class="prks-icon-btn close-btn"
            aria-label="Close"
            @click="closeMerge"
          >
            ×
          </button>
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
              class="tags-page-merge-filter"
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
                <button
                  type="button"
                  class="tags-page-merge-pick-btn"
                  :data-tag-merge-pick="candidate.id"
                  :style="{ '--tag-accent': candidate.color }"
                  @click="pickMergeTarget(candidate.id)"
                >
                  {{ candidate.name }}
                </button>
              </li>
            </ul>
          </div>
          <div v-else id="tags-page-merge-confirm" class="tags-page-merge-confirm">
            <p id="tags-page-merge-confirm-text" class="tags-page-merge-confirm-text">
              <strong>{{ mergeSource.name }}</strong> will become an alias of <strong>{{ mergeTarget.name }}</strong>.
              It will no longer appear as a separate tag; searches and links using that name will use the same files
              as the target tag.
            </p>
            <div class="tags-page-merge-confirm-actions">
              <button id="tags-page-merge-back-btn" type="button" class="tags-page-merge-back-btn" @click="backToMergePick">
                Back
              </button>
              <button
                id="tags-page-merge-confirm-btn"
                type="button"
                class="tags-page-merge-confirm-btn"
                :disabled="actionBlocked('merge')"
                @click="confirmMerge"
              >
                Merge
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
