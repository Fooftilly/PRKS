<script setup lang="ts">
/**
 * One feature dialog shell: header, title, close button, body and an
 * optional footer. ModalHost renders these for the create forms (#1036).
 *
 * The root keeps the classic `.modal` contract (`hidden` when closed, the
 * opening's `data-prks-open-generation`, `inert` while it prepares) and adds
 * `data-prks-dialog-ready` once the opening is prepared, its baseline taken
 * and its first field focused. These are reflected synchronously from
 * `dialogs.ts`, so code that reads the DOM right after `openModal` or
 * `closeModals` sees the new state at once.
 *
 * The form in the body registers its own opening steps with
 * `useDialogLifecycle`, which finds this dialog's id.
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import PrksIconButton from '../components/PrksIconButton.vue'
import { dialogState, provideDialogId, registerDialog, unregisterDialog } from './dialogs'

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
  if (dialogState.ready[props.id] === true) el.dataset.prksDialogReady = 'true'
  else delete el.dataset.prksDialogReady
}

registerDialog(props.id)
provideDialogId(props.id)

watch(
  () => [
    dialogState.active === props.id,
    dialogState.generations[props.id],
    dialogState.inert[props.id],
    dialogState.ready[props.id],
  ],
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
      <PrksIconButton class="close-btn" label="Close" @click="onClose">&times;</PrksIconButton>
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
