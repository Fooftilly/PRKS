<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksIconButton from '../../components/PrksIconButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import PrksState from '../../components/PrksState.vue'
import { usePendingAction } from '../../route-surface/pending-action'
import { registerPublishersAliasCloser } from './closers'
import type { PublishersIntents } from './intents'
import type { PublisherRow } from './projection'
import { usePublishersCatalog } from './usePublishersCatalog'

const props = defineProps<{
  intents: PublishersIntents
}>()

const { rows, loaded, loading, loadError, refreshError, retry } = usePublishersCatalog()

const { actionBusy, actionBlocked, withBusy } = usePendingAction()
const rootEl = ref<HTMLElement | null>(null)
const draftName = ref('')
const aliasPublisherId = ref<string | null>(null)
const aliasDraft = ref('')
const aliasTrigger = ref<HTMLElement | null>(null)
const createError = ref('')
const aliasAddError = ref('')
const aliasRemoveError = ref('')
const aliasDeleteError = ref('')
const aliasPublisher = computed(() => rows.value.find((row) => row.id === aliasPublisherId.value) ?? null)
const plusIcon = computed(() => window.prksTagPlusIconHtml?.() ?? '')
const buildingIcon = computed(() => window.prksIcon?.('building-2', { size: 'sm' }) ?? '')

let unregisterAlias: (() => void) | null = null

