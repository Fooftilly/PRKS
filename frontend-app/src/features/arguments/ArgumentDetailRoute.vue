<script setup lang="ts">
import { computed, inject, ref, watch } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksField from '../../components/PrksField.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import PrksLinkButton from '../../components/PrksLinkButton.vue'
import PrksResearchRow from '../../components/PrksResearchRow.vue'
import PrksResearchSectionHead from '../../components/PrksResearchSectionHead.vue'
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
  ArgumentSourceRef,
} from './types'

const MUTATION_ROLE = 'argument-mutation-control'

const props = defineProps<{
  projection: ArgumentDetailProjection
}>()

const intents = inject(argumentIntentsKey)

const editing = ref(false)
const draft = ref<ArgumentEditorForm | null>(null)
const nextRowKey = createEditorRowKeys()
const { actionBusy, actionBlocked, withBusy } = useArgumentPendingAction()

const availability = computed(() => props.projection.availability)
const argument = computed(() => props.projection.argument)
const ready = computed(() => availability.value === 'ready' && !!argument.value)
const kindLabel = computed(() => (argument.value?.kind === 'stance' ? 'Stance' : 'Argument'))
const mainTextHtml = computed(() => {
  const text = String(argument.value?.main_text || '')
  return text.trim() ? researchMarkdownHtml(text) : '<p class="meta-row">No main text yet.</p>'
})

function sourceAuthorsLabel(source: ArgumentSourceRef): string {
  return source.authors
    .map((author) => `${author.first_name || ''} ${author.last_name || ''}`.trim() || author.credit_name || '')
    .filter(Boolean)
    .join(', ')
}

function targetHref(row: { type: string; id: string }): string {
  return row.type === 'position'
    ? `#/positions/${encodeURIComponent(row.id)}`
    : `#/arguments/${encodeURIComponent(row.id)}`
}

function targetKind(row: { type: string; kind?: string }): string {
  if (row.type === 'position') return 'Position'
  return row.kind === 'stance' ? 'Stance' : 'Argument'
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

watch(
  () => props.projection.generation,
  () => {
    leaveEdit()
  },
)
</script>

<template>
  <div data-prks-argument-detail-view>
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
        <PrksLinkButton href="#/arguments">Back to Arguments &amp; Stances</PrksLinkButton>
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
              <PrksButton id="prks-arg-cancel" @click="onCancel">
                Cancel
              </PrksButton>
            </template>
            <template v-else>
              <PrksButton id="prks-arg-view-graph" @click="onViewGraph">
                View in graph
              </PrksButton>
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
                variant="quiet-danger"
                class="prks-page-action--destructive"
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
          <PrksResearchSectionHead title="Main text" heading-id="prks-arg-text-h" />
          <div class="research-md" v-html="mainTextHtml"></div>
        </section>
        <section class="research-entity__section" aria-labelledby="prks-arg-targets-h">
          <PrksResearchSectionHead
            title="Responds to"
            heading-id="prks-arg-targets-h"
            :count="argument.targets.length"
          />
          <div v-if="argument.targets.length" class="list-view prks-research-index">
            <PrksResearchRow
              v-for="row in argument.targets"
              :key="`${row.type}:${row.id}`"
              :href="targetHref(row)"
              :title="row.name || row.id"
              :kind="targetKind(row)"
              :meta="[row.verdict_label || row.verdict_id || ''].filter(Boolean)"
            />
          </div>
          <p v-else class="meta-row">No targets.</p>
        </section>
        <section class="research-entity__section" aria-labelledby="prks-arg-sources-h">
          <PrksResearchSectionHead
            title="Sources"
            heading-id="prks-arg-sources-h"
            :count="argument.sources.length"
            sub="Works where this was made or taken."
          />
          <div v-if="argument.sources.length" class="list-view prks-research-index">
            <PrksResearchRow
              v-for="row in argument.sources"
              :key="row.work_id"
              :href="`#/works/${encodeURIComponent(row.work_id)}`"
              :title="row.work_title || row.work_id"
              :meta="[sourceAuthorsLabel(row), row.pages ? `pp. ${row.pages}` : ''].filter(Boolean)"
            />
          </div>
          <p v-else class="meta-row">No sources.</p>
        </section>
        <section class="research-entity__section" aria-labelledby="prks-arg-resp-h">
          <PrksResearchSectionHead
            title="Responses"
            heading-id="prks-arg-resp-h"
            :count="argument.responses.length"
          />
          <div v-if="argument.responses.length" class="list-view prks-research-index">
            <PrksResearchRow
              v-for="row in argument.responses"
              :key="row.id"
              :href="`#/arguments/${encodeURIComponent(row.id)}`"
              :title="row.name || row.id"
              :kind="row.kind === 'stance' ? 'Stance' : 'Argument'"
              :meta="[row.verdict_label || row.verdict_id || ''].filter(Boolean)"
            />
          </div>
          <p v-else class="meta-row">No responses.</p>
        </section>
        <section class="research-entity__section" aria-labelledby="prks-arg-mentions-h">
          <PrksResearchSectionHead
            title="Mentioned in notes"
            heading-id="prks-arg-mentions-h"
            :count="argument.mentions.length"
          />
          <div v-if="argument.mentions.length" class="list-view prks-research-index">
            <PrksResearchRow
              v-for="row in argument.mentions"
              :key="row.work_id"
              :href="`#/works/${encodeURIComponent(row.work_id)}`"
              :title="row.title || row.work_id"
            />
          </div>
          <p v-else class="meta-row">Not mentioned in research notes.</p>
        </section>
      </div>
      <form v-else-if="draft" id="prks-arg-form" class="prks-arg-form form-pane" @submit.prevent="onSave">
        <PrksField v-slot="{ labelledBy, describedBy }" label="Name" for-id="prks-arg-name">
          <input id="prks-arg-name" v-model="draft.name" type="text" :aria-labelledby="labelledBy" :aria-describedby="describedBy">
        </PrksField>
        <PrksField v-slot="{ labelledBy, describedBy }" label="Kind" for-id="prks-arg-kind">
          <select id="prks-arg-kind" v-model="draft.kind" :aria-labelledby="labelledBy" :aria-describedby="describedBy">
            <option value="argument">Argument</option>
            <option value="stance">Stance</option>
          </select>
        </PrksField>
        <PrksField v-slot="{ labelledBy, describedBy }" label="Main text" for-id="prks-arg-text">
          <textarea id="prks-arg-text" v-model="draft.main_text" class="textarea-md" rows="8" :aria-labelledby="labelledBy" :aria-describedby="describedBy"></textarea>
        </PrksField>
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
            <PrksButton
              variant="ghost"
              size="sm"
              data-remove="target"
              @click="removeTarget(row.rowKey)"
            >
              Remove
            </PrksButton>
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
            <PrksButton
              variant="ghost"
              size="sm"
              data-remove="source"
              @click="removeSource(row.rowKey)"
            >
              Remove
            </PrksButton>
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
          <PrksButton type="submit" variant="primary" :busy="actionBusy('save')" busy-label="Saving…">
            Save
          </PrksButton>
        </p>
      </form>
    </template>
  </div>
</template>
