<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { usePendingAction } from '../../route-surface/pending-action'
import { registerPublishersAliasCloser } from './closers'
import type { PublishersIntents } from './intents'
import type { PublisherRow, PublishersProjection } from './projection'

const props = defineProps<{
  projection: PublishersProjection
  intents: PublishersIntents
}>()

const { actionBusy, actionBlocked, withBusy } = usePendingAction()
const rootEl = ref<HTMLElement | null>(null)
const draftName = ref('')
const aliasPublisherId = ref<string | null>(props.projection.openAliasPublisherId)
const aliasDraft = ref('')
const aliasTrigger = ref<HTMLElement | null>(null)
const rows = computed(() => props.projection.rows)
const aliasPublisher = computed(() => rows.value.find((row) => row.id === aliasPublisherId.value) ?? null)
const plusIcon = computed(() => window.prksTagPlusIconHtml?.() ?? '')
const buildingIcon = computed(() => window.prksIcon?.('building-2', { size: 'sm' }) ?? '')

let unregisterAlias: (() => void) | null = null

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
  aliasPublisherId.value = null
  aliasDraft.value = ''
  aliasTrigger.value = null
  restoreFocus(trigger)
}

function openAlias(row: PublisherRow, event: MouseEvent): void {
  aliasTrigger.value = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  aliasDraft.value = ''
  aliasPublisherId.value = row.id
}

function onRowKeydown(row: PublisherRow, event: KeyboardEvent): void {
  if (event.key !== 'Enter' && event.key !== ' ') return
  event.preventDefault()
  props.intents.openPublisher(row.name)
}

async function report(title: string, message: string): Promise<void> {
  const alertFn = window.prksAlertMessage
  if (typeof alertFn === 'function') await alertFn(message, title)
}

function focusAliasInput(): void {
  rootEl.value?.querySelector<HTMLInputElement>('#publishers-page-alias-input')?.focus()
}

function createPublisher(): void {
  const name = draftName.value
  void withBusy('create', async () => {
    const outcome = await props.intents.create(name)
    if (outcome.status === 'error') await report('Error', outcome.message)
  })
}

function addAlias(): void {
  const publisher = aliasPublisher.value
  if (!publisher) return
  const draft = aliasDraft.value
  void withBusy('alias-add', async () => {
    const outcome = await props.intents.addAlias(publisher.id, draft)
    if (outcome.status === 'error') await report('Error', outcome.message)
  })
}

function removeAlias(alias: string): void {
  const publisher = aliasPublisher.value
  if (!publisher) return
  void withBusy(`alias-remove:${alias}`, async () => {
    const outcome = await props.intents.removeAlias(publisher.id, alias)
    if (outcome.status === 'error') await report('Error', outcome.message)
  })
}

function removePublisher(): void {
  const publisher = aliasPublisher.value
  if (!publisher) return
  void withBusy('delete', async () => {
    const outcome = await props.intents.remove(publisher.id, publisher.name)
    if (outcome.status === 'error') await report('Error', outcome.message)
  })
}

watch(aliasPublisherId, (id) => {
  if (!id) return
  void nextTick(() => focusAliasInput())
})

onMounted(() => {
  window.prksRefreshIcons?.(rootEl.value)
  unregisterAlias = registerPublishersAliasCloser(closeAlias)
  if (aliasPublisherId.value) void nextTick(() => focusAliasInput())
})

onUnmounted(() => {
  unregisterAlias?.()
  unregisterAlias = null
})
</script>

