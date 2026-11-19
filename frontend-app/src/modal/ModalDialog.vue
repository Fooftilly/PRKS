<script setup lang="ts">
/**
 * One feature dialog shell: header, title, close button, body and an
 * optional footer. ModalHost renders these for the create forms (#1036).
 *
 * The root keeps the classic `.modal` contract (`hidden` when closed, the
 * opening's `data-prks-open-generation`, `inert` while it prepares). Those
 * are reflected synchronously from `dialogs.ts`, so code that reads the DOM
 * right after `openModal` or `closeModals` sees the new state at once.
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { dialogState, registerDialog, unregisterDialog } from './dialogs'

const props = withDefaults(
  defineProps<{
    id: string
    title: string
    /** Extra classes on the dialog root, such as `modal-wide`. */
    dialogClass?: string
    bodyClass?: string
    withFooter?: boolean
  }>(),
  { dialogClass: '', bodyClass: '', withFooter: false },
)

// Fixed for the dialog's life: Vue never patches it again, and `reflect`
// owns `hidden` from mount on.
const rootClass = ['modal', props.dialogClass, 'hidden'].filter(Boolean).join(' ')

const root = ref<HTMLElement | null>(null)
const titleId = `${props.id}-title`

function reflect(): void {
  const el = root.value
  if (!el) return
  el.classList.toggle('hidden', dialogState.active !== props.id)
  const generation = dialogState.generations[props.id]
  if (generation != null) el.dataset.prksOpenGeneration = String(generation)
  else delete el.dataset.prksOpenGeneration
  el.toggleAttribute('inert', dialogState.inert[props.id] === true)
}

registerDialog(props.id)

watch(
  () => [dialogState.active === props.id, dialogState.generations[props.id], dialogState.inert[props.id]],
  reflect,
  { flush: 'sync' },
)

onMounted(reflect)

onBeforeUnmount(() => unregisterDialog(props.id))

function onClose(): void {
  window.requestModalClose?.('button')
}
</script>

<template>
  <div
    :id="id"
    ref="root"
    :class="rootClass"
    role="dialog"
    aria-modal="true"
    :aria-labelledby="titleId"
    data-prks-dialog=""
  >
    <div class="modal-header">
      <h3 :id="titleId">{{ title }}</h3>
      <button type="button" class="prks-icon-btn close-btn" aria-label="Close" @click="onClose">&times;</button>
    </div>
    <div :class="['modal-body', bodyClass]">
      <slot />
    </div>
    <div
      v-if="withFooter"
      :id="`${id}-footer`"
      class="modal-footer modal-footer--sticky prks-form-actions"
    ></div>
  </div>
</template>
