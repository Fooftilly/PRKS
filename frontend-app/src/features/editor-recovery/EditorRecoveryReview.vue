<script setup lang="ts">
/**
 * Review of unsaved recovery drafts for one editor (#466 slice 3).
 *
 * Every draft can be inspected and copied. The actions offered are the ones
 * the adapter judged safe for that draft now: Restore for editing when it
 * overwrites nothing, Compare when the current note must be weighed first,
 * and Discard (after confirmation). Compare shows the current note beside an
 * editable copy of the recovered text; only "Replace note with this text"
 * writes, and only through the editor's ordinary save path. Close changes
 * nothing. A refused action (the draft or note changed meanwhile) reloads
 * the list instead of acting on what it showed.
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksIconButton from '../../components/PrksIconButton.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import {
  completenessText,
  currentNoteText,
  failureText,
  lengthText,
  originText,
  pipelineText,
  queueText,
  reasonText,
  typedAtText,
} from './labels'
import type { RecoveryActionResult, RecoveryCandidateView, RecoveryDetails, ReviewActions } from './types'


const props = defineProps<{
  /** e.g. "Research Notes". */
  subject: string
  /** The Work's title, for identity. */
  entityTitle: string
  actions: ReviewActions
}>()

const emit = defineEmits<{
  close: []
}>()

const COPY_FEEDBACK_MS = 1500

const dialog = ref<HTMLElement | null>(null)
const details = ref<RecoveryDetails | null>(null)
const loading = ref(true)
const unavailable = ref(false)
const selectedId = ref<string | null>(null)
const busy = ref<string | null>(null)
const message = ref('')
const comparing = ref(false)
const chosenText = ref('')
const copyLabel = ref<Record<string, string>>({})
let copyTimers: Array<ReturnType<typeof setTimeout>> = []
let disposed = false

const selected = computed<RecoveryCandidateView | null>(() => {
  const list = details.value ? details.value.candidates : []
  return list.find((c) => c.draftId === selectedId.value) || null
})
const headingId = 'editor-recovery-review-heading'

async function load(keepMessage = false): Promise<void> {
  loading.value = true
  if (!keepMessage) message.value = ''
  const next = await props.actions.load().catch(() => null)
  if (disposed) return
  loading.value = false
  if (!next) {
    unavailable.value = true
    details.value = null
    return
  }
  unavailable.value = false
  details.value = next
  comparing.value = false
  // Never pick a winner: the first row is shown, nothing is applied.
  if (!next.candidates.some((c) => c.draftId === selectedId.value)) {
    selectedId.value = next.candidates.length ? next.candidates[0]!.draftId : null
  }
}

async function run(name: string, act: () => Promise<RecoveryActionResult>, closeOnSuccess: boolean): Promise<void> {
  if (busy.value) return
  busy.value = name
  message.value = ''
  let result: RecoveryActionResult
  try {
    result = await act()
  } catch {
    result = { ok: false, code: 'failed' }
  }
  if (disposed) return
  busy.value = null
  if (result.ok) {
    if (closeOnSuccess) {
      emit('close')
      return
    }
    await load()
    return
  }
  message.value = failureText(result.code)
  await load(true)
}

function restore(): void {
  const d = details.value
  const c = selected.value
  if (!d || !c) return
  void run('restore', () => props.actions.restore(d, c), true)
}

function startCompare(): void {
  const c = selected.value
  if (!c || c.body === null) return
  chosenText.value = c.body
  comparing.value = true
  message.value = ''
}

async function replace(): Promise<void> {
  const d = details.value
  const c = selected.value
  if (!d || !c || busy.value) return
  const revision = d.current.revision === null ? 'the current note' : `the current note (revision ${d.current.revision})`
  const ok = await props.actions.confirm({
    title: `Replace ${props.subject}?`,
    message: `The text in the right column replaces ${revision} and saves like an edit you typed. Copy the current note first if you still need it.`,
    confirmLabel: 'Replace note',
    danger: true,
  })
  if (!ok || disposed) return
  const shown = d.current
  const text = chosenText.value
  void run('replace', () => props.actions.replace(d, c, text, shown), true)
}