<template>
  <div ref="rootEl" class="tags-page publishers-page" data-prks-publishers-page>
    <div class="prks-page-header page-header tags-page__header publishers-page__header">
      <div class="publishers-page__header-lede">
        <h2 class="prks-page-title">Publishers</h2>
        <p class="tags-page__sub publishers-page__sub">
          Canonical names and alternate spellings for search. Files still store whatever publisher string each book
          has; search matches a substring on that field, or treats exact matches as the same publisher when you define
          aliases (e.g. “OUP” and “Oxford University Press”). Click row to view files; use <strong>Aliases</strong> to
          edit variants.
        </p>
      </div>
      <div class="publishers-page__add">
        <label class="search-advanced__label" for="publishers-page-new-name">New canonical publisher</label>
        <div class="publishers-page__add-row">
          <div class="tag-add-shell combobox-container publishers-page__add-shell">
            <div class="tag-add-shell__field">
              <span class="work-html-slot" v-html="plusIcon"></span>
              <input
                id="publishers-page-new-name"
                v-model="draftName"
                type="text"
                class="tag-add-shell__input"
                maxlength="200"
                placeholder="e.g. Oxford University Press"
                autocomplete="off"
                aria-label="New canonical publisher name"
              >
            </div>
          </div>
          <button
            id="publishers-page-add-btn"
            type="button"
            class="tags-page-alias-add__submit"
            :disabled="actionBlocked('create') || actionBusy('create')"
            @click="createPublisher"
          >
            Add
          </button>
        </div>
      </div>
    </div>
    <div id="publishers-page-cloud" class="list-view publishers-page__list">
      <p v-if="!rows.length" class="tags-page__empty publishers-page__empty">
        No publisher groups yet. Add a canonical name below, then add alternate spellings that appear on your files (⋯).
      </p>
      <div
        v-for="row in rows"
        :key="row.id"
        class="project-card publishers-page__list-item"
        :data-prks-route="row.searchHash"
        data-prks-middleclick-nav="1"
      >
        <div
          class="publishers-page__list-main"
          role="button"
          tabindex="0"
          :data-publisher-nav="row.encodedName"
          :aria-label="`View files for publisher ${row.name}`"
          @keydown="onRowKeydown(row, $event)"
        >
          <span class="publishers-page__badge">
            <span class="work-html-slot" v-html="buildingIcon"></span>
            <span>{{ row.name }}</span>
          </span>
          <p class="meta-row publishers-page__list-stats">{{ row.stats }}</p>
        </div>
        <div class="publishers-page__list-actions">
          <button
            type="button"
            class="prks-btn prks-btn--secondary prks-btn--sm publishers-page__alias-btn"
            :data-publisher-alias-edit="row.id"
            title="Aliases"
            :aria-label="`Edit aliases for ${row.name}`"
            @click.stop="openAlias(row, $event)"
          >
            ⋯<span>Aliases</span>
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="aliasPublisher"
      id="publishers-page-alias-backdrop"
      class="modal-backdrop tags-page-alias-backdrop"
      role="presentation"
      @click.self="closeAlias"
    >
      <div
        id="publishers-page-alias-modal"
        class="modal tags-page-alias-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="publishers-page-alias-heading"
        tabindex="-1"
      >
        <div class="modal-header">
          <h3 id="publishers-page-alias-heading">Publisher aliases</h3>
          <button
            id="publishers-page-alias-modal-close"
            type="button"
            class="prks-icon-btn close-btn"
            aria-label="Close"
            @click="closeAlias"
          >
            ×
          </button>
        </div>
        <div class="modal-body tags-page-alias-modal__body">
          <p class="modal-helper">
            Alternate spellings that appear on some books. Search for any of these (or the canonical name) includes
            files whose publisher field exactly matches any label in this group (case-insensitive), or contains your
            search as a substring.
          </p>
          <p class="tags-page-alias-panel__for">
            Canonical name: <strong id="publishers-page-alias-canonical">{{ aliasPublisher.name }}</strong>
          </p>
          <ul id="publishers-page-alias-list" class="tags-page-alias-list">
            <li v-if="!aliasPublisher.aliases.length" class="tags-page-alias-list__none">No aliases yet.</li>
            <li v-for="alias in aliasPublisher.aliases" :key="alias" class="tags-page-alias-list__item">
              <span class="tags-page-alias-list__text">{{ alias }}</span>
              <button
                type="button"
                class="tags-page-alias-remove"
                :data-publisher-alias-remove="alias"
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
              id="publishers-page-alias-input"
              v-model="aliasDraft"
              type="text"
              class="tags-page-alias-input"
              maxlength="200"
              placeholder="New alias…"
              autocomplete="off"
              aria-label="New alias"
            >
            <button
              id="publishers-page-alias-add-btn"
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
              id="publishers-page-delete-btn"
              type="button"
              class="prks-btn prks-btn--danger"
              :disabled="actionBlocked('delete')"
              @click="removePublisher"
            >
              Delete publisher
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