function aliasEditButton(publisherId: string): HTMLElement | null {
  const root = rootEl.value
  if (!root) return null
  const escaped = typeof CSS !== 'undefined' && typeof CSS.escape === 'function' ? CSS.escape(publisherId) : publisherId
  const node = root.querySelector(`[data-publisher-alias-edit="${escaped}"]`)
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

function closeAlias(): void {
  const trigger = aliasTrigger.value
  const publisherId = aliasPublisherId.value
  aliasPublisherId.value = null
  aliasDraft.value = ''
  aliasTrigger.value = null
  void nextTick(() => restoreFocus(trigger, publisherId ? aliasEditButton(publisherId) : null))
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

function focusAliasInput(): void {
  rootEl.value?.querySelector<HTMLInputElement>('#publishers-page-alias-input')?.focus()
}

function clearAliasActionErrors(): void {
  aliasAddError.value = ''
  aliasRemoveError.value = ''
  aliasDeleteError.value = ''
}

function createPublisher(): void {
  const name = draftName.value
  void withBusy('create', async () => {
    const outcome = await props.intents.create(name)
    if (outcome.status === 'error') createError.value = outcome.message
    else if (outcome.status === 'success') {
      createError.value = ''
      draftName.value = ''
    }
  })
}

function addAlias(): void {
  const publisher = aliasPublisher.value
  if (!publisher) return
  const publisherId = publisher.id
  const draft = aliasDraft.value
  void withBusy('alias-add', async () => {
    const outcome = await props.intents.addAlias(publisherId, draft)
    if (aliasPublisherId.value !== publisherId) return
    if (outcome.status === 'error') aliasAddError.value = outcome.message
    else if (outcome.status === 'success') {
      aliasAddError.value = ''
      aliasDraft.value = ''
    }
  })
}

function removeAlias(alias: string): void {
  const publisher = aliasPublisher.value
  if (!publisher) return
  const publisherId = publisher.id
  void withBusy(`alias-remove:${alias}`, async () => {
    const outcome = await props.intents.removeAlias(publisherId, alias)
    if (aliasPublisherId.value !== publisherId) return
    if (outcome.status === 'error') aliasRemoveError.value = outcome.message
    else if (outcome.status === 'success') aliasRemoveError.value = ''
  })
}

function removePublisher(): void {
  const publisher = aliasPublisher.value
  if (!publisher) return
  const publisherId = publisher.id
  void withBusy('delete', async () => {
    const outcome = await props.intents.remove(publisherId, publisher.name)
    if (aliasPublisherId.value !== publisherId) return
    if (outcome.status === 'error') aliasDeleteError.value = outcome.message
    else if (outcome.status === 'success') aliasDeleteError.value = ''
  })
}

watch(aliasPublisherId, (id) => {
  clearAliasActionErrors()
  if (!id) return
  void nextTick(() => focusAliasInput())
})

// Rows arrive after mount; their row icons are placeholders until refreshed.
watch(rows, () => window.prksRefreshIcons?.(rootEl.value), { flush: 'post' })

// A publisher deleted here or in another pane closes its alias dialog.
watch(rows, (next) => {
  const id = aliasPublisherId.value
  if (id && !next.some((row) => row.id === id)) closeAlias()
})

onMounted(() => {
  window.prksRefreshIcons?.(rootEl.value)
  unregisterAlias = registerPublishersAliasCloser(closeAlias)
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
                :aria-invalid="createError ? 'true' : undefined"
                :aria-describedby="createError ? 'publishers-page-create-error' : undefined"
              >
            </div>
          </div>
          <PrksButton
            id="publishers-page-add-btn"
            class="tags-page-alias-add__submit"
            variant="secondary"
            size="sm"
            :busy="actionBusy('create')"
            :disabled="actionBlocked('create')"
            busy-label="Adding…"
            @click="createPublisher"
          >
            Add
          </PrksButton>
        </div>
        <PrksInlineMessage
          v-if="createError"
          id="publishers-page-create-error"
          tone="error"
          status
          data-publishers-create-error
        >
          {{ createError }}
        </PrksInlineMessage>
      </div>
    </div>
    <PrksInlineMessage v-if="refreshError" tone="error" status data-publishers-refresh-error>
      {{ refreshError }}
    </PrksInlineMessage>
    <PrksState v-if="loading" kind="loading" message="Loading publishers…" data-publishers-loading />
    <PrksState v-else-if="loadError" kind="error" :message="loadError" data-publishers-load-error>
      <PrksButton variant="secondary" size="sm" @click="retry">Try again</PrksButton>
    </PrksState>
    <div v-if="loaded" id="publishers-page-cloud" class="list-view publishers-page__list">
      <p v-if="!rows.length" class="tags-page__empty publishers-page__empty">
        No publisher groups yet. Add a canonical name below, then add alternate spellings that appear on your files (⋯).
      </p>
      <div
        v-for="row in rows"
        :key="row.id"
        class="project-card publishers-page__list-item"
      >
        <div
          class="publishers-page__list-main"
          role="button"
          tabindex="0"
          :data-prks-route="row.searchHash"
          data-prks-middleclick-nav="1"
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
          <PrksIconButton
            id="publishers-page-alias-modal-close"
            class="close-btn"
            label="Close"
            @click="closeAlias"
          >
            ×
          </PrksIconButton>
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
                :class="{ 'tags-page-alias-remove--busy': actionBusy(`alias-remove:${alias}`) }"
                :data-publisher-alias-remove="alias"
                :aria-label="actionBusy(`alias-remove:${alias}`) ? 'Removing…' : 'Remove alias'"
                :aria-busy="actionBusy(`alias-remove:${alias}`) ? 'true' : undefined"
                :disabled="actionBlocked(`alias-remove:${alias}`) || actionBusy(`alias-remove:${alias}`)"
                @click="removeAlias(alias)"
              >
                <template v-if="actionBusy(`alias-remove:${alias}`)">Removing…</template>
                <template v-else>×</template>
              </button>
            </li>
          </ul>
          <PrksInlineMessage
            v-if="aliasRemoveError"
            id="publishers-page-alias-remove-error"
            tone="error"
            status
            data-publishers-alias-remove-error
          >
            {{ aliasRemoveError }}
          </PrksInlineMessage>
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
              :aria-invalid="aliasAddError ? 'true' : undefined"
              :aria-describedby="aliasAddError ? 'publishers-page-alias-add-error' : undefined"
            >
            <PrksButton
              id="publishers-page-alias-add-btn"
              class="tags-page-alias-add__submit"
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
            id="publishers-page-alias-add-error"
            tone="error"
            status
            data-publishers-alias-add-error
          >
            {{ aliasAddError }}
          </PrksInlineMessage>
          <div class="tags-page-alias-delete">
            <PrksButton
              id="publishers-page-delete-btn"
              variant="danger"
              :busy="actionBusy('delete')"
              :disabled="actionBlocked('delete')"
              busy-label="Deleting…"
              @click="removePublisher"
            >
              Delete publisher
            </PrksButton>
            <PrksInlineMessage
              v-if="aliasDeleteError"
              id="publishers-page-delete-error"
              tone="error"
              status
              data-publishers-delete-error
            >
              {{ aliasDeleteError }}
            </PrksInlineMessage>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
