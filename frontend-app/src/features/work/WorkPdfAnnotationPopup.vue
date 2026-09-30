<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { bindFloatingPosition } from '../../floating/bind-floating-position'
import { popupDraftForSession, type PopupDraftIdentity } from './pdf-annotation-popup-draft'

export interface AnnotationPopupView {
  open: boolean
  annId: string
  epoch: number
  comment: string
  meta: string
  pageIndex: number | null
  generation: number | null
}

const props = defineProps<{
  state: AnnotationPopupView
  tabId: string
  anchor: () => HTMLElement | null
  boundary: () => HTMLElement | null
  onSave: (text: string, ticket: { generation: number; annId: string; epoch: number }) => void
  onClose: (ticket: { generation: number; annId: string; epoch: number }) => void
  onDelete: (ticket: { generation: number; annId: string; epoch: number }) => void
}>()

const draft = ref('')
const previous = ref<PopupDraftIdentity | null>(null)
const floatingRef = ref<HTMLElement | null>(null)
const textRef = ref<HTMLTextAreaElement | null>(null)
let stopPosition = (): void => {}

function ticket() {
  return {
    generation: typeof props.state.generation === 'number' ? props.state.generation : Number.NaN,
    annId: props.state.annId,
    epoch: props.state.epoch,
  }
}

watch(
  () => [props.state.open, props.state.annId, props.state.epoch, props.state.comment] as const,
  () => {
    const next = props.state
    const identityChanged =
      !previous.value || previous.value.annId !== next.annId || previous.value.epoch !== next.epoch
    draft.value = popupDraftForSession(previous.value, next, draft.value)
    previous.value = next.open ? { annId: next.annId, epoch: next.epoch } : null
    if (identityChanged && next.open) {
      void nextTick(() => {
        textRef.value?.focus()
      })
    }
  },
  { immediate: true },
)

function place() {
  stopPosition()
  stopPosition = () => {}
  if (!props.state.open || !floatingRef.value) return
  const reference = props.anchor()
  if (!reference) return
  stopPosition = bindFloatingPosition(reference, floatingRef.value, props.boundary())
}

watch(
  () => [props.state.open, props.state.annId, props.state.epoch, floatingRef.value] as const,
  () => {
    place()
  },
)

onBeforeUnmount(() => {
  stopPosition()
})

function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape') return
  event.preventDefault()
  event.stopPropagation()
  props.onClose(ticket())
}
</script>

<template>
  <div
    v-if="state.open"
    ref="floatingRef"
    class="pdf-annotation-popup"
    role="dialog"
    aria-label="Annotation comment"
    data-prks-role="pdf-annotation-popup"
    data-no-interaction=""
    :data-prks-owner-tab-id="tabId"
    :data-prks-annotation-id="state.annId"
    :data-prks-popup-epoch="state.epoch"
    @keydown="onKeydown"
    @pointerdown.stop
  >
    <div class="pdf-annotation-popup__meta">{{ state.meta }}</div>
    <label class="pdf-annotation-popup__label">
      Comment
      <textarea
        ref="textRef"
        v-model="draft"
        class="textarea-md pdf-annotation-popup__text"
        data-prks-role="pdf-annotation-popup-text"
        placeholder="Add a note/comment for this annotation…"
      ></textarea>
    </label>
    <div class="pdf-annotation-popup__actions">
      <button type="button" class="prks-btn prks-btn--secondary" @click="onClose(ticket())">Cancel</button>
      <button type="button" class="prks-btn prks-btn--secondary" @click="onDelete(ticket())">Delete annotation</button>
      <button type="button" class="prks-btn prks-btn--primary" @click="onSave(draft, ticket())">Save comment</button>
    </div>
  </div>
</template>
