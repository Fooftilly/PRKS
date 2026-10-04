<script setup lang="ts">
import { computed, inject, onMounted, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import { argumentIntentsKey } from './intents'
import { useArgumentPendingAction } from './pending-action'
import { defaultArgumentVerdict } from './match'
import { researchMarkdownHtml } from './markdown'
import {
  argumentEditorDraftFromForm,
  createEditorRowKeys,
  draftFromArgument,
  type ArgumentDetailProjection,
} from './projection'
import type {
  ArgumentEditorForm,
  ArgumentEditorSourceRow,
  ArgumentEditorTargetRow,
  ArgumentMentionRef,
  ArgumentResponseRef,
  ArgumentSourceRef,
  ArgumentTargetRef,
} from './types'

const MUTATION_ROLE = 'argument-mutation-control'

const props = defineProps<{
  projection: ArgumentDetailProjection
}>()

const intents = inject(argumentIntentsKey)
const rootEl = ref<HTMLElement | null>(null)
const textHeadHost = ref<HTMLElement | null>(null)
const textHost = ref<HTMLElement | null>(null)
const targetsHeadHost = ref<HTMLElement | null>(null)
const targetsHost = ref<HTMLElement | null>(null)
const sourcesHeadHost = ref<HTMLElement | null>(null)
const sourcesHost = ref<HTMLElement | null>(null)
const responsesHeadHost = ref<HTMLElement | null>(null)
const responsesHost = ref<HTMLElement | null>(null)
const mentionsHeadHost = ref<HTMLElement | null>(null)
const mentionsHost = ref<HTMLElement | null>(null)

const editing = ref(false)
const draft = ref<ArgumentEditorForm | null>(null)
const nextRowKey = createEditorRowKeys()
const { actionBusy, actionBlocked, withBusy } = useArgumentPendingAction()

const availability = computed(() => props.projection.availability)
const argument = computed(() => props.projection.argument)
const ready = computed(() => availability.value === 'ready' && !!argument.value)
const kindLabel = computed(() => (argument.value?.kind === 'stance' ? 'Stance' : 'Argument'))

function escHtml(value: string): string {
  const fn = window.prksEscapeHtml
  if (typeof fn === 'function') return fn(value)
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function researchRows(rows: string): string {
  return rows
}

function linkRow(href: string, title: string, kind: string, meta: string[]): string {
  const rowHtml = window.prksResearchIndexRowHtml
  const safeMeta = meta.filter(Boolean)
  if (typeof rowHtml === 'function') {
    return rowHtml({
      href,
      title: escHtml(title),
      kind: kind ? escHtml(kind) : '',
      meta: safeMeta.map((item) => escHtml(item)),
    })
  }
  return `<a class="prks-list-row prks-research-row" href="${href}">${escHtml(title)}</a>`
}

function targetRowsHtml(rows: readonly ArgumentTargetRef[]): string {
  return rows
    .map((row) => {
      const href =
        row.type === 'position'
          ? `#/positions/${encodeURIComponent(row.id)}`
          : `#/arguments/${encodeURIComponent(row.id)}`
      const kind = row.type === 'position' ? 'Position' : row.kind === 'stance' ? 'Stance' : 'Argument'
      return linkRow(href, row.name || row.id, kind, [row.verdict_label || row.verdict_id || ''])
    })
    .join('')
}

function sourceAuthorsLabel(source: ArgumentSourceRef): string {
  return source.authors
    .map((author) => `${author.first_name || ''} ${author.last_name || ''}`.trim() || author.credit_name || '')
    .filter(Boolean)
    .join(', ')
}

function sourceRowsHtml(rows: readonly ArgumentSourceRef[]): string {
  return rows
    .map((row) => {
      const pages = row.pages ? `pp. ${row.pages}` : ''
      return linkRow(
        `#/works/${encodeURIComponent(row.work_id)}`,
        row.work_title || row.work_id,
        '',
        [sourceAuthorsLabel(row), pages].filter(Boolean),
      )
    })
    .join('')
}

function responseRowsHtml(rows: readonly ArgumentResponseRef[]): string {
  return rows
    .map((row) => {
      const kind = row.kind === 'stance' ? 'Stance' : 'Argument'
      return linkRow(
        `#/arguments/${encodeURIComponent(row.id)}`,
        row.name || row.id,
        kind,
        [row.verdict_label || row.verdict_id || ''],
      )
    })
    .join('')
}

function mentionRowsHtml(rows: readonly ArgumentMentionRef[]): string {
  return rows
    .map((row) => linkRow(`#/works/${encodeURIComponent(row.work_id)}`, row.title || row.work_id, '', []))
    .join('')
}

function paintSectionHead(
  host: HTMLElement | null,
  title: string,
  opts: { headingId?: string; count?: number; sub?: string },
): void {
  if (!host) return
  const fn = window.prksResearchSectionHeadHtml
  host.innerHTML = typeof fn === 'function' ? fn(title, opts) : ''
}

function paintRead(): void {
  const current = argument.value
  if (!current || editing.value) return
  const text = String(current.main_text || '')
  if (textHost.value) {
    textHost.value.innerHTML = text.trim()
      ? researchMarkdownHtml(text)
      : '<p class="meta-row">No main text yet.</p>'
  }
  paintSectionHead(textHeadHost.value, 'Main text', { headingId: 'prks-arg-text-h' })
  paintSectionHead(targetsHeadHost.value, 'Responds to', {
    headingId: 'prks-arg-targets-h',
    count: current.targets.length,
  })
  paintSectionHead(sourcesHeadHost.value, 'Sources', {
    headingId: 'prks-arg-sources-h',
    count: current.sources.length,
    sub: 'Works where this was made or taken.',
  })
  paintSectionHead(responsesHeadHost.value, 'Responses', {
    headingId: 'prks-arg-resp-h',
    count: current.responses.length,
  })
  paintSectionHead(mentionsHeadHost.value, 'Mentioned in notes', {
    headingId: 'prks-arg-mentions-h',
    count: current.mentions.length,
  })
  if (targetsHost.value) targetsHost.value.innerHTML = researchRows(targetRowsHtml(current.targets))
  if (sourcesHost.value) sourcesHost.value.innerHTML = researchRows(sourceRowsHtml(current.sources))
  if (responsesHost.value) responsesHost.value.innerHTML = researchRows(responseRowsHtml(current.responses))
  if (mentionsHost.value) mentionsHost.value.innerHTML = researchRows(mentionRowsHtml(current.mentions))
  const root = rootEl.value
  if (root && typeof window.prksRefreshIcons === 'function') window.prksRefreshIcons(root)
}

function leaveEdit(): void {
  editing.value = false
  draft.value = null
  intents?.cancelEdit()
}

function verdictChoices(selected: string): { id: string; label: string }[] {
  const verdicts = argument.value?.verdicts ?? []
  const choices = verdicts.map((verdict) => ({ id: verdict.id, label: verdict.label || verdict.id }))
  if (selected && !choices.some((choice) => choice.id === selected)) {
    choices.unshift({ id: selected, label: selected })
  }
  return choices
}

function targetKindLabel(row: ArgumentEditorTargetRow): string {
  if (row.type === 'position') return 'Position'
  if (row.kind === 'stance') return 'Stance'
  return 'Argument'
}

async function onEdit(): Promise<void> {
  const current = argument.value
  if (!current || !intents) return
  const generation = props.projection.generation
  await withBusy('edit', async () => {
    const ok = await intents.enterEdit(current.id)
    if (!ok || props.projection.generation !== generation || argument.value?.id !== current.id) return
    draft.value = draftFromArgument(current, nextRowKey)
    editing.value = true
  })
}

function onCancel(): void {
  leaveEdit()
}

async function onSave(): Promise<void> {
  const current = argument.value
  const body = draft.value
  if (!current || !body || !intents) return
  await withBusy('save', async () => {
    const ok = await intents.save(current.id, argumentEditorDraftFromForm(body))
    if (ok && argument.value?.id === current.id) {
      editing.value = false
      draft.value = null
    }
  })
}

function onViewGraph(): void {
  if (argument.value) intents?.viewGraph(argument.value)
}

function onResponse(): void {
  const current = argument.value
  if (!current) return
  void withBusy('response', () => intents?.createResponse(current) ?? Promise.resolve())
}

function onDelete(): void {
  const current = argument.value
  if (!current) return
  void withBusy('delete', () => intents?.remove(current) ?? Promise.resolve())
}

function addTarget(): void {
  const body = draft.value
  const current = argument.value
  if (!body || !current) return
  void withBusy('add-target', async () => {
    const verdict = defaultArgumentVerdict(body.kind)
    await intents?.pickTarget(current.id, (picked) => {
      body.targets.push({
        rowKey: nextRowKey(),
        type: picked.type,
        id: picked.id,
        name: picked.name || picked.id,
        kind: picked.kind,
        verdict_id: verdict,
      })
    })
  })
}

function addSource(): void {
  const body = draft.value
  const current = argument.value
  if (!body || !current) return
  void withBusy('add-source', async () => {
    await intents?.pickSource(current.id, (picked) => {
      body.sources.push({
        rowKey: nextRowKey(),
        work_id: picked.work_id,
        work_title: picked.work_title || picked.work_id,
        pages: '',
      })
    })
  })
}

function removeTarget(rowKey: string): void {
  const body = draft.value
  if (!body) return
  body.targets = body.targets.filter((row) => row.rowKey !== rowKey)
}

function removeSource(rowKey: string): void {
  const body = draft.value
  if (!body) return
  body.sources = body.sources.filter((row) => row.rowKey !== rowKey)
}

function repaintTarget(row: ArgumentEditorTargetRow): void {
  void withBusy(`target:${row.rowKey}`, async () => {
    await intents?.pickTarget(argument.value?.id || '', (picked) => {
      row.type = picked.type
      row.id = picked.id
      row.name = picked.name || picked.id
      row.kind = picked.kind
    })
  })
}

function repaintSource(row: ArgumentEditorSourceRow): void {
  void withBusy(`source:${row.rowKey}`, async () => {
    await intents?.pickSource(argument.value?.id || '', (picked) => {
      row.work_id = picked.work_id
      row.work_title = picked.work_title || picked.work_id
    })
  })
}

onMounted(() => {
  paintRead()
})

watch(
  () => props.projection.generation,
  () => {
    leaveEdit()
  },
)

watch(
  () =>
    [
      props.projection.generation,
      editing.value,
      argument.value?.id,
      argument.value?.name,
      argument.value?.kind,
      argument.value?.main_text,
      argument.value?.targets,
      argument.value?.sources,
      argument.value?.responses,
      argument.value?.mentions,
    ] as const,
  () => {
    if (!editing.value) paintRead()
  },
  { flush: 'post' },
)
</script>

<template>
  <div ref="rootEl" data-prks-argument-detail-view>
    <template v-if="availability === 'unavailable'">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Argument or Stance not available offline</h2>
      </div>
      <PrksInlineMessage data-prks-role="offline-unavailable">
        This item is not available offline.
      </PrksInlineMessage>
    </template>
    <template v-else-if="availability === 'not-found' || !argument">
      <div class="prks-page-header page-header">
        <h2 class="prks-page-title">Argument not found.</h2>
      </div>
      <p class="meta-row">
        <a class="prks-btn prks-btn--secondary" href="#/arguments">Back to Arguments &amp; Stances</a>
      </p>
    </template>
    <template v-else-if="ready && argument">
      <div class="prks-page-header page-header">
        <div class="page-header__title-row">
          <div>
            <p class="saved-view-detail__kicker">{{ kindLabel }}</p>
            <h2 class="prks-page-title">{{ argument.name || argument.id }}</h2>
          </div>
          <div class="page-header__actions">
            <template v-if="editing">
              <button type="button" class="prks-btn prks-btn--secondary" id="prks-arg-cancel" @click="onCancel">
                Cancel
              </button>
            </template>
            <template v-else>
              <button
                type="button"
                class="prks-btn prks-btn--secondary"
                id="prks-arg-view-graph"
                @click="onViewGraph"
              >
                View in graph
              </button>
              <PrksButton
                id="prks-arg-edit"
                :data-prks-role="MUTATION_ROLE"
                :busy="actionBusy('edit')"
                :disabled="actionBlocked('edit')"
                busy-label="Editing…"
                @click="onEdit"
              >
                Edit
              </PrksButton>
              <PrksButton
                id="prks-arg-response"
                :data-prks-role="MUTATION_ROLE"
                :busy="actionBusy('response')"
                :disabled="actionBlocked('response')"
                busy-label="Creating…"
                @click="onResponse"
              >
                New response
              </PrksButton>
              <PrksButton
                id="prks-arg-delete"
                variant="ghost"
                class="prks-btn--quiet-danger prks-page-action--destructive"
                :data-prks-role="MUTATION_ROLE"
                :busy="actionBusy('delete')"
                :disabled="actionBlocked('delete')"
                busy-label="Deleting…"
                @click="onDelete"
              >
                Delete
              </PrksButton>
            </template>
          </div>
        </div>
      </div>
      <div v-if="!editing" class="research-entity">
        <section class="research-entity__section" aria-labelledby="prks-arg-text-h">
          <div ref="textHeadHost"></div>
          <div ref="textHost" class="research-md"></div>
        </section>
        <section class="research-entity__section" aria-labelledby="prks-arg-targets-h">
          <div ref="targetsHeadHost"></div>
          <div v-if="argument.targets.length" ref="targetsHost" class="list-view prks-research-index"></div>
          <p v-else class="meta-row">No targets.</p>
        </section>
        <section class="research-entity__section" aria-labelledby="prks-arg-sources-h">
          <div ref="sourcesHeadHost"></div>
          <div v-if="argument.sources.length" ref="sourcesHost" class="list-view prks-research-index"></div>
          <p v-else class="meta-row">No sources.</p>
        </section>
        <section class="research-entity__section" aria-labelledby="prks-arg-resp-h">
          <div ref="responsesHeadHost"></div>
          <div v-if="argument.responses.length" ref="responsesHost" class="list-view prks-research-index"></div>
          <p v-else class="meta-row">No responses.</p>
        </section>
        <section class="research-entity__section" aria-labelledby="prks-arg-mentions-h">
          <div ref="mentionsHeadHost"></div>
          <div v-if="argument.mentions.length" ref="mentionsHost" class="list-view prks-research-index"></div>
          <p v-else class="meta-row">Not mentioned in research notes.</p>
        </section>
      </div>
      <form v-else-if="draft" id="prks-arg-form" class="prks-arg-form form-pane" @submit.prevent="onSave">
        <label class="form-field-label" for="prks-arg-name">Name</label>
        <input id="prks-arg-name" v-model="draft.name" type="text">
        <label class="form-field-label" for="prks-arg-kind">Kind</label>
        <select id="prks-arg-kind" v-model="draft.kind">
          <option value="argument">Argument</option>
          <option value="stance">Stance</option>
        </select>
        <label class="form-field-label" for="prks-arg-text">Main text</label>
        <textarea id="prks-arg-text" v-model="draft.main_text" class="textarea-md" rows="8"></textarea>
        <h3>Responds to</h3>
        <div id="prks-arg-targets">
          <p v-if="!draft.targets.length" class="meta-row">None yet.</p>
          <div v-for="row in draft.targets" :key="row.rowKey" class="prks-arg-row">
            <span class="prks-research-row__kicker">{{ targetKindLabel(row) }}</span>
            <PrksButton
              class="prks-arg-rel__pick"
              data-pick="target"
              :busy="actionBusy(`target:${row.rowKey}`)"
              :disabled="actionBlocked(`target:${row.rowKey}`)"
              busy-label="Choosing…"
              @click="repaintTarget(row)"
            >
              {{ row.name || row.id || 'Choose…' }}
            </PrksButton>
            <label class="form-field-label" :for="`prks-arg-verdict-${row.rowKey}`">Verdict</label>
            <select
              :id="`prks-arg-verdict-${row.rowKey}`"
              v-model="row.verdict_id"
              data-field="verdict"
              aria-label="Verdict"
            >
              <option v-for="choice in verdictChoices(row.verdict_id)" :key="choice.id" :value="choice.id">
                {{ choice.label }}
              </option>
            </select>
            <button
              type="button"
              class="prks-btn prks-btn--ghost prks-btn--sm"
              data-remove="target"
              @click="removeTarget(row.rowKey)"
            >
              Remove
            </button>
          </div>
        </div>
        <PrksButton
          id="prks-arg-add-target"
          size="sm"
          :busy="actionBusy('add-target')"
          :disabled="actionBlocked('add-target')"
          busy-label="Adding…"
          @click="addTarget"
        >
          Add target
        </PrksButton>
        <h3>Sources</h3>
        <p class="meta-row">Works where this was made or taken.</p>
        <div id="prks-arg-sources">
          <p v-if="!draft.sources.length" class="meta-row">None yet.</p>
          <div v-for="row in draft.sources" :key="row.rowKey" class="prks-arg-row">
            <PrksButton
              class="prks-arg-rel__pick"
              data-pick="source"
              :busy="actionBusy(`source:${row.rowKey}`)"
              :disabled="actionBlocked(`source:${row.rowKey}`)"
              busy-label="Choosing…"
              @click="repaintSource(row)"
            >
              {{ row.work_title || row.work_id || 'Choose a work…' }}
            </PrksButton>
            <input
              v-model="row.pages"
              type="text"
              data-field="pages"
              placeholder="pages"
              maxlength="100"
              aria-label="Pages"
            >
            <button
              type="button"
              class="prks-btn prks-btn--ghost prks-btn--sm"
              data-remove="source"
              @click="removeSource(row.rowKey)"
            >
              Remove
            </button>
          </div>
        </div>
        <PrksButton
          id="prks-arg-add-source"
          size="sm"
          :busy="actionBusy('add-source')"
          :disabled="actionBlocked('add-source')"
          busy-label="Adding…"
          @click="addSource"
        >
          Add source
        </PrksButton>
        <p class="prks-arg-form__actions">
          <PrksButton type="submit" variant="primary" class="prks-btn prks-btn--primary" :busy="actionBusy('save')" busy-label="Saving…">
            Save
          </PrksButton>
        </p>
      </form>
    </template>
  </div>
</template>