async function discard(): Promise<void> {
  const d = details.value
  const c = selected.value
  if (!d || !c || busy.value) return
  const ok = await props.actions.confirm({
    title: 'Discard this draft?',
    message: `Its ${lengthText(c.length)} are removed from this device. The current note does not change.`,
    confirmLabel: 'Discard draft',
    danger: true,
  })
  if (!ok || disposed) return
  void run('discard', () => props.actions.discard(d, c), false)
}

async function copy(key: string, text: string | null): Promise<void> {
  if (text === null) return
  let label = 'Copied'
  try {
    await props.actions.copy(text)
  } catch {
    label = 'Copy failed'
  }
  if (disposed) return
  copyLabel.value = { ...copyLabel.value, [key]: label }
  copyTimers.push(
    setTimeout(() => {
      const next = { ...copyLabel.value }
      delete next[key]
      copyLabel.value = next
    }, COPY_FEEDBACK_MS),
  )
}

function close(): void {
  if (busy.value) return
  emit('close')
}

onMounted(() => {
  void load().then(() => nextTick(() => dialog.value?.focus()))
})

onBeforeUnmount(() => {
  disposed = true
  for (const timer of copyTimers) clearTimeout(timer)
  copyTimers = []
})
</script>

<template>
  <div class="modal-backdrop" data-prks-role="editor-recovery-review-backdrop" role="presentation" @click.self="close">
    <div
      id="editor-recovery-review-modal"
      ref="dialog"
      class="modal editor-recovery-review"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="headingId"
      tabindex="-1"
      data-prks-role="editor-recovery-review"
    >
      <div class="modal-header">
        <h3 :id="headingId">Unsaved {{ subject }}</h3>
        <PrksIconButton class="close-btn" label="Close" data-prks-role="editor-recovery-close" @click="close">
          ×
        </PrksIconButton>
      </div>
      <div class="modal-body editor-recovery-review__body">
        <p class="modal-helper editor-recovery-review__identity">
          <strong data-prks-role="editor-recovery-entity">{{ entityTitle }}</strong>
          <template v-if="details">
            · Current note: <span data-prks-role="editor-recovery-current">{{ currentNoteText(details.current) }}</span>
            · <span data-prks-role="editor-recovery-queue">{{ queueText(details.current) }}</span>
          </template>
        </p>
        <PrksInlineMessage v-if="message" tone="error" status data-prks-role="editor-recovery-message">{{ message }}</PrksInlineMessage>
        <PrksInlineMessage v-if="loading && !details">Loading…</PrksInlineMessage>
        <PrksInlineMessage v-else-if="unavailable" tone="error" status>
          This review is no longer connected to an open editor. Close it and open Review from the note again.
        </PrksInlineMessage>
        <PrksInlineMessage v-else-if="details && !details.candidates.length" status data-prks-role="editor-recovery-empty">
          No unsaved drafts are left for this note.
        </PrksInlineMessage>
        <div v-else-if="details" class="editor-recovery-review__layout">
          <fieldset class="editor-recovery-review__list" :disabled="!!busy || comparing">
            <legend class="editor-recovery-review__legend">Drafts on this device ({{ details.candidates.length }})</legend>
            <label
              v-for="c in details.candidates"
              :key="c.draftId"
              class="editor-recovery-review__item"
              :class="{ 'editor-recovery-review__item--selected': c.draftId === selectedId }"
              data-prks-role="editor-recovery-candidate"
              :data-draft-id="c.draftId"
            >
              <input v-model="selectedId" type="radio" name="editor-recovery-draft" :value="c.draftId">
              <span class="editor-recovery-review__item-text">
                <span class="editor-recovery-review__item-title">{{ typedAtText(c.updatedAt) }} · {{ lengthText(c.length) }}</span>
                <span class="editor-recovery-review__item-meta">{{ originText(c) }} · {{ completenessText(c) }}</span>
              </span>
            </label>
          </fieldset>
          <section v-if="selected" class="editor-recovery-review__detail" data-prks-role="editor-recovery-detail">
            <dl class="editor-recovery-review__facts">
              <dt>Last typed</dt>
              <dd>{{ typedAtText(selected.updatedAt) }}</dd>
              <dt>From</dt>
              <dd data-prks-role="editor-recovery-origin">{{ originText(selected) }}</dd>
              <dt>Completeness</dt>
              <dd data-prks-role="editor-recovery-completeness">{{ completenessText(selected) }}</dd>
              <dt>Length</dt>
              <dd>{{ lengthText(selected.length) }}</dd>
              <dt>When left</dt>
              <dd>{{ pipelineText(selected.pipelineState) }}<template v-if="selected.typedOnRevision !== null">, typed on revision {{ selected.typedOnRevision }}</template></dd>
              <dt>Why it is here</dt>
              <dd data-prks-role="editor-recovery-reason">{{ reasonText(selected) }}</dd>
            </dl>
            <div v-if="comparing && details" class="editor-recovery-review__compare" data-prks-role="editor-recovery-compare">
              <label class="editor-recovery-review__column">
                <span class="editor-recovery-review__column-label">Current note</span>
                <textarea class="prks-input editor-recovery-review__text" readonly :value="details.current.text" data-prks-role="editor-recovery-current-text"></textarea>
              </label>
              <label class="editor-recovery-review__column">
                <span class="editor-recovery-review__column-label">Text to keep (recovered; edit to combine)</span>
                <textarea v-model="chosenText" class="prks-input editor-recovery-review__text" data-prks-role="editor-recovery-chosen-text"></textarea>
              </label>
            </div>
            <label v-else-if="selected.body !== null" class="editor-recovery-review__column">
              <span class="editor-recovery-review__column-label">Recovered text</span>
              <textarea class="prks-input editor-recovery-review__text" readonly :value="selected.body" data-prks-role="editor-recovery-text"></textarea>
            </label>
          </section>
        </div>
      </div>
      <div class="modal-footer editor-recovery-review__actions">
        <template v-if="selected && comparing">
          <PrksButton variant="primary" :busy="busy === 'replace'" busy-label="Replacing…" :disabled="!!busy" data-prks-role="editor-recovery-replace" @click="replace">Replace note with this text</PrksButton>
          <PrksButton :disabled="!!busy" data-prks-role="editor-recovery-copy-current" @click="copy('current', details ? details.current.text : null)">{{ copyLabel.current || 'Copy current note' }}</PrksButton>
          <PrksButton variant="quiet-danger" :disabled="!!busy" data-prks-role="editor-recovery-discard" @click="discard">Keep current note, discard draft</PrksButton>
          <PrksButton variant="ghost" :disabled="!!busy" data-prks-role="editor-recovery-back" @click="comparing = false">Back</PrksButton>
        </template>
        <template v-else-if="selected">
          <PrksButton v-if="selected.action === 'restore'" variant="primary" :busy="busy === 'restore'" busy-label="Restoring…" :disabled="!!busy" data-prks-role="editor-recovery-restore" @click="restore">Restore for editing</PrksButton>
          <PrksButton v-if="selected.action === 'reconcile'" variant="primary" :disabled="!!busy" data-prks-role="editor-recovery-compare-btn" @click="startCompare">Compare with current note</PrksButton>
          <PrksButton :disabled="!!busy || selected.body === null" data-prks-role="editor-recovery-copy" @click="copy('draft', selected.body)">{{ copyLabel.draft || 'Copy text' }}</PrksButton>
          <PrksButton
            v-if="selected.lineage !== 'self-live' && selected.lineage !== 'other-live'"
            variant="quiet-danger"
            :busy="busy === 'discard'"
            busy-label="Discarding…"
            :disabled="!!busy"
            data-prks-role="editor-recovery-discard"
            @click="discard"
          >Discard draft</PrksButton>
        </template>
        <PrksButton variant="ghost" class="editor-recovery-review__close" :disabled="!!busy" data-prks-role="editor-recovery-cancel" @click="close">Close</PrksButton>
      </div>
    </div>
  </div>
</template>
